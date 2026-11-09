"""Plan fix E: near misses shown as "Hypotheses to investigate" - never as insights."""
from __future__ import annotations

import contextlib
import importlib
import sys
import types

from services import hypothesis_tier as tier
from services import insight_funnel

QUOTE = {"evidence_id": "EVID-DELAY", "publisher": "slashdot.org", "url": "https://slashdot.org/x",
         "text": "Albanese complained it took three months for OpenAI to alert Australia's government."}


def hypothesis(hid, thesis_status="inference_supported", quotes=(QUOTE,), **extra):
    item = {"id": hid, "thesis": f"Hypothesis {hid}: the provider, not the victim, controls breach discovery.",
            "mechanism": "Agent accesses site -> provider logs show it -> victim learns months later",
            "grounding": {"thesis": thesis_status, "mechanism": "partially_supported"},
            "falsifier": "Australia detected the access itself.", "watch_signal": "Taskforce findings.",
            "supporting_evidence": list(quotes),
            "grounded_claims": [
                {"claim_id": f"LAB-{hid}-THESIS", "claim_text": "t", "grounding_status": thesis_status,
                 "unsupported_parts": []},
                {"claim_id": f"LAB-{hid}-MECH", "claim_text": "m", "grounding_status": "partially_supported",
                 "unsupported_parts": ["Traditional breaches are identified by victims",
                                       "The claim requires multiple supporting evidence records, but fewer"]},
                {"claim_id": f"LAB-{hid}-LEVERAGE", "claim_text": "OpenAI controls the agent logs.",
                 "grounding_status": "unsupported"},
                {"claim_id": f"LAB-{hid}-MOVE", "claim_text": "Leaders complain to CEOs.",
                 "grounding_status": "supported"}]}
    item.update(extra)
    return item


def sections(*items, verified=()):
    return {"strategic_hypotheses": {"verified": list(verified), "promising_unverified": list(items)}}


def test_qualifying_conditions():
    assert tier.qualifies(hypothesis("A"))
    assert tier.qualifies(hypothesis("B", thesis_status="partially_supported"))
    assert not tier.qualifies(hypothesis("C", thesis_status="unsupported"))      # e.g. EV4
    assert not tier.qualifies(hypothesis("D", quotes=()))                        # no sourced premise
    contradicted = hypothesis("E")
    contradicted["grounded_claims"][2]["grounding_status"] = "contradicted"
    assert not tier.qualifies(contradicted)                                      # never shown as a premise
    contradicted_ids = hypothesis("F")
    contradicted_ids["grounded_claims"][3]["contradicted_evidence_ids"] = ["EVID-X"]
    assert not tier.qualifies(contradicted_ids)


def test_premises_open_points_and_assumptions_are_kept_apart():
    item = hypothesis("A")
    assert [q["evidence_id"] for q in tier.premises(item)] == ["EVID-DELAY"]
    assert tier.open_points(item) == ["Leverage: OpenAI controls the agent logs."]    # supported MOVE not listed
    assert tier.assumptions(item) == ["Traditional breaches are identified by victims"]
    unknown = hypothesis("U", unresolved_fact_ids={"actor_move_basis": ["F99"]})
    assert "cites unknown fact F99 (not checked)" in tier.open_points(unknown)[-1]
    assert tier.coverage(item) == "1 source quote from 1 publisher"
    twice = hypothesis("T", quotes=(QUOTE, dict(QUOTE, evidence_id="EVID-COPY")))   # same passage, two ids
    assert len(tier.premises(twice)) == 1


def test_order_uses_your_ratings_then_quote_count_never_critic_score():
    two = dict(QUOTE, evidence_id="EVID-2", publisher="indianexpress.com",
               text="Australia set up a taskforce to examine how AI-related breaches are identified and reported.")
    items = [hypothesis("A", critic_total=10), hypothesis("B", quotes=(QUOTE, two), critic_total=7),
             hypothesis("C", critic_total=10), hypothesis("D", critic_total=10)]
    assert [h["id"] for h in tier.select(sections(*items))] == ["B", "A", "C"]
    rated = tier.select(sections(*items), {"lab:D": "useful", "lab:B": "poor"})
    assert [h["id"] for h in rated] == ["D", "A", "C"] and rated[0]["_rating"] == "useful"


def test_funnel_reports_selected_items_as_shown():
    data = sections(hypothesis("A"), hypothesis("C", thesis_status="unsupported"), verified=[{"id": "V", "thesis": "v"}])
    display = {r["id"]: r["outcome"] for r in insight_funnel.build(data)["stages"]["hypothesis_lab"]["display"]}
    assert display == {"A": "shown", "C": "collapsed", "V": "shown"}


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
    return importlib.import_module("frontend.dashboard"), calls, saved


def test_dashboard_section_is_labelled_and_not_called_an_insight():
    dashboard, calls, saved = _dashboard()
    try:
        dashboard.render_hypotheses_to_investigate(tier.select(sections(hypothesis("A"))))
        text = "\n".join(str(arg) for _, arg in calls)
        assert "Hypotheses to investigate" in text and "Not insights." in text
        assert "**What the sources establish** (1 source quote from 1 publisher):" in text
        assert "[slashdot.org](https://slashdot.org/x)" in text
        assert "**Assumed, not stated by any source:** Traditional breaches are identified by victims" in text
        assert "**Not established by the sources:**" in text and "**Would be wrong if:**" in text
        calls.clear()
        dashboard.render_hypotheses_to_investigate([])
        assert not calls
        calls.clear()   # an item shown above is not repeated in the collapsed list
        dashboard.render_strategic_hypotheses(sections(hypothesis("A"), hypothesis("B")), exclude_ids=["A"])
        text = "\n".join(str(arg) for _, arg in calls)
        assert "Hypothesis B:" in text and "Hypothesis A:" not in text
    finally:
        for name, mod in saved.items():
            sys.modules.pop(name, None) if mod is None else sys.modules.__setitem__(name, mod)
