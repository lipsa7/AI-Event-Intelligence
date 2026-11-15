"""v6.36.3 - GPT review of v6.36.2:
1. An unsupported 'who gains over whom' claim was recorded as an assumption (position_shift) but the
   dashboard never showed it or its label. It is now shown with its label, and any assumed step the
   dashboard has no line for is listed as an assumption rather than silently dropped.
2. A named loser was mandatory for every rule-based move, which invites invented rivalry. Now a
   rule-based move needs a specific actor, a specific counterparty, the mechanism and the consequence for
   the counterparty; a named loser is required only for a competitive-displacement claim.
"""

from __future__ import annotations

import importlib
import inspect
import sys
import types

import pytest

from services import editorial_briefing as brief
from tests.test_v6_36_2_fit_on_shown import PARSE_FACTS, _item


def test_a_mutual_benefit_move_needs_no_loser():
    item = _item("Offer agencies shared safeguards and audit access", gains="", loses="",
                 consequence="gains audit rights it lacked", displacement=False)
    out = brief.parse_draft({"implications": [item]}, PARSE_FACTS, [])
    assert len(out["implications"]) == 1
    kept = out["implications"][0]
    assert kept["counterparty"] == "Services Australia" and kept["who_loses"] == "" and not kept["competitive_displacement"]


def test_a_displacement_claim_must_name_the_loser():
    declared = _item("Bundle log access into renewals", loses="", displacement=True)
    worded = _item("Win contracts from rivals with log access", loses="")          # worded as displacement
    for bad in (declared, worded, _item("Win contracts from Anthropic", loses="OpenAI", displacement=True)):
        out = brief.parse_draft({"implications": [bad]}, PARSE_FACTS, [])
        assert out["implications"] == [] and out["position_rejected"], bad
    good = brief.parse_draft({"implications": [_item("Win contracts from Anthropic with log access")]}, PARSE_FACTS, [])
    assert good["implications"][0]["who_loses"] == "Anthropic" and good["implications"][0]["competitive_displacement"]


def test_counterparty_and_position_are_checked_as_steps():
    draft = brief.parse_draft({"implications": [_item("Win contracts from Anthropic with log access")]}, PARSE_FACTS, [])
    steps = {c.get("_field"): c for c in brief.grounding_claims(draft, PARSE_FACTS, []) if c.get("_role") == "step"}
    assert {"counterparty_consequence", "position_shift"} <= set(steps)
    assert "Services Australia" in steps["counterparty_consequence"]["claim_text"]
    assert "OpenAI gains position relative to Anthropic" in steps["position_shift"]["claim_text"]
    mutual = brief.parse_draft({"implications": [_item("Offer shared safeguards", loses="", displacement=False)]},
                               PARSE_FACTS, [])
    assert "position_shift" not in {c.get("_field") for c in brief.grounding_claims(mutual, PARSE_FACTS, [])}


@pytest.fixture
def dash(monkeypatch):
    """The real dashboard module with a recording stand-in for streamlit (not installed in tests)."""
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


def _render(dash, item):
    dash._shown.clear()
    dash.render_editorial_briefing({"editorial_briefing": {"headline": "H", "implications": [item]}})
    return "\n".join(dash._shown)


def test_dashboard_shows_every_assumed_step_with_its_label(dash):
    item = dict(_item("Win contracts from Anthropic with log access"), competitive_displacement=True,
                assumption_fields=["position_shift", "counterparty_consequence"])
    text = _render(dash, item)
    assert "OpenAI gains position over Anthropic (assumption)" in text
    assert "acts on Services Australia: gets audit access before renewing (assumption)" in text
    # A mutual-benefit move shows no loser line at all.
    mutual = _render(dash, dict(_item("Offer shared safeguards", loses="", displacement=False), who_loses=""))
    assert "gains position over" not in mutual and "acts on Services Australia" in mutual
    # An assumed step the dashboard has no line for is still listed as an assumption, never dropped.
    odd = _render(dash, dict(_item("Offer shared safeguards"), assumption_fields=["some_new_step"]))
    assert "not backed by the sources, shown as assumptions: some new step" in odd


def test_compliance_backstop_accepts_a_named_counterparty():
    assert not brief.is_compliance_only("Meet the reporting duties early so Services Australia can audit agents",
                                        "Services Australia", "")
    assert brief.is_compliance_only("Ensure compliance with reporting duties", "Services Australia", "")
