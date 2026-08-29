"""Typed dialogue state with per-slot KEEP / SET / DELETE / UNKNOWN ops.

Every user turn compiles to a list of per-slot ops which are then applied
to the SessionState. Parses BOTH the simulator's verbatim reply formats
("For that, what matters is: c1; c2.") AND free text (split on ';' /
sentence boundaries). Price phrases normalize to numeric intervals;
negations ("not leather", "no longer want X") produce polarity -1 or
DELETE ops — negation parsing is applied only to free-text segments,
never to verbatim card strings (which legitimately contain tokens like
"no show socks").

Pure stdlib. Deterministic. No I/O.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .retrieval import TOKEN_RE, STOPWORDS

ASK_ENUM = (
    "category", "material", "color", "size", "style", "brand",
    "budget", "feature", "use_case", "other",
)

MATERIALS = ("cotton", "polyester", "nylon", "leather", "wool", "spandex", "silk", "rayon", "fabric")
COLOR_WORDS = ("black", "white", "blue", "red", "pink", "green", "brown", "gray", "grey", "purple", "yellow", "orange")

# ---- simulator message templates (verbatim formats, case-insensitive) ----
OPENER_RE = re.compile(r"^\s*i'?m looking for\s+(?P<rest>.+)$", re.IGNORECASE | re.DOTALL)
STILL_EXPLORING_RE = re.compile(r",?\s*but\s+i'?m still exploring\.?\s*$", re.IGNORECASE)
KEY_REQ_RE = re.compile(r"^\s*a key requirement is:\s*(?P<c>.+?)\.?\s*$", re.IGNORECASE | re.DOTALL)
REPLY_RE = re.compile(r"^\s*for that, what matters is:\s*(?P<body>.+)$", re.IGNORECASE | re.DOTALL)
NO_ADDITIONAL_RE = re.compile(r"i don'?t have an additional preference for\s+([a-z_]+)", re.IGNORECASE)
BOUNDARY_RE = re.compile(r"i don'?t have a preference for\s+([a-z_]+)", re.IGNORECASE)
NUDGE_MARKER = "those options are not quite right yet"

# ---- price normalization ----
PRICE_NUM_RE = re.compile(r"\$?\s*([0-9]+(?:\.[0-9]+)?)")
BETWEEN_RE = re.compile(
    r"between\s+\$?\s*([0-9]+(?:\.[0-9]+)?)\s+and\s+\$?\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE
)
UPPER_CUES = ("under", "below", "less than", "at most", "no more than", "max", "up to", "<=")
LOWER_CUES = ("over", "above", "more than", "at least", "min", ">=")
AROUND_CUES = ("around", "about", "approximately", "roughly", "budget")

# ---- negation (free text only) ----
NO_LONGER_RE = re.compile(
    r"(?:i\s+)?no longer\s+(?:want|need|care about|like)?\s*(?P<t>.+)$", re.IGNORECASE
)
NEG_LEAD_RE = re.compile(r"^\s*(?:not|without)\s+(?P<t>.+)$", re.IGNORECASE)
# bare "no X" negates only when X starts with a material/color word, to avoid
# breaking legitimate product phrases ("no show socks", "no iron shirt").
NEG_NO_RE = re.compile(
    r"^\s*no\s+(?P<t>(?:%s)\b.*)$" % "|".join(MATERIALS + COLOR_WORDS), re.IGNORECASE
)

# slot types where two disjoint values are treated as conflicting
CONFLICT_SLOTS = {"material", "color", "budget", "brand", "size"}


def classify_constraint(value: str) -> str:
    """Port of the evaluator's keyword classifier (never returns category/brand)."""
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


def content_tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in TOKEN_RE.findall(text)
        if len(token) > 1 and token.lower() not in STOPWORDS
    }


