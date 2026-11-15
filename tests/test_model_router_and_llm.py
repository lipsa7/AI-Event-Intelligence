import pytest

from services.model_router_service import ModelRouterService
from services.llm_service import (
    LLMService,
    LLMConfigurationError,
)


def test_model_router_uses_local_catalog():
    router = ModelRouterService(
        model_catalog={
            "local-small": {
                "model_name": "qwen3:4b",
                "provider": "ollama",
                "tier": "small",
                "capabilities": [
                    "json",
                    "reasoning",
                    "evidence_classification",
                ],
                "local": True,
                "paid": False,
            },
            "local-medium": {
                "model_name": "qwen3:8b",
                "provider": "ollama",
                "tier": "medium",
                "capabilities": [
                    "json",
                    "reasoning",
                    "strong_reasoning",
                    "evidence_classification",
                ],
                "local": True,
                "paid": False,
            },
        },
        availability_checker=lambda model_name: True,
    )

    decision = router.route(
        task="grounding",
        blueprint={
            "model_routing_policy": {
                "require_local": True,
                "allow_escalation": True,
                "max_escalations_per_stage": 1,
                "max_tier": "xlarge",
                "allowed_model_ids": [],
                "blocked_model_ids": [],
                "require_confirmed_availability": False,
                "task_overrides": {
                    "grounding": {
                        "minimum_tier": "small",
                        "required_capabilities": [
                            "json",
                            "evidence_classification",
                        ],
                    }
                },
            },
            "cost_policy": {
                "paid_external_services_allowed": False,
                "paid_llm_calls_allowed": False,
                "paid_search_apis_allowed": False,
            },
        },
    )

    assert (
        decision["provider"]
        == "ollama"
    )

    assert (
        decision[
            "paid_model_selected"
        ]
        is False
    )


def test_llm_service_rejects_remote_base_url_by_default():
    with pytest.raises(
        LLMConfigurationError
    ):
        LLMService(
            base_url=(
                "https://remote.example.com"
            ),
            allow_remote_base_url=False,
        )