"""
Embedding-based article selection: services/vector_service.py and the
orchestrator's _select_discovery_candidates.

A fake Ollama transport maps words to a few concept dimensions (AI, retail,
finance, ...). It tests the plumbing (relevance threshold, MMR order,
fallback, caching, prefixes), not the quality of a real embedding model.

Run:  python -m tests.test_vector_selection
"""

from __future__ import annotations

import hashlib
import math

from services.research_orchestrator_service import ResearchOrchestratorService as R
from services.vector_service import VectorService, VectorServiceError


CONCEPTS = {
    0: {"ai", "artificial", "intelligence", "generative", "assistant", "model", "llm", "agents", "chatbot"},
    1: {"retail", "retailer", "retailers", "shopping", "store", "stores", "walmart", "shoppers", "checkout"},
    2: {"investment", "jobs", "turnover", "profit", "results", "shares", "acquired", "acquisition"},
    3: {"greece", "nike", "lvmh", "brics", "satellites", "rocket"},
}


def fake_vector(text: str) -> list[float]:
    vector = [0.0] * 16
    for raw in text.casefold().replace(":", " ").split():
        word = raw.strip(".,;()'\"")
        hit = False
        for dim, words in CONCEPTS.items():
            if word in words:
                vector[dim] += 1.0
                hit = True
        if not hit and word:
            vector[4 + int(hashlib.md5(word.encode()).hexdigest(), 16) % 12] += 0.15
    return vector


class FakeOllama:
    def __init__(self, missing_model=False, legacy=False):
        self.calls = []
        self.missing_model = missing_model
        self.legacy = legacy

    def __call__(self, path, payload):
        self.calls.append((path, payload))
        if self.missing_model:
            raise VectorServiceError("Ollama returned HTTP 404: model 'nomic-embed-text' not found")
        if path == "/api/embed":
            if self.legacy:
                raise VectorServiceError("Ollama returned HTTP 404: 404 page not found")
            return {"embeddings": [fake_vector(text) for text in payload["input"]]}
        return {"embedding": fake_vector(payload["prompt"])}


def service(**kwargs):
    return VectorService(transport=FakeOllama(**kwargs))


FAILED_RUN = [
    "Greece approves €6.6bn of private investment set to create 21,000 jobs",
    "Wall Street is anxiously awaiting the results to be announced by Nike",
    "CRIF acquired ICAP and turnover rose 10%",
    "BRICS expanded in 2024 to include Egypt and Ethiopia",
]
WALMART = "Walmart launches generative AI shopping assistant for shoppers"


# ----------------------------------------------------------------------
# VectorService
# ----------------------------------------------------------------------

def test_embeddings_are_normalised_cached_and_prefixed_for_nomic():
    fake = FakeOllama()
    vs = VectorService(transport=fake)
    first = vs.embed(["AI retail"], purpose="query")[0]
    assert abs(math.sqrt(sum(x * x for x in first)) - 1.0) < 1e-9
    vs.embed(["AI retail"], purpose="query")
    assert len(fake.calls) == 1  # cached
    assert fake.calls[0][1]["input"] == ["search_query: AI retail"]


def test_older_ollama_falls_back_to_legacy_endpoint():
    fake = FakeOllama(legacy=True)
    vs = VectorService(transport=fake)
    assert vs.available()
    assert any(path == "/api/embeddings" for path, _ in fake.calls)


def test_missing_model_reports_unavailable_without_raising():
    vs = service(missing_model=True)
    assert vs.available() is False
    assert "not found" in vs.last_error


def test_remote_endpoint_refused_by_default():
    try:
        VectorService(base_url="https://example.com", transport=FakeOllama())
    except VectorServiceError:
        return
    raise AssertionError("remote base_url should be refused")


def test_mmr_prefers_a_distinct_relevant_story_over_a_duplicate():
    vs = service()
    docs = [WALMART, WALMART + " today", "Retailers deploy AI agents at checkout"]
    order = [row["index"] for row in vs.mmr_select("AI retail", docs, lambda_=0.5)]
    assert order.index(2) < order.index(1)


def test_event_index_upsert_and_search():
    vs = service()
    vs.upsert_event(event_id="EVENT-A", title=WALMART, summary="", metadata={"source_id": "SOURCE-1"})
    vs.upsert_event(event_id="EVENT-B", title=FAILED_RUN[0], summary="")
    assert vs.search("AI retail", k=1)[0]["event_id"] == "EVENT-A"


# ----------------------------------------------------------------------
# Orchestrator selection
# ----------------------------------------------------------------------

def orchestrator_with(vector_service):
    orchestrator = object.__new__(R)
    orchestrator.vector_service = vector_service
    return orchestrator


def candidates():
    items = [{"title": t, "url": f"https://news.example/{i}"} for i, t in enumerate(FAILED_RUN)]
    items.append({"title": WALMART, "url": "https://news.example/walmart"})
    return items


