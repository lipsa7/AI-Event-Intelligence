"""
Free discovery providers (GDELT, RSS, Exa) and the multi-source merge.

No network: every provider gets a fake transport or fetcher.

Run:  python -m tests.test_free_news_providers
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from services import free_news_providers as fnp
from services import web_search_service as wss

NOW = datetime.now(timezone.utc)
QUERY = '(artificial intelligence) AND ("HDFC Bank" OR underwriting) AND (launched OR deployed)'


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        status, body = self.responses.pop(0)
        return status, body if isinstance(body, bytes) else body.encode(), {}


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 2))
        self.now += seconds


def gdelt(responses, clock=None):
    clock = clock or FakeClock()
    fnp.GDELTDocProvider._last_request_at = 0.0
    fnp.GDELTDocProvider._cooldown_until = 0.0
    transport = FakeTransport(responses)
    provider = fnp.GDELTDocProvider(transport=transport, sleep=clock.sleep, clock=clock, retries=2)
    return provider, transport, clock


GDELT_JSON = json.dumps({"articles": [
    {"url": "https://news.example/hdfc", "title": "HDFC Bank deployed AI underwriting",
     "seendate": NOW.strftime("%Y%m%dT%H%M%SZ"), "domain": "news.example", "language": "English", "sourcecountry": "India"},
    {"url": "https://news.example/hdfc", "title": "duplicate", "seendate": ""},
]})


# ----------------------------------------------------------------------
# Query translation and matching
# ----------------------------------------------------------------------

def test_gdelt_query_translation():
    assert fnp.to_gdelt_query(QUERY) == '"artificial intelligence" ("hdfc bank" OR underwriting) (launched OR deployed)'
    # Short keywords are rejected by GDELT: bare "AI" becomes AI phrases; topic words are each required.
    assert fnp.to_gdelt_query("(AI in retail) AND (launch OR deployment)") == (
        '("artificial intelligence" OR "generative AI" OR "AI model") retail (launch OR deployment)'
    )


def test_gdelt_keeps_short_topic_words_with_a_neighbour_and_never_drops_a_group():
    # "EV" alone is too short for GDELT; it is searched as the phrase "ev batteries".
    assert fnp.to_gdelt_query("(EV batteries) AND (launch OR deal)") == '"ev batteries" (launch OR deal)'
    assert fnp.to_gdelt_query("(chips in EU) AND (launch OR deal)") == 'chips "chips eu" (launch OR deal)' or \
        "eu" in fnp.to_gdelt_query("(chips in EU) AND (launch OR deal)")
    # A required group GDELT cannot search at all is an error, not a silently broader query.
    for query in ("(AI) AND (EU OR UK)", "(AI) AND (EU)"):
        try:
            fnp.to_gdelt_query(query)
        except fnp.FreeNewsProviderError:
            continue
        raise AssertionError(f"expected an error for {query}")


def test_query_matching_accepts_plurals_and_inflections():
    assert fnp.query_matches("EV batteries", "Startup unveils a cheaper EV battery")
    assert fnp.query_matches("EV battery", "Two new EV batteries announced")
    assert fnp.query_matches("(AI) AND (lenders)", "Bank uses AI for lending decisions")
    assert fnp.query_matches("data centers", "A new data center opens in Pune")
    assert not fnp.query_matches("EV batteries", "EV makers cut prices")       # 'battery' absent


def test_query_matching_requires_every_group():
    assert fnp.query_matches(QUERY, "HDFC Bank deployed generative AI for loan underwriting")
    assert not fnp.query_matches(QUERY, "HDFC Bank deployed new branches in Mumbai")   # no AI
    assert not fnp.query_matches(QUERY, "OpenAI deployed a new model")                  # no bank/underwriting
    assert fnp.query_matches("AI in retail", "Walmart rolls out AI tools for retailers")
    assert fnp.query_matches("data center power", "Utilities race to supply power for new data centers")


# ----------------------------------------------------------------------
# GDELT
# ----------------------------------------------------------------------

def test_gdelt_parses_results_and_marks_seen_dates():
    provider, transport, _ = gdelt([(200, GDELT_JSON)])
    result = provider.search(QUERY, limit=10)
    assert [row["url"] for row in result["results"]] == ["https://news.example/hdfc"]
    row = result["results"][0]
    assert row["provider"] == "gdelt" and row["published_at_basis"] == "gdelt_seen_date"
    url = transport.requests[0].full_url
    assert "mode=artlist" in url and "format=json" in url and "sourcelang%3Aenglish" in url


def test_gdelt_spaces_requests_and_retries_once_within_the_wait_cap():
    provider, transport, clock = gdelt([(200, GDELT_JSON), (429, ""), (200, GDELT_JSON)])
    provider.search(QUERY)
    provider.search(QUERY)  # second call: waits for the interval, one refusal, one 10 s retry, success
    assert clock.slept[0] >= 6.0 - 0.01          # spacing between requests
    assert 10.0 in clock.slept and 20.0 not in clock.slept   # total back-off capped (12 s default)
    assert len(transport.requests) == 3


def test_gdelt_gives_up_after_retries():
    provider, _, _ = gdelt([(429, ""), (429, ""), (429, "")])
    try:
        provider.search(QUERY)
    except fnp.FreeNewsRateLimitError:
        return
    raise AssertionError("expected FreeNewsRateLimitError")


def test_gdelt_plain_text_error_with_http_200_is_an_error_not_empty_results():
    provider, _, _ = gdelt([(200, "The specified phrase is too short.")])
    try:
        provider.search(QUERY)
    except fnp.FreeNewsProviderError as error:
        assert "too short" in str(error)
        return
    raise AssertionError("expected FreeNewsProviderError")


def test_gdelt_headline_saying_too_many_is_a_result_not_a_rate_limit():
    body = json.dumps({"articles": [
        {"url": "https://news.example/pilots", "title": "Too many requests for AI pilots, say HDFC Bank underwriting teams after launch",
         "seendate": NOW.strftime("%Y%m%dT%H%M%SZ"), "domain": "news.example"},
    ]})
    provider, transport, _ = gdelt([(200, body)])
    result = provider.search(QUERY)
    assert len(result["results"]) == 1 and len(transport.requests) == 1


def test_gdelt_cooldown_after_repeated_refusals_fails_fast_then_recovers():
    provider, transport, clock = gdelt([(429, ""), (429, ""), (429, ""), (200, GDELT_JSON)])
    try:
        provider.search(QUERY)
    except fnp.FreeNewsRateLimitError:
        pass
    else:
        raise AssertionError("expected FreeNewsRateLimitError")
    # One refusal, one 10 s retry, then the cooldown (was 3 attempts and ~30 s of back-off).
    assert provider.last_http_attempts == 2 and 10 <= provider.last_wait_seconds < 20
    # During the cooldown: no HTTP request, no waiting, an error the orchestrator treats as a rate limit.
    slept = len(clock.slept)
    try:
        provider.search(QUERY)
    except fnp.FreeNewsRateLimitError as error:
        assert "rate limit" in str(error)
    else:
        raise AssertionError("expected fast failure during cooldown")
    assert len(transport.requests) == 2 and len(clock.slept) == slept
    clock.now += provider.cooldown_seconds + 1
    assert len(provider.search(QUERY)["results"]) == 1


def test_gdelt_empty_body_means_no_results():
    provider, _, _ = gdelt([(200, "")])
    assert provider.search(QUERY)["results"] == []


# ----------------------------------------------------------------------
# RSS
# ----------------------------------------------------------------------

def rss_items():
    recent = (NOW - timedelta(days=1)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    old = (NOW - timedelta(days=90)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    return [
        {"url": "https://feed.example/a", "title": "HDFC Bank deployed AI underwriting", "summary": "", "published_at": recent},
        {"url": "https://feed.example/b", "title": "HDFC Bank opens branches", "summary": "", "published_at": recent},
        {"url": "https://feed.example/c", "title": "Axis Bank launched AI underwriting", "summary": "", "published_at": old},
    ]


def test_rss_matches_query_and_date_window():
    calls = []
    provider = fnp.RSSFeedProvider(feeds=["https://feed.example/rss"], fetcher=lambda url, t: calls.append(url) or rss_items())
    result = provider.search(QUERY)
    assert [row["url"] for row in result["results"]] == ["https://feed.example/a"]
    provider.search(QUERY)
    assert len(calls) == 1  # cached


def test_rss_pauses_a_failing_feed_then_tries_it_again():
    calls = []
    clock = FakeClock()
    healthy = {"on": False}

    def flaky(url, timeout):
        calls.append(url)
        if not healthy["on"]:
            raise OSError("timed out")
        return rss_items()

    provider = fnp.RSSFeedProvider(feeds=["https://feed.example/rss"], fetcher=flaky, clock=clock)
    errors = []
    for _ in range(5):
        try:
            provider.search(QUERY)
        except fnp.FreeNewsProviderError as error:   # every feed failed: an error, not "no news"
            errors.append(error)
    assert len(errors) == 5
    assert len(calls) == provider.max_failures             # paused after max_failures
    assert isinstance(errors[-1], fnp.FreeNewsRateLimitError)   # all paused: reported like a rate limit
    assert "timed out" in provider.feed_status["https://feed.example/rss"]["last_error"]
    healthy["on"] = True
    clock.now += provider.feed_cooldown_seconds + 1          # a blip must not switch the feed off for good
    assert [row["url"] for row in provider.search(QUERY)["results"]] == ["https://feed.example/a"]


def test_rss_one_failing_feed_does_not_hide_the_others():
    def fetch(url, timeout):
        if "broken" in url:
            raise OSError("down")
        return rss_items()

    provider = fnp.RSSFeedProvider(feeds=["https://broken.example/rss", "https://feed.example/rss"], fetcher=fetch)
    assert [row["url"] for row in provider.search(QUERY)["results"]] == ["https://feed.example/a"]


def test_rss_date_window_uses_utc_and_whole_end_day():
    yesterday = NOW - timedelta(days=1)
    items = [{"url": "https://feed.example/late", "title": "HDFC Bank deployed AI underwriting",
              "published_at": yesterday.replace(hour=23, minute=30).strftime("%a, %d %b %Y %H:%M:%S +0000")},
             {"url": "https://feed.example/ist", "title": "HDFC Bank launched AI underwriting tool",
              "published_at": yesterday.replace(hour=1).strftime("%a, %d %b %Y %H:%M:%S +0530")}]
    provider = fnp.RSSFeedProvider(feeds=["https://feed.example/rss"], fetcher=lambda u, t: items)
    day = yesterday.strftime("%Y-%m-%d")
    # Explicit dates are strict (v6.10.1): 01:00 +05:30 is 19:30 UTC the day before, outside the window.
    urls = {row["url"] for row in provider.search(QUERY, from_date=day, to_date=day)["results"]}
    assert urls == {"https://feed.example/late"}
    start, end = fnp._window({"from_date": day, "to_date": day}, 7)
    assert end.tzinfo is not None and (end - start).total_seconds() == 86399


def test_rss_without_feeds_is_a_configuration_error():
    saved = os.environ.pop("NEWS_RSS_FEEDS", None)
    os.environ["NEWS_RSS_FEED_FILE"] = "/nonexistent/feeds.txt"
    try:
        fnp.RSSFeedProvider(feeds=[])
    except fnp.FreeNewsConfigurationError:
        return
    finally:
        os.environ.pop("NEWS_RSS_FEED_FILE", None)
        if saved is not None:
            os.environ["NEWS_RSS_FEEDS"] = saved
    raise AssertionError("expected FreeNewsConfigurationError")


def test_simple_feed_parser_reads_rss_and_atom():
    rss = b"""<rss><channel><item><title>A</title><link>https://x.example/a</link>
              <pubDate>Sat, 03 Oct 2026 10:00:00 +0000</pubDate></item></channel></rss>"""
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>B</title>
               <link href="https://x.example/b"/><updated>2026-10-03T10:00:00Z</updated></entry></feed>"""
    assert fnp._simple_parse_feed(rss)[0]["url"] == "https://x.example/a"
    assert fnp._simple_parse_feed(atom)[0]["url"] == "https://x.example/b"


