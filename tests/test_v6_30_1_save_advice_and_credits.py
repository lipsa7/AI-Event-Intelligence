"""v6.30.1 (live run RUN-660F2134AAD54C11A9861CC2, 2026-10-10):
1. The "what each actor should do next" section was built and logged but never saved into
   the report sections, so the dashboard could not show it.
2. Google 402 "prepayment credits are depleted" was treated as a rate limit and retried
   three times on every fallback model; waiting cannot fix it, so it now fails at once.
"""

from __future__ import annotations

import inspect

import pytest

import services.gemini_llm_service as gemini
from services.research_orchestrator_service import ResearchOrchestratorService as R


def test_actor_moves_is_saved_with_the_report_sections():
    source = inspect.getsource(R._persist_report)
    start = source.index("canonical_sections = {")
    block = source[start:source.index("\n        }\n", start)]
    assert '"actor_moves": _json_safe(report.get("actor_moves"' in block


def test_depleted_credits_fail_at_once(monkeypatch):
    calls = []

    class Models:
        def generate_content(self, **kwargs):
            calls.append(kwargs.get("model"))
            raise Exception("402 RESOURCE_EXHAUSTED. {'error': {'code': 402, 'message': 'Your prepayment "
                            "credits are depleted. Please go to AI Studio to manage your billing.'}}")

    service = gemini.GeminiLLMService(api_key="test-key", model_name="gemini-3.5-flash",
                                      rate_limit_retries=2, retry_base_seconds=0.0)
    service._client = type("Client", (), {"models": Models()})()
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    with pytest.raises(gemini.GeminiCreditsDepletedError):
        service.ask("hi")
    assert len(calls) == 1
    assert issubclass(gemini.GeminiCreditsDepletedError, gemini.GeminiRateLimitError)  # callers' fallbacks still apply