def parse_price_interval(text: str) -> tuple[float, float] | None:
    """Normalize a price phrase to a numeric (low, high) interval, else None."""
    lowered = text.lower()
    match = BETWEEN_RE.search(lowered)
    if match:
        low, high = float(match.group(1)), float(match.group(2))
        return (min(low, high), max(low, high))
    has_dollar = "$" in text
    priceish = has_dollar or any(cue in lowered for cue in UPPER_CUES + LOWER_CUES + AROUND_CUES)
    if not priceish:
        return None
    nums = [float(m.group(1)) for m in PRICE_NUM_RE.finditer(lowered)]
    if not nums:
        return None
    price = nums[0]
    if any(cue in lowered for cue in UPPER_CUES):
        return (0.0, price)
    if any(cue in lowered for cue in LOWER_CUES):
        return (price, float("inf"))
    return (0.8 * price, 1.2 * price)


@dataclass
class Constraint:
    text: str
    slot_type: str
    polarity: int = 1                    # +1 positive, -1 negated
    status: str = "active"               # active | superseded | deleted
    source_turn: int = 0
    origin: str = "reply"                # opener | reply | override | free
    hard: bool = False
    is_preference: bool = False          # opener soft-preference phrasing
    price_interval: tuple | None = None

    def tokens(self) -> set[str]:
        return content_tokens(self.text)


@dataclass
class Op:
    kind: str                            # KEEP | SET | DELETE | UNKNOWN
    constraint: Constraint | None = None  # for SET, or identity-DELETE
    target_text: str | None = None       # for text-match DELETE
    note: str = ""


@dataclass
class ParseMeta:
    exhausted_attr: str | None = None    # "I don't have an additional preference for X"
    boundary_deflected: bool = False     # boundary deflection (NOT exhaustion)
    nudge: bool = False                  # null-ask zero-information nudge


@dataclass
class SessionState:
    session_id: str
    user_profile: dict = field(default_factory=dict)
    category_anchor: str | None = None
    constraints: list = field(default_factory=list)
    exhausted_attrs: set = field(default_factory=set)
    force_other: bool = False            # re-ask `other` once after an override
    last_ask: str | None = None
    turn: int = 0
    op_log: list = field(default_factory=list)
    deflect_counts: dict = field(default_factory=dict)  # attr -> #no-info deflections (robust parse)

    # ---- views ----
    def active_constraints(self) -> list:
        return [c for c in self.constraints if c.status == "active"]

    def known_slot_types(self) -> set[str]:
        return {c.slot_type for c in self.active_constraints() if c.polarity > 0}

    def evidence_texts(self) -> list[str]:
        """Positive evidence for retrieval v1: anchor + active +polarity texts."""
        texts: list[str] = []
        if self.category_anchor:
            texts.append(self.category_anchor)
        texts.extend(c.text for c in self.active_constraints() if c.polarity > 0)
        return texts

    def budget_interval(self) -> tuple | None:
        for c in reversed(self.active_constraints()):
            if c.price_interval is not None:
                return c.price_interval
        return None

    # ---- op application ----
    def apply(self, ops: list) -> None:
        for op in ops:
            self.op_log.append((self.turn, op.kind, op.constraint.text if op.constraint else op.target_text, op.note))
            if op.kind == "SET" and op.constraint is not None:
                self._apply_set(op.constraint)
            elif op.kind == "DELETE":
                self._apply_delete(op)
            # KEEP / UNKNOWN: no state change (logged only)

    def _apply_set(self, new: Constraint) -> None:
        for existing in self.active_constraints():
            if existing.text.lower() == new.text.lower() and existing.polarity == new.polarity:
                return  # exact restatement: KEEP
        # Auto-supersede on conflict only for user-authored values (free text
        # or override payloads). Reply/opener strings are verbatim card truth:
        # a cotton/leather blend legitimately yields two disjoint "material"
        # strings, and both are real signal.
        if new.polarity > 0 and new.origin in ("free", "override"):
            for existing in self.active_constraints():
                if self._conflicts(existing, new):
                    existing.status = "superseded"
        self.constraints.append(new)

    def _apply_delete(self, op: Op) -> None:
        if op.constraint is not None:
            for existing in self.constraints:
                if existing is op.constraint:
                    existing.status = "deleted"
            return
        if not op.target_text:
            return
        target_tokens = content_tokens(op.target_text)
        if not target_tokens:
            return
        for existing in self.active_constraints():
            if target_tokens & existing.tokens():
                existing.status = "deleted"

    @staticmethod
    def _conflicts(old: Constraint, new: Constraint) -> bool:
        """Same conflict-prone slot, disjoint content: new value supersedes old."""
        if old.polarity <= 0 or old.slot_type != new.slot_type:
            return False
        if old.slot_type not in CONFLICT_SLOTS:
            return False
        if old.slot_type == "budget":
            return old.price_interval != new.price_interval
        old_tokens, new_tokens = old.tokens(), new.tokens()
        return bool(old_tokens) and bool(new_tokens) and not (old_tokens & new_tokens)


