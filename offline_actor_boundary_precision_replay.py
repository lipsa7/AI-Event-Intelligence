#!/usr/bin/env python3
"""
offline_actor_recall_replay.py

READ-ONLY counterfactual replay for the V5.3.3 recall + actor-boundary precision extractor fix.

Purpose
-------
Use the already persisted corpus for one report and answer:

    If we regenerate ONLY entity_mentions from each persisted event anchor with
    the current ExtractionAgent._deep_anchor_entity_mentions_from_text(), while
    leaving every other event/evidence/admission rule unchanged, how does
    Strategic Event Admission change?

This is deliberately narrower than full re-extraction. It isolates the causal
effect of the entity-candidate recall change.

It does NOT:
- call NewsAPI;
- fetch the web;
- write to SQLite;
- run Streamlit;
- change Strategic Admission;
- change StrategicInference thresholds;
- tighten the old `unknown`-actor precision behavior;
- resolve "The Company" from source context.

Prerequisite
------------
Place offline_corpus_integrity_diagnostic.py at the project root.
Replace agents/extraction_agent.py with the V5.3.3 recall + actor-boundary precision build before run.

Example
-------
    .\\venv\\Scripts\\python.exe .\\offline_actor_recall_replay.py ^
        --project-root . ^
        --report-id REPORT-FF59222B19F714AB8918D407 ^
        --topic "artificial intelligence"

Output
------
    data/actor_recall_replay.json
"""

from __future__ import annotations

import argparse
import copy
import importlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping


REPLAY_VERSION = "offline-actor-boundary-precision-replay-v1.2"


def clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def load_diag(root: Path):
    root_text = str(root.resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    return importlib.import_module("offline_corpus_integrity_diagnostic")


def load_extractor(root: Path):
    root_text = str(root.resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    module = importlib.import_module("agents.extraction_agent")
    return module, getattr(module, "ExtractionAgent")


def load_orchestrator(root: Path):
    root_text = str(root.resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    module = importlib.import_module("services.research_orchestrator_service")
    return module


def get_actor(orch_module: Any, event: Mapping[str, Any]) -> str:
    cls = getattr(orch_module, "ResearchOrchestratorService")
    fn = getattr(cls, "_event_primary_institution")
    return clean(fn(event))


def assess(
    orch_module: Any,
    *,
    topic: str,
    event: Mapping[str, Any],
    evidence_items: list[dict[str, Any]],
) -> dict[str, Any]:
    cls = getattr(orch_module, "ResearchOrchestratorService")
    fn = getattr(cls, "_strategic_event_assessment")
    result = fn(
        topic=topic,
        event=event,
        evidence_items=evidence_items,
    )
    return dict(result or {})


def mention_names(event: Mapping[str, Any]) -> list[str]:
    raw = event.get("entity_mentions")
    if not isinstance(raw, list):
        return []
    output = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        name = clean(item.get("mention") or item.get("name"))
        if name and name not in output:
            output.append(name)
    return output


def evidence_by_event(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--topic", default="artificial intelligence")
    parser.add_argument("--output", default="data/actor_recall_replay.json")
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    report_id = clean(args.report_id).upper()
    topic = clean(args.topic)

    diag = load_diag(root)
    extractor_module, ExtractionAgent = load_extractor(root)
    orch_module = load_orchestrator(root)

    agent_version = clean(
        getattr(extractor_module, "AGENT_VERSION", "")
        or getattr(ExtractionAgent, "AGENT_VERSION", "")
    )
    build_id = clean(
        getattr(extractor_module, "PATCH_BUILD_ID", "")
        or getattr(ExtractionAgent, "PATCH_BUILD_ID", "")
    )

    print("OFFLINE ACTOR RECALL REPLAY")
    print(f"Version:   {REPLAY_VERSION}")
    print(f"Extractor: {agent_version}")
    print(f"Build:     {build_id}")
    print(f"Report:    {report_id}")
    print(f"Topic:     {topic}")
    print("Mode:      READ-ONLY / NO NEWSAPI / NO WEB")

    if "v5.3.3-deep-actor-recall-boundary-precision" not in agent_version:
        print(
            "\nERROR: agents/extraction_agent.py is not the expected "
            "V5.3.3 recall + actor-boundary precision build."
        )
        return 2

    databases = diag.sqlite_candidates(root)
    report, report_lookup = diag.find_report(databases, report_id)
    if report is None:
        print("\nERROR: report not found.")
        return 3

    admission = diag.find_admission(report)
    if not admission:
        print("\nERROR: strategic admission diagnostics not found in report.")
        return 4

    event_ids, actor_missing_ids, considered_ids = diag.event_ids_from_admission(admission)
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

    rebuilt_evidence, evidence_diag = diag.rebuild_evidence_deterministically(
        root=root,
        topic=topic,
        events=[
            {
                key: value
                for key, value in event_rows[event_id].items()
                if not str(key).startswith("_diagnostic_")
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
        return 5

    by_event = evidence_by_event(rebuilt_evidence)

    before_reason_counts = Counter()
    after_reason_counts = Counter()
    before_admitted = 0
    after_admitted = 0
    actor_recovered = 0
    events_changed = 0
    rows = []

    for event_id in sorted(event_ids):
        persisted = event_rows.get(event_id)
        if not isinstance(persisted, Mapping):
            continue

        before_event = {
            key: value
            for key, value in persisted.items()
            if not str(key).startswith("_diagnostic_")
        }
        after_event = copy.deepcopy(before_event)

        anchor = diag.event_anchor_text(before_event)
        regenerated_mentions = ExtractionAgent._deep_anchor_entity_mentions_from_text(
            anchor
        )
        after_event["entity_mentions"] = regenerated_mentions

        evidence_items = by_event.get(event_id, [])

        before_actor = get_actor(orch_module, before_event)
        after_actor = get_actor(orch_module, after_event)

        before_assessment = assess(
            orch_module,
            topic=topic,
            event=before_event,
            evidence_items=evidence_items,
        )
        after_assessment = assess(
            orch_module,
            topic=topic,
            event=after_event,
            evidence_items=evidence_items,
        )

        before_ok = bool(before_assessment.get("admit"))
        after_ok = bool(after_assessment.get("admit"))

        if before_ok:
            before_admitted += 1
        if after_ok:
            after_admitted += 1

        for reason in before_assessment.get("reasons", []) or []:
            before_reason_counts[clean(reason)] += 1
        for reason in after_assessment.get("reasons", []) or []:
            after_reason_counts[clean(reason)] += 1

        if not before_actor and after_actor:
            actor_recovered += 1

        if (
            before_actor != after_actor
            or before_ok != after_ok
            or list(before_assessment.get("reasons") or [])
            != list(after_assessment.get("reasons") or [])
            or mention_names(before_event) != mention_names(after_event)
        ):
            events_changed += 1

        rows.append(
            {
                "event_id": event_id,
                "source_id": clean(before_event.get("source_id")),
                "title": clean(before_event.get("title")),
                "summary": clean(before_event.get("summary")),
                "evidence_count": len(evidence_items),
                "before": {
                    "actor": before_actor,
                    "admit": before_ok,
                    "reasons": list(before_assessment.get("reasons") or []),
                    "entity_mentions": diag.json_safe(
                        before_event.get("entity_mentions") or []
                    ),
                },
                "after_recall_boundary_precision": {
                    "actor": after_actor,
                    "admit": after_ok,
                    "reasons": list(after_assessment.get("reasons") or []),
                    "entity_mentions": diag.json_safe(regenerated_mentions),
                },
                "delta": {
                    "actor_recovered": bool(not before_actor and after_actor),
                    "admission_changed": before_ok != after_ok,
                    "removed_reasons": sorted(
                        set(before_assessment.get("reasons") or [])
                        - set(after_assessment.get("reasons") or [])
                    ),
                    "added_reasons": sorted(
                        set(after_assessment.get("reasons") or [])
                        - set(before_assessment.get("reasons") or [])
                    ),
                },
            }
        )

    print("\n" + "=" * 82)
    print("BEFORE → AFTER RECALL + ACTOR-BOUNDARY PRECISION")
    print("=" * 82)
    print(f"Events replayed:             {len(rows)}")
    print(f"Actors newly recovered:      {actor_recovered}")
    print(f"Strategic admitted before:   {before_admitted}")
    print(f"Strategic admitted after:    {after_admitted}")
    print(f"Events with any delta:       {events_changed}")
    print(f"Before exclusion reasons:    {dict(before_reason_counts)}")
    print(f"After exclusion reasons:     {dict(after_reason_counts)}")

    print("\nActor-missing change:")
    print(
        "  specific_acting_institution_missing: "
        f"{before_reason_counts.get('specific_acting_institution_missing', 0)}"
        " → "
        f"{after_reason_counts.get('specific_acting_institution_missing', 0)}"
    )

    print("\nEvents with newly recovered actors:")
    for item in rows:
        if item["delta"]["actor_recovered"]:
            print(
                f"  {item['event_id']} | "
                f"{item['after_recall_boundary_precision']['actor']} | "
                f"evidence={item['evidence_count']} | "
                f"admit={item['after_recall_boundary_precision']['admit']}"
            )

    output = {
        "replay_version": REPLAY_VERSION,
        "read_only": True,
        "extractor": {
            "agent_version": agent_version,
            "patch_build_id": build_id,
        },
        "report_id": report_id,
        "topic": topic,
        "report_lookup": report_lookup,
        "event_tables": event_table_info,
        "source_tables": source_table_info,
        "evidence_rebuild": evidence_diag,
        "reported_actor_missing_event_count": len(actor_missing_ids),
        "reported_considered_event_ids": sorted(considered_ids),
        "summary": {
            "event_count": len(rows),
            "actor_recovered_count": actor_recovered,
            "strategic_admitted_before": before_admitted,
            "strategic_admitted_after": after_admitted,
            "before_reason_counts": dict(before_reason_counts),
            "after_reason_counts": dict(after_reason_counts),
            "specific_actor_missing_before": before_reason_counts.get(
                "specific_acting_institution_missing", 0
            ),
            "specific_actor_missing_after": after_reason_counts.get(
                "specific_acting_institution_missing", 0
            ),
        },
        "events": rows,
        "interpretation_rules": [
            "This replay changes only entity_mentions regenerated from persisted event anchors using V5.3.3 recall + placeholder and candidate-boundary filtering.",
            "Unknown-acronym actor-gate behavior is intentionally unchanged; this cycle only removes generic placeholder actors.",
            "No source-context resolution of 'The Company' is performed.",
            "No admission or StrategicInference thresholds are changed.",
            "A higher admitted-event count is not by itself proof of better strategic quality; topic/coherence gates must still hold.",
        ],
    }

    output_path = (root / args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(diag.json_safe(output), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print(f"\nJSON output: {output_path}")
    print("No production data was modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
