"""v6.21.2: fixes from replay RUN-E8B8791954BF422AAD5837FD (first run on v6.21.1).

1. A reasoning step that cites its own records inline ("... [EVID-B168...]")
   was checked only against the insight-level list; the checker reported the
   inline records as "not supplied". Inline records are now sent with it.
2. An implication's stated fact (the rule, the holder, why it matters) is
   checked against all the briefing's quoted facts, not only those the
   implication cites. It must still be STATED by a quoted fact.
3. The models are told to prefer conclusions about what facts jointly show
   over unstated causes, and to ask for 2-4 implications including a simple one.
"""

from __future__ import annotations

import inspect

import pytest

import tests.test_v6_17_report_sections as base
from services import claim_support_gate as gate
from services import editorial_briefing as brief
from services.research_orchestrator_service import ResearchOrchestratorService as R

INLINE = "EVID-B1681BB2563B48BD84904ADE"


@pytest.fixture(autouse=True)
def _private_ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_WATCH_ITEMS", str(tmp_path / "watch_items.sqlite3"))
    monkeypatch.setenv("EVENT_INTELLIGENCE_EVENT_MEMORY", str(tmp_path / "event_memory.sqlite3"))


def test_inline_cited_records_reach_the_mechanism_check():
    obj = R.__new__(R)
    insight = {"insight_id": "I", "insight_revision_id": "IR", "insight": "Conclusion sentence.",
               "supporting_evidence_ids": ["EVID-A"],
               "mechanism": {"description": f"Detection took two months [{INLINE}]. Reporting then lagged.",
                             "evidence_ids": ["EVID-A"]}}
    steps = [c for c in obj._claims_from_insight(insight) if c["epistemic_type"] == "mechanism"]
    assert INLINE in steps[0]["evidence_ids"] and "EVID-A" in steps[0]["evidence_ids"]
    assert steps[1]["evidence_ids"] == ["EVID-A"]               # no leak to the next sentence


def test_inline_cited_records_reach_the_display_check():
    candidate = {"insight_revision_id": "IR", "insight": "x", "metadata": {"insight_derivation": {
        "mechanism": f"Detection took two months [{INLINE}]. Reporting then lagged.",
        "mechanism_support_evidence_ids": ["EVID-A"]}}}
    claims = gate.display_claims(candidate, ["EVID-A"])
    mech = [c for c in claims if c["_target"][:2] == ("derivation_text", "mechanism")]
    assert INLINE in mech[0]["evidence_ids"] and INLINE not in mech[1]["evidence_ids"]


def test_implication_fact_is_checked_against_every_quoted_fact():
    facts = brief.select_facts(base.DEVELOPMENTS)
    draft = brief.parse_draft({"headline": "", "paragraphs": [], "analogies": [], "implications": [
        {"basis": "incentive", "text": "Read-only agent access appears strategically attractive for Equals Money.",
         "fact_ids": ["F2"], "actor": "Equals Money", "policy_or_regulation": "Okta-style kill switches exist",
         "company_relevance": "it runs an MCP server", "economic_or_strategic_effect": "lower payment risk",
         "attractive_move": "read-only access", "conditions": "regulators allow agent payments"}]}, facts, [])
    claims = brief.grounding_claims(draft, facts, [])
    rule = next(c for c in claims if c["claim_text"].startswith(brief.POLICY_EXISTS_PREFIX))
    every = {f["evidence_id"] for f in facts}
    assert set(rule["evidence_ids"]) == every and rule["_bar"] == ["supported"]
    core = next(c for c in claims if c.get("_role") == "core")
    assert set(core["evidence_ids"]) == {facts[1]["evidence_id"]}    # the implication itself: its own facts


def test_prompts_steer_away_from_unstated_causes_and_ask_for_several_implications():
    from services import research_orchestrator_service as ros
    assert "WHY something happened" in ros.CLAIM_ALTERNATIVE_INSTRUCTION
    import agents.strategic_inference_agent as agent
    assert "asserts a cause no record states" in inspect.getsource(agent)
    prompt = brief.build_prompt("t", brief.select_facts(base.DEVELOPMENTS), [])
    assert "Write 4 to 6 implications" in prompt  # v6.21.5: over-generate, then novelty filter
    assert 'simple basis "trend" implication' in prompt