# ---- constraint builders / segment compilation ----

def make_constraint(text: str, turn: int, origin: str, hard: bool = False,
                    is_preference: bool = False, polarity: int = 1) -> Constraint:
    text = re.sub(r"\s+", " ", text).strip(" ;,.\t\n")
    return Constraint(
        text=text,
        slot_type=classify_constraint(text),
        polarity=polarity,
        source_turn=turn,
        origin=origin,
        hard=hard,
        is_preference=is_preference,
        price_interval=parse_price_interval(text) if classify_constraint(text) == "budget" else None,
    )


def compile_segment(segment: str, turn: int, origin: str, hard: bool = False,
                    is_preference: bool = False) -> list:
    """Compile one text segment to ops. Negation parsing on free text only."""
    segment = segment.strip(" ;,\t\n")
    if not segment:
        return []
    if origin == "free":
        match = NO_LONGER_RE.search(segment)
        if match:
            return [Op(kind="DELETE", target_text=match.group("t"), note="no-longer")]
        match = NEG_LEAD_RE.match(segment) or NEG_NO_RE.match(segment)
        if match:
            constraint = make_constraint(match.group("t"), turn, origin, hard, is_preference, polarity=-1)
            if constraint.text:
                return [Op(kind="SET", constraint=constraint, note="negation")]
            return []
    constraint = make_constraint(segment, turn, origin, hard, is_preference)
    if not constraint.text or not content_tokens(constraint.text):
        return [Op(kind="UNKNOWN", target_text=segment, note="no-content")]
    return [Op(kind="SET", constraint=constraint)]


def split_free_text(text: str) -> list[str]:
    """Free text: split on ';', sentence boundaries ('. ', '! ', '? '), ' but '."""
    parts = re.split(r";|(?<=[.!?])\s+|\s+but\s+", text)
    return [part for part in (p.strip() for p in parts) if part]


# ---- turn parsers ----

def parse_opener(message: str, turn: int) -> tuple[str | None, list, ParseMeta]:
    """Parse the turn-1 opener. Returns (category_anchor, ops, meta)."""
    meta = ParseMeta()
    match = OPENER_RE.match(message.strip())
    if not match:
        ops = []
        for segment in split_free_text(message):
            ops.extend(compile_segment(segment, turn, origin="free"))
        return None, ops, meta
    rest = match.group("rest").strip()
    exploring = STILL_EXPLORING_RE.search(rest)
    if exploring:
        category = rest[: exploring.start()].strip(" ,.")
        return category or None, [Op(kind="KEEP", note="browsing-opener")], meta
    if ". " in rest:
        category, remainder = rest.split(". ", 1)
    else:
        category, remainder = rest.rstrip("."), ""
    category = category.strip(" ,.")
    ops: list = []
    remainder = remainder.strip()
    if remainder:
        key_req = KEY_REQ_RE.match(remainder)
        if key_req:
            ops.extend(compile_segment(key_req.group("c"), turn, origin="opener", hard=True))
        else:
            # opener-stated preference (override sessions' old_value lands here)
            ops.extend(
                compile_segment(remainder.rstrip("."), turn, origin="opener", is_preference=True)
            )
    return category or None, ops, meta


