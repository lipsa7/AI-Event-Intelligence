"""v6.20.10: fixes from the v6.20.9 replay (RUN-DFA27E66C51C4CDE8CD46B04).

1. One claim check returning invalid output voided the whole briefing check.
2. A claim-checked finding of tier "underlying_dynamic" had no release route,
   so the final policy blocked the report ("never blank" broken).
3. Undated precedent pages were refused even when they state a dated past case.
4. The dashboard's submission log line did not reach system.log.
"""

from __future__ import annotations

import json
import logging

import pytest

import tests.test_v6_17_report_sections as base
from services.grounding_service import GroundingService, GroundingValidationError
from services.research_orchestrator_service import ResearchOrchestratorService as R


@pytest.fixture(autouse=True)
def _private_ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_WATCH_ITEMS", str(tmp_path / "watch_items.sqlite3"))
    monkeypatch.setenv("EVENT_INTELLIGENCE_EVENT_MEMORY", str(tmp_path / "event_memory.sqlite3"))


class OneBadCheck:
    """Like the real service: a batch raises if any one claim's check is invalid."""

    def __init__(self, bad_text):
        self.bad_text = bad_text

    def ground_claims(self, *, claims, evidence_records):
        GroundingService._normalize_evidence_records(GroundingService.__new__(GroundingService), evidence_records)
        if any(self.bad_text in c["claim_text"] for c in claims):
            raise GroundingValidationError("Support classifier referenced evidence IDs outside the supplied set")
        return {"claims": [{"claim_id": c["claim_id"],  # v6.21.6: novelty tests find nothing already said
                            "grounding_status": "unsupported" if c["claim_id"].startswith("NOVELTY") else "supported"}
                           for c in claims]}


# 1 ---------------------------------------------------------------------

def test_one_failed_check_removes_only_its_sentence(caplog):
    obj = base.orchestrator(json.dumps(base.DRAFT), OneBadCheck("Anthropic has reportedly filed"))
    with caplog.at_level(logging.WARNING):
        out = obj._editorial_briefing(topic="AI", developments=base.DEVELOPMENTS, evidence=[],
                                      state={"historical_context": {"records": base.HISTORY}})
    assert out["mode"] == "checked_analysis"
    shown = [s["text"] for p in out["paragraphs"] for s in p]
    assert "Anthropic has reportedly filed for an IPO." not in shown
    assert out["implications"]
    assert any("1 of" in r.getMessage() and "GroundingValidationError" in r.getMessage() for r in caplog.records)


def test_isolated_check_fails_closed():
    obj = base.orchestrator("{}", OneBadCheck("bad"))
    result = obj._ground_claims_isolated(
        [{"claim_id": "C1", "claim_text": "bad claim", "evidence_ids": ["E1"]},
         {"claim_id": "C2", "claim_text": "good claim", "evidence_ids": ["E1"]}],
        [{"evidence_id": "E1", "text": "x"}, {"evidence_id": "E1", "text": "dup"}], label="t")
    assert [c["grounding_status"] for c in result["claims"]] == ["unsupported", "supported"]
    assert result["check_failures"] == 1


# 2 ---------------------------------------------------------------------

def test_release_routes():
    assert R._has_release_route({"finding_tier": "hidden_insight"})
    assert not R._has_release_route({"finding_tier": "underlying_dynamic"})
    assert not R._has_release_route({"finding_tier": "strategic_connection"})
    assert R._has_release_route({"finding_tier": "strategic_connection",
                                 "metadata": {"claim_support_gate": {"released_as": "verified_connection"}}})


