"""Analytic moves of the publications that sell original analysis, chosen per story by signal (v13.31).

Research report of 22 Nov, "Reproducing paid-grade original analysis in an AI news pipeline":
* The best analysts' originality comes from a small number of reusable moves applied to a stable framework
  and a memory of their own positions (Thompson "rarely starts from scratch"; Levine explains the legal or
  financial mechanism; Lex sizes a number against the right denominator; Stoller reads the record against
  the public story; Evans and Hobart reason from historical patterns; ICD 203 asks for alternatives and for
  change against earlier judgments).
* AnalysisBank (Yang, Ma, Koa, Kan, arXiv 2609.00818): store each move as data_signal -> analytical_move,
  retrieve it by the SIGNAL, and execute ONE move per call. That raised the share of novel, data-grounded
  insight 1.7-3.7x over structural prompting; skipping per-move execution cost 4.6 points because "the
  composer defaults to the generic analytical moves". In 42% of traced cases the baseline had the data but
  did "not draw the analytical inference" - the pipeline's "restates the news" failure.
The four lenses of v13.26 (constraints, value chain, follow the money, structured analogy) are four of
these moves. Signals are read by code from the story's records, fact table, consensus and case memory.

Every move ends with the same structure (report: BLUF thesis -> mechanism -> number -> rival -> forecast
-> what would change our mind; 150-300 words; assertive and concise - Huang, Zang & Zheng, The Accounting
Review 2014: analyst text is "more useful when it places more emphasis on nonfinancial topics, is written
more assertively and concisely"). The exact gate thresholds are NOT in the prompt (both reports: rules shown
to the generator get gamed, as with the borrowed numbers); code enforces them.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

VERSION = "analytic-moves-v1"
MAX_MOVES = 4

_LEGAL = re.compile(r"\b(sue[sd]?|lawsuit|complaint|court|judge|ruling|settle(?:d|ment)|injunction|statute|act\b|"
                    r"rule(?:making)?|regulation|regulator|agency|order|subpoena|attorney general|antitrust|"
                    r"export control|entity list|ban|fine[sd]?|penalt)", re.I)
_DEAL = re.compile(r"\b(deal|partnership|acqui(?:re|sition)|launch(?:es|ed)?|pricing|price[sd]?|licens|distribut|"
                   r"app store|platform|api\b|contract|investment|stake|merger)", re.I)
_INTENT = re.compile(r"\b(plans?|vow(?:s|ed)?|pledge[sd]?|intends?|threaten(?:s|ed)?|wants?|seeks?|promis(?:e|ed)|"
                     r"will (?:not )?(?:resist|fight|comply|appeal|block|require))\b", re.I)
_DOCUMENT = re.compile(r"\b(filing|complaint|ruling|opinion|rule|notice|10-K|10-Q|8-K|order|bill|act)\b", re.I)

MOVES: dict[str, dict[str, str]] = {
    "mechanism_explainer": {
        "source": "Money Stuff (Levine), Lawfare",
        "instruction": "Name the specific statute, rule section or legal test that decides the outcome, quote its "
                       "operative words from the records, and explain in at most three steps how it turns the "
                       "reported facts into a consequence for a named party. Do not restate the news."},
    "value_capture": {
        "source": "Stratechery (Thompson, Aggregation Theory)",
        "instruction": "Identify who controls demand (the user relationship), who supplies, and who is being "
                       "commoditised; say whose margin rises and whose falls, and whether this confirms or "
                       "contradicts the earlier theses listed."},
    "constraints": {
        "source": "Papic, Geopolitical Alpha",
        "instruction": "List the binding constraints on the actor - legal, political (votes), economic or market, "
                       "geopolitical, time - each with a record that shows it, and predict the outcome the "
                       "constraints allow, not the one the actor says it prefers."},
    "follow_the_money": {
        "source": "Levine, Stoller, sell-side research",
        "instruction": "Using figures from the fact table or filings, show each party's exposure (revenue, contract "
                       "value, fine, cost) and explain the action by the exposure; say where the stated motive and "
                       "the exposure diverge."},
    "sizing": {
        "source": "FT Lex",
        "instruction": "Put the headline number next to the denominator that changes its meaning (share of "
                       "revenue, of cash flow, of the market, of last year's volume) - use the COMPUTED BY CODE "
                       "figures - and say whether it is material."},
    "structured_analogy": {
        "source": "Evans, Stoller, Hobart; Green & Armstrong 2007",
        "instruction": "Take the closest case from PAST CASES (by mechanism, not topic). State two points where it "
                       "matches and one where it differs, its outcome and how long the outcome took, and use that "
                       "as the base rate. Cite its case ID; do not use a case from memory."},
    "variant_perception": {
        "source": "Steinhardt; Eurasia Group 'red herrings'",
        "instruction": "Take the consensus view (quote it) and the one public-record fact those sources do not use, "
                       "and explain why that fact changes the conclusion."},
    "second_order": {
        "source": "Thompson, Evans",
        "instruction": "Given the first-order effect, name who must respond next - a different actor from those in "
                       "the story - and their most constrained response, with the record that shows the constraint."},
    "change_vs_prior": {
        "source": "ICD 203 (explain change or consistency); Thompson quoting his own earlier pieces",
        "instruction": "Say whether this development strengthens, weakens or overturns the EARLIER THESES listed, "
                       "and what that changes in the forecast."},
    "absence": {
        "source": "AnalysisBank absent-mechanism signals; Hobart",
        "instruction": "Identify what the filing, ruling or rule conspicuously does not contain (no damages figure, "
                       "no compliance date, no named enforcer) and explain why that gap matters for a named party."},
}

STRUCTURE = (
    "Write the insight as: (1) the thesis in one short assertive sentence; (2) the mechanism; (3) the number that "
    "carries it; (4) the strongest rival explanation; (5) a dated forecast that would come out differently if the "
    "rival were true - not a step that happens in almost every case; (6) what would change your mind. 150-300 "
    "words, plain words, no coined labels. Strategic, legal and non-financial points count; a bearish conclusion "
    "is fine. Every fact comes from the records; quote the words each mechanism step rests on.")


def _text(view: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> str:
    parts = [str(view.get("labels") or "")] + [str(r.get("text") or "") for r in records or [] if isinstance(r, Mapping)]
    return " ".join(parts)


def signals(view: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Move -> strength (0 = not triggered). Plain code over the story's text, fact table, consensus and memory."""
    text = _text(view, records)
    legal = len(_LEGAL.findall(text))
    computed = view.get("computed_facts") or []
    money = [r for r in view.get("fact_rows") or [] if isinstance(r, Mapping) and r.get("kind") in {"money", "percent", "count"}]
    out = {
        "mechanism_explainer": min(3.0, legal / 2),
        "value_capture": min(3.0, len(_DEAL.findall(text)) / 2),
        "constraints": min(3.0, len(_INTENT.findall(text)) / 2 + (0.5 if legal else 0)),
        "follow_the_money": 1.0 + min(2.0, len(money) / 3),
        "sizing": min(3.0, len(money) / 2 + len([c for c in computed if c.get("kind") in {"money_ratio", "rate"}])),
        "structured_analogy": 2.5 if view.get("cases") else 0.8,
        "variant_perception": 2.0 if len(view.get("claims") or []) >= 2 else 0.0,
        "second_order": 1.0,
        "change_vs_prior": 2.5 if view.get("earlier_theses") else 0.0,
        "absence": 1.5 if (legal and _DOCUMENT.search(text)) else 0.0,
    }
    if computed and any(c.get("kind") in {"date_gap", "reported_later"} for c in computed):
        out["mechanism_explainer"] += 0.5
    return out


