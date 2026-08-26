"""
Offline evaluation for RedTeamAgent.

Measures whether the reviewer detects labelled/seeded report failures without
making the reviewer a release authority.

Gold JSONL (one case per line)
------------------------------
{
  "case_id": "CASE-001",
  "expected_review_status": "complete",
  "available_evidence_ids": ["FACT-001", "FACT-009"],
  "clean_claim_ids": ["CLAIM-010"],
  "seeded_issues": [
    {
      "gold_issue_id": "GI-001",
      "category": "unsupported_inference",
      "severity": "high",
      "claim_id": "CLAIM-001",
      "section": "hidden_insights",
      "description": "...",
      "acceptable_categories": ["unsupported_inference", "grounding_mismatch"],
      "acceptable_severities": ["high", "critical"],
      "case_tags": ["hidden_insight", "seeded_error"]
    }
  ]
}

Prediction JSONL
----------------
{
  "case_id": "CASE-001",
  "review_status": "complete",
  "issue_counts": {"critical": 0, "high": 1, "medium": 0, "low": 0},
  "issues": [
    {
      "related_gold_issue_id": "GI-001",
      "category": "unsupported_inference",
      "severity": "high",
      "claim_id": "CLAIM-001",
      "section": "hidden_insights",
      "description": "...",
      "evidence_ids": ["FACT-001"]
    }
  ]
}

Metrics
-------
- issue detection precision / recall / F1
- false alarm rate
- escaped error rate
- category accuracy + acceptable-category accuracy
- severity accuracy + acceptable-severity accuracy
- critical issue recall
- high-or-critical issue recall
- per-category P/R/F1
- per-severity recall
- case-tag recall
- review-status accuracy
- issue-count consistency
- evidence-reference validity (when a gold allowlist exists)
- clean-claim false alarm rate

Notes
-----
- RedTeamAgent does not authorize release.
- Unmatched reviewer findings count as benchmark false positives, but finite
  gold sets can miss legitimate findings, so inspect them manually.
- Explicit related_gold_issue_id matching is preferred.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


SEVERITIES = ("critical", "high", "medium", "low")
REVIEW_STATUSES = ("complete", "partial", "failed")

ISSUE_CATEGORIES = (
    "unsupported_factual_claim",
    "unsupported_inference",
    "invalid_evidence_reference",
    "temporal_misrepresentation",
    "attribution_error",
    "causal_overreach",
    "fact_inference_confusion",
    "scenario_as_fact",
    "quantitative_overreach",
    "contradiction",
    "support_mislabel",
    "missing_uncertainty",
    "counterevidence_omission",
    "topic_drift",
    "example_leakage",
    "source_independence_overclaim",
    "grounding_mismatch",
    "unsupported_watch_item",
    "incomplete_report",
)

CATEGORY_ALIASES = {
    "unsupported_fact": "unsupported_factual_claim",
    "unsupported_factual_claim": "unsupported_factual_claim",
    "unsupported_inference": "unsupported_inference",
    "invalid_citation": "invalid_evidence_reference",
    "invalid_evidence_id": "invalid_evidence_reference",
    "invalid_evidence_reference": "invalid_evidence_reference",
    "temporal_error": "temporal_misrepresentation",
    "temporal_misrepresentation": "temporal_misrepresentation",
    "attribution_error": "attribution_error",
    "wrong_attribution": "attribution_error",
    "causal_overreach": "causal_overreach",
    "fact_inference_confusion": "fact_inference_confusion",
    "scenario_as_fact": "scenario_as_fact",
    "quantitative_overreach": "quantitative_overreach",
    "contradiction": "contradiction",
    "support_mislabel": "support_mislabel",
    "missing_uncertainty": "missing_uncertainty",
    "counterevidence_omission": "counterevidence_omission",
    "missing_counterevidence": "counterevidence_omission",
    "topic_drift": "topic_drift",
    "example_leakage": "example_leakage",
    "source_independence_overclaim": "source_independence_overclaim",
    "grounding_mismatch": "grounding_mismatch",
    "unsupported_watch_item": "unsupported_watch_item",
    "incomplete_report": "incomplete_report",
}


def _clean(v: Any) -> str:
    return "" if v is None else " ".join(str(v).split()).strip()


def _key(v: Any) -> str:
    text = _clean(v).casefold().replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "", text)
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


def _canonical_text(v: Any) -> str:
    text = _clean(v).casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _tokens(v: Any) -> Set[str]:
    return {x for x in _canonical_text(v).split() if len(x) > 1}


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    a, b = set(a), set(b)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _category(v: Any) -> str:
    return CATEGORY_ALIASES.get(_key(v), "")


def _severity(v: Any) -> str:
    x = _key(v)
    return x if x in SEVERITIES else ""


def _review_status(v: Any) -> str:
    x = _key(v)
    return x if x in REVIEW_STATUSES else ""


def _id(v: Any) -> str:
    return _clean(v).upper()


def _id_list(v: Any) -> List[str]:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return []

    out: List[str] = []
    seen: Set[str] = set()

    for item in v:
        if isinstance(item, Mapping):
            item = (
                item.get("id")
                or item.get("evidence_id")
                or item.get("claim_id")
                or ""
            )
        x = _id(item)
        if x and x not in seen:
            seen.add(x)
            out.append(x)

    return out


def _tags(v: Any) -> List[str]:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return []

    out, seen = [], set()
    for item in v:
        x = _key(item)
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _safe_div(n: int | float, d: int | float) -> Optional[float]:
    return None if d == 0 else float(n) / float(d)


def _r(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(v, 4)


def _prf(tp: int, fp: int, fn: int) -> Dict[str, Any]:
    p = _safe_div(tp, tp + fp)
    r = _safe_div(tp, tp + fn)
    if p is None or r is None:
        f1 = None
    elif p + r == 0:
        f1 = 0.0
    else:
        f1 = 2 * p * r / (p + r)

    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": _r(p),
        "recall": _r(r),
        "f1": _r(f1),
    }


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    path = Path(path)
    rows: List[Dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_no} of {path}: {exc}"
                ) from exc
            if not isinstance(obj, dict):
                raise ValueError(
                    f"Line {line_no} of {path} must be a JSON object."
                )
            rows.append(obj)

    return rows


def _case_id(r: Mapping[str, Any]) -> str:
    return _clean(r.get("case_id") or r.get("report_id") or r.get("id"))


def _index_cases(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}

    for i, row in enumerate(rows):
        cid = _case_id(row)
        if not cid:
            raise ValueError(f"Red-team record {i} is missing case_id.")
        if cid in out:
            raise ValueError(f"Duplicate case_id: {cid}")
        out[cid] = dict(row)

    return out


def _gold_issues(case: Mapping[str, Any]) -> List[Dict[str, Any]]:
    v = case.get("seeded_issues") or case.get("gold_issues") or []
    if isinstance(v, Mapping):
        v = [v]
    return [dict(x) for x in v if isinstance(x, Mapping)] if isinstance(v, list) else []


def _pred_issues(case: Mapping[str, Any]) -> List[Dict[str, Any]]:
    v = case.get("issues", [])
    if isinstance(v, Mapping):
        v = [v]
    return [dict(x) for x in v if isinstance(x, Mapping)] if isinstance(v, list) else []


def _gold_issue_id(issue: Mapping[str, Any]) -> str:
    return _clean(issue.get("gold_issue_id") or issue.get("issue_id") or issue.get("id"))


def _related_gold_id(issue: Mapping[str, Any]) -> str:
    return _clean(
        issue.get("related_gold_issue_id")
        or issue.get("reference_issue_id")
        or issue.get("gold_issue_id")
    )


def _claim_id(issue: Mapping[str, Any]) -> str:
    return _id(issue.get("claim_id") or issue.get("report_claim_id") or "")


def _section(issue: Mapping[str, Any]) -> str:
    return _key(issue.get("section") or issue.get("report_section") or "")


def _description(issue: Mapping[str, Any]) -> str:
    return _clean(
        issue.get("description")
        or issue.get("message")
        or issue.get("reason")
        or issue.get("issue")
    )


def _acceptable_categories(issue: Mapping[str, Any]) -> Set[str]:
    v = issue.get("acceptable_categories")
    if v is None:
        c = _category(issue.get("category"))
        return {c} if c else set()
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return set()
    return {c for x in v if (c := _category(x))}


def _acceptable_severities(issue: Mapping[str, Any]) -> Set[str]:
    v = issue.get("acceptable_severities")
    if v is None:
        s = _severity(issue.get("severity"))
        return {s} if s else set()
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return set()
    return {s for x in v if (s := _severity(x))}


def _evidence_ids(issue: Mapping[str, Any]) -> Set[str]:
    return set(
        _id_list(
            issue.get("evidence_ids")
            or issue.get("supporting_evidence_ids")
            or issue.get("references")
            or []
        )
    )


def _similarity(gold: Mapping[str, Any], pred: Mapping[str, Any]) -> float:
    category_score = float(
        _category(pred.get("category")) in _acceptable_categories(gold)
        and bool(_category(pred.get("category")))
    )

    gc, pc = _claim_id(gold), _claim_id(pred)
    claim_score = 0.5 if not gc else float(bool(pc) and gc == pc)

    gs, ps = _section(gold), _section(pred)
    section_score = 0.5 if not gs else float(bool(ps) and gs == ps)

    description_score = _jaccard(
        _tokens(_description(gold)),
        _tokens(_description(pred)),
    )

    return (
        0.45 * category_score
        + 0.25 * claim_score
        + 0.15 * section_score
        + 0.15 * description_score
    )


def _greedy_assignment(
    matrix: Sequence[Sequence[float]],
) -> List[Tuple[int, int, float]]:
    candidates = [
        (matrix[g][p], g, p)
        for g in range(len(matrix))
        for p in range(len(matrix[g]))
    ]
    candidates.sort(key=lambda x: (-x[0], x[1], x[2]))

    used_g, used_p = set(), set()
    out = []

    for score, g, p in candidates:
        if g in used_g or p in used_p:
            continue
        used_g.add(g)
        used_p.add(p)
        out.append((g, p, score))

    return out


def match_issues(
    gold_issues: Sequence[Mapping[str, Any]],
    pred_issues: Sequence[Mapping[str, Any]],
    *,
    minimum_similarity: float = 0.55,
) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    used_g, used_p = set(), set()

    gold_index = {
        _gold_issue_id(issue): i
        for i, issue in enumerate(gold_issues)
        if _gold_issue_id(issue)
    }

    # Exact supervised match first.
    for p, issue in enumerate(pred_issues):
        gid = _related_gold_id(issue)
        if not gid or gid not in gold_index:
            continue

        g = gold_index[gid]
        if g in used_g:
            continue

        used_g.add(g)
        used_p.add(p)
        matches.append(
            {
                "gold_index": g,
                "predicted_index": p,
                "score": 1.0,
                "method": "explicit_gold_issue_id",
            }
        )

    remaining_g = [i for i in range(len(gold_issues)) if i not in used_g]
    remaining_p = [i for i in range(len(pred_issues)) if i not in used_p]

    if not remaining_g or not remaining_p:
        return matches

    matrix = [
        [
            _similarity(gold_issues[g], pred_issues[p])
            for p in remaining_p
        ]
        for g in remaining_g
    ]

    for lg, lp, score in _greedy_assignment(matrix):
        if score < minimum_similarity:
            continue
        matches.append(
            {
                "gold_index": remaining_g[lg],
                "predicted_index": remaining_p[lp],
                "score": score,
                "method": "structured_alignment",
            }
        )

    return matches


def _issue_counts_consistent(
    case: Mapping[str, Any],
    issues: Sequence[Mapping[str, Any]],
) -> Optional[bool]:
    counts = case.get("issue_counts")
    if not isinstance(counts, Mapping):
        return None

    actual = Counter(
        _severity(issue.get("severity"))
        for issue in issues
        if _severity(issue.get("severity"))
    )

    for sev in SEVERITIES:
        try:
            reported = int(counts.get(sev, 0))
        except (TypeError, ValueError):
            return False
        if reported != actual.get(sev, 0):
            return False

    return True


def evaluate_red_team_records(
    gold_records: Sequence[Mapping[str, Any]],
    prediction_records: Sequence[Mapping[str, Any]],
    *,
    minimum_issue_similarity: float = 0.55,
) -> Dict[str, Any]:
    gold_by_case = _index_cases(gold_records)
    pred_by_case = _index_cases(prediction_records)

    gold_case_ids = set(gold_by_case)
    pred_case_ids = set(pred_by_case)

    missing_case_ids = sorted(gold_case_ids - pred_case_ids)
    extra_case_ids = sorted(pred_case_ids - gold_case_ids)

    total_gold = total_pred = total_matched = 0

    review_ok = review_total = 0
    counts_ok = counts_total = 0

    exact_cat_ok = acceptable_cat_ok = cat_total = 0
    exact_sev_ok = acceptable_sev_ok = sev_total = 0

    category_gold = Counter()
    category_pred = Counter()
    category_tp = Counter()

    severity_gold = Counter()
    severity_detected = Counter()

    tag_gold = Counter()
    tag_detected = Counter()

    valid_evidence = evidence_total = 0

    clean_claim_total = clean_claim_flagged = 0

    per_case: List[Dict[str, Any]] = []

    for cid in sorted(gold_case_ids):
        gold_case = gold_by_case[cid]
        pred_case = pred_by_case.get(cid, {})

        gold_issues = _gold_issues(gold_case)
        pred_issues = _pred_issues(pred_case)

        total_gold += len(gold_issues)
        total_pred += len(pred_issues)

        expected_status = _review_status(
            gold_case.get("expected_review_status")
            or gold_case.get("gold_review_status")
        )
        predicted_status = _review_status(pred_case.get("review_status"))

        if expected_status:
            review_total += 1
            review_ok += int(expected_status == predicted_status)

        consistency = _issue_counts_consistent(pred_case, pred_issues)
        if consistency is not None:
            counts_total += 1
            counts_ok += int(consistency)

        matches = match_issues(
            gold_issues,
            pred_issues,
            minimum_similarity=minimum_issue_similarity,
        )
        total_matched += len(matches)

        matched_g = {m["gold_index"] for m in matches}
        matched_p = {m["predicted_index"] for m in matches}

        # Category raw totals.
        for issue in gold_issues:
            c = _category(issue.get("category"))
            if c:
                category_gold[c] += 1

            s = _severity(issue.get("severity"))
            if s:
                severity_gold[s] += 1

            for tag in _tags(issue.get("case_tags") or issue.get("tags")):
                tag_gold[tag] += 1

        for issue in pred_issues:
            c = _category(issue.get("category"))
            if c:
                category_pred[c] += 1

        allowed_evidence = set(
            _id_list(
                gold_case.get("available_evidence_ids")
                or gold_case.get("valid_evidence_ids")
                or []
            )
        )

        for m in matches:
            g = gold_issues[m["gold_index"]]
            p = pred_issues[m["predicted_index"]]

            gc = _category(g.get("category"))
            pc = _category(p.get("category"))

            cat_total += 1
            exact_cat_ok += int(bool(gc) and gc == pc)
            acceptable_cat_ok += int(
                bool(pc) and pc in _acceptable_categories(g)
            )

            # For per-category F1, count TP against predicted category only
            # when that category is acceptable for the matched gold issue.
            if pc and pc in _acceptable_categories(g):
                category_tp[pc] += 1

            gs = _severity(g.get("severity"))
            ps = _severity(p.get("severity"))

            if gs:
                sev_total += 1
                exact_sev_ok += int(gs == ps)
                acceptable_sev_ok += int(
                    bool(ps) and ps in _acceptable_severities(g)
                )
                severity_detected[gs] += 1

            for tag in _tags(g.get("case_tags") or g.get("tags")):
                tag_detected[tag] += 1

            refs = _evidence_ids(p)
            if refs:
                evidence_total += len(refs)
                if allowed_evidence:
                    valid_evidence += len(refs & allowed_evidence)
                else:
                    valid_evidence += len(refs)

        clean_claims = set(_id_list(gold_case.get("clean_claim_ids") or []))
        clean_claim_total += len(clean_claims)

        flagged_clean = {
            _claim_id(issue)
            for issue in pred_issues
            if _claim_id(issue) in clean_claims
        }
        clean_claim_flagged += len(flagged_clean)

        per_case.append(
            {
                "case_id": cid,
                "gold_issue_count": len(gold_issues),
                "predicted_issue_count": len(pred_issues),
                "matched_issue_count": len(matches),
                "issue_precision": _r(_safe_div(len(matches), len(pred_issues))),
                "issue_recall": _r(_safe_div(len(matches), len(gold_issues))),
                "expected_review_status": expected_status,
                "predicted_review_status": predicted_status,
                "issue_counts_consistent": consistency,
                "matches": [
                    {
                        "gold_issue_id": _gold_issue_id(gold_issues[m["gold_index"]]),
                        "predicted_issue_index": m["predicted_index"],
                        "score": round(float(m["score"]), 4),
                        "method": m["method"],
                    }
                    for m in matches
                ],
                "missed_gold_issues": [
                    {
                        "gold_issue_id": _gold_issue_id(gold_issues[i]),
                        "category": _category(gold_issues[i].get("category")),
                        "severity": _severity(gold_issues[i].get("severity")),
                        "claim_id": _claim_id(gold_issues[i]),
                        "description": _description(gold_issues[i]),
                    }
                    for i in range(len(gold_issues))
                    if i not in matched_g
                ],
                "unmatched_predicted_issues": [
                    {
                        "category": _category(pred_issues[i].get("category")),
                        "severity": _severity(pred_issues[i].get("severity")),
                        "claim_id": _claim_id(pred_issues[i]),
                        "description": _description(pred_issues[i]),
                    }
                    for i in range(len(pred_issues))
                    if i not in matched_p
                ],
            }
        )

    detection = _prf(
        total_matched,
        total_pred - total_matched,
        total_gold - total_matched,
    )

    per_category = {}
    for category in ISSUE_CATEGORIES:
        tp = category_tp[category]
        fp = category_pred[category] - tp
        fn = category_gold[category] - tp

        if tp or fp or fn:
            per_category[category] = _prf(tp, fp, fn)

    severity_recall = {
        sev: _r(_safe_div(severity_detected[sev], severity_gold[sev]))
        for sev in SEVERITIES
        if severity_gold[sev] > 0
    }

    tag_breakdown = {
        tag: {
            "gold_issue_count": tag_gold[tag],
            "detected_issue_count": tag_detected[tag],
            "recall": _r(_safe_div(tag_detected[tag], tag_gold[tag])),
        }
        for tag in sorted(tag_gold)
    }

    critical_recall = _safe_div(
        severity_detected["critical"],
        severity_gold["critical"],
    )
    high_critical_recall = _safe_div(
        severity_detected["critical"] + severity_detected["high"],
        severity_gold["critical"] + severity_gold["high"],
    )

    return {
        "gold_case_count": len(gold_case_ids),
        "prediction_case_count": len(pred_case_ids),
        "missing_case_count": len(missing_case_ids),
        "extra_case_count": len(extra_case_ids),
        "missing_case_ids": missing_case_ids,
        "extra_case_ids": extra_case_ids,

        "gold_issue_count": total_gold,
        "predicted_issue_count": total_pred,
        "matched_issue_count": total_matched,

        "issue_detection": detection,
        "false_alarm_rate": _r(
            _safe_div(total_pred - total_matched, total_pred)
        ),
        "escaped_error_rate": _r(
            _safe_div(total_gold - total_matched, total_gold)
        ),

        "category_accuracy": _r(_safe_div(exact_cat_ok, cat_total)),
        "acceptable_category_accuracy": _r(
            _safe_div(acceptable_cat_ok, cat_total)
        ),
        "category_evaluated_count": cat_total,
        "per_category": per_category,

        "severity_accuracy": _r(_safe_div(exact_sev_ok, sev_total)),
        "acceptable_severity_accuracy": _r(
            _safe_div(acceptable_sev_ok, sev_total)
        ),
        "severity_evaluated_count": sev_total,
        "severity_recall": severity_recall,
        "critical_issue_recall": _r(critical_recall),
        "high_or_critical_issue_recall": _r(high_critical_recall),

        "review_status_accuracy": _r(_safe_div(review_ok, review_total)),
        "review_status_evaluated_count": review_total,

        "issue_count_consistency_rate": _r(
            _safe_div(counts_ok, counts_total)
        ),
        "issue_count_consistency_evaluated_count": counts_total,

        "evidence_reference_validity_rate": _r(
            _safe_div(valid_evidence, evidence_total)
        ),
        "evidence_reference_count": evidence_total,

        "clean_claim_false_alarm_rate": _r(
            _safe_div(clean_claim_flagged, clean_claim_total)
        ),
        "clean_claim_evaluated_count": clean_claim_total,

        "case_tag_breakdown": tag_breakdown,
        "per_case": per_case,

        "interpretation_notes": {
            "false_positive": (
                "Benchmark-unmatched reviewer findings count as false positives "
                "for precision, but finite gold sets may omit legitimate issues."
            ),
            "review_status": (
                "Review completion is separate from issue detection quality."
            ),
            "release": (
                "No publish/release decision is evaluated because RedTeamAgent "
                "is not the release authority."
            ),
            "matching": (
                "Explicit related_gold_issue_id is preferred; structured "
                "similarity is an alignment fallback only."
            ),
        },
    }


def run_evaluation(
    gold_path: str | Path,
    predictions_path: str | Path | None = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Standard interface used by evaluation_runner.py."""
    if predictions_path is None:
        raise ValueError(
            "predictions_path is required for red-team evaluation."
        )

    minimum_issue_similarity = float(
        kwargs.pop("minimum_issue_similarity", 0.55)
    )

    if kwargs:
        raise TypeError(
            "Unknown red-team evaluation option(s): "
            + ", ".join(sorted(kwargs))
        )

    return evaluate_red_team_records(
        load_jsonl(gold_path),
        load_jsonl(predictions_path),
        minimum_issue_similarity=minimum_issue_similarity,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate RedTeamAgent predictions."
    )
    parser.add_argument("--gold", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument(
        "--minimum-issue-similarity",
        type=float,
        default=0.55,
    )
    parser.add_argument("--output", default="")

    args = parser.parse_args()

    result = run_evaluation(
        gold_path=args.gold,
        predictions_path=args.predictions,
        minimum_issue_similarity=args.minimum_issue_similarity,
    )

    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()