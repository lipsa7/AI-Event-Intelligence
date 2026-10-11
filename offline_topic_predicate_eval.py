#!/usr/bin/env python3
"""
offline_topic_predicate_eval.py

Read-only isolation A/B of the CURRENT orchestrator topic predicates versus the
shared StrategicTopicSignalService deterministic baseline.

IMPORTANT EXPERIMENTAL CONTRACT
-------------------------------
This harness changes ONLY topic evidence/relevance/linkage.

It DOES NOT reimplement materiality.  It calls the current orchestrator's
_strategic_event_assessment() once and reuses its:
- materiality_score;
- actor result;
- non-topic exclusion reasons.

The counterfactual event-admission verdict is therefore:

    new_topic_gate_pass
    AND no CURRENT non-topic blockers

where current non-topic blockers are current assessment reasons after removing:
- topic_relevance_below_threshold
- topic_linkage_not_strategic:*

This is intentionally an EVENT-ADMISSION counterfactual only.  It excludes
later evidence-sufficiency / StrategicInference / grounding / publishability
gates.  In particular, the StrategicInference two-support-record gate is
candidate/insight-level, not a per-event gate.

NO NewsAPI.
NO web.
NO LLM.
NO DB writes.
NO orchestrator modification.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence


EVAL_VERSION = (
    "offline-topic-predicate-eval-"
    "v1.1.3-isolated-shared-evidence-baseline-service-guard-verified"
)

EXPECTED_SIGNAL_SERVICE_VERSION_FRAGMENT = (
    "v1.1.3-shared-evidence-baseline-regex-precision-verified"
)

ELIGIBLE_LINKAGES = {
    "direct",
    "material_enabler",
    "material_constraint",
    "material_consequence",
}

TOPIC_REASON_PREFIXES = (
    "topic_relevance_below_threshold",
    "topic_linkage_not_strategic:",
)


def clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def import_project_module(root: Path, name: str):
    root_text = str(root.resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    return importlib.import_module(name)


def evidence_by_event(records):
    output = defaultdict(list)
    for record in records:
        if not isinstance(record, Mapping):
            continue
        event_id = clean(
            record.get("event_id")
            or record.get("linked_event_id")
            or record.get("canonical_event_id")
        ).upper()
        if event_id:
            output[event_id].append(dict(record))
    return output


def current_assessment(
    orch_cls,
    *,
    topic: str,
    event: Mapping[str, Any],
    evidence_items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return dict(
        orch_cls._strategic_event_assessment(
            topic=topic,
            event=event,
            evidence_items=evidence_items,
        )
        or {}
    )


def is_topic_reason(reason: str) -> bool:
    value = clean(reason)
    return any(
        value == prefix or value.startswith(prefix)
        for prefix in TOPIC_REASON_PREFIXES
    )


def non_topic_reasons(current: Mapping[str, Any]) -> list[str]:
    return [
        clean(reason)
        for reason in current.get("reasons", []) or []
        if clean(reason) and not is_topic_reason(clean(reason))
    ]


def shared_topic_assessment(
    orch_cls,
    signal_cls,
    *,
    topic: str,
    event: Mapping[str, Any],
    evidence_items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Change only topic facts/policy; hold all non-topic outputs constant."""
    current = current_assessment(
        orch_cls,
        topic=topic,
        event=event,
        evidence_items=evidence_items,
    )

    text = orch_cls._strategic_event_text(event, evidence_items)
    family = orch_cls._coarse_event_family(event)

    shared = signal_cls.assess_shared_baseline(
        topic=topic,
        text=text,
        event_family=family,
    )

    blockers = non_topic_reasons(current)
    counterfactual_admit = bool(
        shared["topic_gate_pass"] and not blockers
    )

    return {
        "counterfactual_event_admit": counterfactual_admit,
        "topic_gate_pass": bool(shared["topic_gate_pass"]),
        "relevance_score": float(shared["relevance_score"] or 0.0),
        "topic_linkage_type": clean(shared["topic_linkage_type"]),
        # IMPORTANT: not recomputed.
        "materiality_score": current.get("materiality_score"),
        "actor": clean(current.get("actor")),
        "event_class_current": clean(current.get("event_class")),
        "current_non_topic_reasons": blockers,
        "invariant_violation": bool(
            shared.get("invariant_violation")
        ),
        "invariant_violations": list(
            shared.get("invariant_violations") or []
        ),
        "signal_bundle": shared.get("signal_bundle") or {},
    }


