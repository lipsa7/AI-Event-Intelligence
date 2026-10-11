"""
Retrieval fixes: topic check before fetch, topic-anchored queries, NewsAPI
relevance parameters, and off-topic saved sources not reused.

The off-topic headlines below are the actual events from the abstained run
on 2026-10-03.

Run:  python -m tests.test_discovery_relevance
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

from services import web_search_service as wss
from services.research_orchestrator_service import ResearchOrchestratorService as R


OFF_TOPIC_FROM_FAILED_RUN = [
    "Greece approves €6.6bn of private investment set to create 21,000 jobs",
    "Almost five years after the Italian group CRIF acquired ICAP, the financial statements just published show that the group increased its turnover",
    "Wall Street is anxiously awaiting the results to be announced by Nike, which since last week has no longer been included in the S&P 100",
    "following the $15.8 billion acquisition, and was CEO of Rimowa for four years, having brought it into LVMH in 2016",
    "BRICS, originally comprising Brazil, Russia, India, China and South Africa, expanded in 2024",
    "Adobe has released its forecast for the 2026 holiday shopping season, projecting that online sales will reach $275",
    "Then on September 19, a Long March-2D rocket launched four PIESAT-2 synthetic aperture radar satellites",
    "Prodea Investments announced a set of results the day before yesterday that did not satisfy the market.",
]


def test_failed_run_headlines_are_rejected_for_ai_topics():
    for topic in ("AI in retail", "artificial intelligence", "generative AI in banking"):
        for headline in OFF_TOPIC_FROM_FAILED_RUN:
            assert not R._discovery_topic_match(topic, headline), (topic, headline)


def test_on_topic_headlines_are_kept():
    assert R._discovery_topic_match("AI in retail", "Walmart rolls out generative AI shopping assistant across retail stores")
    assert R._discovery_topic_match("AI in retail", "Retailers adopt AI agents to manage inventory")
    assert R._discovery_topic_match("artificial intelligence", "OpenAI signs deal with Microsoft for new data centres")
    assert R._discovery_topic_match("generative AI in banking", "HDFC Bank deploys generative AI for loan processing")
    # AI topic with another content word: that word is required too.
    assert not R._discovery_topic_match("AI in retail", "OpenAI launches a new reasoning model")


def test_non_ai_topic_needs_its_content_words():
    assert R._discovery_topic_match("data center power", "Utilities race to supply power for new data centers")
    assert not R._discovery_topic_match("data center power", "Greece approves €6.6bn of private investment")


def test_filter_records_rejections_and_keeps_on_topic_items():
    state: dict = {}
    items = [{"title": t, "url": f"https://example.com/{i}"} for i, t in enumerate(OFF_TOPIC_FROM_FAILED_RUN)]
    items.append({"title": "Walmart rolls out generative AI across retail stores", "url": "https://example.com/ok"})
    kept = R._filter_discovery_candidates_by_topic(topic="AI in retail", candidates=items, state=state)
    assert [item["url"] for item in kept] == ["https://example.com/ok"]
    assert state["source_discovery_topic_rejected_count"] == len(OFF_TOPIC_FROM_FAILED_RUN)


def test_queries_always_contain_the_topic_and_no_filler():
    for depth, expected in (("fast", 4), ("deep", 6)):
        queries = R._build_initial_discovery_queries(topic="AI in retail", research_depth=depth, rotation=0)
        assert len(queries) == expected
        assert queries[0] == "AI in retail"
        for query in queries[1:]:
            assert query.startswith("(AI in retail) AND (")
        joined = " ".join(queries).casefold()
        for filler in ("latest significant", "since last month", "2026", "october"):
            assert filler not in joined


def test_newsapi_request_uses_relevance_and_headline_fields(monkeypatch=None):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, size):
            return json.dumps({"status": "ok", "totalResults": 0, "articles": []}).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        return Response()

    original = wss.urlopen
    wss.urlopen = fake_urlopen
    try:
        wss.NewsAPIProvider(api_key="test-key").search("AI in retail", limit=5)
    finally:
        wss.urlopen = original
    params = parse_qs(urlparse(captured["url"]).query)
    assert params["sortBy"] == ["relevancy"]
    assert params["searchIn"] == ["title,description"]
    assert params["q"] == ["AI in retail"]


def test_off_topic_saved_sources_are_not_reused():
    payloads = {
        "SOURCE-A": {"title": "Greece approves €6.6bn of private investment", "content": "Greece approves private investment set to create jobs."},
        "SOURCE-B": {"title": "Walmart expands generative AI in retail stores", "content": "Walmart said its generative AI assistant is now in 4,000 retail stores."},
    }
    orchestrator = object.__new__(R)
    orchestrator._load_source_history = lambda topic: {
        "entries": [{"source_id": "SOURCE-A"}, {"source_id": "SOURCE-B"}]
    }
    orchestrator._get_source_extraction_payload = lambda source_id: payloads[source_id]
    state: dict = {}
    selected = orchestrator._cached_source_ids_for_topic(topic="AI in retail", max_sources=5, state=state)
    assert selected == ["SOURCE-B"]
    assert state["source_discovery_cached_rejections"][0]["error"] == "OffTopicCachedSource"


def test_every_lane_query_is_anchored_to_the_topic():
    topic = "artificial intelligence"
    lane_queries = R._strategic_corpus_refill_queries(
        topic=topic, assessment={}, events=[], round_number=2
    )
    lane_queries += [
        "artificial intelligence October 2026 announcement launch deployment -site:reuters.com",
        "artificial intelligence Insilico Medicine 2026 follow-up contracts capacity financing",
    ]
    for raw in lane_queries:
        anchored = R._anchor_discovery_query(topic, raw)
        assert anchored == topic or anchored.startswith(f"({topic}) AND ("), anchored
        for dropped in ("2026", "October", "recent", "site:", "major new developments"):
            assert dropped not in anchored, (dropped, anchored)
    assert R._anchor_discovery_query(
        topic, "artificial intelligence Insilico Medicine 2026 follow-up contracts"
    ) == '(artificial intelligence) AND ("Insilico Medicine") AND (contracts)'
    # Cluster follow-ups keep their actors required, not OR-ed with generic words.
    cluster_query = R._anchor_discovery_query(
        topic, 'artificial intelligence "Microsoft" "OpenAI" 2026 latest developments contracts deployment'
    )
    assert cluster_query == '(artificial intelligence) AND ("Microsoft" OR "OpenAI") AND (contracts OR deployment)'


def test_discovery_sends_anchored_queries_and_fetches_only_on_topic_articles():
    sent, fetched = [], []

    class Search:
        def search(self, query, **kwargs):
            sent.append(query)
            return {"results": [
                {"title": OFF_TOPIC_FROM_FAILED_RUN[0], "url": "https://news.example/greece",
                 "snippet": "Investment plan.", "published_at": "2026-10-01T00:00:00+00:00"},
                {"title": "Walmart rolls out generative AI assistant to retail stores",
                 "url": "https://news.example/walmart", "snippet": "AI retail rollout.",
                 "published_at": "2026-10-01T00:00:00+00:00"},
            ]}

    class Fetch:
        def __getattr__(self, name):
            def fetch(*args, **kwargs):
                fetched.append(kwargs.get("url"))
                raise RuntimeError("test stops at fetch")
            return fetch

    orchestrator = object.__new__(R)
    orchestrator.web_search_service = Search()
    orchestrator.source_fetch_service = Fetch()
    orchestrator._load_source_history = lambda topic: {"entries": [], "run_count": 0}
    state = {"research_depth": "deep", "warnings": [], "errors": []}
    orchestrator._discover_sources(topic="AI retail", max_sources=5, state=state)

    assert sent and all(q == "AI retail" or q.startswith("(AI retail) AND (") for q in sent)
    assert fetched and set(fetched) == {"https://news.example/walmart"}
    assert state["source_discovery_topic_rejected_count"] >= 1


def test_deep_budget_defaults_allow_a_larger_corpus():
    import services.research_orchestrator_service as orchestrator_module
    assert orchestrator_module.DEEP_PROFILE.max_discovered_sources == 20
    assert orchestrator_module.DEEP_CORPUS_MIN_STRATEGIC_EVENTS == 12
    assert orchestrator_module.DEEP_CORPUS_REFILL_MAX_ROUNDS == 4
    assert orchestrator_module.DEEP_CORPUS_REFILL_MAX_SOURCES_PER_ROUND == 8
    assert orchestrator_module._env_int("EVENT_INTELLIGENCE_UNSET_FOR_TEST", 7, 1, 10) == 7


def test_no_newsapi_key_is_embedded_in_code():
    import inspect as _inspect
    source = _inspect.getsource(wss)
    assert not hasattr(wss, "HARDCODED_NEWSAPI_API_KEY")
    # NewsAPI keys are 32 hex characters; none may appear in the code.
    assert not re.search(r"\b[0-9a-f]{32}\b", source, re.I)


def test_newsapi_key_comes_from_configuration_or_fails_clearly():
    import os
    saved = (wss._read_key_from_dotenv, wss._read_key_from_streamlit_secrets,
             os.environ.pop("NEWSAPI_API_KEY", None), os.environ.pop("NEWS_API_KEY", None))
    wss._read_key_from_dotenv = lambda: ""
    wss._read_key_from_streamlit_secrets = lambda: ""
    try:
        assert wss.resolve_newsapi_key() == ""
        os.environ["NEWS_DISCOVERY_PROVIDERS"] = "newsapi"   # NewsAPI as the only source
        try:
            wss.build_default_web_search_service()
        except wss.NewsAPIAuthError as error:
            assert "NEWSAPI_API_KEY" in str(error)
        else:
            raise AssertionError("expected NewsAPIAuthError without a configured key")
        finally:
            os.environ.pop("NEWS_DISCOVERY_PROVIDERS", None)
        os.environ["NEWSAPI_API_KEY"] = "from-environment"
        assert wss.resolve_newsapi_key() == "from-environment"
    finally:
        wss._read_key_from_dotenv, wss._read_key_from_streamlit_secrets = saved[0], saved[1]
        os.environ.pop("NEWSAPI_API_KEY", None)
        if saved[2] is not None:
            os.environ["NEWSAPI_API_KEY"] = saved[2]
        if saved[3] is not None:
            os.environ["NEWS_API_KEY"] = saved[3]


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
