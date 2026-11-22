"""v13.25 (22 Nov 2026): every candidate a run writes stays visible as a draft with what it needs.

User's rule: the run must produce insights that can be made better. RUN-ACC0E1ED's 7 candidates were
folded away under a source-backed fallback, and from v13.23 the exploration stages drop variants before
the claim check, which left no trace in the report."""
import sys
import types

import pytest

_STUBS = ("streamlit", "streamlit.components", "streamlit.components.v1")


class _Anything(types.ModuleType):
    def __getattr__(self, name):
        return lambda *a, **k: _Anything(name)

    def __call__(self, *a, **k):
        return _Anything("call")


@pytest.fixture(scope="module")
def d():
    """The dashboard module; a stand-in for streamlit only while these tests run, removed afterwards so
    other tests see the machine as it is."""
    added = []
    try:
        import streamlit  # noqa: F401
    except ModuleNotFoundError:
        for name in _STUBS:
            sys.modules[name] = _Anything(name)
            added.append(name)
    had_dashboard = "frontend.dashboard" in sys.modules
    import importlib
    module = importlib.import_module("frontend.dashboard")
    yield module
    for name in added:
        sys.modules.pop(name, None)
    if added and not had_dashboard:
        sys.modules.pop("frontend.dashboard", None)


def _gate():
    return {"attempts": [
        {"origin": "exploration", "insight": "California's suit targets testing failures.", "passed": False,
         "reasons": ["source_backed_not_original:conclusion_restates_sources"], "released_as": "source_backed"},
        {"origin": "exploration", "insight": "OpenAI will settle before the 2027 trial.", "passed": False,
         "reasons": ["forecast_already_answered_by_evidence"]}],
        "exploration": {"combinations": [{"variant_dropped": [
            {"insight": "Washington will reverse the foreign-access ban within a year.",
             "problems": ["call_horizon_340_days", "jargon:structural asymmetry"]},
            {"insight": "x" * 50, "problems": ["premise_2_quote_not_found", "no_number"]}]}]}}


def test_dropped_variants_are_listed_with_plain_needs(d):
    drafts = d.draft_variants(_gate())
    assert [x["insight"][:10] for x in drafts] == ["Washington", "x" * 10]
    assert drafts[0]["needs"] == ["a call due within 2-12 weeks (its call is 340 days out)",
                                  "plain words instead of labels (structural asymmetry)"]
    assert drafts[1]["needs"] == ["fact 2 quoted word for word from a source", "a number from the sources"]


def test_full_draft_text_is_kept_in_the_run_log():
    import inspect
    from services import research_orchestrator_service as ros
    src = inspect.getsource(ros.ResearchOrchestratorService._variant_select)
    assert '[:900]' in src and '[:160]' not in src


def test_drafts_open_when_only_a_source_backed_finding_was_released(d, monkeypatch):
    shown = {}

    class Box:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def expander(title, expanded=False):
        shown["title"], shown["expanded"] = title, expanded
        return Box()
    lines = []
    monkeypatch.setattr(d.st, "expander", expander)
    monkeypatch.setattr(d.st, "markdown", lambda text, **k: lines.append(text))
    monkeypatch.setattr(d.st, "caption", lambda text, **k: lines.append(text))
    report = {"analysis_metadata": {"claim_support_gate": _gate()}}
    d.render_claim_support_check(report)
    assert shown["expanded"] is True and shown["title"].startswith("Draft insights")
    assert any("Washington will reverse" in line for line in lines)
    assert any("OpenAI will settle" in line for line in lines)
