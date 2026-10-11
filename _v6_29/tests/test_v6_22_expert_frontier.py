"""v6.22: the novelty bar is published expert analysis, not the headline reader.

After v6.21.8 the user judged the reporting-rule insight "nice enough, but not
standard". A check of published coverage showed why: trade press (Insurance
Business, 5 Oct 2026) and research-group notes (Cloud Security Alliance, 2-3 Oct)
had already made that point and gone further (five agencies affected, insurers'
late-notice rules, APRA's warning that AI supplier contracts lack notification
terms).

Method (how AI research systems judge novelty): Google's co-scientist
Reflection agent and Sakana's AI Scientist search the published literature and
keep only ideas it does not already contain; SciMON rewrites ideas until they
differ from retrieved work. Here:
1. gap research runs one expert-commentary search per development;
2. a hidden insight must not be stated or implied by that commentary (fail closed);
3. implications already said by experts are labelled "widely expected";
4. the model is told to start from the experts' view and go past it;
5. the report quotes "What experts are already saying" with sources.
"""

from __future__ import annotations

import inspect
import json
import pathlib

import pytest

import services.research_orchestrator_service as ros
import tests.test_v6_17_report_sections as base
from services import editorial_briefing as brief
from services import non_obviousness as nob
from services.hypothesis_evidence_service import HypothesisEvidenceService


@pytest.fixture(autouse=True)
def _private_ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_WATCH_ITEMS", str(tmp_path / "watch_items.sqlite3"))
    monkeypatch.setenv("EVENT_INTELLIGENCE_EVENT_MEMORY", str(tmp_path / "event_memory.sqlite3"))


EVENTS = [{"event_id": "EVENT-B373", "underlying_event_id": "U-BREACH",
           "title": "OpenAI agent accessed Australian government systems"}]
NEWS = {"evidence_id": "EVID-B168", "event_id": "EVENT-B373", "source_id": "S1",
        "text": "The breach took place back in June, was discovered by OpenAI in August, and Australia was informed "
                "only on September 10."}
SOCI = {"evidence_id": "EVID-CA90", "source_id": "S3", "analytical_context_only": True, "gap_type": "policy",
        "text": "Cybersecurity incidents, including those involving AI, must be reported to Home Affairs under the SOCI Act."}
# Paraphrase of the published analysis (Insurance Business, 5 Oct 2026).
EXPERT = {"evidence_id": "EVID-EXPERT", "source_id": "S9", "analytical_context_only": True, "gap_type": "commentary",
          "source_url": "https://www.insurancebusinessmag.com/au/news/cyber/x.aspx", "published_at": "2026-10-05",
          "gap_for": "OpenAI agent breach",
          "text": "A vendor's silence about an AI-agent breach tests notification clauses: the customer cannot report "
                  "an incident it does not know about, so the reporting duty depends on the vendor telling it."}
REPORTED = dict(EXPERT, evidence_id="EVID-REPORTED", gap_type="reported",
                text="DCCEEW is the fifth Australian government body affected by the OpenAI agent incidents.")
CONCLUSION = ("Operators cannot meet the SOCI reporting duty for AI-agent incidents they learn of only from the vendor, "
              "months later.")


def grounding():
    return {"claims": [{"claim_id": "C-1", "claim_text": CONCLUSION, "epistemic_type": "hidden_insight",
                        "grounding_status": "inference_supported",
                        "supporting_evidence_ids": ["EVID-B168", "EVID-CA90"]}]}


def checker(expert_status, alone="unsupported", fail_experts=False):
    calls = []

    def ground(claims, records):
        cid = claims[0]["claim_id"]
        calls.append((cid, sorted(r["evidence_id"] for r in records)))
        if cid.startswith("EXPERT") and fail_experts:
            raise TimeoutError("slow")
        status = ("unsupported" if cid.startswith("OBV") else expert_status if cid.startswith("EXPERT") else alone)
        return {"claims": [{"claim_id": c["claim_id"], "grounding_status": status} for c in claims]}
    return ground, calls


