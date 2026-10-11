from __future__ import annotations

import importlib
import json
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest


REPORT_ID = "REPORT-FF59222B19F714AB8918D407"
TOPIC = "artificial intelligence"
EXPECTED_EVENT_COUNT = 38
EXPECTED_SIGNAL_VERSION_FRAGMENT = (
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


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_project_import_path() -> Path:
    root = _project_root()
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    return root


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _enum(value: Any) -> str:
    return _clean(value).casefold().replace("-", "_").replace(" ", "_")


def _is_topic_reason(reason: Any) -> bool:
    value = _clean(reason)
    return any(value.startswith(prefix) for prefix in TOPIC_REASON_PREFIXES)


@lru_cache(maxsize=1)
def _modules() -> tuple[Any, Any, Any]:
    _ensure_project_import_path()

    diag = importlib.import_module("offline_corpus_integrity_diagnostic")
    signal_module = importlib.import_module("services.strategic_topic_signal_service")
    orchestrator_module = importlib.import_module("services.research_orchestrator_service")

    signal_version = _clean(getattr(signal_module, "SERVICE_VERSION", ""))
    assert EXPECTED_SIGNAL_VERSION_FRAGMENT in signal_version, (
        "Wrong StrategicTopicSignalService loaded. "
        f"actual={signal_version!r} "
        f"expected_fragment={EXPECTED_SIGNAL_VERSION_FRAGMENT!r} "
        f"module={getattr(signal_module, '__file__', '')!r}"
    )

    orchestrator_class = getattr(
        orchestrator_module,
        "ResearchOrchestratorService",
        None,
    )
    assert orchestrator_class is not None, (
        "ResearchOrchestratorService missing from "
        f"{getattr(orchestrator_module, '__file__', '')!r}"
    )

    for method_name in (
        "_strategic_event_text",
        "_coarse_event_family",
        "_strategic_event_assessment",
    ):
        assert callable(getattr(orchestrator_class, method_name, None)), (
            f"Orchestrator contract method missing: {method_name}"
        )

    return diag, signal_module, orchestrator_module


@lru_cache(maxsize=1)
def _frozen_corpus() -> tuple[
    dict[str, Mapping[str, Any]],
    dict[str, list[Mapping[str, Any]]],
]:
    diag, _, _ = _modules()
    root = _project_root()

    databases = diag.sqlite_candidates(root)
    report, _ = diag.find_report(databases, REPORT_ID)
    if report is None:
        pytest.skip(
            f"Persisted report {REPORT_ID} is not available in local SQLite databases."
        )

    admission = diag.find_admission(report)
    if not admission:
        pytest.skip("Strategic-admission diagnostics are missing from the persisted report.")

    event_ids, _, _ = diag.event_ids_from_admission(admission)
    assert len(event_ids) == EXPECTED_EVENT_COUNT, (
        "Frozen corpus changed unexpectedly. "
        f"expected={EXPECTED_EVENT_COUNT} actual={len(event_ids)}"
    )

    event_rows, _ = diag.locate_rows_by_id(
        databases,
        event_ids,
        "event_id",
    )
    missing_events = sorted(event_ids - set(event_rows))
    assert not missing_events, f"Persisted events missing: {missing_events}"

    persisted_evidence, _ = diag.locate_persisted_evidence(
        databases,
        event_ids,
    )
    report_evidence = diag.evidence_from_report(report, event_ids)
    evidence_records = diag.dedupe_records(
        [*persisted_evidence, *report_evidence],
        id_keys=("evidence_id", "id"),
    )

    evidence_by_event: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in evidence_records:
        event_id = _clean(
            record.get("event_id")
            or record.get("linked_event_id")
            or record.get("canonical_event_id")
        ).upper()
        if event_id in event_ids:
            evidence_by_event[event_id].append(dict(record))

    return (
        {event_id: event_rows[event_id] for event_id in sorted(event_ids)},
        dict(evidence_by_event),
    )


def _standalone_topic_assessment(
    *,
    topic: str,
    event: Mapping[str, Any],
    evidence_items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    _, signal_module, orchestrator_module = _modules()
    service = signal_module.StrategicTopicSignalService
    orchestrator = orchestrator_module.ResearchOrchestratorService

    # Critical contract detail: compare both implementations on the EXACT same
    # bounded event/evidence text and the EXACT same coarse event family.
    text = orchestrator._strategic_event_text(event, evidence_items)
    family = orchestrator._coarse_event_family(event)

    return dict(
        service.assess_shared_baseline(
            topic=topic,
            text=text,
            event_family=family,
        )
    )


def _native_orchestrator_assessment(
    *,
    topic: str,
    event: Mapping[str, Any],
    evidence_items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    _, _, orchestrator_module = _modules()
    orchestrator = orchestrator_module.ResearchOrchestratorService

    return dict(
        orchestrator._strategic_event_assessment(
            topic=topic,
            event=event,
            evidence_items=list(evidence_items),
        )
    )


def _gate_diagnostic(
    *,
    event_id: str,
    standalone: Mapping[str, Any],
    native: Mapping[str, Any],
) -> dict[str, Any]:
    native_reasons = [
        _clean(reason)
        for reason in (native.get("reasons") or [])
        if _clean(reason)
    ]
    non_topic_reasons = [
        reason
        for reason in native_reasons
        if not _is_topic_reason(reason)
    ]

    shared_relevance = float(standalone.get("relevance_score") or 0.0)
    native_relevance = float(native.get("relevance_score") or 0.0)
    shared_linkage = _enum(standalone.get("topic_linkage_type"))
    native_linkage = _enum(native.get("topic_linkage_type"))

    shared_topic_gate = bool(
        shared_relevance >= 2.0
        and shared_linkage in ELIGIBLE_LINKAGES
    )
    native_topic_gate = bool(
        native_relevance >= 2.0
        and native_linkage in ELIGIBLE_LINKAGES
    )

    # This is intentionally diagnostic, not a second implementation of the
    # complete admission function. It asks: if we keep every current non-topic
    # rejection reason unchanged and swap only the topic predicate, what would
    # the admission result be?
    counterfactual_admit = bool(
        shared_topic_gate
        and not non_topic_reasons
    )

    return {
        "event_id": event_id,
        "standalone": {
            "relevance_score": shared_relevance,
            "topic_linkage_type": shared_linkage,
            "topic_gate_pass": shared_topic_gate,
        },
        "orchestrator": {
            "relevance_score": native_relevance,
            "topic_linkage_type": native_linkage,
            "topic_gate_pass": native_topic_gate,
            "admit": bool(native.get("admit")),
            "materiality_score": native.get("materiality_score"),
            "actor": _clean(native.get("actor")),
            "event_class": _enum(native.get("event_class")),
            "reasons": native_reasons,
        },
        "admission_decomposition": {
            "non_topic_reasons": non_topic_reasons,
            "counterfactual_admit_with_shared_topic_gate": counterfactual_admit,
            "native_admit": bool(native.get("admit")),
        },
    }


def test_shared_topic_signal_matches_integrated_orchestrator_on_frozen_corpus():
    """Lock the post-integration contract on the frozen 38-event corpus.

    Tight equality requirements:
      * relevance_score must be identical per event;
      * topic_linkage_type must be identical per event.

    Admission is checked separately. If it differs, the failure prints the
    current non-topic gate reasons so we know whether actor/materiality/background
    logic diverged rather than merely observing a changed admitted-event count.
    """

    events, evidence_by_event = _frozen_corpus()

    topic_mismatches: list[dict[str, Any]] = []
    admission_mismatches: list[dict[str, Any]] = []

    for event_id, event in events.items():
        evidence_items = evidence_by_event.get(event_id, [])

        standalone = _standalone_topic_assessment(
            topic=TOPIC,
            event=event,
            evidence_items=evidence_items,
        )
        native = _native_orchestrator_assessment(
            topic=TOPIC,
            event=event,
            evidence_items=evidence_items,
        )

        diagnostic = _gate_diagnostic(
            event_id=event_id,
            standalone=standalone,
            native=native,
        )

        standalone_relevance = float(standalone.get("relevance_score") or 0.0)
        native_relevance = float(native.get("relevance_score") or 0.0)
        standalone_linkage = _enum(standalone.get("topic_linkage_type"))
        native_linkage = _enum(native.get("topic_linkage_type"))

        # No fuzzy tolerance here. Both values are deterministic and rounded to
        # three decimals, so exact per-event equality is the intended contract.
        if (
            standalone_relevance != native_relevance
            or standalone_linkage != native_linkage
        ):
            topic_mismatches.append(diagnostic)

        counterfactual_admit = bool(
            diagnostic["admission_decomposition"]
            ["counterfactual_admit_with_shared_topic_gate"]
        )
        native_admit = bool(native.get("admit"))

        if native_admit != counterfactual_admit:
            admission_mismatches.append(diagnostic)

    assert not topic_mismatches, (
        "Shared topic predicate is not integrated exactly. "
        "Per-event relevance/linkage mismatches:\n"
        + json.dumps(topic_mismatches, indent=2, ensure_ascii=False, default=str)
    )

    assert not admission_mismatches, (
        "Admission changed beyond the shared topic-predicate substitution. "
        "Each row below decomposes the non-topic gates so the divergent gate is visible:\n"
        + json.dumps(admission_mismatches, indent=2, ensure_ascii=False, default=str)
    )
