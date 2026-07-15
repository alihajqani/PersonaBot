# core/instrument.py
"""
Maps the marital-relationship questionnaire schema onto its latent constructs so
answer generation can be CHUNKED by construct (breaking the one-shot self-
consistency that inflates Cronbach's alpha) and conditioned on the right
latent-trait bands.

The questionnaire (Porsline form) is a marital-satisfaction battery combining
several validated instruments:

  - Demographics           : gender, occupation, children, years married,
                             psychological-problem disclosure  (TEXT + 2 RADIO)
  - Dyadic Consensus       : 6-point agreement on finances/recreation/religion/
                             affection/friends/sex/decisions…  (RDAS consensus)
  - Dyadic Satisfaction     : 5-point frequency incl. thoughts of divorce, trust
                             + a 7-point global happiness item
  - Dyadic Cohesion        : 5/6-point frequency of kissing, shared interests,
                             laughing, calm talk, working together
  - Affective Expression    : yes/no items on sexual fatigue / lack of affection
  - Commitment             : one graded-statements item (strongest → weakest)
  - Attachment             : 5-point degree (Collins-Read style), split into an
                             anxiety pole and a closeness/trust (avoidance) pole
  - Partner Responsiveness : 5-point agreement about the spouse ( Reis & Shaver )

DEMOGRAPHIC and INSTRUCTION pseudo-questions are answered directly in code (see
`static_answers`) so a persona's identity stays perfectly consistent and every
respondent is female; only the psychometric items are sent to the LLM. Detection
is signature/content-driven (not hard-coded IDs), so it survives ID changes.

`numeric_code` codes each scale monotonically by the SEMANTIC direction of THAT
question's own option list (not a flat option→score dict), which resolves scales
that share option strings — e.g. «ب بیشتر اوقات» belongs to both the satisfaction
frequency scale (=4) and the interaction-frequency scale (=6). The validation
harness auto-reverses negative item-total correlations, so coding only needs to be
monotonic and consistent within a scale.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Tuple


# construct_key -> (Persian label, latent dims relevant to that construct)
CONSTRUCT_TRAIT_DIMS: Dict[str, Tuple[str, List[str]]] = {
    "consensus":              ("توافق زناشویی",        ["marital_satisfaction"]),
    "satisfaction":           ("رضایت زناشویی",        ["marital_satisfaction"]),
    "cohesion":               ("انسجام و ابراز محبت",  ["dyadic_cohesion"]),
    "affectional_expression": ("ابراز عاطفی",          ["marital_satisfaction"]),
    "global_happiness":       ("شادکامی زناشویی",      ["marital_satisfaction"]),
    "commitment":             ("تعهد به رابطه",        ["relationship_commitment"]),
    "partner_responsiveness": ("پاسخگویی همسر",        ["partner_responsiveness"]),
    "attachment_avoidance":   ("اجتناب دلبستگی",       ["attachment_avoidance"]),
    "attachment_anxiety":     ("اضطراب دلبستگی",       ["attachment_anxiety"]),
    "other":                  ("سایر",                  ["marital_satisfaction"]),
}

# Stable ordering of psychometric constructs in the output.
_CONSTRUCT_ORDER: List[str] = [
    "consensus", "satisfaction", "cohesion", "affectional_expression",
    "global_happiness", "commitment", "partner_responsiveness",
    "attachment_avoidance", "attachment_anxiety", "other",
]

# Scales whose option list runs HIGH → LOW (position 0 = the high/strong end),
# so the ordinal score is reversed: score = K - index. Everything else is coded
# low → high by raw list position: score = index + 1.
_SCALE_HIGH_AT_ZERO = {"consensus", "satisfaction", "cohesion_freq_7", "commitment"}

# Keyword sets for attachment-pole splitting and reverse-key detection.
_ATTACH_ANXIETY_KEYS = ("نگرانم", "دوست ندارند", "تمایل ندارند", "در کنارم نباشند")
_SATIS_REVERSE_KEYS = ("طلاق", "جدایی", "پایان دادن", "ترک کرده", "متاسف", "دعوا", "اعصاب")
_AVOID_COMFORT_KEYS = ("راحت است", "در دسترس هستند", "خوشحال می شوم")


# ---------------------------------------------------------------------------
# Scale + construct detection (signature / content driven, ID-agnostic)
# ---------------------------------------------------------------------------

def _option_values(question: Dict[str, Any]) -> List[str]:
    return [o.get("value", o.get("text", "")) for o in question.get("options", [])]


def _scale_of(question: Dict[str, Any]) -> str:
    """Identify the rating scale of a RADIO question by its option signature."""
    values = _option_values(question)
    vset = set(values)
    if "همیشه توافق داریم" in vset:
        return "consensus"
    if "کمی بعضی اوقات" in vset:
        return "satisfaction"
    if "هر روز" in vset:
        return "cohesion_freq_7"
    if "کمتر از یکبار در ماه" in vset:
        return "cohesion_freq_8"
    if "بسیار ناخشنود" in vset:
        return "happiness"
    if "خیلی کم" in vset and "خیلی زیاد" in vset:
        return "attachment"
    if "نه موافق و نه مخالف" in vset:
        return "ppr"
    if vset == {"بله", "خیر"}:
        return "affectional"
    if any("ته دل" in v for v in values) or "امیدی به موفقیت رابطه ام ندارم" in vset:
        return "commitment"
    return "other"


def _is_attachment_anxiety(question: Dict[str, Any]) -> bool:
    text = question.get("question_text", "")
    return any(k in text for k in _ATTACH_ANXIETY_KEYS)


def classify(question: Dict[str, Any]) -> str:
    """
    Classify a question into:
      demographic_gender | demographic_psych | demographic_occupation |
      demographic_children | demographic_years | instruction
    or a psychometric construct key (consensus / satisfaction / cohesion /
    affectional_expression / global_happiness / commitment /
    partner_responsiveness / attachment_avoidance / attachment_anxiety / other).
    """
    if question.get("type") == "TEXT_INPUT":
        t = question.get("question_text", "")
        if "شغل" in t:
            return "demographic_occupation"
        if "فرزند" in t:
            return "demographic_children"
        if "مشترک" in t or "سابقه" in t:
            return "demographic_years"
        # Factual numeric fields (age, spouse age, menopause age/duration,
        # marriage duration, weight, height) and the survey's instruction
        # prose blocks are real answerable TEXT_INPUTs whose values are mapped
        # explicitly in prompts/answer_generation_prompt.json (e.g. "age" ->
        # "سن شما", "خواندم" for instruction texts). Route them to a construct
        # chunk that IS sent to the LLM instead of binning them as
        # "instruction", which previously made static_answers() hard-code "0".
        if any(k in t for k in ("سن", "وزن", "قد", "یائسگی", "ازدواج", "مدت")) or "عبارت" in t:
            return "other"
        return "instruction"

    vset = set(_option_values(question))
    if vset == {"زن", "مرد"}:
        return "demographic_gender"
    if vset == {"دارم", "ندارم"}:
        return "demographic_psych"

    sk = _scale_of(question)
    if sk == "consensus":
        return "consensus"
    if sk == "satisfaction":
        return "satisfaction"
    if sk in ("cohesion_freq_7", "cohesion_freq_8"):
        return "cohesion"
    if sk == "affectional":
        return "affectional_expression"
    if sk == "happiness":
        return "global_happiness"
    if sk == "commitment":
        return "commitment"
    if sk == "ppr":
        return "partner_responsiveness"
    if sk == "attachment":
        return "attachment_anxiety" if _is_attachment_anxiety(question) else "attachment_avoidance"
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
# Demographic + instruction answers, answered directly in code (no LLM) so a
# persona's identity is perfectly consistent and every respondent is female.
# ---------------------------------------------------------------------------

def static_answers(schema: List[Dict[str, Any]], persona: Dict[str, Any]) -> Dict[str, str]:
    """Map demographic / instruction questions to fixed answers drawn from the persona."""
    d = persona.get("demographics", {})
    out: Dict[str, str] = {}
    for q in schema:
        ck = classify(q)
        qid = q["question_id"]
        if ck == "demographic_gender":
            out[qid] = str(d.get("gender", "زن"))
        elif ck == "demographic_psych":
            out[qid] = str(d.get("psych_problem", "ندارم"))
        elif ck == "demographic_occupation":
            out[qid] = str(d.get("occupation", "")).strip()
        elif ck == "demographic_children":
            out[qid] = str(d.get("children_count", 0))
        elif ck == "demographic_years":
            out[qid] = str(d.get("years_married", 1))
        elif ck == "instruction":
            out[qid] = "0"
    return out


# ---------------------------------------------------------------------------
# Reverse-keying detection — only the constructs whose items are a MIX of direct
# and reverse wording. The answer prompt turns these into per-item [معکوس] tags.
# ---------------------------------------------------------------------------

def is_reverse(question: Dict[str, Any], construct_key: str) -> bool:
    """True if the HIGH/agree end of this item's scale means LOWER of the construct's trait."""
    text = question.get("question_text", "")
    if construct_key == "satisfaction":
        return any(k in text for k in _SATIS_REVERSE_KEYS)
    if construct_key == "attachment_avoidance":
        return any(k in text for k in _AVOID_COMFORT_KEYS)
    if construct_key == "attachment_anxiety":
        return "نگران نیستم" in text
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
    if sk in _SCALE_HIGH_AT_ZERO:
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
        parts.append("وقتی گزینه‌ای واقعاً با حال‌وروزت می‌خواند، راحت از گزینه‌ی نهاییِ طیف (مثل «کاملا موافقم»، «خیلی زیاد» یا «همیشه») استفاده می‌کنی.")
    elif ers < -0.30:
        parts.append("حتی وقتی موافق یا مخالفی، معمولاً گزینه‌های ملایم/میانی را به گزینه‌های نهاییِ طیف ترجیح می‌دهی و کمتر سراغ «کاملا…» یا «خیلی زیاد» می‌روی.")

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