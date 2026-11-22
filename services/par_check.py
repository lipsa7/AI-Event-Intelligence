"""The editor's bar: would the user approve this insight? (v13.22, 22 Nov 2026)

Why
---
The user's requirement: an original insight must read as well as the sites that publish original
analysis - not "a cheap thing that looks cheap". Passing the claim-support and non-obviousness gates
shows an insight is backed and not stated by its sources; it does not show it is GOOD. The user has
already labelled 90 of the app's insights (data/insight_review_90.csv: 33 good, 44 explanatory or
speculative, 13 generic restatement). Following Hamel Husain's critique shadowing and the research
report of 22 Nov (section 4), the judge is shown the user's own approved and rejected examples (few-
shot) and a rubric built from the US analytic tradecraft standards (ICD 203: separate facts from
assumptions and judgments, consider alternatives, state implications for the reader, logical
argument) and the app's reader standard. It decides PASS only if the user would approve the
candidate as at least as good as her approved examples, and names the one element missing otherwise;
that becomes the repair brief.

How the judge is checked before it is trusted
---------------------------------------------
tools/calibrate_par.py runs it on the labelled rows NOT used as examples and prints how often it
passes the user's "good" rows (true-positive rate) and fails the others (true-negative rate),
separately (Husain: one agreement number hides a judge that passes everything).

Rules: one model call per candidate that has already passed every other gate; fail-open is NOT used
for a quality bar - if the judge cannot answer, the candidate is held back and logged
(par_check_unavailable), because publishing an unjudged insight is what the user asked to stop.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

VERSION = "par-check-v1"
LABELS_ENV = "EVENT_INTELLIGENCE_PAR_LABELS"
DEFAULT_LABELS = os.path.join("data", "insight_review_90.csv")
# Fixed few-shot rows (row "no" in the CSV), chosen once so calibration rows never overlap them.
EXAMPLE_ROWS = {"good": ["68", "74", "43", "86"], "generic restatement": [], "explanatory/speculative": []}
EXAMPLES_PER_FAIL_LABEL = 2
MISSING_CODES = ("not_beyond_sources", "no_specific_mechanism", "no_consequence_for_named_actor",
                 "no_checkable_signal", "jargon_over_substance", "unsupported_leap", "none")

RUBRIC = (
    "An insight passes only if a demanding editor of a serious analysis publication would run it, and it is at "
    "least as good as the APPROVED examples. All of these must hold:\n"
    "1. BEYOND THE SOURCES - it says something the news reports and published analysis do not already say.\n"
    "2. SPECIFIC MECHANISM - it names who holds what, who depends on it, and which rule, number, deadline or "
    "incentive makes the outcome follow; not 'structural shifts' or 'asymmetries' in general.\n"
    "3. CONSEQUENCE FOR A NAMED ACTOR - what a named company, agency or group will be forced or likely to do.\n"
    "4. CHECKABLE SIGNAL - a dated forecast or a concrete thing to watch that would show it right or wrong.\n"
    "5. PLAIN SUBSTANCE - said in plain words it is still non-obvious; strong vocabulary is not substance.\n"
    "6. EARNED - each step follows from the step before; facts, assumptions and judgments are kept apart.\n"
)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def load_labels(path: str | os.PathLike | None = None) -> list[dict[str, str]]:
    target = Path(path or os.getenv(LABELS_ENV) or DEFAULT_LABELS)
    if not target.exists():
        return []
    with target.open(encoding="utf-8-sig", newline="") as handle:
        rows = [dict(r) for r in csv.DictReader(handle)]
    return [r for r in rows if _clean(r.get("insight")) and _clean(r.get("my_rating"))]


def split(rows: Sequence[Mapping[str, str]]) -> tuple[list[Mapping[str, str]], list[Mapping[str, str]]]:
    """(few-shot examples, held-out rows for calibration)."""
    chosen: list[Mapping[str, str]] = []
    wanted_good = set(EXAMPLE_ROWS["good"])
    chosen += [r for r in rows if r.get("no") in wanted_good]
    for label in ("generic restatement", "explanatory/speculative"):
        fails = [r for r in rows if _clean(r.get("my_rating")) == label]
        chosen += fails[:EXAMPLES_PER_FAIL_LABEL]
    ids = {r.get("no") for r in chosen}
    return chosen, [r for r in rows if r.get("no") not in ids]


def is_pass_label(rating: str) -> bool:
    return _clean(rating).casefold() == "good"


def prompt(candidate: str, examples: Sequence[Mapping[str, str]], *, sources_summary: str = "") -> str:
    shots = []
    for row in examples:
        verdict = "APPROVED" if is_pass_label(row.get("my_rating", "")) else f"REJECTED ({_clean(row.get('my_rating'))})"
        shots.append(f"- {verdict}: {_clean(row.get('insight'))[:500]}")
    return (
        "You are the editor of a serious strategic-analysis publication deciding whether to publish an insight.\n"
        + RUBRIC
        + "\nThe publication's owner labelled earlier insights like this:\n" + "\n".join(shots)
        + ("\n\nWhat the sources and published analysis already say:\n" + sources_summary[:1500] if sources_summary else "")
        + "\n\nCANDIDATE:\n" + _clean(candidate)[:1500]
        + "\n\nReturn JSON {\"verdict\":\"pass\"|\"fail\",\"missing\":\"" + "|".join(MISSING_CODES)
        + "\",\"closest_example\":\"APPROVED or REJECTED\",\"fix\":\"one sentence: what the insight must add to pass\"}."
          " Pass only if every rule holds and it is at least as good as the APPROVED examples."
    )


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


def judge(candidate: str, ask: Callable[[str], Any], *, examples: Sequence[Mapping[str, str]] | None = None,
          sources_summary: str = "") -> dict[str, Any]:
    """{"passed": bool, "missing": code, "fix": text, "available": bool}."""
    if examples is None:
        examples = split(load_labels())[0]
    if not examples:
        return {"passed": True, "available": False, "missing": "", "fix": "", "reason": "no_labels"}
    try:
        data = _parse(ask(prompt(candidate, examples, sources_summary=sources_summary)))
    except Exception as error:
        return {"passed": False, "available": False, "missing": "", "fix": "",
                "reason": "par_check_unavailable:" + type(error).__name__}
    verdict = _clean(data.get("verdict")).casefold()
    if verdict not in {"pass", "fail"}:
        return {"passed": False, "available": False, "missing": "", "fix": "", "reason": "par_check_unavailable:bad_answer"}
    missing = _clean(data.get("missing"))
    if missing not in MISSING_CODES:
        missing = "none" if verdict == "pass" else "unsupported_leap"
    return {"passed": verdict == "pass", "available": True, "missing": missing, "fix": _clean(data.get("fix"))[:300],
            "closest": _clean(data.get("closest_example"))[:20], "reason": ""}


def brief(result: Mapping[str, Any]) -> str:
    code = _clean(result.get("missing"))
    fix = _clean(result.get("fix"))
    return (f"[editor's bar] Below the standard of the approved examples ({code.replace('_', ' ')}). "
            + (fix + " " if fix else "")
            + "Keep every step backed; add the missing element, do not reword.")


# ---------------------------------------------------------------- v13.27: head to head with an approved insight
# Research report of 22 Nov, stage I: compare the top candidate with a stored "good" exemplar of the
# user's, in both orders (position bias, Zheng et al. NeurIPS 2023); publish only if its win probability
# is at least 0.5. The exemplar is the approved example most similar in wording to the candidate.
def exemplar(candidate: str, examples: Sequence[Mapping[str, str]]) -> str:
    good = [_clean(r.get("insight")) for r in examples if is_pass_label(r.get("my_rating", ""))]
    if not good:
        return ""
    words = set(_clean(candidate).casefold().split())
    return max(good, key=lambda text: len(words & set(text.casefold().split())))


def head_to_head_prompt(first: str, second: str) -> str:
    return ("Which insight would a demanding editor of a serious analysis publication rather publish? Judge the "
            "substance: says something the news does not, a specific mechanism with a number, a named actor's forced "
            "move, a call checkable soon. Length and vocabulary are not quality.\n\nA: " + _clean(first)[:1200]
            + "\n\nB: " + _clean(second)[:1200] + '\n\nReturn JSON {"better":"A"|"B"|"tie"}.')


def head_to_head(candidate: str, ask: Callable[[str], Any], examples: Sequence[Mapping[str, str]] | None = None) -> dict[str, Any]:
    """{"win_probability": 0..1 or None, "exemplar": text}. Both orders; a tie counts half."""
    if examples is None:
        examples = split(load_labels())[0]
    target = exemplar(candidate, examples)
    if not target:
        return {"win_probability": None, "exemplar": "", "reason": "no_exemplar"}
    score, answered = 0.0, 0
    for first, second, mine in ((candidate, target, "A"), (target, candidate, "B")):
        try:
            better = _clean(_parse(ask(head_to_head_prompt(first, second))).get("better")).upper()
        except Exception:
            continue
        if better in {"A", "B", "TIE"}:
            answered += 1
            score += 1.0 if better == mine else 0.5 if better == "TIE" else 0.0
    if not answered:
        return {"win_probability": None, "exemplar": target[:300], "reason": "unanswered"}
    return {"win_probability": round(score / answered, 2), "exemplar": target[:300], "orders_answered": answered}
