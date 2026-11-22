"""Per-gate funnel on saved reports, and a sheet of rejects to label (v13.28; research report of 22 Nov, Method 0).

    python tools/gate_funnel.py              # last 4 saved reports, each and in total
    python tools/gate_funnel.py --last 8
    python tools/gate_funnel.py --sheet      # also writes data/labels/rejects_<date>.csv

No model calls. Fill the sheet's "verdict" column with rightly / wrongly (was the reject correct?).
If more than about 30% of a gate's rejects are "wrongly", that gate is miscalibrated (report, Method 2.6).
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _reports(last: int) -> list[tuple[str, str, dict]]:
    uri = (ROOT / "data" / "reports.sqlite3").resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        rows = db.execute("SELECT r.report_id, r.created_at, v.payload_json FROM reports r JOIN report_revisions v "
                          "ON v.report_revision_id = r.current_revision_id ORDER BY r.created_at DESC LIMIT ?",
                          (last,)).fetchall()
    return [(rid, created, json.loads(payload).get("sections") or {}) for rid, created, payload in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--last", type=int, default=4)
    parser.add_argument("--sheet", action="store_true", help="write the rejects to label")
    args = parser.parse_args()
    from services import gate_funnel
    funnels, rows = [], []
    for report_id, created, sections in _reports(args.last):
        meta = sections.get("analysis_metadata") or {}
        gate_log, precedents = meta.get("claim_support_gate") or {}, meta.get("precedent_research") or []
        funnel = gate_funnel.compute(gate_log, precedents)
        funnels.append(funnel)
        print(f"\n{report_id}  {created[:16]}")
        print("\n".join(gate_funnel.lines(funnel)))
        rows += gate_funnel.reject_rows(gate_log, precedents, report_id)
    print(f"\nTOTAL over {len(funnels)} report(s)")
    print("\n".join(gate_funnel.lines(gate_funnel.total(funnels))))
    if args.sheet and rows:
        out = ROOT / "data" / "labels" / f"rejects_{date.today().isoformat()}.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\n{len(rows)} rejects written to {out} - fill 'verdict' with rightly / wrongly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
