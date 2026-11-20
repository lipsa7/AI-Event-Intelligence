"""Print the quality gates, funnel and run metrics of the latest saved report (v13, 18 Nov 2026; metrics v13.13).

    python -m tools.run_quality            latest report
    python -m tools.run_quality --last 5   the last five, one line each

The gates are computed automatically at the end of every run (services/run_quality.py) and saved in
the report's analysis metadata; this command only reads them. Exit code 1 when the latest report
failed a gate, so it can run in a scheduled check.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "reports.sqlite3"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--last", type=int, default=1)
    args = parser.parse_args(argv)
    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = connection.execute(
        "select r.created_at, r.research_question, v.payload_json from reports r join report_revisions v "
        "on v.report_revision_id = r.current_revision_id order by r.created_at desc limit ?", (args.last,)).fetchall()
    failed_latest = False
    for index, (created, question, payload) in enumerate(rows):
        data = json.loads(payload)
        meta = (data.get("analysis_metadata") or {}) or ((data.get("sections") or {}).get("analysis_metadata") or {})
        quality = (data.get("analysis_metadata") or {}).get("quality_gates") \
            or ((data.get("sections") or {}).get("analysis_metadata") or {}).get("quality_gates")
        metrics = meta.get("run_metrics") or ((data.get("sections") or {}).get("analysis_metadata") or {}).get("run_metrics")
        if metrics:
            # v13.13: the run's measured record (services/run_metrics.py)
            from services import run_metrics
            g, f = metrics.get("grounding") or {}, metrics.get("insight_funnel") or {}
            if args.last > 1:
                print(f"{created[:16]}  metrics: released {f.get('insights_released')}/{f.get('candidates_checked')}, "
                      f"claim support {g.get('claim_support_rate')}, citation coverage {g.get('citation_coverage')}, "
                      f"repeats caught {f.get('repeats_of_earlier_caught')}")
            else:
                print(f"{created[:16]}  {question[:40]!r}: run metrics")
                for line in run_metrics.summary_lines(metrics):
                    print("  " + line)
        if not quality:
            print(f"{created[:16]}  {question[:40]!r}: no quality gates (report made before v13)")
            continue
        failed = [name for name, result in quality["gates"].items() if not result["passed"]]
        if index == 0:
            failed_latest = bool(failed)
        print(f"{created[:16]}  {question[:40]!r}: {'PASSED' if not failed else 'FAILED ' + ', '.join(failed)}")
        if args.last == 1:
            for name, result in quality["gates"].items():
                print(f"  {'ok  ' if result['passed'] else 'FAIL'} {name}: {result['detail']}")
            print("  funnel:", json.dumps(quality.get("funnel"), indent=2, default=str))
    return 1 if failed_latest else 0


if __name__ == "__main__":
    sys.exit(main())
