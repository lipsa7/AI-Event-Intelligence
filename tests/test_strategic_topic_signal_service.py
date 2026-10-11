from __future__ import annotations

import importlib
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import pytest

from services.strategic_topic_signal_service import StrategicTopicSignalService


REPORT_ID = "REPORT-FF59222B19F714AB8918D407"
TOPIC = "artificial intelligence"

# Real persisted event IDs from the diagnostic corpus.
GARUTADUSTAT_EVENT_ID = "EVENT-C811E8B2F55A62EF71DCF77A"
SERVIER_PHARMA_AI_EVENT_ID = "EVENT-CFC72BF08ADB39A17F155F44"
INTERIM_AI_RELEASE_EVENT_ID = "EVENT-F0324F27E0F0A34FC0853B9F"
OLD_CONTRADICTION_EVENT_ID = "EVENT-749110838CE9011977EA3F17"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _import_project_module(name: str):
    root = _project_root()
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    return importlib.import_module(name)


@lru_cache(maxsize=1)
def _load_real_corpus_events() -> tuple[Any, dict[str, Mapping[str, Any]]]:
    """Load actual persisted events; skip only if the local diagnostic DB is absent."""
    try:
        diag = _import_project_module("offline_corpus_integrity_diagnostic")
    except ModuleNotFoundError:
        pytest.skip("offline_corpus_integrity_diagnostic.py is not available in this checkout.")
    databases = diag.sqlite_candidates(_project_root())
    report, _ = diag.find_report(databases, REPORT_ID)
    if report is None:
        pytest.skip(
            "Local REPORT-FF59222B19F714AB8918D407 database is not available; "
            "real-corpus regression requires the persisted diagnostic corpus."
        )

    admission = diag.find_admission(report)
    if not admission:
        pytest.skip("Strategic-admission diagnostics missing from local report.")

    event_ids, _, _ = diag.event_ids_from_admission(admission)
    event_rows, _ = diag.locate_rows_by_id(databases, event_ids, "event_id")
    return diag, event_rows


def _real_anchor(event_id: str) -> str:
    diag, rows = _load_real_corpus_events()
    row = rows.get(event_id)
    assert row is not None, f"Expected persisted event missing: {event_id}"
    anchor = diag.event_anchor_text(row)
    assert anchor.strip(), f"Persisted anchor empty: {event_id}"
    return anchor


def _assess_real(event_id: str) -> dict[str, Any]:
    return StrategicTopicSignalService.assess_shared_baseline(
        topic=TOPIC,
        text=_real_anchor(event_id),
        event_family="",
    )


# ---------------------------------------------------------------------------
# Precision/invariant unit tests.  These are not claimed as corpus behavior;
# they directly test previously identified failure modes.
# ---------------------------------------------------------------------------


def test_pricing_model_consequence_phrase_cannot_create_ai_relevance():
    result = StrategicTopicSignalService.assess_shared_baseline(
        topic=TOPIC,
        text="Revenue increased due to the new pricing model.",
        event_family="operations",
    )
    assert result["relevance_score"] == 0.0
    assert result["topic_linkage_type"] == "none"
    assert result["topic_gate_pass"] is False
    assert not result["signal_bundle"]["consequence_relation_signals"]


def test_generic_model_constraint_cannot_create_ai_relevance():
    result = StrategicTopicSignalService.assess_shared_baseline(
        topic=TOPIC,
        text="The regulator restricted use of the new pricing model.",
        event_family="regulation",
    )
    assert result["relevance_score"] == 0.0
    assert result["topic_linkage_type"] == "none"
    assert result["topic_gate_pass"] is False
    assert not result["signal_bundle"]["constraint_relation_signals"]


def test_bank_does_not_create_ai_constraint_relation():
    result = StrategicTopicSignalService.assess_shared_baseline(
        topic=TOPIC,
        text="The AI company opened an account with a commercial bank.",
        event_family="operations",
    )
    assert not result["signal_bundle"]["constraint_relation_signals"]
    assert result["topic_linkage_type"] != "material_constraint"


