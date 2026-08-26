from agents.evidence_discovery_agent import EvidenceDiscoveryAgent


class FailSearch:
    def search(self, *args, **kwargs):
        raise AssertionError(
            "search should not run"
        )


class FailFetch:
    def fetch(self, *args, **kwargs):
        raise AssertionError(
            "fetch should not run"
        )


def test_discovery_respects_external_search_disabled():
    agent = EvidenceDiscoveryAgent(
        web_search_service=FailSearch(),
        source_fetch_service=FailFetch(),
        evidence_processor=lambda **kwargs: [],
    )

    result = agent.discover(
        insight_id="INSIGHT-1",
        insight_text="Grid access may matter.",
        insight_type="prospective",
        premises=[
            {
                "premise_id": "PREMISE-1",
                "text": "Grid queues are long.",
                "evidence_ids": [],
            }
        ],
        blueprint={
            "evidence_discovery_policy": {
                "external_search_enabled": False,
                "max_search_rounds_per_insight": 1,
                "max_search_queries_per_insight": 1,
                "max_sources_fetched_per_insight": 1,
                "max_results_per_query": 1,
                "max_saturation_rounds": 1,
            }
        },
    )

    assert (
        result["stop_reason"]
        == "external_search_disabled"
    )
    assert (
        result["usage"][
            "queries_executed"
        ]
        == 0
    )