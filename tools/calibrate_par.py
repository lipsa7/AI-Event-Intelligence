"""Check the editor's-bar judge (services/par_check.py) against YOUR ratings before trusting it (v13.22).

    python tools/calibrate_par.py              # 40 held-out rows, one Gemini call each (Flash)
    python tools/calibrate_par.py --limit 82   # every held-out row

Reads data/insight_review_90.csv (your my_rating column: good = should PASS; explanatory/speculative
and generic restatement = should FAIL). The rows used as examples inside the judge's prompt are never
scored. Prints, separately (Hamel Husain: one agreement number hides a judge that passes everything):
  * true-positive rate  - of your "good" rows, how many it passes
  * true-negative rate  - of your other rows, how many it fails
and every disagreement, so you can see whether the judge or the label is wrong. Hard ceiling on
Gemini calls: --limit (+0 retries counted against it).
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=40, help="held-out rows to score (one call each)")
    parser.add_argument("--labels", default=None, help="CSV path (default data/insight_review_90.csv)")
    args = parser.parse_args()
    from services import gemini_llm_service as g
    from services import par_check
    from services.research_orchestrator_service import ResearchOrchestratorService

    rows = par_check.load_labels(args.labels)
    if not rows:
        print("No labelled rows found.")
        return 1
    examples, held_out = par_check.split(rows)
    good = [r for r in held_out if par_check.is_pass_label(r["my_rating"])]
    other = [r for r in held_out if not par_check.is_pass_label(r["my_rating"])]
    rng = random.Random(22)
    share = len(good) / max(1, len(held_out))
    n_good = min(len(good), max(1, round(args.limit * share)))
    sample = rng.sample(good, n_good) + rng.sample(other, min(len(other), args.limit - n_good))
    orchestrator = ResearchOrchestratorService()
    from services import outside_judge
    if not outside_judge.available():   # v13.25c: never Gemini judging Gemini
        print("No outside open-source judge: start Ollama with a non-Google model (ollama list), then rerun.")
        return 1
    judge, judged_by = outside_judge.ask, "outside:" + outside_judge.model()
    print(f"Judge: {judged_by}")
    g.set_call_budget(args.limit)
    tp = fn = tn = fp = unavailable = 0
    disagreements = []
    try:
        for row in sample:
            result = par_check.judge(row["insight"], ask=judge, examples=examples)
            if not result.get("available"):
                unavailable += 1
                continue
            should_pass = par_check.is_pass_label(row["my_rating"])
            if should_pass and result["passed"]:
                tp += 1
            elif should_pass:
                fn += 1
                disagreements.append((row, result))
            elif not result["passed"]:
                tn += 1
            else:
                fp += 1
                disagreements.append((row, result))
    finally:
        used = g.call_budget()["used"]
        g.set_call_budget(None)
    print(f"Scored {tp + fn + tn + fp} rows ({unavailable} unanswered), {used} Gemini calls.")
    print(f"True-positive rate (your 'good' it passes): {tp}/{tp + fn}")
    print(f"True-negative rate (your others it fails):  {tn}/{tn + fp}")
    for row, result in disagreements:
        print(f"\n#{row.get('no')} you: {row['my_rating']} | judge: {'pass' if result['passed'] else 'fail'} "
              f"({result.get('missing')}) - {result.get('fix')}\n   {row['insight'][:300]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
