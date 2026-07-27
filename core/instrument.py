# core/instrument.py
"""
Maps the CURRENT questionnaire schema (output/form_schema.json) onto its latent
constructs so answer generation can be CHUNKED by construct (breaking the one-shot
self-consistency that inflates Cronbach's alpha) and conditioned on the right
latent-trait bands.

The current questionnaire is a young-adult Iranian psychological battery:

  - Demographics (5 RADIO):
      سن (age bands)                                 lik_1_1
      جنسیت (gender)                                 lik_1_2   زن / مرد
      مقطع تحصیلی (education)                        lik_1_3   کارشناسی / کارشناسی ارشد / دکتری
      وضعیت تأهل (marital status)                    lik_1_4   مجرد / متاهل / مطلقه / جداشده
      مدت زمان استفاده روزانه از فضای مجازی          lik_1_5   (daily social-media use)

  - Internet Addiction Test (IAT)        msc_1_6_*  (20 items, 5-pt frequency
                                          بندرت … همیشه; ALL DIRECT;
                                          higher = more addiction)  -> internet_addiction
  - Parental Bonding — Care subscale      msc_1_7_*  (16 items = 8 behaviours ×
                                          father/mother: warmth, affection, touch,
                                          approval, open talk, gifts, encouragement,
                                          trust, security; 5-pt amount خیلی کم … خیلی زیاد;
                                          ALL DIRECT; higher = more care)  -> parental_care
  - Dysfunctional Attitudes               msc_1_8_*  (26 items, 7-pt agreement
                                          کاملاً موافقم … کاملاً مخالفم; items 13, 19,
                                          20, 26 are REVERSE — they state healthy /
                                          adaptive beliefs, so agreeing means LESS of
                                          the construct)  -> dysfunctional_attitudes
  - Loneliness / low social & family     msc_1_9_*  (16 items, 5-pt agreement
      support                              کاملاً مخالفم … کاملاً موافقم; ALL DIRECT;
                                          higher = more loneliness)  -> loneliness

DEMOGRAPHIC questions are answered directly in code (see `static_answers`) so a
persona's identity stays perfectly consistent; only the psychometric items are
sent to the LLM. Detection is signature / content-driven and is NORMALISED for
the Arabic↔Persian character traps that porsa sprinkles in (ک/ك, ی/ي, tanwin ً,
ZWNJ ‌, NBSP), with the question-id prefix as a safety net — so it survives both
character variation and id changes, and no real item is mis-binned as "other".

`numeric_code` codes each scale monotonically by the SEMANTIC direction of the
item: scales whose option[0] is the HIGH/strong end are reverse-position coded
(score = K - index), and reverse-worded items within such a scale are flipped
back (score = index + 1).
"""

from __future__ import annotations

import random
import re
from typing import Any, Dict, List, Tuple


# ---------------------------------------------------------------------------
# Character normalisation — the schema stores Persian and Arabic forms
# interchangeably (Arabic ك vs Persian ک, Arabic ي vs Persian ی, tanwin ً,
# ZWNJ ‌, NBSP). All signature matching is done on the normalised form so the
# two look identical to the detector.
# ---------------------------------------------------------------------------

_KAF_AR, _KAF_FA = "ك", "ک"
_YEH_AR, _YEH_FA = "ي", "ی"
# Arabic diacritics / tashkeel to strip (tanwin, harakat, superscript alef).
_DIACRITICS = "ًٌٍَُِّْٰ"
_WS_RE = re.compile(r"\s+")


def _norm(s: str) -> str:
    if not s:
        return ""
    s = s.replace(_KAF_AR, _KAF_FA).replace(_YEH_AR, _YEH_FA)
    for d in _DIACRITICS:
        s = s.replace(d, "")
    s = s.replace(" ", " ").replace("‌", " ")  # NBSP, ZWNJ
    return _WS_RE.sub(" ", s).strip()


# ---------------------------------------------------------------------------
# construct_key -> (Persian label, latent dims relevant to that construct)
# ---------------------------------------------------------------------------

CONSTRUCT_TRAIT_DIMS: Dict[str, Tuple[str, List[str]]] = {
    "internet_addiction":     ("اعتیاد به اینترنت",               ["internet_addiction"]),
    "parental_care":          ("محبت و مراقبت ادراک‌شده والدین",   ["parental_care"]),
    "dysfunctional_attitudes":("نگرش‌های اختلال‌ساز و کمال‌گرایی",   ["dysfunctional_attitudes"]),
    "loneliness":             ("تنهایی و کمبود حمایت اجتماعی",   ["loneliness"]),
    "other":                  ("سایر",                             [
        "internet_addiction", "parental_care", "dysfunctional_attitudes", "loneliness"]),
}