def _plain_echo(prompt):
    """v6.26: the plain-language rewrite step; this fake returns the claim unchanged."""
    import json as _json
    claim = prompt.split("Claim: ", 1)[1].split("\n\n", 1)[0]
    return _json.dumps({"plain": claim})


def ask(prompt):
    if "ONE plain sentence" in prompt:
        return _plain_echo(prompt)
    return '{"takeaways": ["OpenAI agents breached Australian systems."]}'


# 1 the expert test in non-obviousness ----------------------------------

def test_conclusion_already_said_by_experts_is_not_a_hidden_insight():
    ground, calls = checker("inference_supported")
    out = nob.check(grounding=grounding(), evidence=[NEWS, SOCI, EXPERT], events=EVENTS, ground=ground, ask=ask)
    assert not out["passed"] and out["reasons"] == ["conclusion_already_in_expert_commentary"]
    assert ("EXPERT-0", ["EVID-EXPERT"]) in calls                     # checked against the commentary only
    assert "experts" in nob.brief(out["reasons"])[0]


def test_conclusion_beyond_the_experts_passes_and_says_so():
    ground, _ = checker("unsupported")
    out = nob.check(grounding=grounding(), evidence=[NEWS, SOCI, EXPERT], events=EVENTS, ground=ground, ask=ask)
    assert out["passed"] and out["expert_commentary_records"] == 1
    assert out["claims"][0]["expert_commentary_status"] == "unsupported"


def test_expert_check_fails_closed():
    ground, _ = checker("unsupported", fail_experts=True)
    out = nob.check(grounding=grounding(), evidence=[NEWS, SOCI, EXPERT], events=EVENTS, ground=ground, ask=ask)
    assert not out["passed"] and out["reasons"] == ["non_obviousness_untested:expert_check_TimeoutError"]


def test_no_commentary_found_still_runs_the_other_tests():
    ground, calls = checker("supported")
    out = nob.check(grounding=grounding(), evidence=[NEWS, SOCI], events=EVENTS, ground=ground, ask=ask)
    assert out["passed"] and out["expert_commentary_records"] == 0
    assert not any(cid.startswith("EXPERT") for cid, _ in calls)


def test_opinions_and_reported_facts_are_never_constraints():
    assert nob.is_constraint(SOCI)
    assert not nob.is_constraint(EXPERT) and not nob.is_constraint(REPORTED)
    # a commentary record cannot open the constraint route
    ground, _ = checker("unsupported")
    g = grounding()
    g["claims"][0]["supporting_evidence_ids"] = ["EVID-B168", "EVID-EXPERT"]
    out = nob.check(grounding=g, evidence=[NEWS, EXPERT], events=EVENTS, ground=ground, ask=ask)
    assert out["reasons"] == ["conclusion_single_development"]


def test_expert_opinion_is_not_an_independent_observation():
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    events_map, _ = obj._convergence_maps([SOCI, EXPERT], [])
    assert "EVID-CA90" in events_map and "EVID-EXPERT" not in events_map


# 2 gap research ---------------------------------------------------------

def test_gap_research_finds_and_labels_expert_commentary():
    service, search, _ = base._gap_setup()
    receipt = service.investigate_gaps(development=base.DEVELOPMENTS[0], as_of="2026-10-09T00:00:00+00:00",
                                       search=search)
    commentary = [r for r in receipt["records"] if r["gap_type"] == "commentary"]
    assert len(commentary) == 1 and "liability" in commentary[0]["text"]


def test_facts_on_commentary_pages_are_reported_context(monkeypatch):
    original = base.GapModel.ask

    def ask_(self, prompt, **kwargs):
        reply = original(self, prompt, **kwargs)
        if kwargs["metadata"]["task"] != "gap_query_plan" and "Payments lawyers" in prompt:
            data = json.loads(reply)
            data["records"][0]["claim_kind"] = "observation"
            return json.dumps(data)
        return reply

    monkeypatch.setattr(base.GapModel, "ask", ask_)
    service, search, _ = base._gap_setup()
    receipt = service.investigate_gaps(development=base.DEVELOPMENTS[0], as_of="2026-10-09T00:00:00+00:00",
                                       search=search)
    assert [r["gap_type"] for r in receipt["records"] if "lawyers" in r["text"]] == ["reported"]


