from __future__ import annotations

"""
Small in-process cache that lets the free discovery layer hand a bounded
GDELT Context snippet to SourceFetchService when the publisher page itself
cannot be fetched.

This cache does NOT turn discovery metadata into evidence. It only preserves
retrieved context so the normal source -> extraction -> evidence pipeline can
continue with an explicit limited-context marker.
"""

import threading
import time
from collections import OrderedDict
from copy import deepcopy
from typing import Any, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


CACHE_VERSION = "free-news-context-cache-v1.0"
_DEFAULT_TTL_SECONDS = 30 * 60
_DEFAULT_MAX_ITEMS = 2048
_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def canonicalize_url(url: str) -> str:
    url = _clean(url)

    if not url:
        return ""

    try:
        parts = urlsplit(url)
    except ValueError:
        return url

    if (
        parts.scheme.casefold() not in {"http", "https"}
        or not parts.netloc
    ):
        return url

    query_items = []

    for key, value in parse_qsl(
        parts.query,
        keep_blank_values=True,
    ):
        lowered = key.casefold()

        if (
            lowered.startswith("utm_")
            or lowered in _TRACKING_PARAMS
        ):
            continue

        query_items.append(
            (key, value)
        )

    return urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            parts.path or "/",
            urlencode(
                sorted(query_items),
                doseq=True,
            ),
            "",
        )
    )


class FreeNewsContextCache:
    def __init__(
        self,
        *,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        max_items: int = _DEFAULT_MAX_ITEMS,
    ) -> None:

        if ttl_seconds <= 0:
            raise ValueError(
                "ttl_seconds must be > 0"
            )

        if max_items <= 0:
            raise ValueError(
                "max_items must be > 0"
            )

        self.ttl_seconds = float(
            ttl_seconds
        )

        self.max_items = int(
            max_items
        )

        self._items: OrderedDict[
            str,
            dict[str, Any],
        ] = OrderedDict()

        self._lock = (
            threading.RLock()
        )

    def put(
        self,
        url: str,
        payload: Mapping[str, Any],
    ) -> None:

        key = canonicalize_url(
            url
        )

        if not key:
            return

        text = _clean(
            payload.get("text")
        )

        # Never preserve a bare headline or tiny fragment
        # as a usable source fallback.
        if len(text) < 60:
            return

        now = time.monotonic()

        record = {
            "url": key,
            "title": _clean(
                payload.get("title")
            ),
            "text": text,
            "provider": (
                _clean(
                    payload.get("provider")
                )
                or "gdelt_context"
            ),
            "domain": _clean(
                payload.get("domain")
            ),
            "published_at": _clean(
                payload.get("published_at")
            ),
            "language": _clean(
                payload.get("language")
            ),
            "source_country": _clean(
                payload.get("source_country")
            ),
            "context_scope": (
                _clean(
                    payload.get(
                        "context_scope"
                    )
                )
                or (
                    "matching_sentence_"
                    "with_neighboring_context"
                )
            ),
            "limited_context": True,
            "registered_monotonic": now,
            "expires_monotonic": (
                now
                + self.ttl_seconds
            ),
            "cache_version": (
                CACHE_VERSION
            ),
        }

        with self._lock:
            self._purge_expired_locked(
                now
            )

            self._items.pop(
                key,
                None,
            )

            self._items[
                key
            ] = record

            while (
                len(self._items)
                > self.max_items
            ):
                self._items.popitem(
                    last=False
                )

    def get(
        self,
        url: str,
    ) -> Optional[dict[str, Any]]:

        key = canonicalize_url(
            url
        )

        if not key:
            return None

        now = time.monotonic()

        with self._lock:
            self._purge_expired_locked(
                now
            )

            record = (
                self._items.get(
                    key
                )
            )

            if record is None:
                return None

            self._items.move_to_end(
                key
            )

            return deepcopy(
                record
            )

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def _purge_expired_locked(
        self,
        now: float,
    ) -> None:

        expired = [
            key
            for key, value
            in self._items.items()
            if float(
                value.get(
                    "expires_monotonic",
                    0.0,
                )
                or 0.0
            )
            <= now
        ]

        for key in expired:
            self._items.pop(
                key,
                None,
            )


_GLOBAL_CACHE = (
    FreeNewsContextCache()
)


def register_free_news_context(
    url: str,
    *,
    text: str,
    title: str = "",
    provider: str = "gdelt_context",
    domain: str = "",
    published_at: str = "",
    language: str = "",
    source_country: str = "",
    context_scope: str = (
        "matching_sentence_"
        "with_neighboring_context"
    ),
) -> None:

    _GLOBAL_CACHE.put(
        url,
        {
            "text": text,
            "title": title,
            "provider": provider,
            "domain": domain,
            "published_at": (
                published_at
            ),
            "language": language,
            "source_country": (
                source_country
            ),
            "context_scope": (
                context_scope
            ),
        },
    )


def get_free_news_context(
    url: str,
) -> Optional[dict[str, Any]]:

    return _GLOBAL_CACHE.get(
        url
    )


def clear_free_news_context_cache() -> None:
    _GLOBAL_CACHE.clear()