def select(view: Mapping[str, Any], records: Sequence[Mapping[str, Any]], limit: int = MAX_MOVES) -> list[str]:
    """The strongest-signalled moves, at most `limit`, always including one anchor move (a mechanism or a number)
    so no candidate is pure framing. Diversity: one candidate per move (AnalysisBank; Si et al. found 200
    unique ideas in 4,000 seeds when one prompt is sampled repeatedly)."""
    strength = signals(view, records)
    ranked = [name for name, value in sorted(strength.items(), key=lambda kv: -kv[1]) if value > 0]
    chosen = ranked[:limit]
    anchors = [m for m in ("mechanism_explainer", "sizing", "follow_the_money") if strength.get(m, 0) > 0]
    if anchors and not set(chosen) & set(anchors):
        chosen = chosen[:limit - 1] + [anchors[0]]
    return chosen


def instruction(move: str) -> str:
    entry = MOVES.get(move) or {"instruction": move, "source": ""}
    return (f"MOVE: {move} ({entry['source']}). {entry['instruction']}\n{STRUCTURE}")


def cases_block(cases: Sequence[Mapping[str, Any]]) -> str:
    if not cases:
        return ""
    lines = ["PAST CASES (curated library; cite the case ID; outcomes and durations are recorded facts):"]
    for case in cases:
        lines.append(f"- {case.get('case_id')}: {case.get('name')} ({case.get('jurisdiction')}, {case.get('year')}) - "
                     f"theory: {case.get('legal_theory')}; mechanism: {', '.join(case.get('mechanism_tags') or [])}; "
                     f"outcome: {case.get('outcome')} after {case.get('time_to_outcome_days') or '?'} days")
    return "\n".join(lines)


def earlier_block(theses: Sequence[str]) -> str:
    if not theses:
        return ""
    return ("EARLIER THESES OF THIS APP ON THESE ACTORS (say whether this extends, corrects or contradicts them; "
            "do not repeat them):\n" + "\n".join(f"- {t[:300]}" for t in theses[:3]))
