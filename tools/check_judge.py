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
    print("GROQ_API_KEY set -> Groq; OUTSIDE_JUDGE_PROVIDER / OUTSIDE_JUDGE_SIMPLE_PROVIDER override.")
    failed = 0
    for kind, label in (("hard", "hard judgments (editor's bar, novelty, ranking)"),
                        ("simple", "yes/no checks (pre-mortem, number, past cases, repeats)")):
        which, model = outside_judge.provider(kind), outside_judge.model(kind=kind)
        print(f"\n{label}: {which} {model or 'none'}")
        if which == "ollama":
            installed = outside_judge._installed()
            print("  Ollama chat models (largest first):", ", ".join(installed) or "none - is Ollama running?")
        if not model:
            failed += 1
            continue
        started = time.monotonic()
        try:
            reply = outside_judge.ask('Return JSON {"ok": true}.', kind=kind)
        except Exception as error:
            failed += 1
            print(f"  FAILED after {time.monotonic() - started:.0f} s: {error}")
            if "allocate" in str(error) or "memory" in str(error):
                print("  The model does not fit in free RAM. Use Groq for this kind (remove OUTSIDE_JUDGE_SIMPLE_PROVIDER).")
            continue
        print(f"  OK in {time.monotonic() - started:.0f} s: {reply[:120]}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
