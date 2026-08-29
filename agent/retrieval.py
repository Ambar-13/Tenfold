"""Retrieval v1: faithful port of the grid-probe FTS5 BM25 query builder.

Ported line-for-line in behavior from the 2026-08-28 grid probe
(scratchpad gridwork/run/starter/agent.py): same FTS5 schema, same
tokenizer, same stopword set, same 60-term cap, same OR-of-quoted-terms
expression, same bm25 column weights, same LIMIT.

v1 is the default so that state/invalidation effects are isolated from
retrieval changes. TENFOLD_RETRIEVAL=v2 selects RetrieverV2 (
conjunctive field-aware retrieval; see the RetrieverV2 docstring).

Additions that do NOT change ranking of retrieved items:
  - `pad()` appends catalog-order ASINs after the ranked results so a
    recommending turn always emits exactly top_k unique catalog-valid
    ASINs (padding is free insurance; appended at the tail only).

Pure stdlib (sqlite3 FTS5). Deterministic. No network.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from pathlib import Path

TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "i", "in", "is", "it", "me", "my", "of", "on", "or", "please", "some",
    "that", "the", "this", "to", "want", "with", "would", "you", "looking",
    # crude dialog boilerplate (simulator phrasing), not product evidence
    "im", "m", "what", "matters", "key", "requirement", "need", "actually",
    "ignore", "earlier", "preference", "still", "exploring",
}

MAX_QUERY_TERMS = 60
# bm25 column weights: parent_asin, title, categories, features, details,
# store, description — identical to the probe.
BM25_SQL = (
    "SELECT parent_asin FROM products WHERE products MATCH ? "
    "ORDER BY bm25(products, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0) LIMIT ?"
)


def text_of(value: object) -> str:
    """Flatten a catalog field to searchable text (probe `_text`)."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{key} {item}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


def terms(text: str) -> list[str]:
    """Tokenize to lowercase evidence terms (probe `_terms`)."""
    return [
        token.lower()
        for token in TOKEN_RE.findall(text)
        if len(token) > 1 and token.lower() not in STOPWORDS
    ]


