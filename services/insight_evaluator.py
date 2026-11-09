"""Insight evaluator v1.2 â€” leverage-first, evidence-aware ranking.

Product objective:
    development -> dependency -> leverage/control point -> incentives -> constraint
    -> strategic move -> countermove -> observable watch signal.

Important rules:
- Evidence eligibility comes first. Unsupported candidates are never promoted by a score.
- Expert corroboration is metadata, not a reason to downgrade a useful insight.
- A routine compliance/regulatory/coordination action is not a leverage decision unless
  the dependency and control point are explicit.
- Pairwise order flips are uncertainty, not confidence.
- Repairable rejected candidates may be surfaced separately as unverified hypotheses;
  they are never mixed into the evidence-eligible ranking.
"""

from __future__ import annotations

import itertools
import json
import math
import re
from typing import Any, Callable, Mapping, Sequence

VERSION = "insight-evaluator-v1.7-substance-audit"
MAX_ITEMS = 14
RUBRIC_BATCH = 4
PAIRWISE_TOP = 5
TOP_SHOWN = 3

RUBRIC = (
    ("A1", "decision_relevance", "Would knowing this materially change what a reader should monitor?"),
    ("A2", "decision_relevance", "Could it affect an actual decision, risk, opportunity or priority for a named actor?"),
    ("B1", "mechanism", "Does it explain WHY A leads to B, rather than placing A and B side by side?"),
    ("B2", "mechanism", "Is the causal bridge supported by the supplied evidence, beyond merely being labelled an assumption?"),
    ("C1", "leverage_incentives", "Does it identify who holds a control point, who depends on it, and what the dependency is?"),
    ("C2", "leverage_incentives", "Does it explain the relevant actors' incentives, constraints, alternatives or likely responses?"),
    ("D1", "predictive_specificity", "Does it name a concrete next move or effect, not merely 'things may change'?"),
    ("D2", "predictive_specificity", "Does it give a timeframe, threshold, signpost, watch item, countermove or falsifier?"),
    ("E1", "informational_lift", "Would this materially change a knowledgeable reader's mental model, rather than merely add another implication?"),
    ("E2", "informational_lift", "Does it reveal an overlooked constraint, control point, incentive, anomaly, feedback loop or consequence that the individual sources do not make explicit?"),
    ("F1", "actor_specificity", "Does the conclusion materially depend on these particular organisations or events?"),
    ("F2", "actor_specificity", "Would it become false or substantially weaker if the organisation names were swapped for similar actors?"),
)
DIMENSIONS = (
    "decision_relevance", "mechanism", "leverage_incentives", "predictive_specificity",
    "informational_lift", "actor_specificity",
)
DIMENSION_NAMES = {
    "decision_relevance": "decision relevance",
    "mechanism": "mechanism",
    "leverage_incentives": "leverage and incentives",
    "predictive_specificity": "specificity",
    "informational_lift": "adds to the sources",
    "actor_specificity": "specific to these actors",
}

TYPE_LABELS = {
    "leverage_decision": "Strategic decision from leverage",
    "strategic_action": "Strategic action",
    "cross_event": "Cross-event synthesis",
    "derived": "Derived strategic insight",
    "analysis": "Analysis",
    "widely_expected": "Widely expected",
}
PROVENANCE_LABELS = TYPE_LABELS | {
    "expert_backed": "Expert-backed",
    "stated_by_sources": "Stated by its sources",
}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _json(reply: Any) -> Any:
    text = str(reply or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.S)
        try:
            return json.loads(match.group(0)) if match else {}
        except Exception:
            return {}


def _bool(value: Any) -> bool:
    return value is True or _clean(value).casefold() in {"true", "yes", "y", "1"}


def band(score: int, item: Mapping[str, Any] | None = None) -> str:
    """Strong means both a high score and real strategic lift, not just actionability."""
    if score >= 10:
        if item is None or _clean(item.get("strategic_type")) in {"leverage_decision", "cross_event", "derived"}:
            return "strong strategic insight"
        return "useful insight"
    if score >= 7:
        return "useful insight"
    if score >= 4:
        return "supporting observation"
    return "weak or generic"


