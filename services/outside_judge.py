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

VERSION = "outside-judge-v2"
logger = logging.getLogger("event_intelligence.services.outside_judge")
_STATS = {"calls": 0, "failed": 0}
_CACHE: dict[str, Any] = {}
EXCLUDED_FAMILIES = ("gemma", "gemini")


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
        names = [str(m.get("name") or m.get("model") or "") for m in data.get("models") or [] if isinstance(m, dict)]
    except Exception:
        names = []
    _CACHE["models"] = [n for n in names if n]
    return _CACHE["models"]


def model(get: Callable[..., Any] | None = None) -> str:
    # The same setting as the existing pairwise judge (services/insight_judge.py, OLLAMA_JUDGE_MODEL), so
    # one model is configured once; OUTSIDE_JUDGE_MODEL overrides it for these checks only.
    chosen = os.getenv("OUTSIDE_JUDGE_MODEL", "").strip() or os.getenv("OLLAMA_JUDGE_MODEL", "").strip()
    if chosen and not any(family in chosen.casefold() for family in EXCLUDED_FAMILIES):
        return chosen
    for name in _installed(get):
        if not any(family in name.casefold() for family in EXCLUDED_FAMILIES):
            return name
    return ""


def available(get: Callable[..., Any] | None = None) -> bool:
    if os.getenv("OUTSIDE_JUDGE", "1").strip() in {"0", "false", "no"}:
        return False
    return bool(_installed(get)) and bool(model(get))


def reset() -> None:
    _CACHE.clear()


def stats() -> dict[str, Any]:
    return dict(_STATS, model=model() if available() else "", available=available())


def ask(prompt: str, post: Callable[..., Any] | None = None) -> str:
    """One judgment from the local open-source model; returns the reply text. Raises on failure."""
    name = model()
    if not name:
        raise RuntimeError("no local open-source judge model (start Ollama or set OUTSIDE_JUDGE_MODEL)")
    if post is None:
        import requests
        post = requests.post
    body = {"model": name, "temperature": 0, "stream": False, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": "You are a strict evaluator. Answer only with the JSON asked for."},
                         {"role": "user", "content": prompt}]}
    _STATS["calls"] += 1
    response = post(_host() + "/v1/chat/completions", headers={"Content-Type": "application/json"},
                    data=json.dumps(body), timeout=_timeout())
    status = getattr(response, "status_code", 200)
    if status >= 400:
        _STATS["failed"] += 1
        raise RuntimeError(f"outside judge HTTP {status}")
    data = response.json()
    return str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


def judge_or(fallback: Callable[[str], Any]) -> tuple[Callable[[str], Any], str]:
    """(ask function, who judges): the outside model when configured, else the fallback (Gemini)."""
    if available():
        return ask, "outside:" + model()
    return fallback, "self-judged:gemini"
