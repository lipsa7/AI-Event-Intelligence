"""v6.20.9: fixes from the first live Deep run on v6.20.8 (RUN-ED24092548084234B2300689).

1. The briefing's claim check crashed on duplicate evidence IDs (gap context
   records were in the evidence AND in state["gap_context_records"]), so every
   implication and forecast was lost.
2. Gap research read only 5 of 8 found pages, and a precedent page published
   in the last 31 days was refused even when it described a dated past case.
3. Numbered mechanisms split into bare "1." / "2." claims that always failed.
4. Why each gap-research page did or did not become evidence is now logged.
"""

from __future__ import annotations

import json
import logging

import pytest

import tests.test_v6_17_report_sections as base
from services import research_orchestrator_service as ros
from services.grounding_service import GroundingService
from services.hypothesis_evidence_service import HypothesisEvidenceService
from services.research_orchestrator_service import ResearchOrchestratorService as R


@pytest.fixture(autouse=True)
def _private_ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_WATCH_ITEMS", str(tmp_path / "watch_items.sqlite3"))
    monkeypatch.setenv("EVENT_INTELLIGENCE_EVENT_MEMORY", str(tmp_path / "event_memory.sqlite3"))


class StrictGrounding:
    """Applies the production evidence validation (duplicate IDs raise), then supports every claim."""

    def __init__(self):
        self.calls = 0

    def ground_claims(self, *, claims, evidence_records):
        GroundingService._normalize_evidence_records(GroundingService.__new__(GroundingService), evidence_records)
        self.calls += 1
        return {"claims": [{"claim_id": c["claim_id"],  # v6.21.6: novelty tests find nothing already said
                            "grounding_status": "unsupported" if c["claim_id"].startswith("NOVELTY") else "supported"}
                           for c in claims]}


RULE = {"evidence_id": "EVID-RULE", "source_id": "SOURCE-RULE", "gap_type": "policy",
        "gap_for": "agent kill switches", "published_at": "2026-09-01",
        "source_url": "https://rules.example/payments",
        "source_excerpt": "The payments regulator requires automated payment systems to be registered."}


# 1 ---------------------------------------------------------------------

def test_production_validation_rejects_duplicates_so_the_double_must_be_real():
    with pytest.raises(Exception):
        StrictGrounding().ground_claims(claims=[], evidence_records=[RULE, dict(RULE)])


def test_briefing_survives_gap_context_already_in_evidence():
    """The live-run shape: the gap record is in evidence AND in gap_context_records."""
    grounding = StrictGrounding()
    obj = base.orchestrator(json.dumps(base.DRAFT), grounding)
    state = {"gap_context_records": [RULE], "historical_context": {"records": base.HISTORY}}
    evidence = [dict(RULE), {"evidence_id": "EVID-1", "text": "Okta added a kill switch."}]
    out = obj._editorial_briefing(topic="AI", developments=base.DEVELOPMENTS, evidence=evidence, state=state)
    assert grounding.calls >= 1  # v6.20.10: one check per claim
    assert out["mode"] == "checked_analysis", out.get("mode")
    assert out["implications"]


def test_historical_case_also_in_evidence_does_not_break_the_briefing():
    grounding = StrictGrounding()
    obj = base.orchestrator(json.dumps(base.DRAFT), grounding)
    state = {"historical_context": {"records": base.HISTORY}}
    out = obj._editorial_briefing(topic="AI", developments=base.DEVELOPMENTS,
                                  evidence=[dict(base.HISTORY[0])], state=state)
    assert out["mode"] == "checked_analysis"


def test_unique_evidence_records_keeps_first_and_drops_unciteable():
    first = {"evidence_id": "E1", "text": "first"}
    out = ros._unique_evidence_records([first, {"evidence_id": "E1", "text": "second"}, {"text": "no id"},
                                        "junk", {"id": "E2"}])
    assert out == [first, {"id": "E2"}]


# 2 ---------------------------------------------------------------------

def test_every_found_gap_page_is_read():
    assert HypothesisEvidenceService.GAP_MAX_SOURCES >= HypothesisEvidenceService.GAP_MAX_QUERIES * 2
    service, search, _ = base._gap_setup(results_per_query=2)
    receipt = service.investigate_gaps(development=base.DEVELOPMENTS[0], as_of="2026-10-09T00:00:00+00:00",
                                       search=search)
    assert not any("source_fetch_budget_exhausted" in note for note in receipt["limitations"]), receipt["limitations"]


def test_recent_page_describing_a_dated_past_case_is_a_precedent(monkeypatch):
    text, _ = base.GAP_PAGES["precedent"]
    monkeypatch.setitem(base.GAP_PAGES, "precedent", (text, "2026-09-30T10:00:00Z"))  # 9 days before as-of
    service, search, _ = base._gap_setup()
    receipt = service.investigate_gaps(development=base.DEVELOPMENTS[0], as_of="2026-10-09T00:00:00+00:00",
                                       search=search)
    assert receipt["found_by_gap"]["precedent"] == 1, receipt["limitations"]
    case = next(r for r in receipt["records"] if r.get("historical_case"))
    assert case["historical_case"]["year"] == 2014


