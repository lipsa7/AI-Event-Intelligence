"""v13.13: every run gets a measured record (owner, 20 Nov 2026), plus the Exa and precedent fixes."""
from services import run_metrics


def _report():
    return {
        "hidden_insights": [{"insight_revision_id": "R1", "falsifier": "If X happens, the thesis is wrong."}],
        "grounding_by_insight": {"R1": {"claims": [
            {"grounding_status": "supported", "supporting_evidence_ids": ["E1"]},
            {"grounding_status": "inference_supported", "supporting_evidence_ids": ["E2"]},
            {"grounding_status": "unsupported", "supporting_evidence_ids": []},
            {"grounding_status": "partially_supported", "supporting_evidence_ids": ["E1"]},
        ]}},
        "strategic_hypotheses": {"verified": [{"thesis": "t"}]},
        "insight_evaluation": {"status": "ranked", "items": [{"score": 9}, {"score": 7}]},
    }


STATE = {
    "claim_support_gate": {"attempts": [
        {"passed": True, "reasons": []},
        {"passed": False, "reasons": ["already_shown_in_earlier_report"]},
        {"passed": False, "reasons": ["overreach_unsourced_word:only"]},
        {"passed": False, "reasons": ["overreach_unsourced_word:compelling"]},
    ]},
    "strategic_hypothesis_lab": {"funnel": {"generated_rows": 18, "repeats_of_earlier_reports": 2}},
    "strategic_event_admission": {"admitted_event_count": 40, "considered_event_count": 30},
    "gap_research_summary": [{"source_reviews": [
        {"outcome": "reviewed", "accepted": 1, "precedents": 1},
        {"outcome": "reviewed", "accepted": 0, "precedents": 0},
        {"outcome": "not_reviewed"},
    ]}],
}
EVIDENCE = [{"evidence_id": "E1", "source_id": "S1", "source_url": "https://www.reuters.com/a"},
            {"evidence_id": "E2", "source_id": "S2", "source_url": "https://apnews.com/b"}]


def test_metrics_from_one_run():
    m = run_metrics.compute(state=STATE, report=_report(), evidence=EVIDENCE,
                            exa_usage={"used": 120, "failed": 1, "refused": 0, "pages": 6, "searches": 4},
                            calls_by_stage={"pipeline": {"attempts": 70}, "hypothesis_lab": {"attempts": 50}},
                            elapsed_seconds=2400.4)
    g, f, r, c = m["grounding"], m["insight_funnel"], m["retrieval"], m["cost"]
    assert g["claims_checked"] == 4 and g["claim_support_rate"] == 0.5     # supported + inference_supported
    assert g["citation_coverage"] == 0.75 and g["independent_publishers_per_insight"] == 2
    assert g["insights_with_falsifier_or_watch_signal"] == 1.0
    assert f["candidates_checked"] == 4 and f["insights_released"] == 1 and f["release_rate"] == 0.25
    assert f["rejections_by_reason"] == {"overreach_unsourced_word": 2, "already_shown_in_earlier_report": 1}
    assert f["repeats_of_earlier_caught"] == 3 and f["lab_theses_generated"] == 18 and f["lab_theses_verified"] == 1
    assert r["topic_filter_removed_share"] == 0.25 and r["research_pages_reviewed"] == 2
    assert r["research_pages_kept"] == 1 and r["past_cases_found"] == 1
    assert r["exa_pages_read"] == 6 and r["exa_searches"] == 4 and r["exa_units_used"] == 120
    assert c["gemini_calls"] == 120 and c["calls_per_released_insight"] == 120 and c["run_seconds"] == 2400.4
    assert m["judge"] == {"status": "ranked", "items_scored": 2, "top_score": 9}
    assert len(run_metrics.summary_lines(m)) == 6


def test_blocked_release_counts_no_released_insight():
    m = run_metrics.compute(state=STATE, report=_report(), evidence=EVIDENCE, released=False)
    assert m["insight_funnel"]["insights_released"] == 0 and m["grounding"]["claims_checked"] == 0
    assert m["grounding"]["claim_support_rate"] is None


def test_empty_run_never_fails():
    m = run_metrics.compute(state={}, report={})
    assert m["insight_funnel"]["candidates_checked"] == 0 and run_metrics.summary_lines(m)


def test_exa_run_budget_is_600_units(monkeypatch):
    from services import exa_contents
    monkeypatch.delenv("EXA_MAX_CALLS_PER_RUN", raising=False)
    assert exa_contents.cap() == 600


def test_precedent_review_says_why_no_past_case():
    import inspect
    from services import hypothesis_evidence_service as hes
    source = inspect.getsource(hes)
    assert '"case_note"' in source and "historical_case=null, say in a few words why" in source


def test_persist_report_saves_and_logs_the_run_metrics(caplog):
    """The metrics are computed where the report is saved, after the release decision."""
    import logging
    import pytest
    from services.research_orchestrator_service import ResearchOrchestratorService as S

    class Stop(Exception):
        pass

    class FailingStore:
        def __getattr__(self, name):
            raise Stop(name)       # stop right after the metrics; saving is not under test

    obj = object.__new__(S)
    obj.report_service = FailingStore()
    obj._quality_gate_inputs = (STATE, [], EVIDENCE)
    obj._insight_evaluation = lambda report, topic: {"status": "ranked", "items": [{"score": 8}]}
    report = _report()
    caplog.set_level(logging.INFO)
    obj._persist_report(topic="t", report=report,       # saving fails on the fake store (logged, not raised)
                        release_decision={"release_allowed": True, "authorizes_release": True})
    metrics = report["analysis_metadata"]["run_metrics"]
    assert metrics["grounding"]["claim_support_rate"] == 0.5 and metrics["judge"]["top_score"] == 9
    assert any("run_metrics=" in r.getMessage() for r in caplog.records)
    assert any("run_metrics_summary | Grounding: claim support 50%" in r.getMessage() for r in caplog.records)
