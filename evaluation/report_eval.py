"""
End-to-end report evaluator for the Event Intelligence research product.

Focus:
- final report contract completeness
- evidence-reference validity
- evidence-trail coverage
- hidden-insight/report discipline
- uncertainty/counterevidence/watch-item structure
- lexical repetition
- research-question fidelity

This intentionally does NOT replace grounding_eval.py, reasoning_eval.py, or
red_team_eval.py. Contract compliance is not release authorization.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set


DEFAULT_REQUIRED_SECTIONS = (
    "executive_synthesis",
    "event_landscape",
    "connections",
    "underlying_dynamics",
    "hidden_insights",
    "second_order_effects",
    "counterevidence_and_alternatives",
    "what_to_watch",
    "evidence_trail",
    "uncertainty",
)

PROHIBITED_LEGACY_SECTIONS = (
    "winners",
    "losers",
    "recommendations",
    "financial_flywheel",
    "moats_and_liabilities",
    "constrained_resource",
    "scarcity_type",
    "gatekeepers",
)

VALID_SUPPORT_LEVELS = {
    "strong",
    "moderate",
    "weak",
    "unknown",
}

ID_PATTERN = re.compile(r"\b[A-Z][A-Z0-9_]{1,20}-[A-Z0-9][A-Z0-9_.:-]*\b")


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _key(value: Any) -> str:
    text = _clean(value).casefold().replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "", text)
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


def _canonical_text(value: Any) -> str:
    text = _clean(value).casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _tokens(value: Any) -> Set[str]:
    return {t for t in _canonical_text(value).split() if len(t) > 1}


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    left, right = set(left), set(right)
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _safe_div(n: int | float, d: int | float) -> Optional[float]:
    return None if d == 0 else float(n) / float(d)


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 4)


def _normalize_id(value: Any) -> str:
    return _clean(value).upper()


def _normalize_id_list(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []

    out: List[str] = []
    seen: Set[str] = set()

    for item in value:
        if isinstance(item, Mapping):
            item = (
                item.get("id")
                or item.get("evidence_id")
                or item.get("source_id")
                or item.get("constraint_id")
                or ""
            )

        normalized = _normalize_id(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)

    return out


def _support_level(value: Any) -> str:
    normalized = _key(value)
    aliases = {
        "high": "strong",
        "medium": "moderate",
        "low": "weak",
        "insufficient": "unknown",
        "insufficient_evidence": "unknown",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in VALID_SUPPORT_LEVELS else ""


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    path = Path(path)
    rows: List[Dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue

            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_number} of {path}: {exc}"
                ) from exc

            if not isinstance(parsed, dict):
                raise ValueError(
                    f"Line {line_number} of {path} must contain a JSON object."
                )

            rows.append(parsed)

    return rows


def _case_id(record: Mapping[str, Any]) -> str:
    return (
        _clean(record.get("case_id"))
        or _clean(record.get("report_id"))
        or _clean(record.get("id"))
    )


def _index_cases(
    rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[str, Dict[str, Any]] = {}

    for index, row in enumerate(rows):
        case_id = _case_id(row)
        if not case_id:
            raise ValueError(f"Report record {index} is missing case_id/report_id.")
        if case_id in indexed:
            raise ValueError(f"Duplicate case_id: {case_id}")
        indexed[case_id] = dict(row)

    return indexed


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (str, Mapping)):
        return [value]
    return []


def _is_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(_clean(value))
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, (list, tuple, set)):
        return bool(value)
    return True


def _item_text(item: Any) -> str:
    if isinstance(item, str):
        return _clean(item)
    if not isinstance(item, Mapping):
        return ""

    for key in (
        "text",
        "statement",
        "claim",
        "description",
        "summary",
        "insight",
        "effect",
        "shift",
        "signal",
        "watch_signal",
        "alternative_explanation",
        "counterevidence",
        "what",
        "title",
    ):
        value = item.get(key)
        if isinstance(value, str) and _clean(value):
            return _clean(value)

    return ""


def _extract_ids(value: Any) -> Set[str]:
    result: Set[str] = set()

    if isinstance(value, str):
        result.update(ID_PATTERN.findall(value.upper()))
        return {_normalize_id(x) for x in result}

    if isinstance(value, Mapping):
        for nested in value.values():
            result.update(_extract_ids(nested))
        return result

    if isinstance(value, list):
        for nested in value:
            result.update(_extract_ids(nested))

    return result


def _item_evidence_ids(item: Any) -> Set[str]:
    if not isinstance(item, Mapping):
        return set()

    ids = set(
        _normalize_id_list(
            item.get("evidence_ids")
            or item.get("supporting_evidence_ids")
            or item.get("references")
            or []
        )
    )

    if ids:
        return ids

    for key in ("evidence", "supporting_evidence", "citations"):
        if key in item:
            ids.update(_extract_ids(item.get(key)))

    return ids


def _collect_reference_occurrences(
    value: Any,
    *,
    path: str = "report",
) -> List[tuple[str, str]]:
    occurrences: List[tuple[str, str]] = []

    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized_key = _key(key)
            nested_path = f"{path}.{key}"

            is_reference_field = (
                normalized_key.endswith("_ids")
                or "evidence" in normalized_key
                or "citation" in normalized_key
                or "reference" in normalized_key
                or normalized_key == "source_id"
            )

            if is_reference_field:
                for evidence_id in sorted(_extract_ids(nested)):
                    occurrences.append((nested_path, evidence_id))

            occurrences.extend(
                _collect_reference_occurrences(nested, path=nested_path)
            )

    elif isinstance(value, list):
        for index, nested in enumerate(value):
            occurrences.extend(
                _collect_reference_occurrences(
                    nested,
                    path=f"{path}[{index}]",
                )
            )

    return list(dict.fromkeys(occurrences))


def _has_mechanism(item: Any) -> bool:
    if not isinstance(item, Mapping):
        return False

    mechanism = item.get("mechanism")

    if isinstance(mechanism, Mapping):
        return bool(
            _item_text(mechanism)
            or _clean(mechanism.get("mechanism_type"))
        )

    return bool(
        _clean(mechanism)
        or _clean(item.get("mechanism_type"))
    )


def _has_counter_or_unknown(item: Any) -> bool:
    if not isinstance(item, Mapping):
        return False

    return bool(
        item.get("counterevidence_ids")
        or item.get("counter_signal_ids")
        or item.get("countersignal_ids")
        or item.get("counter_signals")
        or item.get("counterevidence")
        or item.get("critical_unknowns")
        or item.get("unknowns")
        or item.get("main_uncertainty")
    )


def _has_watch_condition(item: Any) -> bool:
    if not isinstance(item, Mapping):
        return False

    return bool(
        _clean(item.get("condition"))
        or _clean(item.get("signal"))
        or _clean(item.get("watch_signal"))
        or _clean(item.get("indicator"))
        or _clean(item.get("trigger"))
        or _clean(item.get("what_to_watch"))
    )


def _counter_items(report: Mapping[str, Any]) -> List[Any]:
    section = report.get("counterevidence_and_alternatives")

    if isinstance(section, list):
        return section

    if isinstance(section, Mapping):
        result: List[Any] = []

        for key in (
            "counterevidence",
            "counter_signals",
            "countersignals",
        ):
            result.extend(_as_list(section.get(key)))

        return result

    return []


def _required_sections(gold_case: Mapping[str, Any]) -> List[str]:
    value = gold_case.get("required_sections")

    if not isinstance(value, list):
        return list(DEFAULT_REQUIRED_SECTIONS)

    result = []
    for item in value:
        normalized = _key(item)
        if normalized and normalized not in result:
            result.append(normalized)

    return result or list(DEFAULT_REQUIRED_SECTIONS)


def _available_ids(gold_case: Mapping[str, Any]) -> Set[str]:
    return set(
        _normalize_id_list(
            gold_case.get("available_evidence_ids")
            or gold_case.get("valid_evidence_ids")
            or []
        )
    )


def _executive_contract(report: Mapping[str, Any]) -> Dict[str, Any]:
    executive = report.get("executive_synthesis")

    if not isinstance(executive, Mapping):
        return {
            "field_completeness": 0.0,
            "field_validity": {
                "what_is_happening": False,
                "what_it_may_mean": False,
                "evidence_ids": False,
                "support_level": False,
                "main_uncertainty": False,
            },
        }

    validity = {
        "what_is_happening": bool(_clean(executive.get("what_is_happening"))),
        "what_it_may_mean": bool(_clean(executive.get("what_it_may_mean"))),
        "evidence_ids": bool(_normalize_id_list(executive.get("evidence_ids"))),
        "support_level": bool(_support_level(executive.get("support_level"))),
        "main_uncertainty": bool(_clean(executive.get("main_uncertainty"))),
    }

    return {
        "field_completeness": _round(
            sum(int(v) for v in validity.values()) / len(validity)
        ),
        "field_validity": validity,
    }


def _underlying_contract(report: Mapping[str, Any]) -> Dict[str, Any]:
    underlying = report.get("underlying_dynamics")

    required_fields = (
        "mechanisms",
        "structural_shifts",
        "changing_incentives",
        "constraint_links",
    )

    if not isinstance(underlying, Mapping):
        return {
            "field_completeness": 0.0,
            "field_presence": {field: False for field in required_fields},
        }

    presence = {
        field: field in underlying and isinstance(underlying.get(field), list)
        for field in required_fields
    }

    return {
        "field_completeness": _round(
            sum(int(v) for v in presence.values()) / len(presence)
        ),
        "field_presence": presence,
    }


def _uncertainty_contract(report: Mapping[str, Any]) -> Dict[str, Any]:
    uncertainty = report.get("uncertainty")

    if not isinstance(uncertainty, Mapping):
        return {
            "field_completeness": 0.0,
            "critical_unknown_count": 0,
            "known_evidence_limit_count": 0,
        }

    critical = uncertainty.get("critical_unknowns")
    limits = uncertainty.get("known_evidence_limits")

    validity = {
        "critical_unknowns": isinstance(critical, list),
        "known_evidence_limits": isinstance(limits, list),
    }

    return {
        "field_completeness": _round(
            sum(int(v) for v in validity.values()) / len(validity)
        ),
        "critical_unknown_count": len(critical) if isinstance(critical, list) else 0,
        "known_evidence_limit_count": len(limits) if isinstance(limits, list) else 0,
    }


def _repetition_metrics(
    report: Mapping[str, Any],
    threshold: float,
) -> Dict[str, Any]:
    items: List[tuple[str, str]] = []

    def add(section: str, value: Any) -> None:
        for item in _as_list(value):
            text = _item_text(item)
            if text:
                items.append((section, text))

    add("event_landscape", report.get("event_landscape"))
    add("connections", report.get("connections"))

    underlying = report.get("underlying_dynamics")
    if isinstance(underlying, Mapping):
        for key in (
            "mechanisms",
            "structural_shifts",
            "changing_incentives",
            "constraint_links",
        ):
            add(f"underlying_dynamics.{key}", underlying.get(key))

    add("hidden_insights", report.get("hidden_insights"))
    add("second_order_effects", report.get("second_order_effects"))

    for item in _counter_items(report):
        text = _item_text(item)
        if text:
            items.append(("counterevidence_and_alternatives", text))

    add("what_to_watch", report.get("what_to_watch"))

    pairs = 0
    repeated_pairs = 0
    involved: Set[int] = set()

    for left in range(len(items)):
        for right in range(left + 1, len(items)):
            pairs += 1
            score = _jaccard(
                _tokens(items[left][1]),
                _tokens(items[right][1]),
            )

            if score >= threshold:
                repeated_pairs += 1
                involved.add(left)
                involved.add(right)

    return {
        "reasoning_item_count": len(items),
        "pair_count": pairs,
        "repeated_pair_count": repeated_pairs,
        "lexical_repeated_item_pair_rate": _round(
            _safe_div(repeated_pairs, pairs)
        ),
        "repeated_item_count": len(involved),
        "repeated_item_involvement_rate": _round(
            _safe_div(len(involved), len(items))
        ),
    }


def evaluate_report_records(
    gold_records: Sequence[Mapping[str, Any]],
    prediction_records: Sequence[Mapping[str, Any]],
    *,
    repetition_threshold: float = 0.75,
) -> Dict[str, Any]:
    gold_by_case = _index_cases(gold_records)
    pred_by_case = _index_cases(prediction_records)

    gold_ids = set(gold_by_case)
    pred_ids = set(pred_by_case)

    missing_case_ids = sorted(gold_ids - pred_ids)
    extra_case_ids = sorted(pred_ids - gold_ids)

    required_sections_total = 0
    required_sections_present = 0
    required_sections_nonempty = 0

    executive_scores: List[float] = []
    underlying_scores: List[float] = []
    uncertainty_scores: List[float] = []
    question_scores: List[float] = []

    evidence_refs_total = 0
    evidence_refs_valid = 0
    claims_with_refs = 0
    claims_all_refs_valid = 0

    referenced_unique: Set[str] = set()
    trail_unique: Set[str] = set()

    hidden_count = hidden_with_evidence = hidden_with_mechanism = 0
    hidden_with_support = hidden_with_counter_unknown = 0

    second_order_count = second_order_with_evidence = 0
    counter_count = counter_with_evidence = 0
    watch_count = watch_with_condition = watch_with_evidence = 0

    reports_with_prohibited = 0

    repetition_pairs = repetition_repeated_pairs = 0
    repetition_items = repetition_involved_items = 0

    requirement_cases = requirement_cases_satisfied = 0

    per_case: List[Dict[str, Any]] = []

    for case_id in sorted(gold_ids):
        gold = gold_by_case[case_id]
        report = pred_by_case.get(case_id, {})

        required_sections = _required_sections(gold)

        section_presence = {}
        section_nonempty = {}

        for section in required_sections:
            present = section in report
            nonempty = present and _is_nonempty(report.get(section))

            section_presence[section] = present
            section_nonempty[section] = nonempty

            required_sections_total += 1
            required_sections_present += int(present)
            required_sections_nonempty += int(nonempty)

        executive = _executive_contract(report)
        underlying = _underlying_contract(report)
        uncertainty = _uncertainty_contract(report)

        executive_scores.append(float(executive["field_completeness"]))
        underlying_scores.append(float(underlying["field_completeness"]))
        uncertainty_scores.append(float(uncertainty["field_completeness"]))

        gold_question = _clean(gold.get("research_question"))
        predicted_question = _clean(report.get("research_question"))

        question_score = None
        if gold_question:
            question_score = _jaccard(
                _tokens(gold_question),
                _tokens(predicted_question),
            )
            question_scores.append(question_score)

        allowed_ids = _available_ids(gold)
        occurrences = _collect_reference_occurrences(report)

        case_referenced_ids: Set[str] = set()
        invalid_occurrences = []

        for path, evidence_id in occurrences:
            evidence_refs_total += 1
            referenced_unique.add(evidence_id)
            case_referenced_ids.add(evidence_id)

            if not allowed_ids or evidence_id in allowed_ids:
                evidence_refs_valid += 1
            else:
                invalid_occurrences.append(
                    {"path": path, "evidence_id": evidence_id}
                )

        reasoning_items: List[Any] = []
        for key in (
            "event_landscape",
            "connections",
            "hidden_insights",
            "second_order_effects",
            "what_to_watch",
        ):
            reasoning_items.extend(_as_list(report.get(key)))

        if isinstance(report.get("underlying_dynamics"), Mapping):
            for key in (
                "mechanisms",
                "structural_shifts",
                "changing_incentives",
                "constraint_links",
            ):
                reasoning_items.extend(
                    _as_list(report["underlying_dynamics"].get(key))
                )

        reasoning_items.extend(_counter_items(report))

        for item in reasoning_items:
            ids = _item_evidence_ids(item)
            if not ids:
                continue

            claims_with_refs += 1
            if not allowed_ids or ids.issubset(allowed_ids):
                claims_all_refs_valid += 1

        case_trail_ids = _extract_ids(report.get("evidence_trail", {}))
        trail_unique.update(case_trail_ids)

        hidden_items = _as_list(report.get("hidden_insights"))
        for item in hidden_items:
            hidden_count += 1
            hidden_with_evidence += int(bool(_item_evidence_ids(item)))
            hidden_with_mechanism += int(_has_mechanism(item))
            hidden_with_counter_unknown += int(_has_counter_or_unknown(item))

            if isinstance(item, Mapping):
                hidden_with_support += int(
                    bool(_support_level(item.get("support_level")))
                )

        second_items = _as_list(report.get("second_order_effects"))
        for item in second_items:
            second_order_count += 1
            second_order_with_evidence += int(bool(_item_evidence_ids(item)))

        counter_items = _counter_items(report)
        for item in counter_items:
            counter_count += 1
            counter_with_evidence += int(bool(_item_evidence_ids(item)))

        watch_items = _as_list(report.get("what_to_watch"))
        for item in watch_items:
            watch_count += 1
            watch_with_condition += int(_has_watch_condition(item))
            watch_with_evidence += int(bool(_item_evidence_ids(item)))

        prohibited = [
            section
            for section in PROHIBITED_LEGACY_SECTIONS
            if section in report and _is_nonempty(report.get(section))
        ]
        reports_with_prohibited += int(bool(prohibited))

        repetition = _repetition_metrics(
            report,
            threshold=repetition_threshold,
        )
        repetition_pairs += repetition["pair_count"]
        repetition_repeated_pairs += repetition["repeated_pair_count"]
        repetition_items += repetition["reasoning_item_count"]
        repetition_involved_items += repetition["repeated_item_count"]

        requirements = (
            dict(gold.get("requirements"))
            if isinstance(gold.get("requirements"), Mapping)
            else {}
        )

        requirement_checks = {}

        if "counterevidence_required" in requirements:
            requirement_checks["counterevidence_required"] = (
                not bool(requirements["counterevidence_required"])
                or bool(counter_items)
            )

        if "uncertainty_required" in requirements:
            requirement_checks["uncertainty_required"] = (
                not bool(requirements["uncertainty_required"])
                or (
                    uncertainty["critical_unknown_count"] > 0
                    or uncertainty["known_evidence_limit_count"] > 0
                )
            )

        if "minimum_hidden_insights" in requirements:
            requirement_checks["minimum_hidden_insights"] = (
                len(hidden_items) >= int(requirements["minimum_hidden_insights"])
            )

        if "minimum_watch_items" in requirements:
            requirement_checks["minimum_watch_items"] = (
                len(watch_items) >= int(requirements["minimum_watch_items"])
            )

        if requirement_checks:
            requirement_cases += 1
            requirement_cases_satisfied += int(all(requirement_checks.values()))

        trail_coverage = _safe_div(
            len(case_referenced_ids & case_trail_ids),
            len(case_referenced_ids),
        )
        trail_orphan_rate = _safe_div(
            len(case_trail_ids - case_referenced_ids),
            len(case_trail_ids),
        )

        per_case.append(
            {
                "case_id": case_id,
                "research_question_lexical_fidelity": _round(question_score),
                "required_section_presence_rate": _round(
                    _safe_div(
                        sum(int(v) for v in section_presence.values()),
                        len(required_sections),
                    )
                ),
                "required_section_nonempty_rate": _round(
                    _safe_div(
                        sum(int(v) for v in section_nonempty.values()),
                        len(required_sections),
                    )
                ),
                "section_presence": section_presence,
                "section_nonempty": section_nonempty,
                "executive_synthesis_contract": executive,
                "underlying_dynamics_contract": underlying,
                "uncertainty_contract": uncertainty,
                "invalid_evidence_references": invalid_occurrences,
                "evidence_trail_reference_coverage": _round(trail_coverage),
                "evidence_trail_orphan_rate": _round(trail_orphan_rate),
                "hidden_insight_count": len(hidden_items),
                "counterevidence_item_count": len(counter_items),
                "watch_item_count": len(watch_items),
                "prohibited_legacy_sections": prohibited,
                "repetition": repetition,
                "requirement_checks": requirement_checks,
                "all_case_requirements_satisfied": (
                    all(requirement_checks.values())
                    if requirement_checks
                    else None
                ),
            }
        )

    return {
        "gold_case_count": len(gold_ids),
        "prediction_case_count": len(pred_ids),
        "missing_case_count": len(missing_case_ids),
        "extra_case_count": len(extra_case_ids),
        "missing_case_ids": missing_case_ids,
        "extra_case_ids": extra_case_ids,

        "required_section_presence_rate": _round(
            _safe_div(required_sections_present, required_sections_total)
        ),
        "required_section_nonempty_rate": _round(
            _safe_div(required_sections_nonempty, required_sections_total)
        ),

        "executive_synthesis_contract_completeness": _round(
            sum(executive_scores) / len(executive_scores)
            if executive_scores
            else None
        ),
        "underlying_dynamics_contract_completeness": _round(
            sum(underlying_scores) / len(underlying_scores)
            if underlying_scores
            else None
        ),
        "uncertainty_contract_completeness": _round(
            sum(uncertainty_scores) / len(uncertainty_scores)
            if uncertainty_scores
            else None
        ),

        "research_question_lexical_fidelity_mean": _round(
            sum(question_scores) / len(question_scores)
            if question_scores
            else None
        ),

        "evidence_reference_validity_rate": _round(
            _safe_div(evidence_refs_valid, evidence_refs_total)
        ),
        "evidence_reference_occurrence_count": evidence_refs_total,

        "claim_level_evidence_reference_validity_rate": _round(
            _safe_div(claims_all_refs_valid, claims_with_refs)
        ),

        "evidence_trail_reference_coverage": _round(
            _safe_div(
                len(referenced_unique & trail_unique),
                len(referenced_unique),
            )
        ),
        "evidence_trail_orphan_rate": _round(
            _safe_div(
                len(trail_unique - referenced_unique),
                len(trail_unique),
            )
        ),

        "hidden_insight_count": hidden_count,
        "hidden_insight_evidence_presence_rate": _round(
            _safe_div(hidden_with_evidence, hidden_count)
        ),
        "hidden_insight_mechanism_presence_rate": _round(
            _safe_div(hidden_with_mechanism, hidden_count)
        ),
        "hidden_insight_support_level_validity_rate": _round(
            _safe_div(hidden_with_support, hidden_count)
        ),
        "hidden_insight_counter_or_unknown_presence_rate": _round(
            _safe_div(hidden_with_counter_unknown, hidden_count)
        ),

        "second_order_effect_count": second_order_count,
        "second_order_effect_evidence_presence_rate": _round(
            _safe_div(second_order_with_evidence, second_order_count)
        ),

        "counterevidence_item_count": counter_count,
        "counterevidence_evidence_presence_rate": _round(
            _safe_div(counter_with_evidence, counter_count)
        ),

        "watch_item_count": watch_count,
        "watch_item_conditional_signal_presence_rate": _round(
            _safe_div(watch_with_condition, watch_count)
        ),
        "watch_item_evidence_presence_rate": _round(
            _safe_div(watch_with_evidence, watch_count)
        ),

        "report_with_prohibited_legacy_section_rate": _round(
            _safe_div(reports_with_prohibited, len(gold_ids))
        ),

        "lexical_repeated_item_pair_rate": _round(
            _safe_div(repetition_repeated_pairs, repetition_pairs)
        ),
        "repeated_item_involvement_rate": _round(
            _safe_div(repetition_involved_items, repetition_items)
        ),

        "case_requirement_satisfaction_rate": _round(
            _safe_div(requirement_cases_satisfied, requirement_cases)
        ),
        "case_requirement_evaluated_count": requirement_cases,

        "per_case": per_case,

        "interpretation_notes": {
            "evidence_reference_validity": (
                "Checks reference existence only; grounding_eval.py tests support."
            ),
            "repetition": (
                "Lexical overlap is a cheap repetition diagnostic, not semantic diversity."
            ),
            "hidden_insight_quality": (
                "Usefulness/novelty should come from labelled human evaluation."
            ),
            "release": (
                "Report-contract compliance does not authorize release."
            ),
        },
    }


def run_evaluation(
    gold_path: str | Path,
    predictions_path: str | Path | None = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    if predictions_path is None:
        raise ValueError(
            "predictions_path is required for report evaluation."
        )

    repetition_threshold = float(
        kwargs.pop("repetition_threshold", 0.75)
    )

    if not 0.0 <= repetition_threshold <= 1.0:
        raise ValueError(
            "repetition_threshold must be between 0 and 1."
        )

    if kwargs:
        raise TypeError(
            "Unknown report evaluation option(s): "
            + ", ".join(sorted(kwargs))
        )

    return evaluate_report_records(
        load_jsonl(gold_path),
        load_jsonl(predictions_path),
        repetition_threshold=repetition_threshold,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate final research reports."
    )
    parser.add_argument("--gold", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument(
        "--repetition-threshold",
        type=float,
        default=0.75,
    )
    parser.add_argument("--output", default="")

    args = parser.parse_args()

    result = run_evaluation(
        gold_path=args.gold,
        predictions_path=args.predictions,
        repetition_threshold=args.repetition_threshold,
    )

    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
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