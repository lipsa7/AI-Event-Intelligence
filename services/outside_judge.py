"""A judge from a DIFFERENT model family for every check of the generator's output (v13.24, 22 Nov 2026).

Why
---
The user: models critiquing their own output without new outside information does not work - use an
open-source model to evaluate the other model's output. The research report of 22 Nov agrees:
intrinsic self-correction often makes answers worse (Huang et al., ICLR 2024); LLM judges favour their
own model's output (self-enhancement bias, Zheng et al., MT-Bench, NeurIPS 2023). Gemini keeps
generating; every pass/fail and pairwise judgment of what Gemini wrote goes to this judge: novelty
against the consensus, pre-mortem, the tournament and the editor's bar.

Provider: the user's own Ollama (open-source models, local, free), through its OpenAI-compatible
endpoint http://localhost:11434/v1/chat/completions (OLLAMA_HOST to change it). The model is
OUTSIDE_JUDGE_MODEL if set, otherwise the first installed model that is not a Google model (Gemma is
Gemini's family, so it would share its bias). Local models are slower: each judgment waits up to
OUTSIDE_JUDGE_TIMEOUT seconds (default 180). OUTSIDE_JUDGE=0 turns it off.
If Ollama is not running, the run falls back to Gemini and the log says "self-judged:gemini".
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

VERSION = "outside-judge-v4-groq"
logger = logging.getLogger("event_intelligence.services.outside_judge")
_STATS = {"calls": 0, "failed": 0}
# v13.29 (owner, 22 Nov: "groq, free plan"; research report of 22 Nov, section 5): the judge can be a
# reasoning model on GroqCloud - openai/gpt-oss-120b, open-weight (Apache 2.0), not Google. On JudgeBench
# (Tan et al., ICLR 2025) a reasoning judge (o3-mini) scored 70.6-80.9% against 50.9% for GPT-4o with a plain
# prompt; small local models compare research ideas "close to random" on hard pairs. Free plan for
# gpt-oss-120b (Groq rate-limits page): 30 requests/min, 1,000/day, 8,000 tokens/min, 200,000 tokens/day -
# so calls are paced to the token-per-minute limit and Groq's retry-after is honoured; when the daily limit is
# reached the judge stops for the run and checks say "not judged" (never Gemini).
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"
_RECENT_TOKENS: list[tuple[float, int]] = []
_CACHE: dict[str, Any] = {}
EXCLUDED_FAMILIES = ("gemma", "gemini")
EMBEDDING_WORDS = ("embed", "bge-", "minilm", "e5-", "rerank")


def _host() -> str:
    host = os.getenv("OLLAMA_HOST", "").strip() or "http://localhost:11434"
    return host if host.startswith("http") else "http://" + host


def _timeout() -> float:
    try:
        return max(10.0, float(os.getenv("OUTSIDE_JUDGE_TIMEOUT", "180")))
    except ValueError:
        return 180.0


def _installed(get: Callable[..., Any] | None = None) -> list[str]:
    if "models" in _CACHE:
        return _CACHE["models"]
    names: list[str] = []
    try:
        if get is None:
            import requests
            get = requests.get
        data = get(_host() + "/api/tags", timeout=3).json()
        rows = [m for m in data.get("models") or [] if isinstance(m, dict)]
        # v13.27.3: Ollama lists the most recently changed model first - on the user's laptop that is
        # nomic-embed-text, an embedding model that cannot answer a prompt, so every check would have failed.
        # Embedding models are skipped and the LARGEST chat model is used (judge strength grows with size -
        # Zheng et al., MT-Bench, NeurIPS 2023). OUTSIDE_JUDGE_MODEL / OLLAMA_JUDGE_MODEL still override.
        rows = [m for m in rows if not any(word in str(m.get("name") or m.get("model") or "").casefold()
                                           for word in EMBEDDING_WORDS)]
        rows.sort(key=lambda m: -float(m.get("size") or 0))
        names = [str(m.get("name") or m.get("model") or "") for m in rows]
    except Exception:
        names = []
    _CACHE["models"] = [n for n in names if n]
    return _CACHE["models"]


def provider(kind: str = "hard") -> str:
    """groq | ollama for a judgment of this kind.

    v13.29 (owner: Groq free plan, no card): Groq's free plan for gpt-oss-120b allows 200,000 tokens a day -
    about one full run if EVERY check used it. So the hard judgments ("is this original and good?": editor's
    bar, novelty, head-to-head, tournament) go to Groq, and the classification-style checks (pre-mortem
    evidence lookup, past-case ratings, repeat check) go to the local Ollama model when one is installed -
    the research report of 22 Nov: a 14B model is fine for classification-style gates, not for "is this
    original and good?". OUTSIDE_JUDGE_PROVIDER forces one provider for both."""
    chosen = os.getenv("OUTSIDE_JUDGE_PROVIDER", "").strip().casefold()
    if chosen in {"groq", "ollama"}:
        return chosen
    has_groq = bool(os.getenv("GROQ_API_KEY", "").strip())
    if kind == "simple" and _installed() and not _stopped_for("ollama"):
        return "ollama"
    return "groq" if has_groq else "ollama"