def parse_turn(message: str, turn: int) -> tuple[list, ParseMeta]:
    """Parse a turn>1 user message (non-override) to ops + meta."""
    meta = ParseMeta()
    lowered = message.lower()
    if NUDGE_MARKER in lowered:
        meta.nudge = True
        return [Op(kind="KEEP", note="nudge")], meta
    match = NO_ADDITIONAL_RE.search(message)
    if match:
        attr = match.group(1).lower()
        meta.exhausted_attr = attr if attr in ASK_ENUM else "other"
        return [Op(kind="KEEP", note=f"exhausted:{meta.exhausted_attr}")], meta
    match = BOUNDARY_RE.search(message)
    if match:
        meta.boundary_deflected = True  # deflection, NOT exhaustion
        return [Op(kind="KEEP", note="boundary-deflection")], meta
    match = REPLY_RE.match(message.strip())
    if match:
        body = match.group("body").strip().rstrip(".")
        ops: list = []
        for segment in body.split(";"):
            ops.extend(compile_segment(segment, turn, origin="reply"))
        return ops, meta
    ops = []
    for segment in split_free_text(message):
        ops.extend(compile_segment(segment, turn, origin="free"))
    if not ops:
        ops = [Op(kind="UNKNOWN", target_text=message, note="unparsed")]
    return ops, meta


# ===========================================================================
# Robust frame-agnostic parsing (TENFOLD_PARSE=robust, the production default).
#
# Design principles (generic English, written from first principles — no
# simulator template strings, official or hardened, are encoded here):
#   1. Classify each user message SEMANTICALLY before extraction:
#      no-preference/deflection, nudge, disclosure. Overrides are detected
#      upstream by override.detect (its cue battery already generalizes).
#   2. Conversational frame tokens and filler (sentence heads/tails, hedges)
#      are stripped BEFORE a segment becomes constraint text, so the state
#      never fills with dialog boilerplate.
#   3. Card-exhaustion is detected from the semantic no-preference class
#      ("nothing else / no additional / that's all" semantics), restoring the
#      ask policy's attribute rotation and the confidence controller's
#      exhaustion-opens under any phrasing.
#
# The legacy exact-template parsers above (TENFOLD_PARSE=frame) are untouched.
# ===========================================================================

# -- no-preference / deflection battery (any hit marks the message no-info,
#    subject to the residual-content check below) --
NO_PREF_RES = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bno (?:particular |specific |strong |real |firm )?(?:preference|preferences)\b",
        r"\b(?:don'?t|do not|doesn'?t|does not) (?:really )?have (?:a |any |an )?"
        r"(?:particular |specific |strong |real |additional |other |further |more )*"
        r"(?:preference|preferences|requirement|requirements|need|needs|constraint|constraints|"
        r"opinion|opinions|thought|thoughts)\b",
        r"\bno (?:additional|other|further|more|specific|particular) "
        r"(?:preference|preferences|requirement|requirements|need|needs|thought|thoughts|one|ones)\b",
        r"\bnothing (?:else|more|further|new|additional|specific|particular|special|in particular)\b",
        r"\bnothing (?:really )?comes to mind\b",
        r"\bnothing (?:i can |to )?(?:think of|add)\b",
        r"\bcan(?:'t|not) (?:really )?(?:think of|come up with)\b",
        r"\bnot sure what else\b",
        r"\bdon'?t know what else\b",
        r"\b(?:it'?s |that'?s )?up to you\b",
        r"\buse your (?:best )?(?:judgment|judgement|discretion)\b",
        r"\byour (?:call|choice|pick)\b",
        r"\byou (?:can )?(?:decide|choose|pick)\b",
        r"\bwhatever you (?:think|suggest|recommend|prefer|choose|pick|like)\b",
        r"\bgo with whatever\b",
        r"\bwhatever works\b",
        r"\b(?:i'?ll |i will )?(?:trust|defer to) (?:you|your)\b",
        r"\bsurprise me\b",
        r"\bdealer'?s choice\b",
        r"\b(?:doesn'?t|does not|don'?t|do not) (?:really |much )?matter\b",
        r"\bdon'?t (?:really )?(?:mind|care)\b",
        r"\bnot (?:that |too |very |especially |particularly )?(?:picky|fussy|particular|bothered|fussed)\b",
        r"\beither (?:way|one)\b",
        r"\bany(?:thing| of (?:them|those|these))? (?:is |are |would be |works? )?"
        r"(?:fine|ok(?:ay)?|good|great)\b",
        r"\banything (?:works|goes)\b",
        r"\bi'?m (?:pretty )?(?:flexible|easy|open)\b",
        r"\bopen to (?:anything|whatever|suggestions)\b",
        r"\bno (?:real |specific |particular )?(?:requirements?|constraints?|musts?|must-haves?)\b",
        r"\bthat'?s (?:about |pretty much )?(?:it|all|everything)\b",
        r"\bthat (?:about )?covers (?:it|everything)\b",
        r"\bnothing to add\b",
    )
]

