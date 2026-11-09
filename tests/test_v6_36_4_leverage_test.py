"""v6.36.4 - leverage is tested the way resource-dependence theory defines it (Pfeffer & Salancik):
A has leverage over B only when B needs what A controls, B has few alternatives, AND B needs A more
than A needs B. Before, the app asked for the dependency and control point; alternatives were optional
and never checked, and the holder's own dependence (asymmetry) was never asked.

Now, in the briefing, the hypothesis lab and the actor moves:
- the counterparty's alternatives and what the holder needs back are required for leverage;
- each is checked against the sources as its own step and shown with its label;
- the lab reports a leverage test (passed / not established / incomplete); the judge calls an item a
  leverage decision only when the test passed;
- every structural filter's removals are counted and shown, so a thin report can be diagnosed.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

from services import actor_moves as moves
from services import editorial_briefing as brief
from services import insight_evaluator as judge
from services import strategic_hypothesis_lab as lab
from tests.test_v6_32_hypothesis_lab import FACTS as LAB_FACTS, rich_row

ROOT = Path(__file__).resolve().parents[1]
FACTS = [{"fact_id": "F1", "development": "agents", "evidence_id": "E1",
          "text": "Only OpenAI holds the activity logs of its agents used by the Australian Government."}]


def _lever(**over):
    item = {"basis": "leverage", "text": "OpenAI's logs give it leverage over Australian agencies.", "fact_ids": ["F1"],
            "conditions": "agencies keep using the agents", "holder": "OpenAI", "counterparty": "Australian Government",
            "leverage_source": "the agent activity logs", "mechanism": "agencies need the logs to audit the agents",
            "counterparty_alternatives": "none named in the facts",
            "holder_dependence": "OpenAI needs the contract, one of many government customers"}
    item.update(over)
    return item


# --- briefing ---------------------------------------------------------------------------------

def test_briefing_leverage_needs_alternatives_and_holder_dependence():
    ok = brief.parse_draft({"implications": [_lever()]}, FACTS, [])
    assert len(ok["implications"]) == 1 and ok["leverage_rejected"] == []
    for missing in ("counterparty_alternatives", "holder_dependence"):
        out = brief.parse_draft({"implications": [_lever(**{missing: ""})]}, FACTS, [])
        assert out["implications"] == [] and out["leverage_rejected"], missing
    _, fit = brief.enforce_policy_fit(brief.parse_draft({"implications": [_lever(holder_dependence="")]}, FACTS, []),
                                      "OpenAI")
    assert len(fit["leverage_rejected"]) == 1
    prompt = brief.build_prompt("ai", FACTS, [])
    assert "counterparty_alternatives" in prompt and "holder_dependence" in prompt and "not leverage" in prompt


def test_briefing_checks_each_part_of_the_test_as_a_labelled_step():
    draft = brief.parse_draft({"implications": [_lever()]}, FACTS, [])
    steps = {c["_field"]: c for c in brief.grounding_claims(draft, FACTS, []) if c.get("_role") == "step"}
    assert {"mechanism", "counterparty_alternatives", "holder_dependence"} <= set(steps)
    assert "Australian Government's realistic alternatives to OpenAI" in steps["counterparty_alternatives"]["claim_text"]
    assert "OpenAI depends on Australian Government less than" in steps["holder_dependence"]["claim_text"]


def test_topup_rejections_are_counted_too():
    text = (ROOT / "services" / "research_orchestrator_service.py").read_text(encoding="utf-8")
    assert text.count('for key in ("position_rejected", "leverage_rejected"):') == 2


# --- hypothesis lab ---------------------------------------------------------------------------

def test_lab_leverage_is_complete_only_with_alternatives_and_asymmetry():
    assert lab.parse_generation({"hypotheses": [rich_row()]}, LAB_FACTS)[0]["leverage"]["complete"] is True
    row = rich_row()
    row["leverage"] = dict(row["leverage"], holder_dependence="")
    assert lab.parse_generation({"hypotheses": [row]}, LAB_FACTS)[0]["leverage"]["complete"] is False
    assert "LEVERAGE TEST" in lab.generation_prompt("q", LAB_FACTS, ["lens"], batch=1)
    assert "LEVERAGE TEST" in lab.evolution_prompt("q", [], LAB_FACTS)


def _statuses(item, **status):
    claims = lab.grounding_claims(item, ["E1", "E2"])
    out = []
    for c in claims:
        suffix = c["claim_id"].rsplit("-", 1)[-1]
        out.append({"claim_id": c["claim_id"], "grounding_status": status.get(suffix, "supported"),
                    "supporting_evidence_ids": ["E1"]})
    return claims, out


def test_lab_reports_the_leverage_test():
    item = lab.parse_generation({"hypotheses": [rich_row()]}, LAB_FACTS)[0]
    claims, grounded = _statuses(item)
    assert {c["claim_id"].rsplit("-", 1)[-1] for c in claims} >= {"CONTROL", "DEPENDENCY", "ALTERNATIVES", "ASYMMETRY"}
    final = lab.finalize(item, grounded, LAB_FACTS)
    assert final["leverage_test"] == "passed"
    assert final["field_checks"]["leverage_alternatives"] == "supported"
    _, grounded = _statuses(item, ALTERNATIVES="unsupported")
    assert lab.finalize(item, grounded, LAB_FACTS)["leverage_test"] == "not_established"
    bare = dict(item, leverage=dict(item["leverage"], holder_dependence=""))
    _, grounded = _statuses(bare)
    assert lab.finalize(bare, grounded, LAB_FACTS)["leverage_test"] == "incomplete"


def test_judge_calls_it_a_leverage_decision_only_when_the_test_passed():
    def first(test):
        item = dict(rich_row(), critic_total=9, status="verified", supporting_evidence_ids=["E1"], leverage_test=test)
        return judge.candidates({"strategic_hypotheses": {"verified": [item]}, "actor_moves": {"actors": []},
                                 "editorial_briefing": {"implications": []}, "hidden_insights": []})[0]
    assert first("passed")["strategic_type"] == "leverage_decision"
    assert first("not_established")["strategic_type"] != "leverage_decision"


# --- actor moves ------------------------------------------------------------------------------

def test_actor_moves_ask_for_and_check_the_asymmetry():
    assert "holder_dependence" in moves.build_prompt("ai", FACTS)
    contract = judge._leverage_contract({"actor": "OpenAI", "leverage": {
        "holder": "OpenAI", "counterparty": "Australian Government", "dependency": "logs", "control_point": "logs",
        "outside_option": "none named in the facts"}})
    assert contract["complete"] is False                                  # no holder_dependence: not leverage
    actor = {"actor": "OpenAI", "crux": {}, "choice": {}, "moves": [], "rules_and_incentives": [], "others": [],
             "must_be_true": [],
             "powers": [{"power": "cornered_resource", "holder": "OpenAI", "counterparty": "Australian Government",
                         "dependency": "logs", "control_point": "logs", "benefit": "audit leverage", "barrier": "",
                         "outside_option": "none named", "holder_dependence": "one customer of many",
                         "fact_ids": ["F1"]}]}
    chain = [c for c in moves.claims([actor], FACTS) if "holds this advantage" in c.get("claim_text", "")]
    assert chain and "needs the counterparty less than the reverse: one customer of many" in chain[0]["claim_text"]


# --- dashboard --------------------------------------------------------------------------------

@pytest.fixture
def dash(monkeypatch):
    shown: list[str] = []
    fake = types.ModuleType("streamlit")
    fake.__getattr__ = lambda name: (lambda *a, **k: shown.append(" ".join(str(x) for x in a)))
    fake.cache_resource = fake.cache_data = lambda *a, **k: (lambda function: function)
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    monkeypatch.delitem(sys.modules, "frontend.dashboard", raising=False)
    module = importlib.import_module("frontend.dashboard")
    module._shown = shown
    yield module
    sys.modules.pop("frontend.dashboard", None)


def test_dashboard_shows_the_leverage_test(dash):
    item = dict(rich_row(), leverage_test="not_established",
                field_checks={"leverage": "supported", "leverage_alternatives": "unsupported",
                              "leverage_asymmetry": "inference_supported"})
    text = "\n".join(dash.hypothesis_lines(item, verified=True))
    assert "A's alternatives:** C after qualification *(not backed by the cited sources - analysis only)*" in text
    assert "What B needs back:** B sells to many buyers besides A *(follows from the sources)*" in text
    assert "Leverage test:** not passed" in text
    dash._shown.clear()
    briefing_item = dict(_lever(), assumption_fields=["counterparty_alternatives"])
    dash.render_editorial_briefing({"editorial_briefing": {
        "headline": "H", "implications": [briefing_item],
        "policy_fit": {"leverage_rejected": ["x"], "position_rejected": ["y", "z"]}}})
    shown = "\n".join(dash._shown)
    assert "Australian Government's alternatives: none named in the facts (assumption)" in shown
    assert "what OpenAI needs back: OpenAI needs the contract" in shown
    assert "1 leverage implication(s) removed" in shown and "2 rule-based implication(s) removed" in shown
