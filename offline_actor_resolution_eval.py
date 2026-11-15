#!/usr/bin/env python3
"""
offline_actor_resolution_eval.py

Read-only A/B evaluation of ActorResolutionService V1 against the existing
38-event persisted corpus.

Recommended location:
    project root

Prerequisites:
    services/actor_resolution_service.py
    offline_corpus_integrity_diagnostic.py
    spaCy + selected English model

Recommended model:
    python -m spacy download en_core_web_trf

Example:
    .\\venv\\Scripts\\python.exe .\\offline_actor_resolution_eval.py ^
        --project-root . ^
        --report-id REPORT-FF59222B19F714AB8918D407 ^
        --topic "artificial intelligence" ^
        --model en_core_web_trf

What this changes:
    NOTHING.

No NewsAPI, no web retrieval, no DB writes, no ExtractionAgent modification,
no orchestrator modification, no LLM calls.

Evaluation contract
-------------------
For every persisted event emit:
- old actor;
- proposed actor;
- proposed role_status;
- proposed identity_status;
- actor gate result;
- old admission;
- proposed admission;
- relevance score;
- materiality score;
- topic linkage;
- old/new reasons.

The proposed admission gate is simulated as:
    role_status == action_subject
    AND identity_status resolved
    AND entity is institution-eligible
    AND existing deterministic Strategic Admission gates pass.

The current orchestrator file is NOT edited.

The report also prints a dedicated Insilico section so actor work can be compared
with the stable relevance/linkage bottleneck.
"""

from __future__ import annotations

import argparse
import copy
import csv
import importlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping


EVAL_VERSION = "offline-actor-resolution-eval-v1.0-two-axis"


def clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def import_project_module(root: Path, name: str):
    root_text = str(root.resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    return importlib.import_module(name)


def source_publisher(row: Mapping[str, Any]) -> str:
    keys = (
        "publisher",
        "source_name",
        "publication",
        "outlet",
        "provider",
        "news_source",
    )

    for key in keys:
        value = clean(row.get(key))
        if value:
            return value

    for nested_key in ("metadata", "source_metadata", "payload", "article", "data"):
        nested = row.get(nested_key)
        if not isinstance(nested, Mapping):
            continue
        for key in keys:
            value = clean(nested.get(key))
            if value:
                return value

    return ""


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


ACTOR_FIELDS = (
    "primary_actor",
    "acting_entity",
    "actor",
    "organization",
    "organisation",
    "company",
    "institution",
    "regulator",
)


def event_for_sidecar_admission(
    event: Mapping[str, Any],
    resolution: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Isolate proposed actor resolution from the old structured actor candidates.

    The current orchestrator normally searches old entity_mentions after actor fields.
    For an offline A/B of the sidecar gate, those old actor candidates must not leak
    into the proposed assessment.
    """
    proposed = copy.deepcopy(dict(event))

    for key in ACTOR_FIELDS:
        proposed.pop(key, None)

    proposed["entity_mentions"] = []

    if resolution.get("admission_actor_ready"):
        primary = resolution.get("primary_actor")
        if isinstance(primary, Mapping):
            canonical_name = clean(
                primary.get("canonical_name")
                or primary.get("surface_form")
            )
            if canonical_name:
                proposed["primary_actor"] = {
                    "name": canonical_name,
                    "role_status": primary.get("role_status"),
                    "identity_status": primary.get("identity_status"),
                    "resolution_basis": primary.get("resolution_basis"),
                }

    return proposed


def assess(orch_module, *, topic, event, evidence_items):
    cls = getattr(orch_module, "ResearchOrchestratorService")
    fn = getattr(cls, "_strategic_event_assessment")
    return dict(
        fn(
            topic=topic,
            event=event,
            evidence_items=evidence_items,
        )
        or {}
    )


def actor_summary(resolution: Mapping[str, Any]):
    primary = resolution.get("primary_actor")
    if isinstance(primary, Mapping):
        return (
            clean(primary.get("canonical_name") or primary.get("surface_form")),
            clean(primary.get("role_status")),
            clean(primary.get("identity_status")),
            clean(primary.get("entity_type")),
            clean(primary.get("resolution_basis")),
        )

    actor_candidates = [
        item
        for item in resolution.get("mentions", []) or []
        if isinstance(item, Mapping)
        and clean(item.get("role_status")) == "action_subject"
    ]

    if actor_candidates:
        first = actor_candidates[0]
        return (
            "",
            clean(first.get("role_status")),
            clean(first.get("identity_status")),
            clean(first.get("entity_type")),
            clean(first.get("resolution_basis")),
        )

    return "", "unresolved", "unresolved", "", ""


def print_rows(rows):
    print("\n" + "=" * 150)
    print("PER-EVENT A/B")
    print("=" * 150)

    header = (
        f"{'EVENT':<31} "
        f"{'OLD ACTOR':<22} "
        f"{'NEW ACTOR':<24} "
        f"{'ROLE':<14} "
        f"{'IDENTITY':<18} "
        f"{'OLD→NEW':<8} "
        f"{'REL':>4} "
        f"{'MAT':>4} "
        f"{'LINKAGE':<20}"
    )
    print(header)
    print("-" * len(header))

    for row in rows:
        old_new = f"{int(row['old_admit'])}→{int(row['proposed_admit'])}"
        print(
            f"{row['event_id']:<31} "
            f"{row['old_actor'][:22]:<22} "
            f"{row['new_actor'][:24]:<24} "
            f"{row['new_role_status'][:14]:<14} "
            f"{row['new_identity_status'][:18]:<18} "
            f"{old_new:<8} "
            f"{str(row['proposed_relevance_score']):>4} "
            f"{str(row['proposed_materiality_score']):>4} "
            f"{row['proposed_topic_linkage_type'][:20]:<20}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--topic", default="artificial intelligence")
    parser.add_argument("--model", default="en_core_web_trf")
    parser.add_argument(
        "--output-json",
        default="data/actor_resolution_eval.json",
    )
    parser.add_argument(
        "--output-csv",
        default="data/actor_resolution_eval.csv",
    )
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    report_id = clean(args.report_id).upper()
    topic = clean(args.topic)

    diag = import_project_module(root, "offline_corpus_integrity_diagnostic")
    orch_module = import_project_module(root, "services.research_orchestrator_service")
    actor_module = import_project_module(root, "services.actor_resolution_service")

    ActorResolutionService = getattr(actor_module, "ActorResolutionService")
    service = ActorResolutionService(model_name=args.model)

    print("OFFLINE ACTOR RESOLUTION EVAL")
    print(f"Version: {EVAL_VERSION}")
    print(f"Project: {root}")
    print(f"Report:  {report_id}")
    print(f"Topic:   {topic}")
    print(f"Model:   {args.model}")
    print("Mode:    READ-ONLY / NO NEWSAPI / NO WEB / NO LLM")
    print(f"Service: {actor_module.SERVICE_VERSION}")

    databases = diag.sqlite_candidates(root)
    report, report_lookup = diag.find_report(databases, report_id)
    if report is None:
        print("\nERROR: report not found.")
        return 2

    admission = diag.find_admission(report)
    if not admission:
        print("\nERROR: strategic admission diagnostics not found in report.")
        return 3

    event_ids, _, _ = diag.event_ids_from_admission(admission)
    event_rows, event_table_info = diag.locate_rows_by_id(
        databases,
        event_ids,
        "event_id",
    )

    source_ids = {
        clean(event.get("source_id")).upper()
        for event in event_rows.values()
        if clean(event.get("source_id"))
    }
    source_rows, source_table_info = diag.locate_rows_by_id(
        databases,
        source_ids,
        "source_id",
    )

    source_payloads = [
        diag.normalize_source_payload(source_id, source_rows[source_id])
        for source_id in sorted(source_ids)
        if source_id in source_rows
    ]

    evidence_records, evidence_diag = diag.rebuild_evidence_deterministically(
        root=root,
        topic=topic,
        events=[
            {
                k: v
                for k, v in event_rows[event_id].items()
                if not str(k).startswith("_diagnostic_")
            }
            for event_id in sorted(event_ids)
            if event_id in event_rows
        ],
        sources=source_payloads,
    )

    if not evidence_diag.get("success"):
        print(
            "\nERROR: deterministic evidence rebuild failed: "
            f"{evidence_diag.get('error')}"
        )
        return 4

    evid_by_event = evidence_by_event(evidence_records)

    rows = []
    old_reason_counts = Counter()
    proposed_reason_counts = Counter()
    actor_gate_reason_counts = Counter()

    for event_id in sorted(event_ids):
        persisted = event_rows.get(event_id)
        if not isinstance(persisted, Mapping):
            continue

        event = {
            k: v
            for k, v in persisted.items()
            if not str(k).startswith("_diagnostic_")
        }

        source_id = clean(event.get("source_id")).upper()
        source_row = source_rows.get(source_id, {})
        source_text = diag.source_text_from_row(source_row)
        publisher = source_publisher(source_row)
        anchor = diag.event_anchor_text(event)
        evidence_items = evid_by_event.get(event_id, [])

        old_assessment = assess(
            orch_module,
            topic=topic,
            event=event,
            evidence_items=evidence_items,
        )

        resolution = service.resolve_event(
            event_text=anchor,
            source_text=source_text,
            source_publisher=publisher,
        )

        proposed_event = event_for_sidecar_admission(event, resolution)

        proposed_base = assess(
            orch_module,
            topic=topic,
            event=proposed_event,
            evidence_items=evidence_items,
        )

        actor_ready = bool(resolution.get("admission_actor_ready"))
        proposed_admit = bool(
            actor_ready
            and proposed_base.get("admit")
        )

        proposed_reasons = list(proposed_base.get("reasons") or [])
        if not actor_ready:
            proposed_reasons = [
                reason
                for reason in proposed_reasons
                if reason != "specific_acting_institution_missing"
            ]
            for reason in resolution.get("actor_gate_failure_reasons", []) or []:
                reason = clean(reason)
                if reason and reason not in proposed_reasons:
                    proposed_reasons.append(reason)

        if actor_ready:
            proposed_reasons = [
                reason
                for reason in proposed_reasons
                if reason != "specific_acting_institution_missing"
            ]

        old_actor = clean(old_assessment.get("actor"))
        (
            new_actor,
            new_role,
            new_identity,
            new_entity_type,
            new_basis,
        ) = actor_summary(resolution)

        for reason in old_assessment.get("reasons", []) or []:
            old_reason_counts[clean(reason)] += 1
        for reason in proposed_reasons:
            proposed_reason_counts[clean(reason)] += 1
        for reason in resolution.get("actor_gate_failure_reasons", []) or []:
            actor_gate_reason_counts[clean(reason)] += 1

        rows.append(
            {
                "event_id": event_id,
                "source_id": source_id,
                "publisher": publisher,
                "title": clean(event.get("title")),
                "summary": clean(event.get("summary")),
                "event_anchor": anchor,
                "evidence_count": len(evidence_items),
                "old_actor": old_actor,
                "new_actor": new_actor,
                "new_role_status": new_role,
                "new_identity_status": new_identity,
                "new_entity_type": new_entity_type,
                "new_resolution_basis": new_basis,
                "actor_gate_ready": actor_ready,
                "actor_gate_failure_reasons": list(
                    resolution.get("actor_gate_failure_reasons") or []
                ),
                "requires_adjudication_v2": bool(
                    resolution.get("requires_adjudication")
                ),
                "old_admit": bool(old_assessment.get("admit")),
                "proposed_admit": proposed_admit,
                "admission_delta": (
                    f"{int(bool(old_assessment.get('admit')))}"
                    f"->{int(proposed_admit)}"
                ),
                "old_relevance_score": old_assessment.get("relevance_score"),
                "old_materiality_score": old_assessment.get("materiality_score"),
                "old_topic_linkage_type": clean(
                    old_assessment.get("topic_linkage_type")
                ),
                "old_reasons": list(old_assessment.get("reasons") or []),
                "proposed_relevance_score": proposed_base.get("relevance_score"),
                "proposed_materiality_score": proposed_base.get("materiality_score"),
                "proposed_topic_linkage_type": clean(
                    proposed_base.get("topic_linkage_type")
                ),
                "proposed_reasons": proposed_reasons,
                "resolution": resolution,
            }
        )

    print_rows(rows)

    old_admitted = sum(1 for row in rows if row["old_admit"])
    proposed_admitted = sum(1 for row in rows if row["proposed_admit"])
    actor_ready_count = sum(1 for row in rows if row["actor_gate_ready"])
    role_resolved_identity_unresolved = sum(
        1
        for row in rows
        if row["new_role_status"] == "action_subject"
        and row["new_identity_status"] == "unresolved"
    )
    v2_residue = sum(
        1 for row in rows if row["requires_adjudication_v2"]
    )

    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)
    print(f"Events evaluated:                         {len(rows)}")
    print(f"Old admitted:                             {old_admitted}")
    print(f"Proposed admitted:                        {proposed_admitted}")
    print(f"Actor gate ready:                         {actor_ready_count}")
    print(
        "Role resolved / identity unresolved:      "
        f"{role_resolved_identity_unresolved}"
    )
    print(f"Potential V2 adjudication residue:        {v2_residue}")
    print(f"Old reason counts:                        {dict(old_reason_counts)}")
    print(f"Proposed reason counts:                   {dict(proposed_reason_counts)}")
    print(f"Actor-gate reason counts:                 {dict(actor_gate_reason_counts)}")

    print("\n" + "=" * 100)
    print("INSILICO / RELEVANCE-LINKAGE CHECK")
    print("=" * 100)

    insilico_rows = [
        row
        for row in rows
        if "insilico" in (
            f"{row['event_anchor']} "
            f"{diag.source_text_from_row(source_rows.get(row['source_id'], {}))}"
        ).casefold()
    ]

    for row in insilico_rows:
        print(
            f"{row['event_id']} | "
            f"actor={row['new_actor'] or '<unresolved>'} | "
            f"role={row['new_role_status']} | "
            f"identity={row['new_identity_status']} | "
            f"rel={row['proposed_relevance_score']} | "
            f"mat={row['proposed_materiality_score']} | "
            f"link={row['proposed_topic_linkage_type']} | "
            f"admit={row['proposed_admit']} | "
            f"reasons={row['proposed_reasons']}"
        )

    print(
        "\nAcceptance criterion: correctness, not admission count. "
        "A lower proposed admission count is acceptable if false actors disappear."
    )

    output_json = (root / args.output_json).resolve()
    output_csv = (root / args.output_csv).resolve()
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "eval_version": EVAL_VERSION,
        "read_only": True,
        "report_id": report_id,
        "topic": topic,
        "model": service.model_metadata(),
        "report_lookup": report_lookup,
        "event_tables": event_table_info,
        "source_tables": source_table_info,
        "evidence_rebuild": evidence_diag,
        "summary": {
            "event_count": len(rows),
            "old_admitted": old_admitted,
            "proposed_admitted": proposed_admitted,
            "actor_gate_ready_count": actor_ready_count,
            "role_resolved_identity_unresolved_count": role_resolved_identity_unresolved,
            "potential_v2_adjudication_residue_count": v2_residue,
            "old_reason_counts": dict(old_reason_counts),
            "proposed_reason_counts": dict(proposed_reason_counts),
            "actor_gate_reason_counts": dict(actor_gate_reason_counts),
        },
        "events": rows,
        "insilico_event_ids": [row["event_id"] for row in insilico_rows],
        "acceptance_criterion": (
            "Correct actors admitted and noise rejected; raw admitted-event count "
            "is not the success metric."
        ),
    }

    output_json.write_text(
        json.dumps(
            diag.json_safe(payload),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    csv_fields = [
        "event_id",
        "source_id",
        "old_actor",
        "new_actor",
        "new_role_status",
        "new_identity_status",
        "new_entity_type",
        "actor_gate_ready",
        "requires_adjudication_v2",
        "old_admit",
        "proposed_admit",
        "old_relevance_score",
        "proposed_relevance_score",
        "old_materiality_score",
        "proposed_materiality_score",
        "old_topic_linkage_type",
        "proposed_topic_linkage_type",
        "old_reasons",
        "proposed_reasons",
        "actor_gate_failure_reasons",
        "summary",
    ]

    with output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: (
                        json.dumps(row[key], ensure_ascii=False)
                        if isinstance(row.get(key), (list, dict))
                        else row.get(key, "")
                    )
                    for key in csv_fields
                }
            )

    print(f"\nJSON: {output_json}")
    print(f"CSV:  {output_csv}")
    print("No production code or persisted data was modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
