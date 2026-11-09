"""Compare two reports from the same checkpoint (v6.35 experiment: baseline vs profile).

    python compare_runs.py                       # latest valid pair: same checkpoint, org, version; baseline vs profile
    python compare_runs.py REPORT-A REPORT-B     # specific reports (checked the same way; --force to override)
    python compare_runs.py --judge               # also ask the pairwise judge (about 18 short calls)
    python compare_runs.py --blind-export        # v6.36.6: write the items of both runs, shuffled and unlabelled
    python compare_runs.py --blind-import        # ... read your ratings back, then compare

v6.36.6 (research review): rate BLIND - evaluators who know which system produced an output rate it
differently (QUEST framework; Si et al. used blind review). And a single run per arm cannot separate
an effect from run-to-run noise (agentic-eval study: single-run gaps of 2-3 points can be noise), so
if a SECOND baseline replay of the same checkpoint exists, the baseline-vs-baseline difference is shown
for reference next to the baseline-vs-profile difference (v6.36.7: one observed variation, not a
reliable noise estimate; the verdict says better or worse, never just a gap).

Order of evidence (GPT review, 2026-10-22): YOUR ratings first, then the model judge, then
counts. Rate the items first in the dashboard ("Rate these items"). If the profile report is
not better on your ratings, the profile should not stay on by default.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from services import insight_evaluator as judge  # noqa: E402
from services import insight_ratings  # noqa: E402


def experiment(report: dict) -> dict:
    meta = report["sections"].get("analysis_metadata") or {}
    exp = meta.get("experiment") or {}
    profile = meta.get("organisation_profile") or {}
    return {"replay_of": exp.get("replay_of", ""), "focus": (exp.get("focus_organisation") or "").casefold(),
            "advice_produced": exp.get("advice_produced"),
            "version": exp.get("service_version", ""), "topic": exp.get("topic", ""),
            "profile_used": bool(profile.get("used")),
            "user_hypotheses": exp.get("user_hypotheses", ""),
            "control_signature": exp.get("control_signature", ""),
            "controls_stable": exp.get("controls_stable"),
            "replay_input_hash": exp.get("replay_input_hash", "")}


def pair_problems(a: dict, b: dict) -> list[str]:
    """v6.35.1 (GPT review): a comparison is valid only if everything but the profile is the same."""
    ea, eb = experiment(a), experiment(b)
    problems = []
    if not ea["replay_of"] or not eb["replay_of"]:
        problems.append("a report has no checkpoint record (made before v6.35.1, or not a replay)")
    elif ea["replay_of"] != eb["replay_of"]:
        problems.append(f"different checkpoints: {ea['replay_of']} vs {eb['replay_of']}")
    if not ea["focus"] or not eb["focus"]:
        problems.append("a report does not record which organisation it advised")
    elif ea["focus"] != eb["focus"]:
        problems.append(f"different organisation advised: {ea['focus']} vs {eb['focus']}")
    if ea.get("advice_produced") is False or eb.get("advice_produced") is False:
        problems.append("a run produced no advice for the organisation (its next-steps stage failed)")
    if not ea["version"] or not eb["version"]:
        problems.append("a report does not record its software version")
    elif ea["version"] != eb["version"]:
        problems.append(f"different software versions: {ea['version']} vs {eb['version']}")
    if ea.get("user_hypotheses", "") != eb.get("user_hypotheses", ""):   # v6.36.5
        problems.append("the runs used different hypotheses of your own (--hypotheses)")
    from services.experiment_controls import problems as control_problems
    problems.extend(control_problems(ea, eb))
    if ea["profile_used"] == eb["profile_used"]:
        problems.append("both reports are " + ("profile runs" if ea["profile_used"] else "baseline runs"))
    return problems


def noise_problems(a: dict, b: dict) -> list[str]:
    """v6.36.6: two BASELINE replays that differ only by run-to-run randomness."""
    ea, eb = experiment(a), experiment(b)
    problems = [f"different {name}" for name in ("replay_of", "focus", "version", "user_hypotheses")
                if not ea.get(name) == eb.get(name)] + [p for p in ("replay_of", "focus", "version") if not ea.get(p)]
    from services.experiment_controls import problems as control_problems
    problems.extend(control_problems(ea, eb))
    if ea["profile_used"] or eb["profile_used"]:
        problems.append("a noise check needs two baseline runs")
    if a["report_id"] == b["report_id"]:
        problems.append("the same report twice")
    return problems


def noise_partner(db: Path, baseline: dict) -> dict | None:
    """Another baseline replay of the same checkpoint, organisation and version, if one exists."""
    with sqlite3.connect(str(db)) as conn:
        ids = [r[0] for r in conn.execute("SELECT report_id FROM report_revisions GROUP BY report_id "
                                          "ORDER BY MAX(created_at) DESC LIMIT 40").fetchall()]
    for report in load_reports(db, [i for i in ids if i != baseline["report_id"]]):
        if not noise_problems(baseline, report):
            return report
    return None


def share(summary: dict) -> float | None:
    """Useful share, unrounded (v6.36.7: differences are taken before rounding)."""
    return summary["useful"] / summary["rated"] if summary.get("rated") else None


BLIND_CSV = ROOT / "data" / "blind_rating.csv"
BLIND_KEY = ROOT / "data" / "blind_key.json"


def blind_export(reports: list[dict], csv_path: Path | None = None, key_path: Path | None = None,
                 seed: int | None = None) -> int:
    """Items of all runs in one shuffled, unlabelled list; which run each came from is kept in a key file."""
    csv_path, key_path = csv_path or BLIND_CSV, key_path or BLIND_KEY
    rows: dict[str, dict] = {}
    for report in reports:
        profile_used = experiment(report)["profile_used"]
        for item in insight_ratings.rating_items(report["sections"]):
            evidence = item.get("evidence") or []
            limitations = item.get("limitations") or ""
            identity = json.dumps([item["text"].casefold(), evidence, limitations], ensure_ascii=False)
            slot = rows.setdefault(identity, {"text": item["text"], "evidence": evidence,
                                               "limitations": limitations, "keys": []})
            slot["keys"].append({"report_id": report["report_id"], "run_id": report["run_id"], "item_key": item["key"],
                                 "kind": item["kind"], "profile_used": profile_used})
    ordered = list(rows.values())
    random.Random(seed).shuffle(ordered)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["no", "text", "rating (useful / meh / poor)", "reason (optional)",
                         "source excerpts", "limitations / assumptions"])
        for n, row in enumerate(ordered, start=1):
            writer.writerow([n, row["text"], "", "", " | ".join(row["evidence"]), row["limitations"]])
    roles = {"baseline": "", "profile": "", "second_baseline": ""}   # v6.36.7: import compares exactly these
    for report in reports:
        role = "profile" if experiment(report)["profile_used"] else "baseline" if not roles["baseline"] else "second_baseline"
        roles[role] = roles[role] or report["report_id"]
    key_path.write_text(json.dumps({"reports": [r["report_id"] for r in reports], "roles": roles,
                                    "items": {str(n): row for n, row in enumerate(ordered, start=1)}},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    return len(ordered)


def blind_import(csv_path: Path | None = None, key_path: Path | None = None,
                 target: Path | None = None) -> tuple[int, list[str]]:
    csv_path, key_path = csv_path or BLIND_CSV, key_path or BLIND_KEY
    key = json.loads(key_path.read_text(encoding="utf-8"))
    entries, problems = [], []
    with open(csv_path, encoding="utf-8-sig", newline="") as stream:
        for row in list(csv.reader(stream))[1:]:
            if not row or not row[0].strip():
                continue
            no, rating = row[0].strip(), (row[2] if len(row) > 2 else "").strip().casefold()
            reason = row[3] if len(row) > 3 else ""
            if not rating:
                continue
            if rating not in insight_ratings.RATINGS or no not in key["items"]:
                problems.append(f"row {no}: '{rating}' is not useful / meh / poor" if no in key["items"]
                                else f"row {no}: not in the key file")
                continue
            item = key["items"][no]
            if len(row) < 2 or row[1].strip() != item["text"].strip():
                problems.append(f"row {no}: text no longer matches the export key; rating not imported")
                continue
            entries += [dict(k, text=item["text"], rating=rating, reason=reason, blind=True) for k in item["keys"]]
    return insight_ratings.save(entries, target), problems


def exported_roles(key_path: Path | None = None) -> dict:
    """v6.36.7 (GPT review): the runs a blind export was made from - import compares exactly these,
    not whichever runs are newest when you import."""
    key = json.loads((key_path or BLIND_KEY).read_text(encoding="utf-8"))
    roles = key.get("roles") or {}
    if not roles.get("baseline") or not roles.get("profile"):
        raise SystemExit("The blind key file does not name a baseline and a profile run; export again.")
    return roles


def verdict(ratings: list[dict], baseline: dict, profile: dict, second: dict | None) -> list[str]:
    """Your ratings, with direction (v6.36.7, GPT review): better, worse or the same - never only a gap."""
    rb, rp = (insight_ratings.summary(ratings, r["report_id"]) for r in (baseline, profile))
    if not rb["rated"] or not rp["rated"]:
        return ["Rate both runs first, preferably blind (--blind-export / --blind-import); nothing is decided "
                "without your ratings."]
    ids = {baseline["report_id"], profile["report_id"]} | ({second["report_id"]} if second else set())
    blind = all(r.get("blind") for r in ratings if r.get("report_id") in ids)
    lines = [f"Baseline useful {rb['useful']}/{rb['rated']} ({share(rb):.2f}) vs profile useful "
             f"{rp['useful']}/{rp['rated']} ({share(rp):.2f}); poor: baseline {rb['poor']}, profile {rp['poor']}. "
             f"Rated {'blind' if blind else 'NOT blind - you could see which run each item came from'}."]
    change = share(rp) - share(rb)
    lines.append("Direction: " + (f"the profile run was rated BETTER by {change:+.2f} in useful share." if change > 0
                                  else f"the profile run was rated WORSE by {change:+.2f} in useful share." if change < 0
                                  else "no difference in useful share."))
    rn = insight_ratings.summary(ratings, second["report_id"]) if second else {"rated": 0}
    if second and rn["rated"]:
        # v6.36.7 (GPT review): two baselines give ONE observed variation, not a reliable noise estimate,
        # so it is shown for reference and no conclusion about chance is drawn from it.
        spread = share(rn) - share(rb)
        lines.append(f"For reference, the two baseline runs differed by {spread:+.2f} in useful share. That is one "
                     "observed variation between identical setups, not a reliable estimate of chance: a profile "
                     f"difference of {change:+.2f} {'is' if abs(change) > abs(spread) else 'is not'} larger than it, "
                     "and either way the result is preliminary.")
    else:
        lines.append("No rated second baseline, so there is no sense of how much identical runs vary. Treat the "
                     "difference as preliminary; replaying the baseline again (and rating it blind) adds a reference.")
    return lines


def latest_valid_pair(db: Path) -> list[str]:
    """Most recent baseline and profile reports sharing checkpoint, organisation and version."""
    with sqlite3.connect(str(db)) as conn:
        ids = [r[0] for r in conn.execute("SELECT report_id FROM report_revisions GROUP BY report_id "
                                          "ORDER BY MAX(created_at) DESC LIMIT 40").fetchall()]
    reports = load_reports(db, ids)
    groups: dict[tuple, dict] = {}
    for report in reports:                       # newest first
        e = experiment(report)
        if not (e["replay_of"] and e["focus"] and e["version"]):
            continue
        if e.get("control_signature") and e.get("controls_stable") is not True:
            continue
        slot = groups.setdefault((e["replay_of"], e["focus"], e["version"], e["user_hypotheses"],
                                  e.get("control_signature"), e.get("replay_input_hash")), {})
        slot.setdefault("profile" if e["profile_used"] else "baseline", report["report_id"])
        if len(slot) == 2:
            return [slot["baseline"], slot["profile"]]
    raise SystemExit("No valid pair yet: need a baseline replay and a --profile replay of the same checkpoint, "
                     "organisation and software version (v6.35.1 or later).")


def load_reports(db: Path, ids: list[str]) -> list[dict]:
    with sqlite3.connect(str(db)) as conn:
        out = []
        for report_id in ids:
            row = conn.execute("SELECT payload_json FROM report_revisions WHERE report_id = ? "
                               "ORDER BY revision_no DESC LIMIT 1", (report_id,)).fetchone()
            if not row:
                raise SystemExit(f"Report not found: {report_id}")
            payload = json.loads(row[0])
            out.append({"report_id": report_id, "run_id": payload.get("run_id", ""),
                        "sections": payload.get("sections") or {}})
    return out


def describe(report: dict, ratings: list[dict]) -> dict:
    s = report["sections"]
    meta = s.get("analysis_metadata") or {}
    profile = meta.get("organisation_profile") or {}
    evaluation = s.get("insight_evaluation") or {}
    items = [i for i in evaluation.get("items") or [] if isinstance(i, dict)]
    lab = s.get("strategic_hypotheses") or {}
    top = items[:3]
    return {
        "report_id": report["report_id"], "run_id": report["run_id"],
        "profile": (profile.get("name", "") + " / " + profile.get("fingerprint", "")) if profile.get("used") else "none (baseline)",
        "experiment": experiment(report),
        "your_ratings": insight_ratings.summary(ratings, report["report_id"]),
        "ranked_items": len(items),
        "strong_or_useful": sum(1 for i in items if i.get("band") in {"strong strategic insight", "useful insight"}),
        "leverage_decisions": sum(1 for i in items if i.get("strategic_type") == "leverage_decision"),
        "mean_score_top3": round(sum(i.get("score", 0) for i in top) / len(top), 1) if top else None,
        "hypotheses_verified": len(lab.get("verified") or []),
        "hypotheses_not_yet_backed": len(lab.get("promising_unverified") or []),
        "top3": [f"{i.get('score')}/12 {i.get('provenance_label', '')}: {str(i.get('text', ''))[:140]}" for i in top],
    }


def judge_tops(a: dict, b: dict, question: str) -> dict:
    from services.gemini_llm_service import GeminiLLMService
    llm = GeminiLLMService(model_name=os.getenv("GEMINI_BRIEFING_MODEL", "gemini-3.6-flash"), default_json=True)

    def ask(prompt):
        return llm.ask(prompt, system_prompt="Return one JSON object only.", temperature=0.0,
                       response_format="json", metadata={"task": "compare_runs"})

    tops = [[i for i in (r["sections"].get("insight_evaluation") or {}).get("items") or []][:3] for r in (a, b)]
    wins = {"A": 0, "B": 0, "tie_or_uncertain": 0}
    for x in tops[0]:
        for y in tops[1]:
            first = judge.parse_winner(ask(judge.pairwise_prompt(question, x, y)))
            second = {"A": "B", "B": "A"}.get(judge.parse_winner(ask(judge.pairwise_prompt(question, y, x))), "TIE")
            wins[first if first == second and first in {"A", "B"} else "tie_or_uncertain"] += 1
    return wins


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reports", nargs="*", help="REPORT-A REPORT-B (default: the two latest)")
    parser.add_argument("--db", default=str(ROOT / "data" / "reports.sqlite3"))
    parser.add_argument("--judge", action="store_true", help="pairwise-judge the top 3 of A against the top 3 of B")
    parser.add_argument("--force", action="store_true", help="compare even if the pair is not a valid experiment")
    parser.add_argument("--blind-export", action="store_true",
                        help="write the items of both runs (and a second baseline, if any) shuffled and unlabelled "
                             "to data/blind_rating.csv for you to rate")
    parser.add_argument("--blind-import", action="store_true", help="read data/blind_rating.csv back, then compare")
    args = parser.parse_args()
    if args.reports and len(args.reports) != 2:
        parser.error("give two report IDs, or none for the latest valid pair")
    db = Path(args.db)
    roles = exported_roles() if args.blind_import else {}
    if roles:   # v6.36.7: exactly the runs that were exported
        ids = [roles["baseline"], roles["profile"]]
        if args.reports and set(args.reports) != set(ids):
            print("Note: comparing the runs saved with the blind export, not the report IDs given.")
    else:
        ids = list(args.reports) or latest_valid_pair(db)
    a, b = load_reports(db, ids)
    problems = pair_problems(a, b)
    if problems:
        print("NOT A VALID COMPARISON:\n  - " + "\n  - ".join(problems))
        if not args.force:
            return 2
        print("(--force: comparing anyway; treat the result as anecdotal)")
    baseline, profile = (a, b) if not experiment(a)["profile_used"] else (b, a)
    if roles:
        second = load_reports(db, [roles["second_baseline"]])[0] if roles.get("second_baseline") else None
    else:
        second = noise_partner(db, baseline)
    if args.blind_export:
        count = blind_export([baseline, profile] + ([second] if second else []))
        print(f"Wrote {count} items from {2 + bool(second)} runs, shuffled and unlabelled, to {BLIND_CSV}.\n"
              "Open it (Excel is fine), put useful / meh / poor in the rating column, save it as CSV, then run:\n"
              "  python compare_runs.py --blind-import\n"
              f"Do not open {BLIND_KEY.name} before rating: it says which run each item came from.")
        if not second:
            print("No second baseline yet: replaying the baseline once more (then exporting again) adds a reference "
                  "for how much identical runs vary.")
        return 0
    if args.blind_import:
        saved, bad = blind_import()
        print(f"Saved {saved} blind ratings." + ("\n  - " + "\n  - ".join(bad) if bad else ""))
    ratings = insight_ratings.load()
    for label, report in (("BASELINE", baseline), ("PROFILE", profile)):
        print(f"\n=== {label}: " + json.dumps(describe(report, ratings), indent=1, ensure_ascii=False))
    print("\nYOUR VERDICT (decides):")
    for line in verdict(ratings, baseline, profile, second):
        print("  " + line)
    print("\nOne checkpoint and one rater: even a clear result is a go / no-go signal for this story, not proof.")
    if args.judge:
        question = (a["sections"].get("research_question") or "artificial intelligence")
        print("\nMODEL JUDGE (secondary; both orders, flips count as uncertain; A = baseline, B = profile): "
              + json.dumps(judge_tops(baseline, profile, question)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
