"""Reader standard for insights (v10, 15 Nov 2026).

Why this exists
---------------
Of about 90 saved insights, the owner (the only reader whose judgment counts) rated exactly one
as worth stating: the 22 Oct breach-logs insight ("governments learned of the breach three months
late because only the developer could see what its agent did"). What made it good was a CONCRETE
FACT (a three-month delay), a plain MECHANISM (who could see the logs) and a CONSEQUENCE for named
parties. The app presented it as a theory label ("asymmetric telemetry bottleneck"), and the
support check then rejected it because the label went beyond what the sources said.

The other candidates she reviewed failed for reasons a prompt can describe:
- naming collision (10 Oct): a source already said almost the same thing; no consequence shown;
- dual-use contradiction (9 Oct): a contradiction pointed at, nothing follows from it;
- liability shifts to deployers (14 Nov): rests on a liability allocation no source states;
- philanthropy used to shape standards (25 Oct): attributes a motive no source gives.

Research basis
--------------
- Contrastive examples (a good and a bad output with the reason) steer a model better than
  longer instructions (contrastive in-context learning, arXiv 2401.17390).
- An interesting claim denies an assumption the reader holds, and must matter to what the reader
  does (Murray Davis, "That's Interesting!", 1971).
- Analytic writing leads with the judgment and its "so what" (CIA tradecraft notes; ICD 203).
- LLM judges and rankers disagree with human experts (Si, Yang & Hashimoto 2024: 17 of 49 picks
  overlapped), so the standard below is written from the owner's verdicts, not the model's taste.

This module has no model calls. It provides:
- READER_STANDARD: the block inserted into the insight-generation prompts;
- assess(): a cheap check, run before any grounding call, that a candidate rests on at least
  one concrete fact (date, duration, number, amount or named document) and that its opening
  sentence is not a theory label.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

VERSION = "reader-standard-v1.2-dates-are-not-figures"

READER_STANDARD = """WHAT THE READER VALUES (owner's standard - this overrides any lens, mode or exemplar below)
The reader is a business analyst. She keeps an insight only when ALL of these hold:
1. CONCRETE ANCHOR: it rests on a specific, checkable fact from the evidence - a date or gap between
   dates, a delay, a number, an amount, a named party, a reversal, or a clause in a named document.
2. CONSEQUENCE: it says what happens, or has happened, to whom because of that fact. "A contradiction
   exists", "the wording overlaps" or "X while Y" is not a consequence.
3. SOURCED PREMISES: every factual premise is stated by a cited record. Never assume an allocation
   (who bears liability, who pays) or a motive (why someone did it) that no record states.
4. NOT ALREADY SAID: no single source already states the conclusion or something close to it.
5. PLAIN MECHANISM: one plain sentence explains why the consequence follows.
6. It should ideally overturn something an informed reader would assume, or apply beyond this one
   event (other governments, other companies, the reader's own organisation).

HOW TO WRITE IT
- First sentence: the consequence, with the concrete fact in it, in plain words.
- Second sentence: the mechanism - why it happened or why it follows.
- Third sentence: what it means next, written to start with "Watch for" (a concrete observable
  development) or "Forecast:" (a labelled judgment). A forward-looking sentence without that start
  is checked as if it were a sourced fact and fails.
- No theory labels or coined phrases in the insight text ("asymmetric telemetry bottleneck",
  "information hub", "chokepoint", "structural paradox", "bifurcation", "value migration",
  "weaponized", "regime"). Say what actually happens instead.
- Leverage (who controls what, who needs it, what each side gains) still belongs in the leverage
  fields where they are asked for. In the insight text, say it in plain words after the
  consequence ("the developer, not the government, detected it"), not as a label.
- Never overstate: no "exclusive", "entirely", "all", "forces" or "only" unless a record says so.
- Keep the scope of the evidence: if the records describe one company, one government or one
  incident, the insight is about that one ("OpenAI", "Australia"), not about "model providers" or
  "governments" in general. Say "in this case" rather than widening it.
- Add nothing the records do not contain: no number, date, order or bill number, deadline or rule
  that no record states. Gaps you work out from two dated facts are fine ("about three months").
- Never state why someone acted (a motive) unless a record says so; otherwise leave it out.
- Do not stitch together events that no record connects. One development plus one fact that
  changes what it means is enough.
- If the evidence holds no concrete anchor with a consequence, return no hidden insight rather
  than a theme or an explanation. An honest empty result is better than a weak card.

CALIBRATION - THE OWNER'S OWN VERDICTS ON EARLIER OUTPUT (teaching only; these are NOT evidence and
must never be reused as facts in this run)
KEPT (the only one she kept), as it should have been written:
  "Governments learned of the AI-agent breach about three months after it happened (June to
  September). The model developer, not the government, detected it in August and reported it in
  September, so officials learned of it on the developer's timetable. Watch for a fixed incident-
  reporting deadline for AI developers; without one, government responses start months behind."
  How it was actually written (rejected for wording): "...demonstrates an asymmetric telemetry
  bottleneck, where public operators bear security risks ... while model developers retain
  exclusive diagnostic control." The theory label hid the fact and overstated it ("exclusive").
