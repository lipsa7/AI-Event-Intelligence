"""v6.20.11: audit of the one ending no live run has reached yet, plus the
diagnostics the v6.20.10 replay (RUN-736F44E941C844D1843313C5) needed.

1. A hidden insight that passes every claim check, built into a report by the
   real report builder and judged by the real release policy and compiled
   blueprint, must be released. Before v6.20.11 it was blocked because the
   separate "connections" list was empty, and v6.20.10's safety net would then
   have turned the run into an abstention, silently dropping the insight.
2. Briefing checks are logged one by one; a rate-limited check is retried.
3. Precedent searches ask for a named earlier case.
"""

from __future__ import annotations

import inspect
import json
import logging

import pytest

import tests.test_v6_17_report_sections as base
from tests.test_claim_support_gate import PASSING, candidate, make_orchestrator, release
from tests.test_verified_connection_release import _codes, _decide, blueprint  # noqa: F401  (fixture)
from services.research_orchestrator_service import ResearchOrchestratorService as R


@pytest.fixture(autouse=True)
def _private_ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_WATCH_ITEMS", str(tmp_path / "watch_items.sqlite3"))
    monkeypatch.setenv("EVENT_INTELLIGENCE_EVENT_MEMORY", str(tmp_path / "event_memory.sqlite3"))


EVENTS = [{"event_id": "EVENT-A", "title": "A", "primary_actor": "Okta", "event_start_at": "2026-09-01"},
          {"event_id": "EVENT-B", "title": "B", "primary_actor": "Equals Money", "event_start_at": "2026-09-02"}]


def _hidden_insight_report():
    obj = make_orchestrator(verdicts={"INSIGHTREV-X": PASSING})
    (released, evidence, grounded), _ = release(obj, [candidate("X")])
    assert [obj._finding_tier(r) for r in released] == ["hidden_insight"]
    report = obj._build_report(topic="artificial intelligence", run_id="RUN-AUDIT", depth="deep", events=EVENTS,
                               evidence=evidence, constraints={}, graph_context={}, insights=released,
                               grounding=grounded, strategic_connections=[])
    return report, grounded


# 1 ---------------------------------------------------------------------

def test_passed_hidden_insight_is_released_by_the_real_policy(blueprint):  # noqa: F811
    report, grounded = _hidden_insight_report()
    assert report["hidden_insights"] and report["connections"] == []   # the live shape: no separate connections
    decision = _decide(blueprint, report, grounding=grounded)
    assert decision["release_allowed"] is True, _codes(decision)
    assert "SECTION_COVERED_BY_HIDDEN_INSIGHT" in [w["code"] for w in decision["warnings"]]


def test_connections_still_required_without_a_hidden_insight(blueprint):  # noqa: F811
    report, grounded = _hidden_insight_report()
    report["hidden_insights"] = []
    decision = _decide(blueprint, report, grounding=grounded)
    assert decision["release_allowed"] is False
    assert "REQUIRED_REPORT_SECTION_MISSING" in _codes(decision)


def test_safety_net_never_turns_a_hidden_insight_into_an_abstention():
    source = inspect.getsource(R)
    net = source.index("and self._blocked_only_for_missing_finding(release_decision)")
    assert 'and not report.get("hidden_insights")' in source[net - 200:net]


# 2 ---------------------------------------------------------------------

class RateLimitedOnce:
    def __init__(self):
        self.calls = 0

    def ground_claims(self, *, claims, evidence_records):
        self.calls += 1
        if self.calls == 1:
            GeminiRateLimitError = type("GeminiRateLimitError", (Exception,), {})
            raise GeminiRateLimitError("Gemini rate limit reached after bounded retries.")
        return {"claims": [{"claim_id": c["claim_id"], "grounding_status": "supported"} for c in claims]}


def test_rate_limited_check_is_retried(monkeypatch):
    obj = base.orchestrator("{}", RateLimitedOnce())
    monkeypatch.setattr(R, "GROUNDING_RATE_LIMIT_PAUSE_SECONDS", 0.0)
    result = obj._ground_claims_isolated([{"claim_id": "C1", "claim_text": "x", "evidence_ids": ["E1"]}],
                                         [{"evidence_id": "E1", "text": "x"}], label="t")
    assert result["claims"][0]["grounding_status"] == "supported" and result["check_failures"] == 0


def test_other_errors_are_not_retried(monkeypatch):
    class Broken:
        calls = 0

        def ground_claims(self, **kwargs):
            Broken.calls += 1
            raise ValueError("bad output")

    obj = base.orchestrator("{}", Broken())
    result = obj._ground_claims_isolated([{"claim_id": "C1", "claim_text": "x", "evidence_ids": ["E1"]}],
                                         [{"evidence_id": "E1", "text": "x"}], label="t")
    assert Broken.calls == 1 and result["claims"][0]["grounding_status"] == "unsupported"


def test_every_briefing_check_is_logged(caplog):
    obj = base.orchestrator(json.dumps(base.DRAFT))
    with caplog.at_level(logging.INFO):
        obj._editorial_briefing(topic="AI", developments=base.DEVELOPMENTS, evidence=[],
                                state={"historical_context": {"records": base.HISTORY}})
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("editorial_briefing_checks="))
    logged = json.loads(line.split("=", 1)[1])
    assert logged["model_implications"] == len(base.DRAFT["implications"])
    assert logged["well_formed_implications"] == 1        # the one citing an unknown fact is dropped
    targets = {c["target"] for c in logged["checks"]}
    assert "implication/0" in targets and "implication_conditions/0" in targets
    assert all(c["status"] == "supported" for c in logged["checks"])


# 3 ---------------------------------------------------------------------

def test_precedent_search_asks_for_a_named_earlier_case():
    from services import hypothesis_evidence_service as hes
    source = inspect.getsource(hes.HypothesisEvidenceService.investigate_gaps)
    assert "TWO different specific earlier cases" in source and "the current development itself" in source
    assert "no years except in the" in source