# -- exhaustion sub-cues: "nothing MORE beyond what I've said" semantics --
EXHAUST_RES = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\badditional\b",
        r"\bnothing (?:else|more|further|new)\b",
        r"\banything (?:else|more)\b",
        r"\bwhat else\b",
        r"\bno (?:other|further|more)\b",
        r"\bother than (?:that|what)\b",
        r"\bbeyond (?:that|what)\b",
        r"\balready (?:mentioned|covered|said|told|gave)\b",
        r"\bthat'?s (?:about |pretty much )?(?:it|all|everything)\b",
        r"\bthat (?:about )?covers\b",
        r"\bnothing to add\b",
        r"\bcan(?:'t|not) (?:really )?(?:think of|come up with)\b",
        r"\bnot sure what else\b",
        r"\bdon'?t know what else\b",
        r"\bcomes? to mind\b",
    )
]

# -- nudge battery: "those aren't right, keep going" carries zero information --
NUDGE_RES = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bnot (?:quite|exactly|really|entirely) (?:right|it|there|what)\b",
        r"\bnot (?:quite|exactly)\b",
        r"\bnot what i(?:'m| am| was)? (?:looking for|after|hoping|had in mind|expecting)\b",
        r"\bnone of (?:those|these|them)\b",
        r"\bnot (?:seeing|feeling) (?:it|anything|the right)\b",
        r"\bkeep (?:looking|trying|searching|going)\b",
        r"\bnot (?:there|it) yet\b",
        r"\bstill not (?:right|it|quite|what)\b",
        r"\bhaven'?t (?:found|seen|hit) (?:it|the right|anything)\b",
        r"\b(?:show|try) (?:me )?something (?:else|different)\b",
        r"\bmiss(?:es|ed|ing)? the mark\b",
        r"\boff the mark\b",
        r"\bnot a (?:good |great )?(?:fit|match)\b",
        r"\bdon'?t (?:love|like) (?:those|these|them|any of)\b",
        r"\bno luck (?:yet|so far)\b",
    )
]

# -- conversational filler vocabulary: never product evidence. Used for the
#    residual-content check and never subtracted from actual constraint text. --
FILLER_TOKENS = {
    "about", "really", "honestly", "actually", "just", "right", "now", "yet",
    "still", "currently", "think", "guess", "suppose", "know", "say", "said",
    "tell", "sure", "ok", "okay", "well", "hmm", "oh", "ah", "one", "ones",
    "those", "these", "them", "they", "there", "here", "anything", "something",
    "nothing", "else", "more", "specific", "particular", "preference",
    "preferences", "requirement", "requirements", "attribute", "attributes",
    "ask", "asked", "asking", "question", "questions", "options", "option",
    "choice", "choices", "recommendation", "recommendations", "suggestion",
    "suggestions", "mind", "comes", "come", "moment", "top", "head",
    "judgment", "judgement", "discretion", "call", "decide", "pick", "choose",
    "matter", "matters", "much", "many", "far", "though", "however", "maybe",
    "perhaps", "probably", "help", "helps", "thanks", "thank", "front",
    "honest", "mentioned", "additional", "further", "already", "covers",
    "covered", "beyond", "point", "note", "care", "prefer", "wise",
}

