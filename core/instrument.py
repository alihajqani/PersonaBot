# core/instrument.py
"""
Maps the questionnaire schema onto the latent constructs, so answer generation
can be CHUNKED by construct (breaking the one-shot self-consistency that
inflates Cronbach's alpha) and conditioned on the right latent-trait bands.

Constructs are detected from each question's OPTION SIGNATURE rather than
hard-coded IDs, so this keeps working if question IDs change:

  - 5-point agreement (… "حدوسط" …)            -> general self-efficacy
  - 5-point degree  ("هیچ" … "خیلی زیاد")        -> academic / exam behavior
  - 6-point agreement (… "تا حدی موافقم" …)      -> emotional intelligence

Anything unrecognized falls into an "other" chunk that still gets generated.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Tuple


# construct_key -> (Persian label, latent dims relevant to that construct)
CONSTRUCT_TRAIT_DIMS: Dict[str, Tuple[str, List[str]]] = {
    "self_efficacy": (
        "خودکارآمدی عمومی",
        ["self_efficacy"],
    ),
    "academic": (
        "رفتار تحصیلی و امتحان",
        ["study_organization", "test_anxiety", "academic_motivation",
         "exam_self_efficacy", "internal_attribution"],
    ),
    "emotional_intelligence": (
        "هوش هیجانی",
        ["ei_self_awareness", "ei_understanding_others",
         "ei_emotion_regulation", "ei_social_skills"],
    ),
    "other": (
        "سایر",
        ["self_efficacy"],
    ),
}


# Meaning-based ordinal coding (NOT list position — the 6-point EI scale is
# listed out of order in the schema). Edit here if your coding convention differs.
OPTION_SCORES: Dict[str, int] = {
    # 5-point agreement
    "کاملا مخالفم": 1, "مخالفم": 2, "حدوسط": 3, "موافقم": 4, "کاملا موافقم": 5,
    # 5-point degree
    "هیچ": 1, "کم": 2, "تاحدی": 3, "زیاد": 4, "خیلی زیاد": 5,
    # 6-point agreement (monotonic by meaning)
    "تا حدی مخالفم": 3, "تا حدی موافقم": 4,
}
# For the 6-point scale, agreement words shift up by one vs. the 5-point scale.
OPTION_SCORES_6PT: Dict[str, int] = {
    "کاملا مخالفم": 1, "مخالفم": 2, "تا حدی مخالفم": 3,
    "تا حدی موافقم": 4, "موافقم": 5, "کاملا موافقم": 6,
}


def numeric_code(question: Dict[str, Any], value: str) -> int | None:
    """Map an option string to its ordinal score for this question's scale."""
    if _construct_of(question) == "emotional_intelligence":
        return OPTION_SCORES_6PT.get(value)
    return OPTION_SCORES.get(value)


def scale_size(question: Dict[str, Any]) -> int:
    return len(question.get("options", [])) or 0


def _construct_of(question: Dict[str, Any]) -> str:
    values = {opt.get("value", opt.get("text", "")) for opt in question.get("options", [])}
    if not values:
        return "other"
    if "هیچ" in values and "خیلی زیاد" in values:
        return "academic"
    if "تا حدی موافقم" in values or "تا حدی مخالفم" in values:
        return "emotional_intelligence"
    if "حدوسط" in values:
        return "self_efficacy"
    return "other"


def group_by_construct(schema: List[Dict[str, Any]]) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """Returns [(construct_key, [questions...]), ...] preserving construct order."""
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for q in schema:
        buckets.setdefault(_construct_of(q), []).append(q)
    order = ["self_efficacy", "academic", "emotional_intelligence", "other"]
    return [(k, buckets[k]) for k in order if k in buckets]


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
        parts.append("وقتی گزینه‌ای واقعاً با حال‌وروزت می‌خواند، راحت از گزینه‌ی نهاییِ طیف (مثل «کاملا موافقم» یا «خیلی زیاد») استفاده می‌کنی.")
    elif ers < -0.30:
        parts.append("حتی وقتی موافق یا مخالفی، معمولاً گزینه‌های ملایم/میانی را به گزینه‌های نهاییِ طیف ترجیح می‌دهی و کمتر سراغ «کاملا…» یا «خیلی زیاد» می‌روی.")

    if not parts:
        return "سبک پاسخ‌دهی‌ات معمولی و متعادل است."
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Careless / inattentive responder simulated directly in code (LLMs cannot
# convincingly fake inattention).  Produces a straightlining-with-noise pattern
# that validation can later detect — a hallmark of real survey datasets.
# ---------------------------------------------------------------------------

def careless_answers(schema: List[Dict[str, Any]], rng: random.Random) -> Dict[str, str]:
    answers: Dict[str, str] = {}
    # pick a per-construct "anchor" option index to straightline around
    anchors: Dict[str, int] = {}
    for q in schema:
        opts = [o.get("value", o.get("text", "")) for o in q.get("options", [])]
        if not opts:
            continue
        ck = _construct_of(q)
        if ck not in anchors:
            anchors[ck] = rng.randrange(len(opts))
        base = min(anchors[ck], len(opts) - 1)
        # 70% straightline on the anchor, else drift to a neighbor (light noise)
        if rng.random() < 0.70:
            idx = base
        else:
            idx = max(0, min(len(opts) - 1, base + rng.choice([-1, 1])))
        answers[q["question_id"]] = opts[idx]
    return answers
