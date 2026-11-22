"""
Shared pytest configuration.

The project uses normal package imports:

    from services...
    from models...
    from agents...
    from utils...

No service modules are imported or aliased here.

Tests should import the actual package contract they exercise.
"""

from __future__ import annotations

import sys

from pathlib import Path


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)


if (
    str(PROJECT_ROOT)
    not in sys.path
):
    sys.path.insert(
        0,
        str(PROJECT_ROOT),
    )

import pytest


@pytest.fixture(autouse=True)
def _no_paid_exa_calls_in_tests(monkeypatch, tmp_path):
    """v13.4: tests never call Exa (paid) or touch the real monthly counter, even with EXA_API_KEY in
    .env. Tests of the Exa code turn it back on with a fake transport."""
    monkeypatch.setenv("EXA_FALLBACK", "0")
    try:
        from services import exa_contents
        monkeypatch.setattr(exa_contents, "USAGE_FILE", tmp_path / "exa_usage.json")
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _no_earlier_reports_in_tests(monkeypatch):
    """v13.12: the owner's saved reports never change test results; tests of the earlier-insight
    check supply their own."""
    try:
        from services import earlier_insights
        monkeypatch.setattr(earlier_insights, "load", lambda topic="", data_dir=None: [])
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _no_alternate_google_search_in_tests():
    """v13.17: no test may reach the run's Google search helper left registered by another test."""
    yield
    try:
        from services import source_fetch_service
        source_fetch_service.set_google_searcher(None)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _no_editor_bar_judge_in_tests(monkeypatch):
    """v13.22: the editor's-bar judge (services/par_check.py) needs a model; tests of the release loop
    that predate it run without it. tests/test_v13_22_par_check.py turns it on with a fake judge."""
    monkeypatch.setenv("EVENT_INTELLIGENCE_PAR_CHECK", "0")


@pytest.fixture(autouse=True)
def _no_variant_view_in_tests(monkeypatch):
    """v13.23: the consensus call and tournament need a model; older exploration tests run without them.
    tests/test_v13_23_variant_view.py turns them on with fake answers."""
    monkeypatch.setenv("EVENT_INTELLIGENCE_VARIANT_VIEW", "0")
    # v13.29: the variant-perception, diagnosticity and load-bearing-number checks need fields and judges the
    # older fixtures do not have; tests/test_v13_29_variant_perception.py turns them on.
    monkeypatch.setenv("EVENT_INTELLIGENCE_VARIANT_PERCEPTION", "0")


@pytest.fixture(autouse=True)
def _no_outside_judge_in_tests(monkeypatch):
    """v13.24: never call a local Ollama from tests; tests that need the outside judge use fakes."""
    from services import outside_judge
    monkeypatch.setenv("OUTSIDE_JUDGE_PROVIDER", "ollama")   # v13.29: never call Groq from tests
    outside_judge.reset()
    real = outside_judge._installed
    monkeypatch.setattr(outside_judge, "_installed", lambda get=None: real(get) if get is not None else
                        list(outside_judge._CACHE.get("models") or []))
    yield
    outside_judge.reset()
