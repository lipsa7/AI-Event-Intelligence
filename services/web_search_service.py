from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


SERVICE_VERSION = "web-search-service-v2.5-multi-source-free"
PATCH_BUILD_ID = "WEB-SEARCH-V25-MULTI-SOURCE-20261004"

# v2.3: discovery precision.
# - sortBy=relevancy (was publishedAt: newest article matching ANY field won).
# - searchIn=title,description: the topic must appear in the headline or
#   standfirst, not just somewhere in the body.
# Override with NEWSAPI_SORT_BY / NEWSAPI_SEARCH_IN if ever needed.
DEFAULT_SORT_BY = "relevancy"
DEFAULT_SEARCH_IN = "title,description"
_ALLOWED_SEARCH_IN = {"title", "description", "content"}

DEFAULT_NEWSAPI_ENDPOINT = "https://newsapi.org/v2/everything"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0 Safari/537.36 EventIntelligence/2.1"
)

__all__ = [
    "WebSearchError",
    "WebSearchValidationError",
    "WebSearchProviderError",
    "WebSearchRateLimitError",
    "NewsAPIError",
    "NewsAPIQuotaError",
    "NewsAPIAuthError",
    "NewsAPIProvider",
    "WebSearchService",
    "MultiSourceSearchService",
    "build_discovery_providers",
    "build_default_web_search_service",
    "SERVICE_VERSION",
    "PATCH_BUILD_ID",
]


class WebSearchError(RuntimeError):
    pass


class WebSearchValidationError(WebSearchError):
    pass


class WebSearchProviderError(WebSearchError):
    pass


class WebSearchRateLimitError(WebSearchProviderError):
    pass


class NewsAPIError(WebSearchProviderError):
    pass


class NewsAPIQuotaError(WebSearchRateLimitError, NewsAPIError):
    """NewsAPI request quota/rate window is exhausted."""


class NewsAPIAuthError(NewsAPIError):
    """NewsAPI key is missing, invalid, or disabled."""


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _canonical_url(value: Any) -> str:
    url = _clean(value)
    if not url:
        return ""
    if not re.match(r"^https?://", url, flags=re.I):
        return ""
    return url.split("#", 1)[0].strip()


