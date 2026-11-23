"""Blind side-by-side test: your pipeline's insights against paragraphs from analyst publications (v13.31).

The only credible proof that insights are "at par" (both research reports of 22 Nov): blind pairwise
comparison by readers, not LLM judges - LLM judges agree with expert reviewers on novelty about as often as
chance (Si et al.: best judge 53.3% vs 56.1% between reviewers).

1) Fill data/blind_pairs_input.csv with columns:
     event, pipeline_text, pipeline_date, benchmark_text, benchmark_source, benchmark_date
   One row per event: your app's insight and a same-event paragraph from Money Stuff, FT Lex, Lawfare,
   BIG, Stratechery etc. The app's insight must be dated BEFORE the benchmark piece (so it could not have
   drawn on it); rows that are not are skipped.
2) python tools/blind_pairs.py make     -> data/blind_sheet.csv (A/B in random order, formatting stripped,
                                           both cut to the same length) and data/blind_key.csv (do not open)
3) Readers fill "choice" with A, B or tie (and "reason") in blind_sheet.csv - you and 1-2 others who read
   these publications. Si et al. style-normalised so well that experts could tell AI from human only 50%
   of the time; without it, readers judge voice, not substance.
4) python tools/blind_pairs.py score    -> the app's win rate (ties count half) with a 95% Wilson interval,
                                           and whether it is non-inferior at 45%.
Sample size (analyst report, standard binomial power): about 193 judgments to tell 40% from 50% at 80%
power; start with a pilot of 30.
"""
from __future__ import annotations

import csv
import math
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _plain(text: str) -> str:
    text = re.sub(r"[*_#>`\[\]]", "", str(text or ""))
    return " ".join(text.split())


def _cut(text: str, words: int) -> str:
    parts = text.split()
    return " ".join(parts[:words]) + (" ..." if len(parts) > words else "")


def make(seed: int = 22) -> int:
    source = DATA / "blind_pairs_input.csv"
    if not source.exists():
        print(f"Create {source} first (see the instructions at the top of this file).")
        return 1
    with source.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rng = random.Random(seed)
    sheet, key, skipped = [], [], 0
    for n, row in enumerate(rows, start=1):
        if (row.get("pipeline_date") or "") > (row.get("benchmark_date") or "9999"):
            skipped += 1
            continue
        app, other = _plain(row.get("pipeline_text")), _plain(row.get("benchmark_text"))
        if not app or not other:
            skipped += 1
            continue
        words = min(len(app.split()), len(other.split()), 250)
        app, other = _cut(app, words), _cut(other, words)
        app_is_a = rng.random() < 0.5
        sheet.append({"id": n, "event": row.get("event", ""), "A": app if app_is_a else other,
                      "B": other if app_is_a else app, "choice": "", "reason": ""})
        key.append({"id": n, "app": "A" if app_is_a else "B", "benchmark_source": row.get("benchmark_source", "")})
    for name, rows_out in (("blind_sheet.csv", sheet), ("blind_key.csv", key)):
        with (DATA / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows_out[0]) if rows_out else ["id"])
            writer.writeheader()
            writer.writerows(rows_out)
    print(f"{len(sheet)} pairs written to data/blind_sheet.csv ({skipped} skipped: app insight not earlier, or empty).")
    return 0


def wilson(wins: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = wins / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def score() -> int:
    with (DATA / "blind_sheet.csv").open(encoding="utf-8", newline="") as handle:
        sheet = {r["id"]: r for r in csv.DictReader(handle)}
    with (DATA / "blind_key.csv").open(encoding="utf-8", newline="") as handle:
        key = {r["id"]: r for r in csv.DictReader(handle)}
    wins = ties = losses = 0
    by_source: dict[str, list[float]] = {}
    for pid, row in sheet.items():
        choice = (row.get("choice") or "").strip().upper()
        if choice not in {"A", "B", "TIE"} or pid not in key:
            continue
        value = 0.5 if choice == "TIE" else (1.0 if choice == key[pid]["app"] else 0.0)
        wins += value == 1.0
        ties += value == 0.5
        losses += value == 0.0
        by_source.setdefault(key[pid].get("benchmark_source") or "?", []).append(value)
    n = wins + ties + losses
    if not n:
        print("No judged pairs yet (fill the 'choice' column with A, B or tie).")
        return 1
    rate = (wins + 0.5 * ties) / n
    low, high = wilson(wins + 0.5 * ties, n)
    print(f"App vs analysts: {wins} wins, {ties} ties, {losses} losses over {n} pairs - win rate {rate:.0%} "
          f"(95% interval {low:.0%}-{high:.0%}).")
    print("Non-inferior at 45%: " + ("yes" if low >= 0.45 else "not shown yet") + f"; about 193 judgments are needed "
          "to tell 40% from 50%.")
    for source, values in sorted(by_source.items()):
        print(f"  vs {source}: {sum(values) / len(values):.0%} over {len(values)}")
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    raise SystemExit(make() if command == "make" else score() if command == "score" else (print(__doc__) or 1))
