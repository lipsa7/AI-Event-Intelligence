"""Check the editor's-bar judge against YOUR ratings and fit its calibration layer (v13.22; v13.28).

    python tools/calibrate_par.py                       # every labelled row, nearest examples, fits + saves
    python tools/calibrate_par.py --limit 30            # a quick look (does not save)
    python tools/calibrate_par.py --exemplars fixed     # A/B: the old fixed examples (does not save)

Reads data/insight_review_90.csv (my_rating: good = should pass; explanatory/speculative and generic
restatement = should not). Uses ONLY the local open-source judge in Ollama (never Gemini judging Gemini).
Each row is judged with the examples most similar to it, never itself. Then (research report of 22 Nov,
Method 8) a logistic regression maps the judge's six item scores to your ratings, with 5-fold
cross-validation, and picks the threshold where cross-validated precision on "good" reaches 0.7. The judge
gates releases ONLY if that precision is reached with the installed model (data/par_calibration.json);
otherwise its verdict is shown as advisory. Each row is one local model call (no API cost; on a laptop
CPU a 14B model can take 30-90 seconds a row).
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=0, help="rows to score (0 = all; a partial run is not saved)")
    parser.add_argument("--labels", default=None, help="CSV path (default data/insight_review_90.csv)")
    parser.add_argument("--exemplars", choices=("nearest", "fixed"), default="nearest")
    args = parser.parse_args()
    from services import outside_judge, par_calibration, par_check

    rows = par_check.load_labels(args.labels)
    if not rows:
        print("No labelled rows found.")
        return 1
    if not outside_judge.available():   # v13.25c: never Gemini judging Gemini
        print("No outside open-source judge: start Ollama with a non-Google model (ollama list), then rerun.")
        return 1
    model = outside_judge.model()
    print(f"Judge: {model} | examples: {args.exemplars}")
    fixed, held = par_check.split(rows)
    sample = held if args.exemplars == "fixed" else list(rows)
    if args.limit:
        sample = random.Random(22).sample(sample, min(args.limit, len(sample)))
    tp = fn = tn = fp = unavailable = 0
    x, y, disagreements = [], [], []
    started = time.monotonic()
    for n, row in enumerate(sample, 1):
        pool = [r for r in rows if r is not row]
        examples = fixed if args.exemplars == "fixed" else par_check.nearest_examples(row["insight"], pool)
        result = par_check.judge(row["insight"], ask=outside_judge.ask, examples=examples)
        if not result.get("available"):
            unavailable += 1
            continue
        good = par_check.is_pass_label(row["my_rating"])
        tp, fn, tn, fp = (tp + (good and result["passed"]), fn + (good and not result["passed"]),
                          tn + (not good and not result["passed"]), fp + (not good and result["passed"]))
        if good != result["passed"]:
            disagreements.append((row, result))
        feats = par_calibration.features(result.get("scores") or {}, result["passed"], par_check.ITEMS)
        if feats is not None:
            x.append(feats)
            y.append(1 if good else 0)
        if n % 10 == 0:
            print(f"  {n}/{len(sample)} rows, {time.monotonic() - started:.0f} s")
    print(f"\nScored {tp + fn + tn + fp} rows ({unavailable} unanswered; {len(x)} with all six item scores).")
    print(f"Judge's own verdict - passes your 'good': {tp}/{tp + fn}; fails your others: {tn}/{tn + fp}")
    for row, result in disagreements[:15]:
        print(f"  #{row.get('no')} you: {row['my_rating']} | judge: {'pass' if result['passed'] else 'fail'} "
              f"({result.get('missing')}) {row['insight'][:160]}")
    if len(set(y)) < 2 or len(x) < 20:
        print("\nToo few scored rows of both kinds to fit the calibration layer.")
        return 1
    calibration = par_calibration.build(x, y, model=model, items=par_check.ITEMS, exemplar_mode=args.exemplars)
    print(f"\nCalibration (5-fold cross-validated): precision {calibration['cv_precision']}, recall "
          f"{calibration['cv_recall']} at threshold {calibration['threshold']} "
          f"({calibration['cv_predicted_positive']} predicted good of {len(y)}; {sum(y)} rated good)")
    trusted = par_calibration.trusted(calibration, model)
    print("TRUSTED as a gate (precision >= 0.7)." if trusted else
          "NOT trusted as a gate - runs will show its verdict as advisory. Try a stronger model (qwen3:14b).")
    if args.limit or args.exemplars != "nearest":
        print("(Partial or A/B run: not saved.)")
        return 0
    print(f"Saved: {par_calibration.save(calibration)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
