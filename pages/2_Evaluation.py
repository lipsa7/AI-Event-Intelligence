"""
AI Event Intelligence — Evaluation page

Place at:
    pages/2_Evaluation.py

What it does:
- reads RELEASED reports from ReportService;
- computes automatic evidence / grounding / report-structure metrics;
- stores those automatic snapshots locally;
- stores one durable human evaluation per evaluator/report/insight;
- calculates aggregate human-evaluation metrics including an explicitly scoped
  "evaluated released-report UGIR";
- exports human labels as CSV.

Important:
- citation validity != semantic support;
- human usefulness != factual evidence;
- current reports may still use paragraph-level rather than atomic sentence-level
  grounding, so this page does not claim that every sentence is independently
  supported;
- pattern-scope fields are shown automatically only if scope_validation survives
  into the persisted report;
- full research completion / abstention rates require attempted-run persistence,
  including failed and blocked runs. This page intentionally does not fabricate
  those denominators from released reports only.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import streamlit as st

from services.report_service import ReportService, ReportServiceError


PAGE_VERSION = "evaluation-page-v1.0-released-report-human-labels"
REPORT_DB_PATH = "data/reports.sqlite3"
EVALUATION_DB_PATH = "data/evaluation/evaluations.sqlite3"

GROUNDING_LABELS = (
    "supported",
    "inference_supported",
    "partially_supported",
    "unsupported",
    "contradicted",
    "unresolved",
)

CLAIM_SCOPES = (
    "entity_specific",
    "cross_entity_pattern",
    "sector_structural_pattern",
    "prospective_hypothesis",
    "background_explainer",
    "unclassified",
)

EVENT_DEPENDENCIES = (
    "necessary",
    "partial",
    "illustrative",
    "unresolved",
)

SUPPORTED_FAMILY = {"supported", "inference_supported"}
WEAK_OR_FAILED_GROUNDING = {
    "partially_supported",
    "unsupported",
    "contradicted",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def enum_text(value: Any) -> str:
    return clean_text(value).casefold().replace("-", "_").replace(" ", "_")


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    return str(value)


def stable_id(prefix: str, payload: Mapping[str, Any], length: int = 24) -> str:
    raw = json.dumps(
        json_safe(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(raw).hexdigest()[:length].upper()}"


def ratio(numerator: int, denominator: int) -> Optional[float]:
    return None if denominator <= 0 else numerator / denominator


def pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def mean(values: Sequence[float]) -> Optional[float]:
    vals = [float(v) for v in values if v is not None]
    return None if not vals else sum(vals) / len(vals)


def bool_from_choice(value: str) -> bool:
    return clean_text(value).casefold() == "yes"


def item_text(item: Any) -> str:
    if isinstance(item, str):
        return clean_text(item)
    if not isinstance(item, Mapping):
        return clean_text(item)
    for key in (
        "text",
        "insight",
        "statement",
        "description",
        "connection",
        "signal",
        "summary",
        "title",
    ):
        value = clean_text(item.get(key))
        if value:
            return value
    return ""


def evidence_ids(item: Any) -> list[str]:
    if not isinstance(item, Mapping):
        return []

    fields = (
        "evidence_ids",
        "supporting_evidence_ids",
        "support_evidence_ids",
        "counterevidence_ids",
        "counter_signal_ids",
        "references",
    )
    out: list[str] = []
    seen: set[str] = set()

    for field in fields:
        values = item.get(field)
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, (list, tuple, set)):
            continue

        for value in values:
            if isinstance(value, Mapping):
                value = value.get("evidence_id") or value.get("id") or ""
            text = clean_text(value).upper()
            if text and text not in seen:
                seen.add(text)
                out.append(text)

    return out


def report_payload(report: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = report.get("payload")
    return payload if isinstance(payload, Mapping) else {}


def report_sections(report: Mapping[str, Any]) -> Mapping[str, Any]:
    sections = report.get("sections")
    if isinstance(sections, Mapping):
        return sections

    payload = report_payload(report)
    nested = payload.get("sections")
    if isinstance(nested, Mapping):
        return nested

    # Current orchestrator payload itself contains the canonical sections.
    return payload if payload else report


def report_value(report: Mapping[str, Any], key: str, default: Any = "") -> Any:
    if key in report:
        return report.get(key, default)

    payload = report_payload(report)
    if key in payload:
        return payload.get(key, default)

    # ReportService persists run-level fields such as research_depth inside
    # metadata.  Read them here so the Evaluation page does not show
    # "unknown" when the value is actually present.
    for container in (report, payload):
        if not isinstance(container, Mapping):
            continue
        metadata = container.get("metadata")
        if isinstance(metadata, Mapping) and key in metadata:
            return metadata.get(key, default)

    sections = report_sections(report)
    if key in sections:
        return sections.get(key, default)

    metadata = sections.get("metadata") if isinstance(sections, Mapping) else None
    if isinstance(metadata, Mapping) and key in metadata:
        return metadata.get(key, default)

    return default


def mapping_value(report: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = report_value(report, key, {})
    return value if isinstance(value, Mapping) else {}


def get_report_id(report: Mapping[str, Any]) -> str:
    return clean_text(report_value(report, "report_id"))


def get_report_revision_id(report: Mapping[str, Any]) -> str:
    return clean_text(
        report_value(report, "report_revision_id")
        or report_value(report, "revision_id")
    )


def get_run_id(report: Mapping[str, Any]) -> str:
    return clean_text(report_value(report, "run_id"))


def get_research_question(report: Mapping[str, Any]) -> str:
    return clean_text(
        report_value(report, "research_question")
        or report_value(report, "topic")
        or report_value(report, "title")
    )


def insight_identity(item: Any, index: int) -> tuple[str, str]:
    if isinstance(item, Mapping):
        logical = clean_text(item.get("insight_id"))
        revision = clean_text(item.get("insight_revision_id"))
        if logical:
            return logical, revision

    logical = stable_id(
        "INSIGHT-EVAL",
        {"index": index, "text": item_text(item)},
        length=16,
    )
    return logical, ""


def get_grounding_map(report: Mapping[str, Any]) -> Mapping[str, Any]:
    for container in (report, report_payload(report), report_sections(report)):
        if not isinstance(container, Mapping):
            continue
        for key in ("grounding_by_insight", "grounding"):
            value = container.get(key)
            if isinstance(value, Mapping):
                return value
    return {}


def grounding_statuses(report: Mapping[str, Any]) -> list[str]:
    statuses: list[str] = []

    for raw in get_grounding_map(report).values():
        if not isinstance(raw, Mapping):
            continue

        claims = raw.get("claims")
        if isinstance(claims, list) and claims:
            for claim in claims:
                if not isinstance(claim, Mapping):
                    continue
                status = enum_text(
                    claim.get("grounding_status")
                    or claim.get("epistemic_status")
                    or claim.get("status")
                )
                if status:
                    statuses.append(status)
            continue

        status = enum_text(
            raw.get("grounding_status")
            or raw.get("epistemic_status")
            or raw.get("status")
        )
        if status:
            statuses.append(status)

    return statuses


def scope_validation_from_item(item: Any) -> Mapping[str, Any]:
    if not isinstance(item, Mapping):
        return {}

    direct = item.get("scope_validation")
    if isinstance(direct, Mapping):
        return direct

    metadata = item.get("metadata")
    if isinstance(metadata, Mapping):
        nested = metadata.get("scope_validation")
        if isinstance(nested, Mapping):
            return nested

    return {}


def collect_analytical_items(sections: Mapping[str, Any]) -> list[tuple[str, Any]]:
    fields = (
        "connections",
        "what_is_happening_underneath",
        "hidden_insights",
        "second_order_effects",
        "counterevidence_and_alternative_explanations",
        "what_to_watch",
    )
    out: list[tuple[str, Any]] = []
    for field in fields:
        for item in as_list(sections.get(field)):
            if item_text(item):
                out.append((field, item))
    return out


def calculate_report_metrics(report: Mapping[str, Any]) -> dict[str, Any]:
    sections = report_sections(report)

    event_landscape = as_list(sections.get("event_landscape"))
    hidden = as_list(sections.get("hidden_insights"))
    connections = as_list(sections.get("connections"))
    underlying = as_list(sections.get("what_is_happening_underneath"))
    second_order = as_list(sections.get("second_order_effects"))
    alternatives = as_list(
        sections.get("counterevidence_and_alternative_explanations")
        or sections.get("counterevidence_and_alternatives")
    )
    watch = as_list(sections.get("what_to_watch"))
    unknowns = as_list(sections.get("critical_unknowns"))
    trail = as_list(sections.get("evidence_trail"))

    inventory_ids: set[str] = set()
    source_ids: set[str] = set()
    evidence_event_ids: set[str] = set()

    for item in trail:
        if not isinstance(item, Mapping):
            continue
        evidence_id = clean_text(item.get("evidence_id") or item.get("id")).upper()
        source_id = clean_text(item.get("source_id"))
        event_id = clean_text(item.get("event_id"))
        if evidence_id:
            inventory_ids.add(evidence_id)
        if source_id:
            source_ids.add(source_id)
        if event_id:
            evidence_event_ids.add(event_id)

    analytical_items = collect_analytical_items(sections)
    referenced_ids: set[str] = set()
    items_with_evidence = 0

    for _, item in analytical_items:
        ids = evidence_ids(item)
        if ids:
            items_with_evidence += 1
            referenced_ids.update(ids)

    invalid_ids = sorted(
        referenced_ids - inventory_ids if inventory_ids else set()
    )

    statuses = grounding_statuses(report)
    grounding_counts = Counter(statuses)

    falsifiable_watch = 0
    for item in watch:
        if not isinstance(item, Mapping):
            continue
        if clean_text(item.get("confirming_condition")) and clean_text(
            item.get("disconfirming_condition")
        ):
            falsifiable_watch += 1

    scope_summaries: list[dict[str, Any]] = []
    for item in hidden:
        scope = scope_validation_from_item(item)
        if not scope:
            continue
        scope_summaries.append(
            {
                "claim_scope": enum_text(scope.get("claim_scope")),
                "pattern_status": enum_text(scope.get("pattern_status")),
                "distinct_actor_count": int(scope.get("distinct_actor_count") or 0),
                "distinct_event_count": int(scope.get("distinct_event_count") or 0),
                "generalization_allowed": bool(scope.get("generalization_allowed")),
                "event_dependency": enum_text(scope.get("event_dependency")),
                "could_be_written_without_events": bool(
                    scope.get("could_be_written_without_events")
                ),
            }
        )

    analysis_metadata = mapping_value(report, "analysis_metadata")
    metadata = mapping_value(report, "metadata")
    depth = enum_text(
        report_value(report, "research_depth")
        or analysis_metadata.get("analysis_mode")
        or metadata.get("research_depth")
        or metadata.get("desired_depth")
    )

    return {
        "report_id": get_report_id(report),
        "report_revision_id": get_report_revision_id(report),
        "run_id": get_run_id(report),
        "research_question": get_research_question(report),
        "research_depth": depth or "unknown",
        "release_state": enum_text(report_value(report, "state") or "released"),
        "event_landscape_count": len(event_landscape),
        "evidence_record_count": len(trail),
        "distinct_source_count": len(source_ids),
        "distinct_evidence_event_count": len(evidence_event_ids),
        "hidden_insight_count": len(hidden),
        "connection_count": len(connections),
        "underlying_dynamic_count": len(underlying),
        "second_order_effect_count": len(second_order),
        "alternative_explanation_count": len(alternatives),
        "watch_signal_count": len(watch),
        "falsifiable_watch_count": falsifiable_watch,
        "critical_unknown_count": len(unknowns),
        "analytical_item_count": len(analytical_items),
        "analytical_items_with_evidence": items_with_evidence,
        "evidence_attachment_rate": ratio(items_with_evidence, len(analytical_items)),
        "evidence_inventory_id_count": len(inventory_ids),
        "unique_referenced_evidence_id_count": len(referenced_ids),
        "valid_referenced_evidence_id_count": len(referenced_ids & inventory_ids),
        "invalid_evidence_reference_count": len(invalid_ids),
        "invalid_evidence_reference_ids": invalid_ids,
        "evidence_reference_validity_rate": (
            ratio(len(referenced_ids & inventory_ids), len(referenced_ids))
            if referenced_ids
            else None
        ),
        "grounding_claim_count": len(statuses),
        "grounding_status_counts": dict(sorted(grounding_counts.items())),
        "supported_family_grounding_count": sum(
            grounding_counts.get(status, 0) for status in SUPPORTED_FAMILY
        ),
        "weak_or_failed_grounding_count": sum(
            grounding_counts.get(status, 0) for status in WEAK_OR_FAILED_GROUNDING
        ),
        "scope_metadata_available": bool(scope_summaries),
        "scope_summaries": scope_summaries,
        "grounding_granularity_note": (
            "Current counts reflect claim objects persisted by the pipeline. "
            "Until atomic sentence-level grounding is wired end-to-end, do not "
            "interpret this as proof that every sentence is independently supported."
        ),
        "computed_at": utc_now_iso(),
        "metrics_version": PAGE_VERSION,
    }


class EvaluationStore:
    def __init__(self, db_path: str = EVALUATION_DB_PATH) -> None:
        self.db_path = clean_text(db_path) or ":memory:"
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS automatic_report_metrics(
                report_id TEXT PRIMARY KEY,
                report_revision_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                research_question TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                computed_at TEXT NOT NULL
            )
            """
        )

        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS human_insight_evaluations(
                evaluation_id TEXT PRIMARY KEY,
                evaluator_id TEXT NOT NULL,
                report_id TEXT NOT NULL,
                report_revision_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                research_question TEXT NOT NULL,
                insight_id TEXT NOT NULL,
                insight_revision_id TEXT NOT NULL,
                insight_text TEXT NOT NULL,
                usefulness INTEGER NOT NULL,
                specificity INTEGER NOT NULL,
                non_obviousness INTEGER NOT NULL,
                mechanism_quality INTEGER NOT NULL,
                epistemic_discipline INTEGER NOT NULL,
                counterevidence_quality INTEGER NOT NULL,
                second_order_value INTEGER NOT NULL,
                grounding_label TEXT NOT NULL,
                claim_scope TEXT NOT NULL,
                scope_correct INTEGER NOT NULL,
                could_be_written_without_events INTEGER NOT NULL,
                event_dependency TEXT NOT NULL,
                unsupported_material_claim_count INTEGER NOT NULL,
                executive_ready INTEGER NOT NULL,
                notes TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(evaluator_id, report_id, insight_id)
            )
            """
        )

        self._db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_human_eval_report
            ON human_insight_evaluations(report_id, insight_id)
            """
        )
        self._db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_human_eval_evaluator
            ON human_insight_evaluations(evaluator_id, updated_at)
            """
        )
        self._db.commit()

    def upsert_automatic_metrics(self, metrics: Mapping[str, Any]) -> None:
        rid = clean_text(metrics.get("report_id"))
        if not rid:
            return
        self._db.execute(
            """
            INSERT INTO automatic_report_metrics(
                report_id, report_revision_id, run_id,
                research_question, metrics_json, computed_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_id) DO UPDATE SET
                report_revision_id=excluded.report_revision_id,
                run_id=excluded.run_id,
                research_question=excluded.research_question,
                metrics_json=excluded.metrics_json,
                computed_at=excluded.computed_at
            """,
            (
                rid,
                clean_text(metrics.get("report_revision_id")),
                clean_text(metrics.get("run_id")),
                clean_text(metrics.get("research_question")),
                json.dumps(json_safe(metrics), ensure_ascii=False, sort_keys=True),
                clean_text(metrics.get("computed_at")) or utc_now_iso(),
            ),
        )
        self._db.commit()

    def get_human_evaluation(
        self,
        evaluator_id: str,
        report_id_value: str,
        insight_id_value: str,
    ) -> Optional[dict[str, Any]]:
        row = self._db.execute(
            """
            SELECT * FROM human_insight_evaluations
            WHERE evaluator_id=? AND report_id=? AND insight_id=?
            LIMIT 1
            """,
            (
                clean_text(evaluator_id),
                clean_text(report_id_value),
                clean_text(insight_id_value),
            ),
        ).fetchone()
        return dict(row) if row else None

    def upsert_human_evaluation(
        self,
        *,
        evaluator_id: str,
        report: Mapping[str, Any],
        insight_id_value: str,
        insight_revision_id_value: str,
        insight_text_value: str,
        usefulness: int,
        specificity: int,
        non_obviousness: int,
        mechanism_quality: int,
        epistemic_discipline: int,
        counterevidence_quality: int,
        second_order_value: int,
        grounding_label: str,
        claim_scope: str,
        scope_correct: bool,
        could_be_written_without_events: bool,
        event_dependency: str,
        unsupported_material_claim_count: int,
        executive_ready: bool,
        notes: str,
    ) -> dict[str, Any]:
        evaluator_id = clean_text(evaluator_id) or "human_primary"
        rid = get_report_id(report)
        iid = clean_text(insight_id_value)
        if not rid or not iid:
            raise ValueError("report_id and insight_id are required")

        existing = self.get_human_evaluation(evaluator_id, rid, iid) or {}
        now = utc_now_iso()
        record = {
            "evaluation_id": stable_id(
                "EVAL",
                {"evaluator_id": evaluator_id, "report_id": rid, "insight_id": iid},
            ),
            "evaluator_id": evaluator_id,
            "report_id": rid,
            "report_revision_id": get_report_revision_id(report),
            "run_id": get_run_id(report),
            "research_question": get_research_question(report),
            "insight_id": iid,
            "insight_revision_id": clean_text(insight_revision_id_value),
            "insight_text": clean_text(insight_text_value),
            "usefulness": int(usefulness),
            "specificity": int(specificity),
            "non_obviousness": int(non_obviousness),
            "mechanism_quality": int(mechanism_quality),
            "epistemic_discipline": int(epistemic_discipline),
            "counterevidence_quality": int(counterevidence_quality),
            "second_order_value": int(second_order_value),
            "grounding_label": enum_text(grounding_label),
            "claim_scope": enum_text(claim_scope),
            "scope_correct": 1 if scope_correct else 0,
            "could_be_written_without_events": (
                1 if could_be_written_without_events else 0
            ),
            "event_dependency": enum_text(event_dependency),
            "unsupported_material_claim_count": max(
                0, int(unsupported_material_claim_count)
            ),
            "executive_ready": 1 if executive_ready else 0,
            "notes": str(notes or ""),
            "created_at": clean_text(existing.get("created_at")) or now,
            "updated_at": now,
        }

        self._db.execute(
            """
            INSERT INTO human_insight_evaluations(
                evaluation_id, evaluator_id, report_id, report_revision_id,
                run_id, research_question, insight_id, insight_revision_id,
                insight_text, usefulness, specificity, non_obviousness,
                mechanism_quality, epistemic_discipline, counterevidence_quality,
                second_order_value, grounding_label, claim_scope, scope_correct,
                could_be_written_without_events, event_dependency,
                unsupported_material_claim_count, executive_ready, notes,
                created_at, updated_at
            )
            VALUES (
                :evaluation_id, :evaluator_id, :report_id, :report_revision_id,
                :run_id, :research_question, :insight_id, :insight_revision_id,
                :insight_text, :usefulness, :specificity, :non_obviousness,
                :mechanism_quality, :epistemic_discipline, :counterevidence_quality,
                :second_order_value, :grounding_label, :claim_scope, :scope_correct,
                :could_be_written_without_events, :event_dependency,
                :unsupported_material_claim_count, :executive_ready, :notes,
                :created_at, :updated_at
            )
            ON CONFLICT(evaluator_id, report_id, insight_id) DO UPDATE SET
                report_revision_id=excluded.report_revision_id,
                run_id=excluded.run_id,
                research_question=excluded.research_question,
                insight_revision_id=excluded.insight_revision_id,
                insight_text=excluded.insight_text,
                usefulness=excluded.usefulness,
                specificity=excluded.specificity,
                non_obviousness=excluded.non_obviousness,
                mechanism_quality=excluded.mechanism_quality,
                epistemic_discipline=excluded.epistemic_discipline,
                counterevidence_quality=excluded.counterevidence_quality,
                second_order_value=excluded.second_order_value,
                grounding_label=excluded.grounding_label,
                claim_scope=excluded.claim_scope,
                scope_correct=excluded.scope_correct,
                could_be_written_without_events=excluded.could_be_written_without_events,
                event_dependency=excluded.event_dependency,
                unsupported_material_claim_count=excluded.unsupported_material_claim_count,
                executive_ready=excluded.executive_ready,
                notes=excluded.notes,
                updated_at=excluded.updated_at
            """,
            record,
        )
        self._db.commit()
        return record

    def list_human_evaluations(self, evaluator_id: str = "") -> list[dict[str, Any]]:
        if clean_text(evaluator_id):
            rows = self._db.execute(
                """
                SELECT * FROM human_insight_evaluations
                WHERE evaluator_id=? ORDER BY updated_at DESC
                """,
                (clean_text(evaluator_id),),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM human_insight_evaluations ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]


def calculate_human_aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(row) for row in rows]
    if not rows:
        return {
            "evaluated_insight_count": 0,
            "evaluated_report_count": 0,
            "avg_usefulness": None,
            "avg_specificity": None,
            "avg_non_obviousness": None,
            "avg_mechanism_quality": None,
            "avg_epistemic_discipline": None,
            "scope_correct_rate": None,
            "genericity_failure_rate": None,
            "executive_ready_rate": None,
            "grounded_family_rate": None,
            "unsupported_material_claim_total": 0,
            "evaluated_released_report_ugir": None,
            "ugir_numerator": 0,
            "ugir_denominator": 0,
        }

    by_report: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        rid = clean_text(row.get("report_id"))
        if rid:
            by_report[rid].append(row)

    qualifying_reports = 0
    for items in by_report.values():
        if any(
            int(item.get("usefulness") or 0) >= 4
            and enum_text(item.get("grounding_label")) in SUPPORTED_FAMILY
            and bool(item.get("scope_correct"))
            and not bool(item.get("could_be_written_without_events"))
            and enum_text(item.get("event_dependency")) == "necessary"
            and int(item.get("unsupported_material_claim_count") or 0) == 0
            and bool(item.get("executive_ready"))
            for item in items
        ):
            qualifying_reports += 1

    denominator = len(by_report)
    return {
        "evaluated_insight_count": len(rows),
        "evaluated_report_count": denominator,
        "avg_usefulness": mean([int(r["usefulness"]) for r in rows]),
        "avg_specificity": mean([int(r["specificity"]) for r in rows]),
        "avg_non_obviousness": mean([int(r["non_obviousness"]) for r in rows]),
        "avg_mechanism_quality": mean([int(r["mechanism_quality"]) for r in rows]),
        "avg_epistemic_discipline": mean(
            [int(r["epistemic_discipline"]) for r in rows]
        ),
        "scope_correct_rate": ratio(
            sum(1 for r in rows if bool(r.get("scope_correct"))), len(rows)
        ),
        "genericity_failure_rate": ratio(
            sum(1 for r in rows if bool(r.get("could_be_written_without_events"))),
            len(rows),
        ),
        "executive_ready_rate": ratio(
            sum(1 for r in rows if bool(r.get("executive_ready"))), len(rows)
        ),
        "grounded_family_rate": ratio(
            sum(
                1
                for r in rows
                if enum_text(r.get("grounding_label")) in SUPPORTED_FAMILY
            ),
            len(rows),
        ),
        "unsupported_material_claim_total": sum(
            int(r.get("unsupported_material_claim_count") or 0) for r in rows
        ),
        "evaluated_released_report_ugir": ratio(qualifying_reports, denominator),
        "ugir_numerator": qualifying_reports,
        "ugir_denominator": denominator,
    }


def rows_to_csv(rows: Sequence[Mapping[str, Any]]) -> str:
    rows = [dict(row) for row in rows]
    if not rows:
        return ""

    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)

    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


@st.cache_resource(show_spinner=False)
def get_report_service() -> ReportService:
    return ReportService(db_path=REPORT_DB_PATH)


@st.cache_resource(show_spinner=False)
def get_evaluation_store() -> EvaluationStore:
    return EvaluationStore(db_path=EVALUATION_DB_PATH)


def report_label(report: Mapping[str, Any]) -> str:
    question = get_research_question(report) or "Untitled research"
    rid = get_report_id(report)
    depth = enum_text(report_value(report, "research_depth")) or "?"
    return f"{question[:90]} · {depth} · {rid[-10:]}"


def render_automatic_metrics(metrics: Mapping[str, Any]) -> None:
    st.subheader("Automatic Run / AI Quality Metrics")
    st.caption(
        "Computed from the persisted released report. Citation presence and ID "
        "validity are deterministic; semantic truth still depends on grounding "
        "and human evaluation."
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Evidence records", int(metrics.get("evidence_record_count") or 0))
    c2.metric("Distinct sources", int(metrics.get("distinct_source_count") or 0))
    c3.metric(
        "Evidence-linked events",
        int(metrics.get("distinct_evidence_event_count") or 0),
    )
    c4.metric("Citation coverage", pct(metrics.get("evidence_attachment_rate")))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Grounding claims", int(metrics.get("grounding_claim_count") or 0))
    c2.metric(
        "Supported family",
        int(metrics.get("supported_family_grounding_count") or 0),
    )
    c3.metric(
        "Weak / failed grounding",
        int(metrics.get("weak_or_failed_grounding_count") or 0),
    )
    c4.metric(
        "Invalid evidence refs",
        int(metrics.get("invalid_evidence_reference_count") or 0),
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Hidden insights", int(metrics.get("hidden_insight_count") or 0))
    c2.metric("Connections", int(metrics.get("connection_count") or 0))
    c3.metric(
        "Second-order effects",
        int(metrics.get("second_order_effect_count") or 0),
    )
    c4.metric(
        "Falsifiable watch signals",
        int(metrics.get("falsifiable_watch_count") or 0),
    )

    with st.expander("Grounding distribution"):
        counts = metrics.get("grounding_status_counts") or {}
        if counts:
            st.json(dict(counts))
        else:
            st.caption("No persisted grounding claim records were found.")
        st.caption(clean_text(metrics.get("grounding_granularity_note")))

    with st.expander("Evidence integrity"):
        st.json(
            {
                "analytical_item_count": metrics.get("analytical_item_count"),
                "analytical_items_with_evidence": metrics.get(
                    "analytical_items_with_evidence"
                ),
                "evidence_attachment_rate": metrics.get("evidence_attachment_rate"),
                "unique_referenced_evidence_id_count": metrics.get(
                    "unique_referenced_evidence_id_count"
                ),
                "valid_referenced_evidence_id_count": metrics.get(
                    "valid_referenced_evidence_id_count"
                ),
                "invalid_evidence_reference_ids": metrics.get(
                    "invalid_evidence_reference_ids"
                ),
            }
        )

    with st.expander("Pattern-scope metadata"):
        if metrics.get("scope_metadata_available"):
            st.json(metrics.get("scope_summaries") or [])
        else:
            st.warning(
                "The current final report does not preserve scope_validation "
                "metadata. Pattern scope / actor diversity therefore remains a "
                "human label here until the report builder persists those fields."
            )


def render_human_form(
    report: Mapping[str, Any],
    store: EvaluationStore,
    evaluator_id: str,
) -> None:
    hidden = [
        item
        for item in as_list(report_sections(report).get("hidden_insights"))
        if item_text(item)
    ]

    st.subheader("Human Insight Evaluation")
    st.caption(
        "Use this for what the system cannot honestly self-score: usefulness, "
        "non-obviousness, scope correctness, event dependency and unsupported "
        "material claims."
    )

    if not hidden:
        st.info("This released report contains no Hidden Insight to evaluate.")
        return

    options: list[str] = []
    identities: list[tuple[str, str, Any]] = []
    for index, item in enumerate(hidden, start=1):
        iid, irev = insight_identity(item, index)
        identities.append((iid, irev, item))
        options.append(f"{index}. {item_text(item)[:120]}")

    selected = st.selectbox(
        "Insight to evaluate",
        options=options,
        key=f"eval_insight_select::{get_report_id(report)}",
    )
    iid, irev, item = identities[options.index(selected)]
    text = item_text(item)

    st.markdown("**Insight being evaluated**")
    st.write(text)

    existing = store.get_human_evaluation(
        evaluator_id,
        get_report_id(report),
        iid,
    ) or {}

    def old_score(key: str) -> int:
        try:
            return min(5, max(1, int(existing.get(key, 3))))
        except (TypeError, ValueError):
            return 3

    grounding = enum_text(existing.get("grounding_label")) or "unresolved"
    if grounding not in GROUNDING_LABELS:
        grounding = "unresolved"

    scope = enum_text(existing.get("claim_scope")) or "unclassified"
    if scope not in CLAIM_SCOPES:
        scope = "unclassified"

    dependency = enum_text(existing.get("event_dependency")) or "unresolved"
    if dependency not in EVENT_DEPENDENCIES:
        dependency = "unresolved"

    with st.form(
        f"human_eval_form::{get_report_id(report)}::{iid}",
        clear_on_submit=False,
    ):
        st.markdown("##### Value / reasoning quality")
        c1, c2 = st.columns(2)
        usefulness = c1.slider("Usefulness", 1, 5, old_score("usefulness"))
        specificity = c2.slider("Specificity", 1, 5, old_score("specificity"))

        c1, c2 = st.columns(2)
        non_obviousness = c1.slider(
            "Non-obviousness", 1, 5, old_score("non_obviousness")
        )
        mechanism_quality = c2.slider(
            "Mechanism quality", 1, 5, old_score("mechanism_quality")
        )

        c1, c2 = st.columns(2)
        epistemic_discipline = c1.slider(
            "Epistemic discipline", 1, 5, old_score("epistemic_discipline")
        )
        counterevidence_quality = c2.slider(
            "Counterevidence quality", 1, 5, old_score("counterevidence_quality")
        )

        second_order_value = st.slider(
            "Second-order value", 1, 5, old_score("second_order_value")
        )

        st.markdown("##### Grounding / scope")
        c1, c2 = st.columns(2)
        grounding_label = c1.selectbox(
            "Human grounding label",
            list(GROUNDING_LABELS),
            index=list(GROUNDING_LABELS).index(grounding),
        )
        claim_scope = c2.selectbox(
            "Correct claim scope",
            list(CLAIM_SCOPES),
            index=list(CLAIM_SCOPES).index(scope),
        )

        c1, c2 = st.columns(2)
        scope_correct_choice = c1.radio(
            "Is the scope/generalization justified?",
            ["Yes", "No"],
            index=0 if bool(existing.get("scope_correct", 0)) else 1,
            horizontal=True,
        )
        genericity_choice = c2.radio(
            "Could essentially this be written without these events?",
            ["Yes", "No"],
            index=(
                0
                if bool(existing.get("could_be_written_without_events", 0))
                else 1
            ),
            horizontal=True,
        )

        event_dependency = st.selectbox(
            "How necessary are the supplied events to deriving the insight?",
            list(EVENT_DEPENDENCIES),
            index=list(EVENT_DEPENDENCIES).index(dependency),
        )

        unsupported_count = st.number_input(
            "Unsupported material claim count",
            min_value=0,
            max_value=100,
            value=max(
                0,
                int(existing.get("unsupported_material_claim_count", 0) or 0),
            ),
            step=1,
        )

        executive_ready_choice = st.radio(
            "Would you show this to a decision-maker in its current form?",
            ["Yes", "No"],
            index=0 if bool(existing.get("executive_ready", 0)) else 1,
            horizontal=True,
        )

        notes = st.text_area(
            "Evaluator notes",
            value=str(existing.get("notes", "") or ""),
            placeholder=(
                "Example: useful mechanism, but the sector-wide conclusion is "
                "not justified by one-company evidence."
            ),
            height=120,
        )

        submitted = st.form_submit_button("Save human evaluation")

    if submitted:
        record = store.upsert_human_evaluation(
            evaluator_id=evaluator_id,
            report=report,
            insight_id_value=iid,
            insight_revision_id_value=irev,
            insight_text_value=text,
            usefulness=usefulness,
            specificity=specificity,
            non_obviousness=non_obviousness,
            mechanism_quality=mechanism_quality,
            epistemic_discipline=epistemic_discipline,
            counterevidence_quality=counterevidence_quality,
            second_order_value=second_order_value,
            grounding_label=grounding_label,
            claim_scope=claim_scope,
            scope_correct=bool_from_choice(scope_correct_choice),
            could_be_written_without_events=bool_from_choice(genericity_choice),
            event_dependency=event_dependency,
            unsupported_material_claim_count=int(unsupported_count),
            executive_ready=bool_from_choice(executive_ready_choice),
            notes=notes,
        )
        st.success(f"Saved evaluation `{record['evaluation_id']}`.")
        st.rerun()


def render_aggregate(store: EvaluationStore, evaluator_id: str) -> None:
    rows = store.list_human_evaluations(evaluator_id)
    agg = calculate_human_aggregate(rows)

    st.subheader("Human Evaluation Summary")
    st.caption(
        "UGIR here means evaluated RELEASED-report UGIR. Its denominator is "
        "evaluated released reports, not every attempted run."
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Evaluated insights", agg["evaluated_insight_count"])
    c2.metric("Evaluated reports", agg["evaluated_report_count"])
    c3.metric(
        "Evaluated released-report UGIR",
        pct(agg["evaluated_released_report_ugir"]),
    )
    c4.metric("Scope-correct rate", pct(agg["scope_correct_rate"]))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Avg usefulness",
        "—" if agg["avg_usefulness"] is None else f"{agg['avg_usefulness']:.2f}/5",
    )
    c2.metric(
        "Avg non-obviousness",
        (
            "—"
            if agg["avg_non_obviousness"] is None
            else f"{agg['avg_non_obviousness']:.2f}/5"
        ),
    )
    c3.metric("Grounded-family rate", pct(agg["grounded_family_rate"]))
    c4.metric("Genericity failure rate", pct(agg["genericity_failure_rate"]))

    c1, c2, c3 = st.columns(3)
    c1.metric("Executive-ready rate", pct(agg["executive_ready_rate"]))
    c2.metric(
        "Unsupported material claims",
        agg["unsupported_material_claim_total"],
    )
    c3.metric(
        "UGIR numerator / denominator",
        f"{agg['ugir_numerator']} / {agg['ugir_denominator']}",
    )

    if rows:
        display = [
            {
                "report_id": r.get("report_id"),
                "insight_id": r.get("insight_id"),
                "usefulness": r.get("usefulness"),
                "specificity": r.get("specificity"),
                "non_obviousness": r.get("non_obviousness"),
                "mechanism_quality": r.get("mechanism_quality"),
                "epistemic_discipline": r.get("epistemic_discipline"),
                "grounding_label": r.get("grounding_label"),
                "claim_scope": r.get("claim_scope"),
                "scope_correct": bool(r.get("scope_correct")),
                "generic_without_events": bool(
                    r.get("could_be_written_without_events")
                ),
                "event_dependency": r.get("event_dependency"),
                "unsupported_claims": r.get("unsupported_material_claim_count"),
                "executive_ready": bool(r.get("executive_ready")),
                "updated_at": r.get("updated_at"),
            }
            for r in rows
        ]
        st.dataframe(display, use_container_width=True, hide_index=True)

        st.download_button(
            "Export human evaluation CSV",
            data=rows_to_csv(rows),
            file_name="event_intelligence_human_evaluations.csv",
            mime="text/csv",
        )


def main() -> None:
    st.set_page_config(
        page_title="AI Event Intelligence · Evaluation",
        page_icon="📊",
        layout="wide",
    )

    st.title("Evaluation & Run Metrics")
    st.caption(
        "Automatic evidence/grounding diagnostics + durable human labels for "
        "usefulness, non-obviousness, scope and event dependency."
    )

    report_service = get_report_service()
    store = get_evaluation_store()

    evaluator_id = clean_text(
        st.sidebar.text_input(
            "Evaluator ID",
            value="human_primary",
            help="Keep this stable so repeat reviews update the same labels.",
        )
    ) or "human_primary"

    try:
        reports = list(report_service.list_reports(state="released") or [])
    except ReportServiceError as exc:
        st.error("Could not load released reports: " + clean_text(exc))
        return

    reports = [r for r in reports if isinstance(r, Mapping)]
    if not reports:
        st.info("No released reports are available yet.")
        render_aggregate(store, evaluator_id)
        return

    by_label: dict[str, Mapping[str, Any]] = {}
    for report in reports:
        label = report_label(report)
        if label in by_label:
            label += " · " + get_report_id(report)
        by_label[label] = report

    selected = st.selectbox("Released report", list(by_label))
    report = by_label[selected]

    c1, c2, c3, c4 = st.columns(4)
    c1.write("**Report ID**")
    c1.code(get_report_id(report) or "unknown")
    c2.write("**Run ID**")
    c2.code(get_run_id(report) or "unknown")
    c3.write("**Depth**")
    c3.code(enum_text(report_value(report, "research_depth")) or "unknown")
    c4.write("**State**")
    c4.code(enum_text(report_value(report, "state")) or "released")

    st.markdown("**Research question**")
    st.write(get_research_question(report) or "Unknown")

    metrics = calculate_report_metrics(report)
    store.upsert_automatic_metrics(metrics)

    render_automatic_metrics(metrics)
    st.divider()
    render_human_form(report, store, evaluator_id)
    st.divider()
    render_aggregate(store, evaluator_id)

    with st.sidebar.expander("Measurement boundaries"):
        st.caption(
            "Automatic metrics are report-derived diagnostics, not a replacement "
            "for a labelled evaluation set."
        )
        st.caption(
            "Human labels are stored in data/evaluation/evaluations.sqlite3."
        )
        st.caption(
            "Full completion and abstention rates need attempted-run records, "
            "including failed and blocked runs."
        )
        st.caption(f"Page version: `{PAGE_VERSION}`")


if __name__ == "__main__":
    main()
