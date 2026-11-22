"""v13.28.3: the local judge falls back to Ollama's own endpoint and explains failures; rule records
must be about the story."""
import json

import pytest

from services import outside_judge, primary_data


class Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _judge(monkeypatch):
    monkeypatch.setenv("OUTSIDE_JUDGE_MODEL", "qwen3:14b")
    outside_judge.reset()
    yield
    outside_judge.reset()


def test_falls_back_to_native_endpoint_and_strips_thinking():
    sent = []

    def post(url, headers, data, timeout):
        sent.append((url, json.loads(data)))
        if url.endswith("/v1/chat/completions"):
            return Resp(400, {"error": {"message": "invalid response_format"}})
        return Resp(200, {"message": {"content": '<think>hmm</think>{"new": true}'}})
    assert outside_judge.ask("x", post=post) == '{"new": true}'
    assert sent[1][0].endswith("/api/chat") and sent[1][1]["think"] is False and sent[1][1]["format"] == "json"


def test_failure_says_why_and_three_in_a_row_stop_the_judge():
    with pytest.raises(RuntimeError, match="HTTP 500 model requires more system memory"):
        outside_judge.ask("x", post=lambda *a, **k: Resp(500, {"error": "model requires more system memory"}))
    for _ in range(2):
        with pytest.raises(RuntimeError):
            outside_judge.ask("x", post=lambda *a, **k: Resp(500))
    assert outside_judge.stopped() and not outside_judge.available()
    with pytest.raises(RuntimeError, match="stopped"):
        outside_judge.ask("x", post=lambda *a, **k: Resp(200, {"choices": [{"message": {"content": "{}"}}]}))
    outside_judge.reset()
    assert not outside_judge.stopped()


def test_a_timeout_is_not_waited_out_twice():
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        raise TimeoutError("read timed out")
    with pytest.raises(RuntimeError, match="TimeoutError"):
        outside_judge.ask("x", post=post)
    assert len(calls) == 1


def test_off_story_rule_records_are_dropped():
    story = ("California Attorney General Rob Bonta sued OpenAI under the Unfair Competition Law over 80 "
             "loss-of-control incidents and the Hugging Face attack.")
    corpus = ["OpenAI agents artificial intelligence report"] * 8 + [story]
    rows = [{"text": 'Federal Register rule "Artificial Intelligence Risk Management for Federal Contractors" by GSA.'},
            {"text": 'Federal Register notice "Unfair Competition Law enforcement: loss-of-control incidents" by FTC.'}]
    kept, dropped = primary_data.relevant(rows, story, corpus)
    assert [r["text"][:40] for r in kept] == [rows[1]["text"][:40]]
    assert dropped and "Risk Management" in dropped[0]


def test_routine_legal_step_is_not_a_forecast():
    from services import variant_view
    routine = {"forecast_contract": {"question": "Will OpenAI file a demurrer or motion to dismiss by January 31, 2027?"}}
    real = {"forecast_contract": {"question": "Will a court grant California a preliminary injunction by January 31, 2027?"}}
    assert variant_view.call_problems(routine) == ["routine_call"]
    assert variant_view.call_problems(real) == []
    assert "routine step" in variant_view.need_text("routine_call")


def test_a_figure_from_another_story_is_flagged():
    from services import variant_view
    records = [
        {"evidence_id": "E1", "event_id": "EV-TX", "text": "Texas Attorney General Ken Paxton sued OpenAI on October 23 "
                                                         "and asks that a court-appointed evaluator approve new ChatGPT models."},
        {"evidence_id": "E2", "event_id": "", "analytical_context_only": True,
         "text": "Hyperscaler capital spending on data centres reached $121.4 billion in the second quarter."},
    ] + [{"evidence_id": f"X{i}", "event_id": "EV-O", "text": "OpenAI artificial intelligence news"} for i in range(6)]
    stapled = {"insight": "Texas wants an evaluator to approve ChatGPT models, threatening $121.4 billion of spending. "
                          "OpenAI must act by October 23."}
    assert variant_view.number_problems(stapled, records) == ["number_off_story:$121.4 billion"]
    own = {"insight": "Texas sued OpenAI on October 23 asking that an evaluator approve new ChatGPT models."}
    assert variant_view.number_problems(own, records) == []
    assert "something else" in variant_view.need_text("number_off_story:$121.4 billion")
