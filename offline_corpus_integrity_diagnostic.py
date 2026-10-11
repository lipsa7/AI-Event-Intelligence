#!/usr/bin/env python3
"""
offline_corpus_integrity_diagnostic.py

READ-ONLY diagnostic for AI Event Intelligence.

This script implements the diagnostic-first sequence before any extractor or
orchestrator production change.

It answers four concrete questions:

1. INSILICO RECALL DISCRIMINATOR
   For every persisted Insilico-related event:
       - Is literal "Insilico" present in the event anchor (summary/snippets)?
       - Is literal "Insilico" present in the persisted source text?
       - Is an Insilico mention present in entity_mentions?

   Classification:
       anchor contains Insilico + entity mention absent
           => candidate_generation_recall_defect

       source contains Insilico + anchor does not
           => anchoring_or_segmentation_recall_defect

       anchor and entity_mentions contain Insilico
           => actor_resolution_or_typing_defect

       neither source nor anchor contains Insilico
           => persisted_source_or_event_provenance_needs_inspection

2. EXTRACTION-BRANCH AUDIT
   Explains how events with empty entity_mentions could survive by inspecting
   extraction_metadata and the material-event actor-guard applicability.

3. EVIDENCE-PER-EVENT DISTRIBUTION
   First reads persisted/report EVID-* records.
   Then, when possible, deterministically rebuilds evidence using the CURRENT
   local EvidenceBuilderAgent with:
       semantic_fact_extraction=False
       semantic_fail_closed=False

   A fail-fast NoLLM object is injected where supported, so an accidental model
   call cannot silently occur.

4. CURRENT STRATEGIC ADMISSION SNAPSHOT
   Uses the current orchestrator's deterministic actor/admission helpers only
   when they can be imported safely. It does not instantiate the orchestrator.

This script does NOT:
- call NewsAPI;
- fetch the web;
- write to the database;
- change EventService;
- change ExtractionAgent;
- change Strategic Admission;
- change StrategicInference thresholds.

Recommended location:
    project root beside app.py

Example:
    .\\venv\\Scripts\\python.exe .\\offline_corpus_integrity_diagnostic.py ^
        --project-root . ^
        --report-id REPORT-FF59222B19F714AB8918D407 ^
        --topic "artificial intelligence"

Output:
    data/corpus_integrity_diagnostic.json
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


DIAGNOSTIC_VERSION = "offline-corpus-integrity-diagnostic-v1.0"
DEFAULT_OUTPUT = "data/corpus_integrity_diagnostic.json"

EVENT_RE = re.compile(r"^EVENT-[A-Z0-9]+$", re.I)
EVID_RE = re.compile(r"^EVID-[A-Z0-9]+$", re.I)
SOURCE_RE = re.compile(r"^SOURCE-[A-Z0-9]+$", re.I)
REPORT_RE = re.compile(r"^REPORT-[A-Z0-9]+$", re.I)

INSILICO_RE = re.compile(r"\binsilico(?:\s+medicine)?\b", re.I)

MATERIAL_ACTION_RE = re.compile(
    r"\b(?:"
    r"rais(?:e|ed|es|ing)|fund(?:ed|ing|s)?|financ(?:e|ed|es|ing)|"
    r"invest(?:s|ed|ment|ing)?|acquir(?:e|ed|es|ing)|acquisition|"
    r"merg(?:e|ed|es|ing)|partnership|partner(?:s|ed|ing)?|"
    r"collaborat(?:e|ed|es|ing|ion)|contract(?:s|ed|ing)?|agreement|deal|"
    r"launch(?:es|ed|ing)?|deploy(?:s|ed|ment|ing)?|"
    r"build(?:s|ing|out|built)?|open(?:s|ed|ing)?|close(?:s|d|ing)?|"
    r"appoint(?:s|ed|ment)?|approve(?:s|d|al)?|ban(?:s|ned|ning)?|"
    r"regulat(?:es|ed|ing|ion)|restrict(?:s|ed|ing|ion)|sue(?:s|d)?|"
    r"announce(?:s|d|ment|ing)?|sign(?:s|ed|ing)?|award(?:s|ed|ing)?|"
    r"introduc(?:e|ed|es|ing)|releas(?:e|ed|es|ing)"
    r")\b",
    re.I,
)

TEXT_KEYS = (
    "body", "article_body", "full_text", "text", "content", "clean_text",
    "article_text", "raw_text", "description", "summary", "title",
)

ANCHOR_KEYS = (
    "summary", "snippets", "evidence", "source_excerpt", "source_text",
    "description", "title",
)


class NoLLM:
    """Fail immediately if deterministic evidence rebuilding attempts an LLM call."""

    def __getattr__(self, name: str):
        def _blocked(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError(
                f"Offline diagnostic forbids LLM use; attempted method={name!r}."
            )
        return _blocked


def clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def enum(value: Any) -> str:
    return clean(value).casefold().replace("-", "_").replace(" ", "_")


def parse_jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[:1] not in "[{":
        return value
    try:
        return json.loads(text)
    except Exception:
        return value


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(v) for v in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, Mapping):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from walk(child)


def mappings(value: Any) -> Iterable[dict[str, Any]]:
    for node in walk(value):
        if isinstance(node, Mapping):
            yield dict(node)


def strings(value: Any) -> Iterable[str]:
    for node in walk(value):
        if isinstance(node, str):
            yield node


def compact(value: Any, max_chars: int = 12000) -> dict[str, Any]:
    safe = json_safe(value)
    text = json.dumps(safe, ensure_ascii=False, sort_keys=True, default=str)
    if len(text) <= max_chars:
        return {"truncated": False, "character_count": len(text), "value": safe}
    return {
        "truncated": True,
        "character_count": len(text),
        "prefix": text[:max_chars],
    }


def dedupe_records(
    records: Iterable[Mapping[str, Any]],
    *,
    id_keys: Sequence[str],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)

        identity = ""
        for key in id_keys:
            identity = clean(item.get(key))
            if identity:
                break

        if not identity:
            identity = json.dumps(
                json_safe(item),
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )

        marker = identity.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        output.append(item)

    return output


def sqlite_candidates(root: Path) -> list[Path]:
    output: list[Path] = []
    seen: set[Path] = set()

    preferred = [
        root / "data" / "event_intelligence.db",
        root / "data" / "event_intelligence.sqlite3",
        root / "data" / "reports.sqlite3",
        root / "data" / "events.sqlite3",
        root / "data" / "research.sqlite3",
    ]

    for path in preferred:
        if path.exists():
            rp = path.resolve()
            if rp not in seen:
                seen.add(rp)
                output.append(path)

    for base in (root / "data", root):
        if not base.exists():
            continue
        for pattern in ("*.db", "*.sqlite", "*.sqlite3"):
            for path in base.rglob(pattern):
                if any(
                    part in {".git", "venv", ".venv", "__pycache__", "node_modules"}
                    for part in path.parts
                ):
                    continue
                rp = path.resolve()
                if rp in seen:
                    continue
                seen.add(rp)
                output.append(path)

    return output


def ro_connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    escaped = table.replace('"', '""')
    rows = conn.execute(f'PRAGMA table_info("{escaped}")').fetchall()
    return [str(row[1]) for row in rows]


def row_dict(columns: Sequence[str], row: Sequence[Any]) -> dict[str, Any]:
    return {
        str(column): parse_jsonish(value)
        for column, value in zip(columns, row)
    }


def find_report(
    databases: Sequence[Path],
    report_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    diagnostic = {"database": "", "table": "", "columns": []}

    for db in databases:
        try:
            conn = ro_connect(db)
        except Exception:
            continue

        try:
            for table in table_names(conn):
                columns = table_columns(conn, table)
                lower = {c.casefold(): c for c in columns}
                report_col = lower.get("report_id")

                if report_col:
                    et = table.replace('"', '""')
                    ec = report_col.replace('"', '""')
                    row = conn.execute(
                        f'SELECT * FROM "{et}" WHERE "{ec}" = ? LIMIT 1',
                        (report_id,),
                    ).fetchone()
                    if row is not None:
                        return row_dict(columns, row), {
                            "database": str(db),
                            "table": table,
                            "columns": columns,
                        }

                # Conservative fallback for JSON-embedded report IDs.
                et = table.replace('"', '""')
                try:
                    rows = conn.execute(f'SELECT * FROM "{et}" LIMIT 5000').fetchall()
                except Exception:
                    continue

                for row in rows:
                    record = row_dict(columns, row)
                    if report_id in set(strings(record)):
                        return record, {
                            "database": str(db),
                            "table": table,
                            "columns": columns,
                        }
        finally:
            conn.close()

    return None, diagnostic


def find_admission(report: Mapping[str, Any]) -> dict[str, Any]:
    candidates = []
    for item in mappings(report):
        if isinstance(item.get("excluded_events"), list) and (
            "considered_event_ids" in item
            or "exclusion_reasons" in item
            or "considered_event_count" in item
        ):
            candidates.append(item)

    if not candidates:
        return {}

    def score(item: Mapping[str, Any]) -> tuple[int, int, int]:
        return (
            len(item.get("excluded_events") or []),
            len(item.get("considered_event_ids") or []),
            int(item.get("admitted_event_count") or 0),
        )

    return dict(max(candidates, key=score))


def event_ids_from_admission(
    admission: Mapping[str, Any],
) -> tuple[set[str], set[str], set[str]]:
    all_ids: set[str] = set()
    actor_missing: set[str] = set()
    considered: set[str] = set()

    for raw in admission.get("considered_event_ids", []) or []:
        event_id = clean(raw).upper()
        if EVENT_RE.match(event_id):
            all_ids.add(event_id)
            considered.add(event_id)

    for raw in admission.get("excluded_events", []) or []:
        if not isinstance(raw, Mapping):
            continue
        event_id = clean(raw.get("event_id") or raw.get("id")).upper()
        if not EVENT_RE.match(event_id):
            continue
        all_ids.add(event_id)
        reasons = [clean(x) for x in (raw.get("reasons") or [])]
        if "specific_acting_institution_missing" in reasons:
            actor_missing.add(event_id)

    return all_ids, actor_missing, considered


def locate_rows_by_id(
    databases: Sequence[Path],
    ids: set[str],
    id_column_name: str,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    found: dict[str, dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []

    if not ids:
        return found, diagnostics

    for db in databases:
        try:
            conn = ro_connect(db)
        except Exception:
            continue

        try:
            for table in table_names(conn):
                columns = table_columns(conn, table)
                lower = {c.casefold(): c for c in columns}
                id_col = lower.get(id_column_name.casefold())
                if not id_col:
                    continue

                et = table.replace('"', '""')
                ec = id_col.replace('"', '""')
                matched = 0

                for identity in sorted(ids - set(found)):
                    try:
                        row = conn.execute(
                            f'SELECT * FROM "{et}" WHERE "{ec}" = ? LIMIT 1',
                            (identity,),
                        ).fetchone()
                    except Exception:
                        row = None

                    if row is None:
                        continue

                    record = row_dict(columns, row)
                    record["_diagnostic_database"] = str(db)
                    record["_diagnostic_table"] = table
                    found[identity] = record
                    matched += 1

                if matched:
                    diagnostics.append(
                        {
                            "database": str(db),
                            "table": table,
                            "id_column": id_col,
                            "matched_count": matched,
                            "columns": columns,
                        }
                    )

                if len(found) == len(ids):
                    return found, diagnostics
        finally:
            conn.close()

    return found, diagnostics


def source_text_from_row(row: Mapping[str, Any]) -> str:
    pieces: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        if isinstance(value, str):
            text = value.strip()
            if text and text not in seen:
                seen.add(text)
                pieces.append(text)

    for key in TEXT_KEYS:
        add(row.get(key))

    for nested_key in ("payload", "metadata", "article", "source", "data"):
        nested = row.get(nested_key)
        if isinstance(nested, Mapping):
            for key in TEXT_KEYS:
                add(nested.get(key))

    return "\n".join(pieces)


def event_anchor_text(event: Mapping[str, Any]) -> str:
    pieces: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        if isinstance(value, str):
            text = value.strip()
            if text and text not in seen:
                seen.add(text)
                pieces.append(text)
        elif isinstance(value, (list, tuple)):
            for item in value:
                add(item)
        elif isinstance(value, Mapping):
            for key in ("text", "excerpt", "source_excerpt", "snippet", "value"):
                add(value.get(key))

    # Summary/snippets first because those are the most important event anchors.
    add(event.get("summary"))
    add(event.get("snippets"))
    for key in ANCHOR_KEYS:
        add(event.get(key))

    return "\n".join(pieces)


def entity_mentions(event: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = event.get("entity_mentions")
    if not isinstance(raw, list):
        return []
    return [dict(x) for x in raw if isinstance(x, Mapping)]


def contains_insilico(value: Any) -> bool:
    return bool(INSILICO_RE.search(" ".join(strings(value))))


def insilico_mention_present(event: Mapping[str, Any]) -> bool:
    for mention in entity_mentions(event):
        name = clean(mention.get("mention") or mention.get("name"))
        if INSILICO_RE.search(name):
            return True
    return False


def classify_insilico_recall(
    *,
    anchor_has_insilico: bool,
    source_has_insilico: bool,
    entity_has_insilico: bool,
) -> str:
    """
    Diagnostic classification only. It deliberately does not propose a code patch.
    """
    if anchor_has_insilico and not entity_has_insilico:
        return "candidate_generation_recall_defect"
    if source_has_insilico and not anchor_has_insilico:
        return "anchoring_or_segmentation_recall_defect"
    if anchor_has_insilico and entity_has_insilico:
        return "mention_present_check_actor_resolution_or_typing"
    if not source_has_insilico and not anchor_has_insilico:
        return "source_or_event_provenance_needs_inspection"
    return "mixed_or_unclassified"


def metadata_branch_signature(event: Mapping[str, Any]) -> dict[str, Any]:
    metadata = event.get("extraction_metadata")
    if not isinstance(metadata, Mapping):
        metadata = {}

    interesting_keys = (
        "research_depth",
        "effective_extraction_depth",
        "extraction_mode",
        "mode",
        "strategy",
        "extraction_strategy",
        "construction_method",
        "fallback_used",
        "semantic_recovery",
        "chunk_index",
        "chunk_count",
        "taxonomy",
        "taxonomy_reason",
        "event_taxonomy",
        "event_taxonomy_reason",
        "prompt_version",
        "agent_version",
        "build_id",
    )

    result = {}
    for key in interesting_keys:
        value = metadata.get(key)
        if value not in (None, "", [], {}):
            result[key] = json_safe(value)

    # Preserve key names even if this build uses different metadata labels.
    result["_metadata_keys"] = sorted(str(k) for k in metadata.keys())
    return result


def material_actor_guard_applies(event: Mapping[str, Any]) -> bool:
    text = " ".join(
        clean(event.get(key))
        for key in ("event_type", "title", "summary")
        if clean(event.get(key))
    )
    return bool(MATERIAL_ACTION_RE.search(text))


def analyze_empty_mentions(
    event_rows: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    rows = []
    branch_counts: Counter[str] = Counter()
    guard_counts: Counter[str] = Counter()

    for event_id, event in sorted(event_rows.items()):
        mentions = entity_mentions(event)
        if mentions:
            continue

        signature = metadata_branch_signature(event)
        branch_key = json.dumps(
            {k: v for k, v in signature.items() if k != "_metadata_keys"},
            sort_keys=True,
            default=str,
        )
        if branch_key == "{}":
            branch_key = "<no recognized branch metadata>"
        branch_counts[branch_key] += 1

        guard_applies = material_actor_guard_applies(event)
        guard_counts["guard_applies" if guard_applies else "guard_not_applicable"] += 1

        rows.append(
            {
                "event_id": event_id,
                "source_id": clean(event.get("source_id")),
                "title": clean(event.get("title")),
                "summary": clean(event.get("summary"))[:1800],
                "event_type": clean(event.get("event_type")),
                "material_actor_guard_would_apply_by_current_regex": guard_applies,
                "extraction_branch_signature": signature,
                "event_keys": sorted(
                    str(k) for k in event.keys()
                    if not str(k).startswith("_diagnostic_")
                ),
                "persisted_record": compact(
                    {
                        k: v for k, v in event.items()
                        if not str(k).startswith("_diagnostic_")
                    },
                    10000,
                ),
            }
        )

    return {
        "empty_entity_mention_event_count": len(rows),
        "guard_applicability_distribution": dict(guard_counts),
        "branch_signature_distribution": dict(branch_counts),
        "events": rows,
    }


def locate_persisted_evidence(
    databases: Sequence[Path],
    event_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    for db in databases:
        try:
            conn = ro_connect(db)
        except Exception:
            continue

        try:
            for table in table_names(conn):
                columns = table_columns(conn, table)
                lower = {c.casefold(): c for c in columns}
                evid_col = lower.get("evidence_id")
                event_col = lower.get("event_id")
                if not evid_col or not event_col:
                    continue

                et = table.replace('"', '""')
                try:
                    rows = conn.execute(f'SELECT * FROM "{et}" LIMIT 30000').fetchall()
                except Exception:
                    continue

                matched = 0
                for row in rows:
                    record = row_dict(columns, row)
                    evid_id = clean(record.get(evid_col)).upper()
                    event_id = clean(record.get(event_col)).upper()
                    if not EVID_RE.match(evid_id):
                        continue
                    if event_ids and event_id not in event_ids:
                        continue
                    record["evidence_id"] = evid_id
                    record["event_id"] = event_id
                    records.append(record)
                    matched += 1

                if matched:
                    diagnostics.append(
                        {
                            "database": str(db),
                            "table": table,
                            "matched_count": matched,
                        }
                    )
        finally:
            conn.close()

    return (
        dedupe_records(records, id_keys=("evidence_id", "id")),
        diagnostics,
    )


def evidence_from_report(
    report: Mapping[str, Any],
    event_ids: set[str],
) -> list[dict[str, Any]]:
    output = []

    for item in mappings(report):
        evid_id = clean(item.get("evidence_id") or item.get("id")).upper()
        if not EVID_RE.match(evid_id):
            continue

        event_id = clean(
            item.get("event_id")
            or item.get("linked_event_id")
            or item.get("canonical_event_id")
        ).upper()

        if event_ids and event_id not in event_ids:
            continue

        record = dict(item)
        record["evidence_id"] = evid_id
        if event_id:
            record["event_id"] = event_id
        output.append(record)

    return dedupe_records(output, id_keys=("evidence_id", "id"))


def normalize_source_payload(
    source_id: str,
    row: Mapping[str, Any],
) -> dict[str, Any]:
    text = source_text_from_row(row)

    title = clean(row.get("title") or row.get("article_title"))
    summary = clean(
        row.get("summary")
        or row.get("description")
        or row.get("article_summary")
    )

    body = clean(
        row.get("body")
        or row.get("article_body")
        or row.get("full_text")
        or row.get("content")
        or row.get("text")
        or row.get("clean_text")
        or text
    )

    # Supply compatible aliases without inventing factual content.
    return {
        "source_id": source_id,
        "title": title,
        "summary": summary,
        "body": body,
        "text": body,
        "content": body,
        "url": clean(row.get("url") or row.get("source_url")),
        "published_at": clean(
            row.get("published_at")
            or row.get("publication_date")
            or row.get("published_date")
        ),
        "source_metadata": {
            k: json_safe(v)
            for k, v in row.items()
            if not str(k).startswith("_diagnostic_")
        },
    }


def instantiate_evidence_builder(root: Path):
    root_text = str(root.resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)

    module = importlib.import_module("agents.evidence_builder_agent")
    cls = getattr(module, "EvidenceBuilderAgent")

    signature = inspect.signature(cls)
    if "llm_service" in signature.parameters:
        return cls(llm_service=NoLLM())
    return cls()


def rebuild_evidence_deterministically(
    *,
    root: Path,
    topic: str,
    events: Sequence[Mapping[str, Any]],
    sources: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    diagnostic = {
        "attempted": True,
        "success": False,
        "error": "",
        "metadata": {},
        "llm_forbidden": True,
    }

    try:
        builder = instantiate_evidence_builder(root)
        build = getattr(builder, "build_evidence")
        sig = inspect.signature(build)

        kwargs = {
            "topic": topic,
            "events": list(events),
            "sources": list(sources),
        }

        if "semantic_fact_extraction" in sig.parameters:
            kwargs["semantic_fact_extraction"] = False
        if "semantic_fail_closed" in sig.parameters:
            kwargs["semantic_fail_closed"] = False

        result = build(**kwargs)

        if isinstance(result, Mapping):
            records = list(
                result.get("evidence_records")
                or result.get("evidence")
                or []
            )
            # Some versions return typed groups rather than evidence_records.
            if not records:
                for key in ("facts", "metrics", "entity_triples"):
                    for item in result.get(key, []) or []:
                        if isinstance(item, Mapping):
                            records.append(dict(item))
        elif isinstance(result, list):
            records = list(result)
        else:
            records = []

        records = [dict(x) for x in records if isinstance(x, Mapping)]

        metadata_getter = getattr(builder, "get_last_run_metadata", None)
        if callable(metadata_getter):
            try:
                diagnostic["metadata"] = json_safe(metadata_getter())
            except Exception:
                diagnostic["metadata"] = {}

        diagnostic["success"] = True
        diagnostic["evidence_count"] = len(records)
        return records, diagnostic

    except Exception as exc:
        diagnostic["error"] = f"{type(exc).__name__}: {exc}"
        return [], diagnostic


def evidence_source_ids(record: Mapping[str, Any]) -> set[str]:
    output = set()
    for node in walk(record):
        if isinstance(node, str):
            candidate = clean(node).upper()
            if SOURCE_RE.match(candidate):
                output.add(candidate)
    return output


def evidence_type(record: Mapping[str, Any]) -> str:
    return enum(
        record.get("evidence_type")
        or record.get("type")
        or record.get("record_type")
        or record.get("kind")
        or "unknown"
    ) or "unknown"


def evidence_histogram(
    event_ids: set[str],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        event_id = clean(
            raw.get("event_id")
            or raw.get("linked_event_id")
            or raw.get("canonical_event_id")
        ).upper()
        if event_id in event_ids:
            by_event[event_id].append(dict(raw))

    histogram = Counter()
    per_event = []
    all_evid_ids = set()
    all_sources = set()
    type_counts = Counter()

    for event_id in sorted(event_ids):
        items = by_event.get(event_id, [])
        count = len(items)
        bucket = "0" if count == 0 else "1" if count == 1 else "2" if count == 2 else "3+"
        histogram[bucket] += 1

        evid_ids = []
        sources = set()
        event_types = Counter()

        for item in items:
            evid_id = clean(item.get("evidence_id") or item.get("id")).upper()
            if evid_id:
                evid_ids.append(evid_id)
                all_evid_ids.add(evid_id)
            srcs = evidence_source_ids(item)
            sources.update(srcs)
            all_sources.update(srcs)
            kind = evidence_type(item)
            event_types[kind] += 1
            type_counts[kind] += 1

        per_event.append(
            {
                "event_id": event_id,
                "evidence_count": count,
                "evidence_ids": sorted(evid_ids),
                "distinct_source_count": len(sources),
                "source_ids": sorted(sources),
                "evidence_type_distribution": dict(event_types),
            }
        )

    return {
        "event_count": len(event_ids),
        "histogram": {k: histogram.get(k, 0) for k in ("0", "1", "2", "3+")},
        "events_with_at_least_1": sum(
            1 for item in per_event if item["evidence_count"] >= 1
        ),
        "events_with_at_least_2": sum(
            1 for item in per_event if item["evidence_count"] >= 2
        ),
        "distinct_evidence_count": len(all_evid_ids),
        "distinct_source_count": len(all_sources),
        "evidence_type_distribution": dict(type_counts),
        "per_event": per_event,
    }


def import_orchestrator_module(root: Path):
    root_text = str(root.resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    return importlib.import_module("services.research_orchestrator_service")


def current_actor(module: Any, event: Mapping[str, Any]) -> str:
    cls = getattr(module, "ResearchOrchestratorService", None)
    fn = getattr(cls, "_event_primary_institution", None)
    if not callable(fn):
        return ""
    try:
        return clean(fn(event))
    except Exception:
        return ""


def strategic_assessment(
    module: Any,
    *,
    topic: str,
    event: Mapping[str, Any],
    evidence_items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    cls = getattr(module, "ResearchOrchestratorService", None)
    fn = getattr(cls, "_strategic_event_assessment", None)
    if not callable(fn):
        return {}
    try:
        return dict(
            fn(
                topic=topic,
                event=event,
                evidence_items=list(evidence_items),
            )
        )
    except Exception as exc:
        return {"diagnostic_error": f"{type(exc).__name__}: {exc}"}


def print_header(title: str) -> None:
    print("\n" + "=" * 84)
    print(title)
    print("=" * 84)


def print_histogram(label: str, stats: Mapping[str, Any]) -> None:
    hist = stats.get("histogram") or {}
    print(f"\n{label}")
    print(
        "  evidence/event: "
        f"0={hist.get('0', 0)} "
        f"1={hist.get('1', 0)} "
        f"2={hist.get('2', 0)} "
        f"3+={hist.get('3+', 0)}"
    )
    print(f"  events >=1 evidence: {stats.get('events_with_at_least_1', 0)}")
    print(f"  events >=2 evidence: {stats.get('events_with_at_least_2', 0)}")
    print(f"  distinct evidence:   {stats.get('distinct_evidence_count', 0)}")
    print(f"  distinct sources:    {stats.get('distinct_source_count', 0)}")
    print(f"  evidence types:      {stats.get('evidence_type_distribution', {})}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--topic", default="artificial intelligence")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--no-evidence-rebuild",
        action="store_true",
        help="Skip deterministic EvidenceBuilder replay.",
    )
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    report_id = clean(args.report_id).upper()
    topic = clean(args.topic)

    if not REPORT_RE.match(report_id):
        print("ERROR: --report-id must be a REPORT-* identifier.", file=sys.stderr)
        return 2

    print("OFFLINE CORPUS INTEGRITY DIAGNOSTIC")
    print(f"Version: {DIAGNOSTIC_VERSION}")
    print(f"Project: {root}")
    print(f"Report:  {report_id}")
    print(f"Topic:   {topic}")
    print("Mode:    READ-ONLY / NO NEWSAPI / NO WEB / LLM FORBIDDEN IN REBUILD")

    databases = sqlite_candidates(root)
    report, report_lookup = find_report(databases, report_id)
    if report is None:
        print("ERROR: report not found in local SQLite databases.", file=sys.stderr)
        return 3

    admission = find_admission(report)
    if not admission:
        print("ERROR: strategic admission diagnostic not found in report.", file=sys.stderr)
        return 4

    event_ids, actor_missing_ids, considered_ids = event_ids_from_admission(admission)
    event_rows, event_table_info = locate_rows_by_id(
        databases,
        event_ids,
        "event_id",
    )

    source_ids = {
        clean(event.get("source_id")).upper()
        for event in event_rows.values()
        if SOURCE_RE.match(clean(event.get("source_id")).upper())
    }
    source_rows, source_table_info = locate_rows_by_id(
        databases,
        source_ids,
        "source_id",
    )

    print_header("1. INSILICO RECALL DISCRIMINATOR")

    insilico_rows = []
    classification_counts = Counter()

    for event_id, event in sorted(event_rows.items()):
        broad_event_blob = event_anchor_text(event)
        source_id = clean(event.get("source_id")).upper()
        source_row = source_rows.get(source_id, {})
        source_text = source_text_from_row(source_row)

        # Include events that are Insilico-related either in event material,
        # source material, or entity mentions.
        relevant = (
            contains_insilico(broad_event_blob)
            or contains_insilico(source_text)
            or insilico_mention_present(event)
        )
        if not relevant:
            continue

        anchor_has = contains_insilico(broad_event_blob)
        source_has = contains_insilico(source_text)
        entity_has = insilico_mention_present(event)

        classification = classify_insilico_recall(
            anchor_has_insilico=anchor_has,
            source_has_insilico=source_has,
            entity_has_insilico=entity_has,
        )
        classification_counts[classification] += 1

        record = {
            "event_id": event_id,
            "source_id": source_id,
            "anchor_has_literal_insilico": anchor_has,
            "source_has_literal_insilico": source_has,
            "entity_mentions_has_insilico": entity_has,
            "classification": classification,
            "title": clean(event.get("title")),
            "summary": clean(event.get("summary"))[:2500],
            "snippets": json_safe(event.get("snippets") or []),
            "entity_mentions": json_safe(entity_mentions(event)),
            "extraction_branch_signature": metadata_branch_signature(event),
            "source_text_excerpt_around_insilico": "",
            "persisted_event_record": compact(
                {
                    k: v for k, v in event.items()
                    if not str(k).startswith("_diagnostic_")
                },
                16000,
            ),
        }

        if source_has:
            match = INSILICO_RE.search(source_text)
            if match:
                lo = max(0, match.start() - 500)
                hi = min(len(source_text), match.end() + 1200)
                record["source_text_excerpt_around_insilico"] = source_text[lo:hi]

        insilico_rows.append(record)

        print(
            f"{event_id} | anchor={anchor_has} source={source_has} "
            f"entity={entity_has} | {classification}"
        )
        print(f"  summary: {clean(event.get('summary'))[:420]}")

    print("\nInsilico classification distribution:")
    print(" ", dict(classification_counts))

    print_header("2. EMPTY entity_mentions / EXTRACTION-BRANCH AUDIT")

    empty_analysis = analyze_empty_mentions(event_rows)
    print(
        "Empty entity_mentions: "
        f"{empty_analysis['empty_entity_mention_event_count']} / {len(event_rows)}"
    )
    print(
        "Guard applicability: "
        f"{empty_analysis['guard_applicability_distribution']}"
    )
    print("Branch signatures:")
    for key, count in sorted(
        empty_analysis["branch_signature_distribution"].items(),
        key=lambda item: (-item[1], item[0]),
    ):
        print(f"  {count:>3}  {key[:1000]}")

    persisted_evidence, evidence_db_info = locate_persisted_evidence(
        databases,
        event_ids,
    )
    report_evidence = evidence_from_report(report, event_ids)
    observed_evidence = dedupe_records(
        [*persisted_evidence, *report_evidence],
        id_keys=("evidence_id", "id"),
    )

    reported_evidence_count = int(admission.get("admitted_evidence_count") or 0)

    print_header("3. EVIDENCE-PER-EVENT DISTRIBUTION")

    observed_hist = evidence_histogram(event_ids, observed_evidence)
    print_histogram("Persisted/report evidence only", observed_hist)

    if reported_evidence_count:
        print(f"\nReport says prefilter evidence count: {reported_evidence_count}")
        print(f"Recovered raw evidence count:        {len(observed_evidence)}")

    rebuild_records: list[dict[str, Any]] = []
    rebuild_diag: dict[str, Any] = {
        "attempted": False,
        "success": False,
        "reason": "disabled",
    }

    if not args.no_evidence_rebuild:
        source_payloads = [
            normalize_source_payload(source_id, source_rows[source_id])
            for source_id in sorted(source_ids)
            if source_id in source_rows
        ]

        rebuild_records, rebuild_diag = rebuild_evidence_deterministically(
            root=root,
            topic=topic,
            events=[
                {
                    k: v for k, v in event_rows[event_id].items()
                    if not str(k).startswith("_diagnostic_")
                }
                for event_id in sorted(event_ids)
                if event_id in event_rows
            ],
            sources=source_payloads,
        )

        if rebuild_diag.get("success"):
            rebuilt_hist = evidence_histogram(event_ids, rebuild_records)
            print_histogram("Deterministic EvidenceBuilder replay", rebuilt_hist)
        else:
            rebuilt_hist = {}
            print(
                "\nDeterministic EvidenceBuilder replay unavailable: "
                f"{rebuild_diag.get('error') or rebuild_diag.get('reason')}"
            )
    else:
        rebuilt_hist = {}

    # Prefer rebuilt evidence for read-only admission snapshot when available.
    analysis_evidence = rebuild_records if rebuild_records else observed_evidence
    evidence_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in analysis_evidence:
        event_id = clean(record.get("event_id")).upper()
        if event_id:
            evidence_by_event[event_id].append(dict(record))

    print_header("4. CURRENT ADMISSION SNAPSHOT (NO POLICY CHANGE)")

    admission_replay = []
    replay_error = ""
    try:
        orch_module = import_orchestrator_module(root)

        replay_reason_counts = Counter()
        replay_admitted = 0

        for event_id in sorted(event_ids):
            event = event_rows.get(event_id)
            if not isinstance(event, Mapping):
                continue

            assessment = strategic_assessment(
                orch_module,
                topic=topic,
                event=event,
                evidence_items=evidence_by_event.get(event_id, []),
            )
            actor = current_actor(orch_module, event)

            if assessment.get("admit"):
                replay_admitted += 1
            for reason in assessment.get("reasons", []) or []:
                replay_reason_counts[clean(reason)] += 1

            admission_replay.append(
                {
                    "event_id": event_id,
                    "actor": actor,
                    "admit": bool(assessment.get("admit")),
                    "reasons": list(assessment.get("reasons") or []),
                    "event_class": assessment.get("event_class"),
                    "topic_linkage_type": assessment.get("topic_linkage_type"),
                    "relevance_score": assessment.get("relevance_score"),
                    "materiality_score": assessment.get("materiality_score"),
                    "evidence_count": len(evidence_by_event.get(event_id, [])),
                }
            )

        print(f"Replayed events:  {len(admission_replay)}")
        print(f"Admitted events:  {replay_admitted}")
        print(f"Reason counts:     {dict(replay_reason_counts)}")
    except Exception as exc:
        replay_error = f"{type(exc).__name__}: {exc}"
        print(f"Admission replay unavailable: {replay_error}")

    print_header("5. DECISION")

    if classification_counts.get("candidate_generation_recall_defect"):
        next_change = (
            "RECALL FIRST: literal Insilico exists in event anchor but is absent "
            "from entity_mentions. Fix entity candidate generation only; replay "
            "before tightening unknown-actor precision."
        )
    elif classification_counts.get("anchoring_or_segmentation_recall_defect"):
        next_change = (
            "ANCHORING FIRST: source contains Insilico but persisted event anchor "
            "does not. Fix event anchoring/segmentation before entity extraction."
        )
    elif classification_counts.get("mention_present_check_actor_resolution_or_typing"):
        next_change = (
            "ENTITY MENTION EXISTS: inspect actor typing/resolution; candidate "
            "generation is not the binding defect for those records."
        )
    else:
        next_change = (
            "NO SINGLE RECALL CAUSE PROVEN. Inspect persisted source/event provenance "
            "before changing extractor or orchestrator."
        )

    print(next_change)
    print("\nDo NOT loosen Strategic Admission or StrategicInference thresholds.")

    output_path = (root / args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output = {
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "read_only": True,
        "report_id": report_id,
        "topic": topic,
        "report_lookup": report_lookup,
        "database_candidates": [str(x) for x in databases],
        "admission_reported": {
            "admitted_event_count": admission.get("admitted_event_count"),
            "considered_event_count": admission.get("considered_event_count"),
            "excluded_event_count": admission.get("excluded_event_count"),
            "admitted_evidence_count": admission.get("admitted_evidence_count"),
            "considered_evidence_count": admission.get("considered_evidence_count"),
            "exclusion_reasons": json_safe(admission.get("exclusion_reasons") or {}),
            "considered_event_ids": sorted(considered_ids),
            "actor_missing_event_ids": sorted(actor_missing_ids),
        },
        "events": {
            "requested_count": len(event_ids),
            "persisted_count": len(event_rows),
            "missing_ids": sorted(event_ids - set(event_rows)),
            "tables": event_table_info,
        },
        "sources": {
            "requested_count": len(source_ids),
            "persisted_count": len(source_rows),
            "missing_ids": sorted(source_ids - set(source_rows)),
            "tables": source_table_info,
        },
        "insilico_recall": {
            "event_count": len(insilico_rows),
            "classification_distribution": dict(classification_counts),
            "events": insilico_rows,
        },
        "empty_entity_mentions_analysis": empty_analysis,
        "evidence": {
            "reported_prefilter_evidence_count": reported_evidence_count,
            "persisted_db_evidence_count": len(persisted_evidence),
            "report_evidence_count": len(report_evidence),
            "observed_distinct_evidence_count": len(observed_evidence),
            "observed_histogram": observed_hist,
            "evidence_db_tables": evidence_db_info,
            "deterministic_rebuild": rebuild_diag,
            "deterministic_rebuilt_histogram": rebuilt_hist,
        },
        "admission_replay": {
            "error": replay_error,
            "events": admission_replay,
        },
        "recommended_next_change": next_change,
        "guardrails": [
            "Recall defect is fixed before unknown-actor precision tightening.",
            "Unknown remains unresolved; it is not automatically actor or irrelevant.",
            "No publisher/source metadata is promoted to actor.",
            "No acronym is expanded beyond explicit persisted source support.",
            "Orchestrator remains unchanged during this diagnostic cycle.",
            "StrategicInference thresholds remain unchanged.",
        ],
    }

    output_path.write_text(
        json.dumps(json_safe(output), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print(f"\nJSON output: {output_path}")
    print("No production code or database was modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
