"""Check the local open-source judge before a run (v13.28.3). No Gemini call.

    python tools/check_judge.py

Prints the models Ollama has, the one chosen as judge, and the result of one small judgment with its time
and, on failure, the HTTP status and Ollama's own message (on 22 Nov every judgment failed and the log
only said RuntimeError).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    from services import outside_judge
    outside_judge.reset()
    print("Provider:", outside_judge.provider(), "(GROQ_API_KEY set -> Groq; OUTSIDE_JUDGE_PROVIDER overrides)")
    if outside_judge.provider() == "ollama":
        installed = outside_judge._installed()
        print("Ollama chat models (largest first):", ", ".join(installed) or "none - is Ollama running?")
    model = outside_judge.model()
    print("Judge:", model or "none")
    if not model:
        return 1
    started = time.monotonic()
    try:
        reply = outside_judge.ask('Return JSON {"ok": true}.')
    except Exception as error:
        print(f"FAILED after {time.monotonic() - started:.0f} s: {error}")
        print("If it says HTTP 500 / out of memory, try a smaller model: set OUTSIDE_JUDGE_MODEL=qwen3:8b in .env.")
        return 1
    print(f"OK in {time.monotonic() - started:.0f} s: {reply[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
