"""Print the app's track record from its own saved data (v13.20). No model calls.

    python tools/audit.py            # summary
    python tools/audit.py --json     # every figure as JSON

Every number the track-record page shows comes from services/track_record.py, recomputed here from
data/reports.sqlite3, data/forecasts/forecasts.sqlite3 and data/reader_ratings.jsonl.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default="data", help="data folder (default: data)")
    parser.add_argument("--json", action="store_true", help="print every figure as JSON")
    args = parser.parse_args()
    from services import track_record
    record = track_record.compute(args.data)
    if args.json:
        print(json.dumps(record, indent=2, ensure_ascii=False, default=str))
    else:
        for line in track_record.summary_lines(record):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
