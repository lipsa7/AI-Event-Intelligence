"""
Extraction failure must not erase stored events.

Reviewer's reproduction (v6.4): with fallback disabled and a model failure,
ExtractionAgentV6.extract() returns no events with run_status="failed"; the
orchestrator replaced the stored events with nothing, with no warning and no
recorded failure. The orchestrator now checks the run status before touching
storage.

Runs the real ExtractionAgentV6 and the real
ResearchOrchestratorService._extract_events against an in-memory event store.

Run:  python -m tests.test_extraction_persistence
"""

from __future__ import annotations

import json
import re

from agents.extraction_agent_v6 import ExtractionAgentV6
from services.research_orchestrator_service import ResearchOrchestratorService as R
from tests.v6_model_stubs import FramesAndVerifier

SOURCE_ID = "SOURCE-1"
ARTICLE = {
    "source_id": SOURCE_ID,
    "title": "",
    "body": (
        "Acme Inc. announced a partnership with Beta Corp to deploy AI agents across its claims unit.\n\n"
        "Sarvam launched a new Indic LLM for banks."
    ),
}
ACME = {
    "taxonomy": "observed_event", "event_type": "partnership",
    "actor": {"mention": "Acme Inc.", "sentence_id": "S1", "entity_type": "organization"},
    "action_sentence_ids": ["S1"], "ai_link_sentence_ids": ["S1"],
}
SARVAM = {
    "taxonomy": "observed_event", "event_type": "product launch",
    "actor": {"mention": "Sarvam", "sentence_id": "S2", "entity_type": "organization"},
    "action_sentence_ids": ["S2"], "ai_link_sentence_ids": ["S2"],
}
STORED = {"event_id": "EVENT-OLD", "source_id": SOURCE_ID, "title": "Stored from an earlier run"}


class MemoryEventStore:
    def __init__(self, stored=None):
        self.stored = {SOURCE_ID: [dict(event) for event in stored or []]}
        self.replace_calls = 0

    def replace_events_for_source(self, *, source_id, events):
        self.replace_calls += 1
        self.stored[source_id] = [dict(event) for event in events]

    def list_by_source_id(self, source_id):
        return list(self.stored.get(source_id, []))

    def to_dict(self, event):
        return dict(event)


class ModelDown:
    model_name = "gemini-test"

    def ask(self, prompt, system_prompt=None, **kwargs):
        raise TimeoutError("simulated model failure")


class FailsSecondWindow(FramesAndVerifier):
    """Answers the first window; the window containing S2 times out."""

    def __init__(self):
        super().__init__([ACME, SARVAM], fail_on={"S2"})


def run(agent, stored):
    store = MemoryEventStore(stored)
    orchestrator = object.__new__(R)
    orchestrator.extraction_agent = agent
    orchestrator.event_service = store
    orchestrator._get_source_extraction_payload = lambda source_id: dict(ARTICLE)
    state = {"research_depth": "deep", "warnings": []}
    output = orchestrator._extract_events(source_ids=[SOURCE_ID], state=state)
    return store, state, output


def v6(llm, window_characters=None):
    agent = ExtractionAgentV6(llm_service=llm, entity_tagger=None, fallback_to_v5=False)
    if window_characters:
        agent.max_window_characters = window_characters  # one window per paragraph
    return agent


def test_failed_extraction_keeps_stored_events_and_reports_it():
    agent = v6(ModelDown())
    store, state, output = run(agent, [STORED])

    assert agent.get_last_run_metadata()["run_status"] == "failed"
    assert store.replace_calls == 0
    assert store.stored[SOURCE_ID] == [STORED]                 # was: []
    assert [event["event_id"] for event in output] == ["EVENT-OLD"]
    failure = state["extraction_failures"][0]                  # was: none recorded
    assert failure["error"] == "ExtractionNotPersistedError"
    assert failure["run_status"] == "failed"
    assert failure["preserved_existing_event_count"] == 1
    assert [w["type"] for w in state["warnings"]] == ["source_extraction_failure"]  # was: none
    assert state["extraction_runs"][0]["events_persisted"] is False


