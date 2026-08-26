from services.web_search_service import (
    CallableSearchProvider,
    WebSearchService,
    build_default_web_search_service,
)


def test_callable_provider_deduplicates_urls_and_keeps_snippets_non_evidence():
    provider = CallableSearchProvider(
        lambda **kwargs: {
            "results": [
                {
                    "title": "A",
                    "url": (
                        "https://example.com/a"
                        "?utm_source=x"
                    ),
                    "snippet": "one",
                },
                {
                    "title": "A duplicate",
                    "url": (
                        "https://example.com/a"
                    ),
                    "snippet": "two",
                },
            ],
            "usage": {},
        },
        name="fake",
    )

    service = WebSearchService(
        providers=[
            provider
        ],
        allow_failover=False,
    )

    result = service.search(
        query="test",
        max_results=10,
    )

    assert (
        len(
            result["results"]
        )
        == 1
    )

    assert (
        result["results"][0][
            "is_grounding_evidence"
        ]
        is False
    )


def test_default_factory_requires_no_paid_provider():
    service = (
        build_default_web_search_service(
            searxng_base_url=(
                "http://127.0.0.1:8080"
            )
        )
    )

    assert [
        provider.provider_name
        for provider
        in service.providers
    ] == [
        "searxng"
    ]


def test_module_no_longer_depends_on_typing_Set_name():
    # Regression test for the NameError
    # reported during import/runtime.
    import services.web_search_service as module

    assert not hasattr(
        module,
        "Set",
    )