"""Align the free-model insight judge with your own ratings (v11, 15 Nov 2026).

1. Rate: open data/insight_review_90.csv and put "pass" or "fail" in the your_rating column
   (and, if you like, one line in your_note). Your six earlier verdicts are built in.
   python judge_tools.py suggest  lists the unrated insights most worth rating next.
   At least 20 pass-or-fail rows are needed (10 to tune on, 10 to check on); 40 is better.
2. Run:  python judge_tools.py status    how many rows are rated, and whether the judge is aligned
         python judge_tools.py measure   the judge's agreement with you on the held-out half
         python judge_tools.py tune      automatic tuning of the judge's instructions, then measure

The judge uses a free open model: GROQ_API_KEY (Groq free tier, default model openai/gpt-oss-120b)
or INSIGHT_JUDGE_PROVIDER=ollama with OLLAMA_JUDGE_MODEL (local, default qwen2.5:14b).
Runs use the judge to rank lab ideas and released insights only once it is aligned:
held-out balanced accuracy at least 80% and Cohen's kappa at least 0.6 on at least 10 rows,
with at least 3 rated pass and 3 rated fail among them.
"""
from __future__ import annotations

import argparse
import json
import sys

from services import insight_judge as judge


def _print(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["status", "suggest", "measure", "tune"])
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--proposals", type=int, default=3)
    args = parser.parse_args(argv)

    rows = judge.load_ratings()
    tuning, held_out = judge.split(rows)
    state = judge.load_state()
    if args.command == "status":
        _print({"ratings_file": str(judge.RATINGS_PATH), "rated": len(rows),
                "rated_pass": sum(r["label"] for r in rows), "rated_fail": sum(not r["label"] for r in rows),
                "needed": 2 * judge.MIN_HELD_OUT, "judge_provider": judge.provider() or "none configured",
                "judge_model": judge.model_name(), "aligned": judge.is_aligned(state),
                "last_measure": {k: state.get(k) for k in ("model", "held_out_n", "held_out_accuracy",
                                                            "held_out_kappa", "measured_at")}})
        return 0
    if args.command == "suggest":
        _print(judge.suggest_to_rate())
        return 0
    if len(held_out) < judge.MIN_HELD_OUT:
        print(f"Only {len(rows)} rows are rated pass or fail; rate at least {2 * judge.MIN_HELD_OUT} "
              f"in {judge.RATINGS_PATH} (column your_rating).")
        return 1
    try:
        ask = judge.default_asker()
    except judge.JudgeUnavailable as error:
        print(f"Judge model not available: {error}")
        return 1
    if args.command == "measure":
        result = judge.agreement(held_out, ask)
        saved = judge.save_state(judge.current_instructions(), result, tuning_n=len(tuning))
        _print({"held_out": {k: result[k] for k in ("n", "accuracy", "balanced_accuracy", "kappa", "rated_pass",
                                                     "rated_fail")}, "aligned": saved["aligned"],
                "disagreements": [{"no": e["id"], "you": "pass" if e["label"] else "fail",
                                   "judge_reason": e["judge_reason"]} for e in result["errors"]]})
        return 0
    result = judge.tune(rows, ask, rounds=args.rounds, proposals=args.proposals)
    _print({**result, "aligned": judge.is_aligned()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