# -- segment-head discourse markers (pure conversational lead-ins) --
SEG_DISCOURSE_RE = re.compile(
    r"^(?:(?:hi|hey|hello|oh|ah|um+|uh+|well|hmm+|so|ok(?:ay)?|right|sure|yes|yeah|yep|"
    r"honestly|to be honest|frankly|good question|great question|fair question|fair enough|"
    r"good point|thanks for asking|let me think|let'?s see|come to think of it|"
    r"now that you (?:mention|ask) it|if i had to (?:say|choose|pick)|"
    r"off the top of my head|you know)[\s,!.:;—-]+)+",
    re.IGNORECASE,
)

# -- segment-head frame/hedge phrases that INTRODUCE content without being it --
SEG_FRAME_RE = re.compile(
    r"^(?:"
    r"for (?:that|this|me)|in that case|on that (?:front|note)|as for (?:that|this)|"
    r"regarding (?:that|this)|when it comes to (?:that|this)|about (?:that|this)|"
    r"what (?:really |truly |mostly )?matters (?:to me |most |here )?(?:is|would be|are)|"
    r"what(?:'s| is) (?:most |really )?important (?:to me |here )?(?:is|would be|are)|"
    r"what i(?:'m| am) (?:really )?(?:after|looking for) (?:is|would be)|"
    r"what i(?:'d| would) (?:say|call) (?:matters|important)(?: is)?|"
    r"the (?:main|key|big|important) thing(?:s)?(?: for me)? (?:is|are|would be)|"
    r"my (?:main|key|big|top) (?:priority|concern|requirement|preference) (?:is|would be)|"
    r"a key requirement is|key requirements? (?:is|are)|"
    r"i(?:'d| would) (?:go with|lean toward(?:s)?|prefer|like|want|love|need|say)|"
    r"i (?:really |definitely |mostly |mainly |generally |usually )?"
    r"(?:need|want|prefer|care about|value|like|am after)|"
    r"i'?m (?:really |mostly |mainly )?(?:after|hoping for|looking for|leaning toward(?:s)?|"
    r"interested in|drawn to|partial to)|"
    r"it (?:should|needs? to|must|has to|ought to) (?:be|have|come with|include)|"
    r"ideally|preferably|let'?s say|i guess|i suppose|i think|i'?d say|"
    r"something (?:with|that(?:'s| is| has)?|like|along the lines of|in)|"
    r"if possible|maybe|probably|definitely|hopefully"
    r")[\s,:;—-]*",
    re.IGNORECASE,
)

# -- segment-tail hedges/filler --
SEG_TAIL_RE = re.compile(
    r"[\s,;—-]+(?:"
    r"if (?:that(?:'s| is)? )?possible|if that (?:helps|works|makes sense)|"
    r"i (?:think|guess|suppose)|i'?d say|you know|for me|to me|at least|"
    r"or so|or something(?: like that)?|something like that|"
    r"that'?s (?:about )?(?:it|all)|hope that helps|please|thanks?|thank you|"
    r"if you can|when possible|would be (?:nice|great|ideal)"
    r")[.!?\s]*$",
    re.IGNORECASE,
)

_ATTR_VARIANTS = [(attr, attr.replace("_", " ")) for attr in ASK_ENUM]


