"""v6.21.1: fixes from replay RUN-353F7E74B4E94FCD820FAFC4 (first run on v6.21).

1. The red-team semantic review made 751 of the run's 892 Gemini calls (14.3M
   of 15.0M input tokens), mostly re-reviewing the evidence trail, the event
   landscape and internal diagnostics. It now reviews the analysis a reader
   sees; deterministic checks still cover the whole report.
2. Implications: facts and the implication itself must still pass; the
   reasoning step(s) and the "could be wrong if" condition may be labelled
   assumptions (the claim gate v2.0 rule). Contradiction removes it.
3. A precedent page published in the case's year dates the case.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import tests.test_v6_17_report_sections as base
from agents.red_team_agent import RedTeamAgent
from services import editorial_briefing as brief


@pytest.fixture(autouse=True)
def _private_ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_WATCH_ITEMS", str(tmp_path / "watch_items.sqlite3"))
    monkeypatch.setenv("EVENT_INTELLIGENCE_EVENT_MEMORY", str(tmp_path / "event_memory.sqlite3"))


# 1 red-team scope -------------------------------------------------------

class CountingLLM:
    def __init__(self):
        self.prompts = []

    def ask(self, prompt, system_prompt="", **kwargs):
        self.prompts.append(prompt)
        return json.dumps({"issues": []})


def _report():
    evidence_ids = [f"EVID-{i:04d}" for i in range(120)]
    return {
        "research_question": "artificial intelligence",
        "event_landscape": [{"event_id": f"EVENT-{i}", "summary": f"Event {i}", "evidence_ids": [evidence_ids[i]]}
                            for i in range(40)],
        "evidence_trail": [{"evidence_id": eid, "text": "source text " * 20} for eid in evidence_ids],
        "analysis_metadata": {"run_observability": {"counts": list(range(50))}, "notes": ["x"] * 30},
        "connections": [{"text": "A links to B.", "evidence_ids": evidence_ids[:2]}],
        "hidden_insights": [{"text": "Insight.", "evidence_ids": evidence_ids[:3]}],
        "what_is_happening_underneath": [], "second_order_effects": [],
        "counterevidence_and_alternative_explanations": [], "what_to_watch": [], "critical_unknowns": ["u"],
        "editorial_briefing": {"paragraphs": [[{"text": "Briefing sentence.", "fact_ids": ["F1"],
                                                 "citations": [{"evidence_id": evidence_ids[5]}]}]]},
    }


def _evidence():
    return {"evidence_records": [{"evidence_id": f"EVID-{i:04d}", "text": "evidence " * 30} for i in range(120)]}


def test_red_team_reviews_reader_facing_analysis_only(monkeypatch):
    monkeypatch.delenv("EVENT_INTELLIGENCE_RED_TEAM_FULL", raising=False)
    scoped = CountingLLM()
    result = RedTeamAgent(llm_service=scoped).review(topic="t", evidence=_evidence(), constraints={},
                                                     report=_report(), grounding_results={})
    monkeypatch.setenv("EVENT_INTELLIGENCE_RED_TEAM_FULL", "1")
    full = CountingLLM()
    RedTeamAgent(llm_service=full).review(topic="t", evidence=_evidence(), constraints={}, report=_report(),
                                          grounding_results={})
    assert result["review_status"] == "complete"
    assert len(scoped.prompts) * 10 < len(full.prompts), (len(scoped.prompts), len(full.prompts))
    reviewed = " ".join(scoped.prompts)
    assert "A links to B." in reviewed and "Insight." in reviewed and "Briefing sentence." in reviewed


def test_deterministic_checks_still_see_the_whole_report(monkeypatch):
    monkeypatch.delenv("EVENT_INTELLIGENCE_RED_TEAM_FULL", raising=False)
    report = _report()
    report["evidence_trail"][0]["evidence_id"] = "EVID-NOT-IN-EVIDENCE"   # bad reference, outside review scope
    report["event_landscape"][0]["evidence_ids"] = ["EVID-NOT-IN-EVIDENCE"]
    result = RedTeamAgent(llm_service=CountingLLM()).review(topic="t", evidence=_evidence(), constraints={},
                                                            report=report, grounding_results={})
    text = json.dumps(result)
    assert "EVID-NOT-IN-EVIDENCE" in text


# 2 implications with labelled assumptions -------------------------------

INCENTIVE = {"headline": "", "paragraphs": [], "analogies": [], "implications": [
    {"basis": "incentive", "text": "Faster breach reporting appears strategically attractive for Equals Money.",
     "fact_ids": ["F2"], "actor": "Equals Money", "policy_or_regulation": "payments cannot be initiated by AI agents",
     "company_relevance": "it runs an MCP server", "economic_or_strategic_effect": "avoids fines",
     "attractive_move": "report incidents within days", "conditions": "regulators do not enforce the rule",
     "counterparty": "the FCA", "counterparty_consequence": "sees fewer agent-initiated payment incidents",
     "watch_for": "an enforcement notice", "horizon": "6-12 months"}]}


def _checked(statuses_by_prefix):
    facts, cases = base.brief.select_facts(base.DEVELOPMENTS), []
    draft = brief.parse_draft(INCENTIVE, facts, cases)
    claims = brief.grounding_claims(draft, facts, cases)
    statuses = {c["claim_id"]: ("supported" if c["_bar"] == ["supported"] else "inference_supported") for c in claims}
    for claim in claims:
        for prefix, status in statuses_by_prefix.items():
            if claim["claim_text"].startswith(prefix) or claim.get("_field") == prefix:
                statuses[claim["claim_id"]] = status
    return brief.sanitize(draft, claims, statuses, facts, cases)


def test_unsourced_reasoning_and_condition_become_labelled_assumptions():
    out = _checked({"economic_or_strategic_effect": "unsupported", "conditions": "unsupported"})
    assert len(out["implications"]) == 1
    assert set(out["implications"][0]["assumption_fields"]) == {"economic_or_strategic_effect", "conditions"}


def test_unsupported_rule_or_implication_text_still_removes_it():
    assert _checked({brief.POLICY_EXISTS_PREFIX: "inference_supported"})["implications"] == []
    out = _checked({"economic_or_strategic_effect": "unsupported", "attractive_move": "unsupported"})
    assert len(out["implications"]) == 1        # two steps: allowed
    core = INCENTIVE["implications"][0]["text"]
    assert _checked({brief.INCENTIVE_PREFIX + core: "unsupported"})["implications"] == []


def test_contradiction_always_removes_the_implication():
    assert _checked({"conditions": "contradicted"})["implications"] == []
    assert _checked({"economic_or_strategic_effect": "contradicted"})["implications"] == []


def test_dashboard_marks_assumptions():
    import pathlib
    source = (pathlib.Path(__file__).resolve().parents[1] / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    assert 'tag("economic_or_strategic_effect")' in source and 'tag("conditions")' in source


# 3 precedent dated by its page -----------------------------------------

def test_page_published_in_the_case_year_dates_the_case():
    from services.hypothesis_evidence_service import HypothesisEvidenceService
    body = ("Staff at the US Department of Agriculture have been told to avoid the term climate change. "
            "The guidance was later withdrawn after public criticism.")

    class Model:
        model_name = "m"
        timeout_seconds = 30

        def ask(self, prompt, **kwargs):
            first, outcome = body.split(". ", 1)
            return json.dumps({"review_complete": True, "records": [{
                "quote": first + ".", "relation": "supports", "claim_kind": "reported_claim", "connection": "c",
                "historical_case": {"year": 2017, "date_quote": first + ".", "outcome_quote": outcome,
                                    "similarity": "official wording change", "difference": "agency, not president"}}]})

    service = HypothesisEvidenceService(web_search_service=None, source_fetch_service=None, llm_service=Model())
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    out = service._process_source(source={"url": "https://theguardian.com/2017/usda", "text_content": body,
                                          "published_at": "2017-08-07T00:00:00Z"},
                                  context={}, candidate={"insight": "i"}, now=now, timeout=10, enforce_window=False)
    case = out["accepted_evidence"][0]["historical_case"]
    assert case["year"] == 2017 and case["date_basis"] == "page_publication_date"
    # A page from another year does not date it.
    out = service._process_source(source={"url": "https://x.example/2026", "text_content": body,
                                          "published_at": "2026-09-01T00:00:00Z"},
                                  context={}, candidate={"insight": "i"}, now=now, timeout=10, enforce_window=False)
    assert out["accepted_evidence"] == []
