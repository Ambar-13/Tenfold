"""Intent-override detection and selective invalidation.

Detection is SEMANTIC, not prefix-match (the private set may paraphrase):
  1. Cue battery — strong cues (ignore / forget / disregard / scratch that /
     changed my mind / no longer / never mind) fire alone; weak cues
     (actually / instead / rather than / on second thought / "what I need
     is") need two independent hits.
  2. Payload extraction — "what I need is: X"-style patterns, with a
     cue-stripping fallback.
  3. Slot-conflict trigger — a free-text message introducing a constraint
     in the same conflict-prone slot as an existing active one with a
     disjoint value counts as an override even without a cue.

Simulator reply-template messages ("For that, what matters is: ...") are
NEVER treated as overrides: card strings are verbatim product attributes
and may contain cue-like tokens.

Actions (env TENFOLD_OVERRIDE):
  selective (default) — DELETE constraints named as abandoned (opener-
    stated preferences on a generic "earlier preference" reference, or
    text-matched old values) and constraints in direct slot-conflict with
    the new value; KEEP the category anchor and all compatible
    constraints; SET the new value as a hard constraint; re-ask `other`
    once afterward.
  keep_category — delete ALL constraints, keep the category anchor, SET new.
  erase — delete ALL constraints AND the category anchor, SET new.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import state as state_mod

STRONG_CUE_RES = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bignore (?:my|the|that|all|what)\b",
        r"\bforget (?:my|the|that|about|what|it)\b",
        r"\bdisregard\b",
        r"\bscratch that\b",
        r"\bchanged? my mind\b",
        r"\bchange of plans?\b",
        r"\bnever ?mind\b",
        r"\bno longer (?:want|need|care|interested|after)\b",
        r"\bdon'?t (?:want|need|care about) (?:that|it|those) (?:anymore|any more)\b",
        r"\bnot (?:that|what) i (?:want|need|meant)\b",
    )
]
WEAK_CUE_RES = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bactually\b",
        r"\binstead\b",
        r"\brather than\b",
        r"\bon second thought\b",
        r"\bwhat i (?:really |actually )?(?:need|want) is\b",
        r"\bnow i (?:want|need|realize)\b",
        r"\blet'?s go with\b",
    )
]
PAYLOAD_RES = [
    re.compile(r"what i (?:really |actually )?(?:need|want) is:?\s*(?P<p>.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"what matters(?: to me)?(?: now)? is:?\s*(?P<p>.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"i(?:'m| am) (?:now )?(?:looking for|after|interested in):?\s*(?P<p>.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"(?:instead|now)[, ]+i (?:want|need|prefer|would like):?\s*(?P<p>.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"\blet'?s go with:?\s*(?P<p>.+)$", re.IGNORECASE | re.DOTALL),
]
GENERIC_ABANDON_RE = re.compile(
    r"(?:(?:earlier|previous|prior|original|first)\s+(?:preference|preferences|requirement|requirements|request|choice))"
    r"|(?:what i said (?:earlier|before))",
    re.IGNORECASE,
)
CUE_STRIP_RE = re.compile(
    r"^(?:actually|ok(?:ay)?|hmm+|well|so|no|wait|on second thought)[, ]+"
    r"|(?:ignore|forget(?: about)?|disregard)\s+(?:my|the|that|all|what)\s+[^.;]*[.;]?"
    r"|scratch that[.,;]?"
    r"|i(?:'ve| have)? changed my mind[.,;]?"
    r"|never ?mind[.,;]?",
    re.IGNORECASE,
)


@dataclass
class OverrideSignal:
    new_texts: list = field(default_factory=list)
    generic_abandon: bool = False
    abandoned_texts: list = field(default_factory=list)  # old values named in the message
    via: str = "cue"                                     # cue | slot_conflict


def _extract_payload(message: str) -> list[str]:
    for pattern in PAYLOAD_RES:
        match = pattern.search(message)
        if match:
            payload = match.group("p").strip()
            if payload.endswith("."):
                payload = payload[:-1]
            return [seg.strip() for seg in payload.split(";") if seg.strip()]
    # fallback: strip cue phrases, treat the remainder as the payload
    stripped = message
    for _ in range(4):
        new = CUE_STRIP_RE.sub("", stripped).strip()
        if new == stripped:
            break
        stripped = new
    stripped = stripped.strip(" .,;")
    if stripped and state_mod.content_tokens(stripped):
        return [seg.strip() for seg in stripped.split(";") if seg.strip()]
    return []


def _named_abandoned(message: str, payload_texts: list, state) -> list[str]:
    """Texts of active constraints referenced in the NON-payload part of the message.

    The payload (the new values) is removed first so the new constraint —
    which may restate a value already in state — never names itself as
    abandoned. Requires at least half of a constraint's content tokens to
    appear in the remaining prefix, so stray shared words don't fire.
    """
    prefix = message
    for payload in payload_texts:
        prefix = prefix.replace(payload, " ")
    prefix_tokens = state_mod.content_tokens(prefix)
    named: list[str] = []
    for constraint in state.active_constraints():
        tokens = constraint.tokens()
        if tokens and len(tokens & prefix_tokens) >= max(1, (len(tokens) + 1) // 2):
            named.append(constraint.text)
    return named


def detect(message: str, state) -> OverrideSignal | None:
    """Return an OverrideSignal if this user message is an intent override."""
    text = message.strip()
    if not text or state_mod.REPLY_RE.match(text):
        return None
    lowered = text.lower()
    if (state_mod.NO_ADDITIONAL_RE.search(text) or state_mod.BOUNDARY_RE.search(text)
            or state_mod.NUDGE_MARKER in lowered):
        return None
    strong = [p for p in STRONG_CUE_RES if p.search(text)]
    weak = [p for p in WEAK_CUE_RES if p.search(text)]
    if strong or len(weak) >= 2:
        signal = OverrideSignal(via="cue")
        signal.new_texts = _extract_payload(text)
        signal.generic_abandon = bool(GENERIC_ABANDON_RE.search(text)) or not signal.new_texts
        signal.abandoned_texts = _named_abandoned(text, signal.new_texts, state)
        return signal
    # Slot-conflict trigger: a new free-text constraint that directly
    # conflicts (same conflict-prone slot, disjoint value) with active state.
    conflict_texts: list[str] = []
    for segment in state_mod.split_free_text(text):
        candidate = state_mod.make_constraint(segment, state.turn, origin="free")
        if not candidate.text or candidate.polarity <= 0:
            continue
        if candidate.slot_type not in state_mod.CONFLICT_SLOTS:
            continue
        for existing in state.active_constraints():
            if state_mod.SessionState._conflicts(existing, candidate):
                conflict_texts.append(candidate.text)
                break
    if conflict_texts:
        return OverrideSignal(new_texts=conflict_texts, via="slot_conflict")
    return None


def apply(state, signal: OverrideSignal, mode: str, turn: int) -> list:
    """Compile the override to per-slot ops per the active mode; apply them."""
    ops: list = []
    if mode == "erase":
        state.category_anchor = None
        for constraint in state.active_constraints():
            ops.append(state_mod.Op(kind="DELETE", constraint=constraint, note="override-erase"))
    elif mode == "keep_category":
        for constraint in state.active_constraints():
            ops.append(state_mod.Op(kind="DELETE", constraint=constraint, note="override-keep_category"))
    else:  # selective (default)
        new_constraints = [
            state_mod.make_constraint(text, turn, origin="override", hard=True)
            for text in signal.new_texts
        ]
        for constraint in state.active_constraints():
            named = constraint.text in signal.abandoned_texts
            abandoned = (
                named
                or (signal.generic_abandon and constraint.origin == "opener" and constraint.is_preference)
            )
            conflicted = any(
                state_mod.SessionState._conflicts(constraint, new)
                for new in new_constraints if new.text
            )
            if abandoned or conflicted:
                ops.append(state_mod.Op(
                    kind="DELETE", constraint=constraint,
                    note="override-abandoned" if abandoned else "override-conflict",
                ))
            else:
                ops.append(state_mod.Op(kind="KEEP", constraint=constraint, note="override-compatible"))
    for text in signal.new_texts:
        constraint = state_mod.make_constraint(text, turn, origin="override", hard=True)
        if constraint.text:
            ops.append(state_mod.Op(kind="SET", constraint=constraint, note=f"override-new:{signal.via}"))
    state.apply(ops)
    # re-ask `other` once afterward: the card may still hold undisclosed values
    state.exhausted_attrs.discard("other")
    state.force_other = True
    return ops
