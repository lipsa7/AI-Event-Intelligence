"""v13.16: fixes from RUN-4367E5FE6BE84506BBC92CBB (20 Nov 2026)."""
import pytest

from services import exa_contents as exa

ARTICLE = ("California's attorney general sued OpenAI on Monday, alleging its AI agents attacked Hugging Face "
           "and other companies' systems. The complaint seeks penalties under the state's unfair competition law. ") * 3
REUTERS = ("https://www.reuters.com/business/california-sues-openai-over-ai-models-cyberattacks-hugging-face-"
           "other-companies-2026-11-16/")


def test_precedent_rule_no_longer_rejects_past_cases_for_different_actors():
    import inspect
    from services import hypothesis_evidence_service as hes
    src = inspect.getsource(hes)
    assert "SAME ENTITY: a record must be about the same organisation" not in src
    assert "EXPECTED to involve other actors" in src and "never reject it because its actors differ" in src
    assert "is a past case for a ban reversed under pressure" in src


def test_slug_query_from_headline_urls_only():
    from services.source_fetch_service import _slug_query
    assert _slug_query(REUTERS) == "california sues openai over ai models cyberattacks hugging face other companies"
    assert _slug_query("https://apnews.com/article/62450bd442c04518860e0b312b9f13b4") == ""
    assert _slug_query("https://consent.yahoo.com/v2/collectConsent?sessionId=1") == ""


@pytest.fixture
def exa_on(monkeypatch):
    monkeypatch.setenv("EXA_FALLBACK", "1")
    monkeypatch.setenv("EXA_API_KEY", "k")
    exa.reset_run_budget()


def test_page_blocked_for_app_and_exa_is_read_from_another_site(monkeypatch, exa_on):
    """Reuters answered 401 to the app and to Exa; the same story from another site is used instead."""
    from services import source_fetch_service as sfs
    from services.source_fetch_service_direct import SourceFetchService as Direct

    monkeypatch.setattr(Direct, "fetch", lambda self, url, timeout_seconds=12.0: (_ for _ in ()).throw(
        sfs.SourceFetchNetworkError("Source returned HTTP 401")))
    queries, reads = [], []

    def search(q, **kw):
        queries.append(q)
        return [{"url": "https://www.reuters.com/other"}, {"url": "https://www.theverge.com/x"}]

    def fetch_text(url, **kw):
        reads.append(url)
        if "reuters" in url:
            raise exa.ExaContentsError("Exa contents: CRAWL_HTTP_401")
        return {"url": url, "text": ARTICLE, "title": "California sues OpenAI", "published_at": "2026-11-16"}
    monkeypatch.setattr(exa, "search", search)
    monkeypatch.setattr(exa, "fetch_text", fetch_text)
    source = sfs.SourceFetchService().fetch(REUTERS)
    assert queries == ["california sues openai over ai models cyberattacks hugging face other companies"]
    assert reads == [REUTERS, "https://www.theverge.com/x"]          # the same site is never used as the alternate
    assert source["metadata"]["retrieval_mode"] == "exa_alternate_coverage"
    assert source["metadata"]["alternate_coverage_for"] == REUTERS
    assert source["text_content"].startswith("California's attorney general")


def test_no_alternate_for_urls_without_headline_words(monkeypatch, exa_on):
    from services import source_fetch_service as sfs
    from services.source_fetch_service_direct import SourceFetchService as Direct
    monkeypatch.setattr(Direct, "fetch", lambda self, url, timeout_seconds=12.0: (_ for _ in ()).throw(
        sfs.SourceFetchNetworkError("Source returned HTTP 401")))
    monkeypatch.setattr(exa, "fetch_text", lambda url, **kw: (_ for _ in ()).throw(exa.ExaContentsError("x")))
    called = []
    monkeypatch.setattr(exa, "search", lambda q, **kw: called.append(q) or [])
    monkeypatch.setattr(sfs, "get_free_news_context", lambda url: None)
    with pytest.raises(sfs.SourceFetchError):
        sfs.SourceFetchService().fetch("https://apnews.com/article/62450bd442c04518860e0b312b9f13b4")
    assert called == []