# ----------------------------------------------------------------------
# Exa
# ----------------------------------------------------------------------

def test_exa_request_and_results():
    body = json.dumps({"results": [{"url": "https://exa.example/x", "title": "Bank adopts AI",
                                    "publishedDate": "2026-09-30T00:00:00.000Z", "id": "1"}],
                       "costDollars": {"total": 0.007}})
    transport = FakeTransport([(200, body)])
    provider = fnp.ExaProvider(api_key="test", transport=transport)
    result = provider.search(QUERY, limit=5)
    sent = json.loads(transport.requests[0].data)
    assert sent["category"] == "news" and "contents" not in sent and sent["numResults"] == 5
    assert "startPublishedDate" in sent and "AND" not in sent["query"]
    assert transport.requests[0].get_header("X-api-key") == "test"
    assert result["results"][0]["provider"] == "exa" and result["cost_estimate_usd"] == 0.007


def test_exa_credit_or_key_problems_are_configuration_errors_and_cap_is_enforced():
    provider = fnp.ExaProvider(api_key="test", transport=FakeTransport([(402, "{}")]), max_requests=1)
    try:
        provider.search(QUERY)
    except fnp.FreeNewsConfigurationError:
        pass
    else:
        raise AssertionError("expected FreeNewsConfigurationError")
    try:
        provider.search(QUERY)
    except fnp.FreeNewsRateLimitError:
        return
    raise AssertionError("expected the per-session cap")


