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