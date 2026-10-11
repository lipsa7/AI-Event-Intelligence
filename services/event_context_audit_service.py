"""Opt-in JSONL context audit. Observes the pipeline; never changes a decision.

Set EVENT_INTELLIGENCE_CONTEXT_AUDIT_DIR to enable. Each process appends to
its own extraction/admission files, with thread locking around complete lines.
Only this module owns audit output. It never opens gold/calibration files.
"""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import threading
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


AUDIT_VERSION = "event-context-audit-v1.0"
_logger = logging.getLogger(__name__)
_lock = threading.Lock()
_process_run_id = uuid.uuid4().hex
_sequence = 0
_warned: set[tuple[str, str]] = set()
_extraction_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "event_intelligence_extraction_audit", default=None
)


def context_audit_enabled() -> bool:
    return bool(os.getenv("EVENT_INTELLIGENCE_CONTEXT_AUDIT_DIR", "").strip())


def _json_default(value: Any) -> Any:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (set, tuple)):
        return list(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def write_context_audit(stage: str, payload: Mapping[str, Any]) -> bool:
    """Append one record, or warn once and return False if audit I/O fails.

    Per-process files avoid inter-process line interleaving on Windows as well
    as Linux. Logging failures are deliberately outside extraction/admission.
    """
    directory = os.getenv("EVENT_INTELLIGENCE_CONTEXT_AUDIT_DIR", "").strip()
    if not directory:
        return False
    try:
        if stage not in {"extraction", "admission"}:
            raise ValueError("Unknown context audit stage")
        destination = Path(directory).expanduser().resolve()
        with _lock:
            global _sequence
            _sequence += 1
            record = dict(payload)
            record.update({
                "audit_version": AUDIT_VERSION,
                "stage": stage,
                "run_id": os.getenv("EVENT_INTELLIGENCE_CONTEXT_AUDIT_RUN_ID", "").strip()
                or _process_run_id,
                "process_id": os.getpid(),
                "sequence": _sequence,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            })
            data = (json.dumps(record, ensure_ascii=False, default=_json_default)
                    + "\n").encode("utf-8")
            destination.mkdir(parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
            descriptor = os.open(destination / f"{stage}-{os.getpid()}.jsonl", flags, 0o600)
            try:
                remaining = memoryview(data)
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written <= 0:
                        raise OSError("Incomplete context audit write")
                    remaining = remaining[written:]
            finally:
                os.close(descriptor)
        return True
    except Exception as exc:
        _warn_audit_failure(directory, exc)
        return False


def _warn_audit_failure(directory: str, exc: Exception) -> None:
    # Even a misconfigured application logging handler must not alter results.
    try:
        key = (directory, type(exc).__name__)
        with _lock:
            if key in _warned:
                return
            _warned.add(key)
        _logger.warning(
            "Context audit output failed; pipeline decisions are unaffected. "
            "directory=%s error=%s message=%s",
            directory, type(exc).__name__, str(exc),
        )
    except Exception:
        pass


def _audit_observation(method):
    """Guard diagnostic preparation as well as file I/O."""
    @functools.wraps(method)
    def guarded(*args, **kwargs):
        if not context_audit_enabled():
            return None
        try:
            return method(*args, **kwargs)
        except Exception as exc:
            _warn_audit_failure(os.getenv("EVENT_INTELLIGENCE_CONTEXT_AUDIT_DIR", ""), exc)
            return None
    return guarded


@_audit_observation
def set_extraction_audit_context(**values: Any) -> None:
    if not context_audit_enabled():
        return
    current = _extraction_context.get()
    if current is not None:
        _extraction_context.set({**current, **values})


@_audit_observation
def audit_extraction_decision(decision: str, reason: str, **details: Any) -> None:
    if not context_audit_enabled():
        return
    write_context_audit("extraction", {
        **(_extraction_context.get() or {}),
        "record_type": "candidate_decision",
        "decision": decision,
        "reason": reason,
        **details,
    })


def audit_extraction_call(*, agent_version: str, build_id: str):
    """Scope diagnostics to one extract() call, including concurrent calls."""
    def decorate(method):
        @functools.wraps(method)
        def wrapped(self, *args, **kwargs):
            if not context_audit_enabled():
                return method(self, *args, **kwargs)
            token = _extraction_context.set({
                "extraction_call_id": uuid.uuid4().hex,
                "agent_version": agent_version,
                "extractor_build_id": build_id,
                "semantic_gate_setting": os.getenv("EVENT_INTELLIGENCE_FAST_SEMANTIC_GATE", "1"),
                "semantic_recovery_setting": os.getenv("EVENT_INTELLIGENCE_DEEP_SEMANTIC_RECOVERY", "0"),
            })
            try:
                result = method(self, *args, **kwargs)
                # The snapshot happens after final metadata/context enrichment.
                # No fields are inserted into the returned event payloads.
                try:
                    for event in result.get("events", []) or []:
                        audit_extraction_decision(
                            "accepted", "validated_event", record_type="validated_event",
                            event=event, event_id=event.get("event_id"),
                            source_id=event.get("source_id"),
                        )
                    audit_extraction_decision(
                        "completed", "source_extraction_completed", record_type="source_completed",
                        event_ids=[event.get("event_id") for event in result.get("events", []) or []],
                        diagnostics=dict(getattr(self, "_validation_diagnostics", {}) or {}),
                    )
                except Exception as exc:
                    _warn_audit_failure(os.getenv("EVENT_INTELLIGENCE_CONTEXT_AUDIT_DIR", ""), exc)
                return result
            except Exception as exc:
                audit_extraction_decision(
                    "failed", "source_extraction_failed", record_type="source_failed",
                    error_type=type(exc).__name__,
                )
                raise
            finally:
                _extraction_context.reset(token)
        return wrapped
    return decorate


@_audit_observation
def audit_admission_decision(
    *, topic: str, event: Mapping[str, Any],
    evidence_items: Sequence[Mapping[str, Any]], gate_input: str,
    assessment: Mapping[str, Any], service_version: str, build_id: str,
) -> None:
    if not context_audit_enabled():
        return
    # Only fields that can explain this gate decision are captured. The exact
    # bounded input is already available: never reconstruct it from display_text.
    event_keys = (
        "event_id", "source_id", "source_ids", "title", "summary", "event",
        "description", "event_type", "event_time_text", "temporal_status",
        "primary_actor", "acting_entity", "actor", "organization",
        "organisation", "company", "institution", "regulator", "entity_mentions",
        "quantitative_claims", "relationship_mentions", "snippets", "extraction_metadata",
    )
    evidence_keys = (
        "evidence_id", "event_id", "source_id", "source_ids", "evidence_type",
        "type", "claim", "text", "source_excerpt", "statement", "excerpt",
    )
    write_context_audit("admission", {
        "record_type": "admission_decision",
        "service_version": service_version,
        "orchestrator_build_id": build_id,
        "topic": topic,
        "event_id": event.get("event_id"),
        "source_id": event.get("source_id"),
        "event": {key: event[key] for key in event_keys if key in event},
        "evidence_count": len(evidence_items),
        "evidence_rows_considered": [
            {"input_index": idx, **{key: item[key] for key in evidence_keys if key in item}}
            for idx, item in enumerate(evidence_items[:4]) if isinstance(item, Mapping)
        ],
        "gate_input": gate_input,
        "gate_input_sha256": hashlib.sha256(gate_input.encode("utf-8")).hexdigest(),
        "gate_input_characters": len(gate_input),
        "decision": "admitted" if assessment.get("admit") else "rejected",
        "assessment": dict(assessment),
    })
