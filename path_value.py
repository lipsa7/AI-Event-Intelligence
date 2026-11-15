"""What each analysis path cost in one report's run, beside your ratings of its items. No model calls.

    python path_value.py                 # the latest report
    python path_value.py REPORT-...      # a given report
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from services import insight_ratings, path_costs  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("report_id", nargs="?")
    parser.add_argument("--db", default=str(ROOT / "data" / "reports.sqlite3"))
    args = parser.parse_args()
    with sqlite3.connect(f"file:{args.db}?mode=ro", uri=True) as conn:
        if args.report_id:
            row = conn.execute("SELECT report_id, payload_json FROM report_revisions WHERE report_id=? "
                               "ORDER BY revision_no DESC LIMIT 1", (args.report_id,)).fetchone()
        else:
            row = conn.execute("SELECT report_id, payload_json FROM report_revisions "
                               "ORDER BY created_at DESC LIMIT 1").fetchone()
    if not row:
        print("Report not found.")
        return 1
    report_id, payload = row[0], json.loads(row[1])
    run_id = payload.get("run_id", "")
    print(f"{report_id} ({run_id})")
    for line in path_costs.lines(report_id, run_id, insight_ratings.load()):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
