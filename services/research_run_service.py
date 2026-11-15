"""
Compatibility research entry point for the Streamlit dashboard.

Recommended location:
    services/research_run_service.py

This adapter deliberately does not rebuild or bypass the research pipeline.
It delegates directly to ResearchOrchestratorService, preserving grounding,
red-team, policy/release, evidence discovery, Fast/Deep profiles, and report
persistence owned by the orchestrator.
"""

from __future__ import annotations

from typing import Any

from services.research_orchestrator_service import ResearchOrchestratorService


SERVICE_VERSION = "research-run-service-v1.0-orchestrator-adapter"


class ResearchRunService:
    """Stable dashboard-facing adapter over ResearchOrchestratorService."""

    def __init__(self, orchestrator: ResearchOrchestratorService | None = None) -> None:
        self.orchestrator = orchestrator or ResearchOrchestratorService()

    def run_research(
        self,
        research_question: str,
        research_depth: str = "fast",
    ) -> dict[str, Any]:
        question = " ".join(str(research_question or "").split()).strip()
        depth = " ".join(str(research_depth or "fast").split()).strip().casefold()

        if not question:
            raise ValueError("research_question cannot be empty.")

        if depth not in {"fast", "deep"}:
            raise ValueError(
                "research_depth must be 'fast' or 'deep'. "
                f"Received: {research_depth!r}"
            )

        return self.orchestrator.run(
            topic=question,
            research_depth=depth,
        )

    # Compatible aliases for callers that discover one of these names.
    def run(
        self,
        research_question: str,
        research_depth: str = "fast",
    ) -> dict[str, Any]:
        return self.run_research(
            research_question=research_question,
            research_depth=research_depth,
        )

    def research(
        self,
        research_question: str,
        research_depth: str = "fast",
    ) -> dict[str, Any]:
        return self.run_research(
            research_question=research_question,
            research_depth=research_depth,
        )

    def execute(
        self,
        research_question: str,
        research_depth: str = "fast",
    ) -> dict[str, Any]:
        return self.run_research(
            research_question=research_question,
            research_depth=research_depth,
        )


def run_research(
    research_question: str,
    research_depth: str = "fast",
) -> dict[str, Any]:
    """Module-level compatibility entry point."""
    return ResearchRunService().run_research(
        research_question=research_question,
        research_depth=research_depth,
    )