# ----------------------------------------------------------------------
# Multi-source merge
# ----------------------------------------------------------------------

class Stub:
    def __init__(self, name, urls=None, error=None):
        self.provider_name = name
        self.urls = urls or []
        self.error = error
        self.calls = 0

    def search(self, query, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return {"results": [{"url": u, "title": f"title {u}"} for u in self.urls]}


def test_merge_interleaves_dedupes_and_records_providers():
    service = wss.MultiSourceSearchService([
        Stub("gdelt", ["https://a.example/1", "https://a.example/2", "https://shared.example/x"]),
        Stub("rss", ["https://shared.example/x", "https://b.example/1"]),
    ])
    result = service.search(QUERY, limit=10)
    urls = [row["url"] for row in result["results"]]
    assert urls[:2] == ["https://a.example/1", "https://shared.example/x"]       # round-robin
    assert len(urls) == len(set(urls)) == 4
    shared = next(row for row in result["results"] if row["url"] == "https://shared.example/x")
    assert shared["provider"] == "rss" and shared["also_found_by"] == ["gdelt"]
    assert result["provider_stats"]["gdelt"]["status"] == "ok"


def test_one_failing_provider_does_not_stop_the_others_and_quota_switches_it_off():
    newsapi = Stub("newsapi", error=wss.NewsAPIQuotaError("quota"))
    service = wss.MultiSourceSearchService([newsapi, Stub("gdelt", ["https://a.example/1"])])
    first = service.search(QUERY)
    assert [row["url"] for row in first["results"]] == ["https://a.example/1"]
    assert first["provider_stats"]["newsapi"]["error"] == "NewsAPIQuotaError"
    service.search(QUERY)
    assert newsapi.calls == 1   # switched off after the quota error


def _terminal(error):
    from services.research_orchestrator_service import ResearchOrchestratorService as R
    return R._fresh_discovery_terminal_error(error)


def test_all_providers_exhausted_opens_the_orchestrator_circuit():
    service = wss.MultiSourceSearchService([
        Stub("newsapi", error=wss.NewsAPIQuotaError("quota")),
        Stub("gdelt", error=fnp.FreeNewsRateLimitError("cooldown")),
        Stub("rss", error=fnp.FreeNewsRateLimitError("all feeds paused")),
    ])
    for _ in range(2):   # second call: newsapi is disabled, the others still exhausted
        try:
            service.search(QUERY)
        except wss.WebSearchRateLimitError as error:
            assert _terminal(error), str(error)
        else:
            raise AssertionError("expected WebSearchRateLimitError")


def test_a_transient_failure_keeps_discovery_retryable():
    service = wss.MultiSourceSearchService([
        Stub("gdelt", error=fnp.FreeNewsRateLimitError("GDELT rate limit (HTTP 429)")),
        Stub("rss", error=fnp.FreeNewsProviderError("all RSS feeds failed or are paused: x: OSError")),
    ])
    try:
        service.search(QUERY)
    except wss.WebSearchRateLimitError:
        raise AssertionError("one provider failed for an ordinary reason: must stay retryable")
    except wss.WebSearchProviderError as error:
        assert not _terminal(error), str(error)   # the provider's "429" text must not leak into the message
        assert "gdelt=unavailable" in str(error) and "rss=failed" in str(error)
    else:
        raise AssertionError("expected WebSearchProviderError")


def test_default_service_without_paid_keys_uses_free_providers():
    saved = {k: os.environ.pop(k, None) for k in ("NEWSAPI_API_KEY", "NEWS_API_KEY", "EXA_API_KEY", "NEWS_DISCOVERY_PROVIDERS")}
    os.environ["NEWS_RSS_FEEDS"] = "https://feed.example/rss"
    original = (wss._read_key_from_dotenv, wss._read_key_from_streamlit_secrets)
    wss._read_key_from_dotenv = lambda: ""
    wss._read_key_from_streamlit_secrets = lambda: ""
    try:
        service = wss.build_default_web_search_service()
        assert [p.provider_name for p in service.providers] == ["gdelt", "rss"]
        assert set(service.skipped_providers) == {"newsapi", "exa"}
    finally:
        wss._read_key_from_dotenv, wss._read_key_from_streamlit_secrets = original
        os.environ.pop("NEWS_RSS_FEEDS", None)
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


# ----------------------------------------------------------------------
# Tools
# ----------------------------------------------------------------------

def test_feed_checker_can_re_enable_a_demoted_feed():
    from tools.check_news_feeds import feed_urls
    lines = [
        "https://a.example/rss  # note",
        "#candidate https://b.example/feed  # failed 2026-10-04: no items",
        "#candidate https://c.example/rss",
        "# a comment",
        "#candidate (none yet)",
    ]
    assert feed_urls(lines) == [(0, "https://a.example/rss", True),
                                (1, "https://b.example/feed", False),
                                (2, "https://c.example/rss", False)]


def test_trial_scores_an_excel_saved_sheet_and_hides_date_style():
    import io
    import tempfile
    from contextlib import redirect_stdout
    from pathlib import Path
    from tools import compare_news_providers as trial

    assert trial._review_date("20261003T101500Z") == "2026-10-03"
    assert trial._review_date("2026-10-03T10:15:00+05:30") == "2026-10-03"
    assert trial._review_date("") == "unknown"
    with tempfile.TemporaryDirectory() as folder:
        sheet = Path(folder) / "review_sheet.csv"
        (Path(folder) / "review_key.json").write_text('{"R001": "gdelt", "R002": "rss"}', encoding="utf-8")
        sheet.write_text("review_id,title,domain,date,url,relevant_to_topic (y/n),specific_event (y/n),notes\n"
                         "R001,t,d,2026-10-03,u,y,n,\nR002,t,d,unknown,u,n,y,\n", encoding="utf-8-sig")
        output = io.StringIO()
        with redirect_stdout(output):
            trial.score_review(sheet)
    assert "gdelt | 1/1 | 0/1" in output.getvalue() and "rss | 0/1 | 1/1" in output.getvalue()


def test_trial_survives_embedding_failure():
    from tools import compare_news_providers as trial
    import services.vector_service as vs
    original = vs.VectorService

    class Broken:
        last_error = ""
        def available(self):
            return True
        def similarities(self, *a, **k):
            raise ConnectionError("ollama stopped")

    vs.VectorService = Broken
    try:
        result = trial.pipeline_relevance_and_breadth("ai", [{"title": "x", "url": "u"}])
    finally:
        vs.VectorService = original
    assert result["status"].startswith("skipped: ConnectionError")


def test_trial_reports_gdelt_pacing_apart_from_latency():
    from tools import compare_news_providers as trial
    provider, _, clock = gdelt([(429, ""), (200, GDELT_JSON)])
    run = trial.run_provider(provider, [QUERY], per_query=5,
                             start=NOW - timedelta(days=3), end=NOW)
    call = run["calls"][0]
    assert call["status"] == "ok" and call["http_attempts"] == 2 and call["pacing_wait_ms"] >= 10000


# ----------------------------------------------------------------------
# Plain research phrases, GDELT syntax, source coverage (v6.12)
# ----------------------------------------------------------------------

RESEARCH_QUERY = "third party model auditing intellectual property leakage proprietary risk"


def test_plain_research_phrase_matches_relevant_headlines_not_every_word():
    relevant = "Third-party AI model audits raise intellectual property leakage risks for model developers"
    assert fnp.plain_query_terms(RESEARCH_QUERY) is not None
    assert fnp.query_matches(RESEARCH_QUERY, relevant)
    # A strong phrase alone is enough ("intellectual property" + one more word).
    assert fnp.query_matches(RESEARCH_QUERY, "Startups worry about intellectual property in AI deals")
    assert not fnp.query_matches(RESEARCH_QUERY, "Model railway club opens new property downtown")
    assert not fnp.query_matches(RESEARCH_QUERY, "Stock markets close higher on Friday")
    # Short topics and anchored queries keep the every-word / every-group rule.
    assert fnp.plain_query_terms("AI in retail") is None and fnp.plain_query_terms(QUERY) is None
    assert not fnp.query_matches("data center power", "New data center opens")


def test_gdelt_plain_phrase_is_an_or_search_ranked_locally():
    assert fnp.to_gdelt_query("AI model safety compliance post-training guardrails") == (
        '("artificial intelligence" OR "generative AI" OR model OR safety OR compliance '
        'OR "post training" OR guardrails)'
    )
    articles = json.dumps({"articles": [
        {"url": "https://g.example/weak", "title": "Model trains run late", "seendate": NOW.strftime("%Y%m%dT%H%M%SZ")},
        {"url": "https://g.example/best", "title": "Third-party model auditing raises intellectual property leakage risk",
         "seendate": NOW.strftime("%Y%m%dT%H%M%SZ")},
        {"url": "https://g.example/ok", "title": "Auditing AI models: intellectual property concerns grow",
         "seendate": NOW.strftime("%Y%m%dT%H%M%SZ")},
    ]})
    provider, transport, _ = gdelt([(200, articles)])
    result = provider.search(RESEARCH_QUERY, max_results=2)
    assert [row["url"] for row in result["results"]] == ["https://g.example/best", "https://g.example/ok"]
    assert "maxrecords=50" in transport.requests[0].full_url      # fetch more, keep the best


def test_gdelt_never_sends_punctuation_it_rejects():
    query = fnp.to_gdelt_query('(AI) AND ("Rolls-Royce" OR post-training) AND (deal)')
    assert '"rolls royce"' in query and '"post training"' in query and "-" not in query


def test_gdelt_research_calls_do_not_retry():
    provider, transport, clock = gdelt([(429, ""), (200, GDELT_JSON)])
    try:
        provider.search(QUERY, max_retries=0)
    except fnp.FreeNewsRateLimitError:
        pass
    else:
        raise AssertionError("expected an immediate rate-limit error")
    assert len(transport.requests) == 1 and 10.0 not in clock.slept


class Dated:
    """Source double that records the dates it was asked for."""

    def __init__(self, name, earliest=None, rows=None, error=None):
        self.provider_name = name
        self._earliest = earliest
        self.rows = rows if rows is not None else [{"url": f"https://{name}.example/a", "title": "t"}]
        self.error = error
        self.calls = []
        if earliest is not None:
            self.earliest_available = lambda: self._earliest

    def search(self, query, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"results": self.rows}


def test_old_dates_skip_sources_that_cannot_cover_them_without_counting_a_failure():
    now = datetime.now(timezone.utc)
    newsapi = Dated("newsapi", earliest=now - timedelta(days=30))
    gdelt_source = Dated("gdelt", earliest=datetime(2017, 1, 1, tzinfo=timezone.utc))
    exa = Dated("exa")
    service = wss.MultiSourceSearchService([newsapi, gdelt_source, exa])
    result = service.search(QUERY, from_date="2016-01-01", to_date=(now - timedelta(days=60)).isoformat())
    stats = result["provider_stats"]
    assert newsapi.calls == []                                     # no request spent
    assert stats["newsapi"]["outcome"] == "OUT_OF_COVERAGE" and stats["newsapi"]["status"] == "out_of_coverage"
    assert gdelt_source.calls[0]["from_date"].startswith("2017-01-01")   # clamped to what GDELT holds
    assert stats["gdelt"]["coverage"] == "partial" and stats["gdelt"]["outcome"] == "SUCCESS_WITH_RESULTS"
    assert stats["exa"]["status"] == "ok"


def test_each_source_outcome_is_named_not_collapsed():
    service = wss.MultiSourceSearchService([
        Dated("rss", rows=[]),
        Dated("gdelt", error=fnp.FreeNewsRateLimitError("GDELT rate limit")),
        Dated("exa", error=TimeoutError("timed out")),
        Dated("other", error=fnp.FreeNewsProviderError("GDELT error: bad query")),
    ])
    stats = service.search(QUERY)["provider_stats"]
    assert {name: row["outcome"] for name, row in stats.items()} == {
        "rss": "SUCCESS_NO_RESULTS", "gdelt": "RATE_LIMITED", "exa": "TIMEOUT", "other": "SOURCE_ERROR",
    }


def test_rss_window_older_than_its_items_is_out_of_coverage():
    items = [{"url": "https://feed.example/a", "title": "HDFC Bank deployed AI underwriting",
              "published_at": (NOW - timedelta(days=3)).strftime("%a, %d %b %Y %H:%M:%S +0000")}]
    rss = fnp.RSSFeedProvider(feeds=["https://feed.example/rss"], fetcher=lambda u, t: items)
    service = wss.MultiSourceSearchService([rss, Dated("exa")])
    result = service.search(QUERY, from_date="2019-01-01", to_date="2019-12-31")
    assert result["provider_stats"]["rss"]["outcome"] == "OUT_OF_COVERAGE"
    assert result["provider_stats"]["exa"]["status"] == "ok"


def test_no_source_covering_the_dates_is_an_ordinary_error_not_a_rate_limit():
    now = datetime.now(timezone.utc)
    service = wss.MultiSourceSearchService([Dated("newsapi", earliest=now - timedelta(days=30))])
    try:
        service.search(QUERY, from_date="2018-01-01", to_date="2018-12-31")
    except wss.WebSearchRateLimitError:
        raise AssertionError("must not open the discovery circuit")
    except wss.WebSearchProviderError as error:
        assert "no configured source covers the requested dates" in str(error)
        assert not _terminal(error)
    else:
        raise AssertionError("expected WebSearchProviderError")


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