def test_precedent_still_needs_a_dated_past_case(monkeypatch):
    """Relaxing the page-date window must not let a current-year story count as a precedent."""
    text = ("This year, banks capped third-party payment apps at read-only access. "
            "Within weeks the caps became standard across the industry.")
    monkeypatch.setitem(base.GAP_PAGES, "precedent", (text, "2026-09-30T10:00:00Z"))
    original = base.GapModel.ask

    def ask(self, prompt, **kwargs):
        reply = original(self, prompt, **kwargs)
        if kwargs["metadata"]["task"] != "gap_query_plan" and "This year" in prompt:
            data = json.loads(reply)
            data["records"][0]["historical_case"]["year"] = 2026
            return json.dumps(data)
        return reply

    monkeypatch.setattr(base.GapModel, "ask", ask)
    service, search, _ = base._gap_setup()
    receipt = service.investigate_gaps(development=base.DEVELOPMENTS[0], as_of="2026-10-09T00:00:00+00:00",
                                       search=search)
    assert receipt["found_by_gap"]["precedent"] == 0


def test_non_precedent_gaps_still_enforce_their_date_window(monkeypatch):
    text, _ = base.GAP_PAGES["dependency"]
    monkeypatch.setitem(base.GAP_PAGES, "dependency", (text, "2019-01-01T10:00:00Z"))  # older than 2 years
    service, search, _ = base._gap_setup()
    receipt = service.investigate_gaps(development=base.DEVELOPMENTS[0], as_of="2026-10-09T00:00:00+00:00",
                                       search=search)
    assert receipt["found_by_gap"]["dependency"] == 0


# 3 ---------------------------------------------------------------------

def test_numbered_mechanism_has_no_bare_marker_claims():
    units = R._split_material_claims(
        "1. OpenAI agents crawled the site. 2. The notice took nine days. 3) Regulators asked why.")
    assert units == ["OpenAI agents crawled the site.", "The notice took nine days.", "Regulators asked why."]


def test_abbreviations_do_not_split_a_claim():
    assert R._split_material_claims("The U.S. Government acted. Sen. Sanders objected.") == [
        "The U.S. Government acted.", "Sen. Sanders objected."]


def test_ordinary_numbers_are_kept():
    assert R._split_material_claims("Revenue rose 2. Costs fell 3.5% in 2026.") == [
        "Revenue rose 2.", "Costs fell 3.5% in 2026."]


def test_mechanism_claims_from_a_numbered_insight():
    obj = R.__new__(R)
    insight = {"insight_id": "INSIGHT-1", "insight_revision_id": "INSIGHTREV-1",
               "insight": "Agents expose slow breach reporting.", "evidence_ids": ["EVID-1", "EVID-2"],
               "mechanism": {"description": "1. Agents act at machine speed. 2. Reporting stays manual.",
                             "evidence_ids": ["EVID-1", "EVID-2"]}}
    mechanism = [c["claim_text"] for c in obj._claims_from_insight(insight) if c["epistemic_type"] == "mechanism"]
    assert mechanism == ["Agents act at machine speed.", "Reporting stays manual."]


# 4 ---------------------------------------------------------------------

def test_gap_page_outcomes_are_logged(caplog):
    service, search, _ = base._gap_setup()
    with caplog.at_level(logging.INFO, logger="event_intelligence.services.hypothesis_evidence_service"):
        receipt = service.investigate_gaps(development=base.DEVELOPMENTS[0], as_of="2026-10-09T00:00:00+00:00",
                                           search=search)
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("gap_research_sources="))
    logged = json.loads(line.split("=", 1)[1])
    assert {s["gap"] for s in logged["sources"]} == {"precedent", "policy", "dependency", "numbers", "commentary"}
    assert all(s["outcome"] == "reviewed" for s in logged["sources"])
    assert receipt["source_reviews"] == logged["sources"]


def test_refused_gap_page_logs_its_reason(monkeypatch, caplog):
    text, _ = base.GAP_PAGES["numbers"]
    monkeypatch.setitem(base.GAP_PAGES, "numbers", (text, "2019-01-01T10:00:00Z"))
    service, search, _ = base._gap_setup()
    with caplog.at_level(logging.INFO, logger="event_intelligence.services.hypothesis_evidence_service"):
        receipt = service.investigate_gaps(development=base.DEVELOPMENTS[0], as_of="2026-10-09T00:00:00+00:00",
                                           search=search)
    refused = [s for s in receipt["source_reviews"] if s["gap"] == "numbers"]
    assert refused and refused[0]["outcome"] == "not_reviewed"
    assert "outside the requested research window" in refused[0]["reason"]
