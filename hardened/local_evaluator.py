from __future__ import annotations

import argparse
import json
import os
import random
import re
import statistics
import uuid
from collections import defaultdict
from pathlib import Path

from starter.agent import Agent

# ---------------------------------------------------------------------------
# HARDENED SIMULATOR FORK. This file is a copy of
# kit/evaluator/local_evaluator.py with a seeded perturbation layer bolted on.
# Scoring, protocol, MAX_TURNS, first-hit semantics and override gating are
# IDENTICAL to the official evaluator; only the USER-SIDE MESSAGE TEXT (and,
# at H2+, which/how many card strings a reply reveals) is perturbed.
#
# Tier selection: env HARDENED_TIER in {H0, H1, H2, H3} (default H0).
#   H0 = no perturbation; must reproduce the official evaluator byte-for-byte.
#   H1 = paraphrase: constraint strings paraphrased when quoted, reply-template
#        variety, override messages from 7 templates with no shared prefix,
#        opener paraphrases (same category anchor tokens, new sentence).
#   H2 = H1 + restrictions/reorder: 'other' reveals at most 1 constraint per
#        ask (or, seeded, behaves as a random specific attribute); disclosure
#        order shuffled (soft-before-hard possible); the boundary deflection
#        hits a seeded-random non-null ask (1st..3rd), not always the first.
#   H3 = H2 + degradation: one card entry dropped from the disclosure pool
#        (never the override new_value); light noise on user messages
#        (fillers + a seeded adjacent-char swap in template words only, so
#        constraint content and category anchors are never corrupted).
#
# All randomness is seeded per (sample_id, scenario_type, tier); runs are
# deterministic. Optional env HARDENED_LOG=<path> writes one JSON line per
# session with the full user-side dialog + (original -> paraphrase) traces;
# write-only, never affects behavior or the results JSON.
# ---------------------------------------------------------------------------

HARDENED_TIER = os.environ.get("HARDENED_TIER", "H0").upper()
if HARDENED_TIER not in {"H0", "H1", "H2", "H3"}:
    raise SystemExit(f"HARDENED_TIER must be one of H0/H1/H2/H3, got {HARDENED_TIER!r}")
HARDENED_LOG = os.environ.get("HARDENED_LOG") or None


MAX_TURNS = 10
TOP_K = 10
ALLOWED_ATTRIBUTES = {
    "category", "material", "color", "size", "style", "brand",
    "budget", "feature", "use_case", "other",
}
MATERIALS = ("cotton", "polyester", "nylon", "leather", "wool", "spandex", "silk", "rayon", "fabric")
SEARCH_FIELDS = ("title", "features", "details", "description", "categories", "store")
MATERIAL_RE = re.compile(r"\b(cotton|polyester|nylon|leather|wool|spandex|silk|rayon|fabric)\b", re.I)
COLOR_RE = re.compile(r"\b(black|white|blue|red|pink|green|brown|gray|grey|purple|yellow|orange)\b", re.I)


def searchable_text(product: dict) -> str:
    parts: list[str] = []
    for field in SEARCH_FIELDS:
        value = product.get(field)
        if isinstance(value, dict):
            parts.extend(f"{key} {item}" for key, item in value.items())
        elif isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif value is not None:
            parts.append(str(value))
    return " ".join(parts).strip()


def _flatten_values(value: object) -> list[str]:
    if isinstance(value, dict):
        return [f"{key}: {item}" for key, item in value.items() if item not in (None, "", [])]
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)] if value not in (None, "") else []


def _clean_constraint(value: str, limit: int) -> str:
    return re.sub(r"\s+", " ", value).strip(" -;,.\t\n")[:limit].rstrip()


def intent_card(product: dict, limit: int = 180) -> dict:
    title = _clean_constraint(str(product.get("title") or "product"), limit)
    candidates = [*_flatten_values(product.get("features")), *_flatten_values(product.get("details"))]
    corpus = searchable_text(product)
    material = MATERIAL_RE.search(corpus)
    color = COLOR_RE.search(corpus)
    if material:
        candidates.insert(0, material.group(1).lower())
    if color:
        candidates.insert(1, f"color: {color.group(1).lower()}")
    if product.get("price") not in (None, ""):
        candidates.append(f"budget around ${product['price']}")
    cleaned = list(dict.fromkeys(_clean_constraint(item, limit) for item in candidates if _clean_constraint(item, limit)))
    if not cleaned:
        cleaned = [title]
    return {
        "target_category": title,
        "hard_constraints": cleaned[:2],
        "soft_preferences": cleaned[2:4] or cleaned[:1],
    }