def load_gold(path_text: str) -> dict[str, dict[str, str]]:
    if not path_text:
        return {}
    path = Path(path_text).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return {
            clean(row.get("event_id")).upper(): dict(row)
            for row in reader
            if clean(row.get("event_id"))
        }


def binary_metrics(
    pairs: Sequence[tuple[bool, bool]],
) -> dict[str, Any]:
    tp = sum(pred and gold for pred, gold in pairs)
    fp = sum(pred and not gold for pred, gold in pairs)
    fn = sum((not pred) and gold for pred, gold in pairs)
    tn = sum((not pred) and (not gold) for pred, gold in pairs)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall)
        else 0.0
    )
    accuracy = (tp + tn) / len(pairs) if pairs else 0.0

    return {
        "n": len(pairs),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "accuracy": round(accuracy, 4),
    }


def print_rows(rows):
    print("\n" + "=" * 170)
    print("TOPIC-PREDICATE ISOLATION A/B")
    print("=" * 170)

    header = (
        f"{'EVENT':<31} "
        f"{'OLD REL':>7} "
        f"{'NEW REL':>7} "
        f"{'OLD LINK':<20} "
        f"{'NEW LINK':<20} "
        f"{'OLD TG':<6} "
        f"{'NEW TG':<6} "
        f"{'OLD ADM':<7} "
        f"{'CF ADM':<7} "
        f"{'MAT':>5} "
        f"{'NON-TOPIC BLOCKERS'}"
    )
    print(header)
    print("-" * len(header))

    for row in rows:
        blockers = ",".join(row["current_non_topic_reasons"])
        print(
            f"{row['event_id']:<31} "
            f"{str(row['old_relevance_score']):>7} "
            f"{str(row['new_relevance_score']):>7} "
            f"{row['old_topic_linkage_type'][:20]:<20} "
            f"{row['new_topic_linkage_type'][:20]:<20} "
            f"{str(row['old_topic_gate_pass']):<6} "
            f"{str(row['new_topic_gate_pass']):<6} "
            f"{str(row['old_admit']):<7} "
            f"{str(row['counterfactual_event_admit']):<7} "
            f"{str(row['materiality_score']):>5} "
            f"{blockers}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--topic", default="artificial intelligence")
    parser.add_argument("--gold-csv", default="")
    parser.add_argument(
        "--output-json",
        default="data/topic_predicate_eval.json",
    )
    parser.add_argument(
        "--output-csv",
        default="data/topic_predicate_eval.csv",
    )
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    report_id = clean(args.report_id).upper()
    topic = clean(args.topic)

    diag = import_project_module(root, "offline_corpus_integrity_diagnostic")
    orch_module = import_project_module(root, "services.research_orchestrator_service")
    signal_module = import_project_module(
        root,
        "services.strategic_topic_signal_service",
    )

    orch_cls = getattr(orch_module, "ResearchOrchestratorService")
    signal_cls = getattr(signal_module, "StrategicTopicSignalService")

    actual_signal_version = clean(
        getattr(signal_module, "SERVICE_VERSION", "")
    )
    if EXPECTED_SIGNAL_SERVICE_VERSION_FRAGMENT not in actual_signal_version:
        print(
            "ERROR: signal service version mismatch: "
            f"actual={actual_signal_version!r}, "
            f"expected fragment={EXPECTED_SIGNAL_SERVICE_VERSION_FRAGMENT!r}",
            file=sys.stderr,
        )
        return 5

    print("OFFLINE TOPIC PREDICATE ISOLATION EVAL")
    print(f"Version:      {EVAL_VERSION}")
    print(f"Project:      {root}")
    print(f"Report:       {report_id}")
    print(f"Topic:        {topic}")
    print(f"Orchestrator: {getattr(orch_module, 'SERVICE_VERSION', '')}")
    print(f"Signal svc:   {getattr(signal_module, 'SERVICE_VERSION', '')}")
    print("Mode:         READ-ONLY / NO NEWSAPI / NO WEB / NO LLM / NO DB WRITES")
    print("Isolation:    MATERIALITY + ACTOR + NON-TOPIC BLOCKERS HELD CURRENT")
    print("Scope:        EVENT ADMISSION ONLY; DOWNSTREAM EVIDENCE/INFERENCE GATES EXCLUDED")

    databases = diag.sqlite_candidates(root)
    report, _ = diag.find_report(databases, report_id)
    if report is None:
        print("ERROR: report not found.", file=sys.stderr)
        return 2

    admission = diag.find_admission(report)
    if not admission:
        print("ERROR: strategic admission diagnostics not found.", file=sys.stderr)
        return 3

    event_ids, _, _ = diag.event_ids_from_admission(admission)
    event_rows, _ = diag.locate_rows_by_id(databases, event_ids, "event_id")

    source_ids = {
        clean(event.get("source_id")).upper()
        for event in event_rows.values()
        if clean(event.get("source_id"))
    }
    source_rows, _ = diag.locate_rows_by_id(databases, source_ids, "source_id")
    source_payloads = [
        diag.normalize_source_payload(source_id, source_rows[source_id])
        for source_id in sorted(source_ids)
        if source_id in source_rows
    ]

    events = [
        {
            key: value
            for key, value in event_rows[event_id].items()
            if not str(key).startswith("_diagnostic_")
        }
        for event_id in sorted(event_ids)
        if event_id in event_rows
    ]

    evidence_records, evidence_diag = diag.rebuild_evidence_deterministically(
        root=root,
        topic=topic,
        events=events,
        sources=source_payloads,
    )
    if not evidence_diag.get("success"):
        print(
            "ERROR: deterministic evidence rebuild failed: "
            f"{evidence_diag.get('error')}",
            file=sys.stderr,
        )
        return 4

    evid_by_event = evidence_by_event(evidence_records)
    gold = load_gold(args.gold_csv)

    rows = []
    old_reason_counts = Counter()
    non_topic_reason_counts = Counter()
    old_contradictions = 0
    new_contradictions = 0
    contextual_to_none_count = 0

    for event_id in sorted(event_ids):
        persisted = event_rows.get(event_id)
        if not isinstance(persisted, Mapping):
            continue

        event = {
            key: value
            for key, value in persisted.items()
            if not str(key).startswith("_diagnostic_")
        }
        evidence_items = evid_by_event.get(event_id, [])

        old = current_assessment(
            orch_cls,
            topic=topic,
            event=event,
            evidence_items=evidence_items,
        )
        new = shared_topic_assessment(
            orch_cls,
            signal_cls,
            topic=topic,
            event=event,
            evidence_items=evidence_items,
        )

        old_linkage = clean(old.get("topic_linkage_type"))
        old_relevance = float(old.get("relevance_score") or 0.0)
        old_topic_gate = bool(
            old_relevance >= 2.0 and old_linkage in ELIGIBLE_LINKAGES
        )

        new_linkage = clean(new.get("topic_linkage_type"))
        new_relevance = float(new.get("relevance_score") or 0.0)

        if old_relevance < 2.0 and old_linkage in ELIGIBLE_LINKAGES:
            old_contradictions += 1
        if new["invariant_violation"]:
            new_contradictions += 1

        if old_linkage == "contextual" and new_linkage == "none":
            contextual_to_none_count += 1

        for reason in old.get("reasons", []) or []:
            old_reason_counts[clean(reason)] += 1
        for reason in new["current_non_topic_reasons"]:
            non_topic_reason_counts[reason] += 1

        gold_row = gold.get(event_id, {})
        gold_material_ai = None
        gold_should_admit = None
        if gold_row:
            gold_material_ai = bool(
                clean(gold_row.get("article_topic"))
                in {"AI_PRIMARY", "AI_MATERIAL"}
                and clean(gold_row.get("event_ai_linkage"))
                in {"DIRECT_MATERIAL", "INDIRECT_MATERIAL"}
            )
            gold_should_admit = clean(gold_row.get("should_admit")) == "YES"

        rows.append(
            {
                "event_id": event_id,
                "source_id": clean(event.get("source_id")).upper(),
                "event_text": diag.event_anchor_text(event),
                "evidence_count": len(evidence_items),
                "actor": new["actor"],
                "materiality_score": new["materiality_score"],
                "old_relevance_score": old_relevance,
                "new_relevance_score": new_relevance,
                "old_topic_linkage_type": old_linkage,
                "new_topic_linkage_type": new_linkage,
                "old_topic_gate_pass": old_topic_gate,
                "new_topic_gate_pass": new["topic_gate_pass"],
                "old_admit": bool(old.get("admit")),
                "counterfactual_event_admit": new["counterfactual_event_admit"],
                "current_non_topic_reasons": new["current_non_topic_reasons"],
                "old_reasons": list(old.get("reasons") or []),
                "signal_bundle": new["signal_bundle"],
                "gold_material_ai": gold_material_ai,
                "gold_should_admit": gold_should_admit,
            }
        )

    print_rows(rows)

    old_topic_count = sum(row["old_topic_gate_pass"] for row in rows)
    new_topic_count = sum(row["new_topic_gate_pass"] for row in rows)
    old_admitted = sum(row["old_admit"] for row in rows)
    cf_admitted = sum(row["counterfactual_event_admit"] for row in rows)

    summary: dict[str, Any] = {
        "eval_version": EVAL_VERSION,
        "orchestrator_version": getattr(orch_module, "SERVICE_VERSION", ""),
        "signal_service_version": getattr(signal_module, "SERVICE_VERSION", ""),
        "report_id": report_id,
        "topic": topic,
        "event_count": len(rows),
        "old_topic_gate_pass_count": old_topic_count,
        "new_topic_gate_pass_count": new_topic_count,
        "old_event_admitted_count": old_admitted,
        "counterfactual_event_admitted_count": cf_admitted,
        "old_contradiction_count": old_contradictions,
        "new_contradiction_count": new_contradictions,
        "old_contextual_to_new_none_count": contextual_to_none_count,
        "old_reason_counts": dict(old_reason_counts),
        "current_non_topic_reason_counts": dict(non_topic_reason_counts),
        "experimental_contract": {
            "changed": ["topic signal extraction", "relevance", "topic linkage"],
            "held_constant_from_current_orchestrator": [
                "materiality_score",
                "actor",
                "non-topic exclusion reasons",
            ],
            "excluded_from_counterfactual": [
                "candidate-level evidence sufficiency",
                "StrategicInference support-record gate",
                "grounding",
                "red-team",
                "publishability/release gates",
            ],
            "warning": (
                "counterfactual_event_admit is not a prediction that production "
                "will publish an insight"
            ),
        },
    }

    if gold:
        topic_pairs_old = [
            (row["old_topic_gate_pass"], bool(row["gold_material_ai"]))
            for row in rows
            if row["gold_material_ai"] is not None
        ]
        topic_pairs_new = [
            (row["new_topic_gate_pass"], bool(row["gold_material_ai"]))
            for row in rows
            if row["gold_material_ai"] is not None
        ]
        admission_pairs_old = [
            (row["old_admit"], bool(row["gold_should_admit"]))
            for row in rows
            if row["gold_should_admit"] is not None
        ]
        admission_pairs_cf = [
            (row["counterfactual_event_admit"], bool(row["gold_should_admit"]))
            for row in rows
            if row["gold_should_admit"] is not None
        ]

        summary["gold_metrics"] = {
            "old_topic_gate": binary_metrics(topic_pairs_old),
            "new_topic_gate": binary_metrics(topic_pairs_new),
            "old_event_admission": binary_metrics(admission_pairs_old),
            "counterfactual_event_admission": binary_metrics(admission_pairs_cf),
            "note": (
                "Event-admission metrics hold CURRENT actor/materiality/non-topic "
                "blockers constant. They EXCLUDE downstream evidence sufficiency, "
                "StrategicInference, grounding, red-team and release gates."
            ),
        }

    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)
    print(f"Events:                              {len(rows)}")
    print(f"Old topic-gate pass:                 {old_topic_count}")
    print(f"New topic-gate pass:                 {new_topic_count}")
    print(f"Old event admitted:                  {old_admitted}")
    print(f"Counterfactual event admitted:       {cf_admitted}")
    print(f"Old rel/link contradictions:         {old_contradictions}")
    print(f"New rel/link contradictions:         {new_contradictions}")
    print(f"Old contextual -> new none:          {contextual_to_none_count}")
    print(f"Current non-topic reason counts:     {dict(non_topic_reason_counts)}")
    print("NOTE: counterfactual admission excludes downstream evidence/inference/release gates.")

    if "gold_metrics" in summary:
        print(f"Gold metrics:                         {summary['gold_metrics']}")

    output_json = (root / args.output_json).resolve()
    output_csv = (root / args.output_csv).resolve()
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    output_json.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    csv_columns = [
        "event_id",
        "source_id",
        "event_text",
        "evidence_count",
        "actor",
        "materiality_score",
        "old_relevance_score",
        "new_relevance_score",
        "old_topic_linkage_type",
        "new_topic_linkage_type",
        "old_topic_gate_pass",
        "new_topic_gate_pass",
        "old_admit",
        "counterfactual_event_admit",
        "gold_material_ai",
        "gold_should_admit",
        "old_reasons",
        "current_non_topic_reasons",
    ]

    with output_csv.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=csv_columns,
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            flat["old_reasons"] = json.dumps(row["old_reasons"], ensure_ascii=False)
            flat["current_non_topic_reasons"] = json.dumps(
                row["current_non_topic_reasons"],
                ensure_ascii=False,
            )
            writer.writerow(flat)

    print(f"JSON: {output_json}")
    print(f"CSV:  {output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
