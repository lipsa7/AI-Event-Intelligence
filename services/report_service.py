"""
Durable structured research-report service.

Recommended location:
    services/report_service.py

Role
----
Final reasoning + grounding + red-team outputs
        ↓
ReportService
        ↓
versioned structured report
        ↓
PolicyEnforcementService.validate_release(...)
        ↓
released / blocked report state
        ↓
Streamlit UI + AuditReceiptService

ReportService stores report artifacts. It does NOT decide whether a report is
releaseable. A report may move to state="released" only when the caller supplies
a PolicyEnforcementService release decision with authorizes_release=True.

The report unit is the research topic/question, not an article.

Canonical report sections:
- event_landscape
- connections
- what_is_happening_underneath
- hidden_insights
- second_order_effects
- counterevidence_and_alternative_explanations
- what_to_watch
- evidence_trail
- critical_unknowns

Every report revision is immutable. The logical REPORT-* record points to the
current REPORTREV-* revision.

No LLM, search, grounding, red-team or private chain-of-thought storage occurs
inside this service.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence


SERVICE_VERSION = "report-service-v2.0"
SCHEMA_VERSION = "report-schema-v2.0"

REPORT_STATES = {"draft", "blocked", "released", "superseded", "archived"}

CANONICAL_SECTIONS = (
    "event_landscape",
    "connections",
    "what_is_happening_underneath",
    "hidden_insights",
    "second_order_effects",
    "counterevidence_and_alternative_explanations",
    "what_to_watch",
    "evidence_trail",
    "critical_unknowns",
)

PROHIBITED_PRIVATE_REASONING_FIELDS = {
    "chain_of_thought", "chainofthought", "cot", "scratchpad",
    "hidden_reasoning", "hiddenreasoning", "internal_monologue",
    "internalmonologue", "raw_reasoning", "rawreasoning",
    "private_reasoning", "privatereasoning", "reasoning_tokens",
    "reasoningtokens", "deliberation", "deliberation_trace",
    "deliberationtrace", "raw_prompt", "rawprompt", "raw_completion",
    "rawcompletion", "raw_response", "rawresponse",
}


class ReportServiceError(RuntimeError):
    pass


class ReportValidationError(ReportServiceError):
    pass


class ReportConflictError(ReportServiceError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return "" if value is None else " ".join(str(value).split()).strip()


def _enum(value: Any) -> str:
    text = _clean(value).casefold().replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "", text)
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _sha(value: Any) -> str:
    raw = json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _stable_id(prefix: str, payload: Any, length: int = 24) -> str:
    return f"{prefix.upper()}-{_sha(payload)[:length].upper()}"


def _private_path(value: Any, path: str = "") -> Optional[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            current = f"{path}.{key}" if path else str(key)
            if _enum(key) in PROHIBITED_PRIVATE_REASONING_FIELDS:
                return current
            nested = _private_path(item, current)
            if nested:
                return nested
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            nested = _private_path(item, f"{path}[{i}]")
            if nested:
                return nested
    return None


def _ids(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return []
    out = []
    for item in values:
        if isinstance(item, Mapping):
            item = (
                item.get("evidence_id")
                or item.get("insight_id")
                or item.get("insight_revision_id")
                or item.get("id")
                or ""
            )
        text = _clean(item)
        if text and text not in out:
            out.append(text)
    return out


def _collect_evidence_ids(value: Any) -> list[str]:
    found = []

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, item in node.items():
                normalized = _enum(key)
                if normalized in {
                    "evidence_ids",
                    "supporting_evidence_ids",
                    "counterevidence_ids",
                    "analogical_evidence_ids",
                    "feasibility_evidence_ids",
                    "counter_incentive_evidence_ids",
                }:
                    for evidence_id in _ids(item):
                        if evidence_id not in found:
                            found.append(evidence_id)
                else:
                    visit(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                visit(item)

    visit(value)
    return found


class ReportService:
    def __init__(
        self,
        *,
        db_path: str = "data/reports.sqlite3",
        clock: Any = _now,
    ) -> None:
        if not callable(clock):
            raise ReportValidationError("clock must be callable.")

        self.clock = clock
        self.db_path = _clean(db_path) or ":memory:"

        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")

        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS reports(
                report_id TEXT PRIMARY KEY,
                research_question TEXT NOT NULL,
                current_revision_id TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS report_revisions(
                report_revision_id TEXT PRIMARY KEY,
                report_id TEXT NOT NULL,
                revision_no INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                report_fingerprint TEXT NOT NULL,
                release_decision_id TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(report_id) REFERENCES reports(report_id),
                UNIQUE(report_id, revision_no)
            )
            """
        )
        self._db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_report_revisions_report
            ON report_revisions(report_id, revision_no)
            """
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "ReportService":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def create_report(
        self,
        *,
        research_question: str,
        sections: Mapping[str, Any],
        report_id: str = "",
        run_id: str = "",
        topic_id: str = "",
        cluster_ids: Sequence[str] = (),
        insight_ids: Sequence[str] = (),
        insight_revision_ids: Sequence[str] = (),
        blueprint_id: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        research_question = _clean(research_question)
        if not research_question:
            raise ReportValidationError("research_question cannot be empty.")

        normalized_sections = self._normalize_sections(sections)
        payload = self._build_payload(
            research_question=research_question,
            sections=normalized_sections,
            run_id=run_id,
            topic_id=topic_id,
            cluster_ids=cluster_ids,
            insight_ids=insight_ids,
            insight_revision_ids=insight_revision_ids,
            blueprint_id=blueprint_id,
            metadata=metadata,
        )

        report_id = _clean(report_id) or _stable_id(
            "REPORT",
            {
                "research_question": research_question,
                "topic_id": _clean(topic_id),
                "run_id": _clean(run_id),
            },
        )

        if self._db.execute(
            "SELECT 1 FROM reports WHERE report_id=?",
            (report_id,),
        ).fetchone():
            raise ReportConflictError(f"report_id already exists: {report_id}")

        revision_no = 1
        revision_id = self._revision_id(report_id, revision_no, payload)
        fingerprint = _sha(payload)
        now = self.clock()

        self._db.execute(
            """
            INSERT INTO reports(
                report_id, research_question, current_revision_id,
                state, created_at, updated_at
            )
            VALUES (?, ?, ?, 'draft', ?, ?)
            """,
            (report_id, research_question, revision_id, now, now),
        )
        self._db.execute(
            """
            INSERT INTO report_revisions(
                report_revision_id, report_id, revision_no, payload_json,
                report_fingerprint, release_decision_id, state, created_at
            )
            VALUES (?, ?, ?, ?, ?, '', 'draft', ?)
            """,
            (
                revision_id,
                report_id,
                revision_no,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                fingerprint,
                now,
            ),
        )
        self._db.commit()
        return self.get_report(report_id)

    def revise_report(
        self,
        *,
        report_id: str,
        sections: Mapping[str, Any],
        metadata: Optional[Mapping[str, Any]] = None,
        insight_ids: Optional[Sequence[str]] = None,
        insight_revision_ids: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        current = self.get_report(report_id)
        if current["state"] in {"released", "superseded", "archived"}:
            raise ReportValidationError(
                "Released/superseded/archived report cannot be silently edited. "
                "Create a new logical report or explicitly supersede it."
            )

        payload = deepcopy(current["payload"])
        payload["sections"] = self._normalize_sections(sections)
        payload["evidence_ids"] = _collect_evidence_ids(payload["sections"])

        if metadata is not None:
            if not isinstance(metadata, Mapping):
                raise ReportValidationError("metadata must be an object.")
            payload["metadata"] = _json_safe(dict(metadata))

        if insight_ids is not None:
            payload["insight_ids"] = _ids(insight_ids)

        if insight_revision_ids is not None:
            payload["insight_revision_ids"] = _ids(insight_revision_ids)

        self._validate_payload(payload)

        revision_no = int(current["revision_no"]) + 1
        revision_id = self._revision_id(report_id, revision_no, payload)
        fingerprint = _sha(payload)
        now = self.clock()

        self._db.execute(
            """
            INSERT INTO report_revisions(
                report_revision_id, report_id, revision_no, payload_json,
                report_fingerprint, release_decision_id, state, created_at
            )
            VALUES (?, ?, ?, ?, ?, '', 'draft', ?)
            """,
            (
                revision_id,
                report_id,
                revision_no,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                fingerprint,
                now,
            ),
        )
        self._db.execute(
            """
            UPDATE reports
            SET current_revision_id=?, state='draft', updated_at=?
            WHERE report_id=?
            """,
            (revision_id, now, report_id),
        )
        self._db.commit()
        return self.get_report(report_id)

    def apply_release_decision(
        self,
        *,
        report_id: str,
        release_decision: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(release_decision, Mapping):
            raise ReportValidationError("release_decision must be an object.")

        current = self.get_report(report_id)
        decision_id = _clean(
            release_decision.get("decision_id")
            or release_decision.get("release_decision_id")
        )
        if not decision_id:
            raise ReportValidationError("release decision requires decision_id.")

        authorized = release_decision.get("authorizes_release") is True
        release_allowed = (
            release_decision.get("release_allowed") is True
            or release_decision.get("allowed") is True
        )

        state = "released" if authorized and release_allowed else "blocked"
        now = self.clock()

        self._db.execute(
            """
            UPDATE report_revisions
            SET state=?, release_decision_id=?
            WHERE report_revision_id=?
            """,
            (state, decision_id, current["report_revision_id"]),
        )
        self._db.execute(
            """
            UPDATE reports
            SET state=?, updated_at=?
            WHERE report_id=?
            """,
            (state, now, report_id),
        )
        self._db.commit()

        result = self.get_report(report_id)
        result["release_decision"] = {
            "decision_id": decision_id,
            "release_allowed": release_allowed,
            "authorizes_release": authorized,
        }
        return result

    def supersede_report(
        self,
        *,
        report_id: str,
        superseded_by_report_id: str,
    ) -> Dict[str, Any]:
        report_id = _clean(report_id)
        superseded_by_report_id = _clean(superseded_by_report_id)

        if not self._db.execute(
            "SELECT 1 FROM reports WHERE report_id=?",
            (superseded_by_report_id,),
        ).fetchone():
            raise ReportValidationError("superseding report does not exist.")

        current = self.get_report(report_id)
        payload = deepcopy(current["payload"])
        payload.setdefault("metadata", {})
        payload["metadata"]["superseded_by_report_id"] = superseded_by_report_id

        self._db.execute(
            "UPDATE reports SET state='superseded', updated_at=? WHERE report_id=?",
            (self.clock(), report_id),
        )
        self._db.execute(
            """
            UPDATE report_revisions
            SET state='superseded', payload_json=?
            WHERE report_revision_id=?
            """,
            (
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                current["report_revision_id"],
            ),
        )
        self._db.commit()
        return self.get_report(report_id)

    def get_report(self, report_id: str) -> Dict[str, Any]:
        report_id = _clean(report_id)
        row = self._db.execute(
            """
            SELECT r.*, rr.revision_no, rr.payload_json, rr.report_fingerprint,
                   rr.release_decision_id, rr.state AS revision_state,
                   rr.created_at AS revision_created_at
            FROM reports r
            JOIN report_revisions rr
              ON rr.report_revision_id=r.current_revision_id
            WHERE r.report_id=?
            """,
            (report_id,),
        ).fetchone()

        if row is None:
            raise ReportServiceError(f"Report not found: {report_id}")

        payload = json.loads(row["payload_json"])

        return {
            "report_id": report_id,
            "report_revision_id": _clean(row["current_revision_id"]),
            "revision_no": int(row["revision_no"]),
            "state": _clean(row["state"]),
            "research_question": _clean(row["research_question"]),
            "report_fingerprint": _clean(row["report_fingerprint"]),
            "release_decision_id": _clean(row["release_decision_id"]),
            "payload": payload,
            **payload,
            "created_at": _clean(row["created_at"]),
            "updated_at": _clean(row["updated_at"]),
            "revision_created_at": _clean(row["revision_created_at"]),
            "metadata_runtime": {
                "service_version": SERVICE_VERSION,
                "release_authorized_here": False,
                "grounding_performed_here": False,
                "private_chain_of_thought_stored": False,
            },
        }

    def get_revision(self, report_revision_id: str) -> Dict[str, Any]:
        row = self._db.execute(
            "SELECT * FROM report_revisions WHERE report_revision_id=?",
            (_clean(report_revision_id),),
        ).fetchone()
        if row is None:
            raise ReportServiceError("Report revision not found.")

        payload = json.loads(row["payload_json"])
        return {
            "report_revision_id": _clean(row["report_revision_id"]),
            "report_id": _clean(row["report_id"]),
            "revision_no": int(row["revision_no"]),
            "state": _clean(row["state"]),
            "report_fingerprint": _clean(row["report_fingerprint"]),
            "release_decision_id": _clean(row["release_decision_id"]),
            "payload": payload,
            **payload,
            "created_at": _clean(row["created_at"]),
        }

    def list_reports(
        self,
        *,
        state: str = "",
        topic_id: str = "",
        limit: Optional[int] = None,
    ) -> list[Dict[str, Any]]:
        rows = self._db.execute(
            "SELECT report_id FROM reports ORDER BY updated_at DESC, report_id ASC"
        ).fetchall()

        output = []
        for row in rows:
            report = self.get_report(row["report_id"])
            if state and report["state"] != _enum(state):
                continue
            if topic_id and report["payload"].get("topic_id") != _clean(topic_id):
                continue
            output.append(report)

        if limit is not None:
            if isinstance(limit, bool) or int(limit) <= 0:
                raise ReportValidationError("limit must be positive integer.")
            output = output[: int(limit)]

        return output

    def _build_payload(
        self,
        *,
        research_question: str,
        sections: Mapping[str, Any],
        run_id: str,
        topic_id: str,
        cluster_ids: Sequence[str],
        insight_ids: Sequence[str],
        insight_revision_ids: Sequence[str],
        blueprint_id: str,
        metadata: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        if metadata is not None and not isinstance(metadata, Mapping):
            raise ReportValidationError("metadata must be an object.")

        payload = {
            "schema_version": SCHEMA_VERSION,
            "research_question": research_question,
            "run_id": _clean(run_id),
            "topic_id": _clean(topic_id),
            "cluster_ids": _ids(cluster_ids),
            "insight_ids": _ids(insight_ids),
            "insight_revision_ids": _ids(insight_revision_ids),
            "blueprint_id": _clean(blueprint_id),
            "sections": deepcopy(dict(sections)),
            "evidence_ids": _collect_evidence_ids(sections),
            "metadata": _json_safe(dict(metadata or {})),
        }
        self._validate_payload(payload)
        return payload

    def _normalize_sections(self, sections: Mapping[str, Any]) -> Dict[str, Any]:
        if not isinstance(sections, Mapping):
            raise ReportValidationError("sections must be an object.")

        private = _private_path(sections)
        if private:
            raise ReportValidationError(
                f"sections contain private/raw reasoning field at {private!r}."
            )

        normalized = {
            section: deepcopy(sections.get(section, []))
            for section in CANONICAL_SECTIONS
        }

        # Preserve explicit extra presentation sections without giving them
        # epistemic authority.
        for key, value in sections.items():
            key = _enum(key)
            if key and key not in normalized:
                normalized[key] = deepcopy(value)

        return _json_safe(normalized)

    def _validate_payload(self, payload: Mapping[str, Any]) -> None:
        private = _private_path(payload)
        if private:
            raise ReportValidationError(
                f"report contains private/raw reasoning field at {private!r}."
            )

        if _clean(payload.get("schema_version")) != SCHEMA_VERSION:
            raise ReportValidationError("report schema version mismatch.")

        if not _clean(payload.get("research_question")):
            raise ReportValidationError("research_question cannot be empty.")

        sections = payload.get("sections")
        if not isinstance(sections, Mapping):
            raise ReportValidationError("sections must be an object.")

    @staticmethod
    def _revision_id(
        report_id: str,
        revision_no: int,
        payload: Mapping[str, Any],
    ) -> str:
        return _stable_id(
            "REPORTREV",
            {
                "report_id": report_id,
                "revision_no": revision_no,
                "payload_fingerprint": _sha(payload),
            },
        )


StructuredReportService = ReportService