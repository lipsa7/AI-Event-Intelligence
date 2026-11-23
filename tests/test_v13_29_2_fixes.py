"""v13.29.2: dates are not stray numbers; yes/no checks on gpt-oss-20b; a model that cannot load stops at once."""
import json

import pytest

from services import outside_judge, variant_view


def test_a_day_of_the_month_is_not_a_figure():
    text = "Forecast: Will OpenAI file by January 15, 2027? The EU sandboxes start 10 November 2026 (2026-11-10)."
    assert variant_view._figures(text) == []
    assert variant_view._figures("Hyperscalers spent $121.4 billion over 80 incidents.") == ["$121.4 billion", "80"]


def test_the_texas_draft_is_no_longer_held_back_by_its_forecast_date():
    from datetime import datetime, timedelta, timezone
    near = (datetime.now(timezone.utc) + timedelta(days=40)).isoformat()
    text = ("Texas forces OpenAI into localized compliance bottlenecks. Forecast: Will OpenAI declare it may delay "
            "new models in Texas by January 15, 2027?")
    cand = {"insight": text, "leverage_analysis": {"holder": "OpenAI"},
            "forecast_contract": {"question": "q", "deadline": near},
            "variant_perception": {"consensus_quote": "", "overlooked_evidence_id": "E2", "rival_hypothesis": "r",
                                   "load_bearing_assumption": "a",
                                   "mechanism_chain": [{"step": "OpenAI must clear the evaluator", "evidence_ids": ["E1"]},
                                                       {"step": "OpenAI delays releases", "evidence_ids": ["E2"]}]}}
    records = [{"evidence_id": "E1", "event_id": "V1", "text": "Texas sued OpenAI demanding an evaluator."},
               {"evidence_id": "E2", "event_id": "V1", "text": "The evaluator must approve new models before release."}]
    problems = variant_view.perception_problems(cand, {"claims": []}, records)
    assert not any(p.startswith("number_not_in_chain") for p in problems)
    assert variant_view.number_problems(cand, records) == []


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.headers = status, body, {}

    def json(self):
        return self._body


@pytest.fixture
def groq(monkeypatch):
    monkeypatch.delenv("OUTSIDE_JUDGE_PROVIDER", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "k")
    outside_judge.reset()
    outside_judge._RECENT_TOKENS.clear()
    yield
    outside_judge.reset()
    outside_judge._RECENT_TOKENS.clear()


def test_simple_checks_call_gpt_oss_20b_and_its_daily_limit_does_not_stop_the_hard_judge(groq):
    seen = []

    def post(url, headers, data, timeout):
        seen.append(json.loads(data)["model"])
        if json.loads(data)["model"] == "openai/gpt-oss-20b":
            return _Resp(429, {"error": {"message": "Rate limit reached on tokens per day (TPD)"}})
        return _Resp(200, {"choices": [{"message": {"content": "{}"}}], "usage": {"total_tokens": 10}})
    with pytest.raises(RuntimeError):
        outside_judge.ask("x", post=post, sleep=lambda s: None, now=lambda: 0.0, kind="simple")
    assert seen == ["openai/gpt-oss-20b"] and outside_judge.stopped("simple") and not outside_judge.stopped("hard")
    assert outside_judge.ask("y", post=post, sleep=lambda s: None, now=lambda: 0.0) == "{}"
    assert seen[-1] == "openai/gpt-oss-120b"


def test_a_local_model_that_cannot_load_stops_after_one_failure(monkeypatch):
    monkeypatch.setenv("OUTSIDE_JUDGE_PROVIDER", "ollama")
    monkeypatch.setattr(outside_judge, "_installed", lambda get=None: ["qwen3:14b"])
    outside_judge.reset()
    error = _Resp(500, {"error": "llama-server process has terminated: failed to allocate buffer of size 6311116800"})
    with pytest.raises(RuntimeError):
        outside_judge.ask("x", post=lambda *a, **k: error)
    assert outside_judge.stopped() and not outside_judge.available()
    outside_judge.reset()
