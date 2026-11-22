"""v13.29: the hard judgments go to gpt-oss-120b on Groq's free plan, the yes/no ones to the local model."""
import json

import pytest

from services import outside_judge


class _Resp:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


def _ok(text, tokens=500):
    return _Resp(200, {"choices": [{"message": {"content": text}}], "usage": {"total_tokens": tokens}})


@pytest.fixture
def groq(monkeypatch):
    monkeypatch.delenv("OUTSIDE_JUDGE_PROVIDER", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    outside_judge.reset()
    outside_judge._RECENT_TOKENS.clear()
    yield
    outside_judge.reset()
    outside_judge._RECENT_TOKENS.clear()


def test_hard_calls_go_to_groq_and_simple_calls_to_the_local_model(groq, monkeypatch):
    monkeypatch.setattr(outside_judge, "_installed", lambda get=None: ["qwen3:14b"])
    assert outside_judge.provider("hard") == "groq" and outside_judge.model() == "openai/gpt-oss-120b"
    assert outside_judge.provider("simple") == "ollama" and outside_judge.model(kind="simple") == "qwen3:14b"
    monkeypatch.setattr(outside_judge, "_installed", lambda get=None: [])
    assert outside_judge.provider("simple") == "groq"      # no local model: Groq does those too


def test_groq_request_carries_the_key_reasoning_and_json_mode(groq):
    seen = {}

    def post(url, headers, data, timeout):
        seen.update(url=url, headers=headers, body=json.loads(data))
        return _ok('{"verdict":"pass"}')
    assert outside_judge.ask("judge this", post=post, sleep=lambda s: None, now=lambda: 0.0) == '{"verdict":"pass"}'
    assert seen["url"] == outside_judge.GROQ_URL and seen["headers"]["Authorization"] == "Bearer test-key"
    assert seen["body"]["model"] == "openai/gpt-oss-120b" and seen["body"]["reasoning_effort"] == "medium"
    assert seen["body"]["response_format"] == {"type": "json_object"}


def test_rate_limit_waits_retry_after_then_succeeds(groq):
    replies = [_Resp(429, {"error": {"message": "Rate limit reached ... tokens per minute (TPM)"}},
                     {"retry-after": "7"}), _ok("{}")]
    waits = []
    out = outside_judge.ask("x", post=lambda *a, **k: replies.pop(0), sleep=waits.append, now=lambda: 0.0)
    assert out == "{}" and 7.0 in waits


def test_daily_limit_stops_groq_for_the_run(groq):
    reply = _Resp(429, {"error": {"message": "Rate limit reached on tokens per day (TPD): Limit 200000"}})
    with pytest.raises(RuntimeError):
        outside_judge.ask("x", post=lambda *a, **k: reply, sleep=lambda s: None, now=lambda: 0.0)
    assert outside_judge.stopped() and not outside_judge.available()


def test_calls_are_paced_to_the_tokens_per_minute_limit(groq, monkeypatch):
    monkeypatch.setenv("GROQ_JUDGE_TPM", "4000")
    clock = {"t": 0.0}
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        clock["t"] += seconds
    post = lambda *a, **k: _ok("{}", tokens=3000)
    outside_judge.ask("y" * 300, post=post, sleep=sleep, now=lambda: clock["t"])
    outside_judge.ask("y" * 300, post=post, sleep=sleep, now=lambda: clock["t"])
    assert waits and waits[0] >= 59      # the second call waited for the first minute's tokens to expire


def test_tests_never_reach_groq_by_default():
    assert outside_judge.provider() == "ollama"     # conftest forces the local provider
