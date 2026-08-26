from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


SERVICE_VERSION = "web-search-service-v2.2-newsapi-embedded-key"
PATCH_BUILD_ID = "WEB-SEARCH-V22-NEWSAPI-EMBEDDED-KEY-20260825"

DEFAULT_NEWSAPI_ENDPOINT = "https://newsapi.org/v2/everything"
HARDCODED_NEWSAPI_API_KEY = "8af4921c804f454caa99075dd4d8541e"
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
    5. project/parent .env file;
    6. the embedded project key supplied for this local case-study app.

    Environment/secret values intentionally override the embedded key so the
    project can rotate credentials later without another code change.
    """

    for value in (
        explicit_key,
        os.getenv("NEWSAPI_API_KEY"),
        os.getenv("NEWS_API_KEY"),
        _read_key_from_streamlit_secrets(),
        _read_key_from_dotenv(),
        HARDCODED_NEWSAPI_API_KEY,
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
    sort_by: str = "publishedAt"

    provider_name: str = "newsapi"

    def __post_init__(self) -> None:
        self.api_key = _clean(self.api_key)
        if not self.api_key:
            raise NewsAPIAuthError(
                "NewsAPI API key is unavailable after checking explicit, "
                "environment, Streamlit, .env, and embedded project settings."
            )

        self.endpoint = _clean(self.endpoint) or DEFAULT_NEWSAPI_ENDPOINT
        if not re.match(r"^https://", self.endpoint, flags=re.I):
            raise WebSearchValidationError(
                "NewsAPI endpoint must use https://"
            )

        self.language = (_clean(self.language) or "en").casefold()
        self.sort_by = _clean(self.sort_by) or "publishedAt"
        if self.sort_by not in {
            "relevancy",
            "popularity",
            "publishedAt",
        }:
            self.sort_by = "publishedAt"

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

        params: dict[str, str] = {
            "q": query[:500],
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


def build_default_web_search_service(
    newsapi_api_key: Optional[str] = None,
) -> WebSearchService:
    """Build the default live discovery service.

    Runtime policy:
        NewsAPI /v2/everything
            -> success: use fresh result URLs and let SourceFetchService persist
               the fetched articles;
            -> quota/rate/auth/provider failure: raise to ResearchOrchestrator,
               which falls back to previously persisted SOURCE-* records for the
               same topic;
            -> zero results: return [] so ResearchOrchestrator can use the same
               persisted-source fallback.

    The key may come from environment/Streamlit/.env or the embedded local
    project fallback. It is sent only in the X-Api-Key header and is never
    placed in the request URL or emitted in diagnostics.
    """

    api_key = resolve_newsapi_key(newsapi_api_key)
    provider = NewsAPIProvider(api_key=api_key)
    return WebSearchService([provider])