ONLY OKAY: "Renaming all AI 'Super Intelligence' collides with a bill that bans 'artificial
  superintelligence'." Why: a source already said almost this, and no consequence was shown. It
  would become an insight only with the bill's operative definition and what that brings into scope.
WEAK: "Companies ban 'abusive or cruel' consumer use while building warfighting prototypes." Why: a
  contradiction pointed at; nothing follows for anyone.
REJECTED: "Liability for model failures shifts entirely to enterprise deployers." Why: no source
  states who bears liability.
REJECTED: "Philanthropy is being used to shape AI standards." Why: a motive no source gives.
"""

# Concrete anchors: something a reader could check.
_MONTHS = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
           r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")
_NUMBER_WORDS = (r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|several|"
                 r"a few|dozens? of|hundreds? of|thousands? of")
_ANCHOR_PATTERNS = {
    "month": re.compile(rf"\b(?:{_MONTHS})\b\.?", re.I),
    "year": re.compile(r"\b(?:19|20)\d{2}\b"),
    "duration": re.compile(
        rf"\b(?:\d+|{_NUMBER_WORDS})[\s-]+(?:hours?|days?|weeks?|months?|quarters?|years?)\b", re.I),
    "amount": re.compile(
        r"(?:[$€£₹]\s?\d|\b\d[\d,.]*\s?(?:%|percent|per cent|million|billion|trillion|bn|crore|lakh)\b)",
        re.I),
    # A standalone number (not part of a model or product name such as GPT-4 or B2B).
    "number": re.compile(r"(?<![A-Za-z0-9\-])\d[\d,]*(?:\.\d+)?(?![A-Za-z\-]?[A-Za-z])"),
    # A named document: capitalised name words before the document type (case-sensitive, so
    # "a voluntary accord" does not count but "Ban Artificial Superintelligence Act" does).
    "document": re.compile(
        r"\b(?:[A-Z][\w'-]+\s+)+(?:Act|Bill|Order|Accord|Agreement|Directive|Regulation|Framework)\b"),
    "legal_record": re.compile(
        r"\bexecutive order\b|\bcourt (?:filing|ruling|order)\b|\blawsuit\b|\bsection \d", re.I),
}

# Theory labels and coined phrases the owner does not want leading an insight.
_JARGON = re.compile(
    r"\b(?:asymmetr\w*|bottleneck\w*|paradox\w*|bifurcat\w*|telemetry|commoditi[sz]\w*|panopticon|"
    r"chokepoints?|weaponi[sz]\w*|interdependen\w*|hegemon\w*|arbitrage|value migration|"
    r"feedback loops?|information hubs?|control points?|path[- ]dependen\w*|quasi-\w+|"
    r"sovereign utility|dual-use|regimes?|paradigms?|operationali[sz]\w*|structural(?:ly)?|"
    r"mental models?|incentive asymmetr\w*|risk transfer|gatekeep\w*)\b",
    re.I,
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, Mapping):
        return " ".join(_text(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_text(v) for v in value)
    return " ".join(str(value).split())


def first_sentence(text: str) -> str:
    text = _text(text)
    match = re.search(r"(?<=[.!?])\s+(?=[A-Z\"'(])", text)
    return text[: match.start()] if match else text


def anchors(text: Any) -> list[str]:
    """Kinds of concrete anchor found in the text (empty list: none)."""
    flat = _text(text)
    return [kind for kind, pattern in _ANCHOR_PATTERNS.items() if pattern.search(flat)]


def jargon(text: Any) -> list[str]:
    seen: list[str] = []
    for match in _JARGON.finditer(_text(text)):
        term = match.group(0).lower()
        if term not in seen:
            seen.append(term)
    return seen


def assess(insight_text: Any, supporting: Iterable[Any] = ()) -> dict[str, Any]:
    """Cheap, model-free check of one candidate against the reader standard.

    insight_text: the candidate's main text (thesis / insight sentence) - what the reader reads.
    supporting:   observations or premises the candidate cites; reported for diagnosis only.

    Fails (passes=False) when the insight text itself contains no concrete, checkable fact
    (date, duration, number, amount or named document). The reader judges the sentence she
    reads, and the cited observations almost always contain dates, so checking them would pass
    nearly everything. Measured on the owner's five verdicts (15 Nov 2026): the kept insight and
    the "okay" one pass; the weak one and both rejected ones fail.
    A theory label in the first sentence is reported (plain_wording=False) and lowers the
    ranking score, but is not a rejection, so a good idea that is badly worded is not lost.
    """
    insight = _text(insight_text)
    support = _text(list(supporting))
    insight_anchors = anchors(insight)
    support_anchors = anchors(support)
    lead = first_sentence(insight)
    lead_jargon = jargon(lead)
    all_jargon = jargon(insight)
    passes = bool(insight_anchors)
    reasons: list[str] = []
    if not passes:
        reasons.append("reader_standard_no_concrete_fact")
    if lead_jargon:
        reasons.append("reader_standard_theory_label_in_first_sentence")
    score = (2 if insight_anchors else (1 if support_anchors else 0)) - min(2, len(lead_jargon))
    return {
        "version": VERSION,
        "passes": passes,
        "anchored_in_text": bool(insight_anchors),
        "anchors": insight_anchors,
        "anchors_in_cited_facts": support_anchors,
        "plain_wording": not lead_jargon,
        "theory_labels": all_jargon,
        "score": score,
        "reasons": reasons,
    }


# Words that turn a backed point into an overstated one (15 Nov 2026 analysis of the saved runs:
# 25 of the 33 insights rated "good" used one of these, and the claim check failed many of them on
# exactly that word, e.g. "exclusive diagnostic control"). Allowed only when a cited record uses it.
_ABSOLUTES = re.compile(
    r"\b(?:exclusive(?:ly)?|entire(?:ly)?|compel(?:s|led|ling)?|forc(?:e|es|ed|ing)|inevitabl\w*|"
    r"guarantee\w*|massive(?:ly)?|profound(?:ly)?|structural(?:ly)?|fundamental(?:ly)?|unique(?:ly)?|"
    r"sole(?:ly)?|absolute(?:ly)?|complete(?:ly)?|always|never|only|must)\b",
    re.I,
)
_DIGITS = re.compile(r"(?<![A-Za-z0-9])\d[\d,]*(?:\.\d+)?")


_MONTH_WORD = (r"(january|february|march|april|may|june|july|august|september|october|november|december|"
               r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.?")


def _numbers(text: str) -> list[str]:
    """Numbers in the text, skipping model or product names (GPT-4, Gemini-2.5, B2B):
    a number joined by a hyphen to a capitalised word. "12-to-72-hour" yields 12 and 72."""
    out: list[str] = []
    for match in _DIGITS.finditer(text):
        start = match.start()
        if start > 0 and text[start - 1] == "-":
            word = re.search(r"([A-Za-z]+)-$", text[:start])
            if word and word.group(1)[0].isupper():
                continue
        # v13 (19 Nov 2026, simulated run): a day of the month ("24 September", "September 24")
        # is a date, not an invented figure; the day often comes from the article's publication
        # date (computed gaps), which no cited sentence repeats. It rejected the breach insight.
        before, after = text[max(0, start - 12):start], text[match.end():match.end() + 12]
        if re.search(_MONTH_WORD + r"\s*$", before, re.I) or re.match(r"\s*(of\s+)?" + _MONTH_WORD, after, re.I):
            if len(match.group(0).rstrip(".,")) <= 2:
                continue
        if re.fullmatch(r"20\d\d", match.group(0).rstrip(".,")) and re.search(
                _MONTH_WORD + r"(\s+\d{1,2})?,?\s*$", text[max(0, start - 16):start], re.I):
            continue   # the year of a written date ("24 September 2026")
        out.append(match.group(0).replace(",", "").rstrip("."))
    return out


def _word_stem(word: str) -> str:
    word = word.lower()
    for suffix in ("ingly", "ly", "ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def overreach(insight_text: Any, cited_text: Any) -> dict[str, list[str]]:
    """Model-free check for the two overreach patterns the claim check rejected most often.

    absolutes:  overstating words in the insight that no cited record uses;
    specifics:  numbers in the insight (order numbers, percentages, deadlines, counts) that no
                cited record contains - details the generator added, such as "Executive Order
                14434" or "a 12-to-72-hour rule" in the saved runs. Number words ("three months")
                are not checked, so a gap worked out from two dated facts stays allowed.
    The result names the exact words, so a repair can change only those (models fix a located
    error far more reliably than they find one: Tyen et al. 2024, arXiv 2311.08516).
    """
    insight = _text(insight_text)
    cited = _text(cited_text).lower()
    absolutes: list[str] = []
    for match in _ABSOLUTES.finditer(insight):
        word = match.group(0)
        stem = _word_stem(word)
        if not re.search(r"\b" + re.escape(stem), cited) and word.lower() not in absolutes:
            absolutes.append(word.lower())
    cited_numbers = set(_numbers(_text(cited_text)))
    # A deadline year in a forecast ("before 2027", "by 2028") is a judgment, not an added fact.
    deadline_years = set(re.findall(r"\b(?:by|before|until|through|within|after|in)\s+(?:early |mid-|late |the end of )?"
                                    r"((?:19|20)\d{2})\b", insight, re.I))
    specifics: list[str] = []
    for number in _numbers(insight):
        if number in deadline_years:
            continue
        if number and number not in cited_numbers and number not in specifics:
            specifics.append(number)
    return {"absolutes": absolutes, "specifics": specifics}


def overreach_brief(found: Mapping[str, Any]) -> list[str]:
    """Repair instruction naming the exact words to change, and nothing else."""
    lines: list[str] = []
    if found.get("absolutes"):
        lines.append("[overreach] Remove or soften these words; no cited record uses them: "
                     + ", ".join(f'"{w}"' for w in found["absolutes"]) + ".")
    if found.get("specifics"):
        lines.append("[overreach] Remove these numbers; no cited record contains them: "
                     + ", ".join(found["specifics"]) + ". Do not replace them with other details.")
    if lines:
        lines.append("[overreach] Keep the subject as narrow as the records: name the one company, government "
                     "or incident they describe instead of a whole group. Keep everything else the same.")
    return lines


def rank_key(assessment: Mapping[str, Any]) -> int:
    """Higher is better; for sorting candidates before costly checks."""
    return int(assessment.get("score") or 0)


__all__ = ["VERSION", "READER_STANDARD", "assess", "anchors", "jargon", "first_sentence", "rank_key",
           "overreach", "overreach_brief"]
