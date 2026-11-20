"""Claim-support release gate (v6.13).

Decides whether a strategic insight may be released, from the claim-level
grounding verdicts that GroundingService already produces (one independent
model call per claim, reading only the cited evidence).

Method: claim-level attribution. An output counts as attributable to its
sources only when each of its atomic claims is (AIS, Rashkin et al. 2021;
FActScore, Min et al. 2023). The old insight-level aggregate called an insight
"supported" when ANY one claim was supported, so the background facts (which
always pass) carried an unsupported conclusion and mechanism through release.

Rules
-----
- Premises (epistemic type "fact") must be "supported": stated by their
  cited evidence.
- Conclusion sentences ("hidden_insight") and mechanism steps ("mechanism")
  must be "supported" or "inference_supported": stated by, or following
  directly from, at least two cited records (GroundingService already
  downgrades a multi-evidence claim with fewer than two supporting records).
- Any core claim that is contradicted, unsupported or only partly supported
  blocks release.
- Second-order effects are prospective by nature. A sentence that does not
  reach "supported"/"inference_supported" is removed from the released text
  (and from the released grounding record); it does not block the insight.
- A model-written alternative explanation keeps its citation only when the
  cited evidence states it ("supported"). Otherwise the citation is removed
  (the text stays, as an unsourced alternative), because a source must never
  be credited with a statement it does not make.
- v1.1: a conclusion sentence or mechanism step must be backed by records
  from at least two different sources (several records cut from one article
  are one source).
- v1.1: everything else the report shows for the insight (novel proposition,
  triggering anomaly, why it is easy to miss, derivation, likelihood basis,
  why it matters, connections) is grounded claim by claim. A failing sentence
  or item is removed; a consequence labelled confirmed/supported_inference
  that fails is relabelled "hypothesis". Questions assert nothing and forward
  -looking items (falsifiers, watch signals) are shown as such, not grounded.
  v1.2: a question is checked for the facts it takes for granted; a
  historical case's "important difference" and a counter-case's "why it
  matters" are checked with the item.
- v1.1: final quality is enforced: status insufficient_evidence or
  needs_refinement, or a critical dimension at 1/5 or lower, blocks release.

- v2.0 convergent-evidence rule (analytic tradecraft: ICD 203 separates
  assumptions from judgments and states analytic confidence; the CIA
  Tradecraft Primer's Key Assumptions Check lists the premises a judgment
  needs that the information does not establish). A mechanism step that the
  sources do not state is no longer forced to pass as sourced fact. It
  becomes a labelled KEY ASSUMPTION, and the insight may still be released,
  only when ALL of these hold:
    * the conclusion passes as before (inference from >= 2 independent
      sources) and every premise is supported;
    * the supported premises come from >= 3 different underlying news
      events and >= 2 different publishers (convergent, independent
      observations);
    * no claim is contradicted, and at most 3 steps are assumptions.
  The released record lists the assumptions as "analysis, not stated by any
  source" and carries an analytic confidence: high with >= 5 events from
  >= 3 publishers, at most one assumption and no counterevidence; otherwise
  moderate. Without the evidence-to-event map (older callers) the v1.6 rule
  applies unchanged.

- v3.0 typed leverage (8 Nov 2026). The objective is "see the leverage for a thing and add
  incentives": the leverage and the incentives are the app's reasoning, not something a news
  report states. Until v2.0 they were checked as facts a source must state, so in the 25 Oct
  run (REPORT-316E7FC814727B8CCAA30C52) every candidate failed on "the evidence does not state
  that investors depend on ..." or "does not state the purpose of the funding". Same split as the
  hypothesis lab (GPT lab-dependency patch, 26 Oct) and ICD 203 (facts vs assumptions vs judgments):
    * CONTROL - what the holder controls or holds - is a fact: it must be "supported".
    * DEPENDENCY - who needs it - must follow from the cited facts ("supported" or
      "inference_supported"). Partly backed: a labelled assumption. Not backed at all: blocks
      (leverage nobody can show is not leverage).
    * INCENTIVES - what each side gains or loses - pass when stated or inferred from the cited
      facts; otherwise they are shown as labelled assumptions ("inferred incentive"). Incentives are
      rarely written in news; requiring a source to state them rejected the objective itself.
    * Anything contradicted still blocks. Mechanism steps keep the v2.0 convergence rule; leverage
      and incentive assumptions do not need it (the control fact is sourced) but count toward the
      same limit of MAX_KEY_ASSUMPTIONS.
  Claims built by older code (one "-LEVERAGE" fact) are judged as before.
- v3.0: leverage_reasons also requires the holder and the dependent to be NAMED parties (an
  organisation, agency or person), not a category ("Venture investors seeking exposure to AI"):
  the 25 Oct candidates' dependents were all categories, which is what made them generic. This
  runs before any grounding call, so it also saves the claim checks on such candidates.

Pure functions, no model calls: the orchestrator supplies grounding results.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Mapping, Sequence

VERSION = "claim-support-gate-v4.1-two-lane"

# v2.0 convergent-evidence rule
CONVERGENCE_MIN_EVENTS = 3
CONVERGENCE_MIN_PUBLISHERS = 2
HIGH_CONFIDENCE_EVENTS = 5
HIGH_CONFIDENCE_PUBLISHERS = 3
MAX_KEY_ASSUMPTIONS = 3
ASSUMPTION_LABEL = "analysis, not stated by any source"
# v3.0 typed leverage claims (claim-id suffixes built by the orchestrator's _leverage_claims).
DEPENDENCY_SUFFIX = "-DEPENDENCY"
# v10.1: every claim the app writes from the leverage fields (not from the insight text itself).
LEVERAGE_CLAIM_SUFFIXES = ("-CONTROL", "-DEPENDENCY", "-HOLDER-INCENTIVE", "-DEPENDENT-INCENTIVE", "-LEVERAGE")
INCENTIVE_SUFFIXES = ("-HOLDER-INCENTIVE", "-DEPENDENT-INCENTIVE")
REASONED_PASSING = frozenset({"supported", "inference_supported"})
ASSUMPTION_KIND_LABEL = {
    "mechanism": "",
    "dependency": "Inferred dependency (partly stated by the sources): ",
    "incentive": "Inferred incentive (not stated by the sources): ",
    "forecast": "Forecast - the app's judgment, not stated by the sources: ",
}
# v3.1 (9 Nov 2026, Claude-proxy replay of the 25 Oct run): a conclusion sentence the model itself
# labels as a hypothesis or forecast ("Hypothesis: when issued, the FTC's demands will ...") can
# never be stated by a source; checked as a conclusion it always failed, so forward reasoning was
# never released. It is now a labelled forecast (ICD 203: judgments labelled, not withheld) that
# counts toward MAX_KEY_ASSUMPTIONS; the insight still needs >= 1 non-forecast conclusion sentence
# that the cited records support from two sources.
_FORECAST_SENTENCE = re.compile(
    r"^\s*(?:hypothesis|forecast|prediction|projection)\b|\(labell?ed (?:assumption|hypothesis)", re.I)

# Status a claim of each core type must reach for release.
CORE_PASSING = {
    "fact": frozenset({"supported"}),
    "hidden_insight": frozenset({"supported", "inference_supported"}),
    "mechanism": frozenset({"supported", "inference_supported"}),
    "connection": frozenset({"supported", "inference_supported"}),
}
# Prunable types: sentences below this bar are removed, not release-blocking.
PRUNABLE_PASSING = {
    "second_order_effect": frozenset({"supported", "inference_supported"}),
}
# An alternative's citation is kept only when the cited evidence states it.
ALTERNATIVE_BACKED = frozenset({"supported"})
# Conclusion sentences and mechanism steps need records from >= 2 sources.
INDEPENDENT_SOURCE_TYPES = frozenset({"hidden_insight", "mechanism"})
MIN_INDEPENDENT_SOURCES = 2
# v3.2 (12 Nov 2026): a conclusion sentence backed by ONE source is released, labelled, when that
# source is credible (official, research, established news, professional), it supports the sentence,
# nothing contradicts it and every other check passes. Basis: the Admiralty / NATO AJP-2.1 code rates
# source reliability and corroboration separately - an uncorroborated report from a reliable source is
# "probably true" (2), not discarded, and only corroboration earns "confirmed" (1); ICD 203 asks that
# products describe source quality and state confidence with its basis; AP's two-source norm has an
# exception for an authoritative source in a position to know, and requires attributing another
# outlet's reporting to that outlet. Mechanism steps already become labelled assumptions.
# Set EVENT_INTELLIGENCE_SINGLE_SOURCE_RELEASE=0 to require two sources again.
SINGLE_SOURCE_LABEL = "Single source, not yet corroborated - moderate confidence: "


def _single_source_release_on() -> bool:
    import os
    return os.getenv("EVENT_INTELLIGENCE_SINGLE_SOURCE_RELEASE", "1").strip() not in {"0", "false", "no"}
# Final quality: these statuses, or a critical dimension at or below the
# floor (scores are 0-4), block release.
BLOCKING_QUALITY_STATUSES = frozenset({"insufficient_evidence", "needs_refinement"})
# counterevidence_quality is critical, except when the challenge search of
# the final wording completed and found nothing and no counterevidence is
# attached: the quality service scores it 1 whenever none is attached, but
# "searched, none found" is a valid result (see quality_reasons(ignore=...)).
CRITICAL_QUALITY_DIMENSIONS = (
    "evidence_distance", "cross_event_synthesis", "mechanism_quality",
    "epistemic_discipline", "counterevidence_quality", "prospective_feasibility",
)
CRITICAL_QUALITY_FLOOR = 1

FAILURE_REASON = {
    "fact": "premise_not_supported_by_cited_evidence",
    "hidden_insight": "conclusion_not_supported_by_cited_evidence",
    "mechanism": "mechanism_step_not_supported_by_cited_evidence",
    "connection": "connection_not_supported_by_cited_evidence",
}
SOURCE_REASON = {
    "hidden_insight": "conclusion_not_backed_by_two_independent_sources",
    "mechanism": "mechanism_step_not_backed_by_two_independent_sources",
}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _unique(values: Sequence[Any]) -> list[str]:
    seen, out = set(), []
    for value in values or []:
        text = _clean(value)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _claims(grounding: Any) -> list[Mapping[str, Any]] | None:
    if not isinstance(grounding, Mapping):
        return None
    if _clean(grounding.get("review_status")).casefold() != "complete":
        return None
    claims = grounding.get("claims")
    if not isinstance(claims, list):
        return None
    return [claim for claim in claims if isinstance(claim, Mapping)]


def _type(claim: Mapping[str, Any]) -> str:
    return _clean(claim.get("epistemic_type")).casefold()


def _status(claim: Mapping[str, Any]) -> str:
    return _clean(claim.get("grounding_status") or claim.get("status")).casefold()


def _failure_summary(claim: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "claim_id": _clean(claim.get("claim_id")),
        "epistemic_type": _type(claim),
        "grounding_status": _status(claim),
        "claim_text": _clean(claim.get("claim_text"))[:400],
        "unsupported_parts": [
            _clean(part)[:300] for part in (claim.get("unsupported_parts") or [])[:3]
            if _clean(part)
        ],
    }


def reasoned_kind(claim: Mapping[str, Any]) -> str:
    """v3.0: "dependency" / "incentive" for the typed leverage claims, else ""."""
    claim_id = _clean(claim.get("claim_id")).upper()
    if _type(claim) == "hidden_insight" and _FORECAST_SENTENCE.search(_clean(claim.get("claim_text"))):
        return "forecast"
    if _type(claim) == "fact":
        return ""          # an older "-LEVERAGE"-style fact claim keeps the fact bar
    if claim_id.endswith(DEPENDENCY_SUFFIX):
        return "dependency"
    if claim_id.endswith(INCENTIVE_SUFFIXES):
        return "incentive"
    return ""


def convergence(claims: Sequence[Mapping[str, Any]], evidence_events: Mapping[str, str] | None,
                evidence_publishers: Mapping[str, str] | None) -> dict[str, Any]:
    """Independent observations behind an insight: the distinct underlying
    events and publishers of its SUPPORTED premises (facts)."""
    events, publishers = set(), set()
    for claim in claims:
        if _type(claim) != "fact" or _status(claim) != "supported":
            continue
        ids = claim.get("supporting_evidence_ids") or claim.get("valid_evidence_ids") or []
        for eid in ids:
            key = _clean(eid)
            event = _clean((evidence_events or {}).get(key))
            publisher = _clean((evidence_publishers or {}).get(key))
            if event:
                events.add(event)
            if publisher:
                publishers.add(publisher)
    return {"events": len(events), "publishers": len(publishers),
            "met": len(events) >= CONVERGENCE_MIN_EVENTS and len(publishers) >= CONVERGENCE_MIN_PUBLISHERS}


def analytic_confidence(conv: Mapping[str, Any], assumptions: int, counterevidence: int) -> str:
    """ICD 203-style confidence in the judgment (not a probability)."""
    if (conv.get("events", 0) >= HIGH_CONFIDENCE_EVENTS and conv.get("publishers", 0) >= HIGH_CONFIDENCE_PUBLISHERS
            and assumptions <= 1 and counterevidence == 0):
        return "high"
    return "moderate"


# ---------------------------------------------------------------------------------------------
# v4.0 two-lane gate (12 Nov 2026). Research basis (Fable reviews, 12 Nov):
#   * ALCE (Gao et al. 2023) and FActScore (Min et al. 2023) verify FACTUAL statements against
#     their citations; ICD 203 lists "distinguish underlying intelligence information from
#     analysts' assumptions and judgments" as a tradecraft standard; ICD 206 describes source
#     quality instead of blocking single-source items.
#   * Google's AI co-scientist grounds facts in generation/reflection and ranks hypotheses by
#     pairwise tournament; forecasting systems (Halawi et al. 2024, ForecastBench) score
#     predictions when they resolve, not by whether a source states them.
# So: FACT claims (premises, what an actor controls) are checked against sources; every other
# claim (conclusion sentences, mechanism, dependency, incentives, forecasts, second-order) is a
# JUDGMENT: it is blocked only when a cited record CONTRADICTS it, and otherwise released with
# a label saying how the sources relate to it. Earlier rules that sent judgments to the
# source-checker (v1-v3: two sources per conclusion, the 3-assumption limit, convergence) made
# forward reasoning impossible to release; they stay as evaluate_legacy, selected with
# EVENT_INTELLIGENCE_GATE_MODE=legacy.
JUDGMENT_LABEL = {
    "supported": "stated by the cited sources",
    "inference_supported": "follows from the cited facts",
    "partially_supported": "judgment - partly backed by the cited facts",
    "unsupported": "judgment - not stated by the sources",
}
FACT_LANE_TYPES = frozenset({"fact"})


def _gate_mode() -> str:
    import os
    return os.getenv("EVENT_INTELLIGENCE_GATE_MODE", "two_lane").strip().casefold()


def evaluate(grounding: Any, evidence_sources: Mapping[str, str] | None = None,
             evidence_events: Mapping[str, str] | None = None,
             evidence_publishers: Mapping[str, str] | None = None, *,
             credible_sources: Any = None) -> dict[str, Any]:
    if _gate_mode() == "legacy":
        return evaluate_legacy(grounding, evidence_sources, evidence_events, evidence_publishers,
                               credible_sources=credible_sources)
    return evaluate_two_lane(grounding, evidence_sources, evidence_events, evidence_publishers,
                             credible_sources=credible_sources)


def evaluate_two_lane(grounding: Any, evidence_sources: Mapping[str, str] | None = None,
                      evidence_events: Mapping[str, str] | None = None,
                      evidence_publishers: Mapping[str, str] | None = None, *,
                      credible_sources: Any = None) -> dict[str, Any]:
    """v4.0 release verdict. Facts lane: a premise must be 'supported' by its citations; a
    'partially_supported' premise is released labelled 'partly verified'; an unsupported or
    contradicted premise blocks (fabricated facts are still caught). At least one premise must
    be fully supported. Judgment lane: blocked only by 'contradicted'. A fact backed by a single
    source is labelled with that, never blocked (ICD 206 / Admiralty: 'probably true')."""
    claims = _claims(grounding)
    if claims is None:
        return {"version": VERSION, "passed": False, "repairable": False,
                "reasons": ["grounding_unavailable"], "failed_claims": [], "pruned_claim_ids": [],
                "core_claims": 0, "core_claims_passed": 0, "key_assumptions": [],
                "key_assumption_claim_ids": [], "judgments": [], "single_source_claims": [],
                "confidence": ""}
    failed, pruned, reasons = [], [], []
    labelled: list[dict[str, Any]] = []
    judgments: list[dict[str, Any]] = []
    single_source: list[dict[str, Any]] = []
    facts_total = facts_supported = conclusion_total = core_total = core_passed = 0
    contradicted = False
    for claim in claims:
        claim_type, status = _type(claim), _status(claim)
        claim_id, text = _clean(claim.get("claim_id")), _clean(claim.get("claim_text"))
        if status == "contradicted":
            contradicted = True
            failed.append(_failure_summary(claim))
            reasons.append("claim_contradicted_by_cited_evidence")
            continue
        if claim_type in FACT_LANE_TYPES:
            facts_total += 1
            core_total += 1
            # 12 Nov 2026 (first capped Gemini run + saved candidates): a fact the cited records
            # ENTAIL without stating word for word ("inference_supported") was blocking. ALCE's
            # citation-recall test is entailment by the cited passages, so an entailed fact passes.
            if status in ("supported", "inference_supported"):
                facts_supported += 1
                core_passed += 1
                if status == "inference_supported":
                    labelled.append({"claim_id": claim_id, "kind": "entailed_fact", "text": text[:460],
                                     "grounding_status": status, "label": "follows from the cited records",
                                     "unsupported_parts": []})
                if evidence_sources is not None:
                    sources = {_clean(evidence_sources.get(_clean(eid)))
                               for eid in claim.get("supporting_evidence_ids") or []} - {""}
                    if len(sources) == 1:
                        single_source.append({"claim_id": claim_id, "kind": "single_source",
                                              "text": (SINGLE_SOURCE_LABEL + text)[:460],
                                              "grounding_status": status, "source_id": next(iter(sources)),
                                              "label": "single source", "unsupported_parts": []})
            elif status == "partially_supported":
                core_passed += 1
                labelled.append({"claim_id": claim_id, "kind": "partial_fact",
                                 "text": ("Partly verified fact: " + text)[:460], "grounding_status": status,
                                 "label": "partly verified by the cited sources",
                                 "unsupported_parts": _failure_summary(claim).get("unsupported_parts") or []})
            else:
                failed.append(_failure_summary(claim))
                reasons.append("premise_not_supported_by_cited_evidence")
            continue
        if claim_type in PRUNABLE_PASSING or claim_type in CORE_PASSING or reasoned_kind(claim):
            # judgment lane
            core_total += 1
            core_passed += 1
            conclusion_total += claim_type == "hidden_insight"
            label = JUDGMENT_LABEL.get(status, JUDGMENT_LABEL["unsupported"])
            judgments.append({"claim_id": claim_id, "epistemic_type": claim_type, "grounding_status": status,
                              "label": label, "claim_text": text[:460]})
            if status not in ("supported", "inference_supported"):
                labelled.append({"claim_id": claim_id, "kind": reasoned_kind(claim) or claim_type,
                                 "text": text[:460], "grounding_status": status, "label": label,
                                 "unsupported_parts": _failure_summary(claim).get("unsupported_parts") or []})
    if not conclusion_total:
        reasons.append("no_conclusion_claim_checked")
    if facts_total and not facts_supported:
        reasons.append("no_premise_fully_supported")
    if not facts_total:
        reasons.append("no_factual_premise")
    reasons = _unique(reasons)
    passed = not reasons
    conv = convergence(claims, evidence_events, evidence_publishers) if evidence_events is not None else {
        "events": 0, "publishers": 0, "met": False, "not_measured": True}
    shown = labelled + single_source if passed else []
    return {
        "version": VERSION, "passed": passed,
        "repairable": (not passed and not contradicted and facts_supported > 0),
        "reasons": reasons, "failed_claims": failed, "pruned_claim_ids": pruned,
        "core_claims": core_total, "core_claims_passed": core_passed, "convergence": conv,
        "key_assumptions": shown, "key_assumption_claim_ids": [i["claim_id"] for i in shown],
        "judgments": judgments if passed else [], "single_source_claims": single_source if passed else [],
        "confidence": ("" if not passed else "moderate (rests on single-source or partly verified facts)"
                       if (single_source or any(i["kind"] == "partial_fact" for i in labelled))
                       else "high (facts confirmed by two or more sources)"),
    }


def evaluate_legacy(grounding: Any, evidence_sources: Mapping[str, str] | None = None,
                    evidence_events: Mapping[str, str] | None = None,
                    evidence_publishers: Mapping[str, str] | None = None, *,
                    credible_sources: Any = None) -> dict[str, Any]:
    """Release verdict for one insight's grounding record.

    passed: every core claim reached its bar.
    repairable: failed, but nothing is contradicted and at least one core claim
        passed, so a narrowed revision of the same thesis may pass.
    evidence_sources: {evidence_id: source_id}. When given, a passing
        conclusion sentence or mechanism step must also be backed by records
        from at least MIN_INDEPENDENT_SOURCES different sources.
    credible_sources: source IDs rated credible; a conclusion backed by one of them alone is
        released labelled "single source, moderate confidence" (v3.2).
    """
    claims = _claims(grounding)
    credible = {_clean(x) for x in credible_sources or []} if credible_sources is not None else None
    single_source: list[dict[str, Any]] = []
    if claims is None:
        return {
            "version": VERSION, "passed": False, "repairable": False,
            "reasons": ["grounding_unavailable"], "failed_claims": [],
            "pruned_claim_ids": [], "core_claims": 0, "core_claims_passed": 0,
        }
    failed, pruned, reasons = [], [], []
    core_total = core_passed = conclusion_total = 0
    contradicted = False
    # v2.0: mechanism steps that are not contradicted but not sourced either,
    # held back until the convergence test decides whether they may stand
    # as labelled assumptions.
    assumption_candidates: list[tuple[Mapping[str, Any], dict[str, Any], str]] = []
    # v3.0: leverage/incentive reasoning not established by the sources, shown as labelled
    # assumptions (no convergence requirement; same overall limit).
    reasoned_candidates: list[tuple[Mapping[str, Any], dict[str, Any], str, str]] = []
    for claim in claims:
        claim_type, status = _type(claim), _status(claim)
        kind = reasoned_kind(claim)
        if kind:
            core_total += 1
            if status in REASONED_PASSING:
                core_passed += 1
            elif status == "contradicted":
                contradicted = True
                failed.append(_failure_summary(claim))
                reasons.append("claim_contradicted_by_cited_evidence")
            elif kind in ("incentive", "forecast") or status == "partially_supported":
                reasoned_candidates.append((claim, _failure_summary(claim), kind,
                                            {"dependency": "leverage_dependency_not_supported_by_cited_evidence",
                                             "forecast": "forecast_premises_not_supported"}.get(
                                                kind, "incentive_not_supported_by_cited_evidence")))
            else:
                failed.append(_failure_summary(claim))
                reasons.append("leverage_dependency_not_supported_by_cited_evidence")
            continue
        if claim_type in CORE_PASSING:
            core_total += 1
            conclusion_total += claim_type == "hidden_insight"
            if status in CORE_PASSING[claim_type]:
                if evidence_sources is not None and claim_type in INDEPENDENT_SOURCE_TYPES:
                    sources = {
                        _clean(evidence_sources.get(_clean(eid)))
                        for eid in claim.get("supporting_evidence_ids") or []
                    } - {""}
                    if len(sources) < MIN_INDEPENDENT_SOURCES:
                        summary = _failure_summary(claim)
                        summary["unsupported_parts"] = [
                            f"backed by {len(sources)} distinct source(s); at least "
                            f"{MIN_INDEPENDENT_SOURCES} independent sources are required"
                        ]
                        if claim_type == "mechanism":
                            assumption_candidates.append((claim, summary, SOURCE_REASON[claim_type]))
                            continue
                        if (claim_type == "hidden_insight" and len(sources) == 1 and credible is not None
                                and sources <= credible and _single_source_release_on()):
                            core_passed += 1
                            single_source.append({
                                "claim_id": _clean(claim.get("claim_id")),
                                "text": (SINGLE_SOURCE_LABEL + _clean(claim.get("claim_text")))[:460],
                                "grounding_status": status, "label": "single credible source, moderate confidence",
                                "kind": "single_source", "source_id": next(iter(sources)),
                                "unsupported_parts": summary["unsupported_parts"]})
                            continue
                        failed.append(summary)
                        reasons.append(SOURCE_REASON[claim_type])
                        continue
                core_passed += 1
                continue
            contradicted = contradicted or status == "contradicted"
            if claim_type == "mechanism" and status in {"unsupported", "partially_supported"}:
                assumption_candidates.append((claim, _failure_summary(claim), FAILURE_REASON[claim_type]))
                continue
            failed.append(_failure_summary(claim))
            reasons.append(
                "claim_contradicted_by_cited_evidence" if status == "contradicted"
                else FAILURE_REASON[claim_type]
            )
        elif claim_type in PRUNABLE_PASSING and status not in PRUNABLE_PASSING[claim_type]:
            pruned.append(_clean(claim.get("claim_id")))
    if not conclusion_total:
        reasons.append("no_conclusion_claim_checked")
    conv = convergence(claims, evidence_events, evidence_publishers) if evidence_events is not None else {
        "events": 0, "publishers": 0, "met": False, "not_measured": True}
    key_assumptions: list[dict[str, Any]] = []
    total_assumptions = len(assumption_candidates) + len(reasoned_candidates)
    if reasoned_candidates:
        if not reasons and not contradicted and total_assumptions <= MAX_KEY_ASSUMPTIONS:
            key_assumptions.extend(
                {"claim_id": _clean(c.get("claim_id")),
                 "text": (ASSUMPTION_KIND_LABEL[kind] + _clean(c.get("claim_text")))[:460],
                 "grounding_status": _status(c), "label": ASSUMPTION_LABEL, "kind": kind,
                 "unsupported_parts": summary.get("unsupported_parts") or []}
                for c, summary, kind, _ in reasoned_candidates)
        else:
            for _, summary, _, reason in reasoned_candidates:
                failed.append(summary)
                reasons.append(reason)
            if total_assumptions > MAX_KEY_ASSUMPTIONS:
                reasons.append("too_many_unverified_assumptions")
    if assumption_candidates:
        allowed = (evidence_events is not None and conv["met"] and not reasons and not contradicted
                   and total_assumptions <= MAX_KEY_ASSUMPTIONS)
        if allowed:
            key_assumptions.extend({"claim_id": _clean(c.get("claim_id")), "text": _clean(c.get("claim_text"))[:400],
                                    "grounding_status": _status(c), "label": ASSUMPTION_LABEL, "kind": "mechanism"}
                                   for c, _, _ in assumption_candidates)
        else:
            for _, summary, reason in assumption_candidates:
                failed.append(summary)
                reasons.append(reason)
            if key_assumptions:   # the leverage assumptions cannot stand once the insight fails
                for item in key_assumptions:
                    failed.append({"claim_id": item["claim_id"], "epistemic_type": "connection",
                                   "grounding_status": item["grounding_status"], "claim_text": item["text"][:400],
                                   "unsupported_parts": item["unsupported_parts"]})
                    reasons.append({"dependency": "leverage_dependency_not_supported_by_cited_evidence",
                                    "forecast": "forecast_premises_not_supported"}.get(
                                        item["kind"], "incentive_not_supported_by_cited_evidence"))
                key_assumptions = []
            if evidence_events is not None and not conv["met"]:
                reasons.append("unsourced_steps_without_convergent_observations")
            elif total_assumptions > MAX_KEY_ASSUMPTIONS:
                reasons.append("too_many_unverified_assumptions")
    reasons = _unique(reasons)
    passed = not reasons
    return {
        "version": VERSION,
        "passed": passed,
        "repairable": (not passed and not contradicted and core_passed > 0 and conclusion_total > 0),
        "reasons": reasons,
        "failed_claims": failed,
        "pruned_claim_ids": pruned,
        "core_claims": core_total,
        "core_claims_passed": core_passed,
        "convergence": conv,
        "key_assumptions": key_assumptions + (single_source if passed else []),
        "key_assumption_claim_ids": [item["claim_id"] for item in key_assumptions + (single_source if passed else [])],
        "single_source_claims": single_source if passed else [],
        "confidence": ("moderate (single credible source)" if passed and single_source
                       else "high (two or more independent sources)" if passed else ""),
    }


def aggregate(claims: Sequence[Mapping[str, Any]]) -> str:
    """Same aggregate rule as GroundingService.ground_claims."""
    statuses = [_status(claim) for claim in claims]
    if not statuses:
        return "unsupported"
    if "contradicted" in statuses:
        return "contradicted"
    if all(status == "unsupported" for status in statuses):
        return "unsupported"
    if "unsupported" in statuses or "partially_supported" in statuses:
        return "partially_supported"
    if "inference_supported" in statuses:
        return "inference_supported"
    return "supported"


def _rebuilt_grounding(grounding: Mapping[str, Any], keep: list[Mapping[str, Any]]) -> dict[str, Any]:
    out = copy.deepcopy(dict(grounding))
    status = aggregate(keep)
    counts = {key: 0 for key in ("supported", "partially_supported", "inference_supported", "unsupported", "contradicted")}
    for claim in keep:
        counts[_status(claim)] = counts.get(_status(claim), 0) + 1
    support = _unique([eid for claim in keep for eid in (claim.get("supporting_evidence_ids") or [])])
    contra = _unique([eid for claim in keep for eid in (claim.get("contradicted_evidence_ids") or [])])
    parts = _unique([part for claim in keep for part in (claim.get("unsupported_parts") or [])])
    out.update({
        "status": status, "grounding_status": status,
        "claims": [copy.deepcopy(dict(claim)) for claim in keep],
        "claim_count": len(keep), "status_counts": counts,
        "support_evidence_ids": support, "supporting_evidence_ids": support,
        "counterevidence_ids": contra, "contradicted_evidence_ids": contra,
        "critical_unknowns": parts, "unsupported_parts": parts,
    })
    metadata = dict(out.get("metadata") or {})
    metadata["aggregate_status"] = status
    metadata["claim_support_gate"] = VERSION
    out["metadata"] = metadata
    return out


def sanitize_for_release(
    candidate: Mapping[str, Any],
    grounding: Mapping[str, Any],
    verdict: Mapping[str, Any],
    alternative_checks: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Released copy of a PASSED candidate and its grounding record.

    - second_order_effect keeps only its sentences that reached the bar (the
      grounding claims are exactly those sentences, in order);
    - pruned claims leave the released grounding record, whose aggregate is
      recomputed;
    - an alternative whose cited evidence does not back it loses the citation.

    alternative_checks: {alternative_id: grounding status}, or None when the
    check could not run (then every cited alternative loses its citation).
    """
    if not verdict.get("passed"):
        raise ValueError("Only a passed candidate can be released")
    claims = _claims(grounding) or []
    pruned = set(verdict.get("pruned_claim_ids") or [])
    assumption_ids = set(verdict.get("key_assumption_claim_ids") or [])
    # v2.0: key assumptions leave the released grounding record (it lists
    # only what the sources back) and are shown separately, labelled.
    keep = [claim for claim in claims
            if _clean(claim.get("claim_id")) not in pruned and _clean(claim.get("claim_id")) not in assumption_ids]
    out = copy.deepcopy(dict(candidate))
    notes: dict[str, Any] = {"pruned_second_order_sentences": [], "alternatives_uncited": []}

    if pruned:
        second = [claim for claim in claims if _type(claim) == "second_order_effect"]
        kept_text = [_clean(c.get("claim_text")) for c in second if _clean(c.get("claim_id")) not in pruned]
        notes["pruned_second_order_sentences"] = [
            _clean(c.get("claim_text"))[:400] for c in second if _clean(c.get("claim_id")) in pruned
        ]
        out["second_order_effect"] = " ".join(text for text in kept_text if text)

    checks = dict(alternative_checks or {}) if alternative_checks is not None else None
    alternatives = []
    for item in out.get("alternative_explanations") or []:
        if not isinstance(item, Mapping):
            alternatives.append(item)
            continue
        alt = dict(item)
        alt_id = _clean(alt.get("alternative_id"))
        if alt.get("evidence_ids"):
            status = _clean((checks or {}).get(alt_id)).casefold() if checks is not None else ""
            if status not in ALTERNATIVE_BACKED:
                notes["alternatives_uncited"].append({
                    "alternative_id": alt_id,
                    "removed_evidence_ids": list(alt.get("evidence_ids") or []),
                    "grounding_status": status or "not_checked",
                })
                alt["evidence_ids"] = []
        alternatives.append(alt)
    if "alternative_explanations" in out:
        out["alternative_explanations"] = alternatives
    uncited = {item["alternative_id"] for item in notes["alternatives_uncited"]}
    metadata = dict(out.get("metadata") or {})
    analysis = metadata.get("alternative_analysis")
    if isinstance(analysis, list) and uncited:
        metadata["alternative_analysis"] = [
            {**dict(row), "analysis_status": "plausible_unverified_alternative",
             "citation_removed_by": VERSION}
            if isinstance(row, Mapping) and _clean(row.get("alternative_id")) in uncited else row
            for row in analysis
        ]
    assumptions = [dict(item) for item in verdict.get("key_assumptions") or []]
    conv = dict(verdict.get("convergence") or {})
    counter = len(_unique(candidate.get("counterevidence_ids") or []))
    confidence = analytic_confidence(conv, len(assumptions), counter) if conv and not conv.get("not_measured") else ""
    if verdict.get("single_source_claims"):
        # v3.2: an uncorroborated conclusion caps confidence (Admiralty "probably true", not "confirmed").
        confidence = "low" if confidence == "low" else "moderate"
    metadata["claim_support_gate"] = {
        "version": VERSION, "passed": True,
        "core_claims": verdict.get("core_claims"),
        "core_claims_passed": verdict.get("core_claims_passed"),
        "key_assumptions": assumptions,
        "convergence": conv,
        "analytic_confidence": confidence,
        **notes,
    }
    if assumptions:
        metadata["key_assumptions"] = assumptions
    if confidence:
        metadata["analytic_confidence"] = {
            "level": confidence,
            "independent_events": conv.get("events", 0),
            "independent_publishers": conv.get("publishers", 0),
            "key_assumptions": len(assumptions),
            "counterevidence_items": counter,
            "basis": (f"{conv.get('events', 0)} independent news events from {conv.get('publishers', 0)} "
                      f"publishers point to this; {len(assumptions)} reasoning step(s) are assumptions; "
                      + ("counterevidence was found and is shown." if counter
                         else "no counterevidence was found in the search.")),
        }
    out["metadata"] = metadata
    return out, _rebuilt_grounding(grounding, keep), notes


