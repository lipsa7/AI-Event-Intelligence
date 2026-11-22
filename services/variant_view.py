"""Variant view: consensus first, several competing candidates, a tournament (v13.23, 22 Nov 2026).

Why
---
The user's bar: an original insight must read at par with publications that sell original analysis.
In RUN-ACC0E1ED the generator produced 7 candidates; 5 restated their sources, the 2 shown were
restatements, forecasts ran 9-16 months, and the generic briefing lines ("state AGs will bring more
public-nuisance suits") were what any reader would guess. The strategic-inference prompt already asks
for leverage, the outside view and a forecast - but in one call, for one candidate, with no explicit
statement of what everyone already expects.

The method of professional differentiated analysis, from its own sources:
* Michael Steinhardt's "variant perception": "a well-founded view that was meaningfully different from
  market consensus", and knowing what the expectations truly are is the first step (No Bull, 2001).
* Howard Marks's second-level thinking (Oaktree memo "It's Not Easy", 2015): what is the range of
  outcomes, which will occur, how my view differs from the consensus, what happens if the consensus is
  right and what if I am.
* Marko Papic's constraints framework (Geopolitical Alpha, 2020): preferences are optional and subject
  to constraints; bet on the political, economic, geopolitical, legal and time constraints.
* Over-generate, then rank by pairwise comparison (Google AI co-scientist's Elo tournament; Si, Yang &
  Hashimoto 2024 on LLM idea duplication) - the best of several candidates, not the first.

So, per exploration slot:
1. CONSENSUS (one call): what readers of these reports already believe will happen next, cited.
2. VARIANT CANDIDATES: the generator is told the consensus and asked for up to three DIFFERENT variant
   views, each with the constraint the consensus ignores, a number, a named actor's move and a dated call.
3. STRUCTURE GATE (no call): a candidate without a number, a named actor, or a resolvable call within
   2-12 weeks is dropped - such a call can be scored in weeks (Brier needs resolved forecasts).
4. TOURNAMENT (pairwise calls): survivors are compared in both orders on the editor's rubric; the
   winner goes on to the claim-support, non-obviousness and editor's-bar gates.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

VERSION = "variant-view-v3"
MIN_HORIZON_DAYS = 14
MAX_HORIZON_DAYS = 84
MAX_CANDIDATES = 3
_NUMBER = re.compile(r"\d")   # any figure or date (amount, share, count, day, year)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _parse(answer: Any) -> Mapping[str, Any]:
    if isinstance(answer, Mapping):
        return answer
    text = _clean(answer)
    first, last = text.find("{"), text.rfind("}")
    if first < 0 or last <= first:
        return {}
    try:
        data = json.loads(text[first:last + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, Mapping) else {}


# ---------------------------------------------------------------- 1. consensus map
# v13.24 (research report of 22 Nov, stage A): the consensus is RETRIEVED and CITED - published analysis
# found by search (institutional notes, analysts, editorials), not only the run's own reports - as 3-5
# dated claims. BIS Bulletin no. 120 had stated "AI financing shifts to debt" months before the app
# "found" it; that is what this map is for. It also names the search terms for the primary sources.
LENSES = (
    ("constraints", "Papic's constraints: the legal, financial, capacity, political or time constraint that forces a move, whatever the actor prefers"),
    ("value_chain", "Thompson's aggregation theory / value chain: which layer of the supplier-distributor-customer chain gains or loses control and margin"),
    ("follow_the_money", "follow the money: who pays, who gains, and the filed or ruled number that moves"),
    ("structured_analogy", "structured analogy (Green & Armstrong): what happened next in past cases with the same mechanism, and what that implies here"),
)


def consensus_prompt(labels: Sequence[str], records: Sequence[Mapping[str, Any]],
                     analysis: Sequence[Mapping[str, Any]] = ()) -> str:
    lines = []
    for record in list(records)[:20]:
        text = _clean(record.get("text") or record.get("source_excerpt"))[:260]
        if text:
            kind = "published analysis" if _clean(record.get("gap_type")).startswith("commentary") else "report"
            lines.append(f"[{_clean(record.get('evidence_id'))}] ({kind}) {text}")
    for n, item in enumerate(list(analysis)[:8], start=1):
        text = _clean(item.get("snippet") or item.get("title"))[:260]
        if text:
            lines.append(f"[CONS-{n}] (published analysis, {_clean(item.get('url'))[:90]}) {text}")
    return (
        "Developments: " + " | ".join(labels) + "\n\nSources:\n" + "\n".join(lines)
        + "\n\nList the CONSENSUS: 3 to 5 claims that most coverage and analysts already make about these developments "
          "- what they believe and what they expect to happen next. Each claim cites the source id it comes from and its "
          "date if shown. No opinions of your own. Also give short search terms (2-5 words each) for finding primary "
          "records: a US Federal Register rule or notice, an SEC filing, and a court case about these developments "
          "(empty string if none applies). Return JSON {\"claims\":[{\"claim\":\"...\",\"source\":\"EVID-... or CONS-n\","
          "\"date\":\"\"}],\"search_terms\":{\"federal_register\":\"\",\"sec\":\"\",\"court\":\"\"}}."
    )


def consensus(labels: Sequence[str], records: Sequence[Mapping[str, Any]], ask: Callable[[str], Any],
              analysis: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    try:
        data = _parse(ask(consensus_prompt(labels, records, analysis)))
    except Exception as error:
        return {"consensus": "", "claims": [], "search_terms": {}, "error": type(error).__name__}
    claims = []
    for row in data.get("claims") or []:
        if isinstance(row, Mapping) and _clean(row.get("claim")) and _clean(row.get("source")):
            claims.append({"claim": _clean(row.get("claim"))[:300], "source": _clean(row.get("source"))[:40],
                           "date": _clean(row.get("date"))[:20]})
    claims = claims[:5]
    terms = data.get("search_terms") if isinstance(data.get("search_terms"), Mapping) else {}
    analysis_urls = {f"CONS-{n}": _clean(item.get("url")) for n, item in enumerate(list(analysis)[:8], start=1)}
    for claim in claims:
        if claim["source"] in analysis_urls:
            claim["url"] = analysis_urls[claim["source"]]
    return {"consensus": " ".join(c["claim"] for c in claims)[:900], "claims": claims,
            "expected_next": [], "search_terms": {k: _clean(terms.get(k))[:80] for k in ("federal_register", "sec", "court")},
            "enough": len(claims) >= 3}


# ---------------------------------------------------------------- 2. generator instruction
def instruction(view: Mapping[str, Any], fact_ids: Sequence[str] = ()) -> str:
    claims = [c for c in view.get("claims") or [] if isinstance(c, Mapping)]
    if not claims and not _clean(view.get("consensus")):
        return ""
    listed = "\n".join(f"CONSENSUS[{n}] ({c.get('source')}{', ' + c['date'] if c.get('date') else ''}): {c['claim']}"
                       for n, c in enumerate(claims, start=1)) or f"CONSENSUS[1]: {view.get('consensus')}"
    lenses = "\n".join(f"- {name}: {text}" for name, text in LENSES)
    facts = (" Primary records (federal rules, filings, dockets) are in the evidence as " + ", ".join(list(fact_ids)[:12])
             + " - prefer their dates and numbers for the mechanism and the call." if fact_ids else "")
    return (
        "\n\nVARIANT VIEW (Steinhardt's variant perception; Marks's second-level thinking; Papic's constraints).\n"
        "WHAT INFORMED READERS ALREADY BELIEVE:\n" + listed + "\n"
        "Return up to THREE candidates, each through a DIFFERENT lens from this list and making a different point:\n"
        + lenses + "\n"
        "Each candidate must: (1) name in novel_proposition which CONSENSUS[n] it contradicts or materially refines "
        "(\"CONSENSUS[2]: ...\") and why that consensus misses it; (2) rest on the CONSTRAINT or mechanism the lens "
        "reveals - preferences and statements of intent are not constraints; (3) carry at least one cited NUMBER or "
        "date in the insight text;" + facts + " (4) name the actor and the move it is forced into; (5) give a "
        "forecast_contract - a yes/no question resolvable within 2 to 12 weeks of today from a named public source "
        "(a docket, a Federal Register date, a filing, an official statement), with its deadline and resolution rule; "
        "(6) put what you would see if the consensus is right instead in alternative_explanations; (7) put the lens "
        "name in analytical_lenses. Write the first sentence of the insight as the thesis in at most 35 plain words; "
        "no coined labels (\"structural asymmetry\", \"blind spot\", \"paradigm\"). If no candidate meets all of "
        "this, return fewer or none."
    )


# ---------------------------------------------------------------- 3. structure gate
def _deadline(candidate: Mapping[str, Any]) -> datetime | None:
    contract = _mapping(candidate.get("forecast_contract")) or _mapping(
        _mapping(candidate.get("metadata")).get("forecast_contract"))
    raw = _clean(contract.get("deadline"))
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        match = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
        if not match:
            return None
        value = datetime.fromisoformat(match.group(1))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def structure_problems(candidate: Mapping[str, Any], today: datetime | None = None) -> list[str]:
    """Reasons a candidate is not a variant view a reader can check. Empty list: it passes."""
    today = today or datetime.now(timezone.utc)
    text = _clean(candidate.get("insight") or candidate.get("text") or candidate.get("conclusion"))
    problems: list[str] = []
    if not _NUMBER.search(text):
        problems.append("no_number")
    holder = _clean(_mapping(candidate.get("leverage_analysis")).get("holder"))
    if not holder and not re.search(r"\b[A-Z][a-zA-Z&.-]+(?:\s[A-Z][a-zA-Z&.-]+)*\b", text[1:]):
        problems.append("no_named_actor")
    deadline = _deadline(candidate)
    contract = _mapping(candidate.get("forecast_contract")) or _mapping(
        _mapping(candidate.get("metadata")).get("forecast_contract"))
    if deadline is None or not _clean(contract.get("question")):
        problems.append("no_dated_call")
    else:
        days = (deadline - today).days
        if days < MIN_HORIZON_DAYS or days > MAX_HORIZON_DAYS:
            problems.append(f"call_horizon_{days}_days")
    return problems


# ---------------------------------------------------------------- 3a. evidence first
def evidence_problems(candidate: Mapping[str, Any]) -> list[str]:
    """Every premise must cite a record AND its quote must have been found in an evidence record
    (quote_first's check, recorded in the premise notes as quote_check=cited|elsewhere). A premise with
    no citation, a quote found nowhere, or one too common to attribute fails. No model call."""
    premises = [p for p in candidate.get("premises") or [] if isinstance(p, Mapping)]
    if not premises:
        return ["no_premises"]
    problems = []
    for number, premise in enumerate(premises, start=1):
        if not [e for e in premise.get("evidence_ids") or [] if _clean(e)]:
            problems.append(f"premise_{number}_uncited")
            continue
        notes = _clean(premise.get("notes"))
        match = re.search(r"quote_check=(\w+)", notes)
        status = match.group(1) if match else ""
        if status not in {"cited", "elsewhere"}:
            problems.append(f"premise_{number}_quote_{status or 'missing'}")
    return problems


# ---------------------------------------------------------------- 3b. plain language
# Coined phrases from the user's own labelled insights (data/insight_review_90.csv) and the research
# report's examples; a thesis that leans on them reads as jargon, not substance.
JARGON = ("structural asymmetry", "structural incentive asymmetry", "incentive asymmetry", "blind spot",
          "telemetry blind spot", "paradigm", "inflection point", "tectonic", "seismic shift", "structural shift",
          "new normal", "arms race", "double-edged", "moat", "flywheel", "convergence of", "nexus")
THESIS_MAX_WORDS = 35


def plain_language_problems(candidate: Mapping[str, Any]) -> list[str]:
    text = _clean(candidate.get("insight") or candidate.get("text"))
    thesis = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0] if text else ""
    problems = []
    if len(thesis.split()) > THESIS_MAX_WORDS:
        problems.append(f"thesis_{len(thesis.split())}_words")
    lowered = text.casefold()
    used = [j for j in JARGON if j in lowered]
    if used:
        problems.append("jargon:" + ",".join(used[:3]))
    return problems


# ---------------------------------------------------------------- 3c. novelty against the consensus
def novelty_prompt(candidate: str, view: Mapping[str, Any]) -> str:
    claims = "\n".join(f"CONSENSUS[{n}]: {c.get('claim')}" for n, c in enumerate(view.get("claims") or [], start=1)) \
        or f"CONSENSUS[1]: {_clean(view.get('consensus'))}"
    return ("Does the CANDIDATE say anything that the CONSENSUS claims do not already say or directly imply?\n"
            + claims + "\n\nCANDIDATE: " + _clean(candidate)[:1200]
            + '\n\nReturn JSON {"new":true|false,"overlap_quote":"the consensus words it repeats, or empty",'
              '"what_is_new":"one sentence, or empty"}.')


def novelty(candidate: str, view: Mapping[str, Any], ask: Callable[[str], Any]) -> dict[str, Any]:
    if not view.get("claims") and not _clean(view.get("consensus")):
        return {"new": True, "checked": False}
    try:
        data = _parse(ask(novelty_prompt(candidate, view)))
    except Exception as error:
        return {"new": True, "checked": False, "error": type(error).__name__}
    if not isinstance(data.get("new"), bool):
        return {"new": True, "checked": False, "error": "bad_answer"}
    return {"new": data["new"], "checked": True, "overlap": _clean(data.get("overlap_quote"))[:200],
            "what_is_new": _clean(data.get("what_is_new"))[:200]}


# ---------------------------------------------------------------- 3d. pre-mortem
def premortem_prompt(candidate: Mapping[str, Any], evidence_lines: Sequence[str]) -> str:
    contract = _mapping(candidate.get("forecast_contract"))
    return ("Pre-mortem (Klein): it is " + (_clean(contract.get("deadline"))[:10] or "the deadline") + " and this call "
            "FAILED.\nCALL: " + _clean(contract.get("question"))[:300]
            + "\nINSIGHT: " + _clean(candidate.get("insight") or candidate.get("text"))[:900]
            + "\n\nEVIDENCE ALREADY IN HAND:\n" + "\n".join(evidence_lines[:30])
            + '\n\nGive the three most likely reasons it failed. For each, say whether the evidence above ALREADY '
              'shows it (quote the record id). Return JSON {"reasons":[{"reason":"","already_in_evidence":true|false,'
              '"evidence_id":""}]}.')


def premortem(candidate: Mapping[str, Any], evidence_lines: Sequence[str], ask: Callable[[str], Any]) -> dict[str, Any]:
    try:
        data = _parse(ask(premortem_prompt(candidate, evidence_lines)))
    except Exception as error:
        return {"killed": False, "checked": False, "error": type(error).__name__}
    reasons = [r for r in data.get("reasons") or [] if isinstance(r, Mapping)]
    shown = [r for r in reasons if r.get("already_in_evidence") is True and _clean(r.get("evidence_id"))]
    return {"killed": bool(shown), "checked": bool(reasons),
            "reasons": [_clean(r.get("reason"))[:160] for r in reasons[:3]],
            "evidenced": [{"reason": _clean(r.get("reason"))[:160], "evidence_id": _clean(r.get("evidence_id"))} for r in shown]}


# ---------------------------------------------------------------- 3e. repair, not drop
# v13.25c (22 Nov 2026): a candidate that misses a rule is sent back ONCE with exactly what it misses
# (the research report: "a Flash-Lite rewrite runs once; the meaning is then re-verified"; SciMON revises
# an idea against the prior work it overlaps instead of discarding it). Only if the repair still fails is
# it held back - and then shown in the report as a draft with what it needs.
NEEDS_TEXT = {
    "no_number": "put at least one number or date from the records into the insight text",
    "no_named_actor": "name the company, agency or person who acts",
    "no_dated_call": "add a yes/no forecast_contract question with a deadline and resolution rule",
    "restates_consensus": "go beyond the consensus it repeats (quoted below): say what follows from it that it does not say",
}


def need_text(code: str) -> str:
    if code in NEEDS_TEXT:
        return NEEDS_TEXT[code]
    if code.startswith("call_horizon_"):
        return (f"move the forecast deadline to between {MIN_HORIZON_DAYS} and {MAX_HORIZON_DAYS} days from today "
                "- pick the nearest checkable step on the way to the outcome (a filing, a hearing, a comment "
                "deadline, a statement)")
    if code.startswith("thesis_"):
        return f"cut the first sentence (the thesis) to at most {THESIS_MAX_WORDS} plain words"
    if code.startswith("jargon"):
        return "replace these labels with what they mean in plain words: " + code.split(":", 1)[-1]
    return code.replace("_", " ")


def repair_prompt(candidate: Mapping[str, Any], problems: Sequence[str], evidence_lines: Sequence[str],
                  overlap: str = "", today: datetime | None = None) -> str:
    today = today or datetime.now(timezone.utc)
    fixes = "\n".join(f"- {need_text(p)}" for p in problems)
    return ("Revise this insight so it meets every point below. Keep its facts and its point; use only facts "
            "stated in the records; do not add claims the records do not support.\nTODAY: " + today.strftime("%Y-%m-%d")
            + "\nFIX:\n" + fixes
            + (f"\nTHE CONSENSUS IT REPEATS: {overlap}" if overlap else "")
            + "\n\nINSIGHT: " + _clean(candidate.get("insight") or candidate.get("text"))[:1500]
            + "\nFORECAST: " + json.dumps(_mapping(candidate.get("forecast_contract")), ensure_ascii=False)[:500]
            + "\n\nRECORDS:\n" + "\n".join(list(evidence_lines)[:30])
            + '\n\nReturn JSON {"insight":"revised text, thesis first","forecast_contract":{"question":"yes/no",'
              '"deadline":"ISO date","resolution_criteria":"public source and rule"},"holder":"named actor"}.')


def apply_repair(candidate: Mapping[str, Any], answer: Any, problems: Sequence[str]) -> dict[str, Any] | None:
    data = _parse(answer)
    text = _clean(data.get("insight"))
    if not text:
        return None
    out = dict(candidate)
    out["insight"] = text
    contract = _mapping(data.get("forecast_contract"))
    if _clean(contract.get("question")) and _clean(contract.get("deadline")):
        out["forecast_contract"] = {k: _clean(contract.get(k)) for k in ("question", "deadline", "resolution_criteria")}
    holder = _clean(data.get("holder"))
    if holder and not _clean(_mapping(out.get("leverage_analysis")).get("holder")):
        out["leverage_analysis"] = dict(_mapping(out.get("leverage_analysis")), holder=holder)
    meta = dict(_mapping(out.get("metadata")))
    meta["variant_repaired"] = {"fixed": list(problems), "original_insight": _clean(candidate.get("insight") or candidate.get("text"))[:900]}
    out["metadata"] = meta
    return out


# ---------------------------------------------------------------- 4. tournament
TOURNAMENT_RUBRIC = (
    "Which candidate would a demanding editor of a serious analysis publication rather publish? Prefer, in this "
    "order: a claim meaningfully different from the consensus yet well-founded; the constraint the consensus "
    "ignores, with a cited number; a named actor's forced move; a call that can be checked within weeks. Strong "
    "vocabulary is not substance; length is not quality."
)


def pair_prompt(view: Mapping[str, Any], a: str, b: str) -> str:
    return (TOURNAMENT_RUBRIC + f"\n\nCONSENSUS: {_clean(view.get('consensus'))}\n\nA: {_clean(a)[:1200]}\n\nB: {_clean(b)[:1200]}"
            + '\n\nReturn JSON {"better":"A"|"B","why":"one sentence"}.')


def tournament(candidates: Sequence[Mapping[str, Any]], view: Mapping[str, Any],
               ask: Callable[[str], Any]) -> list[Mapping[str, Any]]:
    """Candidates ordered best first by pairwise wins, each pair judged in both orders (position bias)."""
    items = list(candidates)
    if len(items) < 2:
        return items
    wins = [0.0] * len(items)
    text = [_clean(c.get("insight") or c.get("text")) for c in items]
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            for first, second in ((i, j), (j, i)):
                try:
                    data = _parse(ask(pair_prompt(view, text[first], text[second])))
                except Exception:
                    continue
                better = _clean(data.get("better")).upper()
                if better == "A":
                    wins[first] += 1
                elif better == "B":
                    wins[second] += 1
    order = sorted(range(len(items)), key=lambda k: (-wins[k], k))
    return [dict(items[k], metadata=dict(_mapping(items[k].get("metadata")),
                                          variant_tournament={"wins": wins[k], "rank": rank + 1,
                                                              "of": len(items)}))
            for rank, k in enumerate(order)]