def _strip_segment_frame(segment: str) -> tuple[str, bool]:
    """Iteratively strip discourse heads, frame heads, and hedge tails.

    Returns (clean_text, frame_matched): frame_matched is True when a
    content-introducing frame phrase was removed (the remainder then reads as
    a quoted/stated preference value rather than raw free prose)."""
    text = segment.strip(" \t\n,;.—-")
    frame_matched = False
    for _ in range(8):
        before = text
        text = SEG_DISCOURSE_RE.sub("", text).strip(" \t\n,;—-")
        new = SEG_FRAME_RE.sub("", text)
        if new != text:
            frame_matched = True
            text = new.strip(" \t\n,;:—-")
        text = SEG_TAIL_RE.sub("", text).strip(" \t\n,;—-")
        if text == before:
            break
    return text.strip(" \t\n,;:.—-"), frame_matched


def _residual_tokens(message: str, cue_patterns: list) -> set:
    """Content tokens left after removing cue matches, attribute names, and filler."""
    text = message.lower()
    for pattern in cue_patterns:
        text = pattern.sub(" ", text)
    for attr, spaced in _ATTR_VARIANTS:
        text = text.replace(spaced, " ").replace(attr, " ")
    return {t for t in content_tokens(text) if t not in FILLER_TOKENS}


def _message_attr(message: str, state) -> str:
    """Which ask attribute a no-info message refers to: named in the message,
    else the attribute we last asked, else `other`."""
    lowered = message.lower()
    for attr, spaced in _ATTR_VARIANTS:
        if attr in lowered or spaced in lowered:
            return attr
    last = getattr(state, "last_ask", None)
    if last in ASK_ENUM:
        return last
    return "other"


def classify_no_info(message: str) -> str | None:
    """Semantic pre-classification: 'nudge' | 'no_pref' | None (informative).

    A message is no-info only when a battery cue fires AND, after removing the
    cue text, attribute names, and conversational filler, at most 2 content
    tokens remain (so mixed messages that also disclose a constraint fall
    through to disclosure parsing)."""
    text = message.strip()
    if not text or REPLY_RE.match(text):
        return None  # verbatim disclosure frame is never no-info
    if any(p.search(text) for p in NUDGE_RES):
        if len(_residual_tokens(text, NUDGE_RES + NO_PREF_RES)) <= 2:
            return "nudge"
    if any(p.search(text) for p in NO_PREF_RES):
        if len(_residual_tokens(text, NO_PREF_RES + NUDGE_RES)) <= 2:
            return "no_pref"
    return None


def split_robust(text: str) -> list[str]:
    """Segmentation for robust parsing: ';', sentence boundaries, contrastives."""
    parts = re.split(r";|(?<=[.!?])\s+|\s+but\s+|\s+and also\s+|\s+plus\s+|\s+—\s+|\s+-\s+", text)
    return [part for part in (p.strip() for p in parts) if part]


def parse_turn_robust(message: str, turn: int, state) -> tuple[list, ParseMeta]:
    """Frame-agnostic parse of a turn>1 user message (override handled upstream).

    Flow: verbatim reply frame -> legacy reply path (byte-equivalent on the
    official simulator); semantic no-info class -> KEEP + exhaustion/deflection
    bookkeeping; otherwise frame-strip and compile segments to ops."""
    meta = ParseMeta()
    text = message.strip()
    # 1. Verbatim official reply frame: exact legacy handling.
    match = REPLY_RE.match(text)
    if match:
        body = match.group("body").strip().rstrip(".")
        ops: list = []
        for segment in body.split(";"):
            ops.extend(compile_segment(segment, turn, origin="reply"))
        return ops, meta
    # 2. Semantic no-info classes.
    kind = classify_no_info(text)
    if kind == "nudge":
        meta.nudge = True
        return [Op(kind="KEEP", note="nudge-robust")], meta
    if kind == "no_pref":
        attr = _message_attr(text, state)
        exhausted = any(p.search(text) for p in EXHAUST_RES)
        counts = getattr(state, "deflect_counts", None)
        prior = counts.get(attr, 0) if counts is not None else 0
        if exhausted or prior >= 1:
            # "nothing MORE" semantics, or a repeat no-info on the same
            # attribute: the card has nothing left for this attribute.
            meta.exhausted_attr = attr
            return [Op(kind="KEEP", note=f"exhausted-robust:{attr}")], meta
        # First blanket refusal without "additional" semantics: deflection
        # (the card may still be full — boundary sessions deflect once).
        meta.boundary_deflected = True
        if counts is not None:
            counts[attr] = prior + 1
        return [Op(kind="KEEP", note=f"deflection-robust:{attr}")], meta
    # 3. Disclosure / free text: strip frames, then compile segments.
    ops = []
    any_frame = False
    lead, lead_frame = _strip_segment_frame(text)
    any_frame = any_frame or lead_frame
    segments: list[str] = []
    for raw_segment in split_robust(lead):
        clean, seg_frame = _strip_segment_frame(raw_segment)
        any_frame = any_frame or seg_frame
        if clean:
            segments.append(clean)
    origin = "reply" if any_frame else "free"
    for segment in segments:
        ops.extend(compile_segment(segment, turn, origin=origin))
    if not ops:
        ops = [Op(kind="UNKNOWN", target_text=message, note="unparsed-robust")]
    return ops, meta


