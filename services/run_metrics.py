"""Run metrics: one measured record per run (v13.13, 20 Nov 2026).

Owner, 20 Nov 2026: "add AI evaluation metrics or other metrics to the logs or dashboard, so each
run can be measured". Computed at the end of every run (no model calls), saved in the report's
analysis metadata as ``run_metrics``, logged as one ``run_metrics=`` line, shown on the dashboard
and printed by ``python -m tools.run_quality``. Every metric is a count or a ratio of counts from
this run, so two runs can be compared directly.

Groups and where each idea comes from
-------------------------------------
retrieval
    sources, events kept by the topic check and the share it removed, Exa pages and searches
    (and units spent), research pages reviewed and kept, past cases found.
insight_funnel
    candidates checked, insights released, release rate, rejection reasons by type, repeats of
    earlier reports caught, lab theses generated and verified. (A funnel shows WHERE candidates are
    lost - the run_quality gates name the target.)
grounding  (the AI-evaluation part)
    * claim support rate: claims of released insights that the cited sources support (supported or
      inference_supported) over all their checked claims - the "faithfulness" measure of RAGAS
      (Es et al. 2023) and the citation-precision idea of ALCE (Gao et al. 2023);
    * citation coverage: share of those claims that cite at least one source (ALCE citation recall);
    * independent publishers per released insight (corroboration; ICD 203 sourcing standard);
    * insights with a falsifier or watch signal (forecastability; Tetlock's checkable forecasts).
novelty
    released insights that repeat an earlier report (should be 0), repeats caught before checking.
judge
    the insight judge's status and top score when it ran.
cost
    Gemini calls (attempts) by stage, calls per released insight, run time.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

VERSION = "run-metrics-v1"
BACKED = ("supported", "inference_supported")


def _m(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _ratio(part: float, whole: float) -> float | None:
    return round(part / whole, 3) if whole else None


def _reason_type(reason: str) -> str:
    reason = str(reason or "")
    for prefix in ("overreach_unsourced_word", "overreach_unsourced_number", "source_backed_not_original",
                   "after_display_check", "no_release_route"):
        if reason.startswith(prefix):
            return prefix
    return reason.split(":", 1)[0] or "unknown"


def compute(*, state: Mapping[str, Any], report: Mapping[str, Any], events: Sequence[Mapping[str, Any]] = (),
            evidence: Sequence[Mapping[str, Any]] = (), released: bool = True, exa_usage: Mapping[str, Any] | None = None,
            calls_by_stage: Mapping[str, Mapping[str, Any]] | None = None, elapsed_seconds: float | None = None,
            evaluation: Mapping[str, Any] | None = None) -> dict[str, Any]:
    gate = _m(state.get("claim_support_gate"))
    early = _m(state.get("claim_support_gate_early_exploration"))
    attempts = [a for a in list(gate.get("attempts") or []) + list(early.get("attempts") or []) if isinstance(a, Mapping)]
    insights = [i for i in (report.get("hidden_insights") or []) if isinstance(i, Mapping)] if released else []
    grounding = _m(report.get("grounding_by_insight"))
    by_evidence = {str(r.get("evidence_id") or "").upper(): r for r in evidence or [] if isinstance(r, Mapping)}

    # retrieval
    admission = _m(state.get("strategic_event_admission"))
    considered = admission.get("considered_event_count")
    admitted = admission.get("admitted_event_count")
    gap = [g for g in (state.get("gap_research_summary") or []) if isinstance(g, Mapping)]
    reviewed = kept = cases = 0
    for receipt in gap:
        for source in receipt.get("source_reviews") or []:
            if not isinstance(source, Mapping):
                continue
            if source.get("outcome") == "reviewed":
                reviewed += 1
                kept += int(source.get("accepted") or 0) > 0
                cases += int(source.get("precedents") or 0)
    exa = dict(exa_usage or {})
    retrieval = {
        "sources": len({str(r.get("source_id") or "") for r in evidence or [] if isinstance(r, Mapping)} - {""}),
        "events_admitted": admitted, "events_analysed": considered,
        "topic_filter_removed_share": _ratio((admitted or 0) - (considered or 0), admitted or 0)
        if isinstance(admitted, int) and isinstance(considered, int) else None,
        "research_pages_reviewed": reviewed, "research_pages_kept": kept, "past_cases_found": cases,
        "exa_units_used": exa.get("used"), "exa_calls_failed": exa.get("failed"),
        "exa_calls_refused_by_budget": exa.get("refused"),
        "exa_pages_read": exa.get("pages"), "exa_searches": exa.get("searches"),
    }

    # insight funnel
    reasons = Counter(_reason_type(r) for a in attempts if not a.get("passed")
                      for r in (a.get("reasons") or ["unknown"]))
    lab = _m(state.get("strategic_hypothesis_lab"))
    lab_funnel = _m(lab.get("funnel"))
    funnel = {
        "candidates_checked": len(attempts),
        "insights_released": len(insights),
        "release_rate": _ratio(len(insights), len(attempts)),
        "rejections_by_reason": dict(reasons.most_common()),
        "repeats_of_earlier_caught": reasons.get("already_shown_in_earlier_report", 0)
        + int(lab_funnel.get("repeats_of_earlier_reports") or 0),
        "lab_theses_generated": lab_funnel.get("generated_rows"),
        "lab_theses_verified": len(_m(report.get("strategic_hypotheses")).get("verified") or []),
    }

    # grounding (AI evaluation)
    total = backed = cited = 0
    publishers_per_insight: list[int] = []
    for insight in insights:
        key = str(insight.get("insight_revision_id") or "")
        claims = [c for c in (_m(grounding.get(key)).get("claims") or []) if isinstance(c, Mapping)]
        publishers: set[str] = set()
        for claim in claims:
            total += 1
            backed += str(claim.get("grounding_status") or "") in BACKED
            ids = list(claim.get("supporting_evidence_ids") or claim.get("cited_evidence_ids") or [])
            cited += bool(ids)
            for eid in ids:
                record = by_evidence.get(str(eid).upper()) or {}
                host = urlparse(str(record.get("source_url") or "")).netloc.replace("www.", "")
                if host:
                    publishers.add(host)
        publishers_per_insight.append(len(publishers))
    checkable = sum(1 for i in insights if any(str(i.get(k) or _m(i.get("forecast_contract")).get(k) or "").strip()
                                               for k in ("falsifier", "watch_signal", "question")))
    grounding_metrics = {
        "claims_checked": total,
        "claim_support_rate": _ratio(backed, total),
        "citation_coverage": _ratio(cited, total),
        "independent_publishers_per_insight": (round(sum(publishers_per_insight) / len(publishers_per_insight), 2)
                                               if publishers_per_insight else None),
        "insights_with_falsifier_or_watch_signal": _ratio(checkable, len(insights)),
    }

    # judge
    evaluation = _m(evaluation or report.get("insight_evaluation"))
    items = [i for i in (evaluation.get("items") or []) if isinstance(i, Mapping)]
    judge = {"status": evaluation.get("status") or ("ranked" if items else "not_run"),
             "items_scored": len(items),
             "top_score": max((i.get("score") for i in items if isinstance(i.get("score"), (int, float))), default=None)}

    # cost
    stages = {name: int(_m(row).get("attempts") or 0) for name, row in (calls_by_stage or {}).items()}
    calls = sum(stages.values()) if stages else None
    cost = {"gemini_calls": calls, "gemini_calls_by_stage": stages or None,
            "calls_per_released_insight": _ratio(calls or 0, len(insights)) if calls else None,
            "run_seconds": round(float(elapsed_seconds), 1) if elapsed_seconds is not None else None}

    # v13.23 (22 Nov 2026): the run checks itself against the project objective - original insights a
    # reader would rate at par with publications that sell original analysis. Counted: insights that are
    # not restatements of their sources, passed the editor's bar (services/par_check.py), and carry a
    # call checkable within 12 weeks (services/variant_view.py). The log says MET or NOT MET.
    def _field(item: Mapping[str, Any], key: str) -> Any:
        return item.get(key) if item.get(key) is not None else _m(item.get("metadata")).get(key)
    originals = [i for i in insights if not _m(_field(i, "source_backed"))]
    # v13.27: an insight the editor's bar did NOT judge (no outside model) does not count toward MET.
    at_bar = [i for i in originals if _m(_field(i, "par_check")).get("passed") is True
              and _m(_field(i, "par_check")).get("available") is not False]
    try:
        from services import variant_view
        checkable_soon = [i for i in at_bar if not [p for p in variant_view.structure_problems(i)
                                                    if p.startswith(("call_horizon", "no_dated_call"))]]
    except Exception:
        checkable_soon = []
    # v13.27 (research report of 22 Nov, Q5.6): three ESTIMATED numbers, never "met" - the editor's-bar
    # rubric, novelty against the consensus, and the win probability against the user's approved example.
    judged = [i for i in originals if _m(_field(i, "par_check")) and _m(_field(i, "par_check")).get("available") is not False]
    novel = [i for i in originals if _m(_field(i, "variant_novelty")).get("checked")]
    duels = [float(_m(_m(_field(i, "par_check")).get("head_to_head")).get("win_probability"))
             for i in originals if isinstance(_m(_m(_field(i, "par_check")).get("head_to_head")).get("win_probability"), (int, float))]
    objective = {"original_insights": len(originals), "at_editors_bar": len(at_bar),
                 "at_bar_with_call_within_12_weeks": len(checkable_soon),
                 "background_findings": len(insights) - len(originals),
                 "rubric_passed": f"{len(at_bar)}/{len(judged)}" if judged else "not judged",
                 "new_vs_consensus": (f"{sum(1 for i in novel if _m(_field(i, 'variant_novelty')).get('new'))}/{len(novel)}"
                                      if novel else "not judged"),
                 "win_probability_vs_approved_example": round(sum(duels) / len(duels), 2) if duels else None,
                 "met": len(checkable_soon) >= 1}

    return {"version": VERSION, "released": bool(released), "retrieval": retrieval, "insight_funnel": funnel,
            "grounding": grounding_metrics, "novelty": {"repeats_caught": funnel["repeats_of_earlier_caught"]},
            "judge": judge, "cost": cost, "objective": objective}


def summary_lines(metrics: Mapping[str, Any]) -> list[str]:
    """Plain lines for the log, the dashboard and tools/run_quality."""
    r, f, g, j, c = (_m(metrics.get(k)) for k in ("retrieval", "insight_funnel", "grounding", "judge", "cost"))

    def pct(value: Any) -> str:
        return f"{value:.0%}" if isinstance(value, (int, float)) else "n/a"
    o = _m(metrics.get("objective"))
    objective_line = (f"Objective estimate (not proof - blind comparisons and resolved calls are): "
                      f"editor's bar {o.get('rubric_passed', 'not judged')}, new vs consensus "
                      f"{o.get('new_vs_consensus', 'not judged')}, win chance vs your approved example "
                      f"{o.get('win_probability_vs_approved_example') if o.get('win_probability_vs_approved_example') is not None else 'n/a'}; "
                      f"{'MET' if o.get('met') else 'NOT MET'}: {o.get('at_bar_with_call_within_12_weeks', 0)} "
                      f"original insight(s) at the editor's bar with a call checkable within 12 weeks "
                      f"({o.get('original_insights', 0)} original, {o.get('at_editors_bar', 0)} at the bar, "
                      f"{o.get('background_findings', 0)} background findings)") if o else ""
    return ([objective_line] if objective_line else []) + [
        f"Retrieval: {r.get('sources')} sources, {r.get('events_analysed')} of {r.get('events_admitted')} events analysed; "
        f"{r.get('research_pages_kept')} of {r.get('research_pages_reviewed')} research pages kept; "
        f"{r.get('past_cases_found')} past cases; Exa {r.get('exa_pages_read') or 0} pages, "
        f"{r.get('exa_searches') or 0} searches, {r.get('exa_units_used')} units "
        f"({r.get('exa_calls_failed') or 0} failed, {r.get('exa_calls_refused_by_budget') or 0} refused by budget)",
        f"Insights: {f.get('insights_released')} released of {f.get('candidates_checked')} checked "
        f"(rate {pct(f.get('release_rate'))}); repeats of earlier reports caught: {f.get('repeats_of_earlier_caught')}; "
        f"lab {f.get('lab_theses_verified')} verified of {f.get('lab_theses_generated')} generated",
        f"Rejections: " + (", ".join(f"{k} {v}" for k, v in _m(f.get("rejections_by_reason")).items()) or "none"),
        f"Grounding: claim support {pct(g.get('claim_support_rate'))}, citation coverage {pct(g.get('citation_coverage'))} "
        f"over {g.get('claims_checked')} claims; {g.get('independent_publishers_per_insight')} publishers per insight; "
        f"checkable {pct(g.get('insights_with_falsifier_or_watch_signal'))}",
        f"Judge: {j.get('status')}, {j.get('items_scored')} scored, top {j.get('top_score')}",
        f"Cost: {c.get('gemini_calls')} Gemini calls, {c.get('calls_per_released_insight')} per released insight, "
        f"{c.get('run_seconds')} s",
    ]
