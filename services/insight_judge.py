"""Insight judge on a free open model, aligned to the owner's own ratings (v11, 15 Nov 2026).

Why a separate judge
--------------------
Gemini writes the insights, so a Gemini judge grades its own family's work and shares its blind
spots (Panickssery et al. 2024: LLM evaluators recognise and favour their own generations). This
judge runs on an open-weight model, never on Gemini:

* Groq's free tier (``GROQ_API_KEY``), model ``INSIGHT_JUDGE_MODEL``, default
  ``openai/gpt-oss-120b`` (open weights, Apache 2.0); or
* a local Ollama model (``INSIGHT_JUDGE_PROVIDER=ollama``, model ``OLLAMA_JUDGE_MODEL``,
  default ``qwen2.5:14b``), which costs nothing at all.

What it does
------------
1. ``verdict``: pass or fail against the owner's standard, with a one-line reason. Binary,
   because graded scales drift between calls (Zheng et al. 2023, MT-Bench).
2. ``compare``: which of two insights is better, asked in BOTH orders; a winner counts only when
   both orders agree, otherwise it is a tie (position bias, Wang et al. 2023).
3. ``tournament``: every pair of up to five items is compared; ranked by wins. Used to order lab
   theses and released insights, and ONLY once the judge agrees with the owner (``is_aligned``),
   as the research report advised.
4. ``agreement``: the judge's verdicts against the owner's ratings in
   ``data/insight_review_90.csv`` (column ``your_rating``: pass or fail), on a held-out half the
   tuning never sees. Accuracy and Cohen's kappa (agreement beyond chance).
5. ``tune``: automatic prompt tuning. The judge's instructions are rewritten by the model from the
   cases it got wrong, each rewrite is scored on the tuning half, and the best is kept only if it
   does not do worse on the held-out half (APO / ProTeGi, Pryzant et al. 2023; OPRO, Yang et al. 2023).

Run ``python judge_tools.py`` to rate, measure and tune. Nothing here ever blocks a release.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

VERSION = "insight-judge-v1"
ROOT = Path(__file__).resolve().parent.parent
RATINGS_PATH = Path(os.getenv("INSIGHT_JUDGE_RATINGS") or ROOT / "data" / "insight_review_90.csv")
STATE_PATH = Path(os.getenv("INSIGHT_JUDGE_STATE") or ROOT / "data" / "evaluation" / "judge_state.json")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_OLLAMA_MODEL = "qwen2.5:14b"
MIN_HELD_OUT = 10
ALIGNED_ACCURACY = 0.8
ALIGNED_KAPPA = 0.6
TOURNAMENT_MAX_ITEMS = 5

# v12: the owner's six verdicts, built in so the ratings file never has to be replaced. Used only
# where the file's own your_rating cell is empty; matched by row number AND date.
SEED_RATINGS = {
    ("54", "2026-10-22"): ("pass", "Good: the concrete three-month delay; present it as the delay, not as a power theory"),
    ("41", "2026-10-10"): ("pass", "Okay: can be presented"),
    ("42", "2026-10-10"): ("pass", "Okay: same naming collision as 41"),
    ("10", "2026-10-09"): ("fail", "Less than okay: a label, nothing follows"),
    ("84", "2026-10-25"): ("fail", "Rejected: stitched accord, grant and funding into one story"),
    ("86", "2026-11-14"): ("fail", "Rejected: liability theory with no concrete fact"),
}
MIN_EACH_CLASS = 3

PASS_WORDS = {"pass", "yes", "good", "nice", "okay", "ok", "keep", "1", "true"}
FAIL_WORDS = {"fail", "no", "bad", "reject", "rejected", "0", "false", "drop"}

DEFAULT_INSTRUCTIONS = """You judge strategic insights about AI governance news for one reader: an analyst who wants
to learn something concrete she did not already know and could act on.

