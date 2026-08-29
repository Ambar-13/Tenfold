"""TENFOLD 2.0 Agent: structured state + selective invalidation.

Orchestration per turn:
  1. Turn 1: parse the opener (category anchor + any stated constraint).
  2. Turn >1: run semantic override detection FIRST; if it fires, apply
     the configured invalidation mode; otherwise compile the message to
     per-slot KEEP/SET/DELETE/UNKNOWN ops and apply them.
  3. Choose the ask attribute per the ask policy.
  4. If the hold controller says recommend, query retrieval with the
     positive evidence and pad to exactly top_k unique catalog ASINs;
     otherwise emit an empty recommendations list (legal while holding).

Env switches (read ONCE at Agent init; every ablation is a switch):
  TENFOLD_OVERRIDE  = erase | keep_category | selective   (default selective)
  TENFOLD_HOLD      = hold0 | hold1 | hold2 | hold3 | conf (default conf; tuning-split argmax)
  TENFOLD_ASK       = other_first | specific | other_always (default other_first)
  TENFOLD_RETRIEVAL = v1 | v2                              (default v2 = the shipped
                      production retrieval; v1 is the legacy ablation path, kept
                      reproducible by passing TENFOLD_RETRIEVAL=v1 explicitly)
  TENFOLD_PARSE     = frame | robust (default robust. `frame` is
                      the legacy exact-template parser; `robust` classifies
                      each user message semantically — no-preference /
                      deflection, nudge, disclosure, override — and strips
                      conversational frame tokens before segments become
                      constraint text. Legacy arms stay reproducible by
                      passing TENFOLD_PARSE=frame explicitly.)
  TENFOLD_COVERAGE  = contain | idf (default idf; read by RetrieverV2 at init.
                      IDF-weighted content-token coverage demoted
                      to a BM25-band tie-breaker, vs the legacy full-token
                      containment rerank. Legacy arms: TENFOLD_COVERAGE=contain.)
  TENFOLD_CONF_TUNE = JSON dict overriding policy.CONF_DEFAULTS (controller
                      tuning only; experiments/tune_conf.sh)
  TENFOLD_CONF_LOG  = path; when set and TENFOLD_HOLD=conf, append one JSON
                      line per turn with the controller decision + features
                      (instrumentation only; never affects behavior)

TENFOLD_HOLD=conf requires TENFOLD_RETRIEVAL=v2, which is the default, so a
bare Agent(catalog_path) selects the full production configuration: the per-turn
{ask+hold, ask+recommend} decision consumes RetrieverV2 confidence
features (policy.Policy.should_recommend_conf). Under v1, conf aliases
fixed hold2.

Responses are strictly contract-conformant: message (str), ask_attribute
(enum or null), recommendations (exactly 10 unique catalog-valid
{"parent_asin": ...} dicts when recommending, [] when holding), usage.
No exception ever escapes respond().
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import override as override_mod
from . import policy as policy_mod
from . import retrieval as retrieval_mod
from . import state as state_mod

HOLD_MESSAGE = "Got it. Let me narrow this down before I recommend anything."
RECOMMEND_MESSAGE = "Here are my best matches so far. Anything else that matters to you?"
FALLBACK_MESSAGE = "Let me keep refining based on what you've told me."

ZERO_USAGE = {"prompt_tokens": 0, "completion_tokens": 0}


class Agent:
    def __init__(self, catalog_path: str | Path = "data/catalog.jsonl") -> None:
        # Env read once at init; nothing else reads the environment.
        self.override_mode = os.environ.get("TENFOLD_OVERRIDE", "selective")
        if self.override_mode not in ("erase", "keep_category", "selective"):
            self.override_mode = "selective"
        self.hold_mode = os.environ.get("TENFOLD_HOLD", "conf")
        self.ask_mode = os.environ.get("TENFOLD_ASK", "other_first")
        self.retrieval_mode = os.environ.get("TENFOLD_RETRIEVAL", "v2")
        self.parse_mode = os.environ.get("TENFOLD_PARSE", "robust")
        if self.parse_mode not in ("frame", "robust"):
            self.parse_mode = "robust"
        conf_tunables = None
        raw = os.environ.get("TENFOLD_CONF_TUNE", "")
        if raw:
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    conf_tunables = loaded
            except ValueError:
                pass
        self.conf_log_path = os.environ.get("TENFOLD_CONF_LOG", "")
        self.policy = policy_mod.Policy(self.ask_mode, self.hold_mode, conf_tunables)
        if self.retrieval_mode == "v2":
            self.retriever = retrieval_mod.RetrieverV2(catalog_path)
        else:
            self.retrieval_mode = "v1"
            self.retriever = retrieval_mod.RetrieverV1(catalog_path)
        self._states: dict[str, state_mod.SessionState] = {}

    def reset(self, session_id: str, user_profile: dict) -> None:
        try:
            profile = dict(user_profile) if isinstance(user_profile, dict) else {}
        except Exception:
            profile = {}
        self._states[session_id] = state_mod.SessionState(session_id=session_id, user_profile=profile)

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        try:
            return self._respond(session_id, str(user_message or ""), int(turn), int(top_k))
        except Exception:
            # Never let an exception escape: safe, contract-conformant fallback.
            return {
                "message": FALLBACK_MESSAGE,
                "ask_attribute": None,
                "recommendations": [],
                "usage": dict(ZERO_USAGE),
            }

    # ---- internals ----
    def _respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        state = self._states.get(session_id)
        if state is None:  # defensive: reset() should always have been called
            state = state_mod.SessionState(session_id=session_id)
            self._states[session_id] = state
        state.turn = turn

        if turn == 1:
            if self.parse_mode == "robust":
                anchor, ops, _meta = state_mod.parse_opener_robust(user_message, turn)
            else:
                anchor, ops, _meta = state_mod.parse_opener(user_message, turn)
            if anchor:
                state.category_anchor = anchor
            state.apply(ops)
        elif self.parse_mode == "robust":
            # Semantic no-info classes (no-preference/deflection, nudge) are
            # checked BEFORE override detection: a paraphrased refusal must
            # never be mistaken for an override or ingested as constraints.
            if state_mod.classify_no_info(user_message) is not None:
                ops, meta = state_mod.parse_turn_robust(user_message, turn, state)
                state.apply(ops)
                if meta.exhausted_attr:
                    state.exhausted_attrs.add(meta.exhausted_attr)
            else:
                signal = override_mod.detect(user_message, state)
                if signal is not None:
                    override_mod.apply(state, signal, self.override_mode, turn)
                else:
                    ops, meta = state_mod.parse_turn_robust(user_message, turn, state)
                    state.apply(ops)
                    if meta.exhausted_attr:
                        state.exhausted_attrs.add(meta.exhausted_attr)
        else:
            signal = override_mod.detect(user_message, state)
            if signal is not None:
                override_mod.apply(state, signal, self.override_mode, turn)
            else:
                ops, meta = state_mod.parse_turn(user_message, turn)
                state.apply(ops)
                if meta.exhausted_attr:
                    state.exhausted_attrs.add(meta.exhausted_attr)

        ask = self.policy.choose_ask(state, turn)
        if ask is not None and ask not in state_mod.ASK_ENUM:
            ask = "other"
        state.last_ask = ask

        recommendations: list[dict] = []
        if self.hold_mode == "conf" and self.retrieval_mode == "v2":
            # Confidence controller: per-turn ask+hold vs ask+recommend.
            asins, conf_feats = self.retriever.search_state_conf(state, top_k)
            opened = self.policy.should_recommend_conf(state, turn, conf_feats)
            if self.conf_log_path:
                self._log_conf(session_id, turn, opened, conf_feats)
            if opened:
                asins = self.retriever.pad(asins, top_k)
                recommendations = [{"parent_asin": asin} for asin in asins]
        elif self.policy.should_recommend(state, turn):
            if self.retrieval_mode == "v2":
                asins = self.retriever.search_state(state, top_k)
            else:
                asins = self.retriever.search(state.evidence_texts(), top_k)
            asins = self.retriever.pad(asins, top_k)
            recommendations = [{"parent_asin": asin} for asin in asins]

        return {
            "message": RECOMMEND_MESSAGE if recommendations else HOLD_MESSAGE,
            "ask_attribute": ask,
            "recommendations": recommendations,
            "usage": dict(ZERO_USAGE),
        }

    def _log_conf(self, session_id: str, turn: int, opened: bool, feats: dict) -> None:
        """Instrumentation only (TENFOLD_CONF_LOG): one JSON line per conf turn."""
        try:
            record = {"session_id": session_id, "turn": turn, "open": bool(opened)}
            record.update(feats)
            with open(self.conf_log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        except Exception:
            pass  # logging must never break respond()