# Stable ordering of constructs in the output.
_CONSTRUCT_ORDER: List[str] = [
    "internet_addiction", "parental_care", "dysfunctional_attitudes",
    "loneliness", "other",
]

# Constructs whose items mix direct and reverse wording — the answer generator
# emits per-item [معکوس]/[مستقیم] direction tags only for these.
REVERSE_KEYED_CONSTRUCTS = {"dysfunctional_attitudes"}

# Scales whose option list runs HIGH → LOW (option[0] = the high/strong end),
# so direct items are reverse-position coded: score = K - index. Reverse-worded
# items inside such a scale are flipped back to score = index + 1.
_SCALE_HIGH_AT_ZERO = {"dysfunctional_attitudes"}

# Reverse-worded items of the dysfunctional-attitudes scale: these four state
# HEALTHY / adaptive beliefs, so agreeing (کاملاً موافقم) means LESS of the
# construct.  (13) one can enjoy an activity without regard to its outcome;
# (19) one can reach goals without being too hard on oneself; (20) one can be
# blamed without becoming sad; (26) one can be happy without anyone loving them.
_DYSF_REVERSE_IDS = {"msc_1_8_13", "msc_1_8_19", "msc_1_8_20", "msc_1_8_26"}


# ---------------------------------------------------------------------------
# Scale + demographic detection (signature / content driven, id safety net)
# ---------------------------------------------------------------------------

def _option_values(question: Dict[str, Any]) -> List[str]:
    return [o.get("value", o.get("text", "")) for o in question.get("options", [])]


def _scale_of(question: Dict[str, Any]) -> str:
    """Identify the rating scale of a MATRIX_RADIO question by its (normalised)
    option signature. Each scale has a unique anchor that no other scale shares."""
    vset = {_norm(v) for v in _option_values(question)}
    if "بندرت" in vset and "همیشه" in vset:        # IAT frequency
        return "internet_addiction"
    if "خیلی کم" in vset and "خیلی زیاد" in vset:  # parental-care amount
        return "parental_care"
    if "بی تفاوتم" in vset and "کاملا موافقم" in vset:  # 7-pt agreement
        return "dysfunctional_attitudes"
    if "تاحدی" in vset and "کاملا مخالفم" in vset:      # 5-pt agreement (loneliness)
        return "loneliness"
    return "other"


def _scale_of_id(question: Dict[str, Any]) -> str:
    """Safety-net: classify by question-id prefix so no real item ever falls
    through to 'other' even if a signature edge-case fails."""
    qid = question.get("question_id", "")
    if qid.startswith("msc_1_6_"):
        return "internet_addiction"
    if qid.startswith("msc_1_7_"):
        return "parental_care"
    if qid.startswith("msc_1_8_"):
        return "dysfunctional_attitudes"
    if qid.startswith("msc_1_9_"):
        return "loneliness"
    return "other"


def _demographic_of(question: Dict[str, Any]) -> str | None:
    """Identify one of the five demographic RADIO questions by content (with the
    gender option-set as the primary signal), normalised for kaf/ye/diacritics."""
    if question.get("type") != "RADIO":
        return None
    t = _norm(question.get("question_text", ""))
    vals = _option_values(question)
    vset_norm = {_norm(v) for v in vals}
    if vset_norm == {"زن", "مرد"}:
        return "demographic_gender"
    # age: every option is an age band ending in «سال»
    if vals and all("سال" in _norm(v) for v in vals):
        return "demographic_age"
    if "تحصیل" in t or "مقطع" in t:
        return "demographic_education"
    if "تاهل" in t or "تأهل" in t:
        return "demographic_marital"
    if "مجازی" in t or ("استفاده" in t and "روزانه" in t):
        return "demographic_social_media"
    return None


def classify(question: Dict[str, Any]) -> str:
    """
    Classify a question into one of the five demographic keys
    (demographic_age | demographic_gender | demographic_education |
     demographic_marital | demographic_social_media) or a psychometric construct
    (internet_addiction | parental_care | dysfunctional_attitudes | loneliness |
     other).
    """
    demo = _demographic_of(question)
    if demo:
        return demo
    sk = _scale_of(question)
    if sk == "other":
        sk = _scale_of_id(question)
    return sk


