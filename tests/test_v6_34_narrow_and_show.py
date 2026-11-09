"""v6.34 (run RUN-3F1C454275044725A7CFC4B7, 2026-10-11):
1. The hypothesis lab's output was saved but never shown on the dashboard.
2. Its best leverage idea ("only OpenAI's models can parse the forensic logs regulators need")
   was blocked by its wording ("physically impossible", "uniquely capable"), not its substance.
   Now a blocked or unsupported candidate is restated at the strength its facts support and
   checked again with the same grounding and blockers (ICD 203: state the judgment at the
   strength the evidence supports). Contradicted candidates are never rescued.
3. eval_insight_judge.py called insight_evaluator.agreement(), which v6.32 removed.
4. Implications cite evidence IDs only; the judge now sees what those records state.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import sys
import types

import services.research_orchestrator_service as ros
from services import insight_evaluator as ev
from services import strategic_hypothesis_lab as lab

FACTS = [
    {"fact_id": "F1", "text": "OpenAI said reviewing the agent's activity logs costs $500,000 a day.",
     "evidence_id": "EVID-1", "source_id": "S1"},
    {"fact_id": "F2", "text": "Australia relied on OpenAI's own report to learn what its agent accessed.",
     "evidence_id": "EVID-2", "source_id": "S2"},
    {"fact_id": "F3", "text": "OpenAI notified Australia three months after the access.",
     "evidence_id": "EVID-3", "source_id": "S3"},
]
BLOCKED = {
    "id": "EVOLVED_H2", "status": "promising_unverified", "critic_total": 10,
    "thesis": "Independent auditing of agent breaches is physically impossible, so only OpenAI is uniquely capable of telling "
              "governments what happened.",
    "mechanism": "Log volume -> only the provider can parse it -> governments depend on the provider's account.",
    "fact_ids": ["F1", "F2", "F3"], "evidence_ids": ["EVID-1", "EVID-2"],
    "leverage": {"holder": "OpenAI", "counterparty": "Australian Government", "dependency": "an account of what the agent did",
                 "control_point": "the agent activity logs", "outside_option": "none identified", "complete": True},
    "likely_actor_move": "Regulators demand direct access to the logs.",
    "grounding": {"thesis": "inference_supported", "mechanism": "inference_supported"},
    "promotion_blockers": ["absolute_or_exclusive_claim_requires_direct_support"],
}
NARROWED = {"thesis": "Australia depended on OpenAI's own report to learn what OpenAI's agent accessed, and that review "
                      "is costly, so the account governments rely on comes from the party under scrutiny.",
            "mechanism": "Costly log review held by OpenAI -> Australia relies on OpenAI's report -> pressure for direct log access",
            "likely_actor_move": "Australian agencies ask for direct access to agent logs in AI contracts.",
            "leverage": {"holder": "OpenAI", "counterparty": "Australian Government",
                         "dependency": "an account of what the agent accessed", "control_point": "the agent activity logs",
                         "outside_option": "its own forensic review of its systems",
                         "holder_dependence": "OpenAI needs the government contract, one of many"},
            "fact_ids": ["F1", "F2", "F9"], "what_was_narrowed": "removed 'physically impossible' and 'uniquely capable'"}


def test_blocked_or_unsupported_candidates_are_narrowed_contradicted_ones_are_not():
    assert lab.needs_narrowing(BLOCKED)
    assert lab.needs_narrowing(dict(BLOCKED, promotion_blockers=[], grounding={"thesis": "unsupported",
                                                                               "mechanism": "inference_supported"}))
    assert not lab.needs_narrowing(dict(BLOCKED, grounding={"thesis": "contradicted", "mechanism": "supported"}))
    assert not lab.needs_narrowing(dict(BLOCKED, status="verified"))
    assert not lab.needs_narrowing(dict(BLOCKED, narrowed_from={"thesis": "x"}))
    prompt = lab.narrow_prompt(BLOCKED, FACTS)
    assert "physically impossible" in prompt and "uniquely capable" in prompt
    assert "[F2] Australia relied on OpenAI" in prompt and "holder=OpenAI" in prompt


def test_narrowed_version_keeps_the_leverage_and_drops_unknown_facts():
    out = lab.parse_narrow(json.dumps(NARROWED), BLOCKED, FACTS)
    assert out["id"] == "EVOLVED_H2-N" and out["fact_ids"] == ["F1", "F2"]          # F9 was never cited
    assert out["leverage"]["complete"] is True and "grounding" not in out and "promotion_blockers" not in out
    assert out["narrowed_from"]["thesis"].startswith("Independent auditing")
    still = dict(NARROWED, thesis=NARROWED["thesis"] + " It is physically impossible to audit otherwise.")
    assert lab.parse_narrow(still, BLOCKED, FACTS) is None
    assert lab.parse_narrow({"drop": True}, BLOCKED, FACTS) is None
    assert lab.parse_narrow(dict(NARROWED, fact_ids=["F9"]), BLOCKED, FACTS) is None


def _orchestrator(narrow_reply):
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    obj._remaining_discretionary_seconds = lambda: float("inf")
    row = {k: BLOCKED[k] for k in ("id", "thesis", "mechanism", "fact_ids", "leverage", "likely_actor_move")}
    row.update({"novel_proposition": "The account governments rely on comes from the party under scrutiny.",
                "why_not_obvious": "Breach coverage treats the delay as the story, not who holds the logs.",
                "alternative_explanation": "The delay was ordinary investigation time.",
                "falsifier": "A government completes its own independent review.", "watch_signal": "Log-access clauses.",
                "critic_scores": {k: 2 for k in ("mental_model_change", "mechanism_depth", "surprise_nonobviousness",
                                                 "strategic_consequence", "falsifiability")}})
    obj._ask_strong_model = lambda prompt: (json.dumps({"hypotheses": [row]}), "pro")
    asked = []

    def briefing(prompt):
        asked.append(prompt)
        return json.dumps(narrow_reply)

    obj._ask_briefing_model = briefing

    class Grounding:
        def ground_claims(self, *, claims, evidence_records):
            return {"claims": [{"claim_id": c["claim_id"], "claim_text": c["claim_text"],
                                "grounding_status": "inference_supported", "supporting_evidence_ids": c["evidence_ids"]}
                               for c in claims]}

    obj.grounding_service = Grounding()
    return obj, asked


def _run(obj):
    os.environ["EVENT_INTELLIGENCE_HYPOTHESIS_LAB_RESEARCH"] = "0"
    try:
        developments = [{"title": "OpenAI agent breach", "facts": [
            {"text": f["text"], "evidence_id": f["evidence_id"], "source_id": f["source_id"], "date": "2026-09-24"}
            for f in FACTS]}]
        evidence = [{"evidence_id": f["evidence_id"], "source_id": f["source_id"], "text": f["text"]} for f in FACTS]
        return obj._strategic_hypothesis_lab(topic="ai", developments=developments, evidence=evidence,
                                             state={"research_depth": "deep"})
    finally:
        os.environ.pop("EVENT_INTELLIGENCE_HYPOTHESIS_LAB_RESEARCH", None)


def test_lab_narrows_a_blocked_idea_and_verifies_the_backed_core():
    obj, asked = _orchestrator(NARROWED)
    out = _run(obj)
    assert out["narrowing"]["tried"] == 1 and out["narrowing"]["verified"] == 1
    assert [v["id"] for v in out["verified"]] == ["EVOLVED_H2-N"]
    assert out["verified"][0]["narrowed_from"]["original_blockers"] == ["absolute_or_exclusive_claim_requires_direct_support"]
    original = out["promising_unverified"][0]                         # the original stays on record
    assert original["id"] == "EVOLVED_H2" and original["narrowing"] == "narrowed_version_verified:EVOLVED_H2-N"
    assert asked and asked[0].startswith("NARROW THIS STRATEGIC HYPOTHESIS")


def test_lab_keeps_the_original_when_nothing_strategic_survives():
    obj, _ = _orchestrator({"drop": True})
    out = _run(obj)
    assert out["verified"] == [] and out["narrowing"]["dropped"] == 1
    assert out["promising_unverified"][0]["narrowing"] == "nothing_strategic_survived_or_still_overstated"


def _dashboard():
    calls = []

    class Ctx(contextlib.nullcontext):
        def __call__(self, fn=None, *a, **k):
            return fn

    class St(types.ModuleType):
        def __getattr__(self, name):
            return lambda *a, **k: calls.append((name, a[0] if a else k)) or Ctx()

    saved = {name: sys.modules.get(name) for name in ("streamlit", "frontend.dashboard")}
    sys.modules["streamlit"] = St("streamlit")
    sys.modules.pop("frontend.dashboard", None)
    try:
        return importlib.import_module("frontend.dashboard"), calls, saved
    except Exception:
        for name, mod in saved.items():
            sys.modules.pop(name, None) if mod is None else sys.modules.__setitem__(name, mod)
        raise


def test_dashboard_shows_verified_and_not_yet_backed_hypotheses():
    dashboard, calls, saved = _dashboard()
    try:
        narrowed = dict(lab.parse_narrow(NARROWED, BLOCKED, FACTS), status="verified",
                        supporting_evidence=["Australia relied on OpenAI's own report."])
        sections = {"strategic_hypotheses": {"raw_count": 20, "evolved_count": 7, "verified": [narrowed],
                                             "promising_unverified": [BLOCKED]}}
        dashboard.render_strategic_hypotheses(sections)
        text = "\n".join(str(arg) for _, arg in calls)
        assert "Strategic hypotheses" in text and "**Verified** ·" in text and "**Not yet backed** ·" in text
        assert "**Leverage:** OpenAI → Australian Government; controls: the agent activity logs" in text
        assert "Stated more narrowly than the first version" in text
        assert "absolute claim" in text                                   # blocker in plain words
        assert "“Australia relied on OpenAI's own report.”" in text
    finally:
        for name, mod in saved.items():
            sys.modules.pop(name, None) if mod is None else sys.modules.__setitem__(name, mod)


def test_judge_sees_implication_evidence_and_agreement_is_restored():
    report = {"editorial_briefing": {"implications": [
        {"text": "Agencies will add vendor-notice clauses to AI contracts at renewal.",
         "citations": [{"evidence_id": "EVID-3"}]}]}}
    item = ev.candidates(report, {"EVID-3": "OpenAI notified Australia three months after the access."})[0]
    assert item["evidence"] == ["OpenAI notified Australia three months after the access."]
    assert "three months after the access" in ev.rubric_prompt("ai", [item])
    gold = {"question": "ai",
            "human_pairs": [{"id": "H1", "a": "x" * 30, "b": "y" * 30, "human_winner": "A"}],
            "reference_pairs": [{"id": "R1", "a": "x" * 30, "b": "y" * 30, "human_winner": "B"}]}
    result = ev.agreement(gold, lambda prompt: {"winner": "A" if prompt.index("x" * 30) < prompt.index("y" * 30) else "B"})
    assert result["agreement_with_you"]["pairwise_accuracy"] == 1.0 and result["reference_sanity"]["pairwise_accuracy"] == 0.0
    assert result["human_pairs"] == 1 and result["reference_pairs"] == 1