def _blocked_everywhere_but(monkeypatch, open_host):
    """Direct fetch: 401 everywhere except `open_host`, which returns a real article."""
    from services import source_fetch_service as sfs
    from services.source_fetch_service_direct import SourceFetchService as Direct

    def fetch(self, url, timeout_seconds=12.0):
        if open_host and open_host in url:
            return {"text_content": ARTICLE, "final_url": url, "metadata": {"title": "California sues OpenAI"}}
        raise sfs.SourceFetchNetworkError("Source returned HTTP 401")
    monkeypatch.setattr(Direct, "fetch", fetch)
    monkeypatch.setattr(exa, "fetch_text", lambda url, **kw: (_ for _ in ()).throw(exa.ExaContentsError("401")))
    monkeypatch.setattr(sfs, "get_free_news_context", lambda url: None)
    return sfs


def test_google_is_the_last_resort_when_exa_finds_no_other_site(monkeypatch, exa_on):
    sfs = _blocked_everywhere_but(monkeypatch, "apnews.com")
    monkeypatch.setattr(exa, "search", lambda q, **kw: [{"url": "https://www.reuters.com/same-site"}])
    asked = []
    sfs.set_google_searcher(lambda q: asked.append(q) or [{"url": "https://apnews.com/article/california-openai"}])
    try:
        source = sfs.SourceFetchService().fetch(REUTERS)
    finally:
        sfs.set_google_searcher(None)
    assert asked == ["california sues openai over ai models cyberattacks hugging face other companies"]
    assert source["metadata"]["retrieval_mode"] == "google_alternate_coverage_direct"   # AP's own page
    assert source["metadata"]["alternate_found_by"] == "google"
    assert source["metadata"]["alternate_coverage_for"] == REUTERS


def test_google_not_used_when_exa_finds_another_site(monkeypatch, exa_on):
    sfs = _blocked_everywhere_but(monkeypatch, "theverge.com")
    monkeypatch.setattr(exa, "search", lambda q, **kw: [{"url": "https://www.theverge.com/x"}])
    asked = []
    sfs.set_google_searcher(lambda q: asked.append(q) or [])
    try:
        source = sfs.SourceFetchService().fetch(REUTERS)
    finally:
        sfs.set_google_searcher(None)
    assert asked == [] and source["metadata"]["retrieval_mode"] == "exa_alternate_coverage_direct"


def test_nothing_found_anywhere_still_fails_cleanly(monkeypatch, exa_on):
    sfs = _blocked_everywhere_but(monkeypatch, "")
    monkeypatch.setattr(exa, "search", lambda q, **kw: None)
    sfs.set_google_searcher(lambda q: [{"url": "https://apnews.com/article/x"}])
    try:
        with pytest.raises(sfs.SourceFetchError):
            sfs.SourceFetchService().fetch(REUTERS)
    finally:
        sfs.set_google_searcher(None)


def test_orchestrator_registers_a_capped_google_helper(monkeypatch):
    from services import source_fetch_service as sfs
    from services import grounded_search
    from services.research_orchestrator_service import ResearchOrchestratorService as S

    made = {}

    class FakeGrounded:
        def __init__(self, llm, max_calls=None):
            made["max_calls"] = max_calls
            self.available = True

        def search(self, query, max_results=4):
            return {"results": [{"url": "https://apnews.com/a"}]}
    monkeypatch.setattr(grounded_search, "GeminiGroundedSearch", FakeGrounded)
    obj = object.__new__(S)
    obj.extraction_agent = type("A", (), {"llm": object()})()
    try:
        obj._register_alternate_google_search()
        assert made["max_calls"] == 8 and sfs._GOOGLE_SEARCHER("q") == [{"url": "https://apnews.com/a"}]
        monkeypatch.setenv("EVENT_INTELLIGENCE_ALTERNATE_GOOGLE_SEARCHES", "0")
        obj._register_alternate_google_search()
        assert sfs._GOOGLE_SEARCHER is None
    finally:
        sfs.set_google_searcher(None)
