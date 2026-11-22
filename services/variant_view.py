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

VERSION = "variant-view-v5-variant-perception"
MIN_HORIZON_DAYS = 14
MAX_HORIZON_DAYS = 84
MAX_CANDIDATES = 3
# Any figure or date: a digit (amount, share, count, day, year), a month name ("in June"), or a number
# word ("three months", "a dozen"). v13.27.2: digits only failed "the June breach was disclosed in
# September, three months later" - a date-anchored candidate the lens rules ask for ("a cited number or date").
_MONTHS = ("january|february|march|april|june|july|august|september|october|november|december")
_NUMBER = re.compile(
    # v13.27.2 + review: "may", "one", "half", "double" and short month names ("mar", "dec") are ordinary
    # words ("OpenAI may face one more suit"), so they are left out - with a digit beside them ("May 2026",
    # "12 Dec") the digit already counts.
    r"\d|\b(?:" + _MONTHS + r"|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|dozen|hundred|"
    r"thousand|million|billion|trillion)\b", re.I)


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


def base_rate_line(view: Mapping[str, Any] | None) -> str:
    rate = _mapping(_mapping(view).get("base_rate"))
    if not rate or rate.get("rate") is None:
        return ""
    return (" OUTSIDE VIEW (Green & Armstrong structured analogies, computed): " + _clean(rate.get("text"))
            + ". Start the likelihood from this rate and say how this case differs.")


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
        "name in analytical_lenses; (8) in what_to_watch give 2-3 signals, each naming the public source to watch "
        "(a docket number, an SEC form type, a Federal Register agency or document, an official channel)."
        + base_rate_line(view) + " Write the first sentence of the insight as the thesis in at most 35 plain words; "
        "no coined labels (\"structural asymmetry\", \"blind spot\", \"paradigm\"). If no candidate meets all of "
        "this, return fewer or none."
    )


def lens_instruction(view: Mapping[str, Any], lens: str, fact_ids: Sequence[str] = ()) -> str:
    """v13.26 (research report of 22 Nov, stage D): the rules for ONE lens in ONE short generation call.
    Same requirements as instruction(), for a single candidate; the consensus is listed when there is one."""
    text = dict(LENSES).get(lens, lens)
    claims = [c for c in view.get("claims") or [] if isinstance(c, Mapping)] if view else []
    listed = "\n".join(f"CONSENSUS[{n}] ({c.get('source')}{', ' + c['date'] if c.get('date') else ''}): {c['claim']}"
                       for n, c in enumerate(claims, start=1))
    if not listed and view and _clean(view.get("consensus")):
        listed = f"CONSENSUS[1]: {_clean(view.get('consensus'))}"
    facts = (" Primary records (federal rules, filings, dockets) are in the evidence as " + ", ".join(list(fact_ids)[:12])
             + " - prefer their dates and numbers." if fact_ids else "")
    return (
        f"LENS: {lens} - {text}.\n"
        + ("WHAT INFORMED READERS ALREADY BELIEVE:\n" + listed + "\n" if listed else "")
        + "Write ONE candidate through this lens only. It must: "
        + ("(1) name in novel_proposition which CONSENSUS[n] it contradicts or materially refines and why that "
           "consensus misses it; " if listed else "(1) say in novel_proposition what an informed reader of these "
           "reports would not already conclude; ")
        + "(2) rest on the constraint or mechanism this lens reveals - stated intentions are not constraints; "
          "(3) carry at least one cited number or date in the insight text;" + facts + " (4) name the actor and "
          "the move it is forced into; (5) give a forecast_contract: a yes/no question resolvable within 2 to 12 "
          "weeks from a named public source (a docket, a Federal Register date, a filing, an official statement), "
          "with its deadline and resolution rule; (6) put what you would see if the consensus is right instead in "
          "alternative_explanations; (7) in what_to_watch give 2-3 signals, each naming the public source to watch (a "
          "docket number, an SEC form type, a Federal Register agency or document, an official channel). First "
          "sentence of the insight: the thesis in at most " + str(THESIS_MAX_WORDS) + " plain words, no coined labels "
          "(\"structural asymmetry\", \"blind spot\", \"paradigm\")." + base_rate_line(view)
        + (VARIANT_PERCEPTION_RULES if listed else VARIANT_PERCEPTION_RULES_NO_CONSENSUS)
    )