def _domain_from_url(value: Any) -> str:
    url = _canonical_url(value)
    if not url:
        return ""
    try:
        host = (urlparse(url).hostname or "").casefold()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def _normalize_datetime(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    try:
        candidate = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(candidate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return text[:120]


def _dedupe_results(
    items: Iterable[Mapping[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()

    for raw in items:
        if not isinstance(raw, Mapping):
            continue

        url = _canonical_url(
            raw.get("canonical_url")
            or raw.get("url")
            or raw.get("link")
        )
        title = _clean(raw.get("title"))
        if not url or not title:
            continue

        url_key = url.casefold().rstrip("/")
        title_key = re.sub(
            r"[^a-z0-9]+",
            " ",
            title.casefold(),
        ).strip()

        if url_key in seen_urls:
            continue
        if title_key and title_key in seen_titles:
            continue

        seen_urls.add(url_key)
        if title_key:
            seen_titles.add(title_key)

        item = dict(raw)
        item["url"] = url
        item["canonical_url"] = url
        item["title"] = title
        item["domain"] = (
            _clean(item.get("domain"))
            or _domain_from_url(url)
        )
        item["snippet"] = _clean(
            item.get("snippet")
            or item.get("description")
            or item.get("content")
        )[:1200]
        item["published_at"] = _normalize_datetime(
            item.get("published_at")
            or item.get("publishedAt")
            or item.get("published")
            or item.get("date")
        )
        output.append(item)

        if len(output) >= max(1, int(limit)):
            break

    return output


def _read_key_from_dotenv() -> str:
    """Read only the NewsAPI key from a nearby .env without adding a dependency.

    This is intentionally tiny and conservative. It supports ordinary
    KEY=value and quoted values; it does not try to implement full shell syntax.
    The key is never logged or returned in diagnostics.
    """

    candidates: list[Path] = []
    cwd = Path.cwd()
    for base in (cwd, cwd.parent):
        candidate = base / ".env"
        if candidate not in candidates:
            candidates.append(candidate)

    for path in candidates:
        try:
            if not path.is_file():
                continue
            for raw_line in path.read_text(
                encoding="utf-8",
                errors="ignore",
            ).splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key not in {"NEWSAPI_API_KEY", "NEWS_API_KEY"}:
                    continue
                value = value.strip()
                if (
                    len(value) >= 2
                    and value[0] == value[-1]
                    and value[0] in {"'", '"'}
                ):
                    value = value[1:-1]
                value = value.strip()
                if value:
                    return value
        except OSError:
            continue

    return ""


def _read_key_from_streamlit_secrets() -> str:
    """Resolve a key from Streamlit secrets without requiring Streamlit."""

    try:
        import streamlit as st  # type: ignore
    except Exception:
        return ""

    try:
        for key in ("NEWSAPI_API_KEY", "NEWS_API_KEY"):
            value = _clean(st.secrets.get(key, ""))
            if value:
                return value
    except Exception:
        return ""

    return ""


def resolve_newsapi_key(explicit_key: Optional[str] = None) -> str:
    """Resolve NewsAPI credentials.

    Resolution order:
    1. explicit constructor/factory argument;
    2. NEWSAPI_API_KEY environment variable;
    3. NEWS_API_KEY environment alias;
    4. Streamlit secrets;
    5. project/parent .env file.

    No key is embedded in code. Set NEWSAPI_API_KEY in the environment, in
    Streamlit secrets, or in a .env file in the project folder.
    """

    for value in (
        explicit_key,
        os.getenv("NEWSAPI_API_KEY"),
        os.getenv("NEWS_API_KEY"),
        _read_key_from_streamlit_secrets(),
        _read_key_from_dotenv(),
    ):
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return ""


def _decode_error_payload(error: HTTPError) -> dict[str, Any]:
    try:
        raw = error.read(256_000)
    except Exception:
        return {}

    try:
        decoded = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return {}

    return dict(decoded) if isinstance(decoded, Mapping) else {}


@dataclass
class NewsAPIProvider:
    api_key: str
    endpoint: str = DEFAULT_NEWSAPI_ENDPOINT
    user_agent: str = DEFAULT_USER_AGENT
    language: str = "en"
    sort_by: str = ""
    search_in: str = ""

    provider_name: str = "newsapi"

    def __post_init__(self) -> None:
        self.api_key = _clean(self.api_key)
        if not self.api_key:
            raise NewsAPIAuthError(
                "NewsAPI API key not configured. Set NEWSAPI_API_KEY in the "
                "environment, in Streamlit secrets, or as NEWSAPI_API_KEY=... "
                "in a .env file in the project folder."
            )

        self.endpoint = _clean(self.endpoint) or DEFAULT_NEWSAPI_ENDPOINT
        if not re.match(r"^https://", self.endpoint, flags=re.I):
            raise WebSearchValidationError(
                "NewsAPI endpoint must use https://"
            )

        self.language = (_clean(self.language) or "en").casefold()
        self.sort_by = (
            _clean(self.sort_by)
            or _clean(os.getenv("NEWSAPI_SORT_BY"))
            or DEFAULT_SORT_BY
        )
        if self.sort_by not in {
            "relevancy",
            "popularity",
            "publishedAt",
        }:
            self.sort_by = DEFAULT_SORT_BY
        self.search_in = self._normalize_search_in(
            _clean(self.search_in)
            or _clean(os.getenv("NEWSAPI_SEARCH_IN"))
            or DEFAULT_SEARCH_IN
        )

    def earliest_available(self) -> datetime:
        """Oldest article the plan can search: NEWSAPI_MAX_AGE_DAYS (default 30,
        the free Developer plan's one-month limit)."""
        try:
            days = float(os.getenv("NEWSAPI_MAX_AGE_DAYS", "30"))
        except ValueError:
            days = 30.0
        return datetime.now(timezone.utc) - timedelta(days=max(1.0, days))

    @staticmethod
    def _normalize_search_in(value: str) -> str:
        fields = [
            part.strip().casefold()
            for part in str(value or "").split(",")
            if part.strip().casefold() in _ALLOWED_SEARCH_IN
        ]
        return ",".join(dict.fromkeys(fields)) or DEFAULT_SEARCH_IN

    def _raise_newsapi_error(
        self,
        *,
        status_code: int,
        payload: Mapping[str, Any],
        fallback_message: str,
    ) -> None:
        code = _clean(payload.get("code"))
        message = _clean(payload.get("message")) or fallback_message
        normalized = code.casefold()

        if (
            status_code == 429
            or normalized in {
                "apikeyexhausted",
                "ratelimited",
            }
        ):
            raise NewsAPIQuotaError(
                f"NewsAPI quota/rate limit reached: {code or status_code}: {message}"
            )

        if (
            status_code == 401
            or normalized in {
                "apikeydisabled",
                "apikeyinvalid",
                "apikeymissing",
            }
        ):
            raise NewsAPIAuthError(
                f"NewsAPI API key authentication failed: {code or status_code}: {message}"
            )

        raise NewsAPIError(
            f"NewsAPI provider error: {code or status_code}: {message}"
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        max_results: Optional[int] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        **kwargs: Any,
    ) -> dict[str, Any]:
        query = _clean(query)
        if not query:
            raise WebSearchValidationError("query cannot be empty")

        requested = max_results if max_results is not None else limit
        requested = max(1, min(_safe_int(requested, 10), 100))
        timeout = max(
            2.0,
            min(
                float(timeout_seconds or DEFAULT_TIMEOUT_SECONDS),
                30.0,
            ),
        )

        sort_by = _clean(kwargs.get("sort_by")) or self.sort_by
        if sort_by not in {
            "relevancy",
            "popularity",
            "publishedAt",
        }:
            sort_by = self.sort_by

        search_in = (
            self._normalize_search_in(_clean(kwargs.get("search_in")))
            if _clean(kwargs.get("search_in"))
            else self.search_in
        )

        params: dict[str, str] = {
            "q": query[:500],
            "searchIn": search_in,
            "language": self.language,
            "sortBy": sort_by,
            "pageSize": str(requested),
            "page": "1",
        }

        from_value = _clean(
            kwargs.get("from_date")
            or kwargs.get("from")
        )
        to_value = _clean(
            kwargs.get("to_date")
            or kwargs.get("to")
        )
        if from_value:
            params["from"] = from_value
        if to_value:
            params["to"] = to_value

        url = self.endpoint.rstrip("?") + "?" + urlencode(params)

        # NewsAPI recommends header authentication so the key is not exposed in
        # URLs/logs. Never include self.api_key in exception messages.
        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
                "X-Api-Key": self.api_key,
            },
            method="GET",
        )

        try:
            with urlopen(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 200) or 200)
                raw = response.read(4_000_000)
        except HTTPError as error:
            payload = _decode_error_payload(error)
            self._raise_newsapi_error(
                status_code=int(error.code),
                payload=payload,
                fallback_message=_clean(error.reason) or "HTTP error",
            )
            raise AssertionError("unreachable")
        except (URLError, TimeoutError, OSError) as error:
            raise NewsAPIError(
                f"NewsAPI request failed: {type(error).__name__}: {error}"
            ) from error

        try:
            decoded = json.loads(
                raw.decode("utf-8", errors="replace")
            )
        except json.JSONDecodeError as error:
            raise NewsAPIError(
                "NewsAPI returned invalid JSON"
            ) from error

        if not isinstance(decoded, Mapping):
            raise NewsAPIError(
                "NewsAPI returned a non-object response"
            )

        if status >= 400 or _clean(decoded.get("status")).casefold() == "error":
            self._raise_newsapi_error(
                status_code=status,
                payload=decoded,
                fallback_message="NewsAPI request failed",
            )

        raw_articles = decoded.get("articles", [])
        normalized: list[dict[str, Any]] = []

        for article in raw_articles if isinstance(raw_articles, list) else []:
            if not isinstance(article, Mapping):
                continue

            article_url = _canonical_url(article.get("url"))
            title = _clean(article.get("title"))
            if not article_url or not title:
                continue

            source = article.get("source")
            source_mapping = (
                source if isinstance(source, Mapping) else {}
            )

            normalized.append(
                {
                    "url": article_url,
                    "canonical_url": article_url,
                    "title": title,
                    "snippet": _clean(
                        article.get("description")
                        or article.get("content")
                    ),
                    "description": _clean(article.get("description")),
                    "provider_content": _clean(article.get("content")),
                    "domain": _domain_from_url(article_url),
                    "published_at": _normalize_datetime(
                        article.get("publishedAt")
                    ),
                    "author": _clean(article.get("author")),
                    "source_name": _clean(source_mapping.get("name")),
                    "source_provider_id": _clean(source_mapping.get("id")),
                    "provider": self.provider_name,
                }
            )

        results = _dedupe_results(normalized, requested)
        return {
            "provider": self.provider_name,
            "query": query,
            "sort_by": sort_by,
            "search_in": search_in,
            "results": results,
            "result_count": len(results),
            "total_results": _safe_int(
                decoded.get("totalResults"),
                len(results),
            ),
        }


class WebSearchService:
    """Provider wrapper used by ResearchOrchestratorService.

    Default construction intentionally has one live provider: NewsAPI. If
    NewsAPI is exhausted/unavailable, the error is propagated to the
    orchestrator, which owns the persisted same-topic SOURCE-* fallback.
    This avoids silently changing the evidence source from NewsAPI to GDELT.
    """

    def __init__(self, providers: Sequence[Any]) -> None:
        self.providers = [
            provider
            for provider in providers
            if provider is not None
        ]
        if not self.providers:
            raise WebSearchValidationError(
                "WebSearchService requires at least one provider"
            )

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        max_results: Optional[int] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        **kwargs: Any,
    ) -> dict[str, Any]:
        query = _clean(query)
        if not query:
            raise WebSearchValidationError("query cannot be empty")

        errors: list[dict[str, str]] = []
        last_error: Optional[BaseException] = None

        for provider in self.providers:
            name = (
                _clean(getattr(provider, "provider_name", ""))
                or type(provider).__name__
            )
            try:
                result = provider.search(
                    query,
                    limit=limit,
                    max_results=max_results,
                    timeout_seconds=timeout_seconds,
                    **kwargs,
                )
                mapping = (
                    dict(result)
                    if isinstance(result, Mapping)
                    else {"results": result}
                )
                results = mapping.get("results", [])
                if isinstance(results, list) and results:
                    mapping.setdefault("provider", name)
                    mapping["provider_attempts"] = list(errors)
                    return mapping

                errors.append(
                    {
                        "provider": name,
                        "error": "NoResults",
                        "message": "provider returned no results",
                    }
                )
            except Exception as error:
                last_error = error
                errors.append(
                    {
                        "provider": name,
                        "error": type(error).__name__,
                        "message": str(error),
                    }
                )

                # Quota/auth failures should immediately reach the orchestrator
                # so it can switch to persisted SOURCE-* records instead of
                # spending additional discovery calls.
                if isinstance(
                    error,
                    (NewsAPIQuotaError, NewsAPIAuthError),
                ):
                    raise

        if last_error is not None:
            raise last_error

        # No provider error, but no current NewsAPI articles matched. Returning
        # an empty result lets the orchestrator use its persisted-source fallback.
        return {
            "provider": "newsapi",
            "query": query,
            "results": [],
            "result_count": 0,
            "provider_attempts": errors,
        }

    search_web = search
    discover = search


class MultiSourceSearchService(WebSearchService):
    """Asks every configured provider and merges the results.

    Each provider gets the same query, limit and date window. Results are
    interleaved round-robin (so one provider cannot fill the whole limit),
    de-duplicated by URL and title, and keep `provider` plus `also_found_by`.
    A provider that fails is recorded in `provider_stats` and skipped; one that
    hits its quota or auth limit is switched off for the rest of the process.
    Only when every provider fails is an error raised: WebSearchRateLimitError
    (whose message the orchestrator's circuit breaker recognises) when all of
    them are out of quota, rate limited, cooling down or unconfigured, else an
    ordinary WebSearchProviderError, so a retry stays possible.
    """

    TERMINAL_ERRORS = ("NewsAPIQuotaError", "NewsAPIAuthError", "FreeNewsConfigurationError")

    def __init__(self, providers: Sequence[Any]) -> None:
        super().__init__(providers)
        self.disabled: dict[str, str] = {}
        self.provider_totals: dict[str, dict[str, Any]] = {}

    # A failure of one of these types means the provider cannot return
    # anything new for a while (quota, rate limit, cooldown, bad key/config).
    EXHAUSTED_ERRORS = frozenset({
        "WebSearchRateLimitError", "NewsAPIQuotaError", "NewsAPIAuthError",
        "FreeNewsRateLimitError", "FreeNewsConfigurationError",
    })

    @staticmethod
    def _name(provider: Any) -> str:
        return _clean(getattr(provider, "provider_name", "")) or type(provider).__name__

    # Per-source outcome of one search. Never collapsed into success/failure.
    SUCCESS_WITH_RESULTS = "SUCCESS_WITH_RESULTS"
    SUCCESS_NO_RESULTS = "SUCCESS_NO_RESULTS"
    OUT_OF_COVERAGE = "OUT_OF_COVERAGE"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    SOURCE_ERROR = "SOURCE_ERROR"

    def _failure_outcome(self, error: BaseException) -> str:
        names = {cls.__name__ for cls in type(error).__mro__}
        text = str(error).casefold()
        if names & {"WebSearchRateLimitError", "NewsAPIQuotaError", "FreeNewsRateLimitError"}:
            return self.RATE_LIMITED
        if "timeout" in " ".join(names).casefold() or "timed out" in text or "timeout" in text:
            return self.TIMEOUT
        return self.SOURCE_ERROR

    @staticmethod
    def _requested_window(kwargs: Mapping[str, Any]) -> Optional[tuple[Optional[datetime], datetime]]:
        """(start or None, end) when the caller asked for dates, else None."""
        raw_from = _clean(kwargs.get("from_date") or kwargs.get("from"))
        raw_to = _clean(kwargs.get("to_date") or kwargs.get("to"))
        if not raw_from and not raw_to:
            return None
        from services.free_news_providers import _parse_date

        start = _parse_date(raw_from) if raw_from else None
        end = _parse_date(raw_to) if raw_to else None
        if end is not None and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_to):
            end = end + timedelta(days=1) - timedelta(seconds=1)
        return start, (end or datetime.now(timezone.utc))

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _is_exhausted(self, error: BaseException) -> bool:
        return bool({cls.__name__ for cls in type(error).__mro__} & self.EXHAUSTED_ERRORS)

    def _no_provider_error(self, stats: Mapping[str, Mapping[str, Any]]) -> WebSearchProviderError:
        """Error for a search in which no provider returned anything.

        When every provider is disabled or failed with a quota / rate-limit /
        cooldown / configuration error, a retry in this run cannot help, so
        the message says "rate limit" and "quota" and the orchestrator opens
        its fresh-discovery circuit. If any provider failed for another reason
        (a network error, a bad response) the error is ordinary and the
        message names providers and outcomes only, so no provider's own text
        can open the circuit by accident.
        """
        outcomes = {
            name: (
                "out_of_coverage" if row.get("status") == "out_of_coverage"
                else "unavailable" if row.get("exhausted") or row.get("status") == "disabled"
                else "failed"
            )
            for name, row in stats.items()
        }
        summary = ", ".join(f"{name}={outcome}" for name, outcome in outcomes.items()) or "none configured"
        asked = {name: outcome for name, outcome in outcomes.items() if outcome != "out_of_coverage"}
        if outcomes and not asked:
            # Nothing configured can search these dates: not a failure of any
            # source and not a reason to stop discovery for the run.
            return WebSearchProviderError(
                f"no configured source covers the requested dates ({summary})"
            )
        if all(outcome == "unavailable" for outcome in asked.values()):
            return WebSearchRateLimitError(
                "every discovery provider is out of quota, rate limited, cooling down or not "
                f"configured ({summary})"
            )
        return WebSearchProviderError(f"no discovery provider returned results ({summary})")

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        max_results: Optional[int] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        **kwargs: Any,
    ) -> dict[str, Any]:
        import time as _time

        query = _clean(query)
        if not query:
            raise WebSearchValidationError("query cannot be empty")
        requested = max(1, min(_safe_int(max_results if max_results is not None else limit, 10), 100))
        per_provider: dict[str, list[dict[str, Any]]] = {}
        stats: dict[str, dict[str, Any]] = {}
        window = self._requested_window(kwargs)
        for provider in self.providers:
            name = self._name(provider)
            totals = self.provider_totals.setdefault(
                name, {"requests": 0, "failures": 0, "results": 0, "latency_ms": 0.0}
            )
            if name in self.disabled:
                reason = self.disabled[name]
                stats[name] = {
                    "status": "disabled", "reason": reason,
                    "outcome": self.RATE_LIMITED if "Quota" in reason else self.SOURCE_ERROR,
                }
                continue
            call_kwargs = dict(kwargs)
            coverage: dict[str, Any] = {}
            earliest_fn = getattr(provider, "earliest_available", None)
            if window is not None and callable(earliest_fn):
                earliest = earliest_fn()
                start, end = window
                if end < earliest:
                    # The source cannot search these dates: not asked, and
                    # not counted as a failure (no request spent on it).
                    stats[name] = {
                        "status": "out_of_coverage", "outcome": self.OUT_OF_COVERAGE,
                        "earliest_available": self._iso(earliest),
                        "requested_to": self._iso(end),
                    }
                    continue
                if start is None or start < earliest:
                    call_kwargs.pop("from", None)
                    call_kwargs["from_date"] = self._iso(earliest)
                    coverage = {
                        "coverage": "partial",
                        "requested_from": self._iso(start) if start else "",
                        "searched_from": self._iso(earliest),
                    }
            started = _time.perf_counter()
            totals["requests"] += 1
            try:
                result = provider.search(
                    query, limit=requested, max_results=requested,
                    timeout_seconds=timeout_seconds, **call_kwargs,
                )
            except Exception as error:
                latency = round((_time.perf_counter() - started) * 1000, 1)
                if any("OutOfCoverage" in cls.__name__ for cls in type(error).__mro__):
                    stats[name] = {
                        "status": "out_of_coverage", "outcome": self.OUT_OF_COVERAGE,
                        "message": _clean(str(error))[:240], "latency_ms": latency,
                    }
                    continue
                totals["failures"] += 1
                totals["latency_ms"] += latency
                stats[name] = {
                    "status": "failed", "error": type(error).__name__,
                    "message": _clean(str(error))[:240], "latency_ms": latency,
                    "outcome": self._failure_outcome(error),
                }
                stats[name]["exhausted"] = self._is_exhausted(error)
                if type(error).__name__ in self.TERMINAL_ERRORS:
                    self.disabled[name] = type(error).__name__
                continue
            latency = round((_time.perf_counter() - started) * 1000, 1)
            totals["latency_ms"] += latency
            mapping = dict(result) if isinstance(result, Mapping) else {"results": result}
            rows = [dict(row) for row in mapping.get("results") or [] if isinstance(row, Mapping)]
            for row in rows:
                row.setdefault("provider", name)
                row.setdefault("discovery_provider", name)
            per_provider[name] = rows
            totals["results"] += len(rows)
            stats[name] = {
                "status": "ok", "results": len(rows), "latency_ms": latency,
                "outcome": self.SUCCESS_WITH_RESULTS if rows else self.SUCCESS_NO_RESULTS,
                **coverage,
            }
            if getattr(provider, "coverage_note", ""):
                stats[name]["coverage_note"] = provider.coverage_note
            if isinstance(mapping.get("date_filter"), Mapping):
                stats[name]["date_filter"] = dict(mapping["date_filter"])

        if not per_provider:
            raise self._no_provider_error(stats)

        interleaved: list[dict[str, Any]] = []
        queues = [list(rows) for rows in per_provider.values()]
        while any(queues):
            for rows in queues:
                if rows:
                    interleaved.append(rows.pop(0))
        found_by: dict[str, list[str]] = {}
        for row in interleaved:
            key = _canonical_url(row.get("url")).casefold().rstrip("/")
            found_by.setdefault(key, [])
            if row.get("provider") not in found_by[key]:
                found_by[key].append(row.get("provider"))
        merged = _dedupe_results(interleaved, requested)
        for row in merged:
            key = _canonical_url(row.get("url")).casefold().rstrip("/")
            row["also_found_by"] = [p for p in found_by.get(key, []) if p != row.get("provider")]
        return {
            "provider": "+".join(per_provider),
            "query": query,
            "results": merged,
            "result_count": len(merged),
            "provider_stats": stats,
            "provider_attempts": [
                {"provider": name, "error": row.get("error", ""), "message": row.get("message", "")}
                for name, row in stats.items() if row.get("status") != "ok"
            ],
        }

    search_web = search
    discover = search