def test_successful_extraction_replaces_stored_events():
    store, state, output = run(v6(FramesAndVerifier([ACME, SARVAM])), [STORED])
    assert store.replace_calls == 1
    assert {e["roles"]["actor"]["mention"] for e in store.stored[SOURCE_ID]} == {"Acme Inc.", "Sarvam"}
    assert len(output) == 2
    assert state["extraction_failures"] == []
    assert state["extraction_runs"][0]["events_persisted"] is True


def test_partial_extraction_does_not_replace_existing_events():
    agent = v6(FailsSecondWindow(), window_characters=100)
    store, state, output = run(agent, [STORED])
    assert agent.get_last_run_metadata()["run_status"] == "partial_ok"
    assert store.stored[SOURCE_ID] == [STORED]
    assert [event["event_id"] for event in output] == ["EVENT-OLD"]
    assert state["extraction_failures"][0]["run_status"] == "partial_ok"


def test_partial_extraction_is_stored_when_nothing_was_stored():
    agent = v6(FailsSecondWindow(), window_characters=100)
    store, state, output = run(agent, [])
    assert agent.get_last_run_metadata()["run_status"] == "partial_ok"
    assert [e["roles"]["actor"]["mention"] for e in store.stored[SOURCE_ID]] == ["Acme Inc."]
    assert state["extraction_runs"][0]["events_persisted"] is True


def test_extractor_exception_keeps_stored_events():
    import os
    os.environ["EXTRACTION_V6_GEMINI_CLASS"] = "services.does_not_exist:GeminiLLMService"
    try:
        agent = ExtractionAgentV6(entity_tagger=None, fallback_to_v5=False)  # no provider -> raises
    finally:
        os.environ.pop("EXTRACTION_V6_GEMINI_CLASS", None)
    store, state, output = run(agent, [STORED])
    assert store.replace_calls == 0
    assert store.stored[SOURCE_ID] == [STORED]
    assert [event["event_id"] for event in output] == ["EVENT-OLD"]
    assert state["extraction_failures"][0]["error"] == "RuntimeError"
    assert state["extraction_failures"][0]["preserved_existing_event_count"] == 1


HOTEL_ARTICLE = {"source_id": SOURCE_ID, "title": "",
                 "body": "Hotel Group acquired a resort.\n\nThe deal includes an AI concierge for guests."}
HOTEL = {
    "taxonomy": "observed_event", "event_type": "acquisition",
    "actor": {"mention": "Hotel Group", "sentence_id": "S1", "entity_type": "organization"},
    "action_sentence_ids": ["S1"], "ai_link_sentence_ids": ["S2"],
}


def run_hotel(stored, **stub_kwargs):
    agent = v6(FramesAndVerifier([HOTEL], filter_by_window=False, **stub_kwargs))
    store = MemoryEventStore(stored)
    orchestrator = object.__new__(R)
    orchestrator.extraction_agent = agent
    orchestrator.event_service = store
    orchestrator._get_source_extraction_payload = lambda source_id: dict(HOTEL_ARTICLE)
    state = {"research_depth": "deep", "warnings": []}
    output = orchestrator._extract_events(source_ids=[SOURCE_ID], state=state)
    return agent, store, state, output


def test_verifier_failure_keeps_previously_verified_events():
    # Reviewer case 3: a verifier timeout gave run_status "ok" and replaced the
    # stored event with a copy missing its AI link, with no warning.
    _, verified_store, _, _ = run_hotel([])
    verified = verified_store.stored[SOURCE_ID]
    assert verified[0]["roles"]["ai_link_sentence_ids"] == ["S2"]

    agent, store, state, output = run_hotel(verified, fail_verification=True)
    metadata = agent.get_last_run_metadata()
    assert metadata["run_status"] == "partial_ok" and metadata["complete"] is False
    assert store.replace_calls == 0
    assert store.stored[SOURCE_ID] == verified
    assert output[0]["roles"]["ai_link_sentence_ids"] == ["S2"]
    failure = state["extraction_failures"][0]
    assert failure["error"] == "ExtractionNotPersistedError"
    assert failure["incomplete_reasons"] == ["ai_link_verification_failed"]
    assert [w["type"] for w in state["warnings"]] == ["source_extraction_failure"]
    assert state["extraction_runs"][0]["events_persisted"] is False