# v13.29 (research report of 22 Nov, P1): variant perception as Steinhardt asked his analysts for it - the
# idea, the consensus view, the variant perception and the trigger - with ICD 203's rival hypothesis and
# load-bearing assumption. Each part except the final judgment is checked by code (perception_problems).
VARIANT_PERCEPTION_RULES = (
    " Fill variant_perception: consensus_quote = words copied exactly from one CONSENSUS line you depart from; "
    "overlooked_evidence_id = the ONE evidence ID from THIS story that the consensus does not use, and why it "
    "misses it; mechanism_chain = at least two links from that fact to the conclusion, each citing evidence IDs, "
    "with the overlooked fact and the named actor inside the chain and any number in the insight carried by a "
    "link (not added for colour); rival_hypothesis = the strongest other explanation, not a straw man; "
    "load_bearing_assumption = the one assumption that, if false, kills the thesis. The forecast must be much "
    "more likely if the thesis is right than if it is wrong - never a step that happens in almost every case "
    "(a defendant answering, moving to dismiss, appealing).")
VARIANT_PERCEPTION_RULES_NO_CONSENSUS = (VARIANT_PERCEPTION_RULES
                                        .replace("consensus_quote = words copied exactly from one CONSENSUS line you "
                                                 "depart from; ", "consensus_quote = empty; ")
                                        .replace("the consensus does not use", "these reports do not lead with")
                                        .replace("it misses it", "readers would miss it"))


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


# v13.28.3: in the 22 Nov test all four top candidates forecast "OpenAI will file a motion to dismiss / demurrer"
# - steps a defendant takes in nearly every lawsuit, so the call is right whatever the insight says.
_ROUTINE_CALL = re.compile(
    r"\b(motion to dismiss|demurrer|responsive pleading|file (?:an? )?(?:answer|response|appeal)|"
    r"respond to the (?:lawsuit|complaint|suit)|answer the (?:lawsuit|complaint)|notice of appeal|"
    r"will (?:appeal|deny|contest|challenge) (?:the|this) (?:lawsuit|ruling|allegations|claims))\b", re.I)
_FIGURE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?(?:\s?(?:billion|million|trillion|bn|m|%|percent))?", re.I)


def call_problems(candidate: Mapping[str, Any]) -> list[str]:
    contract = _mapping(candidate.get("forecast_contract")) or _mapping(
        _mapping(candidate.get("metadata")).get("forecast_contract"))
    return ["routine_call"] if _ROUTINE_CALL.search(_clean(contract.get("question"))) else []


def number_problems(candidate: Mapping[str, Any], records: Sequence[Any]) -> list[str]:
    """v13.28.3: a figure in the insight that only a record about ANOTHER story contains. On 22 Nov both Texas
    winners carried "$121.4 billion" of quarterly AI infrastructure spending - a run-wide context record - to
    satisfy the number rule. A figure in one of the story's own reports, or in a context record that shares
    distinctive words with the story, is fine; a figure in no record is left to the claim check."""
    from services import primary_data
    rows = [_mapping(r) for r in records or [] if isinstance(r, Mapping)]
    story = [r for r in rows if _clean(r.get("event_id"))]
    if not story:
        return []
    story_text = " ".join(_clean(r.get("text") or r.get("claim")) for r in story)
    corpus = [_clean(r.get("text") or r.get("claim")) for r in rows]
    problems = []
    text = _clean(candidate.get("insight") or candidate.get("text"))
    for match in _FIGURE.finditer(text):
        figure = match.group(0).strip()
        digits = re.sub(r"[^\d.]", "", figure).strip(".")
        if len(digits.replace(".", "")) < 2 or (len(digits) == 4 and digits.startswith(("19", "20"))):
            continue   # single digits and years are not the figures this rule is about
        holders = [r for r in rows if digits and digits in re.sub(r"[^\d.]", " ", _clean(r.get("text") or r.get("claim"))).split()]
        if not holders or any(_clean(r.get("event_id")) for r in holders):
            continue
        kept, _ = primary_data.relevant([{"text": _clean(r.get("text") or r.get("claim"))} for r in holders],
                                        story_text, corpus)
        if not kept:
            problems.append("number_off_story:" + figure)
    return list(dict.fromkeys(problems))[:2]