def _leverage_contract(actor: Mapping[str, Any]) -> dict[str, Any]:
    """Deterministic leverage contract. No dependency/control point => no leverage label."""
    lev = actor.get("leverage") if isinstance(actor.get("leverage"), Mapping) else {}
    powers = [p for p in actor.get("powers") or [] if isinstance(p, Mapping)]
    p = powers[0] if powers else lev
    holder = _clean(p.get("holder") or lev.get("holder") or actor.get("actor"))
    counterparty = _clean(p.get("counterparty") or lev.get("counterparty"))
    dependency = _clean(p.get("dependency") or lev.get("dependency"))
    control_point = _clean(p.get("control_point") or lev.get("control_point"))
    outside_option = _clean(p.get("outside_option") or lev.get("outside_option"))
    holder_dependence = _clean(p.get("holder_dependence") or lev.get("holder_dependence"))
    benefit = _clean(p.get("benefit") or lev.get("benefit") or lev.get("text"))
    # v6.36.4 (resource-dependence theory): leverage also needs the counterparty's alternatives and the
    # holder's own (smaller) dependence; without them it is a dependency, not a leverage decision.
    from services.leverage_validation import alternatives_known
    complete = bool(holder and counterparty and dependency and control_point and holder_dependence
                    and alternatives_known(outside_option) and p.get("leverage_test") == "passed")
    return {
        "holder": holder,
        "counterparty": counterparty,
        "dependency": dependency,
        "control_point": control_point,
        "outside_option": outside_option,
        "holder_dependence": holder_dependence,
        "benefit": benefit,
        "complete": complete,
    }


def _hidden_type(item: Mapping[str, Any]) -> str:
    route = _clean(item.get("insight_route") or (item.get("metadata") or {}).get("insight_route")).casefold()
    lev = item.get("leverage_analysis") if isinstance(item.get("leverage_analysis"), Mapping) else {}
    holder = _clean(lev.get("holder"))
    dependent = _clean(lev.get("dependent") or lev.get("counterparty"))
    dependency = _clean(lev.get("dependency") or lev.get("what_dependent_needs"))
    control = _clean(lev.get("control_point") or lev.get("controlled_asset") or lev.get("controls"))
    move = _clean(lev.get("predicted_move"))
    if holder and dependent and dependency and control and move and lev.get("leverage_test") == "passed":
        return "leverage_decision"
    if route == "cross_development":
        return "cross_event"
    return "derived"


def _expert_corroboration(item: Mapping[str, Any]) -> list[str]:
    backed = item.get("source_backed") if isinstance(item.get("source_backed"), Mapping) else {}
    return [
        _clean(c.get("publisher"))
        for c in backed.get("expert_citations") or []
        if isinstance(c, Mapping) and _clean(c.get("publisher"))
    ][:3]


def _evidence_texts(*values: Any, limit: int = 8) -> list[str]:
    out: list[str] = []
    for value in values:
        seq = value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else [value]
        for item in seq or []:
            if isinstance(item, Mapping):
                text = _clean(item.get("text") or item.get("claim") or item.get("statement"))
            else:
                text = _clean(item)
            if text and text not in out:
                out.append(text[:500])
            if len(out) >= limit:
                return out
    return out


def provenance_label(item: Mapping[str, Any]) -> str:
    stype = _clean(item.get("strategic_type") or item.get("provenance"))
    main = TYPE_LABELS.get(stype, PROVENANCE_LABELS.get(stype, stype or "Analysis"))
    suffix: list[str] = []
    if item.get("corroboration"):
        suffix.append("Expert corroborated")
    if bool(item.get("widely_expected")):
        suffix.append("Widely expected")
    return main + (" Â· " + " Â· ".join(suffix) if suffix else "")


