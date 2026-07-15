# core/instrument.py
"""
Maps the workplace questionnaire schema onto its latent constructs so answer
generation can be CHUNKED by construct (breaking the one-shot self-consistency
that inflates Cronbach's alpha) and conditioned on the right latent-trait bands.

The questionnaire (Porsline form) is a workplace battery combining three
validated instruments, each on its own rating scale:

  - Demographics                : age, occupation  (2 TEXT_INPUT)
  - Job Satisfaction            : 5-point [بسیار کم → بسیار زیاد] — overall
                                  satisfaction, pay, promotion, responsibility,
                                  performance evaluation, valued/intrinsic work
                                  (7 items)
  - Workplace Relations         : same 5-point scale — management support,
                                  coworkers, help from colleagues, supervisor
                                  trust  (5 items; one item lists options in
                                  reversed order)
  - Work→Family Conflict        : 7-point agreement [کاملا مخالفم → کاملا موافقم]
                                  — work demands disturbing family life  (5 items)
  - Family→Work Conflict        : same 7-point agreement scale — family demands
                                  disturbing work duties  (5 items)
  - Perceived Org. Support      : 7-point «تا حدودی» variant — org valuation,
                                  recognition, care, welfare  (8 items; 4 are
                                  reverse-keyed; one item has a typo «مخالم» and
                                  only 6 options)

DEMOGRAPHIC pseudo-questions (age, occupation) are answered directly in code (see
`static_answers`) so a persona's identity stays perfectly consistent; only the
psychometric items are sent to the LLM. The demographic TEXT_INPUTs are detected
by question-text keyword (سن / شغل); the psychometric constructs are detected by
option-signature (the three scales) plus a stable question-id → construct split
within each scale (the IDs are stable in Porsline, so explicit id sets are
unambiguous and survive option-wording quirks).

`numeric_code` codes each scale monotonically by the SEMANTIC direction of THAT
question's own option list. The single reversed-order 5-pt item (50920348) is
handled item-level: its option list runs HIGH→LOW, so its ordinal score is
K - index. The validation harness auto-reverses negative item-total correlations,
so coding only needs to be monotonic and consistent within a scale.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Tuple


# construct_key -> (Persian label, latent dims relevant to that construct).
# The trait dim in each tuple MUST be a key of trait_sampler.LOADINGS.
CONSTRUCT_TRAIT_DIMS: Dict[str, Tuple[str, List[str]]] = {
    "job_satisfaction":                ("رضایت شغلی",            ["job_satisfaction"]),
    "workplace_relations":             ("روابط شغلی",            ["workplace_relations"]),
    "work_to_family_conflict":         ("تضاد کار-خانواده",      ["work_to_family_conflict"]),
    "family_to_work_conflict":         ("تضاد خانواده-کار",     ["family_to_work_conflict"]),
    "perceived_organizational_support": ("حمایت ادراک‌شده سازمان", ["perceived_organizational_support"]),
    "other":                           ("سایر",                  ["job_satisfaction"]),
}

# Stable ordering of psychometric constructs in the output.
_CONSTRUCT_ORDER: List[str] = [
    "job_satisfaction", "workplace_relations",
    "work_to_family_conflict", "family_to_work_conflict",
    "perceived_organizational_support", "other",
]

# ---------------------------------------------------------------------------
# Stable question-id -> construct assignment within the multi-construct scales.
# Porsline IDs are stable, so explicit sets are unambiguous (equivalently: the
# 5-pt block = 50919467..50920903, the agreement block = 50921353..50923717,
# the POS block = 50924914..50926291).
# ---------------------------------------------------------------------------
_JS_IDS  = {"50919467", "50919721", "50920124", "50920537", "50920701", "50920794", "50920903"}
_WR_IDS  = {"50919558", "50919922", "50920292", "50920348", "50920478"}
_W2F_IDS = {"50921353", "50921503", "50921703", "50922306", "50922605"}
_F2W_IDS = {"50922924", "50923158", "50923442", "50923605", "50923717"}

# Reverse-keyed perceived-organizational-support items: the AGREE end means
# LOWER organizational support, so the answer is inverted (high support →
# «مخالفم»/«کاملا مخالفم»).
_POS_REVERSE_IDS = {"50925130", "50925315", "50925610", "50925955"}

# Per-scale HIGH anchor — the option that sits at the HIGH/strong end of the
# construct. If an item's own option list starts with its high anchor
# (opts[0] in HIGH_ANCHORS[scale]), the list runs HIGH→LOW and the ordinal
# score is K - index; otherwise it runs LOW→HIGH and the score is index + 1.
# This resolves the single reversed-order 5-pt item (50920348: بسیار زیاد first).
HIGH_ANCHORS: Dict[str, set] = {
    "five_pt":        {"بسیار زیاد"},
    "seven_pt_agree": {"کاملا موافقم"},
    "seven_pt_pos":   {"کاملا موافقم"},
}


# ---------------------------------------------------------------------------
# Scale + construct detection (option-signature + stable ids)
# ---------------------------------------------------------------------------

def _option_values(question: Dict[str, Any]) -> List[str]:
    return [o.get("value", o.get("text", "")) for o in question.get("options", [])]


def _scale_of(question: Dict[str, Any]) -> str:
    """Identify the rating scale of a RADIO question by its option signature."""
    vset = set(_option_values(question))
    if "بسیار کم" in vset and "بسیار زیاد" in vset:
        return "five_pt"
    if "تا حدودی مخالفم" in vset or "تا حدودی موافقم" in vset:
        return "seven_pt_pos"
    if "کاملا مخالفم" in vset and "کاملا موافقم" in vset:
        return "seven_pt_agree"
    return "other"


def classify(question: Dict[str, Any]) -> str:
    """
    Classify a question into one of:
      demographic_age | demographic_occupation
    or a psychometric construct key (job_satisfaction / workplace_relations /
    work_to_family_conflict / family_to_work_conflict /
    perceived_organizational_support / other).
    """
    if question.get("type") == "TEXT_INPUT":
        t = question.get("question_text", "")
        if "سن" in t:
            return "demographic_age"
        if "شغل" in t:
            return "demographic_occupation"
        return "other"

    sk = _scale_of(question)
    qid = question.get("question_id", "")
    if sk == "five_pt":
        if qid in _JS_IDS:
            return "job_satisfaction"
        if qid in _WR_IDS:
            return "workplace_relations"
        return "other"
    if sk == "seven_pt_agree":
        if qid in _W2F_IDS:
            return "work_to_family_conflict"
        if qid in _F2W_IDS:
            return "family_to_work_conflict"
        return "other"
    if sk == "seven_pt_pos":
        return "perceived_organizational_support"
    return "other"


def group_by_construct(schema: List[Dict[str, Any]]) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """Psychometric constructs only (demographics & instructions excluded), in stable order."""
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for q in schema:
        ck = classify(q)
        if ck.startswith("demographic_") or ck == "instruction":
            continue
        buckets.setdefault(ck, []).append(q)
    return [(ck, buckets[ck]) for ck in _CONSTRUCT_ORDER if ck in buckets]


# ---------------------------------------------------------------------------
# Demographic answers, answered directly in code (no LLM) so a persona's
# identity (age, occupation) is perfectly consistent with her profile.
# ---------------------------------------------------------------------------

def static_answers(schema: List[Dict[str, Any]], persona: Dict[str, Any]) -> Dict[str, str]:
    """Map demographic questions to fixed answers drawn from the persona."""
    d = persona.get("demographics", {})
    out: Dict[str, str] = {}
    for q in schema:
        ck = classify(q)
        qid = q["question_id"]
        if ck == "demographic_age":
            out[qid] = str(d.get("age", "")).strip()
        elif ck == "demographic_occupation":
            out[qid] = str(d.get("occupation", "")).strip()
    return out


# ---------------------------------------------------------------------------
# Reverse-keying detection — for constructs whose items are a MIX of direct and
# reverse wording. The answer prompt turns these into per-item [معکوس] tags.
# ---------------------------------------------------------------------------

def is_reverse(question: Dict[str, Any], construct_key: str) -> bool:
    """True if the HIGH/agree end of this item's scale means LOWER of the construct's trait."""
    if construct_key == "perceived_organizational_support":
        return question.get("question_id") in _POS_REVERSE_IDS
    return False


# ---------------------------------------------------------------------------
# Ordinal coding for the validation harness (Cronbach's alpha etc.).
# ---------------------------------------------------------------------------

def numeric_code(question: Dict[str, Any], value: str) -> int | None:
    """Map an option string to its ordinal score for this question's scale (monotonic)."""
    opts = _option_values(question)
    if value not in opts:
        return None
    sk = _scale_of(question)
    i = opts.index(value)
    k = len(opts)
    # Item-level direction: if this item's own list starts at the HIGH anchor,
    # it runs HIGH→LOW, so the ordinal score is K - index. Otherwise LOW→HIGH
    # and the score is index + 1. Resolves the reversed-order 5-pt item.
    if opts and opts[0] in HIGH_ANCHORS.get(sk, set()):
        return k - i          # position 0 = high end → K; last → 1
    return i + 1               # position 0 = low end → 1; last → K