# ---------------------------------------------------------------- 3f. variant perception, checked by code (P1, P4)
def _vp(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(candidate.get("variant_perception")) or _mapping(
        _mapping(candidate.get("metadata")).get("variant_perception"))


def _norm_words(text: Any) -> str:
    return " ".join("".join(c.casefold() if c.isalnum() else " " for c in _clean(text)).split())


def perception_problems(candidate: Mapping[str, Any], view: Mapping[str, Any], records: Sequence[Any]) -> list[str]:
    """v13.29 (research report of 22 Nov, P1 and P4). Empty list: the variant perception holds together.
    * the consensus is QUOTED (string match against the consensus map; a paraphrase invites a straw man);
    * the overlooked fact is an evidence ID of this story that no consensus source cites;
    * a mechanism chain of at least two cited links carries the overlooked fact and the named actor;
    * a rival hypothesis and a load-bearing assumption are stated (ICD 203);
    * every figure in the insight comes from a record the chain cites (the number carries the argument)."""
    from services import primary_data
    vp = _vp(candidate)
    if not vp:
        return ["no_variant_perception"]
    rows = {_clean(_mapping(r).get("evidence_id")): _mapping(r) for r in records or [] if isinstance(r, Mapping)}
    problems: list[str] = []
    claims = [c for c in _mapping(view).get("claims") or [] if isinstance(c, Mapping)]
    quote = _norm_words(vp.get("consensus_quote"))
    if claims and (len(quote.split()) < 5 or not any(quote in _norm_words(c.get("claim")) for c in claims)):
        problems.append("consensus_not_quoted")
    fact_id = _clean(vp.get("overlooked_evidence_id"))
    fact = rows.get(fact_id)
    consensus_sources = {_clean(c.get("source")) for c in claims}
    story = [r for r in rows.values() if _clean(r.get("event_id"))]
    story_text = " ".join(_clean(r.get("text") or r.get("claim")) for r in story)
    if not fact:
        problems.append("overlooked_fact_missing")
    elif fact_id in consensus_sources:
        problems.append("overlooked_fact_used_by_consensus")
    elif not _clean(fact.get("event_id")) and story_text and not primary_data.relevant(
            [{"text": _clean(fact.get("text") or fact.get("claim"))}], story_text,
            [_clean(r.get("text")) for r in rows.values()])[0]:
        problems.append("overlooked_fact_off_story")
    chain = [step for step in vp.get("mechanism_chain") or [] if isinstance(step, Mapping)]
    cited_links = [step for step in chain if any(_clean(e) in rows for e in step.get("evidence_ids") or [])]
    if len(cited_links) < 2:
        problems.append("mechanism_chain_too_short")
    chain_ids = {_clean(e) for step in chain for e in step.get("evidence_ids") or []}
    chain_text = " ".join(_clean(step.get("step")) for step in chain).casefold()
    if fact and fact_id not in chain_ids:
        problems.append("overlooked_fact_not_in_chain")
    holder = _clean(_mapping(candidate.get("leverage_analysis")).get("holder") or _mapping(
        _mapping(candidate.get("metadata")).get("leverage_analysis")).get("holder"))
    if holder and holder.casefold().split()[0] not in chain_text:
        problems.append("actor_not_in_chain")
    if not _clean(vp.get("rival_hypothesis")):
        problems.append("no_rival_hypothesis")
    if not _clean(vp.get("load_bearing_assumption")):
        problems.append("no_load_bearing_assumption")
    text = _clean(candidate.get("insight") or candidate.get("text"))
    cited_text = " ".join(_clean(rows[e].get("text") or rows[e].get("claim")) for e in chain_ids if e in rows)
    for match in _FIGURE.finditer(text):
        figure = match.group(0).strip()
        digits = re.sub(r"[^\d.]", "", figure).strip(".")
        if len(digits.replace(".", "")) < 2 or (len(digits) == 4 and digits.startswith(("19", "20"))):
            continue
        if digits not in re.sub(r"[^\d.]", " ", cited_text).split() and digits not in chain_text:
            problems.append("number_not_in_chain:" + figure)
            break
    return problems


# ---------------------------------------------------------------- 3g. forecast diagnosticity (P2)
# v13.29 (research report of 22 Nov, P2): a forecast is worth making only if it is genuinely uncertain (an
# outside-view probability between 20% and 80%) and DIAGNOSTIC - much more likely if the thesis is right
# than if it is wrong (gap >= 0.25). That is the Bayesian core of ACH and of ICD 203's indicators, without
# the matrix ritual that failed in the human experiments (Dhami, Belton & Mandel 2019). "OpenAI files a
# demurrer" is about 0.95 either way, so it fails without a hand-written list.
OUTSIDE_VIEW_RANGE = (0.2, 0.8)
MIN_DIAGNOSTIC_GAP = 0.25


def diagnosticity_prompt(candidate: Mapping[str, Any]) -> str:
    from services import base_rates
    contract = _mapping(candidate.get("forecast_contract")) or _mapping(
        _mapping(candidate.get("metadata")).get("forecast_contract"))
    return ("You are a forecaster. Give three probabilities for the FORECAST.\n"
            "1. p_outside_view: from base rates of comparable events only, ignoring the THESIS.\n"
            "2. p_if_thesis_true: if the THESIS is right.\n3. p_if_thesis_false: if the THESIS is wrong.\n"
            + base_rates.prompt_block()
            + "\n\nTHESIS: " + _clean(candidate.get("insight") or candidate.get("text"))[:900]
            + "\nFORECAST: " + _clean(contract.get("question"))[:300] + " (by " + _clean(contract.get("deadline"))[:10]
            + ")\n\nReturn JSON {\"p_outside_view\":0.0,\"p_if_thesis_true\":0.0,\"p_if_thesis_false\":0.0,"
              "\"reference_class\":\"which class you used\"}.")


def diagnosticity(candidate: Mapping[str, Any], asks: Sequence[Callable[[str], Any]]) -> dict[str, Any]:
    """Median over the judges that answer. {"checked", "problems", "outside", "gap", "answers"}."""
    import statistics
    answers = []
    for ask in asks:
        try:
            data = _parse(ask(diagnosticity_prompt(candidate)))
            row = {k: float(data[k]) for k in ("p_outside_view", "p_if_thesis_true", "p_if_thesis_false")}
        except Exception:
            continue
        if all(0.0 <= v <= 1.0 for v in row.values()):
            answers.append(dict(row, reference_class=_clean(data.get("reference_class"))[:120]))
    if not answers:
        return {"checked": False, "problems": [], "answers": []}
    outside = statistics.median(a["p_outside_view"] for a in answers)
    gap = statistics.median(a["p_if_thesis_true"] - a["p_if_thesis_false"] for a in answers)
    problems = []
    if not OUTSIDE_VIEW_RANGE[0] <= outside <= OUTSIDE_VIEW_RANGE[1]:
        problems.append("forecast_not_uncertain")
    if gap < MIN_DIAGNOSTIC_GAP:
        problems.append("forecast_not_diagnostic")
    return {"checked": True, "problems": problems, "outside": round(outside, 2), "gap": round(gap, 2),
            "answers": answers}


# ---------------------------------------------------------------- 3h. load-bearing number (P4)
def ablation_prompt(candidate: Mapping[str, Any]) -> str:
    return ("Remove every number and date from this insight. Does its conclusion still follow just as well "
            "without them? If yes, the numbers were decoration.\nINSIGHT: "
            + _clean(candidate.get("insight") or candidate.get("text"))[:1200]
            + '\nReturn JSON {"still_follows": true|false, "why": "one sentence"}.')


def number_is_decorative(candidate: Mapping[str, Any], ask: Callable[[str], Any]) -> bool | None:
    if not _FIGURE.search(_clean(candidate.get("insight") or candidate.get("text"))):
        return None
    try:
        value = _parse(ask(ablation_prompt(candidate))).get("still_follows")
    except Exception:
        return None
    return value if isinstance(value, bool) else None


# ---------------------------------------------------------------- 2b. structured analogies (stage C)
# v13.27 (research report of 22 Nov, stage C): Green & Armstrong (International Journal of Forecasting,
# 2007) - structured analogies beat unaided experts (46% vs 32% accuracy; 60% with two or more analogies)
# when each analogy's similarity is RATED and the forecast is derived MECHANICALLY from the ratings, not
# from the analyst's favourite case. The outside model rates each past case 0-10 on mechanism and says
# whether the comparable outcome followed; the base rate is the similarity-weighted share, computed here.
def analogue_prompt(labels: Sequence[str], cases: Sequence[Mapping[str, Any]]) -> str:
    lines = [f"[{c['case_id']}] ({c.get('year') or '?'}) {c['text']}" + (f" Outcome: {c['outcome']}" if c.get("outcome") else "")
             for c in cases]
    return ("CURRENT STORY: " + " | ".join(labels) + "\n\nPAST CASES:\n" + "\n".join(lines)
            + "\n\nFor each past case rate how similar its MECHANISM is to the current story (0 = unrelated, "
              "10 = same actors' roles, same lever, same harm pathway; sector and company names do not matter) and say "
              "whether the outcome a reader would expect for the current story ACTUALLY FOLLOWED in that past case "
              "(yes, no, or unknown if the case text does not say). Return JSON {\"cases\":[{\"case_id\":\"\","
              "\"similarity\":0,\"outcome_followed\":\"yes|no|unknown\",\"expected_outcome\":\"short\"}]}.")


def analogue_cases(records: Sequence[Mapping[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    out = []
    for record in records or []:
        case = _mapping(record.get("historical_case"))
        if not case:
            continue
        text = _clean(record.get("text") or record.get("source_excerpt"))[:260]
        if text:
            out.append({"case_id": _clean(record.get("evidence_id")), "text": text,
                        "year": _clean(case.get("year") or case.get("date"))[:10],
                        "outcome": _clean(case.get("outcome") or case.get("outcome_quote"))[:200]})
    return out[:limit]


def base_rate(rated: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Similarity-weighted share of comparable cases in which the outcome followed (unknowns excluded)."""
    rated = [r for r in rated or [] if isinstance(r, Mapping)]
    usable = [r for r in rated if isinstance(r.get("similarity"), (int, float)) and r.get("outcome_followed") in {"yes", "no"}]
    weight = sum(float(r["similarity"]) for r in usable)
    if not usable or weight <= 0:
        return {"cases_rated": len(rated), "cases_used": 0, "rate": None,
                "text": "no rated past case with a known outcome"}
    rate = sum(float(r["similarity"]) for r in usable if r["outcome_followed"] == "yes") / weight
    strength = "weak (fewer than 3 cases)" if len(usable) < 3 else "usable"
    return {"cases_rated": len(rated), "cases_used": len(usable), "rate": round(rate, 2),
            "text": (f"in {len(usable)} rated past case(s), similarity-weighted, the expected outcome followed "
                     f"{round(rate * 100)}% of the time - {strength}")}


def rate_analogues(labels: Sequence[str], cases: Sequence[Mapping[str, Any]], ask: Callable[[str], Any]) -> dict[str, Any]:
    if not cases:
        return base_rate([])
    try:
        data = _parse(ask(analogue_prompt(labels, cases)))
    except Exception as error:
        return dict(base_rate([]), error=type(error).__name__)
    known = {c["case_id"] for c in cases}
    rated = []
    for row in data.get("cases") or []:
        if not isinstance(row, Mapping) or _clean(row.get("case_id")) not in known:
            continue
        try:
            similarity = max(0.0, min(10.0, float(row.get("similarity"))))
        except (TypeError, ValueError):
            continue
        followed = _clean(row.get("outcome_followed")).casefold()
        rated.append({"case_id": _clean(row.get("case_id")), "similarity": similarity,
                      "outcome_followed": followed if followed in {"yes", "no"} else "unknown",
                      "expected_outcome": _clean(row.get("expected_outcome"))[:160]})
    return dict(base_rate(rated), rated=rated)


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
THESIS_MAX_WORDS = 25   # v13.29: research report of 22 Nov, P1 (was 35)


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
              'shows it (quote the record id). Then the KEY ASSUMPTIONS CHECK (CIA Tradecraft Primer): list up to 3 '
              'things that must be true for the call, each with what would undermine it. Return JSON {"reasons":'
              '[{"reason":"","already_in_evidence":true|false,"evidence_id":""}],"key_assumptions":[{"assumption":"",'
              '"undermined_by":""}]}.')


def premortem(candidate: Mapping[str, Any], evidence_lines: Sequence[str], ask: Callable[[str], Any]) -> dict[str, Any]:
    try:
        data = _parse(ask(premortem_prompt(candidate, evidence_lines)))
    except Exception as error:
        return {"killed": False, "checked": False, "error": type(error).__name__}
    reasons = [r for r in data.get("reasons") or [] if isinstance(r, Mapping)]
    shown = [r for r in reasons if r.get("already_in_evidence") is True and _clean(r.get("evidence_id"))]
    assumptions = [{"assumption": _clean(a.get("assumption"))[:200], "undermined_by": _clean(a.get("undermined_by"))[:200]}
                   for a in data.get("key_assumptions") or [] if isinstance(a, Mapping) and _clean(a.get("assumption"))][:3]
    return {"killed": bool(shown), "checked": bool(reasons), "key_assumptions": assumptions,
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
    "no_variant_perception": ("fill variant_perception: the consensus quote, the overlooked fact, the mechanism chain, "
                              "the rival hypothesis and the load-bearing assumption"),
    "consensus_not_quoted": "copy into consensus_quote the exact words of the CONSENSUS line you depart from",
    "overlooked_fact_missing": "name in overlooked_evidence_id one evidence ID from the records that the consensus does not use",
    "overlooked_fact_used_by_consensus": "the overlooked fact must be one the consensus sources do NOT already use",
    "overlooked_fact_off_story": "the overlooked fact must be about THIS story, not a run-wide figure",
    "mechanism_chain_too_short": "give at least two mechanism_chain links, each citing evidence IDs",
    "overlooked_fact_not_in_chain": "put the overlooked fact's evidence ID into a mechanism_chain link - it must carry the argument",
    "actor_not_in_chain": "name the actor whose behaviour changes inside a mechanism_chain link",
    "no_rival_hypothesis": "state the strongest rival explanation (not a straw man)",
    "no_load_bearing_assumption": "state the one assumption that, if false, kills the thesis",
    "forecast_not_uncertain": ("the forecast is near-certain or near-impossible whatever the thesis says; choose one a "
                               "forecaster would put between 20% and 80% from base rates alone"),
    "forecast_not_diagnostic": ("choose a forecast that is much more likely if the thesis is right than if it is wrong "
                                "(a specific ruling, settlement term, policy change or another party's move)"),
    "number_decorative": "the conclusion holds without its number - use a number the mechanism depends on, or none",
    "routine_call": ("the forecast is a routine step that almost always happens (a defendant answering, moving to "
                     "dismiss or appealing). Forecast something genuinely uncertain that this insight's point predicts "
                     "and its opposite would not: a ruling, a settlement, a product, price or policy change, or "
                     "another party's move"),
}


def need_text(code: str) -> str:
    if code in NEEDS_TEXT:
        return NEEDS_TEXT[code]
    if code.startswith("number_not_in_chain:"):
        return ("the number " + code.split(":", 1)[1] + " is not in any record the mechanism chain cites; make it a "
                "link of the chain or drop it")
    if code.startswith("number_off_story:"):
        return ("the number " + code.split(":", 1)[1] + " comes from a record about something else; use a number "
                "or date from THIS story's records, or none")
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
              '"deadline":"ISO date","resolution_criteria":"public source and rule"},"holder":"named actor",'
              '"variant_perception":{"consensus_quote":"","overlooked_evidence_id":"","why_consensus_misses_it":"",'
              '"mechanism_chain":[{"step":"","evidence_ids":[]}],"rival_hypothesis":"","load_bearing_assumption":""}}.'
            + ("\nCURRENT VARIANT PERCEPTION: " + json.dumps(_vp(candidate), ensure_ascii=False)[:1500] if _vp(candidate) else ""))


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
    perception = _mapping(data.get("variant_perception"))
    if perception and any(_clean(v) for k, v in perception.items() if k != "mechanism_chain"):
        chain = [{"step": _clean(_mapping(x).get("step"))[:400],
                  "evidence_ids": [_clean(e) for e in _mapping(x).get("evidence_ids") or [] if _clean(e)][:6]}
                 for x in perception.get("mechanism_chain") or [] if _clean(_mapping(x).get("step"))]
        fixed = {k: _clean(perception.get(k))[:400] for k in ("consensus_quote", "overlooked_evidence_id",
                 "why_consensus_misses_it", "rival_hypothesis", "load_bearing_assumption")}
        fixed["mechanism_chain"] = chain[:6]
        out["variant_perception"] = fixed
        meta["variant_perception"] = fixed
    meta["variant_repaired"] =  {"fixed": list(problems), "original_insight": _clean(candidate.get("insight") or candidate.get("text"))[:900]}
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
