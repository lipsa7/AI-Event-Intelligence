"""Web search through Gemini's Google Search grounding (v6.18).

Why: the free news providers cannot reach the past (NewsAPI's free plan covers
about one month, RSS only current items, GDELT article lists only recent
months and often rate-limits), and none of them look for regulations,
incentives or dependencies on demand. Gemini's "Grounding with Google Search"
tool searches the open web and returns the pages it used
(groundingMetadata.groundingChunks[].web.uri/title).

Only the returned LINKS are used. Every page is then fetched by the existing
SourceFetchService and reviewed by HypothesisEvidenceService._process_source,
which requires an exact quote from the fetched text, a publication date, and
(for a past case) a dated passage and a quoted outcome. Nothing the search
model writes is used as evidence.

Grounded PROMPTS are capped per run (GROUNDED_SEARCH_MAX_CALLS, default 30 since
v6.24: with 6 gap searches per development plus follow-ups, the old cap of 12
was spent before the third development, which then got no searches at all, and
the expert-commentary search for the second never ran - replay
RUN-FB8AB22DBDD548B5BB6D4898).
One grounded prompt may run more than one Google query, and each query can be
billed; the queries Google actually ran (webSearchQueries) are counted and
logged.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
from typing import Any, Mapping

logger = logging.getLogger("event_intelligence." + __name__)

VERSION = "grounded-search-v1.5"

# Models Google lists as supporting the google_search tool (Gemini API docs,
# "Grounding with Google Search", checked 2026-10-09). The extraction model
# may be one that is not listed (e.g. gemini-3.1-flash-lite), so search uses
# GEMINI_SEARCH_MODEL, else this default.
SEARCH_CAPABLE_MODELS = frozenset({
    "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite",
    "gemini-3.1-pro-preview", "gemini-3-flash-preview", "gemini-2.5-pro", "gemini-2.5-flash",
    "gemini-2.5-flash-lite", "gemini-2.0-flash",
})
DEFAULT_SEARCH_MODEL = "gemini-3.5-flash-lite"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.getenv(name, default))))
    except (TypeError, ValueError):
        return default


# v6.24: pages that are not readable articles (video, social posts). In replay
# RUN-FB8AB22DBDD548B5BB6D4898 three of ten gap-research slots for one
# development went to YouTube videos.
NON_ARTICLE_HOSTS = ("youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com", "x.com",
                     "twitter.com", "reddit.com", "pinterest.com", "vimeo.com", "spotify.com", "podcasts.apple.com")


NON_ARTICLE_NAMES = frozenset({"youtube", "youtu.be", "tiktok", "instagram", "facebook", "x", "twitter", "reddit",
                               "pinterest", "vimeo", "spotify", "apple podcasts"})


def is_article_source(result: Mapping[str, Any]) -> bool:
    """False for video/social pages. Grounded results carry the site in `title`
    (the URL is a Google redirect), so both are checked."""
    from urllib.parse import urlparse
    host = (urlparse(_clean(result.get("url"))).hostname or "").casefold()
    title = _clean(result.get("title")).casefold()
    for blocked in NON_ARTICLE_HOSTS:
        if host == blocked or host.endswith("." + blocked) or title == blocked or title.endswith("." + blocked):
            return False
    # v6.25 (GPT review): grounding titles can be the site NAME ("YouTube", "Reddit").
    return title not in NON_ARTICLE_NAMES


class GeminiGroundedSearch:
    """search(query, from_date=, to_date=, max_results=) -> {"results": [...], "provider_stats": {...}}"""

    def __init__(self, llm: Any, *, max_calls: int | None = None, model_name: str = "") -> None:
        self.llm = llm
        self.client = getattr(llm, "_client", None)
        self.types = getattr(llm, "_types", None)
        # v6.19: one fixed, cheap, search-capable model (or GEMINI_SEARCH_MODEL),
        # so check_setup.py tests exactly the model the run will use.
        self.model_name = _clean(model_name or os.getenv("GEMINI_SEARCH_MODEL")) or DEFAULT_SEARCH_MODEL
        self.max_calls = max_calls if max_calls is not None else _env_int("GROUNDED_SEARCH_MAX_CALLS", 30, 0, 100)
        self.exhausted_queries: list[str] = []
        self.calls = 0
        self.failures: list[str] = []
        self.web_search_queries: list[str] = []

    @property
    def available(self) -> bool:
        return bool(
            self.client is not None and self.types is not None and self.model_name
            and hasattr(self.types, "Tool") and hasattr(self.types, "GoogleSearch")
            and self.calls < self.max_calls
        )

    def _prompt(self, query: str, from_date: str, to_date: str) -> str:
        window = ""
        if from_date or to_date:
            window = (f" Prefer sources published between {from_date[:10] or 'any time'} and "
                      f"{to_date[:10] or 'now'}.")
        return (
            "Search the web for news articles, official documents or regulator pages about: "
            f"{query}.{window} List the most relevant sources with one line each on what they report. "
            "Do not answer from memory."
        )

    DEFAULT_TIMEOUT_SECONDS = 30.0

    def search(self, query: str, *, max_results: int = 4, from_date: str = "", to_date: str = "",
               timeout_seconds: float | None = None, **_: Any) -> dict[str, Any]:
        query = _clean(query)
        if not query or not self.available:
            status = "disabled" if self.calls >= self.max_calls else "unavailable"
            if status == "disabled" and query:
                # v6.24: say so loudly - a spent budget looks like "no evidence exists".
                self.exhausted_queries.append(query)
                if len(self.exhausted_queries) == 1:
                    logger.warning("grounded_search_budget_exhausted calls=%s max_calls=%s first_unsearched=%r",
                                   self.calls, self.max_calls, query[:120])
            return {"results": [], "provider_stats": {"gemini_search": {"status": status, "outcome": status.upper()}}}
        self.calls += 1
        try:
            timeout = float(timeout_seconds) if timeout_seconds else self.DEFAULT_TIMEOUT_SECONDS
        except (TypeError, ValueError):
            timeout = self.DEFAULT_TIMEOUT_SECONDS
        timeout = max(1.0, timeout) if timeout_seconds is None else max(0.01, timeout)
        try:
            config_kwargs: dict[str, Any] = {
                "tools": [self.types.Tool(google_search=self.types.GoogleSearch())],
                "temperature": 0.0,
            }
            http_options = getattr(self.types, "HttpOptions", None)
            if http_options is not None:
                # google-genai request timeout is in milliseconds.
                config_kwargs["http_options"] = http_options(timeout=max(1, int(timeout * 1000)))
            config = self.types.GenerateContentConfig(**config_kwargs)
            # Wall-clock bound as well, in case the transport ignores the
            # request timeout: the run never waits longer than `timeout`.
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = pool.submit(self.client.models.generate_content, model=self.model_name,
                                 contents=self._prompt(query, from_date, to_date), config=config)
            try:
                response = future.result(timeout=timeout)
            finally:
                pool.shutdown(wait=False)
        except concurrent.futures.TimeoutError:
            self.failures.append("TimeoutError")
            logger.warning("Grounded search timed out after %.1fs", timeout)
            return {"results": [], "provider_stats": {"gemini_search": {
                "status": "failed", "outcome": "TIMEOUT", "error": "TimeoutError"}}}
        except Exception as error:
            self.failures.append(type(error).__name__)
            logger.warning("Grounded search failed: %s", type(error).__name__)
            return {"results": [], "provider_stats": {"gemini_search": {
                "status": "failed", "outcome": "FAILED", "error": type(error).__name__}}}
        results = [r for r in self.links(response) if is_article_source(r)][: max(1, int(max_results or 4))]
        executed = self.executed_queries(response)
        self.web_search_queries.extend(executed)
        return {"results": results, "provider_stats": {"gemini_search": {
            "status": "ok", "outcome": "SUCCESS" if results else "SUCCESS_NO_RESULTS", "results": len(results),
            "google_queries": len(executed)}}}

    @staticmethod
    def executed_queries(response: Any) -> list[str]:
        """groundingMetadata.webSearchQueries: the Google queries actually run."""
        output: list[str] = []
        for candidate in (response.get("candidates") if isinstance(response, Mapping)
                          else getattr(response, "candidates", None)) or []:
            metadata = (candidate.get("grounding_metadata") or candidate.get("groundingMetadata")
                        if isinstance(candidate, Mapping)
                        else getattr(candidate, "grounding_metadata", None) or getattr(candidate, "groundingMetadata", None))
            if metadata is None:
                continue
            queries = (metadata.get("web_search_queries") or metadata.get("webSearchQueries")
                       if isinstance(metadata, Mapping)
                       else getattr(metadata, "web_search_queries", None) or getattr(metadata, "webSearchQueries", None))
            output.extend(_clean(q) for q in queries or [] if _clean(q))
        return output

    @staticmethod
    def links(response: Any) -> list[dict[str, Any]]:
        """Source links from groundingMetadata, in order, without duplicates."""
        output: list[dict[str, Any]] = []
        seen: set[str] = set()

        def get(obj: Any, *names: str) -> Any:
            for name in names:
                value = obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)
                if value is not None:
                    return value
            return None

        for candidate in get(response, "candidates") or []:
            metadata = get(candidate, "grounding_metadata", "groundingMetadata")
            for chunk in get(metadata, "grounding_chunks", "groundingChunks") or [] if metadata else []:
                web = get(chunk, "web")
                url = _clean(get(web, "uri")) if web else ""
                if not url.startswith(("http://", "https://")) or url in seen:
                    continue
                seen.add(url)
                output.append({
                    "url": url, "title": _clean(get(web, "title")), "published_at": "",
                    "provider": "gemini_search", "rank": len(output) + 1,
                })
        return output


class CombinedSearch:
    """Free providers first; Gemini grounded search added for the same query.

    Results are merged without duplicate URLs, and both providers' status is
    reported, so a failure of one never reads as "no evidence exists".
    """

    def __init__(self, primary: Any, grounded: GeminiGroundedSearch | None) -> None:
        self.primary = primary
        self.grounded = grounded

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        stats: dict[str, Any] = {}
        raw: Any = None
        if self.primary is not None:
            try:
                method = getattr(self.primary, "search", None)
                raw = method(query, **kwargs) if callable(method) else self.primary(query=query, **kwargs)
            except Exception as error:
                stats["free_providers"] = {"status": "failed", "error": type(error).__name__}
        if isinstance(raw, Mapping):
            results.extend(item for item in raw.get("results") or [] if isinstance(item, Mapping))
            stats.update(raw.get("provider_stats") or {})
        elif isinstance(raw, list):
            results.extend(item for item in raw if isinstance(item, Mapping))
        if self.grounded is not None:
            extra = self.grounded.search(query, **kwargs)
            stats.update(extra.get("provider_stats") or {})
            known = {_clean(item.get("url")) for item in results}
            results.extend(item for item in extra.get("results") or [] if _clean(item.get("url")) not in known)
        return {"results": results, "provider_stats": stats}
