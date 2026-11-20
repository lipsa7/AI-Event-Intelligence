"""v13.3 (20 Nov 2026): Exa contents fallback for blocked pages. No network, no paid calls."""
import pytest

from services import exa_contents as exa

ARTICLE = ("The regulator fined the company 4 million euros on 3 March after it missed the reporting "
           "deadline set in the 2024 rule. The company said it would appeal and changed its filing process. ") * 3


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(exa, "USAGE_FILE", tmp_path / "exa_usage.json")   # never the real counter
    monkeypatch.delenv("EXA_MAX_CALLS_PER_MONTH", raising=False)
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    monkeypatch.setenv("EXA_MAX_CALLS_PER_RUN", "2")
    monkeypatch.delenv("EXA_FALLBACK", raising=False)
    exa.reset_run_budget()
    yield
    exa.reset_run_budget()


def ok_post(seen):
    def post(payload, key, timeout):
        seen.append((payload, key))
        return {"results": [{"url": payload["urls"][0], "text": ARTICLE, "title": "Fine",
                             "publishedDate": "2026-03-03"}],
                "statuses": [{"id": payload["urls"][0], "status": "success"}]}
    return post


def test_request_shape_follows_exa_contents_reference():
    seen = []
    page = exa.fetch_text("https://example.com/a", post=ok_post(seen))
    payload, key = seen[0]
    assert payload == {"urls": ["https://example.com/a"], "text": True}   # top-level text, one content field
    assert key == "test-key" and page["text"].startswith("The regulator") and page["published_at"] == "2026-03-03"


def test_cap_per_run_stops_paid_calls(monkeypatch):
    seen = []
    assert exa.fetch_text("https://example.com/1", post=ok_post(seen))
    assert exa.fetch_text("https://example.com/2", post=ok_post(seen))
    assert exa.fetch_text("https://example.com/3", post=ok_post(seen)) is None    # cap of 2 reached
    assert len(seen) == 2 and exa.usage()["refused"] == 1
    exa.reset_run_budget()
    assert exa.fetch_text("https://example.com/4", post=ok_post(seen))