def alternative_claims(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Grounding claims for the candidate's cited alternative explanations."""
    revision = _clean(candidate.get("insight_revision_id") or candidate.get("insight_id")) or "INSIGHT"
    claims = []
    for index, item in enumerate(candidate.get("alternative_explanations") or [], start=1):
        if not isinstance(item, Mapping) or not item.get("evidence_ids"):
            continue
        text = _clean(item.get("text") or item.get("explanation"))
        if not text:
            continue
        claims.append({
            "claim_id": f"{revision}-ALT-{index:02d}",
            "alternative_id": _clean(item.get("alternative_id")),
            "claim_text": text,
            "evidence_ids": list(item.get("evidence_ids") or []),
            "epistemic_type": "alternative_explanation",
            "multi_evidence_required": False,
        })
    return claims


def repair_brief(verdict: Mapping[str, Any], maximum: int = 12) -> list[str]:
    """Short, model-facing list of the claims that failed (for a revision)."""
    lines = []
    for item in (verdict.get("failed_claims") or [])[:maximum]:
        part = "; ".join(item.get("unsupported_parts") or [])
        lines.append(
            f"[{item.get('epistemic_type')}: {item.get('grounding_status')}] "
            f"{item.get('claim_text')}" + (f" -- checker: {part}" if part else "")
        )
    return [line[:400] for line in lines]


def promising_rejected(candidate: Mapping[str, Any], verdict: Mapping[str, Any]) -> dict[str, Any] | None:
    """Persist a useful-but-unverified candidate without presenting it as a finding.

    This is deliberately NOT a release path. It exists so a strategically interesting thesis
    does not disappear merely because one causal bridge was overstated. The report labels it
    unverified and records exactly what failed and what evidence would be needed next.
    """
    if verdict.get("passed"):
        return None
    text = _clean(candidate.get("insight") or candidate.get("text"))
    if len(text) < 40:
        return None
    failed = [dict(x) for x in verdict.get("failed_claims") or [] if isinstance(x, Mapping)]
    contradicted = any(_clean(x.get("grounding_status")).casefold() == "contradicted" for x in failed)
    if contradicted:
        return None
    meta = candidate.get("metadata") if isinstance(candidate.get("metadata"), Mapping) else {}
    quality = meta.get("insight_quality_check") if isinstance(meta.get("insight_quality_check"), Mapping) else {}
    quality_signal = (
        quality.get("is_merely_explanatory") is False
        and bool(_clean(quality.get("new_information_created") or quality.get("why_not_obvious")))
    )
    core = int(verdict.get("core_claims") or 0)
    passed = int(verdict.get("core_claims_passed") or 0)
    repairable = bool(verdict.get("repairable"))
    if not (repairable or quality_signal or (core and passed >= max(2, core // 3))):
        return None
    needed = []
    for item in failed:
        claim = _clean(item.get("claim_text"))
        parts = [_clean(x) for x in item.get("unsupported_parts") or [] if _clean(x)]
        needed.append({
            "claim": claim[:500],
            "epistemic_type": _clean(item.get("epistemic_type")),
            "grounding_status": _clean(item.get("grounding_status")),
            "missing_support": parts[:4],
        })
    return {
        "status": "promising_unverified",
        "insight_id": _clean(candidate.get("insight_id")),
        "insight_revision_id": _clean(candidate.get("insight_revision_id")),
        "text": text[:1200],
        "supported_core_claims": passed,
        "core_claims": core,
        "support_ratio": round(passed / core, 3) if core else 0.0,
        "failure_reasons": list(verdict.get("reasons") or []),
        "research_needed": needed[:8],
        "why_promising": _clean(quality.get("new_information_created") or quality.get("why_not_obvious"))[:700],
        "warning": "Not a released finding. One or more factual/mechanism steps did not pass the evidence gate.",
    }


def merge_promising(existing: Sequence[Mapping[str, Any]], record: Mapping[str, Any] | None,
                     maximum: int = 5) -> list[dict[str, Any]]:
    """Keep the best-supported revision of each thesis, then highest-support candidates."""
    rows = [dict(x) for x in existing or [] if isinstance(x, Mapping)]
    if record:
        rec = dict(record)
        key = _clean(rec.get("insight_id")) or _clean(rec.get("text"))[:180]
        replaced = False
        for i, old in enumerate(rows):
            old_key = _clean(old.get("insight_id")) or _clean(old.get("text"))[:180]
            if old_key == key:
                if float(rec.get("support_ratio") or 0) >= float(old.get("support_ratio") or 0):
                    rows[i] = rec
                replaced = True
                break
        if not replaced:
            rows.append(rec)
    rows.sort(key=lambda x: (-float(x.get("support_ratio") or 0), -int(x.get("supported_core_claims") or 0)))
    return rows[:maximum]


# ---------------------------------------------------------------------------
# v1.1: final quality enforcement
# ---------------------------------------------------------------------------


def quality_reasons(quality: Any, ignore: Sequence[str] = ()) -> list[str]:
    """Release-blocking reasons from a final quality assessment ([] = passes).

    ignore: critical dimensions not to enforce for this candidate (only
    counterevidence_quality after a completed search that found nothing).
    """
    if not isinstance(quality, Mapping):
        return ["final_quality_unavailable"]
    reasons = []
    status = _clean(quality.get("quality_status")).casefold()
    if status in BLOCKING_QUALITY_STATUSES:
        reasons.append(f"final_quality_{status}")
    dimensions = quality.get("dimensions") if isinstance(quality.get("dimensions"), Mapping) else {}
    for name in CRITICAL_QUALITY_DIMENSIONS:
        if name in set(ignore):
            continue
        value = dimensions.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value <= CRITICAL_QUALITY_FLOOR:
            reasons.append(f"final_quality_{name}_{int(value)}_of_4")
    return reasons


def quality_brief(quality: Any, maximum: int = 6) -> list[str]:
    """Model-facing description of the quality defects behind quality_reasons."""
    if not isinstance(quality, Mapping):
        return []
    lines = []
    for defect in (quality.get("defects") or [])[:maximum]:
        if isinstance(defect, Mapping) and _clean(defect.get("severity")).casefold() in {"high", "critical"}:
            lines.append(f"[quality: {_clean(defect.get('code'))}] {_clean(defect.get('description'))}"[:400])
    return lines


# ---------------------------------------------------------------------------
# v1.1: everything else the report shows for an insight
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT = None


def split_sentences(text: Any) -> list[str]:
    import re
    global _SENTENCE_SPLIT
    if _SENTENCE_SPLIT is None:
        _SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
    return [_clean(part) for part in _SENTENCE_SPLIT.split(_clean(text)) if _clean(part)]


# v1.6: a connection is checked part by part. Each clause must hold on its
# own, and the relationship it draws between events must rest on evidence
# about more than one of them, not only on one company's description of its
# own product or market (the 14:39 run showed "X and Y raised money ... the
# lack of ROI tools creates demand for Ascerta", where the link was only
# Ascerta's pitch placed next to two unrelated funding rounds).
CONNECTION_LINK_PREFIX = (
    "The relationship this statement draws between the events is stated in, or follows from, "
    "evidence about more than one of those events, and does not rest only on a company's own "
    "description of its product or its market: "
)
_SUBORDINATE_LEAD = ("while ", "whereas ", "although ", "even as ", "as ", "because ", "since ")


def connection_parts(text: Any) -> list[str]:
    """Clauses of a connection statement, each to be checked on its own."""
    import re
    parts: list[str] = []
    for sentence in split_sentences(text):
        pieces = [piece for piece in re.split(r"\s*;\s*|,?\s+(?=(?:while|whereas)\s)", sentence) if _clean(piece)]
        for piece in pieces:
            lowered = piece.casefold()
            if lowered.startswith(_SUBORDINATE_LEAD) and "," in piece:
                # "While A, B": A and B are separate claims (main clause after
                # the last comma that leaves at least five words).
                head, tail = piece, ""
                for match in reversed(list(re.finditer(r",\s+", piece))):
                    candidate = piece[match.end():]
                    if len(candidate.split()) >= 5:
                        head, tail = piece[:match.start()], candidate
                        break
                for item in (head, tail):
                    if len(_clean(item).split()) >= 4:
                        parts.append(_clean(item))
                continue
            if len(_clean(piece).split()) >= 4:
                parts.append(_clean(piece))
    return parts or ([_clean(text)] if _clean(text) else [])


def _is_question(sentence: str) -> bool:
    return sentence.rstrip().endswith("?")


# Bars for display material. Observations are facts; everything analytical
# must be stated in, or follow directly from, its cited evidence.
DISPLAY_FACT = frozenset({"supported"})
DISPLAY_ANALYSIS = frozenset({"supported", "inference_supported"})
# v1.3: no shown item survives on "partially_supported" (some material part
# absent, too strong or mismatched). Historical cases and counter-cases use
# the same two bars: the case itself is a fact; the comparison, the
# difference, "why it matters" and the weakens-the-thesis role are analysis.
DISPLAY_COUNTER_FACT = DISPLAY_FACT
DISPLAY_COUNTER_ANALYSIS = DISPLAY_ANALYSIS
LABELLED_AS_SUPPORTED = frozenset({"confirmed", "supported_inference"})


def _metadata(candidate: Mapping[str, Any]) -> dict[str, Any]:
    value = candidate.get("metadata")
    return value if isinstance(value, dict) else {}


def _derivation(candidate: Mapping[str, Any]) -> dict[str, Any]:
    value = _metadata(candidate).get("insight_derivation")
    return value if isinstance(value, dict) else {}


def _ids(value: Any) -> list[str]:
    return _unique(value if isinstance(value, (list, tuple)) else [])


# ---------------------------------------------------------------------------
# v6.23: claims-and-evidence table (as Consensus shows a claim with the papers
# that support or dispute it, and AlphaSense cites the exact snippet behind
# each statement). Built from the released grounding record only.
# ---------------------------------------------------------------------------

EVIDENCE_TABLE_ROLES = {"fact": "Fact", "mechanism": "Reasoning step", "hidden_insight": "Conclusion",
                        "second_order_effect": "Consequence"}
EVIDENCE_TABLE_STATUS = {"supported": "Stated by the sources", "inference_supported": "Follows from the sources"}
MAX_TABLE_ROWS = 14
MAX_SOURCES_PER_ROW = 3


def evidence_table(grounding: Any, evidence: Sequence[Mapping[str, Any]],
                   assumptions: Sequence[Mapping[str, Any]] = (), counter_ids: Sequence[str] = (),
                   reference: Any = None) -> dict[str, Any]:
    """Rows: claim | role | status | sources (publisher, date, exact quote, link). Plus records against.

    reference(source_id) -> {"publisher","url","published_at"} (the article store), optional.
    """
    by_id = {_clean(r.get("evidence_id")): r for r in evidence or [] if isinstance(r, Mapping)}
    cache: dict[str, dict[str, str]] = {}

    def source(eid: str) -> dict[str, str] | None:
        record = by_id.get(eid)
        if record is None:
            return None
        sid = _clean(record.get("source_id"))
        ref = {}
        if callable(reference) and sid and not sid.startswith("COMPUTED"):
            if sid not in cache:
                try:
                    cache[sid] = dict(reference(sid) or {})
                except Exception:
                    cache[sid] = {}
            ref = cache[sid]
        url = _clean(record.get("source_url") or ref.get("url"))
        gap = _clean(record.get("gap_type"))
        publisher = ("Computed pattern across reports" if gap == "pattern"
                     else _clean(ref.get("publisher")) or (re.sub(r"^www\.", "", url.split("/")[2]) if url.count("/") >= 2 else sid))
        return {"evidence_id": eid, "publisher": publisher, "url": url,
                "date": _clean(record.get("published_at") or ref.get("published_at"))[:10],
                "quote": _clean(record.get("source_excerpt") or record.get("text") or record.get("claim"))[:240],
                "kind": "computed" if gap == "pattern" else "context" if record.get("analytical_context_only") else "news"}

    rows, used, publishers = [], set(), set()
    for claim in _claims(grounding) or []:
        kind = _type(claim)
        status = _status(claim)
        if kind not in EVIDENCE_TABLE_ROLES or status not in EVIDENCE_TABLE_STATUS:
            continue
        sources = []
        for eid in _unique(claim.get("supporting_evidence_ids") or claim.get("evidence_ids") or []):
            item = source(_clean(eid))
            if item and len(sources) < MAX_SOURCES_PER_ROW:
                sources.append(item)
                used.add(item["evidence_id"])
                if item["kind"] != "computed":
                    publishers.add(item["publisher"])
        rows.append({"claim": _clean(claim.get("claim_text"))[:400], "role": EVIDENCE_TABLE_ROLES[kind],
                     "status": EVIDENCE_TABLE_STATUS[status], "sources": sources})
    for item in assumptions or []:
        text = _clean(item.get("text") if isinstance(item, Mapping) else item)
        if text:
            rows.append({"claim": text[:400], "role": "Reasoning step",
                         "status": "Assumption (no source states it)", "sources": []})
    order = {"Fact": 0, "Reasoning step": 1, "Conclusion": 2, "Consequence": 3}
    rows.sort(key=lambda r: order.get(r["role"], 9))
    against = [item for item in (source(_clean(e)) for e in _unique(counter_ids or [])) if item]
    return {
        "rows": rows[:MAX_TABLE_ROWS], "against": against[:6],
        "summary": {"records_for": len(used), "publishers": len(publishers), "records_against": len(against),
                    "stated": sum(1 for r in rows if r["status"] == EVIDENCE_TABLE_STATUS["supported"]),
                    "inferred": sum(1 for r in rows if r["status"] == EVIDENCE_TABLE_STATUS["inference_supported"]),
                    "assumptions": sum(1 for r in rows if r["status"].startswith("Assumption"))},
    }


LEVERAGE_BRIEF = (
    "[leverage + incentives] A hidden insight must be a leverage-and-incentive judgment: fill leverage_analysis - a named "
    "holder controls something (resource, access, information, rule position, contract, deadline) that a named dependent "
    "needs, with the records that state it; the holder's incentive from a cited rule, penalty, cost or revenue (and the "
    "dependent's if different); and the predicted move those incentives make rational, which the headlines do not say. "
    "The insight text leads with the concrete consequence (a date, delay, number, amount or named document) and states "
    "that move after it, in plain words. Holder and dependent are NAMED organisations, agencies or people from "
    "the facts (e.g. 'OpenAI', 'the EU AI Office'), never a category ('investors', 'AI firms', 'regulators').")


def without_unbacked_leverage(grounding: Any, verdict: Mapping[str, Any]) -> dict[str, Any] | None:
    """v10.1 (15 Nov 2026): when the ONLY failed claims are the lines the app wrote from the
    leverage fields, the grounding without those lines (to be checked again); otherwise None.
    In the 14 Nov run three of four candidates failed on one such line alone ("Donald Trump
    controls or holds a voluntary accord") with 92-93% of their other claims backed, and the
    report released nothing original. Verify the premises strictly; drop the unbacked add-on."""
    failed = [f for f in verdict.get("failed_claims") or [] if isinstance(f, Mapping)]
    if not failed or verdict.get("passed") or not isinstance(grounding, Mapping):
        return None
    if not all(_clean(f.get("claim_id")).upper().endswith(LEVERAGE_CLAIM_SUFFIXES) for f in failed):
        return None
    claims = grounding.get("claims")
    if not isinstance(claims, list):
        return None
    kept = [c for c in claims if not (isinstance(c, Mapping)
                                      and _clean(c.get("claim_id")).upper().endswith(LEVERAGE_CLAIM_SUFFIXES))]
    return {**grounding, "claims": kept}


def leverage_reasons(candidate: Mapping[str, Any]) -> list[str]:
    """v6.26: the product's bar (user, 2026-10-10): 'see the leverage for a thing and add incentives'.
    A hidden insight needs holder, what it controls, who depends on it, the holder's
    incentive and a predicted move. (Whether the sources state them is checked as claims.)
    v10.1: once the leverage lines were checked and the sources did not back them, the insight may
    be shown without leverage, labelled (metadata leverage_withheld); it is not asked again."""
    if _metadata(candidate).get("leverage_withheld"):
        return []
    lev = candidate.get("leverage_analysis") or _metadata(candidate).get("leverage_analysis")
    lev = lev if isinstance(lev, Mapping) else {}
    missing = [k for k in ("holder", "controls", "dependent", "holder_incentive", "predicted_move") if not _clean(lev.get(k))]
    if missing:
        return ["no_leverage_incentive_structure:" + ",".join(missing)]
    unnamed = [k for k in ("holder", "dependent") if not named_party(lev.get(k))]
    return ["leverage_party_not_named:" + ",".join(unnamed)] if unnamed else []


_GENERIC_LEAD = re.compile(r"^(a|an|all|some|most|many|several|other|various)\b", re.I)


def named_party(value: Any) -> bool:
    """v3.0: an organisation, agency or person, not a category. Uses the pattern grid's test
    (generic groups such as "investors", "AI firms" are not names), but a capital letter on the
    FIRST word of a phrase longer than three words is sentence case, not a name: "Institutional
    capital providers seeking AI exposure" is a category; "Microsoft", "EU regulators", "Simons
    Foundation-supported researchers" are named."""
    from services.pattern_grid import is_named_party
    text = _clean(re.sub(r"\((?:e\.g\.|such as|including)[^)]*\)", "", _clean(value)))
    text = re.sub(r"^the\s+", "", text, flags=re.I)
    if not text or _GENERIC_LEAD.match(text) or not is_named_party(text):
        return False
    words = text.split()
    if len(words) <= 3:
        return True
    rest = " ".join(words[1:])
    return is_named_party(rest)


SO_WHAT_BRIEF = {
    "no_consequence_so_what": (
        "[so what] Every consequence was removed or never stated, so the finding only places facts side by "
        "side ('X while Y'). Keep a consequence: what this forces or changes, for which named company, agency "
        "or group, and when. If no record states that step, keep it as a short mechanism step (it may stand "
        "as a labelled assumption) - never delete the consequence to pass the check."),
    "constraint_insight_needs_checkable_signal": (
        "[so what] A conclusion built on a rule, deadline, dependency or number must say how it can be "
        "checked: a forecast question with a deadline, or a concrete watch signal."),
}


def consequence_parts(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """v6.21.8: the parts of a finding that say what follows from it (the 'so what')."""
    contract = candidate.get("forecast_contract")
    if not isinstance(contract, Mapping):
        contract = _metadata(candidate).get("forecast_contract")
    contract = contract if isinstance(contract, Mapping) else {}
    derivation = _derivation(candidate)
    return {
        "second_order_effect": _clean(candidate.get("second_order_effect")),
        "forecast_question": _clean(contract.get("question")),
        "forecast_deadline": _clean(contract.get("deadline")),
        "watch_signals": [_clean(x) for x in (derivation.get("watch_signals") or candidate.get("watch_signals") or [])
                          if _clean(x)],
    }


def so_what_reasons(candidate: Mapping[str, Any], route: str = "") -> list[str]:
    """v6.21.8 'so what' test (intelligence writing standard: a fact without what it
    changes, for whom, is noise). Applied to the copy the reader would see.

    - every hidden insight keeps at least one consequence (second-order effect or
      forecast question);
    - a constraint-route insight (one development + a cited rule/number) also needs
      a checkable signal: a dated forecast or a watch signal.
    """
    parts = consequence_parts(candidate)
    reasons = []
    if not parts["second_order_effect"] and not parts["forecast_question"]:
        reasons.append("no_consequence_so_what")
    if route == "constraint" and not (
            (parts["forecast_question"] and parts["forecast_deadline"]) or parts["watch_signals"]):
        reasons.append("constraint_insight_needs_checkable_signal")
    return reasons


def so_what_brief(reasons: Sequence[str]) -> list[str]:
    out = [SO_WHAT_BRIEF[r] for r in reasons if r in SO_WHAT_BRIEF]
    if any(str(r).startswith(("no_leverage_incentive_structure", "leverage_party_not_named")) for r in reasons):
        out.append(LEVERAGE_BRIEF)  # v6.26
    return out


def display_claims(candidate: Mapping[str, Any], insight_evidence_ids: Sequence[str],
                   connections: Sequence[Mapping[str, Any]] = (),
                   counter_ids_to_check: Sequence[str] = ()) -> list[dict[str, Any]]:
    """Grounding claims for every analytical sentence/item the report shows.

    Each claim carries "_target" (where it lives) and "_bar" (passing
    statuses); sanitize_display() uses them. Strip both keys before sending
    the claims to the grounding service (it ignores unknown keys anyway).
    """
    revision = _clean(candidate.get("insight_revision_id") or candidate.get("insight_id")) or "INSIGHT"
    all_ids = _unique(insight_evidence_ids)
    claims: list[dict[str, Any]] = []

    def add(target, text, ids, kind, bar, multi=False):
        claims.append({
            "claim_id": f"{revision}-SHOW-{len(claims) + 1:02d}", "claim_text": text,
            "evidence_ids": list(ids or []), "epistemic_type": kind,
            "multi_evidence_required": multi, "_target": target, "_bar": sorted(bar),
        })

    def sentences(target, text, ids, kind, bar, multi=False):
        import re as _re
        for index, sentence in enumerate(split_sentences(text)):
            # v2.0.1: records cited inline in the sentence are checked too.
            sentence_ids = _unique(list(ids or []) + _re.findall(r"\bEVID-[A-Z0-9]{8,40}\b", sentence))
            if _is_question(sentence):
                # A question can carry a false premise ("Why did the August
                # model cause the July intrusion?"): check what it presupposes.
                add((*target, index), "The facts this question takes for granted are stated in, or follow "
                    f"directly from, the evidence: {sentence}", sentence_ids, "connection", DISPLAY_ANALYSIS)
            else:
                add((*target, index), sentence, sentence_ids, kind, bar, multi)

    meta = _metadata(candidate)
    sentences(("meta", "novel_proposition"), meta.get("novel_proposition"), all_ids, "hidden_insight", DISPLAY_ANALYSIS, True)
    sentences(("meta", "triggering_anomaly"), meta.get("triggering_anomaly"), all_ids, "connection", DISPLAY_ANALYSIS)
    check = meta.get("insight_quality_check") if isinstance(meta.get("insight_quality_check"), Mapping) else {}
    for key in ("why_not_obvious", "new_information_created"):
        sentences(("quality_check", key), check.get(key), all_ids, "hidden_insight", DISPLAY_ANALYSIS)
    derivation = _derivation(candidate)
    for index, item in enumerate(derivation.get("observations") or []):
        if isinstance(item, Mapping) and _clean(item.get("text")):
            add(("derivation_item", "observations", index), _clean(item.get("text")),
                _ids(item.get("evidence_ids")) or all_ids, "fact", DISPLAY_FACT)
    mechanism_ids = _ids(derivation.get("mechanism_support_evidence_ids")) or all_ids
    sentences(("derivation_text", "mechanism"), derivation.get("mechanism"), mechanism_ids, "mechanism", DISPLAY_ANALYSIS, True)
    sentences(("derivation_text", "discrimination"), derivation.get("discrimination"), all_ids, "mechanism", DISPLAY_ANALYSIS)
    sentences(("derivation_text", "anomaly_or_question"), derivation.get("anomaly_or_question"), all_ids, "connection", DISPLAY_ANALYSIS)
    for index, item in enumerate(derivation.get("historical_or_analogical_support") or []):
        if isinstance(item, Mapping) and _clean(item.get("case")):
            # A historical case must be in the cited evidence; uncited history is not shown.
            # Every shown part shares the item's target: any failure removes the item.
            target = ("derivation_item", "historical_or_analogical_support", index)
            ids = _ids(item.get("evidence_ids"))
            add(target, _clean(item.get("case")), ids, "fact", DISPLAY_COUNTER_FACT)
            for key in ("mechanism_match", "important_difference"):
                for sentence in split_sentences(item.get(key)):
                    add(target, sentence, ids, "connection", DISPLAY_COUNTER_ANALYSIS)
    for index, item in enumerate(derivation.get("counter_cases") or []):
        if isinstance(item, Mapping) and _clean(item.get("case")):
            ids = _ids(item.get("evidence_ids"))
            add(("derivation_item", "counter_cases", index), _clean(item.get("case")), ids,
                "fact", DISPLAY_COUNTER_FACT)
            thesis = _clean(candidate.get("insight") or candidate.get("text"))
            # Role check: does this evidence actually cut against the thesis?
            add(("derivation_item", "counter_cases", index),
                f"This evidence weakens or cuts against the following thesis: {thesis}", ids,
                "counterevidence", DISPLAY_COUNTER_ANALYSIS)
            # The shown "why it matters" is checked too; if it fails, the item goes.
            for sentence in split_sentences(item.get("why_it_matters")):
                add(("derivation_item", "counter_cases", index), sentence, ids, "connection", DISPLAY_COUNTER_ANALYSIS)
    for index, item in enumerate(derivation.get("consequence_chain") or []):
        if (isinstance(item, Mapping) and _clean(item.get("text"))
                and _clean(item.get("epistemic_level")).casefold() in LABELLED_AS_SUPPORTED):
            add(("consequence", index), _clean(item.get("text")),
                _ids(item.get("evidence_ids")) or all_ids, "second_order_effect", DISPLAY_ANALYSIS)
    likelihood = meta.get("likelihood_assessment") if isinstance(meta.get("likelihood_assessment"), Mapping) else {}
    sentences(("likelihood", "basis"), likelihood.get("basis"), all_ids, "connection", DISPLAY_ANALYSIS)
    for key in ("why_it_matters", "connection"):
        sentences(("top", key), candidate.get(key), all_ids, "connection", DISPLAY_ANALYSIS)
    for index, item in enumerate(connections or []):
        if not isinstance(item, Mapping):
            continue
        text = _clean(item.get("statement") or item.get("text") or item.get("connection"))
        if text:
            ids = _ids(item.get("evidence_ids"))
            for part in connection_parts(text):
                add(("strategic_connection", index), part, ids, "connection", DISPLAY_ANALYSIS)
            add(("strategic_connection", index), CONNECTION_LINK_PREFIX + text, ids,
                "connection", DISPLAY_ANALYSIS, True)
    thesis = _clean(candidate.get("insight") or candidate.get("text"))
    for evidence_id in _unique(counter_ids_to_check):
        # Model-chosen counterevidence must actually cut against the thesis.
        add(("counter_id", evidence_id), f"This evidence weakens or cuts against the following thesis: {thesis}",
            [evidence_id], "counterevidence", DISPLAY_COUNTER_ANALYSIS)
    return claims


def sanitize_display(candidate: Mapping[str, Any], claims: Sequence[Mapping[str, Any]],
                     statuses: Mapping[str, str] | None,
                     connections: Sequence[Mapping[str, Any]] = ()) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Remove or relabel shown material whose grounding did not reach its bar.

    statuses: {claim_id: grounding status}, or None when grounding could not
    run, in which case every checked item fails (fail closed).
    Returns (candidate copy, kept connections, notes).
    """
    out = copy.deepcopy(dict(candidate))
    meta = out.get("metadata")
    if not isinstance(meta, dict):
        meta = {}
        out["metadata"] = meta
    failed_targets: dict[tuple, bool] = {}
    for claim in claims:
        status = _clean((statuses or {}).get(claim["claim_id"])).casefold() if statuses is not None else ""
        ok = status in set(claim["_bar"])
        target = tuple(claim["_target"])
        failed_targets[target] = failed_targets.get(target, False) or not ok
    failed = {target for target, bad in failed_targets.items() if bad}
    notes: dict[str, Any] = {"removed": [], "relabelled_hypothesis": [], "connections_removed": 0,
                             "grounding_ran": statuses is not None}

    def keep_sentences(text, prefix):
        parts = split_sentences(text)
        kept = [s for i, s in enumerate(parts) if (*prefix, i) not in failed]
        for i, sentence in enumerate(parts):
            if (*prefix, i) in failed:
                notes["removed"].append(sentence[:300])
        return " ".join(kept)

    for key in ("novel_proposition", "triggering_anomaly"):
        if _clean(meta.get(key)):
            meta[key] = keep_sentences(meta.get(key), ("meta", key))
    if isinstance(meta.get("insight_quality_check"), dict):
        check = meta["insight_quality_check"]
        for key in ("why_not_obvious", "new_information_created"):
            if _clean(check.get(key)):
                check[key] = keep_sentences(check.get(key), ("quality_check", key))
    derivation = meta.get("insight_derivation") if isinstance(meta.get("insight_derivation"), dict) else None
    if derivation is not None:
        for key in ("mechanism", "discrimination", "anomaly_or_question"):
            if _clean(derivation.get(key)):
                derivation[key] = keep_sentences(derivation.get(key), ("derivation_text", key))
        for key in ("observations", "historical_or_analogical_support", "counter_cases"):
            items = derivation.get(key)
            if isinstance(items, list):
                kept = []
                for index, item in enumerate(items):
                    if ("derivation_item", key, index) in failed:
                        notes["removed"].append(_clean(
                            item.get("text") or item.get("case") if isinstance(item, Mapping) else item)[:300])
                        continue
                    kept.append(item)
                derivation[key] = kept
        chain = derivation.get("consequence_chain")
        if isinstance(chain, list):
            for index, item in enumerate(chain):
                if ("consequence", index) in failed and isinstance(item, dict):
                    item["epistemic_level"] = "hypothesis"
                    item["relabelled_by"] = VERSION
                    notes["relabelled_hypothesis"].append(_clean(item.get("text"))[:300])
    likelihood = meta.get("likelihood_assessment") if isinstance(meta.get("likelihood_assessment"), dict) else None
    if likelihood is not None and _clean(likelihood.get("basis")):
        likelihood["basis"] = keep_sentences(likelihood.get("basis"), ("likelihood", "basis"))
    for key in ("why_it_matters", "connection"):
        if _clean(out.get(key)):
            out[key] = keep_sentences(out.get(key), ("top", key))
    for key in ("counterevidence_ids", "counter_incentive_evidence_ids"):
        if isinstance(out.get(key), list):
            kept_ids = [eid for eid in out[key] if ("counter_id", _clean(eid)) not in failed]
            notes.setdefault("counterevidence_removed", []).extend(
                _clean(eid) for eid in out[key] if ("counter_id", _clean(eid)) in failed)
            out[key] = kept_ids
    kept_connections = []
    for index, item in enumerate(connections or []):
        if ("strategic_connection", index) in failed:
            notes["connections_removed"] += 1
            continue
        kept_connections.append(copy.deepcopy(dict(item)) if isinstance(item, Mapping) else item)
    gate_meta = meta.get("claim_support_gate") if isinstance(meta.get("claim_support_gate"), dict) else {}
    gate_meta.update({"display_checked": len(claims), "display_removed": len(notes["removed"]),
                      "display_relabelled": len(notes["relabelled_hypothesis"]),
                      "display_grounding_ran": statuses is not None})
    meta["claim_support_gate"] = gate_meta
    return out, kept_connections, notes


def forecast_leak_claim(candidate: Mapping[str, Any], evidence_ids: Sequence[str]) -> dict[str, Any] | None:
    """Claim that the forecast's outcome is already reported (None if no forecast)."""
    if _clean(candidate.get("insight_type")).casefold() != "prospective":
        return None
    contract = candidate.get("forecast_contract")
    if not isinstance(contract, Mapping):
        contract = _metadata(candidate).get("forecast_contract")
    question = _clean(contract.get("question")) if isinstance(contract, Mapping) else ""
    if not question:
        return None
    revision = _clean(candidate.get("insight_revision_id") or candidate.get("insight_id")) or "INSIGHT"
    return {
        "claim_id": f"{revision}-FORECAST-LEAK",
        "claim_text": ("The outcome asked about by this forecast question has already happened, as "
                       f"reported in the evidence: {question}"),
        "evidence_ids": _unique(evidence_ids), "epistemic_type": "fact",
        "multi_evidence_required": False,
    }


# ---------------------------------------------------------------------------
# v1.4: hidden insight or verified connection
# ---------------------------------------------------------------------------


def restates_sources(grounding: Any) -> bool:
    """True when every conclusion sentence is stated outright by the sources.

    The claim checker labels a conclusion "supported" when the evidence states
    it and "inference_supported" when it follows from two or more records
    without being stated. A hidden insight must contain at least one inferred
    conclusion; a conclusion the sources already state is a verified
    connection, not a hidden insight. (A proxy for non-obviousness: it tells
    inference from restatement, not how surprising the inference is.)
    """
    claims = _claims(grounding) or []
    conclusions = [_status(c) for c in claims if _type(c) == "hidden_insight"]
    if _gate_mode() != "legacy":
        # 13 Nov 2026 (two-lane gate): a forward conclusion the sources do not state comes back
        # "unsupported" or "partially_supported" - a labelled judgment, the opposite of a
        # restatement. Only a conclusion the sources state outright ("supported") restates them.
        from services import claim_status
        return bool(conclusions) and all(status in claim_status.JUDGMENT_RESTATES_SOURCES for status in conclusions)
    return bool(conclusions) and "inference_supported" not in conclusions


def verified_facts(grounding: Any) -> list[dict[str, Any]]:
    """13 Nov 2026: the factual premises of a candidate that their cited records back.

    Used for the explanatory fallback: when no candidate is released, the reader still gets
    what the sources establish (ICD 203: say what is known, separately from what is judged).
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for claim in _claims(grounding) or []:
        if _type(claim) not in FACT_LANE_TYPES or _status(claim) not in ("supported", "inference_supported"):
            continue
        text = _clean(claim.get("claim_text"))
        ids = [_clean(eid) for eid in claim.get("supporting_evidence_ids") or [] if _clean(eid)]
        if not text or not ids or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        out.append({"text": text[:460], "evidence_ids": ids[:6], "grounding_status": _status(claim)})
    return out


# Only one failure turns a candidate into a verified connection: it passed
# every check but its conclusion restates the sources (the orchestrator keeps
# such a candidate as its fallback). Quality, forecast, grounding or research
# failures never do.


def counterevidence_exemption(research_status: str, candidate: Mapping[str, Any]) -> tuple[str, ...]:
    """Dimensions to ignore: counterevidence_quality after a completed search that
    found nothing, or (v6.25) a search that could not be completed - the insight
    is then shown with that disclosed - with no counterevidence attached."""
    status = _clean(research_status)
    if status and status != "completed_found" and not _unique(candidate.get("counterevidence_ids") or []):
        return ("counterevidence_quality",)
    return ()


def as_hidden_insight(candidate: Mapping[str, Any], reason: str) -> dict[str, Any]:
    """v6.21.7: copy relabelled as a hidden insight (the caller re-applies the
    hidden-insight release rules and the non-obviousness test)."""
    out = copy.deepcopy(dict(candidate))
    previous = out.get("finding_tier") or ""
    out["finding_tier"] = "hidden_insight"
    out["publish_hidden_insight"] = True
    meta = out.get("metadata")
    if not isinstance(meta, dict):
        meta = {}
        out["metadata"] = meta
    previous = previous or meta.get("finding_tier") or ""
    meta["finding_tier"] = "hidden_insight"
    meta["finding_tier_reason"] = reason
    gate_meta = meta.get("claim_support_gate") if isinstance(meta.get("claim_support_gate"), dict) else {}
    gate_meta.update({"promoted_from_tier": previous})
    meta["claim_support_gate"] = gate_meta
    return out


def as_verified_connection(candidate: Mapping[str, Any], reason: str) -> dict[str, Any]:
    """Released copy relabelled from hidden insight to verified connection."""
    out = copy.deepcopy(dict(candidate))
    out["finding_tier"] = "strategic_connection"
    out["publish_hidden_insight"] = False
    meta = out.get("metadata")
    if not isinstance(meta, dict):
        meta = {}
        out["metadata"] = meta
    meta["finding_tier"] = "strategic_connection"
    meta["finding_tier_reason"] = reason
    gate_meta = meta.get("claim_support_gate") if isinstance(meta.get("claim_support_gate"), dict) else {}
    gate_meta.update({"released_as": "verified_connection", "downgrade_reason": reason, "forecast_withheld": True})
    meta["claim_support_gate"] = gate_meta
    return out
