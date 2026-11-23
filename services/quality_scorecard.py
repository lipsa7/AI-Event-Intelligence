"""Quality scorecard: is insight quality improving from run to run? (v13.31)

The owner: "add metrics so as to see how insight quality is improving". Both research reports of 22 Nov:
measure every change on a fixed basis before trusting it (Husain; Yan; Shankar et al., "Who Validates the
Validators?"), and score the code-checkable items of an "at par" rubric derived from the publications that
sell original analysis (items marked + in the analyst report), leaving "at par" itself to blind human
comparison (tools/blind_pairs.py) because LLM judges do not judge novelty reliably (Si et al.: best judge
53.3% against 56.1% between expert reviewers).

Per candidate (each item 1 = met, 0 = not met, None = not checked):
  grounded_quotes   every mechanism step quotes words found in its cited record          (link_check)
  linked            consecutive steps share an entity, instrument or citation            (anti-stitching)
  mechanism         at least two cited mechanism steps                                     (Levine, Lawfare)
  quantified        a figure computed by code or taken from a cited record                (Lex)
  alternatives      a rival hypothesis and a load-bearing assumption                       (ICD 203)
  forecast          a dated forecast that is uncertain and diagnostic, not routine         (ICD 203, P2)
  concise           thesis <= 25 words and the insight <= 300 words                        (Huang et al. 2014)
  not_restated      not close in meaning to the consensus, published analysis or earlier insights
  uncontradicted    no claim contradicted by the story's records
Per run: candidates, mean rubric score, share meeting every item, original insights released, drafts,
the most common failing items, computed facts and consensus sources per story, past cases found.
History is read from saved reports (nothing is written during a run).
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

VERSION = "quality-scorecard-v1"
ITEMS = ("grounded_quotes", "linked", "mechanism", "quantified", "alternatives", "forecast", "concise",
         "not_restated", "uncontradicted")
THESIS_WORDS = 25
INSIGHT_WORDS = 300
# problem codes -> the rubric item they fail
CODE_ITEM = (
    ("link_unquoted", "grounded_quotes"), ("link_quote_not_in_source", "grounded_quotes"),
    ("link_not_connected", "linked"), ("overlooked_fact_off_story", "linked"), ("number_off_story", "linked"),
    ("mechanism_chain_too_short", "mechanism"), ("overlooked_fact_not_in_chain", "mechanism"),
    ("actor_not_in_chain", "mechanism"), ("no_number", "quantified"), ("number_not_in_chain", "quantified"),
    ("number_decorative", "quantified"), ("no_rival_hypothesis", "alternatives"),
    ("no_load_bearing_assumption", "alternatives"), ("forecast_not_uncertain", "forecast"),
    ("forecast_not_diagnostic", "forecast"), ("routine_call", "forecast"), ("no_dated_call", "forecast"),
    ("call_horizon", "forecast"), ("forecast_scheduled_event", "forecast"), ("thesis_", "concise"),
    ("case_not_in_library", "grounded_quotes"), ("no_disanalogy", "alternatives"), ("near_copy_of_existing", "not_restated"),
    ("too_close_to_existing", "not_restated"), ("restates_consensus", "not_restated"),
    ("consensus_not_quoted", "not_restated"), ("contradicted_by_source", "uncontradicted"),
)


def _m(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def item_of(code: str) -> str | None:
    code = _clean(code)
    for prefix, item in CODE_ITEM:
        if code.startswith(prefix):
            return item
    return None


def score_problems(problems: Iterable[str], checked: Mapping[str, bool] | None = None,
                   text: str = "") -> dict[str, Any]:
    """Rubric for one candidate from its problem codes. `checked` marks items that were actually tested;
    an item not tested is None, not a pass."""
    failed = {item_of(p) for p in problems or []} - {None}
    if "no_variant_perception" in set(problems or []):
        failed |= {"grounded_quotes", "linked", "mechanism", "alternatives"}
    checked = dict(checked or {})
    row: dict[str, Any] = {}
    for item in ITEMS:
        if item in failed:
            row[item] = 0
        elif checked.get(item, item not in {"forecast", "not_restated", "uncontradicted"}):
            row[item] = 1
        else:
            row[item] = None
    words = len(_clean(text).split())
    if text and words > INSIGHT_WORDS:
        row["concise"] = 0
    scored = [v for v in row.values() if v is not None]
    row["score"] = round(sum(scored) / len(scored), 3) if scored else None
    row["all_met"] = bool(scored) and all(scored) and None not in row.values()
    return row


def score_candidate(meta: Mapping[str, Any], problems: Sequence[str], text: str) -> dict[str, Any]:
    novelty = _m(meta.get("semantic_novelty"))
    diag = _m(meta.get("variant_diagnosticity"))
    contradiction = _m(meta.get("contradiction_check"))
    checked = {"forecast": bool(diag.get("checked")), "not_restated": bool(novelty.get("checked")),
               "uncontradicted": bool(contradiction.get("checked"))}
    return score_problems(problems, checked, text)


def run_scorecard(gate_log: Mapping[str, Any], report: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Aggregate over every candidate the run's story slots produced (kept and drafts)."""
    combos = [_m(c) for c in _m(_m(gate_log).get("exploration")).get("combinations") or []]
    rows = []
    for combo in combos:
        for item in combo.get("kept") or []:
            if _m(item).get("rubric"):
                rows.append(_m(item)["rubric"])
        for item in combo.get("variant_dropped") or []:
            if _m(item).get("rubric"):
                rows.append(_m(item)["rubric"])
    report = _m(report)
    released = [i for i in report.get("hidden_insights") or [] if isinstance(i, Mapping)]
    from services import proof_line
    originals = proof_line.split(released)[0] if released else []
    failing = Counter(item for r in rows for item in ITEMS if r.get(item) == 0)
    scores = [r["score"] for r in rows if isinstance(r.get("score"), (int, float))]
    return {
        "version": VERSION,
        "candidates": len(rows),
        "mean_rubric_score": round(sum(scores) / len(scores), 3) if scores else None,
        "share_meeting_every_item": round(sum(1 for r in rows if r.get("all_met")) / len(rows), 3) if rows else None,
        "item_pass_rates": {item: (round(sum(1 for r in rows if r.get(item) == 1) / n, 2) if
                                   (n := sum(1 for r in rows if r.get(item) is not None)) else None) for item in ITEMS},
        "most_failed_items": dict(failing.most_common(4)),
        "original_insights_released": len(originals),
        # every candidate that reached the claim-support check (main pass and story slots), and how many passed
        "claim_check": f"{sum(1 for a in _m(gate_log).get('attempts') or [] if _m(a).get('passed'))}/"
                       f"{len(_m(gate_log).get('attempts') or [])}",
        "stories": len(combos),
        "computed_facts_per_story": round(sum(int(c.get("computed_facts") or 0) for c in combos) / len(combos), 1) if combos else None,
        "consensus_sources_per_story": round(sum(int(c.get("consensus_sources") or 0) for c in combos) / len(combos), 1) if combos else None,
        "past_cases_per_story": round(sum(int(c.get("past_cases") or 0) for c in combos) / len(combos), 1) if combos else None,
    }


