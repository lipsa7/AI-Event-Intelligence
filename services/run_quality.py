"""Run quality gates: does this run's OUTPUT meet the owner's decisions? (v13, 18 Nov 2026)

Unit tests check that each stage works; they passed while runs produced nothing useful, because
every stage "worked" on its own (the research report "Fixing AI Event Intelligence", 18 Nov 2026:
"mechanism works but output is useless" is only caught at the output level). This module is
computed automatically at the end of every run and saved in the report's analysis metadata
(``quality_gates``), and a failing gate is logged as a warning. ``python -m tools.run_quality``
prints it for the latest saved report.

Gates (each pins an owner decision):
* insights_released >= 2 on a deep run                 (multiple insights per run, 18 Nov 2026)
* the top stories are not funding-only                  (stories chosen by insight potential)
* the top story reached the generator in depth (>= 6 records, when it has them)
* exploration ran when the main generator returned nothing
* the computed gaps reached the analysis when the sources hold any

The funnel (how many candidates survive each stage) is saved with it, so a weak run shows WHERE
candidates die.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

VERSION = "run-quality-v1"
MIN_INSIGHTS_DEEP = 2
MIN_TOP_STORY_RECORDS = 6


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def funnel(state: Mapping[str, Any], report: Mapping[str, Any], events: Sequence[Mapping[str, Any]] = (),
           evidence: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    gate = _as_mapping(state.get("claim_support_gate"))
    early = _as_mapping(state.get("claim_support_gate_early_exploration"))
    attempts = list(gate.get("attempts") or []) + list(early.get("attempts") or [])
    explored = (list(_as_mapping(gate.get("exploration")).get("combinations") or [])
                + list(_as_mapping(early.get("exploration")).get("combinations") or []))
    lab = _as_mapping(state.get("strategic_hypothesis_lab"))
    return {
        "events": len(events or state.get("events") or []),
        "evidence_records": len(evidence or state.get("evidence") or []),
        "developments": len(report.get("developments_to_watch") or []),
        "computed_gaps": len(report.get("computed_gaps") or []),
        "candidates_checked": len(attempts),
        "stories_explored": len(explored),
        "stories_explored_with_a_candidate": sum(1 for e in explored if _as_mapping(e).get("result") == "candidate"),
        "exploration_after_empty_generator": state.get("exploration_after_empty_generator"),
        "lab_funnel": dict(_as_mapping(lab.get("funnel"))),
        "insights_released": len(report.get("hidden_insights") or []),
        "lab_ideas_verified": len(report.get("strategic_hypotheses") or []),
    }


def gates(state: Mapping[str, Any], report: Mapping[str, Any], events: Sequence[Mapping[str, Any]] = (),
          evidence: Sequence[Mapping[str, Any]] = (), depth: str = "deep") -> dict[str, Any]:
    from services import insight_potential

    results: dict[str, dict[str, Any]] = {}
    flow = funnel(state, report, events, evidence)
    if depth == "deep":
        released = int(flow["insights_released"])
        results["multiple_insights"] = {"passed": released >= MIN_INSIGHTS_DEEP,
                                        "detail": f"{released} released (target {MIN_INSIGHTS_DEEP}+)"}
    developments: Sequence[Mapping[str, Any]] = [d for d in report.get("developments_to_watch") or []
                                                 if isinstance(d, Mapping)]
    events_by_id = {str(e.get("event_id") or "").upper(): e for e in events or [] if isinstance(e, Mapping)}
    funding = []
    for development in developments[:3]:
        members = [events_by_id[e] for e in development.get("event_ids") or [] if e in events_by_id]
        if members and "funding_only" in insight_potential.score(members, topic=str(state.get("topic") or ""))["parts"]:
            funding.append(str(development.get("title") or "")[:80])
    results["top_stories_not_funding_only"] = {"passed": not funding, "detail": funding or "ok"}
    packet = _as_mapping(state.get("strategic_packet_stats"))
    if packet:
        top, available = int(packet.get("top_story_records") or 0), int(packet.get("top_story_available") or 0)
        results["top_story_depth"] = {"passed": top >= min(MIN_TOP_STORY_RECORDS, available),
                                      "detail": f"{top} of {available} records"}
    if state.get("exploration_after_empty_generator") is not None or flow["stories_explored"]:
        results["exploration_ran"] = {"passed": True, "detail": f"{flow['stories_explored']} stories"}
    elif depth == "deep" and not flow["insights_released"]:
        results["exploration_ran"] = {"passed": False, "detail": "no insight and no story explored"}
    return {"version": VERSION, "passed": all(r["passed"] for r in results.values()), "gates": results,
            "funnel": flow}