def test_embeddings_keep_on_topic_article_the_keyword_check_drops():
    assert not R._discovery_topic_match("AI retail", WALMART)  # keyword check misses it
    state: dict = {}
    kept = orchestrator_with(service())._select_discovery_candidates(
        topic="AI retail", candidates=candidates(), state=state
    )
    assert [item["url"] for item in kept] == ["https://news.example/walmart"]
    assert state["source_discovery_relevance_method"] == "embedding:nomic-embed-text"
    assert state["source_discovery_topic_rejected_count"] == len(FAILED_RUN)
    assert all("semantic_relevance" in row for row in state["source_discovery_topic_rejections"])
    assert kept[0]["_semantic_mmr"] > 0


def test_falls_back_to_keyword_check_when_embeddings_unavailable():
    state: dict = {}
    kept = orchestrator_with(service(missing_model=True))._select_discovery_candidates(
        topic="AI retail", candidates=candidates(), state=state
    )
    assert state["source_discovery_relevance_method"] == "keyword"
    assert "not found" in state["source_discovery_relevance_fallback_reason"]
    assert kept == []  # keyword check: none of these say "retail"


def test_final_ranking_keeps_mmr_order_over_keyword_and_recency_points():
    # Reviewer's case, with fixed vectors: MMR orders distinct story -> duplicate
    # pair; the duplicates have every old-score advantage (fresh, event verbs,
    # press URLs). Final ranking must keep the MMR order.
    from datetime import datetime, timezone
    duplicate = "Walmart launches generative AI shopping assistant, announces deployment"
    distinct = "Retailers adopt AI pricing tools"
    table = {
        "AI retail": [1.0, 0.0, 0.0],
        duplicate: [0.9, 0.436, 0.0],   # relevance 0.90
        distinct: [0.95, 0.0, 0.312],   # relevance 0.95, similarity to duplicate 0.86
    }

    def transport(path, payload):
        texts = payload.get("input") or [payload.get("prompt")]
        vectors = [table.get(text.split(": ", 1)[-1], [0.0, 0.0, 1.0]) for text in texts]
        return {"embeddings": vectors} if path == "/api/embed" else {"embedding": vectors[0]}

    now = datetime.now(timezone.utc).isoformat()
    items = [
        {"title": duplicate, "url": "https://a.example/newsroom/walmart-1", "published_at": now},
        {"title": duplicate, "url": "https://b.example/press/walmart-2", "published_at": now},
        {"title": distinct, "url": "https://c.example/story", "published_at": "2026-03-01T00:00:00+00:00"},
    ]
    selected = orchestrator_with(VectorService(transport=transport))._select_discovery_candidates(
        topic="AI retail", candidates=items, state={}
    )
    mmr_order = [item["url"] for item in sorted(selected, key=lambda item: item["_semantic_rank"])]
    assert mmr_order == [
        "https://c.example/story",
        "https://a.example/newsroom/walmart-1",
        "https://b.example/press/walmart-2",
    ]
    # The old score alone would put both duplicates first.
    old_scores = {item["url"]: R._score_initial_discovery_candidate(item)[0] for item in selected}
    assert min(old_scores[url] for url in mmr_order[1:]) > old_scores["https://c.example/story"]

    ranked = R._rank_initial_discovery_candidates(selected, research_depth="deep")
    assert [item["url"] for item in ranked] == mmr_order


def test_previously_used_urls_move_to_the_back_without_reordering_the_rest():
    items = [
        {"title": WALMART, "url": "https://news.example/old", "_semantic_rank": 0},
        {"title": "Retailers deploy AI agents at checkout", "url": "https://news.example/new1", "_semantic_rank": 1},
        {"title": "AI retail pricing tools spread", "url": "https://news.example/new2", "_semantic_rank": 2},
    ]
    ranked = R._rank_initial_discovery_candidates(
        items, research_depth="deep", recent_urls={R._discovery_url_identity(items[0])}
    )
    assert [item["url"] for item in ranked] == [
        "https://news.example/new1", "https://news.example/new2", "https://news.example/old"
    ]


def test_saved_sources_use_embeddings_when_available():
    payloads = {
        "SOURCE-A": {"title": FAILED_RUN[0], "content": FAILED_RUN[0]},
        "SOURCE-B": {"title": WALMART, "content": WALMART},
    }
    orchestrator = orchestrator_with(service())
    orchestrator._load_source_history = lambda topic: {"entries": [{"source_id": "SOURCE-A"}, {"source_id": "SOURCE-B"}]}
    orchestrator._get_source_extraction_payload = lambda source_id: payloads[source_id]
    state: dict = {}
    assert orchestrator._cached_source_ids_for_topic(topic="AI retail", max_sources=5, state=state) == ["SOURCE-B"]


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