def test_supported_verdict_with_invalid_quote_keeps_previously_verified_events():
    # Reviewer case (v6.6): "supported" with no quote, or a quote not in the
    # source, dropped the link but left the run "ok", so the stored verified
    # event was overwritten by a copy without its AI link, with no warning.
    _, verified_store, _, _ = run_hotel([])
    verified = verified_store.stored[SOURCE_ID]
    sentence = "The deal includes an AI concierge for guests."
    for quote in ("", "The resort adds an AI butler"):
        agent, store, state, output = run_hotel(verified, verdicts={sentence: ("supported", quote)})
        metadata = agent.get_last_run_metadata()
        assert metadata["complete"] is False, quote
        assert store.replace_calls == 0 and store.stored[SOURCE_ID] == verified
        assert output[0]["roles"]["ai_link_sentence_ids"] == ["S2"]
        assert state["extraction_failures"][0]["incomplete_reasons"] == ["ai_link_verification_failed"]
        assert [w["type"] for w in state["warnings"]] == ["source_extraction_failure"]


def test_v5_fallback_does_not_replace_stored_v6_events():
    # Default fallback on: v6 model down -> v5 runs. Its result is degraded and
    # must not overwrite events a working v6 run stored.
    _, verified_store, _, _ = run_hotel([])
    verified = verified_store.stored[SOURCE_ID]
    agent = ExtractionAgentV6(llm_service=ModelDown(), entity_tagger=None, fallback_to_v5=True)
    store = MemoryEventStore(verified)
    orchestrator = object.__new__(R)
    orchestrator.extraction_agent = agent
    orchestrator.event_service = store
    orchestrator._get_source_extraction_payload = lambda source_id: dict(HOTEL_ARTICLE)
    state = {"research_depth": "deep", "warnings": []}
    output = orchestrator._extract_events(source_ids=[SOURCE_ID], state=state)
    assert agent.get_last_run_metadata()["extractor_used"] == "v5_fallback"
    assert store.replace_calls == 0 and store.stored[SOURCE_ID] == verified
    assert output[0]["roles"]["ai_link_sentence_ids"] == ["S2"]
    assert state["extraction_failures"][0]["incomplete_reasons"] == ["v6_failed_used_v5_fallback"]
    assert {w["type"] for w in state["warnings"]} == {"source_extraction_failure", "extraction_fallback_to_v5"}


def test_verifier_failure_with_nothing_stored_is_stored_and_flagged():
    _, store, state, _ = run_hotel([], fail_verification=True)
    stored = store.stored[SOURCE_ID][0]
    assert stored["roles"]["ai_link_sentence_ids"] == []
    assert stored["extraction_metadata"]["source_extraction_complete"] is False
    assert stored["extraction_metadata"]["incomplete_reasons"] == ["ai_link_verification_failed"]
    warning = state["warnings"][0]
    assert warning["type"] == "source_extraction_incomplete"
    assert warning["incomplete_reasons"] == ["ai_link_verification_failed"]
    run = state["extraction_runs"][0]
    assert run["events_persisted"] is True and run["extraction_complete"] is False


def test_model_that_finds_no_events_is_a_real_answer():
    class NoEvents(FramesAndVerifier):
        def __init__(self):
            super().__init__([])

    agent = v6(NoEvents())
    store, state, _ = run(agent, [STORED])
    assert agent.get_last_run_metadata()["run_status"] == "no_events_extracted"
    assert store.stored[SOURCE_ID] == []
    assert state["extraction_failures"] == []


if __name__ == "__main__":
    failures = 0
    for name, function in list(globals().items()):
        if name.startswith("test_") and callable(function):
            try:
                function()
                print(f"PASS {name}")
            except Exception as error:
                failures += 1
                print(f"FAIL {name}: {type(error).__name__}: {error}")
    raise SystemExit(1 if failures else 0)