PASS only if ALL hold:
1. It rests on a concrete fact a reader can check: a date or gap between dates, a number or amount, a named
   document or clause, a named party. (Good: "Governments learned of the AI-agent breach about three months
   after it happened; the developer, not the government, detected it.")
2. It says what that fact changes or what follows next, in plain words - not a theory label such as
   "information asymmetry", "regulatory arbitrage" or "whoever controls information holds power".
3. It does not join unrelated news items into one story unless a fact connects them.
4. It does not claim more than the facts show: no motives nobody stated, no "entirely", "only", "forces",
   "inevitable" where the facts are partial.
5. An informed reader of the same news would not already have written it.

FAIL examples the reader rejected: liability "shifts to deployers" because accords are voluntary (a theory, no
concrete fact); philanthropy "shaping standards" stitched from an accord, a grant and a funding round; a
"dual-use contradiction" between a consumer rule and a military contract (a label, nothing follows).
A merely acceptable insight that rests on a checkable fact and says what changes (e.g. the executive renaming
of AI clashing with a statutory definition) still PASSES."""

VERDICT_FORMAT = 'Return JSON only: {"pass": true or false, "reason": "one plain sentence"}'
COMPARE_FORMAT = ('Return JSON only: {"better": "A" or "B" or "tie", "reason": "one plain sentence"}. '
                  "Judge only which a reader would find more useful under the standard; ignore length and order.")


# --------------------------------------------------------------------------- model access

class JudgeUnavailable(RuntimeError):
    pass


def provider() -> str:
    chosen = (os.getenv("INSIGHT_JUDGE_PROVIDER") or "auto").strip().casefold()
    if chosen == "off":
        return ""
    if chosen in {"groq", "ollama"}:
        return chosen
    if (os.getenv("GROQ_API_KEY") or "").strip():
        return "groq"
    if (os.getenv("OLLAMA_JUDGE_MODEL") or "").strip():
        return "ollama"
    return ""


def model_name(kind: str = "") -> str:
    kind = kind or provider()
    if kind == "groq":
        return (os.getenv("INSIGHT_JUDGE_MODEL") or DEFAULT_GROQ_MODEL).strip()
    if kind == "ollama":
        return (os.getenv("OLLAMA_JUDGE_MODEL") or DEFAULT_OLLAMA_MODEL).strip()
    return ""


def available() -> bool:
    return bool(provider())


def _groq_ask(prompt: str, system: str, model: str, timeout: float = 60.0) -> str:
    key = (os.getenv("GROQ_API_KEY") or "").strip()
    if not key:
        raise JudgeUnavailable("GROQ_API_KEY is not set")
    body = json.dumps({"model": model, "temperature": 0, "response_format": {"type": "json_object"},
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]})
    for attempt in range(4):
        request = urllib.request.Request(GROQ_URL, data=body.encode("utf-8"), method="POST",
                                         headers={"Authorization": f"Bearer {key}",
                                                  "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            return str(data["choices"][0]["message"]["content"] or "")
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < 3:     # free-tier rate limit: wait and retry
                wait = float(error.headers.get("retry-after") or 2 * (attempt + 1))
                time.sleep(min(wait, 30.0))
                continue
            raise JudgeUnavailable(f"Groq HTTP {error.code}") from error
    raise JudgeUnavailable("Groq rate limit")


def _ollama_ask(prompt: str, system: str, model: str) -> str:
    from services.llm_service import LLMService

    return LLMService(model_name=model).ask(prompt, system_prompt=system, temperature=0.0, response_format="json")


def default_asker() -> Callable[[str], str]:
    """A function prompt -> reply on the free judge model. Raises JudgeUnavailable when none is set."""
    kind = provider()
    model = model_name(kind)
    if not kind:
        raise JudgeUnavailable("No free judge model configured (set GROQ_API_KEY, or INSIGHT_JUDGE_PROVIDER=ollama)")
    if "gemini" in model.casefold():
        raise JudgeUnavailable("The judge must not be a Gemini model; Gemini writes the insights")
    system = "You are a strict, consistent evaluator. Return one JSON object only."
    if kind == "groq":
        return lambda prompt: _groq_ask(prompt, system, model)
    return lambda prompt: _ollama_ask(prompt, system, model)


def _json(reply: Any) -> dict[str, Any]:
    if isinstance(reply, Mapping):
        return dict(reply)
    text = str(reply or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------- judging

def verdict(text: str, ask: Callable[[str], str], instructions: str = "") -> dict[str, Any]:
    data = _json(ask(f"{instructions or current_instructions()}\n\nINSIGHT:\n{text}\n\n{VERDICT_FORMAT}"))
    passed = data.get("pass")
    if isinstance(passed, str):
        passed = passed.strip().casefold() in PASS_WORDS
    return {"pass": bool(passed), "reason": str(data.get("reason") or "")[:300]}


def _better(first: str, second: str, ask: Callable[[str], str], instructions: str) -> str:
    data = _json(ask(f"{instructions}\n\nWhich insight is better?\n\nA:\n{first}\n\nB:\n{second}\n\n{COMPARE_FORMAT}"))
    choice = str(data.get("better") or "").strip().upper()
    return choice if choice in {"A", "B"} else "TIE"


def compare(a: str, b: str, ask: Callable[[str], str], instructions: str = "") -> str:
    """'a', 'b' or 'tie'. Asked in both orders; a winner counts only when both orders agree."""
    instructions = instructions or current_instructions()
    forward = _better(a, b, ask, instructions)
    backward = _better(b, a, ask, instructions)
    if forward == "A" and backward == "B":
        return "a"
    if forward == "B" and backward == "A":
        return "b"
    return "tie"


def tournament(items: Sequence[Any], text_of: Callable[[Any], str], ask: Callable[[str], str],
               instructions: str = "", max_items: int = TOURNAMENT_MAX_ITEMS) -> tuple[list[Any], list[float]]:
    """Round robin over the first ``max_items`` items; returns (reordered items, scores). Items past the
    cap keep their order after the ranked ones. Ties keep the incoming order."""
    head, tail = list(items[:max_items]), list(items[max_items:])
    scores = [0.0] * len(head)
    for i in range(len(head)):
        for j in range(i + 1, len(head)):
            outcome = compare(text_of(head[i]), text_of(head[j]), ask, instructions)
            if outcome == "a":
                scores[i] += 1
            elif outcome == "b":
                scores[j] += 1
            else:
                scores[i] += 0.5
                scores[j] += 0.5
    order = sorted(range(len(head)), key=lambda k: (-scores[k], k))
    return [head[k] for k in order] + tail, [scores[k] for k in order]


# --------------------------------------------------------------------------- owner's ratings

def label_of(value: Any) -> Optional[bool]:
    word = str(value or "").strip().casefold().split(" ")[0].strip(".,;:-")
    if word in PASS_WORDS:
        return True
    if word in FAIL_WORDS:
        return False
    return None


def load_ratings(path: Path | str | None = None) -> list[dict[str, Any]]:
    path = Path(path or RATINGS_PATH)
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rating, note = row.get("your_rating"), str(row.get("your_note") or "").strip()
            seed = SEED_RATINGS.get((str(row.get("no") or "").strip(), str(row.get("date") or "").strip()[:10]))
            if label_of(rating) is None and seed:
                rating, note = seed[0], note or seed[1]
            label = label_of(rating)
            text = str(row.get("insight") or "").strip()
            if label is None or not text:
                continue
            rows.append({"id": str(row.get("no") or len(rows) + 1), "text": text, "label": label, "note": note})
    return rows


def split(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict], list[dict]]:
    """Deterministic halves (tuning, held-out), balanced by label."""
    tuning, held_out = [], []
    for label in (True, False):
        group = sorted((dict(r) for r in rows if r["label"] is label),
                       key=lambda r: hashlib.sha256(str(r["id"]).encode()).hexdigest())
        for index, row in enumerate(group):
            (tuning if index % 2 == 0 else held_out).append(row)
    return tuning, held_out


def agreement(rows: Sequence[Mapping[str, Any]], ask: Callable[[str], str], instructions: str = "") -> dict[str, Any]:
    instructions = instructions or current_instructions()
    hits = tp = tn = fp = fn = 0
    errors = []
    for row in rows:
        try:
            judged = verdict(row["text"], ask, instructions)
        except JudgeUnavailable:
            raise
        except Exception as error:   # one bad reply does not stop the measurement
            logger.warning("Judge verdict failed: %s", type(error).__name__)
            continue
        if judged["pass"] == row["label"]:
            hits += 1
        else:
            errors.append({**dict(row), "judge_reason": judged["reason"]})
        tp += judged["pass"] and row["label"]
        tn += (not judged["pass"]) and (not row["label"])
        fp += judged["pass"] and not row["label"]
        fn += (not judged["pass"]) and row["label"]
    n = tp + tn + fp + fn
    accuracy = hits / n if n else 0.0
    chance = (((tp + fp) * (tp + fn)) + ((fn + tn) * (fp + tn))) / (n * n) if n else 0.0
    kappa = (accuracy - chance) / (1 - chance) if n and chance < 1 else 0.0
    # v12: balanced accuracy - the mean of the pass rate and the fail rate - so a judge that fails
    # everything cannot look good when most insights are rated fail.
    balanced = ((tp / (tp + fn) if tp + fn else 0.0) + (tn / (tn + fp) if tn + fp else 0.0)) / 2
    return {"n": n, "accuracy": round(accuracy, 3), "balanced_accuracy": round(balanced, 3),
            "kappa": round(kappa, 3), "rated_pass": tp + fn, "rated_fail": tn + fp, "errors": errors}


TUNE_PROMPT = """You maintain the instructions an evaluator uses to judge insights for one reader.
Below are the CURRENT INSTRUCTIONS and cases where the evaluator disagreed with the reader.
Rewrite the instructions so the evaluator would agree with the reader on cases like these, without
naming these exact insights and without making the instructions longer than about 400 words.
Keep every rule that is not contradicted by a case.

CURRENT INSTRUCTIONS:
{instructions}

DISAGREEMENTS:
{cases}

Return JSON only: {{"instructions": "the full rewritten instructions"}}"""


def _cases_text(errors: Sequence[Mapping[str, Any]], limit: int = 8) -> str:
    lines = []
    for row in list(errors)[:limit]:
        lines.append(f"- Insight: {row['text'][:500]}\n  Reader: {'PASS' if row['label'] else 'FAIL'}"
                     + (f" ({row['note'][:200]})" if row.get("note") else "")
                     + f"\n  Evaluator said the opposite: {row.get('judge_reason', '')[:200]}")
    return "\n".join(lines)


def tune(rows: Sequence[Mapping[str, Any]], ask: Callable[[str], str], *, rounds: int = 3,
         proposals: int = 3, save: bool = True) -> dict[str, Any]:
    """Automatic prompt tuning on the tuning half; kept only if the held-out half does not get worse."""
    tuning, held_out = split(rows)
    start = current_instructions()
    best, best_score = start, agreement(tuning, ask, start)
    history = [{"round": 0, "tuning_accuracy": best_score["accuracy"]}]
    for round_no in range(1, rounds + 1):
        if not best_score["errors"]:
            break
        improved = False
        for _ in range(proposals):
            try:
                rewritten = str(_json(ask(TUNE_PROMPT.format(instructions=best,
                                                             cases=_cases_text(best_score["errors"])))
                                      ).get("instructions") or "").strip()
            except JudgeUnavailable:
                raise
            except Exception:
                continue
            if len(rewritten) < 200:
                continue
            score = agreement(tuning, ask, rewritten)
            if (score["accuracy"], score["kappa"]) > (best_score["accuracy"], best_score["kappa"]):
                best, best_score, improved = rewritten, score, True
        history.append({"round": round_no, "tuning_accuracy": best_score["accuracy"], "improved": improved})
        if not improved:
            break
    before = agreement(held_out, ask, start)
    after = agreement(held_out, ask, best) if best != start else before
    keep = best != start and (after["accuracy"], after["kappa"]) >= (before["accuracy"], before["kappa"])
    chosen, measured = (best, after) if keep else (start, before)
    result = {"kept_new_instructions": keep, "history": history,
              "held_out_before": {k: before[k] for k in ("n", "accuracy", "kappa")},
              "held_out_after": {k: after[k] for k in ("n", "accuracy", "kappa")}}
    if save:
        save_state(chosen, measured, tuning_n=len(tuning))
    return result


# --------------------------------------------------------------------------- saved state

def load_state(path: Path | str | None = None) -> dict[str, Any]:
    try:
        data = json.loads(Path(path or STATE_PATH).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def current_instructions() -> str:
    return str(load_state().get("instructions") or DEFAULT_INSTRUCTIONS)


def save_state(instructions: str, held_out: Mapping[str, Any], *, tuning_n: int = 0,
               path: Path | str | None = None) -> dict[str, Any]:
    path = Path(path or STATE_PATH)
    state = {"version": VERSION, "provider": provider(), "model": model_name(),
             "instructions": instructions, "held_out_n": held_out.get("n", 0),
             "held_out_accuracy": held_out.get("accuracy", 0.0), "held_out_kappa": held_out.get("kappa", 0.0),
             "held_out_balanced_accuracy": held_out.get("balanced_accuracy", 0.0),
             "held_out_pass": held_out.get("rated_pass", 0), "held_out_fail": held_out.get("rated_fail", 0),
             "tuning_n": tuning_n, "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    state["aligned"] = is_aligned(state)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    return state


def is_aligned(state: Optional[Mapping[str, Any]] = None) -> bool:
    """The judge may rank only after it agrees with the owner on a held-out set it was not tuned on."""
    state = load_state() if state is None else state
    if state.get("model") and model_name() and state.get("model") != model_name():
        return False      # agreement measured on another model does not carry over
    return (int(state.get("held_out_n") or 0) >= MIN_HELD_OUT
            and int(state.get("held_out_pass") or 0) >= MIN_EACH_CLASS
            and int(state.get("held_out_fail") or 0) >= MIN_EACH_CLASS
            and float(state.get("held_out_balanced_accuracy") or 0) >= ALIGNED_ACCURACY
            and float(state.get("held_out_kappa") or 0) >= ALIGNED_KAPPA)


def suggest_to_rate(path: Path | str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """v12 (no model calls): unrated insights most worth rating next. The ratings have few passes,
    so likely passes - those meeting the reader standard's concrete-fact rule - come first."""
    from services import reader_standard

    rated = {r["id"] for r in load_ratings(path)}
    out = []
    with Path(path or RATINGS_PATH).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            number, text = str(row.get("no") or "").strip(), str(row.get("insight") or "").strip()
            if number in rated or not text:
                continue
            assessment = reader_standard.assess(text)
            out.append({"no": number, "date": row.get("date"), "likely_pass": bool(assessment["passes"]),
                        "score": assessment.get("score", 0), "insight": text[:240]})
    out.sort(key=lambda r: (not r["likely_pass"], -int(r["score"] or 0)))
    return out[:limit]