class RetrieverV1:
    """In-memory FTS5 index over the catalog with the probe's query builder."""

    def __init__(self, catalog_path: str | Path = "data/catalog.jsonl") -> None:
        self.catalog_path = Path(catalog_path)
        self.connection = sqlite3.connect(":memory:")
        self.default_pad: list[str] = []  # first catalog ASINs, file order
        self._build_index()

    def _build_index(self) -> None:
        cursor = self.connection.cursor()
        cursor.execute(
            "CREATE VIRTUAL TABLE products USING fts5("
            "parent_asin UNINDEXED, title, categories, features, details, store, description, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        batch: list[tuple[str, ...]] = []
        with self.catalog_path.open(encoding="utf-8") as handle:
            for line in handle:
                product = json.loads(line)
                asin = str(product["parent_asin"])
                if len(self.default_pad) < 200:
                    self.default_pad.append(asin)
                batch.append(
                    (
                        asin,
                        text_of(product.get("title")),
                        text_of(product.get("categories")),
                        text_of(product.get("features")),
                        text_of(product.get("details")),
                        text_of(product.get("store")),
                        text_of(product.get("description")),
                    )
                )
                if len(batch) >= 1000:
                    cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
                    batch.clear()
        if batch:
            cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
        self.connection.commit()

    def build_expression(self, evidence_texts: list[str]) -> str:
        """Probe query builder: join evidence, dedupe terms, cap 60, OR-quote."""
        query_text = " ".join(evidence_texts)
        unique_terms = list(dict.fromkeys(terms(query_text)))[:MAX_QUERY_TERMS]
        return " OR ".join(f'"{term}"' for term in unique_terms)

    def search(self, evidence_texts: list[str], top_k: int) -> list[str]:
        expression = self.build_expression(evidence_texts)
        if not expression:
            return []
        rows = self.connection.execute(BM25_SQL, (expression, top_k)).fetchall()
        return [str(row[0]) for row in rows]

    def pad(self, asins: list[str], top_k: int) -> list[str]:
        """Pad the ranked list to exactly top_k unique catalog ASINs (tail only)."""
        result: list[str] = []
        seen: set[str] = set()
        for asin in asins:
            if asin not in seen:
                seen.add(asin)
                result.append(asin)
            if len(result) >= top_k:
                return result[:top_k]
        for asin in self.default_pad:
            if asin not in seen:
                seen.add(asin)
                result.append(asin)
            if len(result) >= top_k:
                break
        return result[:top_k]


# ---------------------------------------------------------------------------
# Retrieval v2: typed conjunctive field-aware retrieval.
# ---------------------------------------------------------------------------

# Tunables. Fit on the TUNE split ONLY (sample_id numeric suffix even) via
# experiments/tune_v2.sh; the baked defaults below are the TUNE-selected
# values. TENFOLD_V2_TUNE (JSON dict, read once at RetrieverV2 init) exists
# solely so the tuning script can sweep without code forks.
V2_DEFAULT_TUNABLES = {
    # bm25 field weights for the main v2 query (title > features > description)
    # TUNE-selected 2026-08-28 (cfg6, TUNE TS 0.88813; grid in results/b-tune-v2-*.json)
    "w_title": 6.0,
    "w_categories": 2.0,
    "w_features": 2.5,
    "w_details": 2.5,
    "w_store": 1.0,
    "w_description": 1.0,
    # coverage rerank: verbatim-phrase rank boost + hard-constraint weight
    "verbatim_boost": 1.5,
    "hard_weight": 1.0,
    # ladder: per-level fetch limit and the minimum pool before relaxing
    "retrieve_limit": 400,
    "min_pool": 30,
    # TENFOLD_COVERAGE=idf only: bm25-score band width for the demoted
    # sub-perfect coverage tie-breaker (decisive coverage credit reorders
    # only within a band of BM25 near-ties; <= 0 disables sub-perfect
    # reordering entirely so non-perfect candidates keep pure BM25 order).
    # TUNE-selected 2026-08-28: 0.0 — on the TUNE split the credit tie-break
    # is score-neutral on the official evaluator and slightly TS-negative
    # under paraphrase (H1 TUNE 0.72265 off vs 0.71780 at width 2.0), i.e.
    # sub-perfect coverage is best fully distrusted; the perfect-saturation
    # hoist carries all of the rerank's value.
    "coverage_band": 0.0,
    # TENFOLD_COVERAGE=idf only: saturation threshold for decisive coverage.
    # > 0: a constraint contributes a FULL point only when the candidate
    # covers at least this fraction of its IDF mass, else nothing — coverage
    # acts only on conjunctive, decisive evidence and defers everything
    # marginal to BM25 order. 0: linear IDF fractions.
    "sat_threshold": 0.99,
}

# v1-equivalent bm25 weights mapped onto the v2 schema (extra price column
# weighted 0), used only by the ladder's final v1-fallback rung.
V2_FALLBACK_WEIGHTS = (0.0, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0)

ANCHOR_TOKEN_RE = TOKEN_RE  # anchor tokens keep single chars ("T-Shirts" -> t, shirts)


def _quote(token: str) -> str:
    return '"%s"' % token.replace('"', '""')


def _normalize(text: str) -> str:
    """Canonical token string for verbatim-phrase matching (punctuation-proof)."""
    return " ".join(token.lower() for token in TOKEN_RE.findall(text))


class RetrieverV2(RetrieverV1):
    """Typed conjunctive retrieval: filters + field-weighted BM25 + coverage rerank.

    Query plan compiled from the typed dialogue state:
      hard filters —
        * category: the opener anchor (last two non-generic tokens of the
          target's category path) matched by TOKEN CONTAINMENT against the
          catalog `categories` field (FTS5 column filter, AND of anchor
          tokens). The target always satisfies its own anchor by construction.
        * price: numeric BETWEEN on an unindexed price column when the state
          holds a budget interval (never fires on public cards; implemented
          per spec for sparser private products).
        * exclusions: NOT clauses from negative-polarity constraints.
      soft ranking —
        * field-weighted BM25 (title > features > description) over an OR of
          evidence terms,
        * constraint-coverage rerank: candidates scored by how many active
          constraints they match in title/features/details/description
          (token containment, budget matched against the price column), with
          a VERBATIM-PHRASE match counted as a rank boost (never an ID
          lookup); ties broken by BM25 order.
    Relaxation ladder (never returns an empty pool while evidence exists):
      L0 full plan -> L1 drop price -> L2 drop category -> L3 the v1 OR-soup
      query verbatim. Levels are appended (deduped) while the pool is below
      `min_pool`; the rerank sort key is (level, -coverage, bm25 seq), so
      relaxed candidates can never displace filtered ones — they only fill
      the tail. Recommending turns therefore always reach 10 valid unique
      ASINs (the agent additionally pads from catalog order, as in v1).

    Component ablations (env TENFOLD_V2_ABLATE, read once at init;
    attribution only — v2-only, v1 code paths untouched, default "" is
    byte-identical to the shipped pipeline):
      * "no_rerank"  — constraint-coverage rerank disabled: pool is sorted
        by (ladder level, bm25 seq) only, i.e. pure filtered field-weighted
        BM25 order. Filters/ladder unchanged.
      * "no_filters" — hard filters disabled: no category anchor filter, no
        price BETWEEN, no NOT clauses; the ladder collapses to the bare
        OR-of-terms level (+ v1 fallback rung). Field weights and the
        coverage rerank stay on.
    """

    def __init__(self, catalog_path: str | Path = "data/catalog.jsonl") -> None:
        self.tunables = dict(V2_DEFAULT_TUNABLES)
        raw = os.environ.get("TENFOLD_V2_TUNE", "")
        if raw:
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    for key in self.tunables:
                        if key in loaded:
                            self.tunables[key] = type(self.tunables[key])(loaded[key])
            except (ValueError, TypeError):
                pass
        ablate = os.environ.get("TENFOLD_V2_ABLATE", "").strip()
        self.ablate = ablate if ablate in ("no_rerank", "no_filters") else ""
        # TENFOLD_COVERAGE = contain (legacy full-token containment of
        # raw text) | idf (robust: IDF-weighted content-token overlap, demoted
        # to a tie-breaker within BM25 score bands). Read once at init.
        coverage = os.environ.get("TENFOLD_COVERAGE", "idf").strip()
        self.coverage_mode = coverage if coverage in ("contain", "idf") else "idf"
        self._norm_cache: dict[str, tuple[str, frozenset]] = {}
        self._doc_count = 0
        self._df: dict[str, int] = {}
        super().__init__(catalog_path)
        if self.coverage_mode == "idf":
            self._build_df_table()

    # ---- IDF table (idf coverage mode only) ----
    def _build_df_table(self) -> None:
        """Document frequencies straight from the FTS index (fts5vocab 'row'):
        exactly the corpus the ranking runs on, built once at init."""
        cursor = self.connection.cursor()
        cursor.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS products_vocab USING fts5vocab(products, 'row')"
        )
        self._doc_count = int(
            self.connection.execute("SELECT count(*) FROM products").fetchone()[0]
        )
        self._df = {
            str(term): int(doc)
            for term, doc in self.connection.execute("SELECT term, doc FROM products_vocab")
        }

    def _idf(self, token: str) -> float | None:
        """IDF for a catalog-known token; None for tokens with zero document
        frequency — those can never discriminate between candidates (no
        product contains them), so they are excluded from coverage entirely.
        This is the principled version of frame-token immunity: conversational
        junk that survives parsing scores nothing, instead of poisoning the
        denominator of every candidate equally-badly-but-noisily."""
        df = self._df.get(token)
        if not df:
            return None
        return math.log(1.0 + self._doc_count / df)

    # ---- index ----
    def _build_index(self) -> None:
        cursor = self.connection.cursor()
        cursor.execute(
            "CREATE VIRTUAL TABLE products USING fts5("
            "parent_asin UNINDEXED, price UNINDEXED, "
            "title, categories, features, details, store, description, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        batch: list[tuple] = []
        with self.catalog_path.open(encoding="utf-8") as handle:
            for line in handle:
                product = json.loads(line)
                asin = str(product["parent_asin"])
                if len(self.default_pad) < 200:
                    self.default_pad.append(asin)
                batch.append(
                    (
                        asin,
                        self._parse_price(product.get("price")),
                        text_of(product.get("title")),
                        text_of(product.get("categories")),
                        text_of(product.get("features")),
                        text_of(product.get("details")),
                        text_of(product.get("store")),
                        text_of(product.get("description")),
                    )
                )
                if len(batch) >= 1000:
                    cursor.executemany(
                        "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?)", batch
                    )
                    batch.clear()
        if batch:
            cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?)", batch)
        self.connection.commit()

    @staticmethod
    def _parse_price(value: object) -> float | None:
        """Numeric price, tolerating catalog strings ('from 12.99', em dashes)."""
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            match = re.search(r"([0-9]+(?:\.[0-9]+)?)", value)
            if match:
                return float(match.group(1))
        return None

    # ---- query-plan compilation ----
    def compile_plan(self, state) -> dict:
        """Active typed constraints -> structured query plan."""
        anchor_tokens: list[str] = []
        if getattr(state, "category_anchor", None):
            anchor_tokens = list(dict.fromkeys(
                token.lower() for token in ANCHOR_TOKEN_RE.findall(state.category_anchor)
            ))
        positives: list = []
        negatives: list = []
        price_interval = None
        for constraint in state.active_constraints():
            if constraint.polarity > 0:
                if constraint.price_interval is not None:
                    price_interval = constraint.price_interval  # latest wins below
                else:
                    positives.append(constraint)
            else:
                negatives.append(constraint)
        interval = state.budget_interval()
        if interval is not None:
            price_interval = interval
        term_sources = ([state.category_anchor] if anchor_tokens else []) + [
            c.text for c in positives
        ]
        query_terms = list(dict.fromkeys(terms(" ".join(term_sources))))[:MAX_QUERY_TERMS]
        neg_clauses = []
        for constraint in negatives:
            tokens = terms(constraint.text)
            if tokens:
                neg_clauses.append(" AND ".join(_quote(t) for t in tokens))
        return {
            "anchor_tokens": anchor_tokens,
            "positives": positives,
            "query_terms": query_terms,
            "neg_clauses": neg_clauses,
            "price_interval": price_interval,
        }

    def _match_expression(self, plan: dict, use_category: bool) -> str:
        parts: list[str] = []
        if use_category and plan["anchor_tokens"]:
            parts.append(
                "categories : (" + " AND ".join(_quote(t) for t in plan["anchor_tokens"]) + ")"
            )
        if plan["query_terms"]:
            parts.append("(" + " OR ".join(_quote(t) for t in plan["query_terms"]) + ")")
        if not parts:
            return ""
        expression = " AND ".join(parts)
        for clause in plan["neg_clauses"]:
            expression = f"({expression}) NOT ({clause})"
        return expression

    def _run_level(self, expression: str, weights: tuple, price_interval, limit: int,
                   with_score: bool = False) -> list[tuple]:
        if not expression:
            return []
        rank_expr = "bm25(products, %s)" % ", ".join(str(w) for w in weights)
        select_cols = "parent_asin, price, title, categories, features, details, store, description"
        if with_score:
            select_cols += ", " + rank_expr  # score appended as the LAST column
        sql = f"SELECT {select_cols} FROM products WHERE products MATCH ?"
        params: list = [expression]
        if price_interval is not None:
            low, high = price_interval
            high = 1e12 if high == float("inf") else high
            sql += " AND price IS NOT NULL AND price >= ? AND price <= ?"
            params.extend([float(low), float(high)])
        sql += " ORDER BY %s LIMIT ?" % rank_expr
        params.append(int(limit))
        try:
            return self.connection.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []  # malformed MATCH from hostile free text: treat as empty level

    # ---- coverage rerank ----
    def _candidate_norm(self, row: tuple) -> tuple[str, frozenset]:
        asin = str(row[0])
        cached = self._norm_cache.get(asin)
        if cached is None:
            # text fields live at columns 2..7 (idf-mode rows carry the bm25
            # score as a trailing extra column — never part of the text)
            normalized = _normalize(" ".join(str(part) for part in row[2:8] if part))
            cached = (f" {normalized} ", frozenset(normalized.split()))
            self._norm_cache[asin] = cached
        return cached

    def _coverage(self, row: tuple, plan: dict) -> float:
        norm_text, token_set = self._candidate_norm(row)
        verbatim_boost = float(self.tunables["verbatim_boost"])
        hard_weight = float(self.tunables["hard_weight"])
        score = 0.0
        for constraint in plan["positives"]:
            tokens = constraint.tokens()
            if not tokens:
                continue
            weight = hard_weight if constraint.hard else 1.0
            fraction = len(tokens & token_set) / len(tokens)
            part = fraction
            if fraction >= 1.0:
                phrase = _normalize(constraint.text)
                if phrase and f" {phrase} " in norm_text:
                    part += verbatim_boost
            score += weight * part
        if plan["price_interval"] is not None and row[1] is not None:
            low, high = plan["price_interval"]
            if low <= float(row[1]) <= high:
                score += 1.0
        return score

    # ---- robust coverage (TENFOLD_COVERAGE=idf) ----
    # Attribute-slot LABEL tokens: they name the QUESTION an attribute answers
    # ("color: black", "size: large"), not the product itself, so a labeled
    # constraint is scored on its value tokens whenever value tokens exist.
    ATTR_LABEL_TOKENS = frozenset({
        "color", "colour", "colors", "colour", "material", "materials",
        "size", "sizes", "style", "styles", "brand", "brands", "budget",
        "category", "categories", "feature", "features", "use_case", "use",
        "case", "pattern", "department", "type", "kind",
    })

    def _constraint_idf_tokens(self, constraint) -> list[tuple[str, float]]:
        """(token, idf) pairs for a constraint's content tokens: slot-label
        tokens are dropped when value tokens remain, and only tokens that
        exist in the catalog corpus (df > 0) are kept."""
        tokens = constraint.tokens()
        value_tokens = tokens - self.ATTR_LABEL_TOKENS
        if value_tokens:
            tokens = value_tokens
        pairs: list[tuple[str, float]] = []
        for token in sorted(tokens):
            idf = self._idf(token)
            if idf is not None:
                pairs.append((token, idf))
        return pairs

    def _coverage_detail(self, row: tuple, plan: dict) -> tuple[bool, float, float]:
        """Robust per-candidate coverage: (perfect, credit, linear).

        perfect — the candidate saturates EVERY scoreable active constraint
          (>= sat_threshold of each constraint's IDF mass) and, when a price
          interval is active, has an in-range price. In a verbatim-quoting
          world this is exactly the property that identifies the target;
          under paraphrase it is rarely satisfied by accident, so it is the
          only evidence strong enough to override BM25 across the pool.
        credit — decisive units only: count of saturated constraints
          (+1 for an in-range price). Sub-threshold overlap contributes
          NOTHING: partial token overlap is BM25's business.
        linear — continuous IDF-fraction score incl. the verbatim-phrase
          bonus; used solely to order candidates that are already perfect
          (bonus-only, per the robustness results) and as the conf gap feature.
        """
        norm_text, token_set = self._candidate_norm(row)
        verbatim_boost = float(self.tunables["verbatim_boost"])
        hard_weight = float(self.tunables["hard_weight"])
        sat_threshold = float(self.tunables["sat_threshold"])
        if sat_threshold <= 0:
            sat_threshold = 1.0
        n_scoreable = 0
        n_saturated = 0
        linear = 0.0
        for constraint in plan["positives"]:
            pairs = self._constraint_idf_tokens(constraint)
            if not pairs:
                continue  # nothing catalog-discriminative: contributes nothing
            n_scoreable += 1
            total = sum(idf for _, idf in pairs)
            hit = sum(idf for token, idf in pairs if token in token_set)
            fraction = hit / total if total > 0 else 0.0
            part = fraction
            if fraction >= 1.0:
                phrase = _normalize(constraint.text)
                if phrase and f" {phrase} " in norm_text:
                    part += verbatim_boost
            linear += (hard_weight if constraint.hard else 1.0) * part
            if fraction >= sat_threshold:
                n_saturated += 1
        price_ok = None
        if plan["price_interval"] is not None:
            low, high = plan["price_interval"]
            price_ok = row[1] is not None and low <= float(row[1]) <= high
            if price_ok:
                linear += 1.0
        perfect = (
            n_scoreable > 0
            and n_saturated == n_scoreable
            and (price_ok is None or price_ok)
        )
        credit = float(n_saturated) + (1.0 if price_ok else 0.0)
        return perfect, credit, linear

    def _order_idf(self, pool: list[tuple], plan: dict) -> list[tuple]:
        """Order a ladder pool under robust (idf) coverage.

        Two-regime rule, per ladder level:
          * PERFECT candidates (full conjunctive saturation, see
            _coverage_detail) rank first, ordered by their continuous linear
            score (verbatim bonus included), then BM25 order. This preserves
            the official-tier rank-1 forcing.
          * Everything else keeps BM25 order except that decisive coverage
            credit may break ties WITHIN a BM25 score band
            (width `coverage_band` > 0); when a band's credits are uniform
            the order inside it is exactly BM25 — i.e. the ordering falls
            back to BM25 wherever coverage is non-discriminative, and noisy
            partial overlap can never hoist a candidate across bands.
            `coverage_band` <= 0 (the TUNE-selected default) disables the
            sub-perfect tie-break: non-perfect candidates keep pure BM25
            order and only the perfect-saturation hoist remains.
        Pool entries: (level, seq, row), bm25 score as the row's last column.
        Returns sorted entries: (key_tuple, row, linear_score).
        """
        band_width = float(self.tunables["coverage_band"])
        level_best: dict[int, float] = {}
        for level, seq, row in pool:
            score = float(row[-1])
            if level not in level_best or score < level_best[level]:
                level_best[level] = score
        entries: list[tuple] = []
        for level, seq, row in pool:
            perfect, credit, linear = self._coverage_detail(row, plan)
            if perfect:
                key = (level, 0, 0, -linear, seq)
            elif band_width > 0:
                band = int((float(row[-1]) - level_best[level]) // band_width)
                key = (level, 1, band, -credit, seq)
            else:
                # sub-perfect tie-break disabled: pure BM25 order
                key = (level, 1, 0, 0.0, seq)
            entries.append((key, row, linear))
        entries.sort(key=lambda entry: entry[0])
        return entries

    # ---- public API ----
    def search_state(self, state, top_k: int) -> list[str]:
        """Full v2 pipeline over the typed dialogue state."""
        plan = self.compile_plan(state)
        if self.ablate == "no_filters":
            # Hard filters off: category/price/NOT all disabled; with no
            # anchor tokens the level list below collapses to the bare
            # OR-of-terms level plus the v1 fallback rung.
            plan["anchor_tokens"] = []
            plan["neg_clauses"] = []
            plan["price_interval"] = None
        weights = (
            0.0, 0.0,
            float(self.tunables["w_title"]),
            float(self.tunables["w_categories"]),
            float(self.tunables["w_features"]),
            float(self.tunables["w_details"]),
            float(self.tunables["w_store"]),
            float(self.tunables["w_description"]),
        )
        limit = int(self.tunables["retrieve_limit"])
        min_pool = max(int(self.tunables["min_pool"]), top_k)
        levels: list[tuple] = []  # (use_category, use_price, fallback_v1)
        if plan["anchor_tokens"]:
            if plan["price_interval"] is not None:
                levels.append((True, True, False))
            levels.append((True, False, False))
        levels.append((False, False, False))
        levels.append((False, False, True))

        with_score = self.coverage_mode == "idf"
        pool: list[tuple] = []  # (level_index, seq, row)
        seen: set[str] = set()
        for level_index, (use_category, use_price, fallback) in enumerate(levels):
            if len(pool) >= min_pool:
                break
            if fallback:
                expression = self.build_expression(state.evidence_texts())
                rows = self._run_level(expression, V2_FALLBACK_WEIGHTS, None, limit,
                                       with_score=with_score)
            else:
                expression = self._match_expression(plan, use_category)
                rows = self._run_level(
                    expression, weights,
                    plan["price_interval"] if use_price else None,
                    limit,
                    with_score=with_score,
                )
            for seq, row in enumerate(rows):
                asin = str(row[0])
                if asin not in seen:
                    seen.add(asin)
                    pool.append((level_index, seq, row))
        if not pool:
            return []
        if self.ablate == "no_rerank":
            # Coverage rerank off: pure filtered field-weighted BM25 order.
            ordered = sorted((level, seq, str(row[0])) for level, seq, row in pool)
            return [asin for _, _, asin in ordered[:top_k]]
        if self.coverage_mode == "idf":
            entries = self._order_idf(pool, plan)
            return [str(entry[1][0]) for entry in entries[:top_k]]
        scored = sorted(
            ((level, -self._coverage(row, plan), seq, str(row[0])) for level, seq, row in pool),
        )
        return [asin for _, _, _, asin in scored[:top_k]]

    def search_state_conf(self, state, top_k: int) -> tuple[list[str], dict]:
        """Full v2 pipeline plus confidence features for the controller.

        Pool construction and ranking are DUPLICATED VERBATIM from
        search_state (deliberate: the fixed-hold v2 path must stay
        byte-untouched; keep the two in sync). TENFOLD_V2_ABLATE is a
        attribution switch and is ignored on this path.

        Returns (asins, features):
          n_constraints  — active positive constraints with content tokens
          top1_saturated — top-1 has token-containment fraction 1.0 on every
                           such constraint (and price in range, if an
                           interval is active); False on an empty pool
          top1_level     — relaxation-ladder level of top-1 (0 = fully filtered)
          gap            — top-1 minus top-2 coverage score (rerank units,
                           verbatim boost included); top-1 score if the pool
                           has a single candidate
          pool_size      — deduped candidate-pool size across ladder levels
          card_exhausted — `other` exhausted: simulator replies stop yielding
        """
        plan = self.compile_plan(state)
        weights = (
            0.0, 0.0,
            float(self.tunables["w_title"]),
            float(self.tunables["w_categories"]),
            float(self.tunables["w_features"]),
            float(self.tunables["w_details"]),
            float(self.tunables["w_store"]),
            float(self.tunables["w_description"]),
        )
        limit = int(self.tunables["retrieve_limit"])
        min_pool = max(int(self.tunables["min_pool"]), top_k)
        levels: list[tuple] = []  # (use_category, use_price, fallback_v1)
        if plan["anchor_tokens"]:
            if plan["price_interval"] is not None:
                levels.append((True, True, False))
            levels.append((True, False, False))
        levels.append((False, False, False))
        levels.append((False, False, True))

        with_score = self.coverage_mode == "idf"
        pool: list[tuple] = []  # (level_index, seq, row)
        seen: set[str] = set()
        for level_index, (use_category, use_price, fallback) in enumerate(levels):
            if len(pool) >= min_pool:
                break
            if fallback:
                expression = self.build_expression(state.evidence_texts())
                rows = self._run_level(expression, V2_FALLBACK_WEIGHTS, None, limit,
                                       with_score=with_score)
            else:
                expression = self._match_expression(plan, use_category)
                rows = self._run_level(
                    expression, weights,
                    plan["price_interval"] if use_price else None,
                    limit,
                    with_score=with_score,
                )
            for seq, row in enumerate(rows):
                asin = str(row[0])
                if asin not in seen:
                    seen.add(asin)
                    pool.append((level_index, seq, row))

        features = {
            "n_constraints": sum(1 for c in plan["positives"] if c.tokens()),
            "top1_saturated": False,
            "top1_level": None,
            "gap": 0.0,
            "pool_size": len(pool),
            "card_exhausted": "other" in getattr(state, "exhausted_attrs", set()),
        }
        if not pool:
            return [], features
        if self.coverage_mode == "idf":
            entries = self._order_idf(pool, plan)  # (key, row, linear)
            top1_key, top1_row, top1_linear = entries[0]
            features["top1_level"] = top1_key[0]
            features["top1_saturated"] = self._saturated(top1_row, plan)
            features["gap"] = (
                top1_linear - entries[1][2] if len(entries) > 1 else top1_linear
            )
            return [str(entry[1][0]) for entry in entries[:top_k]], features
        scored = sorted(
            ((level, -self._coverage(row, plan), seq, row) for level, seq, row in pool),
            key=lambda item: (item[0], item[1], item[2]),
        )
        top1_level, top1_neg_cov, _, top1_row = scored[0]
        features["top1_level"] = top1_level
        features["top1_saturated"] = self._saturated(top1_row, plan)
        features["gap"] = (
            (-top1_neg_cov) - (-scored[1][1]) if len(scored) > 1 else -top1_neg_cov
        )
        return [str(row[0]) for _, _, _, row in scored[:top_k]], features

    def _saturated(self, row: tuple, plan: dict) -> bool:
        """True iff `row` token-contains every active positive constraint
        (fraction 1.0 each; vacuously True with zero constraints — the
        controller guards with a minimum-constraint threshold) and, when a
        price interval is active, has an in-range price."""
        _, token_set = self._candidate_norm(row)
        for constraint in plan["positives"]:
            tokens = constraint.tokens()
            if self.coverage_mode == "idf":
                # idf mode: judge saturation on catalog-discriminative VALUE
                # tokens only — slot-label tokens are metadata, and tokens no
                # product contains (df = 0) can never be covered.
                value_tokens = tokens - self.ATTR_LABEL_TOKENS
                if value_tokens:
                    tokens = value_tokens
                tokens = {t for t in tokens if self._idf(t) is not None}
            if tokens and len(tokens & token_set) < len(tokens):
                return False
        if plan["price_interval"] is not None:
            if row[1] is None:
                return False
            low, high = plan["price_interval"]
            if not (low <= float(row[1]) <= high):
                return False
        return True

    def search(self, evidence_texts: list[str], top_k: int) -> list[str]:
        """v1-compatible interface: the fallback rung only (v1 expression/weights)."""
        expression = self.build_expression(evidence_texts)
        rows = self._run_level(expression, V2_FALLBACK_WEIGHTS, None, top_k)
        return [str(row[0]) for row in rows]
