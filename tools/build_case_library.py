"""Draft and review the past-case library (v13.31). The pipeline uses only cases YOU approve.

    python tools/build_case_library.py draft      # search CourtListener with mechanism queries, tag each
                                                  # result with the yes/no judge (Groq gpt-oss-20b), and
                                                  # add drafts (approved: false) to data/case_library.jsonl
    python tools/build_case_library.py review     # show each draft: y approve, n delete, s skip, q quit
    python tools/build_case_library.py list       # approved cases

Both research reports of 22 Nov: live past-case search found 3 cases in 18 searches because the free news
providers cannot reach old cases, so build a library once, tagged by mechanism, legal theory and outcome,
and retrieve by structure (services/case_library.py). Seed mechanisms from the reports: tobacco (Master
Settlement Agreement), opioids (Oklahoma v. Johnson & Johnson), lead paint (People v. ConAgra; Rhode
Island 2008), social media (MDL 3047, New Mexico v. Meta), plus AI copyright fair use, platform antitrust
remedies, export-control enforcement and state consumer-protection suits against tech companies.
CourtListener: needs COURTLISTENER_API_TOKEN in .env; new free accounts get about 5 requests a minute,
50 an hour and 125 a day (limits lowered on 7 May 2026), so `draft` makes at most --queries searches.
Outcomes and durations the search cannot see are left blank for you to fill before approving.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LIBRARY = ROOT / "data" / "case_library.jsonl"
QUERIES = (
    "public nuisance lead paint ConAgra abatement", "public nuisance opioid Johnson Johnson Oklahoma reversed",
    "tobacco master settlement agreement attorneys general", "social media addiction design defect Section 230 school district",
    "New Mexico attorney general Meta public nuisance", "fair use training data generative AI copyright",
    "United States v. Google search monopoly remedies", "state attorney general deceptive trade practices app minors",
    "export administration regulations penalty semiconductor", "consumer protection unfair competition law California technology company",
    "Federal Trade Commission consent decree data security", "Section 230 product design claims dismissed in part",
)
TAG_FIELDS = ("jurisdiction", "plaintiff_type", "defendant_sector", "legal_theory", "mechanism_tags", "key_holding",
              "outcome", "outcome_date", "time_to_outcome_days", "disanalogy_notes")


def _load() -> list[dict]:
    if not LIBRARY.exists():
        return []
    return [json.loads(line) for line in LIBRARY.read_text(encoding="utf-8").splitlines() if line.strip()]


def _save(rows: list[dict]) -> None:
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    LIBRARY.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _search(query: str) -> list[dict]:
    import requests
    token = os.getenv("COURTLISTENER_API_TOKEN", "").strip()
    headers = {"Authorization": f"Token {token}"} if token else {}
    url = "https://www.courtlistener.com/api/rest/v4/search/?" + urlencode({"q": query, "type": "o", "order_by": "score desc"})
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return [r for r in (response.json().get("results") or [])[:3] if isinstance(r, dict)]


def _snippet(result: dict) -> str:
    """CourtListener v4 search puts the text snippet under opinions[]; older responses had it at the top."""
    parts = [str(result.get("snippet") or "")]
    parts += [str(o.get("snippet") or "") for o in result.get("opinions") or [] if isinstance(o, dict)]
    return " ".join(p for p in parts if p.strip())


def _tag(result: dict, query: str) -> dict:
    from services import outside_judge
    prompt = ("Tag this court opinion for a library of past cases. Use only what the text shows; leave a field empty "
              "if it does not say.\nCASE: " + str(result.get("caseName")) + " (" + str(result.get("court")) + ", filed "
              + str(result.get("dateFiled")) + ")\nTEXT: " + " ".join(_snippet(result).split())[:1500]
              + "\nSEARCHED FOR: " + query + "\n\nReturn JSON {" + ",".join(f'"{f}":""' for f in TAG_FIELDS)
              + "} with mechanism_tags as a list of short tags (e.g. affirmative-promotion, design-defect, "
                "failure-to-warn, section-230, preemption, pre-release-approval, public-nuisance, fair-use).")
    try:
        data = json.loads(outside_judge.ask(prompt, kind="simple"))
    except Exception as error:
        print(f"  tagging failed ({type(error).__name__}); kept untagged")
        data = {}
    return {f: data.get(f, [] if f == "mechanism_tags" else "") for f in TAG_FIELDS}


def draft(max_queries: int) -> int:
    from services import project_env
    project_env.load()
    rows = _load()
    known = {r.get("source_url") for r in rows}
    added = 0
    for query in QUERIES[:max_queries]:
        try:
            results = _search(query)
        except Exception as error:
            print(f"Search failed for {query!r}: {error}")
            continue
        for result in results:
            url = "https://www.courtlistener.com" + str(result.get("absolute_url") or "")
            if url in known:
                continue
            known.add(url)
            row = {"case_id": "CASE-" + hashlib.sha256(url.encode()).hexdigest()[:10].upper(),
                   "name": result.get("caseName"), "year": str(result.get("dateFiled") or "")[:4],
                   "source_url": url, "found_by": query, "approved": False, **_tag(result, query)}
            rows.append(row)
            added += 1
            print(f"+ {row['case_id']} {row['name']} ({row['year']})")
        time.sleep(13)   # about 5 requests a minute on a new free CourtListener account
    _save(rows)
    print(f"\n{added} draft case(s) added to {LIBRARY}. Review them: python tools/build_case_library.py review")
    return 0


def review() -> int:
    rows = _load()
    keep = []
    for n, row in enumerate(rows):
        if row.get("approved"):
            keep.append(row)
            continue
        print("\n" + json.dumps({k: row.get(k) for k in ("case_id", "name", "year", "source_url") + TAG_FIELDS},
                                ensure_ascii=False, indent=1))
        answer = input("approve (y) / delete (n) / skip (s) / quit (q)? ").strip().casefold()[:1]
        if answer == "q":
            keep += rows[n:]
            break
        if answer == "y":
            row["approved"] = True
            keep.append(row)
        elif answer != "n":
            keep.append(row)
    _save(keep)
    print(f"{sum(1 for r in keep if r.get('approved'))} approved case(s) in the library.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("draft", "review", "list"))
    parser.add_argument("--queries", type=int, default=len(QUERIES))
    args = parser.parse_args()
    if args.command == "draft":
        return draft(args.queries)
    if args.command == "review":
        return review()
    for row in _load():
        if row.get("approved"):
            print(f"{row['case_id']} {row.get('name')} ({row.get('year')}): {row.get('legal_theory')} -> {row.get('outcome')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
