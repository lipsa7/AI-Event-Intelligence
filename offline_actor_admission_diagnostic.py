#!/usr/bin/env python
"""
Offline actor/admission diagnostic for AI Event Intelligence.

Purpose
-------
Diagnose the 38 -> 1 Strategic Event Admission collapse without NewsAPI or
any admission-policy changes.

The utility:
1. loads a released report from data/reports.sqlite3;
2. extracts the EVENT-* ids referenced by strategic_event_admission;
3. searches local SQLite databases for those persisted EVENT rows;
4. inspects the exact actor fields/entity_mentions seen after EventService
   persistence;
5. runs the CURRENT deterministic actor resolver/assessment in memory only;
6. reports likely schema/type-vocabulary/control-flow failure modes.

It performs no web search, no source fetch, no LLM call, no database mutation,
and no report mutation.
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

DEFAULT_REPORT_ID = "REPORT-FF59222B19F714AB8918D407"
DEFAULT_TOPIC = "artificial intelligence"

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

CURRENT_INSTITUTION_TYPES = {
    "company",
    "organization",
    "organisation",
    "government",
    "government_body",
    "government_agency",
    "agency",
    "regulator",
    "institution",
    "municipality",
    "township",
    "city",
    "county",
    "state_government",
    "local_government",
    "public_body",
    "authority",
    "commission",
    "council",
    "university",
    "research_institution",
    "nonprofit",
    "non_profit",
    "ngo",
    "industry_body",
    "coalition",
}

CURRENT_PERSON_TYPES = {
    "person",
    "individual",
    "executive",
    "founder",
    "ceo",
    "employee",
    "spokesperson",
    "organizer",
    "organiser",
    "researcher",
    "analyst",
}


def clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def enum(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", clean(value).casefold()).strip("_")


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    return str(value)


def qident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def chunked(values: Sequence[str], size: int = 200) -> Iterable[list[str]]:
    for i in range(0, len(values), size):
        yield list(values[i : i + size])


def parse_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except Exception:
        return value


def expand_json_columns(row: Mapping[str, Any]) -> dict[str, Any]:
    output = dict(row)
    for key, raw_value in list(row.items()):
        parsed = parse_jsonish(raw_value)
        if parsed is raw_value:
            continue
        output[key] = parsed
        if isinstance(parsed, Mapping):
            for nested_key, nested_value in parsed.items():
                output.setdefault(str(nested_key), nested_value)

    mentions = output.get("entity_mentions")
    if isinstance(mentions, str):
        parsed = parse_jsonish(mentions)
        if isinstance(parsed, list):
            output["entity_mentions"] = parsed
    return output


def get_report_payload(report_db: Path, report_id: str) -> dict[str, Any]:
    if not report_db.exists():
        raise FileNotFoundError(f"Report DB not found: {report_db}")
    conn = sqlite3.connect(str(report_db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT rr.payload_json
            FROM reports r
            JOIN report_revisions rr
              ON rr.report_revision_id = r.current_revision_id
            WHERE r.report_id = ?
            """,
            (report_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise RuntimeError(f"Report not found: {report_id}")
    payload = json.loads(row["payload_json"])
    if not isinstance(payload, Mapping):
        raise RuntimeError("Report payload_json is not an object")
    return dict(payload)


def collect_named_dicts(value: Any, key_name: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key) == key_name and isinstance(nested, Mapping):
                found.append(dict(nested))
            found.extend(collect_named_dicts(nested, key_name))
    elif isinstance(value, list):
        for nested in value:
            found.extend(collect_named_dicts(nested, key_name))
    return found


def choose_admission(payload: Mapping[str, Any]) -> dict[str, Any]:
    candidates = collect_named_dicts(payload, "strategic_event_admission")
    if not candidates:
        raise RuntimeError(
            "No strategic_event_admission block found in the report payload"
        )

    def score(item: Mapping[str, Any]) -> tuple[int, int, int]:
        excluded = item.get("excluded_events") or []
        considered = item.get("considered_event_ids") or []
        admitted_count = int(item.get("admitted_event_count") or 0)
        return (
            len(excluded) + len(considered),
            admitted_count,
            len(excluded),
        )

    candidates.sort(key=score, reverse=True)
    return dict(candidates[0])


def admission_event_ids(admission: Mapping[str, Any]) -> list[str]:
    ids: set[str] = set()
    for raw in admission.get("considered_event_ids") or []:
        event_id = clean(raw).upper()
        if event_id.startswith("EVENT-"):
            ids.add(event_id)
    for item in admission.get("excluded_events") or []:
        if not isinstance(item, Mapping):
            continue
        event_id = clean(item.get("event_id")).upper()
        if event_id.startswith("EVENT-"):
            ids.add(event_id)
    return sorted(ids)


def discover_sqlite_files(project_root: Path) -> list[Path]:
    candidates: set[Path] = set()
    roots = [project_root / "data", project_root]
    patterns = ("*.sqlite3", "*.sqlite", "*.db")
    for root in roots:
        if not root.exists():
            continue
        for pattern in patterns:
            for path in root.rglob(pattern):
                if path.is_file():
                    candidates.add(path.resolve())
    return sorted(candidates)


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({qident(table)})").fetchall()
    return [str(row[1]) for row in rows]


def scan_db_for_events(
    db_path: Path,
    event_ids: Sequence[str],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except Exception:
        return output

    try:
        tables = [
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            if row and row[0]
        ]
        for table in tables:
            try:
                columns = table_columns(conn, table)
            except Exception:
                continue
            event_id_col = next(
                (
                    col
                    for col in columns
                    if col.casefold() in {"event_id", "eventid"}
                ),
                None,
            )
            if not event_id_col:
                continue

            for batch in chunked(list(event_ids), 200):
                placeholders = ",".join("?" for _ in batch)
                sql = (
                    f"SELECT * FROM {qident(table)} "
                    f"WHERE UPPER({qident(event_id_col)}) IN ({placeholders})"
                )
                try:
                    rows = conn.execute(sql, tuple(batch)).fetchall()
                except Exception:
                    continue
                for row in rows:
                    record = expand_json_columns(dict(row))
                    record["_diagnostic_db"] = str(db_path)
                    record["_diagnostic_table"] = table
                    output.append(record)
    finally:
        conn.close()

    return output


def dedupe_event_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for raw in rows:
        record = dict(raw)
        event_id = clean(
            record.get("event_id")
            or record.get("eventid")
            or record.get("id")
        ).upper()
        if not event_id.startswith("EVENT-"):
            continue

        # Prefer rows that expose more of the actor/entity schema.
        richness = sum(
            1
            for key in (
                *ACTOR_FIELDS,
                "entity_mentions",
                "title",
                "summary",
                "description",
                "event_type",
                "source_id",
            )
            if record.get(key) not in (None, "", [], {})
        )
        existing = by_id.get(event_id)
        existing_richness = (
            sum(
                1
                for key in (
                    *ACTOR_FIELDS,
                    "entity_mentions",
                    "title",
                    "summary",
                    "description",
                    "event_type",
                    "source_id",
                )
                if existing.get(key) not in (None, "", [], {})
            )
            if existing
            else -1
        )
        if existing is None or richness > existing_richness:
            by_id[event_id] = record
    return by_id


def mentions_state(event: Mapping[str, Any]) -> str:
    if "entity_mentions" not in event:
        return "missing"
    raw = event.get("entity_mentions")
    if raw in (None, [], ()):
        return "empty"
    if isinstance(raw, (list, tuple)):
        return "present"
    return "malformed"


def raw_mentions(event: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = event.get("entity_mentions")
    if not isinstance(raw, (list, tuple)):
        return []
    output: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            output.append({"malformed_value": clean(item)})
            continue
        output.append(
            {
                "mention": clean(
                    item.get("mention")
                    or item.get("name")
                    or item.get("entity_name")
                    or item.get("text")
                ),
                "entity_type": clean(
                    item.get("entity_type")
                    or item.get("type")
                    or item.get("label")
                ),
                "role": clean(
                    item.get("role")
                    or item.get("entity_role")
                    or item.get("relation_role")
                ),
                "raw_keys": sorted(str(k) for k in item.keys()),
            }
        )
    return output


def actor_fields(event: Mapping[str, Any]) -> dict[str, Any]:
    return {key: json_safe(event.get(key)) for key in ACTOR_FIELDS}


def import_orchestrator(project_root: Path):
    root = str(project_root.resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    from services.research_orchestrator_service import (  # type: ignore
        ResearchOrchestratorService,
    )

    return ResearchOrchestratorService


def event_summary(event: Mapping[str, Any]) -> str:
    return clean(
        event.get("summary")
        or event.get("event")
        or event.get("title")
        or event.get("description")
    )[:1200]


def inspect_event_service_source(project_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "imported": False,
        "to_dict_mentions_entity_mentions": None,
        "replace_events_mentions_entity_mentions": None,
        "event_service_file": "",
    }
    try:
        root = str(project_root.resolve())
        if root not in sys.path:
            sys.path.insert(0, root)
        import services.event_service as event_service_module  # type: ignore

        result["imported"] = True
        result["event_service_file"] = clean(
            getattr(event_service_module, "__file__", "")
        )
        cls = getattr(event_service_module, "EventService", None)
        if cls is None:
            return result

        for method_name, result_key in (
            ("to_dict", "to_dict_mentions_entity_mentions"),
            ("replace_events_for_source", "replace_events_mentions_entity_mentions"),
        ):
            method = getattr(cls, method_name, None)
            if method is None:
                continue
            try:
                source = inspect.getsource(method)
            except Exception:
                continue
            result[result_key] = "entity_mentions" in source
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def diagnostic_for_event(
    event: Mapping[str, Any],
    orchestrator_cls: Any,
    topic: str,
) -> dict[str, Any]:
    try:
        assessment = orchestrator_cls._strategic_event_assessment(
            topic=topic,
            event=event,
            evidence_items=[],
        )
    except Exception as exc:
        assessment = {
            "admit": False,
            "actor": "",
            "reasons": [f"assessment_error:{type(exc).__name__}"],
            "assessment_error": str(exc),
        }

    actor = clean(assessment.get("actor"))
    mentions = raw_mentions(event)
    normalized_types = [
        enum(item.get("entity_type")) or "unknown"
        for item in mentions
        if isinstance(item, Mapping)
    ]
    institutional_mentions = [
        item
        for item in mentions
        if enum(item.get("entity_type")) in CURRENT_INSTITUTION_TYPES
    ]
    unrecognized_nonperson_mentions = [
        item
        for item in mentions
        if (
            (enum(item.get("entity_type")) or "unknown")
            not in CURRENT_INSTITUTION_TYPES
            and (enum(item.get("entity_type")) or "unknown")
            not in CURRENT_PERSON_TYPES
            and clean(item.get("mention"))
        )
    ]
    structured_actor_values = {
        key: value
        for key, value in actor_fields(event).items()
        if clean(value)
    }

    likely_failure_modes: list[str] = []
    if not actor:
        if structured_actor_values:
            likely_failure_modes.append(
                "structured_actor_present_but_current_resolver_returned_empty"
            )
        if institutional_mentions:
            likely_failure_modes.append(
                "recognized_institutional_entity_present_but_current_resolver_returned_empty"
            )
        if unrecognized_nonperson_mentions:
            likely_failure_modes.append(
                "possible_entity_type_vocabulary_mismatch"
            )
        if mentions_state(event) in {"missing", "empty"}:
            likely_failure_modes.append(
                "entity_mentions_missing_or_empty_after_eventservice_roundtrip"
            )

    return {
        "event_id": clean(event.get("event_id") or event.get("id")).upper(),
        "source_id": clean(event.get("source_id")).upper(),
        "summary": event_summary(event),
        "event_keys": sorted(str(key) for key in event.keys()),
        "entity_mentions_state": mentions_state(event),
        "raw_actor_fields": actor_fields(event),
        "raw_entity_mentions": mentions,
        "raw_entity_types": normalized_types,
        "current_resolved_primary_actor": actor,
        "current_assessment_admit_without_evidence": bool(
            assessment.get("admit")
        ),
        "current_assessment_reasons_without_evidence": list(
            assessment.get("reasons") or []
        ),
        "likely_failure_modes": likely_failure_modes,
        "storage": {
            "db": clean(event.get("_diagnostic_db")),
            "table": clean(event.get("_diagnostic_table")),
        },
    }


def summarize(diagnostics: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    entity_states = Counter(
        clean(item.get("entity_mentions_state")) or "unknown"
        for item in diagnostics
    )
    entity_types = Counter()
    actor_field_presence = Counter()
    failure_modes = Counter()
    resolved = 0

    for item in diagnostics:
        if clean(item.get("current_resolved_primary_actor")):
            resolved += 1
        for raw_type in item.get("raw_entity_types") or []:
            entity_types[clean(raw_type) or "unknown"] += 1
        for key, value in (item.get("raw_actor_fields") or {}).items():
            if clean(value):
                actor_field_presence[key] += 1
        for reason in item.get("likely_failure_modes") or []:
            failure_modes[clean(reason)] += 1

    return {
        "event_rows_found": len(diagnostics),
        "resolved_actor_count": resolved,
        "unresolved_actor_count": len(diagnostics) - resolved,
        "entity_mentions_state_distribution": dict(entity_states),
        "raw_entity_type_distribution": dict(entity_types),
        "raw_actor_field_presence": dict(actor_field_presence),
        "likely_failure_mode_distribution": dict(failure_modes),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        default=".",
        help="AI Event Intelligence project root (default: current directory)",
    )
    parser.add_argument(
        "--report-id",
        default=DEFAULT_REPORT_ID,
    )
    parser.add_argument(
        "--topic",
        default=DEFAULT_TOPIC,
    )
    parser.add_argument(
        "--report-db",
        default="data/reports.sqlite3",
    )
    parser.add_argument(
        "--output",
        default="data/actor_admission_diagnostic.json",
    )
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    report_db = (
        Path(args.report_db)
        if Path(args.report_db).is_absolute()
        else project_root / args.report_db
    )
    output_path = (
        Path(args.output)
        if Path(args.output).is_absolute()
        else project_root / args.output
    )

    payload = get_report_payload(report_db, args.report_id)
    admission = choose_admission(payload)
    event_ids = admission_event_ids(admission)

    if not event_ids:
        raise RuntimeError(
            "No EVENT-* ids were found in the report's strategic admission diagnostics"
        )

    db_files = discover_sqlite_files(project_root)
    raw_rows: list[dict[str, Any]] = []
    for db_path in db_files:
        if db_path.resolve() == report_db.resolve():
            continue
        raw_rows.extend(scan_db_for_events(db_path, event_ids))

    by_id = dedupe_event_rows(raw_rows)
    orchestrator_cls = import_orchestrator(project_root)

    diagnostics = [
        diagnostic_for_event(by_id[event_id], orchestrator_cls, args.topic)
        for event_id in event_ids
        if event_id in by_id
    ]
    missing_event_ids = [
        event_id for event_id in event_ids if event_id not in by_id
    ]

    insilico_events = [
        item
        for item in diagnostics
        if "insilico" in clean(item.get("summary")).casefold()
        or any(
            "insilico" in clean(mention.get("mention")).casefold()
            for mention in item.get("raw_entity_mentions") or []
            if isinstance(mention, Mapping)
        )
    ]

    result = {
        "diagnostic_version": "offline-actor-admission-diagnostic-v1",
        "network_calls": False,
        "database_mutations": False,
        "admission_behavior_changed": False,
        "report_id": args.report_id,
        "topic": args.topic,
        "report_db": str(report_db),
        "sqlite_files_scanned": [str(path) for path in db_files],
        "report_admission_counts": {
            "admitted_event_count": admission.get("admitted_event_count"),
            "considered_event_count": admission.get("considered_event_count"),
            "excluded_event_count": admission.get("excluded_event_count"),
            "admitted_evidence_count": admission.get("admitted_evidence_count"),
            "considered_evidence_count": admission.get("considered_evidence_count"),
            "exclusion_reasons": admission.get("exclusion_reasons"),
        },
        "requested_event_id_count": len(event_ids),
        "requested_event_ids": event_ids,
        "persisted_event_id_count": len(diagnostics),
        "missing_event_ids": missing_event_ids,
        "summary": summarize(diagnostics),
        "event_service_source_inspection": inspect_event_service_source(project_root),
        "insilico_events": insilico_events,
        "events": diagnostics,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(json_safe(result), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("OFFLINE ACTOR / ADMISSION DIAGNOSTIC")
    print(f"Report: {args.report_id}")
    print(f"Topic: {args.topic}")
    print(f"Requested EVENT ids: {len(event_ids)}")
    print(f"Persisted EVENT rows found: {len(diagnostics)}")
    print(f"Missing EVENT rows: {len(missing_event_ids)}")
    print(
        "Resolved actors:",
        result["summary"]["resolved_actor_count"],
        "/",
        len(diagnostics),
    )
    print(
        "entity_mentions states:",
        json.dumps(
            result["summary"]["entity_mentions_state_distribution"],
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    print(
        "entity types:",
        json.dumps(
            result["summary"]["raw_entity_type_distribution"],
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    print(
        "likely failure modes:",
        json.dumps(
            result["summary"]["likely_failure_mode_distribution"],
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    print("Insilico-related persisted events:", len(insilico_events))
    for item in insilico_events[:12]:
        print()
        print(item["event_id"])
        print("  actor:", item["current_resolved_primary_actor"] or "<EMPTY>")
        print("  entity_mentions_state:", item["entity_mentions_state"])
        print(
            "  actor_fields:",
            json.dumps(item["raw_actor_fields"], ensure_ascii=False),
        )
        print(
            "  entity_mentions:",
            json.dumps(item["raw_entity_mentions"], ensure_ascii=False),
        )
        print(
            "  likely:",
            ", ".join(item["likely_failure_modes"]) or "<none>",
        )

    svc = result["event_service_source_inspection"]
    print()
    print("EventService file:", svc.get("event_service_file") or "<unknown>")
    print(
        "EventService.to_dict mentions entity_mentions:",
        svc.get("to_dict_mentions_entity_mentions"),
    )
    print(
        "EventService.replace_events_for_source mentions entity_mentions:",
        svc.get("replace_events_mentions_entity_mentions"),
    )
    print()
    print("JSON output:", output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
