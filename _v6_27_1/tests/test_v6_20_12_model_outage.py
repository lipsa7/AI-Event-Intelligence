"""v6.20.12: when every Gemini model is rate-limited (daily quota), the run
releases the event landscape with an honest note instead of no report.
Live run RUN-ABDC9848F29644CE9AB947B1 failed with no report at all."""

from __future__ import annotations

import inspect
import time

import pytest

import tests.test_v6_17_report_sections as base
from services.research_orchestrator_service import ResearchOrchestratorService as R


@pytest.fixture(autouse=True)
def _private_ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_WATCH_ITEMS", str(tmp_path / "watch_items.sqlite3"))
    monkeypatch.setenv("EVENT_INTELLIGENCE_EVENT_MEMORY", str(tmp_path / "event_memory.sqlite3"))


def test_model_outage_still_releases_the_landscape(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    RateLimit = type("GeminiRateLimitError", (Exception,), {})
    obj = base.orchestrator(RateLimit("Gemini rate limit reached after bounded retries."))
    obj.report_service = None
    obj.policy_enforcement_service = None
    for name in ("telemetry_service", "insight_registry_service", "audit_receipt_service", "insight_trace_service"):
        setattr(obj, name, None)
    events, evidence = base.events_and_evidence()
    state = {"warnings": [], "gap_research_done": True}
    obj._complete_abstention(
        state=state, topic="artificial intelligence", run_id="RUN-T12", depth="deep", events=events,
        evidence=evidence, constraints={}, graph_context={},
        reasons=["strategic_model_unavailable:GeminiRateLimitError"], started_at=time.monotonic(),
        stage="strategic_model_unavailable")
    report = state["report"]
    assert report["event_landscape"]
    assert report["abstention_classification"] == "analysis_model_unavailable"
    assert "analysis model was unavailable" in report["abstention_summary"]
    assert report["editorial_briefing"]["mode"] == "source_statements_only"   # never blank, never invented


def test_provider_failure_routes_to_the_landscape_report():
    source = inspect.getsource(R)
    branch = source.index("strategic_model_unavailable: releasing the event landscape without analysis")
    raise_at = source.index('"Strategic model execution failed. "')
    assert branch < raise_at
    assert 'EVENT_INTELLIGENCE_RELEASE_ON_MODEL_OUTAGE' in source


# Google's 429 wording (v6.20.12) --------------------------------------

def test_google_retry_delay_is_honoured():
    from services.gemini_llm_service import _retry_after_seconds
    text = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota. "
            "Please retry in 41.2s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': "
            "'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '41s'}]}}")
    assert _retry_after_seconds(Exception(text)) == pytest.approx(41.2)
    assert _retry_after_seconds(Exception("{'retryDelay': '17s'}")) == 17.0
    assert _retry_after_seconds(Exception("no delay given")) == 0.0


def test_gemini_warnings_reach_the_app_log():
    import services.gemini_llm_service as gemini
    assert gemini.logger.name.startswith("event_intelligence.")
    source = inspect.getsource(gemini)
    assert "Google said: " in source and "google=%s" in source