def candidates(report: Mapping[str, Any], evidence_text: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Only evidence-eligible, shown analytical items. Rejected hypotheses stay separate.

    v1.5: evidence_text (evidence_id -> what the record states) lets items that cite only
    evidence IDs (implications) show the judge their evidence too."""
    evidence_text = evidence_text or {}
    out: list[dict[str, Any]] = []

    def cited_text(citations: Any) -> list[str]:
        return [evidence_text[_clean(c.get("evidence_id"))] for c in citations or []
                if isinstance(c, Mapping) and _clean(c.get("evidence_id")) in evidence_text]

    def add(
        kind: str,
        ref: str,
        text: str,
        strategic_type: str,
        *,
        corroboration: Sequence[str] = (),
        detail: str = "",
        evidence: Sequence[str] = (),
        leverage_complete: bool = False,
        widely_expected: bool = False,
        derivation: str = "derived",
    ) -> None:
        text = _clean(text)
        if len(text) < 25 or text in {c["text"] for c in out}:
            return
        out.append({
            "id": f"I{len(out) + 1}",
            "kind": kind,
            "ref": ref,
            "text": text[:900],
            "detail": _clean(detail)[:900],
            "strategic_type": strategic_type,
            # compatibility for existing consumers
            "type": strategic_type,
            "provenance": strategic_type,
            "derivation": derivation,
            "corroboration": [s for s in corroboration if s][:3],
            "evidence": [e for e in evidence if e][:8],
            "leverage_complete": bool(leverage_complete),
            "widely_expected": bool(widely_expected),
        })

    # v6.32: Strategic Hypothesis Lab candidates are already grounded before they
    # enter the evaluator. Put them first so a mature structural thesis is not
    # crowded out by routine actor moves when MAX_ITEMS is reached.
    lab = report.get("strategic_hypotheses") if isinstance(report.get("strategic_hypotheses"), Mapping) else {}
    for n, item in enumerate(lab.get("verified") or []):
        if not isinstance(item, Mapping):
            continue
        leverage = item.get("leverage") if isinstance(item.get("leverage"), Mapping) else {}
        from services.leverage_validation import alternatives_known
        complete = (item.get("leverage_test") == "passed"
                    and alternatives_known(leverage.get("outside_option")))
        stype = "leverage_decision" if complete else "cross_event"
        detail = " ".join(x for x in (
            f"Novel proposition: {_clean(item.get('novel_proposition'))}." if _clean(item.get("novel_proposition")) else "",
            f"Why non-obvious: {_clean(item.get('why_not_obvious'))}." if _clean(item.get("why_not_obvious")) else "",
            f"Mechanism: {_clean(item.get('mechanism'))}." if _clean(item.get("mechanism")) else "",
            f"Alternative: {_clean(item.get('alternative_explanation'))}." if _clean(item.get("alternative_explanation")) else "",
            f"Countermove: {_clean(item.get('likely_countermove'))}." if _clean(item.get("likely_countermove")) else "",
            f"Watch: {_clean(item.get('watch_signal'))}." if _clean(item.get("watch_signal")) else "",
            f"Falsifier: {_clean(item.get('falsifier'))}." if _clean(item.get("falsifier")) else "",
            f"Narrowed from: {_clean((item.get('narrowed_from') or {}).get('thesis'))}."
            if isinstance(item.get("narrowed_from"), Mapping) else "",
        ) if x)
        add(
            "strategic_hypothesis",
            f"strategic_hypotheses.verified[{n}]",
            item.get("thesis"),
            stype,
            detail=detail,
            evidence=_evidence_texts(item.get("supporting_evidence")) or
                     [evidence_text[e] for e in item.get("supporting_evidence_ids") or [] if e in evidence_text],
            leverage_complete=complete,
            derivation="cross_event",
        )

    for n, item in enumerate(report.get("hidden_insights") or []):
        if not isinstance(item, Mapping):
            continue
        lev = item.get("leverage_analysis") if isinstance(item.get("leverage_analysis"), Mapping) else {}
        stype = _hidden_type(item)
        add(
            "hidden_insight",
            f"hidden_insights[{n}]",
            item.get("insight") or item.get("text"),
            stype,
            corroboration=_expert_corroboration(item),
            detail=" ".join(_clean(x) for x in (item.get("second_order_effect"), lev.get("predicted_move")) if x),
            evidence=_evidence_texts(item.get("evidence_table"), (item.get("metadata") or {}).get("evidence_table")),
            leverage_complete=stype == "leverage_decision",
            derivation="cross_event" if stype == "cross_event" else "derived",
        )

    moves = report.get("actor_moves") if isinstance(report.get("actor_moves"), Mapping) else {}
    for a, actor in enumerate(moves.get("actors") or []):
        if not isinstance(actor, Mapping):
            continue
        name = _clean(actor.get("actor"))
        crux = actor.get("crux") if isinstance(actor.get("crux"), Mapping) else {}
        contract = _leverage_contract(actor)
        choice = actor.get("choice") if isinstance(actor.get("choice"), Mapping) else {}
        steps = [m for m in actor.get("moves") or [] if isinstance(m, Mapping)]
        picks = ([('choice', choice)] if _clean(choice.get("move")) else []) + [
            (f"moves[{m}]", step) for m, step in enumerate(steps[:2])
        ]
        actor_evidence = _evidence_texts(crux.get("sources"), (actor.get("leverage") or {}).get("sources"))
        for key, move in picks:
            experts = [
                _clean(s.get("publisher")) for s in move.get("expert_sources") or []
                if isinstance(s, Mapping) and _clean(s.get("publisher"))
            ]
            is_leverage = contract["complete"] and _clean(move.get("basis")).casefold() == "leverage"
            stype = "leverage_decision" if is_leverage else "strategic_action"
            lev_detail = ""
            if contract["complete"]:
                lev_detail = (
                    f"Leverage: {contract['holder']} controls {contract['control_point']}; "
                    f"{contract['counterparty']} depends on {contract['dependency']}."
                    + (f" Outside option: {contract['outside_option']}." if contract["outside_option"] else "")
                    + (f" What the holder needs back: {contract['holder_dependence']}."
                       if contract["holder_dependence"] else "")
                )
            add(
                "strategic_decision",
                f"actor_moves.actors[{a}].{key}",
                f"{name} should: {move.get('move')}",
                stype,
                corroboration=experts,
                detail=" ".join(x for x in (
                    f"Crux: {_clean(crux.get('text'))}." if _clean(crux.get("text")) else "",
                    lev_detail,
                    f"Trade-off: {_clean(move.get('trade_off'))}." if _clean(move.get("trade_off")) else "",
                    f"Why it benefits: {_clean(move.get('why_it_benefits'))}." if _clean(move.get("why_it_benefits")) else "",
                    f"Risk: {_clean(move.get('risk'))}." if _clean(move.get("risk")) else "",
                    f"Watch: {_clean(move.get('watch_for'))}." if _clean(move.get("watch_for")) else "",
                ) if x),
                evidence=actor_evidence + _evidence_texts(move.get("sources")),
                leverage_complete=is_leverage,
                derivation="derived",
            )

    briefing = report.get("editorial_briefing") if isinstance(report.get("editorial_briefing"), Mapping) else {}
    for n, item in enumerate(briefing.get("implications") or []):
        if not isinstance(item, Mapping):
            continue
        experts = [
            _clean(c.get("publisher")) for c in item.get("expert_citations") or []
            if isinstance(c, Mapping) and _clean(c.get("publisher"))
        ]
        add(
            "implication",
            f"editorial_briefing.implications[{n}]",
            item.get("text"),
            "derived",
            corroboration=experts,
            detail=_clean(item.get("watch_for")),
            evidence=_evidence_texts(item.get("sources"), item.get("evidence")) + cited_text(item.get("citations")),
            derivation="derived",
        )
    for n, item in enumerate(briefing.get("expected_implications") or []):
        if isinstance(item, Mapping):
            add(
                "implication",
                f"editorial_briefing.expected_implications[{n}]",
                item.get("text"),
                "widely_expected",
                widely_expected=True,
                evidence=_evidence_texts(item.get("sources"), item.get("evidence")) + cited_text(item.get("citations")),
                derivation="derived",
            )
    return out[:MAX_ITEMS]


def promising_unverified(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Combine repairable claim-gate rejects with ambitious lab hypotheses.

    These are never mixed into the verified ranking; they are surfaced so a
    strong idea with one missing bridge is not silently lost.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_many(values: Any) -> None:
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
            return
        for value in values:
            if not isinstance(value, Mapping):
                continue
            row = dict(value)
            text = _clean(row.get("thesis") or row.get("insight") or row.get("text"))
            key = text.casefold()
            if key and key not in seen:
                seen.add(key)
                out.append(row)

    add_many(report.get("promising_hypotheses") or [])
    lab = report.get("strategic_hypotheses") if isinstance(report.get("strategic_hypotheses"), Mapping) else {}
    add_many(lab.get("promising_unverified") or [])
    meta = report.get("analysis_metadata") if isinstance(report.get("analysis_metadata"), Mapping) else {}
    gate = meta.get("claim_support_gate") if isinstance(meta.get("claim_support_gate"), Mapping) else {}
    add_many(gate.get("promising_rejected") or [])
    return out[:8]


def rubric_prompt(question: str, items: Sequence[Mapping[str, Any]]) -> str:
    from services.substance_audit import INSTRUCTION
    checks = "\n".join(f"{key}: {q}" for key, _, q in RUBRIC)
    rows = []
    for i in items:
        row = f"[{i['id']}] Claim: {i['text']}"
        if i.get("detail"):
            row += f"\n    Context: {i['detail']}"
        if i.get("evidence"):
            row += "\n    Source excerpts (check what they actually support):\n      - " + "\n      - ".join(i["evidence"][:6])
        rows.append(row)
    listing = "\n".join(rows)
    return (
        f"Research question: {question}\n\n" + INSTRUCTION + "\n\n"
        "Judge each item with 12 strict yes/no checks. Answer false when the supplied context does not let you verify "
        "the criterion. Do NOT reward confident wording. A normal compliance action, cooperation step, regulator "
        "request, or sensible operating recommendation is not leverage merely because it is specific. Leverage requires "
        "a holder, counterparty, dependency and control point. Being already argued by experts is NOT a weakness.\n\nCHECKS\n"
        + checks + "\n\nITEMS\n" + listing
        + '\n\nReturn JSON: {"scores":[{"id":"I1","answers":{"A1":true,"A2":false,...,"F2":true},'
          '"weakest":"one short phrase","substance":{"plain_claim":"...","ordinary_reading":"...",'
          '"added_inference":"...","decision_change":"...","alternative_explanation":"...",'
          '"discriminating_evidence":"...","key_assumption":"...","facts_supported":true,'
          '"bridge_supported":true,"adds_information":true,"uncertainty_preserved":true}}]} '
          'with all 12 answer keys AND the full substance object for every item. Use false when evidence is missing.'
    )


def parse_rubric(reply: Any, items: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    data = reply if isinstance(reply, Mapping) else _json(reply)
    by_id = {i["id"]: i for i in items}
    out: dict[str, dict[str, Any]] = {}
    for row in data.get("scores") or [] if isinstance(data, Mapping) else []:
        item_id = _clean(row.get("id")) if isinstance(row, Mapping) else ""
        if item_id not in by_id:
            continue
        answers = row.get("answers") if isinstance(row.get("answers"), Mapping) else {}
        if not all(key in answers for key, _, _ in RUBRIC):
            continue
        dims = {d: 0 for d in DIMENSIONS}
        for key, dim, _ in RUBRIC:
            dims[dim] += int(_bool(answers.get(key)))
        item = by_id[item_id]
        from services.substance_audit import parse as parse_substance
        audit = parse_substance(row.get("substance"), item.get("evidence"))
        if not audit["lift_eligible"]:
            dims["informational_lift"] = 0
        if not audit["bridge_supported"]:
            dims["mechanism"] = 0
        # Deterministic guardrail: the model cannot manufacture a leverage score where
        # the structured contract is incomplete.
        if not item.get("leverage_complete"):
            dims["leverage_incentives"] = min(dims["leverage_incentives"], 1)
        raw_score = sum(dims.values())
        cap = 12
        if not audit["complete"] or not audit["facts_supported"] or not audit["uncertainty_preserved"]:
            cap = min(cap, 3)
        if _clean(item.get("strategic_type")) == "strategic_action":
            cap = min(cap, 9)
        if bool(item.get("widely_expected")) or _clean(item.get("strategic_type")) == "widely_expected":
            cap = min(cap, 8)
        if dims["informational_lift"] == 0:
            # A candidate that adds no information beyond the source material
            # may still be useful operating advice, but it cannot score like a
            # premium strategic insight.
            cap = min(cap, 7)
        elif dims["informational_lift"] == 1:
            cap = min(cap, 9)
        score = min(raw_score, cap)
        out[item_id] = {
            "score": score,
            "substance": audit,
            "raw_score": raw_score,
            "score_cap": cap,
            "dimensions": dims,
            "weakest": _clean(row.get("weakest"))[:160],
        }
    return out


def pairwise_prompt(question: str, a: Mapping[str, Any], b: Mapping[str, Any]) -> str:
    def show(label: str, item: Mapping[str, Any]) -> str:
        from services.substance_audit import judging_view
        return f"{label}: " + judging_view(item)
    return (
        f"Research question: {question}\n\n"
        "The product's purpose is strategic decisions based on leverage. Which candidate gives a knowledgeable reader "
        "more strategic value? Prefer a supported chain of dependency -> "
        "control point -> incentives/constraints -> move -> countermove/watch signal over routine compliance or generic "
        "good practice. Reward a non-obvious cross-event mechanism when it is supported. A specific administrative action "
        "is not automatically strategic leverage. Being already argued by experts is NOT a weakness. Ignore which one is shown first and ignore "
        "confident wording. Compare the plain claims and evidence, not prose length, badges or rhetorical labels. "
        "Restating or naming an event is not an additional mechanism. A plausible causal story is not proof.\n\n" + show("A", a) + "\n\n" + show("B", b)
        + '\n\nReturn JSON: {"winner":"A"|"B"|"tie","deciding_dimension":"...","reason":"one sentence"}'
    )


def parse_winner(reply: Any) -> str:
    data = reply if isinstance(reply, Mapping) else _json(reply)
    winner = _clean(data.get("winner") if isinstance(data, Mapping) else "").upper()
    return winner if winner in {"A", "B", "TIE"} else ""


def bradley_terry(ids: Sequence[str], outcomes: Sequence[tuple[str, str, float]], iterations: int = 200) -> dict[str, float]:
    wins = {i: 0.5 for i in ids}
    games: dict[tuple[str, str], float] = {}
    for i, j, s in outcomes:
        wins[i] += s
        wins[j] += 1 - s
        games[(i, j)] = games.get((i, j), 0) + 1
        games[(j, i)] = games.get((j, i), 0) + 1
    strength = {i: 1.0 for i in ids}
    for _ in range(iterations):
        new = {}
        for i in ids:
            denom = 1.0 / (strength[i] + 1.0)
            denom += sum(n / (strength[i] + strength[j]) for (a, j), n in games.items() if a == i)
            new[i] = wins[i] / denom
        total = sum(new.values()) or 1.0
        strength = {i: v * len(ids) / total for i, v in new.items()}
    return strength


def rank(question: str, items: Sequence[dict[str, Any]], judge: Callable[[str], Any], top: int = PAIRWISE_TOP) -> dict[str, Any]:
    pool = list(items[:top])
    outcomes: list[tuple[str, str, float]] = []
    log = {"pairs": 0, "uncertain": 0, "failed": 0}
    stability = {i["id"]: [0, 0] for i in pool}
    for a, b in itertools.combinations(pool, 2):
        try:
            first = parse_winner(judge(pairwise_prompt(question, a, b)))
            second = parse_winner(judge(pairwise_prompt(question, b, a)))
        except Exception:
            log["failed"] += 1
            continue
        if not first or not second:
            log["failed"] += 1
            continue
        log["pairs"] += 1
        one = {"A": a["id"], "B": b["id"], "TIE": "TIE"}[first]
        two = {"A": b["id"], "B": a["id"], "TIE": "TIE"}[second]
        for item in (a, b):
            stability[item["id"]][1] += 1
        if one == two:
            if one == "TIE":
                outcomes.append((a["id"], b["id"], 0.5))
            else:
                outcomes.append((one, b["id"] if one == a["id"] else a["id"], 1.0))
            for item in (a, b):
                stability[item["id"]][0] += 1
        else:
            log["uncertain"] += 1
            outcomes.append((a["id"], b["id"], 0.5))
    strength = bradley_terry([i["id"] for i in pool], outcomes) if log["pairs"] else {}
    return {"strength": strength, "stability": stability, "log": log}


def _rank_priority(item: Mapping[str, Any]) -> int:
    return {
        "leverage_decision": 0,
        "cross_event": 1,
        "derived": 2,
        "strategic_action": 3,
        "analysis": 4,
        "widely_expected": 5,
    }.get(_clean(item.get("strategic_type")), 6)


def evaluate(report: Mapping[str, Any], question: str, judge: Callable[[str], Any],
             evidence_text: Mapping[str, str] | None = None) -> dict[str, Any]:
    items = candidates(report, evidence_text)
    result: dict[str, Any] = {
        "version": VERSION,
        "eligible": len(items),
        "items": [],
        "promising_unverified": promising_unverified(report),
    }
    if not items:
        return dict(result, status="no_items")
    scores: dict[str, dict[str, Any]] = {}
    failures = 0
    for start in range(0, len(items), RUBRIC_BATCH):
        batch = items[start:start + RUBRIC_BATCH]
        try:
            scores.update(parse_rubric(judge(rubric_prompt(question, batch)), batch))
        except Exception:
            failures += 1
    scored = [dict(i, **scores[i["id"]]) for i in items if i["id"] in scores]
    if not scored:
        return dict(result, status="judge_unavailable", rubric_failures=failures)
    scored.sort(key=lambda i: (-i["score"], _rank_priority(i)))

    # Product rule: pairwise ranking is for insights, not merely sensible advice.
    # If at least one candidate has positive informational lift, zero-lift items
    # are quarantined from the pairwise tournament and cannot take the #1 slot.
    # This directly prevents a routine action from beating a supported structural
    # thesis simply because the action is concrete.
    lifted = [i for i in scored if int((i.get("dimensions") or {}).get("informational_lift", 0)) > 0]
    zero_lift = [i for i in scored if int((i.get("dimensions") or {}).get("informational_lift", 0)) <= 0]
    tournament_pool = lifted if lifted else scored
    ranking = rank(question, tournament_pool, judge) if len(tournament_pool) > 1 else {
        "strength": ({tournament_pool[0]["id"]: 1.0} if tournament_pool else {}),
        "stability": {},
        "log": {"pairs": 0, "uncertain": 0, "failed": 0},
    }
    strength = ranking["strength"]
    head = sorted(
        [i for i in tournament_pool if i["id"] in strength],
        key=lambda i: (-strength[i["id"]], -i["score"], _rank_priority(i)),
    )
    tail_lifted = [i for i in lifted if i["id"] not in strength]
    tail_lifted.sort(key=lambda i: (-i["score"], _rank_priority(i)))
    zero_lift.sort(key=lambda i: (-i["score"], _rank_priority(i)))
    ordered = head + tail_lifted + ([] if not lifted else zero_lift)
    if not lifted:
        ordered = head + [i for i in scored if i["id"] not in strength]
    for n, item in enumerate(ordered, start=1):
        consistent, judged = ranking["stability"].get(item["id"], (0, 0))
        ratio = (consistent / judged) if judged else None
        confidence = "high" if ratio is not None and ratio >= 0.75 else "low" if ratio is not None and ratio <= 0.5 else "medium" if ratio is not None else "not_pairwise_ranked"
        item.update({
            "rank": n,
            "of": len(ordered),
            "band": band(item["score"], item),
            "provenance_label": provenance_label(item),
            "pairwise_stability": f"{consistent}/{judged}" if judged else "",
            "pairwise_confidence": confidence,
            "ranked_by": "pairwise" if item["id"] in strength else "rubric score",
        })
    result.update({
        "status": "complete",
        "items": ordered,
        "top": [i["id"] for i in ordered[:TOP_SHOWN]],
        "pairwise": ranking["log"],
        "rubric_failures": failures,
    })
    return result


def gold_agreement(gold_pairs: Sequence[Mapping[str, Any]], question: str, judge: Callable[[str], Any]) -> dict:
    """Only supplied human pairs should be passed here; reference sanity pairs belong elsewhere."""
    rows, agree, flips, ties = [], 0, 0, 0
    for pair in gold_pairs:
        a = {"id": "A", "text": pair["a"], "strategic_type": pair.get("a_provenance", "derived"), "detail": ""}
        b = {"id": "B", "text": pair["b"], "strategic_type": pair.get("b_provenance", "derived"), "detail": ""}
        first = parse_winner(judge(pairwise_prompt(question, a, b)))
        second = parse_winner(judge(pairwise_prompt(question, b, a)))
        second = {"A": "B", "B": "A"}.get(second, second)
        flipped = first != second
        verdict = first if not flipped else "UNCERTAIN"
        flips += flipped
        ties += verdict == "TIE"
        agree += verdict == _clean(pair["human_winner"]).upper()
        rows.append({"id": pair["id"], "human": pair["human_winner"], "judge": verdict})
    n = len(gold_pairs) or 1
    return {
        "pairs": len(gold_pairs),
        "pairwise_accuracy": round(agree / n, 2),
        "order_flip_rate": round(flips / n, 2),
        "tie_rate": round(ties / n, 2),
        "rows": rows,
    }



def agreement(gold: Mapping[str, Any], judge: Callable[[str], Any]) -> dict[str, Any]:
    """v1.5 (restored from v6.31.1; eval_insight_judge.py calls it): only pairs where the user
    chose the winner count as agreement with the user; reference pairs are a separate sanity check."""
    question = gold.get("question", "")
    human = gold.get("human_pairs") or []
    reference = gold.get("reference_pairs") or []
    return {"agreement_with_you": gold_agreement(human, question, judge) if human else None,
            "reference_sanity": gold_agreement(reference, question, judge) if reference else None,
            "human_pairs": len(human), "reference_pairs": len(reference)}
