"""Strategic model cascade without paying for unused answers (Nov 2026).

The 23 Oct replay billed nine ~29k-token strategic calls on eight models to use one: a route that
passed its hard timeout kept running (a provider call cannot be cancelled here) while the next model
started, and Pro was cut off at 90 s although its route allows 150 s. Now a timed-out route stays in
flight and its late answer is used if it arrives first (the earlier route is the stronger model), at
most two routes run at once, and each route gets its own timeout. Pattern: hedged requests (Dean &
Barroso, "The Tail at Scale"). No model calls."""

from __future__ import annotations

import inspect
import threading
import time

import pytest

import agents.strategic_inference_agent as sia


class Slow:
    def __init__(self, seconds, answer=None, error=None):
        self.seconds, self.answer, self.error, self.calls = seconds, answer, error, 0

    def ask(self, prompt, system_prompt=None):
        self.calls += 1
        time.sleep(self.seconds)
        if self.error:
            raise self.error
        return self.answer


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setattr(sia, "STRATEGIC_MIN_ROUTE_SECONDS", 0.05)
    monkeypatch.setattr(sia, "STRATEGIC_IN_FLIGHT_POLL_SECONDS", 0.01)
    monkeypatch.delenv("EVENT_INTELLIGENCE_LLM_PROXY_DIR", raising=False)
    return sia.StrategicInferenceAgent.__new__(sia.StrategicInferenceAgent)


def call(agent, in_flight, service, timeout, model):
    return agent._invoke_route_hedged(in_flight, service, "p", "s", allow_temperature=False,
                                      timeout_seconds=timeout, model_name=model)


def test_a_late_answer_from_the_stronger_route_is_used(agent):
    in_flight = []
    pro, flash = Slow(0.4, "PRO ANSWER"), Slow(2.0, "FLASH ANSWER")
    with pytest.raises(sia.StrategicInferenceModelTimeoutError):
        call(agent, in_flight, pro, 0.1, "pro")
    assert len(in_flight) == 1                       # still running, not forgotten
    answer, model = call(agent, in_flight, flash, 5.0, "flash")
    assert (answer, model) == ("PRO ANSWER", "pro") and in_flight == []


def test_no_more_than_two_routes_run_at_once(agent):
    in_flight = []
    a, b, c = Slow(1.0, "A"), Slow(1.0, "B"), Slow(0.01, "C")
    for service, name in ((a, "a"), (b, "b")):
        with pytest.raises(sia.StrategicInferenceModelTimeoutError):
            call(agent, in_flight, service, 0.1, name)
    assert len(in_flight) == 2
    answer, model = call(agent, in_flight, c, 3.0, "c")   # waits for a slot; route a answers first
    assert (answer, model) == ("A", "a") and c.calls == 0


def test_the_current_routes_own_error_is_raised(agent):
    in_flight = []
    with pytest.raises(ValueError):
        call(agent, in_flight, Slow(0.01, error=ValueError("bad")), 1.0, "x")
    assert in_flight == []


def test_after_the_cascade_a_running_route_is_still_awaited(agent):
    in_flight = []
    with pytest.raises(sia.StrategicInferenceModelTimeoutError):
        call(agent, in_flight, Slow(0.3, "LATE"), 0.05, "pro")
    assert agent._await_in_flight(in_flight, 2.0) == ("LATE", "pro")


def test_cascade_uses_route_timeouts_and_hedging():
    source = inspect.getsource(sia.StrategicInferenceAgent._call_llm)
    assert "self._invoke_route_hedged(" in source and "min(90.0, route_timeout + 15.0)" not in source
    assert "self._await_in_flight(in_flight" in source
    assert sia.STRATEGIC_MAX_ROUTES_IN_FLIGHT == 2
