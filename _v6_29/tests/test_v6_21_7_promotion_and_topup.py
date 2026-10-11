"""v6.21.7: fixes from replay RUN-7CD0B58AA15D4B5E83571188 (first run on v6.21.6).

1. A repaired candidate passed every claim and quality check but the model had
   labelled it "underlying_dynamic". It counted as released, so exploration
   stopped, and the run then discarded it (no release route) -> "None this run".
   Such a finding is now held to the hidden-insight bar (release rules +
   non-obviousness) and released as one when it passes; otherwise it does not
   count as released and exploration continues.
2. The briefing model wrote 2 implications although 4-6 were asked for. One
   top-up request asks for the missing ones.
"""

from __future__ import annotations

import json

import pytest

import services.research_orchestrator_service as ros
import tests.test_v6_17_report_sections as base
from services import claim_support_gate as gate
from services import editorial_briefing as brief
from tests.test_claim_support_gate import DEEP_PROFILE, FAILING, PASSING, candidate, make_orchestrator
from tests.test_v6_21_4_search_coverage import _events_and_evidence


@pytest.fixture(autouse=True)
def _private_ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_WATCH_ITEMS", str(tmp_path / "watch_items.sqlite3"))
    monkeypatch.setenv("EVENT_INTELLIGENCE_EVENT_MEMORY", str(tmp_path / "event_memory.sqlite3"))


def _dynamic(name):
    item = candidate(name)
    item["finding_tier"] = "underlying_dynamic"
    item["metadata"]["finding_tier"] = "underlying_dynamic"
    return item


def _run(obj, candidates, grounded):
    events, evidence = _events_and_evidence()
    state = {"warnings": [], "run_id": "RUN-PROMO"}
    released, _, _ = obj._claim_supported_release(
        topic="ai", candidates=candidates, grounded=grounded, events=events, evidence=evidence,
        graph_context={}, constraints={}, blueprint={}, profile=DEEP_PROFILE, state=state)
    return released, state


# 1 ---------------------------------------------------------------------

def test_passed_dynamic_is_released_as_a_hidden_insight(monkeypatch):
    monkeypatch.setattr(ros, "DEEP_CLAIM_GATE_EXTRA_SYNTHESES", 0)
    obj = make_orchestrator(verdicts={"INSIGHTREV-DYN": PASSING})
    released, state = _run(obj, [_dynamic("DYN")], {"INSIGHTREV-DYN": PASSING()})
    assert len(released) == 1
    assert ros.ResearchOrchestratorService._has_release_route(released[0])
    assert released[0]["metadata"]["claim_support_gate"]["promoted_from_tier"] == "underlying_dynamic"
    assert obj.nonobvious_calls                                    # held to the hidden-insight bar
    assert state["claim_support_gate"]["attempts"][0]["promotion"]["from"] == "underlying_dynamic"


def test_dynamic_failing_non_obviousness_does_not_stop_exploration(monkeypatch):
    monkeypatch.setattr(ros, "DEEP_CLAIM_GATE_EXTRA_SYNTHESES", 0)
    explored = candidate("EXPLORED")
    obj = make_orchestrator(verdicts={"INSIGHTREV-DYN": PASSING, "INSIGHTREV-EXPLORED": PASSING},
                            new_candidates=[[explored]],
                            nonobvious={"passed": False, "reasons": ["conclusion_single_development"]})
    released, state = _run(obj, [_dynamic("DYN")], {"INSIGHTREV-DYN": PASSING()})
    assert obj.analyze_calls, "exploration must run when the dynamic is not releasable"
    assert state["claim_support_gate"]["exploration"]["combinations"]
    assert all(r["insight_revision_id"] != "INSIGHTREV-DYN" or
               ros.ResearchOrchestratorService._has_release_route(r) for r in released)


def test_dynamic_failing_hidden_release_rules_is_not_released(monkeypatch):
    monkeypatch.setattr(ros, "DEEP_CLAIM_GATE_EXTRA_SYNTHESES", 0)
    monkeypatch.setattr(ros, "INSIGHT_EXPLORATION_COMBINATIONS", 0)
    item = _dynamic("DYN")
    item["metadata"]["scope_validation"]["event_dependency"] = "partial"   # fine for a dynamic, not for hidden
    obj = make_orchestrator(verdicts={"INSIGHTREV-DYN": PASSING})
    released, state = _run(obj, [item], {"INSIGHTREV-DYN": PASSING()})
    assert released == []
    attempt = state["claim_support_gate"]["attempts"][0]
    assert attempt["promotion"]["rejection"] == "insight_not_necessarily_event_derived"
    assert any("no_release_route" in r for r in attempt["reasons"])


def test_as_hidden_insight_relabels_a_copy():
    item = _dynamic("DYN")
    out = gate.as_hidden_insight(item, "r")
    assert out["finding_tier"] == "hidden_insight" and out["metadata"]["finding_tier"] == "hidden_insight"
    assert item["finding_tier"] == "underlying_dynamic"


# 2 ---------------------------------------------------------------------

class TwoThenMore:
    timeout_seconds = 60
    max_retries = 3

    def __init__(self):
        self.prompts = []

    def ask(self, prompt, **kwargs):
        self.prompts.append(prompt)
        if "TOP-UP REQUEST" in prompt:
            extra = dict(base.DRAFT["implications"][0])
            extra["text"] = "Equals Money's read-only design may become the default for UK payment APIs by 2027."
            return json.dumps({"implications": [extra]})
        if "ordinary informed reader" in prompt:
            return json.dumps({"takeaways": []})
        return json.dumps(base.DRAFT)


class AllPass:
    def ground_claims(self, *, claims, evidence_records):
        return {"claims": [{"claim_id": c["claim_id"],
                            "grounding_status": "unsupported" if c["claim_id"].startswith("NOVELTY") else
                            ("supported" if c.get("epistemic_type") == "fact" else "inference_supported"),
                            "supporting_evidence_ids": list(c.get("evidence_ids") or [])[:1]} for c in claims]}


def test_too_few_implications_triggers_one_topup():
    obj = base.orchestrator("{}", AllPass())
    model = TwoThenMore()
    obj.extraction_agent.llm = model
    out = obj._editorial_briefing(topic="AI", developments=base.DEVELOPMENTS, evidence=[],
                                  state={"historical_context": {"records": base.HISTORY}})
    assert sum("TOP-UP REQUEST" in p for p in model.prompts) == 1
    texts = [i["text"] for i in out["implications"]]
    assert any("Equals Money's read-only design" in t for t in texts)
    assert len(texts) == 2                      # the one well-formed draft implication + the top-up


def test_topup_prompt_lists_existing_implications():
    prompt = brief.topup_prompt("t", brief.select_facts(base.DEVELOPMENTS), [], ["Already said X."], 3)
    assert "Already said X." in prompt and "Write 3 MORE implications" in prompt
