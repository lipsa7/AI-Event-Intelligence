"""Load the project's .env settings into the environment (v13.10, 20 Nov 2026).

Why: the only .env loader (agents/strategic_inference_agent.py) read the Gemini key and stopped,
so every other setting in .env - EVENT_INTELLIGENCE_MAX_GEMINI_CALLS, the run-time limit, the
reserves - was never seen by a run started from the dashboard. The 20 Nov run (RUN-FF8F...) logged
no "gemini_call_cap" line: it ran with no call ceiling although .env set 480.

What it does: reads <project>/.env once, and for every plain `NAME=value` line whose NAME is an
ordinary environment-variable name, sets it unless the process environment already has it (the
process environment always wins). Values are never logged. Comments, `export ` prefixes and
quotes are handled; anything else is skipped. Safe to call more than once. Not applied while
pytest runs, so the owner's .env never changes test results.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LOADED: list[str] = []


def load(path: Path | None = None) -> list[str]:
    """Names set from .env (never values)."""
    if _LOADED and path is None:
        return list(_LOADED)
    if path is None and "pytest" in __import__("sys").modules:
        # tests set what they need; the owner's real .env (cap, keys) must not change test results
        return []
    env_path = path or Path(__file__).resolve().parent.parent / ".env"
    try:
        text = env_path.read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return []
    names: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.casefold().startswith("export "):
            line = line[7:].strip()
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if not _NAME.match(name):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if name not in os.environ:
            os.environ[name] = value
            names.append(name)
    if path is None:
        _LOADED[:] = names or ["<none>"]
    return names
