"""v6.25: an insight is shown even when its counter-evidence search could not
be completed - disclosed, with confidence capped at moderate - and GPT's two
v6.24 review points.

Product decision (agreed with the user): an insight backed by its evidence is
shown, saying which evidence backs it, even when contrary evidence cannot be
found or searched. This is how comparable products work (Consensus shows a
claim with its supporting and disputing sources; AlphaSense and Perplexity
cite each statement; ICD 203 publishes judgments with confidence levels and
stated information gaps). Until v6.24 the app withheld such an insight
(fail closed), which hid the strongest candidate of replay
RUN-FB8AB22DBDD548B5BB6D4898 when the search allowance ran out.
"""

from __future__ import annotations

import pathlib

import services.research_orchestrator_service as ros
from services import claim_support_gate as gate
from services import grounded_search as gs
from services import pattern_grid as grid
from tests.test_claim_support_gate import DEEP_PROFILE, PASSING, Investigator, candidate, make_orchestrator
from tests.test_v6_21_4_search_coverage import _events_and_evidence


class Incomplete(Investigator):
    def investigate(self, **kw):
        receipt = super().investigate(**kw)
        receipt["assessment"]["status"] = "incomplete"
        return receipt


def _release(investigator, monkeypatch):
    monkeypatch.setattr(ros, "DEEP_CLAIM_GATE_EXTRA_SYNTHESES", 0)
    obj = make_orchestrator(verdicts={"INSIGHTREV-U": PASSING}, investigator=investigator)
    obj._source_reference = lambda sid: {}
    events, evidence = _events_and_evidence()
    released, _, _ = obj._claim_supported_release(
        topic="ai", candidates=[candidate("U")], grounded={"INSIGHTREV-U": PASSING()}, events=events,
        evidence=evidence, graph_context={}, constraints={}, blueprint={}, profile=DEEP_PROFILE,
        state={"warnings": [], "run_id": "RUN-U"})
    return released


def test_unsearched_insight_is_shown_labelled_with_confidence_capped(monkeypatch):
    released = _release(Incomplete(), monkeypatch)
    assert len(released) == 1
    meta = released[0]["metadata"]
    assert meta["counterevidence_check"]["status"] == "incomplete"
    confidence = meta.get("analytic_confidence") or {}
    if confidence:
        assert confidence["level"] == "moderate"
        assert "could not be completed" in confidence["basis"] and "no counterevidence was found" not in confidence["basis"]


def test_completed_search_is_not_labelled(monkeypatch):
    released = _release(Investigator(), monkeypatch)
    assert len(released) == 1 and "counterevidence_check" not in released[0]["metadata"]


def test_found_counterevidence_still_counts_against_quality():
    item = {"counterevidence_ids": ["EVID-C"]}
    assert gate.counterevidence_exemption("incomplete", item) == ()
    assert gate.counterevidence_exemption("completed_found", {}) == ()
    assert gate.counterevidence_exemption("incomplete", {}) == ("counterevidence_quality",)
    assert gate.counterevidence_exemption("", {}) == ()


def test_report_and_dashboard_show_the_disclosure():
    import inspect
    source = inspect.getsource(ros.ResearchOrchestratorService)
    assert '"counterevidence_check": _json_safe(insight_metadata.get("counterevidence_check", {}))' in source
    dash = (pathlib.Path(__file__).resolve().parents[1] / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    assert "**Counter-evidence check incomplete:**" in dash


# GPT review of v6.24 ----------------------------------------------------------

def test_site_names_as_titles_are_dropped():
    redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/Z"
    for title in ("YouTube", "Reddit", "TikTok", "X"):
        assert not gs.is_article_source({"url": redirect, "title": title}), title
    assert gs.is_article_source({"url": redirect, "title": "Reuters"})


def test_generic_capitalised_groups_are_not_named_parties():
    for generic in ("Government officials", "Users", "Regulators", "Tech Companies"):
        assert not grid.is_named_party(generic), generic
    for named in ("US government", "Department of Home Affairs", "AIHW", "Services Australia"):
        assert grid.is_named_party(named), named
