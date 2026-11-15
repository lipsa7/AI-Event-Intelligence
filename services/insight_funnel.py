"""Where candidate insights go in a run (insight funnel v2, 23 Oct 2026).

Built from what a report already records, so it works on saved reports as well as new
runs and needs no model call. Different things are counted in different units, never mixed:

    candidates  one row per distinct candidate id, with its final outcome:
                passed | rejected | downgraded | repaired (rewritten; see its events)
    events      what happened along the way (a narrowing attempt and its result)
    claims      individual checked claims that failed (one candidate has many)
    attempts    how many gate attempts ran
    sentences   sentences pruned from kept text (the briefing records only a count)
    display     what the dashboard does with a kept candidate: shown | collapsed | cut

A figure a report never recorded is "not_recorded", not 0: an old report without the lab's
counters has not been shown to lose nothing. The funnel makes losses traceable; it does not
say whether a lost item was useful - that is what data/insight_ratings.jsonl is for.

Read-only CLI on saved reports (opens reports.sqlite3 read-only):
    python -m services.insight_funnel            # latest report
    python -m services.insight_funnel REPORT-ID  # one report
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

VERSION = "insight-funnel-v2-units"
CANDIDATE_OUTCOMES = ("passed", "rejected", "downgraded", "repaired")
NOT_RECORDED = "not_recorded"
# What the dashboard shows of the lab (frontend/dashboard.py render_strategic_hypotheses).
DASHBOARD_UNVERIFIED_SHOWN = 10


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _row(item_id: Any, text: Any, outcome: str, reason: Any = "") -> dict[str, str]:
    return {"id": _clean(item_id), "text": _clean(text)[:160], "outcome": outcome, "reason": _clean(reason)[:240]}


def _stage(candidates=(), events=(), claims=(), display=(), attempts: Any = NOT_RECORDED,
           sentences_pruned: Any = NOT_RECORDED, **extra) -> dict[str, Any]:
    return {"candidates": list(candidates), "events": list(events), "claims": list(claims),
            "display": list(display), "attempts": attempts, "sentences_pruned": sentences_pruned, **extra}


def _lab(sections: Mapping[str, Any]) -> dict[str, Any]:
    lab = _map(sections.get("strategic_hypotheses"))
    evaluation = _map(sections.get("insight_evaluation"))
    verified = [h for h in _list(lab.get("verified")) if isinstance(h, Mapping)]
    unverified = [h for h in _list(lab.get("promising_unverified")) if isinstance(h, Mapping)]
    candidates, events = [], []
    for h in verified:
        candidates.append(_row(h.get("id"), h.get("thesis"), "passed", "verified"
                               + (f"; rewritten from {_clean(h.get('critic_inherited_from'))}, unscored"
                                  if h.get("critic_inherited_from") else "")))
    for h in unverified:
        if h.get("status") == "analysis":   # sourced facts; one reasoning link shown as its assumption
            candidates.append(_row(h.get("id"), h.get("thesis"), "passed",
                                   "analysis; assumption: " + "; ".join(_clean(a) for a in _list(h.get("key_assumptions")))))
            continue
        grounding = _map(h.get("grounding"))
        failed = {k: v for k, v in _map(h.get("field_checks")).items()
                  if v not in {"supported", "inference_supported", "not_checked"}}
        reason = "; ".join([f"thesis={_clean(grounding.get('thesis'))}",
                            f"mechanism={_clean(grounding.get('mechanism'))}"]
                           + [f"{k}={v}" for k, v in sorted(failed.items())]
                           + [f"blocked:{b}" for b in _list(h.get("promotion_blockers"))])
        narrowing = _clean(h.get("narrowing"))
        repaired = narrowing.startswith("narrowed_version_verified")
        candidates.append(_row(h.get("id"), h.get("thesis"), "repaired" if repaired else "rejected", reason))
        if narrowing:
            events.append(_row(h.get("id"), h.get("thesis"), "narrowing", narrowing))
    # Dashboard: unverified hypotheses (lab and claim-gate near misses, deduplicated by text)
    # are collapsed when any is verified, and cut after DASHBOARD_UNVERIFIED_SHOWN.
    # Plan fix E: qualifying near misses are shown expanded as "Hypotheses to investigate"
    # (selection here ignores your ratings, which can change which three are shown).
    from services import hypothesis_tier
    investigated = {_clean(h.get("id")) for h in hypothesis_tier.select(sections)}
    display, seen = [], set()
    for h in unverified:
        if _clean(h.get("id")) in investigated:
            seen.add(_clean(h.get("thesis")).casefold())
            display.append(_row(h.get("id"), h.get("thesis"), "shown", "hypotheses_to_investigate"))
    pool = unverified + [h for h in _list(evaluation.get("promising_unverified")) if isinstance(h, Mapping)]
    for h in pool:
        key = _clean(h.get("thesis") or h.get("insight") or h.get("text")).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        state = ("cut" if len(seen) - len(investigated) > DASHBOARD_UNVERIFIED_SHOWN
                 else "collapsed" if verified else "shown")
        display.append(_row(h.get("id") or h.get("insight_id"), key, state))
    display += [_row(h.get("id"), h.get("thesis"), "shown") for h in verified]
    counts = {k: v for k, v in _map(lab.get("funnel")).items() if isinstance(v, int)}
    return _stage(candidates, events, display=display, status=_clean(lab.get("status")),
                  display_selection_basis="default_unrated_selection",
                  display_note="Expected default layout; saved user ratings may change the dashboard selection.",
                  generated_kept=lab.get("raw_count", NOT_RECORDED),
                  evolved_kept=lab.get("evolved_count", NOT_RECORDED),
                  losses_before_grounding=counts or NOT_RECORDED,   # v6.36.8+ runs only
                  narrowing=dict(_map(lab.get("narrowing"))) or NOT_RECORDED)


def _claim_gate(sections: Mapping[str, Any]) -> dict[str, Any]:
    meta = _map(_map(sections.get("analysis_metadata")).get("claim_support_gate"))
    candidates, claims = [], []
    for item in _list(sections.get("hidden_insights")):
        if isinstance(item, Mapping):
            candidates.append(_row(item.get("insight_id"), item.get("text"), "passed", item.get("finding_tier")))
    for item in _list(sections.get("strategic_findings")):
        if isinstance(item, Mapping) and _clean(item.get("finding_tier")) != "hidden_insight":
            candidates.append(_row(item.get("insight_id"), item.get("text"), "downgraded",
                                   "finding_tier:" + _clean(item.get("finding_tier"))))
    for item in _list(sections.get("promising_hypotheses")):
        if isinstance(item, Mapping):
            candidates.append(_row(item.get("insight_id"), item.get("text"), "rejected",
                                   "; ".join(_clean(r) for r in _list(item.get("failure_reasons")))))
    attempts = _list(meta.get("attempts"))
    for attempt in attempts:
        for claim in _list(_map(attempt).get("failed_claims")):
            claim = _map(claim)
            claims.append(_row(claim.get("claim_id"), claim.get("claim_text"), "rejected",
                               f"{_clean(claim.get('epistemic_type'))}:{_clean(claim.get('grounding_status'))}"))
    return _stage(candidates, claims=claims, attempts=len(attempts) if "attempts" in meta else NOT_RECORDED,
                  abstention_reasons=[_clean(r) for r in _list(meta.get("abstention_reasons"))])


def _briefing(sections: Mapping[str, Any]) -> dict[str, Any]:
    brief = _map(sections.get("editorial_briefing"))
    candidates = []
    for n, item in enumerate(_list(brief.get("implications"))):
        if isinstance(item, Mapping):
            candidates.append(_row(f"IMP{n + 1}", item.get("text"), "passed", item.get("novelty")))
    for n, item in enumerate(_list(brief.get("expected_implications"))):
        if isinstance(item, Mapping):
            candidates.append(_row(f"EXP{n + 1}", item.get("text"), "downgraded",
                                   "widely_expected:" + ",".join(_clean(r) for r in _list(item.get("novelty_reasons")))))
    for n, item in enumerate(_list(brief.get("analogies"))):
        if isinstance(item, Mapping):
            candidates.append(_row(item.get("case_id") or f"AN{n + 1}", item.get("text"), "passed", "analogy"))
    pruned = brief.get("sentences_removed_by_check")
    return _stage(candidates, sentences_pruned=int(pruned) if isinstance(pruned, int) else NOT_RECORDED,
                  mode=_clean(brief.get("mode")) or NOT_RECORDED,
                  cases=brief.get("case_count", NOT_RECORDED),
                  expert_commentary=len(_list(brief.get("expert_commentary"))) if "expert_commentary" in brief
                  else NOT_RECORDED)


def _actor_moves(sections: Mapping[str, Any]) -> dict[str, Any]:
    moves = _map(sections.get("actor_moves"))
    candidates, claims = [], []
    for actor in _list(moves.get("actors")):
        actor = _map(actor)
        name = _clean(actor.get("actor"))
        for n, move in enumerate(_list(actor.get("moves"))):
            if isinstance(move, Mapping):
                candidates.append(_row(f"{name}:M{n + 1}", move.get("move"), "passed", move.get("basis")))
        for n, move in enumerate(_list(actor.get("expected_moves"))):
            if isinstance(move, Mapping):
                candidates.append(_row(f"{name}:E{n + 1}", move.get("move"), "downgraded", "widely_expected"))
        for n, move in enumerate(_list(actor.get("rejected"))):
            if isinstance(move, Mapping):   # the model's own best-response step set it aside
                candidates.append(_row(f"{name}:R{n + 1}", move.get("move"), "rejected",
                                       "set_aside_by_choice:" + _clean(move.get("why_not"))))
    # "removed" lists unsupported parts of an actor's analysis (crux, leverage, choice, a move):
    # claims, not whole candidates.
    for n, text in enumerate(_list(moves.get("removed"))):
        claims.append(_row(f"AM-X{n + 1}", text, "rejected", "not_supported_by_sources"))
    return _stage(candidates, claims=claims, status=_clean(moves.get("status")) or NOT_RECORDED)


def _tally(stage: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"candidates": dict(Counter(r["outcome"] for r in stage["candidates"]))}
    out["distinct_candidate_ids"] = len({r["id"] for r in stage["candidates"] if r["id"]})
    if stage["events"]:
        out["events"] = len(stage["events"])
    if stage["claims"]:
        out["failed_claims"] = len(stage["claims"])
    if stage["display"]:
        out["display"] = dict(Counter(r["outcome"] for r in stage["display"]))
    for key in ("attempts", "sentences_pruned"):
        if stage[key] != NOT_RECORDED:
            out[key] = stage[key]
    return out


def build(sections: Mapping[str, Any]) -> dict[str, Any]:
    """The funnel for one report's sections (the saved payload's "sections")."""
    stages = {
        "hypothesis_lab": _lab(sections),
        "claim_support_gate": _claim_gate(sections),
        "editorial_briefing": _briefing(sections),
        "actor_moves": _actor_moves(sections),
    }
    return {"version": VERSION, "tally": {name: _tally(stage) for name, stage in stages.items()},
            "stages": stages}


def summary(funnel: Mapping[str, Any]) -> dict[str, Any]:
    """Compact form for the run log: the per-stage tally plus rejected candidate ids."""
    out: dict[str, Any] = {"version": funnel.get("version")}
    for name, stage in _map(funnel.get("stages")).items():
        out[name] = dict(_map(_map(funnel.get("tally")).get(name)))
        if stage.get("display_selection_basis"):
            out[name]["display_selection_basis"] = stage["display_selection_basis"]
        out[name]["rejected_ids"] = [r["id"] for r in stage["candidates"] if r["outcome"] == "rejected" and r["id"]][:12]
    return out


def _load_sections(db_path: Path, report_id: str = "") -> tuple[str, Mapping[str, Any]]:
    uri = db_path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        query = ("SELECT r.report_id, v.payload_json FROM reports r JOIN report_revisions v "
                 "ON v.report_revision_id = r.current_revision_id ")
        row = (db.execute(query + "WHERE r.report_id = ?", (report_id,)).fetchone() if report_id
               else db.execute(query + "ORDER BY r.created_at DESC LIMIT 1").fetchone())
    if not row:
        raise SystemExit(f"No report found: {report_id or 'latest'}")
    return row[0], _map(json.loads(row[1]).get("sections"))


def main(argv: list[str]) -> int:
    db_path = Path(__file__).resolve().parents[1] / "data" / "reports.sqlite3"
    report_id, sections = _load_sections(db_path, argv[0] if argv else "")
    funnel = build(sections)
    print(report_id)
    for name, stage in funnel["stages"].items():
        print(f"\n== {name}  {json.dumps(funnel['tally'][name])}")
        extra = {k: v for k, v in stage.items()
                 if k not in {"candidates", "events", "claims", "display", "attempts", "sentences_pruned"}}
        if extra:
            print("   " + json.dumps(extra, default=str)[:400])
        for unit in ("candidates", "events", "claims", "display"):
            for r in stage[unit]:
                print(f"  [{unit[:-1]}] {r['outcome']:<10} {r['id']:<24} {r['text'][:80]}"
                      + (f"\n               reason: {r['reason']}" if r["reason"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
