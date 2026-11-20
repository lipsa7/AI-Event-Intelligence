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