def lines(card: Mapping[str, Any]) -> list[str]:
    if not card or not card.get("candidates"):
        return ["Quality scorecard: no scored candidates in this run."]
    rates = ", ".join(f"{k} {v:.0%}" for k, v in _m(card.get("item_pass_rates")).items() if v is not None)
    return [f"Quality: {card['candidates']} candidates, mean rubric {card['mean_rubric_score']}, "
            f"{card['share_meeting_every_item']:.0%} meet every item, {card['original_insights_released']} original "
            f"insight(s) released, claim check passed {card.get('claim_check', 'n/a')}",
            f"Quality items passed: {rates}",
            f"Quality inputs per story: {card.get('computed_facts_per_story')} computed facts, "
            f"{card.get('consensus_sources_per_story')} consensus sources, {card.get('past_cases_per_story')} past cases; "
            f"most failed: " + (", ".join(f"{k} {v}" for k, v in _m(card.get("most_failed_items")).items()) or "none")]


def history(db_path: Path | None = None, last: int = 20) -> list[dict[str, Any]]:
    """Scorecards of the last saved reports, oldest first (read-only)."""
    path = db_path or Path(__file__).resolve().parent.parent / "data" / "reports.sqlite3"
    if not path.exists():
        return []
    uri = path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        rows = db.execute("SELECT r.report_id, r.created_at, v.payload_json FROM reports r JOIN report_revisions v "
                          "ON v.report_revision_id = r.current_revision_id ORDER BY r.created_at DESC LIMIT ?",
                          (last,)).fetchall()
    out = []
    for report_id, created, payload in reversed(rows):
        try:
            sections = json.loads(payload).get("sections") or {}
        except (TypeError, ValueError):
            continue
        meta = _m(sections.get("analysis_metadata"))
        card = _m(_m(meta.get("run_metrics")).get("quality_scorecard"))
        if not card:   # reports from before v13.31: score what they recorded
            card = run_scorecard(_m(meta.get("claim_support_gate")), sections)
        out.append({"report_id": report_id, "created_at": created, **card})
    return out


def trend_lines(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    out = []
    previous = None
    for row in rows:
        mean = row.get("mean_rubric_score")
        delta = ("" if previous is None or mean is None or previous.get("mean_rubric_score") is None
                 else f" ({mean - previous['mean_rubric_score']:+.3f})")
        out.append(f"{str(row.get('created_at'))[:16]}  {row.get('report_id')}: {row.get('candidates', 0)} candidates, "
                   f"rubric {mean}{delta}, every item {row.get('share_meeting_every_item')}, "
                   f"claim check passed {row.get('claim_check', 'n/a')}, released {row.get('original_insights_released', 0)}")
        previous = row
    return out
