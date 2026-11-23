"""Rate new insights so the editor's-bar judge can be calibrated on more of YOUR ratings (v13.31).

    python tools/rate_insights.py                 # the latest report's insights and drafts, least-sure first
    python tools/rate_insights.py REPORT-ID

Both research reports of 22 Nov: 90 labels are too few to certify a judge (Eugene Yan: "at least 50-100
failures out of 200+ total samples"; a precision of 0.7 on ~20 predicted positives has a 95% interval of
about 0.48-0.85), and the ratings that teach it most are the ones the judge is least sure about. Each answer
is appended to data/insight_review_90.csv in the same columns, so tools/calibrate_par.py uses it next time.
Keys: g = good, e = explanatory/speculative, r = generic restatement, s = skip, q = quit.
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LABELS = {"g": "good", "e": "explanatory/speculative", "r": "generic restatement"}


def candidates(report_id: str = "") -> tuple[str, list[str]]:
    uri = (ROOT / "data" / "reports.sqlite3").resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        query = ("SELECT r.report_id, v.payload_json FROM reports r JOIN report_revisions v "
                 "ON v.report_revision_id = r.current_revision_id ")
        row = (db.execute(query + "WHERE r.report_id = ?", (report_id,)).fetchone() if report_id
               else db.execute(query + "ORDER BY r.created_at DESC LIMIT 1").fetchone())
    if not row:
        raise SystemExit("No report found.")
    sections = json.loads(row[1]).get("sections") or {}
    meta = sections.get("analysis_metadata") or {}
    texts = [r.get("text") for r in (meta.get("run_metrics") or {}).get("rate_next") or []]
    texts += [i.get("text") or i.get("insight") for i in sections.get("hidden_insights") or [] if isinstance(i, dict)]
    for combo in ((meta.get("claim_support_gate") or {}).get("exploration") or {}).get("combinations") or []:
        texts += [k.get("insight") for k in combo.get("kept") or []]
        texts += [d.get("insight") for d in combo.get("variant_dropped") or []]
    seen, out = set(), []
    for text in texts:
        text = " ".join(str(text or "").split())
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return row[0], out


def main() -> int:
    report_id, texts = candidates(sys.argv[1] if len(sys.argv) > 1 else "")
    path = ROOT / "data" / "insight_review_90.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rated = {" ".join((r.get("insight") or "").split()) for r in rows}
    fields = list(rows[0].keys()) if rows else ["no", "date", "my_rating", "your_rating", "v7_outcome", "insight"]
    next_no = max([int(r["no"]) for r in rows if str(r.get("no", "")).isdigit()] or [0]) + 1
    todo = [t for t in texts if t not in rated]
    print(f"{report_id}: {len(todo)} unrated insight(s). g good / e explanatory-speculative / r generic restatement / s skip / q quit")
    added = 0
    for text in todo:
        print("\n" + text)
        answer = input("> ").strip().casefold()[:1]
        if answer == "q":
            break
        if answer not in LABELS:
            continue
        with path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writerow({**{f: "" for f in fields}, "no": str(next_no), "date": date.today().isoformat(),
                             "my_rating": LABELS[answer], "v7_outcome": "rated with tools/rate_insights.py",
                             "insight": text})
        next_no += 1
        added += 1
    print(f"\n{added} rating(s) added to {path.name} ({len(rows) + added} in total). "
          "Rerun tools/calibrate_par.py when you have about 20 new ones.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