DEFAULT_DISCOVERY_PROVIDERS = "newsapi,gdelt,rss,exa"


def build_discovery_providers(
    names: Optional[Sequence[str]] = None,
    *,
    newsapi_api_key: Optional[str] = None,
) -> tuple[list[Any], dict[str, str]]:
    """Construct the requested providers; returns (providers, skipped{name: reason}).

    NEWS_DISCOVERY_PROVIDERS (default "newsapi,gdelt,rss,exa") picks them.
    A provider without its key/configuration is skipped, not fatal.
    """
    if names is None:
        names = _clean(os.getenv("NEWS_DISCOVERY_PROVIDERS")) or DEFAULT_DISCOVERY_PROVIDERS
    if isinstance(names, str):
        names = names.split(",")
    wanted = [str(name).strip().casefold() for name in names if str(name).strip()]
    providers: list[Any] = []
    skipped: dict[str, str] = {}
    for name in dict.fromkeys(wanted):
        try:
            if name == "newsapi":
                key = resolve_newsapi_key(newsapi_api_key)
                if not key:
                    skipped[name] = "NEWSAPI_API_KEY not set"
                    continue
                providers.append(NewsAPIProvider(api_key=key))
            elif name in {"gdelt", "rss", "exa"}:
                from services.free_news_providers import ExaProvider, GDELTDocProvider, RSSFeedProvider
                if name == "gdelt":
                    providers.append(GDELTDocProvider())
                elif name == "rss":
                    providers.append(RSSFeedProvider())
                else:
                    if not _clean(os.getenv("EXA_API_KEY")):
                        skipped[name] = "EXA_API_KEY not set"
                        continue
                    providers.append(ExaProvider())
            else:
                skipped[name] = "unknown provider"
        except Exception as error:
            skipped[name] = f"{type(error).__name__}: {error}"[:240]
    return providers, skipped


def build_default_web_search_service(
    newsapi_api_key: Optional[str] = None,
) -> WebSearchService:
    """Build the live discovery service from every configured free provider.

    Providers: NewsAPI (if NEWSAPI_API_KEY is set), GDELT (free, no key), RSS
    feeds from config/news_feeds.txt, Exa (if EXA_API_KEY is set). Results are
    merged by MultiSourceSearchService. Keys come from the environment,
    Streamlit secrets or .env (never from code) and are never put in URLs or
    diagnostics. With no provider available, raises NewsAPIAuthError when
    NewsAPI was the only one requested, else WebSearchProviderError.
    """

    providers, skipped = build_discovery_providers(newsapi_api_key=newsapi_api_key)
    if not providers:
        if set(skipped) == {"newsapi"}:
            raise NewsAPIAuthError(
                "NewsAPI API key not configured. Set NEWSAPI_API_KEY in the environment, in "
                "Streamlit secrets, or as NEWSAPI_API_KEY=... in a .env file in the project folder."
            )
        raise WebSearchProviderError(f"no discovery provider available: {skipped}")
    service = MultiSourceSearchService(providers)
    service.skipped_providers = skipped
    return service
