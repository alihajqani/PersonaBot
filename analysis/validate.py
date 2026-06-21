# analysis/validate.py
"""
Psychometric validation harness for the synthetic (silicon-sampled) responses.

Runs WITHOUT any human reference and reports the structural-realism signals that
reviewers scrutinize in LLM survey data:

  * per-item mean / SD / full response distribution
  * Cronbach's alpha per construct (with automatic reverse-item detection)
  * mean inter-item correlation and corrected item-total correlations
  * response-style indices: acquiescence (ARS), extreme (ERS), midpoint (MRS)
  * straightlining / careless-responder rate (near-zero intra-construct variance)

If you later obtain published Persian norms, drop a JSON like
  {"self_efficacy": {"mean": 3.6, "sd": 0.6, "alpha": 0.82}, ...}
and pass it with --reference to get side-by-side deltas.

Usage:
  python -m analysis.validate
  python -m analysis.validate --reference norms.json

Pure standard library.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import utils
from core import instrument


# ---------- small stats helpers (pure python) ----------

def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def _var(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def _sd(xs: List[float]) -> float:
    return math.sqrt(_var(xs))


def _corr(xs: List[float], ys: List[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = _mean(xs), _mean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / math.sqrt(sxx * syy) if sxx and syy else 0.0


# ---------- data loading ----------

def load_responses() -> List[Dict[str, Any]]:
    dirs = [config.ANSWERS_DIR_PATH, os.path.join(config.ANSWERS_DIR_PATH, "done")]
    rows = []
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if fn.endswith(".json"):
                data = utils.load_json_file(os.path.join(d, fn), fn)
                if isinstance(data, dict):
                    rows.append(data)
    return rows


# ---------- per-construct analysis ----------

def analyze_construct(
    qs: List[Dict[str, Any]],
    rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    qids = [q["question_id"] for q in qs]
    qmax = {q["question_id"]: instrument.scale_size(q) for q in qs}
    qmin = 1

    # numeric matrix, listwise-complete respondents for this construct
    matrix: List[List[float]] = []
    for r in rows:
        coded = []
        ok = True
        for q in qs:
            v = r.get(q["question_id"])
            c = instrument.numeric_code(q, v) if v is not None else None
            if c is None:
                ok = False
                break
            coded.append(float(c))
        if ok:
            matrix.append(coded)

    n = len(matrix)
    result: Dict[str, Any] = {"n_complete": n, "n_items": len(qs)}
    if n < 3:
        result["error"] = "too few complete respondents"
        return result

    cols = [[row[j] for row in matrix] for j in range(len(qs))]

    # auto reverse-key detection via corrected item-total correlation sign
    totals = [sum(row) for row in matrix]
    reversed_items = []
    for j, q in enumerate(qs):
        rest = [totals[i] - cols[j][i] for i in range(n)]
        itc = _corr(cols[j], rest)
        if itc < 0:
            mx = qmax[q["question_id"]]
            cols[j] = [(qmin + mx) - v for v in cols[j]]
            reversed_items.append(q["question_id"])
    # recompute matrix/totals after reversing
    matrix = [[cols[j][i] for j in range(len(qs))] for i in range(n)]
    totals = [sum(row) for row in matrix]

    # Cronbach's alpha
    k = len(qs)
    item_var_sum = sum(_var(c) for c in cols)
    total_var = _var(totals)
    alpha = (k / (k - 1)) * (1 - item_var_sum / total_var) if total_var > 0 and k > 1 else float("nan")

    # mean inter-item correlation
    inter = []
    for a in range(k):
        for b in range(a + 1, k):
            inter.append(_corr(cols[a], cols[b]))
    mic = _mean(inter) if inter else float("nan")

    # corrected item-total correlations
    itcs = []
    for j in range(k):
        rest = [totals[i] - matrix[i][j] for i in range(n)]
        itcs.append(_corr(cols[j], rest))

    # scale-score moments (mean item score per respondent)
    scale_scores = [t / k for t in totals]

    result.update({
        "alpha": round(alpha, 3),
        "mean_inter_item_r": round(mic, 3),
        "scale_mean": round(_mean(scale_scores), 3),
        "scale_sd": round(_sd(scale_scores), 3),
        "reversed_items_detected": len(reversed_items),
        "min_item_total_r": round(min(itcs), 3),
        "items": [
            {
                "id": qs[j]["question_id"],
                "mean": round(_mean(cols[j]), 2),
                "sd": round(_sd(cols[j]), 2),
                "item_total_r": round(itcs[j], 2),
            }
            for j in range(k)
        ],
    })
    return result


# ---------- response-style + careless analysis ----------

def analyze_response_styles(schema: List[Dict[str, Any]], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    qmap = {q["question_id"]: q for q in schema}
    ars, ers, mrs, straight = [], [], [], 0
    per_resp_count = 0

    for r in rows:
        codes = []
        endpts = mids = agree = total = 0
        for qid, q in qmap.items():
            v = r.get(qid)
            c = instrument.numeric_code(q, v) if v is not None else None
            if c is None:
                continue
            K = instrument.scale_size(q)
            codes.append(c)
            total += 1
            if c == 1 or c == K:
                endpts += 1
            if c >= (K / 2) + 0.5:   # agree-side
                agree += 1
            if K % 2 == 1 and c == (K + 1) / 2:  # exact midpoint (odd scales only)
                mids += 1
        if total < 5:
            continue
        per_resp_count += 1
        ers.append(endpts / total)
        ars.append(agree / total)
        mrs.append(mids / total)
        if _sd([float(x) for x in codes]) < 0.5:   # near-flat = straightlining
            straight += 1

    return {
        "n_respondents": per_resp_count,
        "acquiescence_rate_mean": round(_mean(ars), 3) if ars else None,
        "extreme_response_rate_mean": round(_mean(ers), 3) if ers else None,
        "midpoint_rate_mean": round(_mean(mrs), 3) if mrs else None,
        "straightlining_rate": round(straight / per_resp_count, 3) if per_resp_count else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", help="path to published-norms JSON for side-by-side deltas")
    args = ap.parse_args()

    schema = utils.load_json_file(config.SCHEMA_FILE_PATH, "schema")
    rows = load_responses()
    if not rows:
        print("No responses found in output/answers (or answers/done). Generate answers first.")
        return

    print(f"\n{'='*60}\nVALIDATION REPORT  —  {len(rows)} synthetic respondents\n{'='*60}")

    report: Dict[str, Any] = {"n_respondents": len(rows), "constructs": {}}

    reference = {}
    if args.reference and os.path.isfile(args.reference):
        reference = utils.load_json_file(args.reference, "reference norms") or {}

    for ck, qs in instrument.group_by_construct(schema):
        label = instrument.CONSTRUCT_TRAIT_DIMS.get(ck, (ck, []))[0]
        res = analyze_construct(qs, rows)
        report["constructs"][ck] = res
        print(f"\n■ {label}  ({ck})  — {res['n_items']} items, n={res.get('n_complete')}")
        if "error" in res:
            print(f"    {res['error']}")
            continue
        print(f"    Cronbach α            : {res['alpha']}")
        print(f"    mean inter-item r     : {res['mean_inter_item_r']}")
        print(f"    scale mean (1..K)     : {res['scale_mean']}  (sd {res['scale_sd']})")
        print(f"    reverse items detected: {res['reversed_items_detected']}")
        print(f"    min item-total r      : {res['min_item_total_r']}")
        if ck in reference:
            ref = reference[ck]
            if "alpha" in ref:
                print(f"    ↳ ref α={ref['alpha']}  Δ={round(res['alpha']-ref['alpha'],3)}")
            if "mean" in ref:
                print(f"    ↳ ref mean={ref['mean']}  Δ={round(res['scale_mean']-ref['mean'],3)}")
        # sanity flags
        if res["alpha"] > 0.95:
            print("    ⚠ α>0.95 — implausibly high; likely inflated internal consistency.")
        if res["mean_inter_item_r"] > 0.6:
            print("    ⚠ mean inter-item r>0.6 — over-clean correlation structure.")

    styles = analyze_response_styles(schema, rows)
    report["response_styles"] = styles
    print(f"\n■ Response styles / careless")
    print(f"    acquiescence rate    : {styles['acquiescence_rate_mean']}")
    print(f"    extreme-response rate: {styles['extreme_response_rate_mean']}")
    print(f"    midpoint rate        : {styles['midpoint_rate_mean']}")
    print(f"    straightlining rate  : {styles['straightlining_rate']}")

    out_path = os.path.join(config.OUTPUT_DIR, "validation_report.json")
    utils.save_json_file(out_path, report, "validation report")
    print(f"\nFull report written to {out_path}\n")


if __name__ == "__main__":
    main()