def scale_size(question: Dict[str, Any]) -> int:
    return len(question.get("options", [])) or 0


# ---------------------------------------------------------------------------
# Response-style -> natural-language nudges for the answering LLM.
# These inject realistic METHOD VARIANCE (acquiescence, extreme/midpoint style)
# without telling the model the actual trait answers.
# ---------------------------------------------------------------------------

def response_style_instruction(style: Dict[str, Any]) -> str:
    parts: List[str] = []

    acq = style.get("acquiescence", 0.0)
    if acq > 0.30:
        parts.append("در سؤال‌های مرزی که مطمئن نیستی، گرایش خفیفی به سمت گزینه‌های موافق/بالاتر داری.")
    elif acq < -0.30:
        parts.append("در سؤال‌های مرزی که مطمئن نیستی، گرایش خفیفی به سمت گزینه‌های مخالف/پایین‌تر داری.")

    ers = style.get("extreme_response", 0.0)
    if ers > 0.30:
        parts.append("وقتی گزینه‌ای واقعاً با حال‌وروزت می‌خواند، راحت از گزینه‌ی نهاییِ طیف (مثل «بسیار زیاد» یا «کاملا موافقم») استفاده می‌کنی.")
    elif ers < -0.30:
        parts.append("حتی وقتی موافق یا مخالفی، معمولاً گزینه‌های ملایم/میانی را به گزینه‌های نهاییِ طیف ترجیح می‌دهی و کمتر سراغ «بسیار زیاد» یا «کاملا…» می‌روی.")

    if not parts:
        return "سبک پاسخ‌دهی‌ات معمولی و متعادل است."
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Careless / inattentive responder simulated directly in code (LLMs cannot
# convincingly fake inattention). Produces a straightlining-with-noise pattern
# over the psychometric items only — demographics stay identity-consistent.
# ---------------------------------------------------------------------------

def careless_answers(schema: List[Dict[str, Any]], rng: random.Random) -> Dict[str, str]:
    answers: Dict[str, str] = {}
    for _ck, questions in group_by_construct(schema):
        anchor: int | None = None
        for q in questions:
            opts = _option_values(q)
            if not opts:
                continue
            if anchor is None:
                anchor = rng.randrange(len(opts))
            base = min(anchor, len(opts) - 1)
            # 70% straightline on the anchor, else drift to a neighbor (light noise)
            if rng.random() < 0.70:
                idx = base
            else:
                idx = max(0, min(len(opts) - 1, base + rng.choice([-1, 1])))
            answers[q["question_id"]] = opts[idx]
    return answers