def model(get: Callable[..., Any] | None = None, kind: str = "hard") -> str:
    if provider(kind) == "groq":
        chosen = os.getenv("GROQ_JUDGE_MODEL", "").strip() or GROQ_DEFAULT_MODEL
        return "" if any(family in chosen.casefold() for family in EXCLUDED_FAMILIES) else chosen
    # The same setting as the existing pairwise judge (services/insight_judge.py, OLLAMA_JUDGE_MODEL), so
    # one model is configured once; OUTSIDE_JUDGE_MODEL overrides it for these checks only.
    chosen = os.getenv("OUTSIDE_JUDGE_MODEL", "").strip() or os.getenv("OLLAMA_JUDGE_MODEL", "").strip()
    if chosen and not any(family in chosen.casefold() for family in EXCLUDED_FAMILIES):
        return chosen
    for name in _installed(get):
        if not any(family in name.casefold() for family in EXCLUDED_FAMILIES):
            return name
    return ""


def available(get: Callable[..., Any] | None = None, kind: str = "hard") -> bool:
    if os.getenv("OUTSIDE_JUDGE", "1").strip() in {"0", "false", "no"}:
        return False
    which = provider(kind)
    if _stopped_for(which):
        return False
    if which == "groq":
        return bool(os.getenv("GROQ_API_KEY", "").strip()) and bool(model(get, kind))
    return bool(_installed(get)) and bool(model(get, kind))


def reset() -> None:
    _CACHE.clear()
    for key in [k for k in _STATS if k.startswith("consecutive_failed") or k == "daily_limit"]:
        _STATS.pop(key, None)


def stats() -> dict[str, Any]:
    return dict(_STATS, model=model() if available() else "", available=available())


MAX_CONSECUTIVE_FAILURES = 3


def _strip_thinking(text: str) -> str:
    """Qwen3 and DeepSeek-R1 can put their reasoning in <think>...</think> before the answer."""
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    return text.strip()


def _detail(response: Any) -> str:
    try:
        body = response.json()
        message = body.get("error") if isinstance(body, dict) else body
        if isinstance(message, dict):
            message = message.get("message") or message
        return " ".join(str(message or "").split())[:200]
    except Exception:
        return " ".join(str(getattr(response, "text", "") or "").split())[:200]


def _fail(reason: str, which: str = "") -> None:
    which = which or provider()
    _STATS["failed"] += 1
    _STATS["consecutive_failed:" + which] = _STATS.get("consecutive_failed:" + which, 0) + 1
    _STATS["last_error"] = reason
    if _STATS["failed"] <= 3:
        logger.warning("outside_judge_failed=%s", json.dumps({"provider": which, "reason": reason}))


def _ok(which: str) -> None:
    _STATS["consecutive_failed:" + which] = 0


def _stopped_for(which: str) -> bool:
    if which == "groq" and _STATS.get("daily_limit"):
        return True
    return _STATS.get("consecutive_failed:" + which, 0) >= MAX_CONSECUTIVE_FAILURES


def stopped(kind: str = "hard") -> bool:
    """v13.28.3: after MAX_CONSECUTIVE_FAILURES failures in a row a provider is treated as unavailable for the
    rest of the run (each failed call can wait OUTSIDE_JUDGE_TIMEOUT seconds), so checks say "not judged".
    v13.29: Groq also once its daily limit is reached."""
    return _stopped_for(provider(kind))


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _pace(estimate: int, sleep: Callable[[float], Any], now: Callable[[], float]) -> None:
    """Wait until this call fits in Groq's tokens-per-minute limit (counted from the replies' usage)."""
    limit = _env_number("GROQ_JUDGE_TPM", 8000)
    while True:
        moment = now()
        _RECENT_TOKENS[:] = [(t, n) for t, n in _RECENT_TOKENS if moment - t < 60]
        used = sum(n for _, n in _RECENT_TOKENS)
        if not _RECENT_TOKENS or used + estimate <= limit:
            return
        sleep(max(1.0, 60 - (moment - _RECENT_TOKENS[0][0]) + 0.5))