def behavior_for(scenario: str, card: dict, rng: random.Random) -> dict:
    behavior: dict = {"scenario_type": scenario}
    if scenario == "intent_override":
        hard = card["hard_constraints"]
        soft = card["soft_preferences"]
        old_value = soft[-1] if soft else "I prefer a different style."
        new_value = hard[0] if hard else "Please prioritize the target requirements."
        behavior["override"] = {
            "turn": rng.choice([3, 4]),
            "old_value": old_value,
            "new_value": new_value,
            "message": f"Actually, ignore my earlier preference. What I need is: {new_value}.",
        }
    return behavior


def load_jsonl(path: str | Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def normalize_recommendations(payload: object, catalog_ids: set[str]) -> list[str]:
    if not isinstance(payload, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in payload:
        value = item.get("parent_asin", "") if isinstance(item, dict) else item
        parent_asin = str(value).strip()
        if not parent_asin or parent_asin in seen or parent_asin not in catalog_ids:
            continue
        seen.add(parent_asin)
        result.append(parent_asin)
        if len(result) >= TOP_K:
            break
    return result


def catalog_index(catalog_path: str | Path) -> tuple[set[str], dict[str, list[str]], dict[str, dict]]:
    identifiers: set[str] = set()
    categories: dict[str, list[str]] = {}
    products: dict[str, dict] = {}
    with Path(catalog_path).open(encoding="utf-8") as handle:
        for line in handle:
            product = json.loads(line)
            parent_asin = str(product["parent_asin"])
            identifiers.add(parent_asin)
            categories[parent_asin] = [str(value) for value in product.get("categories") or []]
            products[parent_asin] = product
    return identifiers, categories, products


def coarse_category(values: list[str]) -> str:
    excluded = {"clothing", "clothing shoes & jewelry", "clothing, shoes & jewelry"}
    cleaned: list[str] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part and part.lower() not in excluded:
                cleaned.append(part)
    return " ".join(cleaned[-2:]) if cleaned else "clothing item"


def classify_constraint(value: str) -> str:
    lowered = value.lower()
    if "budget" in lowered or re.search(r"(?:\$|<=|under)\s*\d", lowered):
        return "budget"
    if any(material in lowered for material in MATERIALS):
        return "material"
    if any(word in lowered for word in ("color", "black", "white", "blue", "red", "pink", "green")):
        return "color"
    if any(word in lowered for word in ("size", "sizing", "width", "wide", "narrow")):
        return "size"
    if any(word in lowered for word in ("department", "style", "fit", "sleeve", "neck")):
        return "style"
    if any(word in lowered for word in ("hiking", "running", "gym", "winter", "outdoor", "work")):
        return "use_case"
    return "feature"


# ---------------------------------------------------------------------------
# Hardened perturbation layer
# ---------------------------------------------------------------------------

SPECIFIC_ATTRIBUTES = ("material", "color", "size", "style", "budget", "feature", "use_case")

_BUDGET_CARD_RE = re.compile(r"^budget around \$(.+)$", re.I)
_COLOR_CARD_RE = re.compile(r"^color:\s*(.+)$", re.I)
_PCT_RE = re.compile(r"^(\d+)%\s+(.+)$")
_CLOSURE_RE = re.compile(r"^(.+?)\s+closure$", re.I)
_KV_RE = re.compile(r"^([^:\d]{1,40}):\s+(.+)$")

# Every rule below derives the paraphrase ONLY from the original card string
# (synonym / reword / reformat); no new attribute values are ever invented, so
# each paraphrase remains TRUE of the target product by construction.


def paraphrase_constraint(text: str, rng: random.Random) -> str:
    t = re.sub(r"\s+", " ", str(text)).strip()
    low = t.lower()
    m = _BUDGET_CARD_RE.match(t)
    if m:
        p = m.group(1).strip()
        return rng.choice([
            "my budget is around ${p}",
            "I can spend about ${p}",
            "somewhere near ${p} price-wise",
            "roughly ${p} is my price range",
        ]).format(p=p)
    m = _COLOR_CARD_RE.match(t)
    if m:
        c = m.group(1).strip().lower()
        return rng.choice([
            "comes in {c}",
            "I want it in {c}",
            "the {c} one would be right",
            "{c} colored",
        ]).format(c=c)
    if low in MATERIALS:
        return rng.choice([
            "made of {m}",
            "{m} material",
            "something in {m}",
        ]).format(m=low)
    m = _PCT_RE.match(t)
    if m:
        pct, rest = m.group(1), m.group(2).strip().lower()
        if pct == "100":
            return rng.choice([
                "made of pure {r}",
                "all {r}, nothing blended",
                "entirely {r}",
            ]).format(r=rest)
        return rng.choice([
            "{p} percent {r}",
            "a {p} percent {r} blend",
        ]).format(p=pct, r=rest)
    if low == "machine wash":
        return rng.choice([
            "machine washable",
            "can go in the washing machine",
            "fine to machine wash",
        ])
    if low == "hand wash only":
        return rng.choice(["needs hand washing", "hand wash it only"])
    if low == "imported":
        return rng.choice(["an imported piece", "it's imported"])
    m = _CLOSURE_RE.match(t)
    if m:
        c = m.group(1).strip().lower()
        if c in ("pull on", "pull-on"):
            return rng.choice([
                "pull-on style",
                "just pulls on, no fasteners",
            ])
        return rng.choice([
            "closes with a {c}",
            "{c} fastening",
            "done up with a {c}",
        ]).format(c=c)
    m = _KV_RE.match(t)
    if m:
        k, v = m.group(1).strip().lower(), m.group(2).strip().lower()
        return rng.choice([
            "the {k} being {v}",
            "{v} for the {k}",
            "a {k} of {v}",
        ]).format(k=k, v=v)
    body = low
    return rng.choice([
        "ideally something with {b}",
        "{b} is what I'm after",
        "I'd like {b}",
        "let's say {b}",
    ]).format(b=body)


_BUYING_OPENERS = (
    "I need {cat}, and the non-negotiable part: {c}.",
    "Shopping for {cat} today. Must-have: {c}.",
    "Can you help me find {cat}? Key thing: {c}.",
    "So, {cat} is what I'm after. One requirement: {c}.",
    "Hoping to buy {cat}. What really matters: {c}.",
)
_BROWSING_OPENERS = (
    "Just browsing for {cat} at the moment.",
    "I'm after {cat}, though I haven't decided on specifics.",
    "Show me {cat} options; still figuring out what I want.",
    "Something like {cat} maybe? Still exploring.",
    "In the market for {cat}, no firm preferences yet.",
)
_OVERRIDE_OPENERS = (
    "Looking around for {cat}. My leaning right now: {c}.",
    "I want {cat}. Preference-wise: {c}.",
    "Need {cat}. At the moment I'd say: {c}.",
    "{cat} is the goal. What I'd prefer: {c}.",
    "Help me pick {cat}. For now: {c}.",
)
_OVERRIDE_FRAMES = (
    "You know what, forget the earlier bit ({old}) - what I really need is this: {new}.",
    "Change of plans: {new}. That's instead of the {old} idea.",
    "On second thought, skip the part about {old}; here's what matters more: {new}.",
    "Let's go a different direction - the priority now: {new}, not {old}.",
    "I've been rethinking. Drop {old}. New requirement: {new}.",
    "Wait, that earlier preference ({old}) no longer applies. What counts: {new}.",
    "Different idea entirely: {new}. Never mind {old}.",
)
_REPLY_FRAMES = (
    "For that, what matters is: {c}.",
    "Hmm, on that front: {c}.",
    "Good question - {c}.",
    "Let me think. {c}. That's important to me.",
    "What I'd say matters there: {c}.",
    "Here's the thing: {c}.",
)
_NOPREF_FRAMES = (
    "I don't have an additional preference for {a}.",
    "Nothing else comes to mind about {a}.",
    "No particular {a} preference, honestly.",
    "Can't think of anything on {a}.",
)
_DEFLECT_FRAMES = (
    "I don't have a preference for {a}; please use your judgment.",
    "No strong feelings about {a} - you decide.",
    "Whatever you think is best on {a}, really.",
)
_NUDGE_FRAMES = (
    "Those options are not quite right yet. Ask me about one specific attribute.",
    "Not quite it yet. Try asking me about one specific attribute.",
    "Hmm, none of these feel right. Ask me something specific.",
)
_FILLER_HEADS = ("Hmm, ", "Well, ", "Honestly, ", "Let me see... ")
_FILLER_TAILS = (" I guess.", " ...something like that.", " Anyway.")


class Perturber:
    """Per-session, seeded user-side perturbations. H0 delegates to the
    official functions so the fork stays byte-equivalent to the kit evaluator
    when perturbations are off."""

    def __init__(self, tier: str, rng: random.Random, sample: dict) -> None:
        self.tier = tier
        self.rng = rng
        self.trace: list[tuple[str, str]] = []
        self.nonnull_asks = 0
        self.boundary_k = 1
        self.dropped: str | None = None
        if tier in ("H2", "H3"):
            self.boundary_k = rng.randint(1, 3)
        if tier == "H3":
            card = sample.get("intent_card", {})
            pool = list(dict.fromkeys([
                *[str(v) for v in card.get("hard_constraints", [])],
                *[str(v) for v in card.get("soft_preferences", [])],
            ]))
            override = (sample.get("behavior") or {}).get("override") or {}
            protected = {str(override.get("new_value", ""))}
            candidates = [v for v in pool if v not in protected]
            if candidates:
                self.dropped = rng.choice(candidates)

    # -- helpers ------------------------------------------------------------

    def _para(self, original: str) -> str:
        para = paraphrase_constraint(original, self.rng)
        self.trace.append((str(original), para))
        return para

    def _frame(self, frames: tuple[str, ...]) -> str:
        frame = self.rng.choice(frames)
        if self.tier == "H3" and self.rng.random() < 0.30:
            frame = self._typo(frame)
        return frame

    def _typo(self, frame: str) -> str:
        # Adjacent-char swap inside a TEMPLATE word only (never a placeholder,
        # so never constraint content or category anchor tokens).
        words = frame.split(" ")
        idx = [i for i, w in enumerate(words) if w.isalpha() and len(w) >= 5 and "{" not in w]
        if not idx:
            return frame
        i = self.rng.choice(idx)
        w = words[i]
        j = self.rng.randrange(1, len(w) - 1)
        words[i] = w[:j - 1] + w[j] + w[j - 1] + w[j + 1:]
        return " ".join(words)

    def _noise(self, msg: str) -> str:
        if self.tier != "H3":
            return msg
        if self.rng.random() < 0.35:
            msg = self.rng.choice(_FILLER_HEADS) + msg
        if self.rng.random() < 0.25:
            msg = msg + self.rng.choice(_FILLER_TAILS)
        return msg

    # -- message generators -------------------------------------------------

    def initial_message(self, sample: dict, category: str, disclosed: set[str]) -> str:
        self.trace = []
        if self.tier == "H0":
            return initial_message(sample, category, disclosed)
        scenario = sample["scenario_type"]
        if scenario == "buying" and sample["intent_card"].get("hard_constraints"):
            constraint = str(sample["intent_card"]["hard_constraints"][0])
            disclosed.add(constraint)
            return self._noise(self._frame(_BUYING_OPENERS).format(cat=category, c=self._para(constraint)))
        if scenario == "intent_override":
            old_value = str(sample["behavior"]["override"]["old_value"])
            return self._noise(self._frame(_OVERRIDE_OPENERS).format(cat=category, c=self._para(old_value)))
        return self._noise(self._frame(_BROWSING_OPENERS).format(cat=category))

    def override_message(self, override: dict) -> str:
        self.trace = []
        if self.tier == "H0":
            return str(override.get("message", "Actually, please ignore my earlier preference."))
        old = self._para(str(override.get("old_value", "")))
        new = self._para(str(override.get("new_value", "")))
        return self._noise(self._frame(_OVERRIDE_FRAMES).format(old=old, new=new))

    def customer_reply(self, sample: dict, ask_attribute: object, disclosed: set[str], boundary_used: bool) -> tuple[str, bool]:
        self.trace = []
        if self.tier == "H0":
            return customer_reply(sample, ask_attribute, disclosed, boundary_used)
        rng = self.rng
        attribute = ask_attribute if isinstance(ask_attribute, str) else None
        if attribute and attribute not in ALLOWED_ATTRIBUTES:
            attribute = "other"
        if sample["scenario_type"] == "boundary" and not boundary_used and attribute:
            self.nonnull_asks += 1
            if self.nonnull_asks >= self.boundary_k:
                return self._noise(self._frame(_DEFLECT_FRAMES).format(a=attribute)), True
        if not attribute:
            return self._noise(self._frame(_NUDGE_FRAMES)), boundary_used
        constraints = [
            *[str(value) for value in sample["intent_card"].get("hard_constraints", [])],
            *[str(value) for value in sample["intent_card"].get("soft_preferences", [])],
        ]
        if self.dropped is not None:
            constraints = [value for value in constraints if value != self.dropped]
        cap = 2
        if self.tier in ("H2", "H3"):
            rng.shuffle(constraints)
            if attribute == "other":
                if rng.random() < 0.35:
                    attribute = rng.choice(SPECIFIC_ATTRIBUTES)
                else:
                    cap = 1
        matches = [
            value for value in constraints
            if value not in disclosed and (attribute == "other" or classify_constraint(value) == attribute)
        ][:cap]
        if not matches:
            return self._noise(self._frame(_NOPREF_FRAMES).format(a=attribute)), boundary_used
        disclosed.update(matches)
        paras = [self._para(value) for value in matches]
        joiner = rng.choice(["; ", ", and also "]) if len(paras) > 1 else ""
        return self._noise(self._frame(_REPLY_FRAMES).format(c=joiner.join(paras))), boundary_used


def initial_message(sample: dict, category: str, disclosed: set[str]) -> str:
    scenario = sample["scenario_type"]
    if scenario == "buying" and sample["intent_card"].get("hard_constraints"):
        constraint = str(sample["intent_card"]["hard_constraints"][0])
        disclosed.add(constraint)
        return f"I'm looking for {category}. A key requirement is: {constraint}."
    if scenario == "intent_override":
        old_value = str(sample["behavior"]["override"]["old_value"])
        return f"I'm looking for {category}. {old_value}"
    return f"I'm looking for {category}, but I'm still exploring."


def customer_reply(sample: dict, ask_attribute: object, disclosed: set[str], boundary_used: bool) -> tuple[str, bool]:
    attribute = ask_attribute if isinstance(ask_attribute, str) else None
    if sample["scenario_type"] == "boundary" and not boundary_used and attribute:
        return f"I don't have a preference for {attribute}; please use your judgment.", True
    if not attribute:
        return "Those options are not quite right yet. Ask me about one specific attribute.", boundary_used
    if attribute not in ALLOWED_ATTRIBUTES:
        attribute = "other"
    constraints = [
        *[str(value) for value in sample["intent_card"].get("hard_constraints", [])],
        *[str(value) for value in sample["intent_card"].get("soft_preferences", [])],
    ]
    matches = [
        value for value in constraints
        if value not in disclosed and (attribute == "other" or classify_constraint(value) == attribute)
    ][:2]
    if not matches:
        return f"I don't have an additional preference for {attribute}.", boundary_used
    disclosed.update(matches)
    return "For that, what matters is: " + "; ".join(matches) + ".", boundary_used


def metric_summary(sessions: list[dict]) -> dict:
    if not sessions:
        return {"sample_count": 0, "hit_rate_at_10": 0.0, "mrr": 0.0, "mttc": None}
    hit_rate = sum(int(item["hit"]) for item in sessions) / len(sessions)
    mrr = statistics.fmean(item["reciprocal_rank"] for item in sessions)
    mttc = statistics.fmean(
        item["first_hit_turn"] if item["first_hit_turn"] is not None else MAX_TURNS + 1 for item in sessions
    )
    return {
        "sample_count": len(sessions),
        "hit_rate_at_10": round(hit_rate, 6),
        "mrr": round(mrr, 6),
        "mttc": round(mttc, 6),
    }


def materialize_hidden_fields(sample: dict, products: dict[str, dict]) -> tuple[dict, dict]:
    if "intent_card" in sample and "behavior" in sample:
        return sample["intent_card"], sample["behavior"]
    target = str(sample["ground_truth"]["parent_asin"])
    product = products[target]
    card = intent_card(product)
    seed_source = f"{sample.get('sample_id', '')}\0{sample.get('scenario_type', '')}"
    rng = random.Random(seed_source)
    behavior = behavior_for(str(sample["scenario_type"]), card, rng)
    return card, behavior


def evaluate(
    agent: Agent,
    samples: list[dict],
    catalog_ids: set[str],
    categories: dict[str, list[str]],
    products: dict[str, dict],
) -> dict:
    sessions: list[dict] = []
    transcripts: list[dict] = []
    total_prompt_tokens = 0
    total_completion_tokens = 0
    for sample in samples:
        session_id = f"public_{uuid.uuid4().hex}"
        agent.reset(session_id, sample["user_profile"])
        target = str(sample["ground_truth"]["parent_asin"])
        effective_intent_card, effective_behavior = materialize_hidden_fields(sample, products)
        effective_sample = {**sample, "intent_card": effective_intent_card, "behavior": effective_behavior}
        disclosed: set[str] = set()
        boundary_used = False
        override_applied = sample["scenario_type"] != "intent_override"
        pert_rng = random.Random(
            f"{sample.get('sample_id', '')}\0{sample.get('scenario_type', '')}\0{HARDENED_TIER}\0hardened"
        )
        pert = Perturber(HARDENED_TIER, pert_rng, effective_sample)
        user_message = pert.initial_message(effective_sample, coarse_category(categories.get(target, [])), disclosed)
        dialog: list[dict] = []
        pending_trace = list(pert.trace)
        hit_turn: int | None = None
        best_rank: int | None = None
        for turn in range(1, MAX_TURNS + 1):
            dialog.append({"turn": turn, "user": user_message, "paraphrase_trace": pending_trace})
            try:
                response = agent.respond(session_id, user_message, turn, TOP_K)
            except Exception:
                response = {"message": "", "ask_attribute": None, "recommendations": []}
            if not isinstance(response, dict) or not isinstance(response.get("message"), str):
                response = {"message": "", "ask_attribute": None, "recommendations": []}
            usage = response.get("usage")
            if isinstance(usage, dict):
                if isinstance(usage.get("prompt_tokens"), int) and usage["prompt_tokens"] >= 0:
                    total_prompt_tokens += usage["prompt_tokens"]
                if isinstance(usage.get("completion_tokens"), int) and usage["completion_tokens"] >= 0:
                    total_completion_tokens += usage["completion_tokens"]
            ranked = normalize_recommendations(response.get("recommendations"), catalog_ids)
            dialog[-1]["ask"] = response.get("ask_attribute") if isinstance(response.get("ask_attribute"), (str, type(None))) else None
            dialog[-1]["n_recs"] = len(ranked)
            if override_applied and target in ranked:
                best_rank = ranked.index(target) + 1
                hit_turn = turn
                break
            if turn == MAX_TURNS:
                break
            override = effective_sample.get("behavior", {}).get("override") or {}
            if not override_applied and turn + 1 == int(override.get("turn", 3)):
                override_applied = True
                new_value = str(override.get("new_value", ""))
                if new_value:
                    disclosed.add(new_value)
                user_message = pert.override_message(override)
            else:
                user_message, boundary_used = pert.customer_reply(
                    effective_sample, response.get("ask_attribute"), disclosed, boundary_used
                )
            pending_trace = list(pert.trace)
        sessions.append({
            "sample_id": sample["sample_id"],
            "scenario_type": sample["scenario_type"],
            "hit": hit_turn is not None,
            "first_hit_turn": hit_turn,
            "best_rank": best_rank,
            "reciprocal_rank": 0.0 if best_rank is None else 1.0 / best_rank,
        })
        transcripts.append({
            "sample_id": sample["sample_id"],
            "scenario_type": sample["scenario_type"],
            "tier": HARDENED_TIER,
            "intent_card": effective_intent_card,
            "override": (effective_behavior.get("override") if isinstance(effective_behavior, dict) else None),
            "dropped_card_entry": pert.dropped,
            "dialog": dialog,
            "hit_turn": hit_turn,
            "best_rank": best_rank,
        })
    if HARDENED_LOG:
        with Path(HARDENED_LOG).open("w", encoding="utf-8") as handle:
            for row in transcripts:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    overall = metric_summary(sessions)
    efficiency = max(0.0, min(1.0, (11.0 - float(overall["mttc"])) / 10.0))
    technical_score = 0.50 * overall["hit_rate_at_10"] + 0.30 * overall["mrr"] + 0.20 * efficiency
    grouped: dict[str, list[dict]] = defaultdict(list)
    for session in sessions:
        grouped[session["scenario_type"]].append(session)
    result = {
        **overall,
        "efficiency": round(efficiency, 6),
        "recommended_technical_score": round(technical_score, 6),
        "reported_token_usage": {
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_tokens": total_prompt_tokens + total_completion_tokens,
        },
        "scenario_metrics": {name: metric_summary(grouped[name]) for name in sorted(grouped)},
        "sessions": sessions,
    }
    if HARDENED_TIER != "H0":
        # Provenance stamp only; omitted at H0 so the H0 output stays
        # byte-identical to the official evaluator's.
        result["hardened_tier"] = HARDENED_TIER
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="TechJam public-set local evaluator")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--output", default="results.json")
    args = parser.parse_args()
    samples = load_jsonl(args.dataset)
    catalog_ids, categories, products = catalog_index(args.catalog)
    result = evaluate(Agent(args.catalog), samples, catalog_ids, categories, products)
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "sessions"}, indent=2))


if __name__ == "__main__":
    main()