def test_off_without_key_or_when_switched_off(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setenv("EXA_FALLBACK", "0")
    assert exa.fetch_text("https://example.com/a", post=ok_post(seen)) is None
    monkeypatch.delenv("EXA_FALLBACK")
    monkeypatch.delenv("EXA_API_KEY")
    monkeypatch.setattr(exa, "_key_from_dotenv", lambda: "")
    assert exa.fetch_text("https://example.com/a", post=ok_post(seen)) is None
    assert seen == []


def test_http_200_with_failed_status_is_not_used():
    def post(payload, key, timeout):
        return {"results": [], "statuses": [{"id": payload["urls"][0], "status": "error",
                                              "error": {"tag": "CRAWL_NOT_FOUND"}}]}
    with pytest.raises(exa.ExaContentsError, match="CRAWL_NOT_FOUND"):
        exa.fetch_text("https://example.com/a", post=post)


def test_blocked_page_is_read_through_exa(monkeypatch):
    from services import source_fetch_service as sfs
    from services.source_fetch_service_direct import SourceFetchService as Direct

    def blocked(self, url, timeout_seconds=12.0):
        raise sfs.SourceFetchNetworkError("HTTP 403")
    monkeypatch.setattr(Direct, "fetch", blocked)
    seen = []
    real = exa.fetch_text
    monkeypatch.setattr(exa, "fetch_text", lambda url, **kw: real(url, post=ok_post(seen)))
    source = sfs.SourceFetchService().fetch("https://news.example.com/story")
    assert source["text_content"].startswith("The regulator")
    assert source["metadata"]["retrieval_mode"] == "exa_contents_fallback"
    assert source["metadata"]["limited_context"] is False
    assert "HTTP 403" in source["metadata"]["publisher_fetch_error"]


def test_exa_failure_falls_through_to_gdelt_path(monkeypatch):
    from services import source_fetch_service as sfs
    from services.source_fetch_service_direct import SourceFetchService as Direct

    monkeypatch.setattr(Direct, "fetch", lambda self, url, timeout_seconds=12.0: (_ for _ in ()).throw(
        sfs.SourceFetchNetworkError("HTTP 429")))
    monkeypatch.setattr(exa, "fetch_text", lambda url, **kw: (_ for _ in ()).throw(exa.ExaContentsError("x")))
    monkeypatch.setattr(sfs, "get_free_news_context", lambda url: None)
    with pytest.raises(sfs.SourceFetchError, match="HTTP 429"):
        sfs.SourceFetchService().fetch("https://news.example.com/story")


def test_monthly_cap_holds_across_runs_and_restarts(monkeypatch):
    monkeypatch.setenv("EXA_MAX_CALLS_PER_MONTH", "3")
    seen = []
    for run in range(3):                      # three runs, cap 2 per run, 3 per month
        exa.reset_run_budget()
        for n in range(2):
            exa.fetch_text(f"https://example.com/{run}/{n}", post=ok_post(seen))
    assert len(seen) == 3 and exa.month_used() == 3
    import json
    assert list(json.loads(exa.USAGE_FILE.read_text()).values()) == [3]   # on disk: survives a restart


def test_failed_call_still_counts():
    def boom(payload, key, timeout):
        raise TimeoutError()
    with pytest.raises(exa.ExaContentsError):
        exa.fetch_text("https://example.com/a", post=boom)
    assert exa.month_used() == 1


@pytest.mark.parametrize("code", [401, 402, 403, 429])
def test_credits_used_up_or_rate_limit_turns_exa_off_for_the_run(code):
    import io
    import urllib.error
    seen = []

    def refused(payload, key, timeout):
        seen.append(1)
        raise urllib.error.HTTPError("https://api.exa.ai/contents", code, "x", {}, io.BytesIO(b""))
    with pytest.raises(exa.ExaContentsError):
        exa.fetch_text("https://example.com/a", post=refused)
    assert exa.fetch_text("https://example.com/b", post=refused) is None    # not called again
    assert len(seen) == 1


def search_post(seen):
    def post(payload, key, timeout):
        seen.append(payload)
        return {"results": [{"url": "https://reg.example.gov/fine", "title": "Fine notice",
                             "publishedDate": "2026-03-03", "highlights": ["The regulator fined the company."]}]}
    return post


def test_search_request_shape_follows_exa_skill(monkeypatch):
    monkeypatch.setenv("EXA_MAX_CALLS_PER_RUN", "300")
    seen = []
    out = exa.search("regulator fine reporting deadline", post=search_post(seen))
    assert set(seen[0]) == {"query", "type", "objective", "contents"}
    assert seen[0]["type"] == "auto" and seen[0]["contents"] == {"highlights": True}
    assert out[0]["provider"] == "exa_search" and out[0]["snippet"].startswith("The regulator")
    assert exa.usage()["used"] == exa.SEARCH_UNITS        # search costs more than a page read


def test_search_refused_when_it_would_pass_the_run_budget(monkeypatch):
    monkeypatch.setenv("EXA_MAX_CALLS_PER_RUN", "20")
    seen = []
    assert exa.search("q1", post=search_post(seen)) is not None     # 17 of 20
    assert exa.search("q2", post=search_post(seen)) is None         # 34 > 20: not made
    assert len(seen) == 1


def test_combined_search_uses_exa_only_when_free_providers_fail(monkeypatch):
    from services.grounded_search import CombinedSearch
    monkeypatch.setenv("EXA_MAX_CALLS_PER_RUN", "300")
    seen = []
    real = exa.search
    monkeypatch.setattr(exa, "search", lambda q, **kw: real(q, post=search_post(seen)))

    class RateLimited:
        def search(self, query, **kw):
            raise RuntimeError("WebSearchRateLimitError")

    class Works:
        def search(self, query, **kw):
            return {"results": [{"url": "https://news.example.com/a", "title": "A"}], "provider_stats": {}}

    out = CombinedSearch(RateLimited(), None).search("regulator fine")
    assert [r["provider"] for r in out["results"]] == ["exa_search"]
    assert out["provider_stats"]["exa_search"]["status"] == "ok"
    out = CombinedSearch(Works(), None).search("regulator fine")
    assert len(seen) == 1 and "exa_search" not in out["provider_stats"]     # not called: free results exist


@pytest.mark.parametrize("title,final_url", [
    ("Federal Register :: Request Access", "https://unblock.federalregister.gov/"),
    ("Client Challenge", "https://www.nature.com/articles/x"),
    ("A quick browser check, then you're on your way.", "https://www.psychologytoday.com/x"),
])
def test_bot_wall_with_a_long_page_is_not_an_article(monkeypatch, title, final_url):
    """v13.12 (RUN-FF8F59D2, 20 Nov): three bot walls were saved as articles; their titles give them away
    even when the page is long. They now go to the Exa fallback like any blocked page."""
    from services import source_fetch_service as sfs
    from services.source_fetch_service_direct import SourceFetchService as Direct

    def wall(self, url, timeout_seconds=12.0):
        return {"text_content": "Some footer text about the site and its policies. " * 60,
                "final_url": final_url, "metadata": {"title": title}}
    monkeypatch.setattr(Direct, "fetch", wall)
    seen = []
    real = exa.fetch_text
    monkeypatch.setattr(exa, "fetch_text", lambda url, **kw: real(url, post=ok_post(seen)))
    source = sfs.SourceFetchService().fetch("https://news.example.com/story")
    assert source["metadata"]["retrieval_mode"] == "exa_contents_fallback" and len(seen) == 1