def group_by_construct(schema: List[Dict[str, Any]]) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """Psychometric constructs only (demographics excluded), in stable order."""
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for q in schema:
        ck = classify(q)
        if ck.startswith("demographic_"):
            continue
        buckets.setdefault(ck, []).append(q)
    return [(ck, buckets[ck]) for ck in _CONSTRUCT_ORDER if ck in buckets]


# ---------------------------------------------------------------------------
# Demographic answers, answered directly in code (no LLM) so a persona's
# identity is perfectly consistent. The persona stores the EXACT schema option
# strings, so these answers are always valid options.
# ---------------------------------------------------------------------------

def static_answers(schema: List[Dict[str, Any]], persona: Dict[str, Any]) -> Dict[str, str]:
    d = persona.get("demographics", {})
    out: Dict[str, str] = {}
    for q in schema:
        ck = classify(q)
        qid = q["question_id"]
        if ck == "demographic_age":
            out[qid] = str(d.get("age_band", ""))
        elif ck == "demographic_gender":
            out[qid] = str(d.get("gender", ""))
        elif ck == "demographic_education":
            out[qid] = str(d.get("education", ""))
        elif ck == "demographic_marital":
            out[qid] = str(d.get("marital_status", ""))
        elif ck == "demographic_social_media":
            out[qid] = str(d.get("social_media_use", ""))
    return out


# ---------------------------------------------------------------------------
# Reverse-keying — only dysfunctional_attitudes mixes direct and reverse items.
# ---------------------------------------------------------------------------

def is_reverse(question: Dict[str, Any], construct_key: str) -> bool:
    """True if the HIGH/agree end of this item means LOWER of the construct."""
    if construct_key == "dysfunctional_attitudes":
        return question.get("question_id") in _DYSF_REVERSE_IDS
    return False


# ---------------------------------------------------------------------------
# Ordinal coding for the validation harness (Cronbach's alpha etc.).
# ---------------------------------------------------------------------------

def numeric_code(question: Dict[str, Any], value: str) -> int | None:
    """Map an option string to its ordinal score for this question's scale
    (monotonic, with reverse-keyed items flipped)."""
    opts = _option_values(question)
    if value in opts:
        i = opts.index(value)
    else:
        nopts = [_norm(o) for o in opts]
        nv = _norm(value)
        if nv not in nopts:
            return None
        i = nopts.index(nv)
    sk = _scale_of(question)
    if sk == "other":
        sk = _scale_of_id(question)
    k = len(opts)
    rev = is_reverse(question, sk)
    if sk in _SCALE_HIGH_AT_ZERO:
        # option[0] = strong/high end. Direct: high→K. Reverse items: flipped.
        return (i + 1) if rev else (k - i)
    # option[0] = low end. Direct: low→1. (Reverse would flip, but none here.)
    return (k - i) if rev else (i + 1)


def scale_size(question: Dict[str, Any]) -> int:
    return len(question.get("options", [])) or 0


# ---------------------------------------------------------------------------
# Response-style -> natural-language nudges for the answering LLM.
# Injects realistic METHOD VARIANCE (acquiescence, extreme/midpoint style)
# without telling the model the actual trait answers.
# ---------------------------------------------------------------------------

def response_style_instruction(style: Dict[str, Any]) -> str:
    parts: List[str] = []

    acq = style.get("acquiescence", 0.0)
    if acq > 0.30:
        parts.append("در سؤال‌های مرزی که مطمئن نیستی، گرایش خفیفی به سمت گزینه‌های موافق/بالاتر (مثل «کاملاً موافقم» یا «همیشه») داری.")
    elif acq < -0.30:
        parts.append("در سؤال‌های مرزی که مطمئن نیستی، گرایش خفیفی به سمت گزینه‌های مخالف/پایین‌تر (مثل «کاملاً مخالفم» یا «بندرت») داری.")

    ers = style.get("extreme_response", 0.0)
    if ers > 0.30:
        parts.append("وقتی گزینه‌ای واقعاً با حال‌وروزت می‌خواند، راحت از گزینه‌ی نهاییِ طیف (مثل «کاملاً موافقم»، «خیلی زیاد» یا «همیشه») استفاده می‌کنی.")
    elif ers < -0.30:
        parts.append("حتی وقتی موافق یا مخالفی، معمولاً گزینه‌های ملایم/میانی (مثل «تاحدی» یا «در حد متوسط») را به گزینه‌های نهاییِ طیف ترجیح می‌دهی.")

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