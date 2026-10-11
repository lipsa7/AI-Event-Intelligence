"""Structural non-obviousness test for hidden insights (v6.20).

Why structural, not a score: asking an LLM "how novel / non-obvious is this?"
agrees poorly with experts (a 2026 study of LLM-as-judge novelty ratings found
roughly 22-40% agreement, arXiv 2606.12071). Entailment ("does text A state
claim B?") is a task LLM checkers do reliably, and it is what the existing
GroundingService already does. So each test below is an entailment or a
support check, never a novelty rating.

A hidden insight is released as such only if at least one of its inferred
conclusion sentences (grounding status "inference_supported") passes ALL of:

  1. cross-development span: its supporting records come from at least two
     different underlying developments (two reports of one story count once,
     via the canonical UnderlyingEventResolver IDs);
  2. not obvious from the headlines: a plain takeaway written from the
     headlines of those developments alone does not state it;
  3. no single development suffices: re-checked against the records of any
     one development (plus cited background records), it is NOT supported.
     (This is the leave-all-but-one variant of an ablation test: if one
     development alone already supports the conclusion, it is not a
     cross-event insight.)

v2 (v6.21.8) - the CONSTRAINT route (consensus vs constraint, after Papic's
constraints-over-preferences method and the intelligence "so what" test):
a conclusion resting on ONE development also qualifies when its support
includes a sourced constraint record found by gap research (a rule, deadline,
dependency, number or past case) and it passes tests 2 and 3, where test 3
removes the constraint: if the development's own records already support the
conclusion without it, the constraint added nothing and the test fails.
(The strategic prompt has always allowed single-actor forensic / constraint
insights; v1 rejected all of them - replay RUN-7CD0B58AA15D4B5E83571188.)

v3 (v6.22) - test 2b: not already said by PUBLISHED EXPERT ANALYSIS of these
developments (gap research's "commentary" records). The headline reader was
too low a bar: in replay RUN-7CD0B58AA15D4B5E83571188 the candidate about the
reporting rule was already in trade-press and research-group commentary.

Fail closed: a check that cannot run (model error, no time) fails the
candidate, which is then shown as a verified connection, not dropped.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

VERSION = "non-obviousness-v4-plain-meaning"
CONSTRAINT_GAPS = frozenset({"policy", "dependency", "numbers", "precedent", "pattern"})  # v6.23: + computed pattern
SUFFICIENT = frozenset({"supported", "inference_supported"})
MAX_CONCLUSIONS = 2      # inferred conclusion sentences tested per candidate
MAX_DEVELOPMENTS = 3     # single-development re-checks per conclusion sentence
MAX_HEADLINES = 8
OBVIOUS_EVIDENCE_ID = "OBVIOUS-SUMMARY-1"

BRIEF = {
    "conclusion_single_development": (
        "[non-obvious] The conclusion is backed by one development only (several reports of one story "
        "count once). Derive a conclusion that needs at least two different developments, OR one that "
        "follows from a cited rule, deadline, dependency, number or past case (a CONTEXT record) applied "
        "to the development - what that constraint forces, for whom, that headline readers do not expect."),
    "plain_meaning_restates_sources": (
        "[plain meaning] Said plainly, the conclusion is something its own sources already state; the wording made it "
        "sound deeper. State a leverage-and-incentive judgment the sources do not state: who holds what, who needs "
        "it, what the cited rules or money make them want, and what they will therefore do."),
    "conclusion_already_in_expert_commentary": (
        "[beyond the experts] Published expert analysis of these developments (the records marked commentary) "
        "already says this. Take what the experts say as the starting point and derive what follows from it "
        "that they have not said, or show with cited evidence where they are wrong."),
    "conclusion_does_not_need_the_constraint": (
        "[non-obvious] The development's own reports already support this conclusion without the cited "
        "rule or number. State what the constraint FORCES that the reports alone do not show."),
    "conclusion_obvious_from_headlines": (
        "[non-obvious] A plain reading of the headlines already says this. Derive what follows from "
        "combining the developments that a headline reader would not conclude."),
    "conclusion_follows_from_one_development": (
        "[non-obvious] One development alone already supports this conclusion. State what only the "
        "combination of developments shows."),
}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _status(claim: Mapping[str, Any]) -> str:
    return _clean(claim.get("grounding_status") or claim.get("status")).casefold()


def _ids(value: Any) -> list[str]:
    return [_clean(x).upper() for x in (value or []) if _clean(x)]


def development_map(evidence: Sequence[Mapping[str, Any]],
                    events: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """{evidence_id: underlying development id}; '' for background records."""
    underlying = {}
    for event in events or []:
        if isinstance(event, Mapping) and _clean(event.get("event_id")):
            eid = _clean(event.get("event_id")).upper()
            underlying[eid] = _clean(event.get("underlying_event_id")).upper() or eid
    out = {}
    for record in evidence or []:
        if not isinstance(record, Mapping) or not _clean(record.get("evidence_id")):
            continue
        event_id = _clean(record.get("event_id")).upper()
        out[_clean(record.get("evidence_id")).upper()] = underlying.get(event_id, event_id) if event_id else ""
    return out


NON_CONSTRAINT_GAPS = frozenset({"commentary", "commentary_unrated", "reported"})
MAX_COMMENTARY_RECORDS = 16


def is_constraint(record: Any) -> bool:
    """A gap-research context record: a rule, dependency, number or past case, not a news event.
    Expert commentary (an opinion) and facts reported on commentary pages are not constraints."""
    if not isinstance(record, Mapping) or _clean(record.get("event_id")):
        return False
    gap = _clean(record.get("gap_type")).casefold()
    if gap in NON_CONSTRAINT_GAPS:
        return False
    return bool(record.get("historical_case") or gap in CONSTRAINT_GAPS
                or (record.get("analytical_context_only") and not gap))


def commentary_records(evidence: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """v3: published expert analysis found by gap research (gap_type "commentary")."""
    return [r for r in evidence or [] if isinstance(r, Mapping) and _clean(r.get("evidence_id"))
            and _clean(r.get("gap_type")).casefold() == "commentary"][:MAX_COMMENTARY_RECORDS]


def inferred_conclusions(grounding: Any) -> list[Mapping[str, Any]]:
    claims = grounding.get("claims") if isinstance(grounding, Mapping) else None
    return [c for c in claims or [] if isinstance(c, Mapping)
            and _clean(c.get("epistemic_type")).casefold() == "hidden_insight"
            and _status(c) == "inference_supported"]


def headlines(developments: Sequence[str], events: Sequence[Mapping[str, Any]]) -> list[str]:
    wanted = set(developments)
    seen, out = set(), []
    for event in events or []:
        if not isinstance(event, Mapping):
            continue
        dev = _clean(event.get("underlying_event_id")).upper() or _clean(event.get("event_id")).upper()
        title = _clean(event.get("title") or event.get("summary"))
        if dev in wanted and dev not in seen and title:
            seen.add(dev)
            out.append(title[:240])
    return out[:MAX_HEADLINES]


def obvious_prompt(titles: Sequence[str]) -> str:
    lines = "\n".join(f"- {t}" for t in titles)
    return (
        "These are news headlines. Write the plain takeaways an ordinary reader would draw from reading "
        "these headlines together: what happened, and the conclusions most readers would immediately reach. "
        "Do not add specialist analysis.\n\n"
        f"Headlines:\n{lines}\n\n"
        'Return JSON: {"takeaways": ["sentence", ...]} with 3 to 6 sentences.'
    )


def plain_prompt(text: str) -> str:
    return ("Rewrite this claim as ONE plain sentence a smart 15-year-old would understand. Keep only what it "
            "actually asserts: no adjectives, no jargon, no hedging, no rhetoric. If it only says two things exist "
            "or coincide, say exactly that.\n\n"
            f"Claim: {_clean(text)}\n\n" 'Return JSON: {"plain": "sentence"}')


def parse_plain(raw: Any) -> str:
    data = raw
    if isinstance(raw, str):
        match = re.search(r"\{.*\}", raw, flags=re.S)
        try:
            data = json.loads(match.group(0)) if match else {}
        except ValueError:
            data = {}
    if isinstance(data, Mapping) and isinstance(data.get("response"), (str, Mapping)):
        return parse_plain(data["response"])
    return _clean(data.get("plain")) if isinstance(data, Mapping) else ""


def parse_takeaways(raw: Any) -> list[str]:
    data = raw
    if isinstance(raw, str):
        match = re.search(r"\{.*\}", raw, flags=re.S)
        try:
            data = json.loads(match.group(0)) if match else {}
        except ValueError:
            data = {}
    if isinstance(data, Mapping) and isinstance(data.get("response"), (str, Mapping)):
        return parse_takeaways(data["response"])
    items = data.get("takeaways") if isinstance(data, Mapping) else None
    return [_clean(x) for x in items or [] if _clean(x)][:6]


def check(*, grounding: Any, evidence: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]],
          ground: Callable[[list[dict[str, Any]], list[Mapping[str, Any]]], Any],
          ask: Callable[[str], Any], budget_ok: Callable[[], bool] = lambda: True) -> dict[str, Any]:
    """Run the three tests. ground(claims, evidence_records) -> GroundingService.ground_claims result."""
    dev_of = development_map(evidence, events)
    by_id = {_clean(r.get("evidence_id")).upper(): r for r in evidence or [] if isinstance(r, Mapping)}
    result: dict[str, Any] = {"version": VERSION, "passed": False, "reasons": [], "claims": []}
    conclusions = inferred_conclusions(grounding)
    if not conclusions:
        result["reasons"] = ["no_inferred_conclusion"]
        return result

    # Test 1 (free): span across >= 2 developments.
    spanning = []
    for claim in conclusions:
        support = _ids(claim.get("supporting_evidence_ids"))
        devs = Counter(dev_of.get(eid, "") for eid in support)
        devs.pop("", None)
        constraints = [eid for eid in support if not dev_of.get(eid) and is_constraint(by_id.get(eid))]
        entry = {"claim_id": _clean(claim.get("claim_id")), "claim_text": _clean(claim.get("claim_text"))[:400],
                 "developments": sorted(devs)}
        result["claims"].append(entry)
        if len(devs) >= 2:
            entry["route"] = "cross_development"
            spanning.append((claim, devs, entry))
        elif len(devs) == 1 and constraints:
            entry["route"] = "constraint"
            entry["constraint_evidence_ids"] = constraints
            spanning.append((claim, devs, entry))
        else:
            entry["failed"] = "conclusion_single_development"
    if not spanning:
        result["reasons"] = ["conclusion_single_development"]
        return result
    spanning.sort(key=lambda item: -len(item[1]))
    spanning = spanning[:MAX_CONCLUSIONS]
    if not budget_ok():
        result["reasons"] = ["non_obviousness_untested:time_budget"]
        return result

    # Test P (v4, v6.26): judge the PLAIN meaning, not the wording. Strong analyst
    # language made a thin point look deep (the user: "it's strongly worded, so
    # you picked it up"). The conclusion is rewritten as one plain sentence; the
    # plain version must not be stated outright by the conclusion's own records,
    # and tests 2 and 2b below run on the plain version.
    for claim, _, entry in spanning:
        try:
            plain = parse_plain(ask(plain_prompt(_clean(claim.get("claim_text")))))
        except Exception as error:  # fail closed
            result["reasons"] = [f"non_obviousness_untested:plain_rewrite_{type(error).__name__}"]
            return result
        if not plain:
            result["reasons"] = ["non_obviousness_untested:plain_rewrite_empty"]
            return result
        entry["plain"] = plain
    restating = []
    for i, (claim, _, entry) in enumerate(spanning):
        own = [by_id[eid] for eid in _ids(claim.get("supporting_evidence_ids")) if eid in by_id
               and not is_constraint(by_id[eid])]
        if not own:
            continue
        try:
            out = (ground([{"claim_id": f"PLAIN-{i}", "claim_text": entry["plain"],
                            "evidence_ids": [_clean(r.get("evidence_id")) for r in own],
                            "epistemic_type": "hidden_insight", "multi_evidence_required": False}], own)
                   or {}).get("claims") or []
        except Exception as error:
            result["reasons"] = [f"non_obviousness_untested:plain_check_{type(error).__name__}"]
            return result
        status = _status(out[0]) if out else ""
        entry["plain_status_against_own_sources"] = status
        if status == "supported":
            entry["failed"] = "plain_meaning_restates_sources"
            restating.append(i)
    spanning = [item for i, item in enumerate(spanning) if i not in restating]
    if not spanning:
        result["reasons"] = ["plain_meaning_restates_sources"]
        return result

    # Test 2: entailed by a plain headline-level takeaway?
    all_devs = sorted({d for _, devs, _ in spanning for d in devs})
    titles = headlines(all_devs, events)
    try:
        takeaways = parse_takeaways(ask(obvious_prompt(titles))) if titles else []
    except Exception as error:  # fail closed
        result["reasons"] = [f"non_obviousness_untested:obvious_summary_{type(error).__name__}"]
        return result
    if titles and not takeaways:
        result["reasons"] = ["non_obviousness_untested:obvious_summary_empty"]
        return result
    result["obvious_summary"] = takeaways
    survivors = spanning
    if takeaways:
        record = {"evidence_id": OBVIOUS_EVIDENCE_ID, "source_id": "OBVIOUS-SUMMARY",
                  "evidence_type": "source_excerpt", "text": " ".join(takeaways), "claim": " ".join(takeaways),
                  "published_at": datetime.now(timezone.utc).date().isoformat()}
        claims = [{"claim_id": f"OBV-{i}", "claim_text": e.get("plain") or _clean(c.get("claim_text")),
                   "evidence_ids": [OBVIOUS_EVIDENCE_ID], "epistemic_type": "hidden_insight",
                   "multi_evidence_required": False} for i, (c, _, e) in enumerate(spanning)]
        try:
            statuses = [_status(c) for c in (ground(claims, [record]) or {}).get("claims") or []]
        except Exception as error:
            result["reasons"] = [f"non_obviousness_untested:obvious_check_{type(error).__name__}"]
            return result
        if len(statuses) != len(spanning):
            result["reasons"] = ["non_obviousness_untested:obvious_check_incomplete"]
            return result
        survivors = []
        for item, status in zip(spanning, statuses):
            item[2]["obvious_summary_status"] = status
            if status == "supported":
                item[2]["failed"] = "conclusion_obvious_from_headlines"
            else:
                survivors.append(item)
    if not survivors:
        result["reasons"] = ["conclusion_obvious_from_headlines"]
        return result

    # Test 2b (v3): already said by published expert analysis? The bar is the
    # expert frontier, not the headline reader (co-scientist / AI Scientist /
    # SciMON judge novelty against retrieved literature the same way).
    experts = commentary_records(evidence)
    result["expert_commentary_records"] = len(experts)
    if experts:
        if not budget_ok():
            result["reasons"] = ["non_obviousness_untested:time_budget"]
            return result
        claims = [{"claim_id": f"EXPERT-{i}", "claim_text": e.get("plain") or _clean(c.get("claim_text")),
                   "evidence_ids": [_clean(r.get("evidence_id")) for r in experts],
                   "epistemic_type": "hidden_insight", "multi_evidence_required": False}
                  for i, (c, _, e) in enumerate(survivors)]
        try:
            statuses = [_status(c) for c in (ground(claims, list(experts)) or {}).get("claims") or []]
        except Exception as error:
            result["reasons"] = [f"non_obviousness_untested:expert_check_{type(error).__name__}"]
            return result
        if len(statuses) != len(survivors):
            result["reasons"] = ["non_obviousness_untested:expert_check_incomplete"]
            return result
        beyond = []
        for item, status in zip(survivors, statuses):
            item[2]["expert_commentary_status"] = status
            if status in SUFFICIENT:
                item[2]["failed"] = "conclusion_already_in_expert_commentary"
            else:
                beyond.append(item)
        if not beyond:
            result["reasons"] = ["conclusion_already_in_expert_commentary"]
            return result
        survivors = beyond

    # Test 3: does any single development (plus cited background) suffice?
    passing = []
    for claim, devs, entry in survivors:
        if not budget_ok():
            entry["failed"] = "non_obviousness_untested:time_budget"
            continue
        cited = _ids(claim.get("cited_evidence_ids")) or _ids(claim.get("evidence_ids"))
        cited = list(dict.fromkeys(cited + _ids(claim.get("supporting_evidence_ids"))))
        background = [eid for eid in cited if not dev_of.get(eid)]
        if entry.get("route") == "constraint":
            # Ablate the constraint: the development's records alone must NOT suffice.
            background = [eid for eid in background if not is_constraint(by_id.get(eid))]
        alone = {}
        for dev, _ in devs.most_common(MAX_DEVELOPMENTS):
            ids = [eid for eid in cited if dev_of.get(eid) == dev] + background
            records = [by_id[eid] for eid in ids if eid in by_id]
            if not records:
                continue
            try:
                out = (ground([{"claim_id": "ALONE-1", "claim_text": _clean(claim.get("claim_text")),
                                "evidence_ids": [_clean(r.get("evidence_id")) for r in records],
                                "epistemic_type": "hidden_insight",
                                "multi_evidence_required": bool(claim.get("multi_evidence_required"))}],
                              records) or {}).get("claims") or []
                status = _status(out[0]) if out else ""
            except Exception as error:
                status = f"error:{type(error).__name__}"
            alone[dev] = status
            if status in SUFFICIENT or status.startswith("error") or not status:
                break
        entry["single_development_status"] = alone
        if any(s in SUFFICIENT for s in alone.values()):
            entry["failed"] = ("conclusion_does_not_need_the_constraint" if entry.get("route") == "constraint"
                               else "conclusion_follows_from_one_development")
        elif any(s.startswith("error") or not s for s in alone.values()):
            entry["failed"] = "non_obviousness_untested:single_development_check"
        else:
            passing.append(entry)
    if passing:
        result["passed"] = True
        result["passing_claim_ids"] = [e["claim_id"] for e in passing]
        result["route"] = ("cross_development" if any(e.get("route") == "cross_development" for e in passing)
                           else "constraint")
        return result
    result["reasons"] = sorted({e.get("failed", "") for _, _, e in survivors} - {""}) or ["no_claim_passed"]
    return result


def brief(reasons: Sequence[str]) -> list[str]:
    return [BRIEF[r] for r in reasons if r in BRIEF]
