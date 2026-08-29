"""Ask policy and hold controller (fixed hold depths + confidence controller).

Hold (env TENFOLD_HOLD): hold0..hold3 — empty recommendations while
turn <= depth, but the agent STILL asks its question that turn. Turn 10
always recommends (turn-10 asks are dead: the simulator generates no
reply). `conf` is the confidence controller: per-turn decision
among {ask+hold, ask+recommend} driven by RetrieverV2 confidence
features (see should_recommend_conf); it requires retrieval v2 and
aliases hold2 under v1 (HOLD_DEPTHS carries the alias).

Ask (env TENFOLD_ASK):
  other_first (default) — ask `other` while the card yields (`other`
    matches ANY undisclosed constraint, so it is uniquely exhaustive);
    once `other` comes back empty, ask a targeted specific attribute for
    a known-missing slot; null once everything is exhausted.
  specific — adaptive rotation over the attributes the simulator's
    classifier can actually produce (never category/brand), advancing
    when one comes back empty (probe no_other mode).
  other_always — ask `other` every turn (probe main mode).
"""
from __future__ import annotations

# Attributes the simulator's classify_constraint can actually return,
# ordered by classification frequency on catalog constraint strings.
SPECIFIC_ATTRIBUTES = ["feature", "material", "budget", "style", "color", "size", "use_case"]

HOLD_DEPTHS = {"hold0": 0, "hold1": 1, "hold2": 2, "hold3": 3, "conf": 2}
ASK_MODES = ("other_first", "specific", "other_always")
MAX_TURNS = 10

# Confidence-controller tunables. Fit on the tuning split ONLY
# (sample_id numeric suffix even) via experiments/tune_conf.sh; mapping in
# experiments/tune_conf_map.md. Baked defaults = the TUNE winner (cfg2,
# TUNE TS 0.89473; cfg0 ties byte-identically — sat_min_constraints 1 vs 2
# is inert on the public set, 2 kept as the conservative choice).
# TENFOLD_CONF_TUNE (JSON dict, read once at Agent init and passed in)
# exists solely so the tuning script can sweep.
CONF_DEFAULTS = {
    # open early only if top-1 constraint coverage is saturated AND its
    # coverage-score lead over top-2 exceeds gap_min (strict >)
    "gap_min": 0.0,
    # minimum active positive constraints for a saturation-open on turn >= 2
    "sat_min_constraints": 2,
    # minimum on turn 1 (buying openers quote hard_constraints[0] verbatim;
    # browsing openers carry zero constraints and can never open on turn 1)
    "opener_min_constraints": 1,
    # unconditional open from this turn on (the fixed-hold fallback depth;
    # 3 == hold2-equivalent — holds beyond 3 are measured TS-losing)
    "fallback_turn": 3,
}


class Policy:
    def __init__(self, ask_mode: str = "other_first", hold_mode: str = "hold2",
                 conf_tunables: dict | None = None) -> None:
        self.ask_mode = ask_mode if ask_mode in ASK_MODES else "other_first"
        self.hold_mode = hold_mode
        self.hold_depth = HOLD_DEPTHS.get(hold_mode, 2)
        self.conf = dict(CONF_DEFAULTS)
        if conf_tunables:
            for key in self.conf:
                if key in conf_tunables:
                    self.conf[key] = type(self.conf[key])(conf_tunables[key])

    def should_recommend(self, state, turn: int) -> bool:
        if turn >= MAX_TURNS:
            return True  # last chance: always recommend on turn 10
        return turn > self.hold_depth

    def should_recommend_conf(self, state, turn: int, conf: dict | None) -> bool:
        """Per-turn decision: ask+hold (False) vs ask+recommend (True).

        Confidence features (`conf`, from RetrieverV2.search_state_conf):
          n_constraints  — active positive constraints with content tokens
          top1_saturated — top-1 token-contains EVERY active constraint
          top1_level     — relaxation-ladder level of top-1 (0 = fully filtered)
          gap            — top-1 minus top-2 coverage score (rerank units)
          card_exhausted — `other` came back empty: replies stop yielding
        Decision (thresholds in self.conf, TUNE-fit):
          * turn >= MAX_TURNS or turn >= fallback_turn -> recommend;
          * card exhausted -> recommend (holding buys nothing further);
          * else recommend iff top-1 coverage is saturated at ladder level 0
            with a strictly positive lead (gap > gap_min) over top-2 and at
            least {opener,sat}_min_constraints constraints are active —
            i.e. never before turn 2 unless the opener itself saturates.
        `conf is None` (retrieval v1): alias fixed hold2 via hold_depth.
        The decision is re-evaluated fresh every turn: an intent override
        that de-saturates top-1 re-holds until the fallback depth.
        """
        if turn >= MAX_TURNS:
            return True  # last chance: always recommend on turn 10
        if conf is None:
            return turn > self.hold_depth
        if turn >= self.conf["fallback_turn"]:
            return True
        if conf.get("card_exhausted"):
            return True
        min_n = (self.conf["opener_min_constraints"] if turn == 1
                 else self.conf["sat_min_constraints"])
        return (
            conf.get("n_constraints", 0) >= min_n
            and bool(conf.get("top1_saturated"))
            and conf.get("top1_level", 1) == 0
            and float(conf.get("gap", 0.0)) > self.conf["gap_min"]
        )

    def choose_ask(self, state, turn: int) -> str | None:
        if turn >= MAX_TURNS:
            return None  # dead ask: no reply is ever generated for turn 10
        if self.ask_mode == "other_always":
            return "other"
        if self.ask_mode == "specific":
            for attribute in SPECIFIC_ATTRIBUTES:
                if attribute not in state.exhausted_attrs:
                    return attribute
            return None
        # other_first
        if state.force_other:
            state.force_other = False
            return "other"
        if "other" not in state.exhausted_attrs:
            return "other"
        known = state.known_slot_types()
        for attribute in SPECIFIC_ATTRIBUTES:
            if attribute not in known and attribute not in state.exhausted_attrs:
                return attribute
        return None