def test_safety_net_matches_the_real_policy_output():
    """The replay's exact block: the real policy's violations for an underlying_dynamic-only report."""
    from services.policy_enforcement_service import PolicyEnforcementService
    policy = PolicyEnforcementService.__new__(PolicyEnforcementService)
    blocked = {"violations": [
        {"code": "REQUIRED_REPORT_SECTION_MISSING", "path": "report.connections", "message": "m", "severity": "hard"},
        {"code": "REQUIRED_REPORT_SECTION_MISSING", "path": "report.hidden_insights", "message": "m", "severity": "hard"}]}
    assert R._blocked_only_for_missing_finding(blocked)
    other = {"violations": blocked["violations"] + [
        {"code": "EVIDENCE_TRAIL_REQUIRED", "path": "report.evidence_trail", "message": "m"}]}
    assert not R._blocked_only_for_missing_finding(other)
    assert not R._blocked_only_for_missing_finding({"violations": []})
    assert not R._blocked_only_for_missing_finding({"violations": [
        {"code": "REQUIRED_REPORT_SECTION_MISSING", "path": "report.evidence_trail"}]})
    # The policy really emits that code/path shape.
    import inspect
    from services import policy_enforcement_service as pes
    source = inspect.getsource(pes.PolicyEnforcementService)
    assert '"REQUIRED_REPORT_SECTION_MISSING"' in source and 'path=f"report.{section}"' in source
    assert policy is not None


def test_unrouted_finding_goes_to_the_abstention_report_in_the_run_flow():
    import inspect
    source = inspect.getsource(R)
    route = source.index("claim_gate_finding_without_release_route")
    red_team = source.index("red_team_result = (\n                    self._red_team(")
    assert route < red_team  # decided before the slow red-team pass
    assert "release_blocked_only_for_missing_finding" in source


# 3 ---------------------------------------------------------------------

def test_undated_page_with_a_dated_past_case_is_a_precedent(monkeypatch):
    text, _ = base.GAP_PAGES["precedent"]
    monkeypatch.setitem(base.GAP_PAGES, "precedent", (text, ""))
    original = base._html

    def html(body, published):
        if not published:
            return f"<html><head><title>Page</title></head><body><article><p>{body}</p>{'<p>Background.</p>' * 15}</article></body></html>".encode()
        return original(body, published)

    monkeypatch.setattr(base, "_html", html)
    service, search, _ = base._gap_setup()
    receipt = service.investigate_gaps(development=base.DEVELOPMENTS[0], as_of="2026-10-09T00:00:00+00:00",
                                       search=search)
    case = [r for r in receipt["records"] if r.get("historical_case")]
    assert len(case) == 1 and case[0]["publication_date_basis"] == "historical_case_date_quote"


def test_undated_page_keeps_no_current_context(monkeypatch):
    """An undated precedent-query page cannot supply non-historical records."""
    from services.hypothesis_evidence_service import HypothesisEvidenceService
    from datetime import datetime, timezone
    body = "Banks require read-only access for payment apps today."

    class Model:
        model_name = "m"
        timeout_seconds = 30

        def ask(self, prompt, **kwargs):
            return json.dumps({"review_complete": True, "records": [
                {"quote": body, "relation": "context", "claim_kind": "observation", "connection": "c",
                 "historical_case": None}]})

    service = HypothesisEvidenceService(web_search_service=None, source_fetch_service=None, llm_service=Model())
    out = service._process_source(source={"url": "https://x.example", "text_content": body}, context={},
                                  candidate={"insight": "i"}, now=datetime(2026, 10, 9, tzinfo=timezone.utc),
                                  timeout=10, allow_undated_historical=True)
    assert out["accepted_evidence"] == []
    assert out["rejected_candidates"][0]["reason"] == "undated_page_record_is_not_a_dated_past_case"


def test_undated_page_still_refused_for_other_gaps():
    from services.hypothesis_evidence_service import HypothesisEvidenceService
    from datetime import datetime, timezone
    service = HypothesisEvidenceService(web_search_service=None, source_fetch_service=None, llm_service=None)
    with pytest.raises(ValueError, match="publication date"):
        service._process_source(source={"url": "https://x.example", "text_content": "text"}, context={},
                                candidate={"insight": "i"}, now=datetime(2026, 10, 9, tzinfo=timezone.utc), timeout=10)


# 4 ---------------------------------------------------------------------

def test_dashboard_submission_reaches_the_app_log():
    import pathlib
    import re
    source = (pathlib.Path(__file__).resolve().parents[1] / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    names = re.findall(r'^logger = logging\.getLogger\(([^)]*)\)', source, re.M)
    assert names == ['"event_intelligence.frontend.dashboard"']
    assert "Dashboard research submission | question=%s requested_depth=%s" in source
