# core/instrument.py
"""
Maps the **personality / mental-health inventory** schema onto its latent
constructs so answer generation can be CHUNKED by construct (breaking the
one-shot self-consistency that inflates Cronbach's alpha) and conditioned on
the right latent-trait bands.

The questionnaire (Porsline form) is a comprehensive Persian personality
battery combining several modules:

  - Demographics            : gender, education, age band, marital status,
                              psychiatric-history disclosure  (5 RADIO)
  - PD screening (بله/خیر)   : ~106 yes/no items covering Avoidant, Dependent,
                              OCPD, Paranoid, Schizotypal, Schizoid, Histrionic,
                              Narcissistic, Borderline, Antisocial / conduct
  - PID-5-style Likert (5)  : ~95 agreement items measuring grandiosity/
                              entitlement/attention, detachment, negative
                              affect, etc.
  - Attachment (7)          : 7-point items split into an anxiety pole and a
                              closeness/avoidance (incl. reverse approach) pole
  - Psychotic-like (4)      : 15 frequency items (هرگز…همیشه), past few months

The six latent traits (see core/trait_sampler.py):
    emotional_instability, detachment_suspicion, impulsivity_antisocial,
    narcissism_attention, rigidity_perfectionism, unusual_experiences

Construct keys are ``<trait>__<scale>`` (e.g. ``emotional_instability__likert5``)
so each chunk is one trait on one scale — small enough for reliable item-by-item
answering and giving the validation harness a per-scale Cronbach's alpha.

DEMOGRAPHIC and INSTRUCTION pseudo-questions are answered directly in code (see
``static_answers``) so a persona's identity stays perfectly consistent; only the
psychometric items are sent to the LLM. Detection is signature/content-driven
(not hard-coded IDs), so it survives ID changes.

``numeric_code`` codes each scale monotonically by the SEMANTIC direction of
THAT question's own option list. The validation harness auto-reverses negative
item-total correlations, so coding only needs to be monotonic and consistent
within a scale.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Tuple


# ---------------------------------------------------------------------------
# Latent traits (must match core/trait_sampler.LOADINGS exactly).
# ---------------------------------------------------------------------------
ALL_TRAITS: List[str] = [
    "emotional_instability",
    "detachment_suspicion",
    "impulsivity_antisocial",
    "narcissism_attention",
    "rigidity_perfectionism",
    "unusual_experiences",
]

_TRAIT_LABELS: Dict[str, str] = {
    "emotional_instability":    "بی‌ثباتی هیجانی و اضطراب",
    "detachment_suspicion":     "انزوا و بدبینی",
    "impulsivity_antisocial":   "تکانشگری و هنجارشکنی",
    "narcissism_attention":     "خودشیفتگی و نیاز به توجه",
    "rigidity_perfectionism":   "کمال‌گرایی و وسواس",
    "unusual_experiences":      "تجربیات غیرعادی",
}

_SCALE_LABELS: Dict[str, str] = {
    "yesno":       "غربالگری شخصیت (بله/خیر)",
    "likert5":     "مقیاس لیکرت ۵درجه‌ای",
    "attachment7": "مقیاس دلبستگی ۷درجه‌ای",
    "frequency4":  "مقیاس فراوانی ۴درجه‌ای",
    "other":       "سایر",
}

_SCALE_ORDER: List[str] = ["yesno", "likert5", "attachment7", "frequency4", "other"]

# construct_key -> (Persian label, latent dims relevant to that construct).
# Built as the cross-product of trait × scale, plus a generic "other" bucket.
CONSTRUCT_TRAIT_DIMS: Dict[str, Tuple[str, List[str]]] = {}
for _t in ALL_TRAITS:
    for _s in _SCALE_ORDER:
        CONSTRUCT_TRAIT_DIMS[f"{_t}__{_s}"] = (_TRAIT_LABELS[_t], [_t])
CONSTRUCT_TRAIT_DIMS["other"] = ("سایر", list(ALL_TRAITS))

# Stable ordering of psychometric constructs in the output.
_CONSTRUCT_ORDER: List[str] = [
    f"{_t}__{_s}" for _t in ALL_TRAITS for _s in _SCALE_ORDER
] + ["other"]

# Scales whose option list runs HIGH → LOW (position 0 = the high/strong end),
# so the ordinal score is reversed: score = K - index. The yes/no PD items put
# «بله» (the pathology/“yes” end) first. Everything else is coded low → high by
# raw list position: score = index + 1.
_SCALE_HIGH_AT_ZERO = {"yesno"}


# ---------------------------------------------------------------------------
# Demographic option signatures (exact sets, order-agnostic) and the instruction
# confirmation string the form expects for TEXT_INPUT section separators.
# ---------------------------------------------------------------------------
_GENDER_OPTS   = frozenset({"آقا", "خانم"})
_EDU_OPTS      = frozenset({"دیپلم", "کاردانی", "کارشناسی", "کارشناسی ارشد", "دکتری"})
_AGE_OPTS      = frozenset({"تا 25 سال", "تا 30 سال", "تا 35سال", "تا 40 سال"})
_MARITAL_OPTS  = frozenset({"مجرد", "متاهل"})
_PSYCH_OPTS    = frozenset({"دارم", "ندارم"})

_INSTRUCTION_TEXT = "خواندم"   # "I read it" — confirms the section-separator prompts

# Persona education values that are NOT directly one of the form's five radio
# options; map them to the nearest valid option.
_EDU_TO_SCHEMA: Dict[str, str] = {
    "دانشجو (کاردانی/کارشناسی)": "کارشناسی",
    "دانشجو ارشد": "کارشناسی ارشد",
}


# ---------------------------------------------------------------------------
# Content keyword sets for routing yes/no and 5-point Likert items to a trait.
# Matching is ZWNJ-agnostic and whitespace-collapsed (see _norm), so it tolerates
# the form's inconsistent spacing/typography. Checks run in priority order; the
# first matching trait wins. Items matching none fall back to emotional
# instability (the residual avoidant / dependent / borderline / negative-affect
# bucket).
# ---------------------------------------------------------------------------
_UNUSUAL_KW = (
    "خرافاتی", "حس ششم", "ششم", "ماوراءالطبیه", "ماوراء الطبیه", "تجارب شخصی",
    "غیر واقعی", "جدا شده", "می‌بینید که دیگران", "صدایی", "صداهایی", "نیروهایی",
    "آهنگ یا موضوعی", "تلوزیون", "با آرزو یا فکر", "معجزه", "دنیای خیال",
    "کنترل نیرو", "پژواک", "همزاد",
)

_IMPULSIVITY_KW = (
    "قبل از 15", "کمتر از 15 سال", "قبل از 13", "قلدر", "دعوا راه", "سلاحی",
    "بی رحمانه", "حیوانات", "سرقت", "اخاذی", "زورگیری", "آتش", "عمدا تخریب",
    "دروغ های زیادی", "فرار کرده", "در می رفتید", "زور وارد", "اموال", "امضای",
    "تکانشی", "منافع خودم", "فریب می دادید", "فریب بدهم", "فریبنده", "استفاده کنم که فقط",
    "هر کاری انجام بدهم، حتی", "آسیب رساندن به دیگران",
    "سوءاستفاده کنم", "سواستفاده کنم",
)

_NARCISSISM_KW = (
    "مرکز توجه", "جلب توجه", "دیده شدن", "خودنمایی", "توجه بگیرم", "توجه مردم",
    "توجه کنند یا تحسین", "ناز و عشوه", "پیشنهاد هم خوابگی", "هیجانی",
    "با هیجان رفتار", "مهم تر، با استعداد", "موفق تر", "برتر از",
    "شایسته برخورد ویژه", "استحقاق", "سزاوار", "انتظار", "با نفوذ", "مهم یا",
    "خودتان خیلی", "تحسین", "رهبری", "رهبر به دنیا", "مسئولیت پذیر",
    "مسئولیت امور", "ظاهر", "آراسته", "خوش تیپ", "مزایای بیشتری",
    "در سطح بالاتری", "از اکثر افراد در بسیاری", "موفق هستم",
    "شهرت", "ماجراهای عاشقانه", "ترین مقام", "نیازهای خود را بر نیاز",
    "سوءاستفاده گری", "ربطی ندارد", "گوش نمی دهید", "حسادت می کنند",
    "ارزش دارند", "بسته به نظر", "لوله کش",
    "مشکلات دیگران برای تان اغلب خسته", "خودخواهانه",
)

_RIGIDITY_KW = (
    "جزئیات", "نظم و ترتیب", "فهرست", "درست انجام دادن", "معیارهای خیلی",
    "دور ریختن اشیا", "یک دنده", "پول خرج کردن برای خود", "تغییرش",
    "تغییر برنامه", "وقت زیادی را به کار", "مفید بودن", "دقیقا ان طوری",
)

_DETACHMENT_KW = (
    "منزوی", "اعتماد می کنند", "علیه تان", "سواستفاده می کنند", "تهدید یا اهانت", "کینه",
    "به خاطر آنچه", "اهانت", "وفاداری همسر", "صحبت می کنند", "نگاه می کنند",
    "مهم نیست دوستان", "ترجیح می دهید کارها را تنها", "ترجیح می دهم تنها",
    "بی علاقه هستید",
    "چیزهای خیلی کمی هستند که برای شما لذت", "مهم نیست که مردم",
    "به ندرت احساسات شدیدی", "صمیمی",
    "علاقه چندانی به تجربه های اجتماعی", "شروع یا ادامه دادن گفتگو",
    "علاقه ام را به فعالیت", "تماس چشمی", "نگاه مستقیم", "ساکت بمانم",
    "از اوقاتی که تنها هستم، لذت", "تنهایی به من کمک", "محیط های پر سر و صدا",
    "خجالت می کنم", "طرد شده ام", "سازگار نیستم", "دوست نزدیک",
    "لمس کند یا در آغوش", "روابط سطحی", "مکان های شلوغ", "افکار و احساساتم",
    "باورها و ارزش های خودم", "غرق می شوم و حضور دیگران",
    "تمایلی به به اشتراک گذاشتن", "نگریانی درباره مشکلات دیگران",
    "تفسیر می کنم", "در میان نمی گذارم",
    "وابسته نیستم", "نیاز داشته باشم", "مستقل", "تنها باشم", "در افکارم غرق",
)

# Attachment-pole splitting (7-point items).
_ATTACH_ANXIETY_KW = (
    "دلتنگی", "آزرده", "تأیید", "دلهره", "فقدان", "دغدغه",
    "نگرانم که", "تمایل صمیمیت دیگران",
)
# Attachment approach items — agreeing means LESS avoidance, so they are reverse.
_ATTACH_APPROACH_KW = (
    "کمک می خواهم", "در میان می گذارم", "اتکا به دیگران", "رازهایم",
)

# Reverse-key markers for the two constructs whose items mix direction.
_EMO_REVERSE_KW = ("به ندرت", "نگرانی نیستم", "اصولا شخص نگرانی", "اصولاً شخص نگرانی")


# ---------------------------------------------------------------------------
# Text normalisation — strip ZWNJ and collapse whitespace so keyword matching
# survives the form's inconsistent typography (e.g. «اداره  حوزه», «تا 35سال»).
# ---------------------------------------------------------------------------
def _norm(s: str) -> str:
    """Strip ZWNJ and ALL whitespace, returning a contiguous string.

    Persian verb prefixes/suffixes (می‌بینید، می‌کنند، تا 35سال) are written
    with ZWNJ, a space, or no separator inconsistently across the form, so
    matching must be separator-agnostic: we remove ZWNJ and every whitespace
    char on both the text and each keyword, then substring-match.
    """
    return "".join(s.replace("‌", "").split())


def _any_kw(text_norm: str, kws: Tuple[str, ...]) -> bool:
    return any(_norm(k) in text_norm for k in kws)


# ---------------------------------------------------------------------------
# Scale + construct detection (signature / content driven, ID-agnostic)
# ---------------------------------------------------------------------------
def _option_values(question: Dict[str, Any]) -> List[str]:
    return [o.get("value", o.get("text", "")) for o in question.get("options", [])]


def _scale_of(question: Dict[str, Any]) -> str:
    """Identify the rating scale of a RADIO question by its option signature."""
    vset = frozenset(_option_values(question))
    if vset & _GENDER_OPTS == vset and vset == _GENDER_OPTS:
        return "demographic_gender"
    if vset == _EDU_OPTS:
        return "demographic_education"
    if vset == _AGE_OPTS:
        return "demographic_age"
    if vset == _MARITAL_OPTS:
        return "demographic_marital"
    if vset == _PSYCH_OPTS:
        return "demographic_psych"
    if "بله" in vset and len(vset) == 2:        # yes/no PD screen (incl. typo بله/خانم)
        return "yesno"
    if "هرگز" in vset and "همیشه" in vset:        # 4-point frequency
        return "frequency4"
    if len(vset) == 7 and "کاملا مخالفم" in vset:  # 7-point attachment
        return "attachment7"
    if "نظری ندارم" in vset and len(vset) == 5:    # 5-point Likert
        return "likert5"
    return "other"


def _classify_content(text_norm: str) -> str:
    """Route a yes/no or 5-point Likert item to one of the six trait keys."""
    if _any_kw(text_norm, _UNUSUAL_KW):
        return "unusual_experiences"
    if _any_kw(text_norm, _IMPULSIVITY_KW):
        return "impulsivity_antisocial"
    if _any_kw(text_norm, _NARCISSISM_KW):
        return "narcissism_attention"
    if _any_kw(text_norm, _RIGIDITY_KW):
        return "rigidity_perfectionism"
    if _any_kw(text_norm, _DETACHMENT_KW):
        return "detachment_suspicion"
    return "emotional_instability"   # residual: avoidant/dependent/borderline/negative-affect


def _classify_attachment(text_norm: str) -> str:
    """Split a 7-point attachment item into its anxiety vs avoidance pole."""
    if _any_kw(text_norm, _ATTACH_APPROACH_KW):   # approach items → avoidance pole (reverse)
        return "detachment_suspicion"
    if _any_kw(text_norm, _ATTACH_ANXIETY_KW):
        return "emotional_instability"
    return "detachment_suspicion"


def classify(question: Dict[str, Any]) -> str:
    """
    Classify a question into a demographic / instruction pseudo-construct or a
    psychometric construct key of the form ``<trait>__<scale>`` (or ``other``).
    """
    if question.get("type") == "TEXT_INPUT":
        # In this inventory every TEXT_INPUT is a section-separator instruction.
        return "instruction"

    sk = _scale_of(question)
    if sk.startswith("demographic_"):
        return sk
    text_norm = _norm(question.get("question_text", ""))
    if sk == "frequency4":
        return "unusual_experiences__frequency4"
    if sk == "attachment7":
        return f"{_classify_attachment(text_norm)}__attachment7"
    if sk == "yesno":
        return f"{_classify_content(text_norm)}__yesno"
    if sk == "likert5":
        return f"{_classify_content(text_norm)}__likert5"
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
# persona's identity is perfectly consistent.
# ---------------------------------------------------------------------------
def _age_band(age: Any) -> str:
    try:
        a = int(age)
    except (TypeError, ValueError):
        a = 30
    if a <= 25:
        return "تا 25 سال"
    if a <= 30:
        return "تا 30 سال"
    if a <= 35:
        return "تا 35سال"   # NOTE: the form's option has no space here
    return "تا 40 سال"


def _map_education(edu: Any, options: List[str]) -> str:
    edu = str(edu) if edu is not None else ""
    if edu in options:
        return edu
    mapped = _EDU_TO_SCHEMA.get(edu)
    if mapped and mapped in options:
        return mapped
    # last resort: substring overlap, else the most common degree
    for o in options:
        if edu and (o in edu or edu in o):
            return o
    return "کارشناسی" if "کارشناسی" in options else options[0]


def static_answers(schema: List[Dict[str, Any]], persona: Dict[str, Any]) -> Dict[str, str]:
    """Map demographic / instruction questions to fixed answers drawn from the persona."""
    d = persona.get("demographics", {}) or {}
    out: Dict[str, str] = {}
    for q in schema:
        ck = classify(q)
        qid = q["question_id"]
        if ck == "demographic_gender":
            g = d.get("gender")
            out[qid] = g if g in _GENDER_OPTS else "آقا"
        elif ck == "demographic_education":
            out[qid] = _map_education(d.get("education_level"), _option_values(q))
        elif ck == "demographic_age":
            out[qid] = _age_band(d.get("age"))
        elif ck == "demographic_marital":
            m = d.get("marital_status")
            out[qid] = m if m in _MARITAL_OPTS else "مجرد"
        elif ck == "demographic_psych":
            p = d.get("psych_problem")
            out[qid] = p if p in _PSYCH_OPTS else "ندارم"
        elif ck == "instruction":
            out[qid] = _INSTRUCTION_TEXT
    return out


# ---------------------------------------------------------------------------
# Reverse-keying detection — only the constructs whose items are a MIX of direct
# and reverse wording. The answer prompt turns these into per-item [معکوس] tags.
# ---------------------------------------------------------------------------
def is_reverse(question: Dict[str, Any], construct_key: str) -> bool:
    """True if the HIGH/agree end of this item's scale means LOWER of the construct's trait."""
    text_norm = _norm(question.get("question_text", ""))
    trait = construct_key.split("__", 1)[0]
    if trait == "emotional_instability":
        # Low-affect negation items: «اصولاً شخص نگرانی نیستم»، «به ندرت ... می‌کنم»
        return _any_kw(text_norm, _EMO_REVERSE_KW)
    if trait == "detachment_suspicion":
        # Attachment approach items: seeking help / self-disclosure (agree = less avoidance)
        return _any_kw(text_norm, _ATTACH_APPROACH_KW)
    return False


# ---------------------------------------------------------------------------
# Ordinal coding for the validation harness (Cronbach's alpha etc.).
# ---------------------------------------------------------------------------
def numeric_code(question: Dict[str, Any], value: str) -> int | None:
    """Map an option string to its ordinal score for this question's scale (monotonic)."""
    opts = _option_values(question)
    if value is None or value not in opts:
        return None
    sk = _scale_of(question)
    # demographic_* are coded low→high by position (not used in alpha, only in
    # response-style indices); yesno is high-at-zero («بله» first = high).
    i = opts.index(value)
    k = len(opts)
    if sk == "yesno":
        return k - i          # position 0 = «بله» (high) → K; last → 1
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