def test_commentary_search_always_runs_within_60_days(monkeypatch):
    """The planner omitted the commentary search: it is added anyway."""
    monkeypatch.setattr(base, "GAP_QUERIES", {g: q for g, q in base.GAP_QUERIES.items() if g != "commentary"})
    service, search, _ = base._gap_setup()
    receipt = service.investigate_gaps(development=base.DEVELOPMENTS[0], as_of="2026-10-09T00:00:00+00:00",
                                       search=search)
    auto = [q for q in receipt["queries"] if q["gap"] == "commentary"]
    assert auto == [{"gap": "commentary", "query": (base.DEVELOPMENTS[0]["title"] + " analysis implications").casefold()}]
    assert HypothesisEvidenceService.COMMENTARY_WINDOW_DAYS == 60 and HypothesisEvidenceService.GAP_MAX_QUERIES == 6


# 3 briefing, report and prompt -----------------------------------------

def test_implication_already_said_by_experts_is_labelled():
    items = [{"text": "Operators will depend on vendors to meet reporting duties."}, {"text": "Something new."}]
    claims = brief.novelty_claims(items, [EXPERT], False, commentary_ids=["EVID-EXPERT"])
    experts = [c for c in claims if c["_test"] == "experts"]
    assert len(experts) == 2 and experts[0]["evidence_ids"] == ["EVID-EXPERT"]
    statuses = {c["claim_id"]: "unsupported" for c in claims}
    statuses["NOVELTY-EXPERT-00"] = "inference_supported"
    fresh, expected = brief.apply_novelty(items, claims, statuses)
    assert [i["text"] for i in fresh] == ["Something new."]
    assert expected[0]["novelty_reasons"] == ["already_in_expert_commentary"]


def test_commentary_is_quoted_with_its_source_and_never_used_as_a_fact():
    items = brief.commentary_items([SOCI, EXPERT, dict(EXPERT, evidence_id="DUP"), REPORTED])
    assert [i["evidence_id"] for i in items] == ["EVID-EXPERT"]
    assert items[0]["source_url"].startswith("https://") and items[0]["published_at"] == "2026-10-05"
    facts = brief.select_context_facts([SOCI, EXPERT, REPORTED])
    assert [f["evidence_id"] for f in facts] == ["EVID-CA90", "EVID-REPORTED"]


def test_briefing_carries_the_expert_commentary():
    class Grounding:
        def ground_claims(self, *, claims, evidence_records):
            return {"claims": [{"claim_id": c["claim_id"],
                                "grounding_status": "unsupported" if c["claim_id"].startswith("NOVELTY") else "supported"}
                               for c in claims]}

    obj = base.orchestrator(json.dumps(base.DRAFT), Grounding())
    out = obj._editorial_briefing(topic="AI", developments=base.DEVELOPMENTS, evidence=[],
                                  state={"historical_context": {"records": base.HISTORY},
                                         "gap_context_records": [EXPERT]})
    assert [i["evidence_id"] for i in out["expert_commentary"]] == ["EVID-EXPERT"]


def test_strategic_model_is_told_to_go_past_the_experts():
    import agents.strategic_inference_agent as agent
    source = inspect.getsource(agent)
    assert "THE EXPERT FRONTIER" in source and "Never cite it as a premise" in source
    assert '"gap_type": _clean(record.get("gap_type"))' in source


def test_dashboard_shows_what_experts_already_say():
    dash = (pathlib.Path(__file__).resolve().parents[1] / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    assert "#### What experts are already saying" in dash and "render_expert_commentary(briefing)" in dash
    assert "(experts already say this)" in dash and "expert_commentary_checked" in dash
