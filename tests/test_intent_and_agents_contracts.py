from types import SimpleNamespace

from services.intent_service import IntentService
from agents.extraction_agent import ExtractionAgent
from agents.evidence_builder_agent import EvidenceBuilderAgent
from agents.red_team_agent import RedTeamAgent
from agents.constraint_agent import ConstraintAgent
from agents.strategic_inference_agent import StrategicInferenceAgent


def test_intent_deterministic_fallback():
    service = IntentService()

    intent = service.interpret(
        request_text=(
            "What is changing in "
            "AI infrastructure?"
        )
    )

    assert (
        intent["intent_type"]
        == "research"
    )
    assert (
        intent["analysis_mode"]
        == "research"
    )
    assert (
        intent["requested_output"]
        == "research_report"
    )
    assert (
        intent["research_question"]
        ==
        "What is changing in AI infrastructure?"
    )


def test_extraction_agent_empty_input_does_not_call_llm():
    class FailLLM:
        def ask(
            self,
            *args,
            **kwargs,
        ):
            raise AssertionError(
                "LLM should not be called"
            )

    agent = ExtractionAgent(
        llm_service=FailLLM()
    )

    article = {
        "title": "",
        "summary": "",
        "content": "",
        "url": (
            "https://example.com/empty"
        ),
    }

    result = agent.extract(
        article
    )

    assert result["events"] == []

    assert (
        agent.get_last_run_metadata()[
            "run_status"
        ]
        == "empty_input"
    )


def test_evidence_builder_empty_input_does_not_call_llm():
    class FailLLM:
        def ask(
            self,
            *args,
            **kwargs,
        ):
            raise AssertionError(
                "LLM should not be called"
            )

    agent = EvidenceBuilderAgent(
        llm_service=FailLLM()
    )

    result = agent.build_evidence(
        topic="test",
        current_events="",
        historical_context="",
        graph_context="",
    )

    assert result["facts"] == []
    assert result["metrics"] == []

    assert (
        agent.get_last_run_metadata()[
            "run_status"
        ]
        == "empty_input"
    )


def test_red_team_empty_report_fails_closed_without_release_decision():
    class FailLLM:
        def ask(
            self,
            *args,
            **kwargs,
        ):
            raise AssertionError(
                "LLM should not be called"
            )

    agent = RedTeamAgent(
        llm_service=FailLLM()
    )

    result = agent.review(
        topic="test",
        evidence={},
        constraints={},
        report={},
        grounding_results={},
    )

    assert (
        result["review_status"]
        == "failed"
    )

    assert (
        "release_allowed"
        not in result
    )


def test_constraint_agent_no_evidence_returns_no_constraints():
    class FailLLM:
        def ask_json(
            self,
            *args,
            **kwargs,
        ):
            raise AssertionError(
                "LLM should not be called"
            )

    agent = ConstraintAgent(
        llm_service=FailLLM()
    )

    result = agent.analyze(
        research_question="What changed?",
        current_events=[],
        evidence=[],
        historical_events=[],
        graph_context={},
        blueprint={},
    )

    assert (
        result["constraints"]
        == []
    )

    assert (
        result["metadata"][
            "run_status"
        ]
        == "no_accepted_evidence"
    )


def test_strategic_inference_no_evidence_fails_closed():
    class FailLLM:
        def ask_json(
            self,
            *args,
            **kwargs,
        ):
            raise AssertionError(
                "LLM should not be called"
            )

    agent = StrategicInferenceAgent(
        llm_service=FailLLM()
    )

    result = agent.analyze(
        research_question="What changed?",
        current_events=[],
        evidence=[],
        historical_events=[],
        graph_context={},
        constraints={},
        blueprint={},
    )

    assert (
        result["candidate_insights"]
        == []
    )

    assert (
        result["metadata"][
            "run_status"
        ]
        == "no_accepted_evidence"
    )

    assert (
        result["evidence_manifest"][
            "all_accepted_evidence_ids"
        ]
        == []
    )