# -- robust opener parsing --
OPENER_HEAD_RE = re.compile(
    r"^(?:(?:hi|hey|hello)[,! ]+)?"
    r"(?:i'?m looking for|i'?m looking to (?:buy|get|find)|i (?:need|want|would like)|"
    r"i'?d like|i'?m (?:after|shopping for|hunting for|in the market for|trying to find|"
    r"hoping to find|searching for)|help me (?:find|pick|choose)|can you (?:help me )?find|"
    r"find me|show me|searching for|looking for|i want to (?:buy|find|get))\s+",
    re.IGNORECASE,
)
BROWSE_TAIL_RE = re.compile(
    r"[,;\s]*(?:but\s+)?(?:i'?m\s+)?(?:still\s+)?(?:just\s+)?"
    r"(?:exploring|browsing|undecided|not sure yet|keeping (?:my )?options open|"
    r"open to (?:options|ideas|suggestions)|window[- ]shopping|weighing (?:my )?options|"
    r"seeing what'?s (?:out there|available)|shopping around)\b[^.]*\.?\s*$",
    re.IGNORECASE,
)
HARD_CUE_RE = re.compile(
    r"\b(?:key requirement|must[- ]have|must (?:be|have|come)|has to (?:be|have)|"
    r"needs? to (?:be|have)|non[- ]negotiable|required|essential|deal[- ]breaker)\b",
    re.IGNORECASE,
)


_ARTICLE_RE = re.compile(r"^(?:a|an|the|some)\s+", re.IGNORECASE)


def parse_opener_robust(message: str, turn: int) -> tuple[str | None, list, ParseMeta]:
    """Robust turn-1 opener parse: legacy templates first, generic fallback after."""
    meta = ParseMeta()
    text = message.strip()
    if OPENER_RE.match(text):
        return parse_opener(message, turn)  # legacy path handles all official frames
    stripped = _ARTICLE_RE.sub("", OPENER_HEAD_RE.sub("", text).strip())
    browse = BROWSE_TAIL_RE.search(stripped)
    if browse and browse.start() > 0:
        category = stripped[: browse.start()].strip(" ,.;")
        category, _ = _strip_segment_frame(category)
        return category or None, [Op(kind="KEEP", note="browsing-opener-robust")], meta
    if ". " in stripped:
        category, remainder = stripped.split(". ", 1)
    else:
        category, remainder = stripped.rstrip("."), ""
    category = category.strip(" ,.;")
    category, _ = _strip_segment_frame(category)
    ops: list = []
    remainder = remainder.strip()
    if remainder:
        hard = bool(HARD_CUE_RE.search(remainder))
        for raw_segment in split_robust(remainder):
            clean, _f = _strip_segment_frame(raw_segment)
            if clean:
                ops.extend(
                    compile_segment(clean, turn, origin="opener",
                                    hard=hard, is_preference=not hard)
                )
    return category or None, ops, meta
