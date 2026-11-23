"""Is insight quality improving? The quality scorecard of the last saved runs (v13.31). No model calls.

    python tools/quality_scorecard.py            # last 10 reports, oldest first, with the change in rubric score
    python tools/quality_scorecard.py --last 30
    python tools/quality_scorecard.py --stage    # the try_variants stage tests too (data/stage_scores.csv)

Each report's scorecard (services/quality_scorecard.py): candidates scored, mean rubric score (share of the
code-checkable "at par" items each candidate meets), share meeting every item, original insights released,
pass rate per item, and the inputs per story (computed facts, consensus sources, past cases). Reports from
before v13.31 are scored from what they recorded, so early rows are rough.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--last", type=int, default=10)
    parser.add_argument("--stage", action="store_true", help="also show the try_variants stage tests")
    args = parser.parse_args()
    from services import quality_scorecard
    rows = quality_scorecard.history(last=args.last)
    print("Full runs (saved reports):")
    print("\n".join("  " + line for line in quality_scorecard.trend_lines(rows)) or "  none")
    if rows:
        last = rows[-1]
        print("\nLatest run, per item: " + ", ".join(
            f"{k} {v:.0%}" for k, v in (last.get("item_pass_rates") or {}).items() if v is not None))
    stage = ROOT / "data" / "stage_scores.csv"
    if args.stage and stage.exists():
        print("\nStage tests (try_variants):")
        with stage.open(encoding="utf-8") as handle:
            for row in list(csv.DictReader(handle))[-args.last:]:
                print(f"  {row['when']}  {row['run_id']}: {row['candidates']} candidates, rubric {row['mean_rubric']}, "
                      f"every item {row['every_item']}, kept {row['kept']}; most failed: {row['most_failed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
