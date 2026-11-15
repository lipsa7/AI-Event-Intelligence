#!/usr/bin/env python3
"""
Regression tests for offline_corpus_integrity_diagnostic.py.

These tests are deliberately deterministic and do not require the application DB,
NewsAPI, Streamlit, or an LLM.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "offline_corpus_integrity_diagnostic.py"

spec = importlib.util.spec_from_file_location("offline_corpus_integrity_diagnostic", MODULE_PATH)
assert spec is not None and spec.loader is not None
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)


def test_candidate_generation_defect_when_anchor_has_name_but_entity_list_does_not():
    assert diag.classify_insilico_recall(
        anchor_has_insilico=True,
        source_has_insilico=True,
        entity_has_insilico=False,
    ) == "candidate_generation_recall_defect"


def test_anchoring_defect_when_source_has_name_but_anchor_does_not():
    assert diag.classify_insilico_recall(
        anchor_has_insilico=False,
        source_has_insilico=True,
        entity_has_insilico=False,
    ) == "anchoring_or_segmentation_recall_defect"


def test_actor_resolution_path_when_entity_mention_exists():
    assert diag.classify_insilico_recall(
        anchor_has_insilico=True,
        source_has_insilico=True,
        entity_has_insilico=True,
    ) == "mention_present_check_actor_resolution_or_typing"


def test_provenance_investigation_when_name_is_nowhere_in_persisted_material():
    assert diag.classify_insilico_recall(
        anchor_has_insilico=False,
        source_has_insilico=False,
        entity_has_insilico=False,
    ) == "source_or_event_provenance_needs_inspection"


def test_event_anchor_text_reads_summary_and_snippets():
    event = {
        "summary": "In June, Insilico Medicine announced a collaboration.",
        "snippets": ["The transaction involves AI drug discovery."],
    }
    anchor = diag.event_anchor_text(event)
    assert "Insilico Medicine" in anchor
    assert "AI drug discovery" in anchor


def test_insilico_entity_detection_is_name_based_not_entity_type_based():
    event = {
        "entity_mentions": [
            {"mention": "Insilico Medicine", "entity_type": "unknown"},
            {"mention": "US", "entity_type": "unknown"},
        ]
    }
    assert diag.insilico_mention_present(event) is True


def test_unknown_us_does_not_make_event_insilico_related():
    event = {
        "summary": "AI investment in the US increased.",
        "entity_mentions": [{"mention": "US", "entity_type": "unknown"}],
    }
    assert diag.insilico_mention_present(event) is False
    assert diag.contains_insilico(diag.event_anchor_text(event)) is False


def test_material_actor_guard_regex_matches_actual_v53_behavior():
    action_event = {
        "event_type": "partnership",
        "summary": "Insilico Medicine announced a collaboration.",
    }
    investment_observation = {
        "event_type": "market_signal",
        "summary": "AI investment in the US rose sharply.",
    }
    plain_macro_observation = {
        "event_type": "market_signal",
        "summary": "Inflation rose sharply in the US.",
    }

    # The current V5.3 regex includes "invest", so this is intentionally True.
    # That is useful diagnostic evidence because the V5.3 docstring says macro
    # observations should not be covered by the named-actor guard.
    assert diag.material_actor_guard_applies(action_event) is True
    assert diag.material_actor_guard_applies(investment_observation) is True
    assert diag.material_actor_guard_applies(plain_macro_observation) is False


def test_evidence_histogram_is_per_event_not_average_only():
    event_ids = {"EVENT-A", "EVENT-B", "EVENT-C", "EVENT-D"}
    evidence = [
        {"evidence_id": "EVID-1", "event_id": "EVENT-A", "evidence_type": "source_excerpt"},
        {"evidence_id": "EVID-2", "event_id": "EVENT-B", "evidence_type": "source_excerpt"},
        {"evidence_id": "EVID-3", "event_id": "EVENT-B", "evidence_type": "quantitative_claim"},
        {"evidence_id": "EVID-4", "event_id": "EVENT-C", "evidence_type": "source_excerpt"},
        {"evidence_id": "EVID-5", "event_id": "EVENT-C", "evidence_type": "quantitative_claim"},
        {"evidence_id": "EVID-6", "event_id": "EVENT-C", "evidence_type": "quantitative_claim"},
    ]

    result = diag.evidence_histogram(event_ids, evidence)
    assert result["histogram"] == {"0": 1, "1": 1, "2": 1, "3+": 1}
    assert result["events_with_at_least_1"] == 3
    assert result["events_with_at_least_2"] == 2


def test_no_llm_object_fails_closed():
    blocked = diag.NoLLM()
    try:
        blocked.ask("hello")
    except RuntimeError as exc:
        assert "forbids LLM use" in str(exc)
    else:
        raise AssertionError("NoLLM must fail if any LLM method is called.")