def _groq_ask(prompt: str, post: Callable[..., Any], sleep: Callable[[float], Any],
              now: Callable[[], float]) -> str:
    key = os.getenv("GROQ_API_KEY", "").strip()
    max_out = int(_env_number("GROQ_JUDGE_MAX_COMPLETION_TOKENS", 1500))
    body = {"model": model(), "temperature": 0, "response_format": {"type": "json_object"},
            "reasoning_effort": os.getenv("GROQ_JUDGE_REASONING_EFFORT", "medium").strip() or "medium",
            "max_completion_tokens": max_out,
            "messages": [{"role": "system", "content": "You are a strict evaluator. Answer only with the JSON asked for."},
                         {"role": "user", "content": prompt}]}
    estimate = len(prompt) // 3 + max_out
    errors: list[str] = []
    for _ in range(4):
        _pace(estimate, sleep, now)
        try:
            response = post(GROQ_URL, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                            data=json.dumps(body), timeout=_timeout())
        except Exception as error:
            errors.append(f"groq: {type(error).__name__}")
            break
        status = getattr(response, "status_code", 200)
        if status == 429:
            detail = _detail(response)
            if "per day" in detail.casefold() or "(tpd)" in detail.casefold() or "(rpd)" in detail.casefold():
                _STATS["daily_limit"] = detail
                errors.append("groq: daily limit reached - " + detail)
                break
            try:
                wait = float((getattr(response, "headers", {}) or {}).get("retry-after") or 10)
            except (TypeError, ValueError):
                wait = 10.0
            _RECENT_TOKENS.append((now(), estimate))
            sleep(min(65.0, max(1.0, wait)))
            errors.append("groq: HTTP 429")
            continue
        if status >= 400:
            errors.append(f"groq: HTTP {status} {_detail(response)}".strip())
            break
        try:
            data = response.json()
        except Exception as error:
            errors.append(f"groq: unreadable reply ({type(error).__name__})")
            break
        used = (data.get("usage") or {}).get("total_tokens") if isinstance(data, dict) else None
        _RECENT_TOKENS.append((now(), int(used) if isinstance(used, (int, float)) else estimate))
        text = _strip_thinking(str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""))
        if text:
            _ok("groq")
            return text
        errors.append("groq: empty reply (raise GROQ_JUDGE_MAX_COMPLETION_TOKENS if reasoning used it all)")
        break
    reason = "; ".join(errors[-2:]) or "no reply"
    _fail(reason, "groq")
    raise RuntimeError("outside judge " + reason)


def ask(prompt: str, post: Callable[..., Any] | None = None, sleep: Callable[[float], Any] | None = None,
        now: Callable[[], float] | None = None, kind: str = "hard") -> str:
    """One judgment from the local open-source model; returns the reply text. Raises on failure.

    v13.28.3: on 22 Nov every judgment by qwen3:14b failed with a bare "RuntimeError" (novelty, pre-mortem,
    tournament), so nothing was judged and the reason was not logged. Now: the OpenAI-compatible endpoint
    first, then Ollama's own /api/chat (format json, thinking off) if that fails; a thinking block is
    removed from the reply; the HTTP status and Ollama's message are logged; three failures in a row stop
    the judge for the run."""
    which = provider(kind)
    name = model(kind=kind)
    if not name:
        raise RuntimeError("no open-source judge model (set GROQ_API_KEY, or start Ollama / set OUTSIDE_JUDGE_MODEL)")
    if _stopped_for(which):
        raise RuntimeError("outside judge stopped after repeated failures: " + str(_STATS.get("last_error", "")))
    if post is None:
        import requests
        post = requests.post
    _STATS["calls"] += 1
    if which == "groq":
        import time
        return _groq_ask(prompt, post, sleep or time.sleep, now or time.monotonic)
    system = "You are a strict evaluator. Answer only with the JSON asked for."
    errors = []
    attempts = (
        ("/v1/chat/completions", {"model": name, "temperature": 0, "stream": False,
                                  "response_format": {"type": "json_object"},
                                  "messages": [{"role": "system", "content": system},
                                               {"role": "user", "content": prompt}]}),
        ("/api/chat", {"model": name, "stream": False, "format": "json", "think": False,
                       "options": {"temperature": 0},
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}),
    )
    for path, body in attempts:
        try:
            response = post(_host() + path, headers={"Content-Type": "application/json"},
                            data=json.dumps(body), timeout=_timeout())
        except Exception as error:   # timeout or connection: the second endpoint would wait just as long
            errors.append(f"{path}: {type(error).__name__}")
            break
        status = getattr(response, "status_code", 200)
        if status >= 400:
            errors.append(f"{path}: HTTP {status} {_detail(response)}".strip())
            continue
        try:
            data = response.json()
        except Exception as error:
            errors.append(f"{path}: unreadable reply ({type(error).__name__})")
            continue
        if path == "/api/chat":
            text = str((data.get("message") or {}).get("content") or "")
        else:
            text = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        text = _strip_thinking(text)
        if text:
            _ok("ollama")
            return text
        errors.append(f"{path}: empty reply")
    reason = "; ".join(errors) or "no reply"
    _fail(reason, "ollama")
    raise RuntimeError("outside judge " + reason)


def _same_provider() -> bool:
    return provider("simple") == provider("hard")


def available_simple() -> bool:
    return available() if _same_provider() else available(kind="simple")


def model_simple() -> str:
    return model() if _same_provider() else model(kind="simple")


def ask_simple(prompt: str) -> str:
    """A classification-style judgment (local model first; Groq when no local model)."""
    return ask(prompt) if _same_provider() else ask(prompt, kind="simple")


def judge_or(fallback: Callable[[str], Any]) -> tuple[Callable[[str], Any], str]:
    """(ask function, who judges): the outside model when configured, else the fallback (Gemini)."""
    if available():
        return ask, "outside:" + model()
    return fallback, "self-judged:gemini"