def test_dosing_and_acquisition_are_material_actions():
    for text in (
        "The company completed dosing in the trial.",
        "The acquisition closed last quarter.",
    ):
        bundle = StrategicTopicSignalService.analyze(topic=TOPIC, text=text)
        assert bundle.has_material_action, text


def test_non_ai_topic_routes_to_generic_branch():
    for topic in (
        "Ukraine aid",
        "airline consolidation",
        "retail pricing models",
    ):
        bundle = StrategicTopicSignalService.analyze(
            topic=topic,
            text=f"Latest developments in {topic}.",
        )
        assert bundle.broad_ai_topic is False, topic


def test_service_reports_invariant_as_data_instead_of_raising():
    result = StrategicTopicSignalService.assess_shared_baseline(
        topic=TOPIC,
        text="The company released an AI model.",
        event_family="product_launch",
    )

    assert result["invariant_violation"] is False
    assert result["invariant_violations"] == []


def test_exact_ai_topic_is_still_supported():
    bundle = StrategicTopicSignalService.analyze(
        topic="AI",
        text="The company announced an AI system.",
    )
    assert bundle.broad_ai_topic is True


def test_relation_and_relevance_cannot_contradict():
    cases = (
        "AI adoption increased revenue and the company reported the result.",
        "The regulator restricted generative AI deployment.",
        "The company released an AI model.",
        "Revenue increased due to the new pricing model.",
    )
    eligible = {
        "direct",
        "material_enabler",
        "material_constraint",
        "material_consequence",
    }
    for text in cases:
        result = StrategicTopicSignalService.assess_shared_baseline(
            topic=TOPIC,
            text=text,
            event_family="operations",
        )
        if result["topic_linkage_type"] in eligible:
            assert result["relevance_score"] >= 2.0
            assert result["invariant_violation"] is False


# ---------------------------------------------------------------------------
# REAL CORPUS regressions.  These use the actual persisted anchor text from the
# report, never a paraphrase invented for the test.
# ---------------------------------------------------------------------------


def test_real_garutadustat_anchor_does_not_gain_ai_relevance_from_actor_identity():
    anchor = _real_anchor(GARUTADUSTAT_EVENT_ID)
    assert "Garutadustat" in anchor
    assert "first-patient dosing" in anchor or "first patient dosing" in anchor

    result = StrategicTopicSignalService.assess_shared_baseline(
        topic=TOPIC,
        text=anchor,
        event_family="",
    )

    assert result["relevance_score"] == 0.0
    assert result["topic_linkage_type"] == "none"
    assert result["topic_gate_pass"] is False


def test_real_servier_pharma_ai_anchor_recovers_explicit_ai_signal():
    anchor = _real_anchor(SERVIER_PHARMA_AI_EVENT_ID)
    assert "Pharma.AI" in anchor

    result = StrategicTopicSignalService.assess_shared_baseline(
        topic=TOPIC,
        text=anchor,
        event_family="partnership",
    )

    assert result["relevance_score"] >= 2.0
    assert result["topic_linkage_type"] == "direct"
    assert result["topic_gate_pass"] is True


def test_real_interim_results_anchor_recognizes_release_and_explicit_ai():
    anchor = _real_anchor(INTERIM_AI_RELEASE_EVENT_ID)
    lowered = anchor.casefold()
    assert "artificial intelligence" in lowered
    assert "released" in lowered or "release" in lowered

    result = StrategicTopicSignalService.assess_shared_baseline(
        topic=TOPIC,
        text=anchor,
        event_family="product_launch",
    )

    assert result["relevance_score"] >= 2.0
    assert result["topic_linkage_type"] == "direct"
    assert result["topic_gate_pass"] is True


def test_real_old_contradiction_event_cannot_remain_contradictory():
    anchor = _real_anchor(OLD_CONTRADICTION_EVENT_ID)
    result = StrategicTopicSignalService.assess_shared_baseline(
        topic=TOPIC,
        text=anchor,
        event_family="",
    )

    eligible = {
        "direct",
        "material_enabler",
        "material_constraint",
        "material_consequence",
    }
    if result["topic_linkage_type"] in eligible:
        assert result["relevance_score"] >= 2.0
