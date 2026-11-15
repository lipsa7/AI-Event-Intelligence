"""
Free/local RSS + Atom discovery and ingestion service.

Recommended location:
    services/rss_service.py

Role
----
configured RSS/Atom feeds
        ↓
RSSService
        ↓
normalized feed-discovery items
        ↓
        ├─ feed-provided full content
        │      ↓
        │  ArticleService
        │      ↓
        │  SOURCE-*
        │
        └─ summary/link only
               ↓
          SourceFetchService
               ↓
          ArticleService
               ↓
          SOURCE-*
               ↓
event extraction pipeline

Critical identity boundary
--------------------------
RSSService owns:
    FEED-*
    RSSITEM-*

ArticleService owns:
    SOURCE-*

RSSService MUST NOT manufacture SOURCE-* identities.

Critical epistemic boundary
----------------------------
RSS metadata and feed items are discovery/ingestion metadata, NOT grounding
evidence.

    FEED-* / RSSITEM-* != SOURCE-* != EVID-*

A feed summary is not silently promoted to full Article.content. If a feed item
does not contain usable full content, its source URL must be fetched through
SourceFetchService before ArticleService persists the source.

Capabilities
------------
- RSS 2.x
- RSS 1.0/RDF-style item discovery
- Atom feeds
- raw XML parsing
- direct HTTP(S) feed fetching using Python stdlib
- conditional GET metadata (ETag / Last-Modified)
- bounded feed downloads with fail-closed overflow behavior
- deterministic FEED-* and RSSITEM-* identities
- duplicate suppression
- feed-provided full-content handoff to ArticleService
- explicit source-fetch request generation for summary/link-only items
- zero paid API requirement
- zero LLM requirement

No private chain-of-thought is requested, stored or returned.
"""

from __future__ import annotations

import email.utils
import hashlib
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Sequence


SERVICE_VERSION = "rss-service-v3.0"
DEFAULT_USER_AGENT = "AIEventIntelligenceRSS/3.0"


TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "dclid",
    "mc_cid",
    "mc_eid",
    "igshid",
}


PROHIBITED_PRIVATE_REASONING_FIELDS = {
    "chain_of_thought",
    "chainofthought",
    "cot",
    "scratchpad",
    "hidden_reasoning",
    "hiddenreasoning",
    "raw_reasoning",
    "rawreasoning",
    "private_reasoning",
    "privatereasoning",
    "internal_monologue",
    "internalmonologue",
    "deliberation",
    "deliberation_trace",
    "deliberationtrace",
}


class RSSServiceError(RuntimeError):
    """Base RSSService error."""


class RSSValidationError(RSSServiceError):
    """Raised when RSS/Atom input violates the service contract."""


class RSSFetchError(RSSServiceError):
    """Raised when an RSS/Atom feed cannot be fetched safely."""


def _clean(value: Any) -> str:
    if value is None:
        return ""

    return " ".join(
        str(value).split()
    ).strip()


def _enum(value: Any) -> str:
    text = (
        _clean(value)
        .casefold()
        .replace("-", "_")
        .replace(" ", "_")
    )

    text = re.sub(
        r"[^a-z0-9_]+",
        "",
        text,
    )

    return text.strip("_")


def _canonical_url(url: str) -> str:
    """
    Canonicalize HTTP(S) URLs for discovery deduplication.

    This is deliberately conservative and does not establish publisher-level
    SOURCE-* identity. ArticleService remains the source identity authority.
    """

    url = _clean(url)

    if not url:
        return ""

    parts = urllib.parse.urlsplit(
        url
    )

    if (
        parts.scheme.casefold()
        not in {"http", "https"}
        or not parts.netloc
    ):
        return url

    query: list[tuple[str, str]] = []

    for (
        key,
        value,
    ) in urllib.parse.parse_qsl(
        parts.query,
        keep_blank_values=True,
    ):
        lower = key.casefold()

        if (
            lower.startswith("utm_")
            or lower in TRACKING_PARAMS
        ):
            continue

        query.append(
            (
                key,
                value,
            )
        )

    query.sort()

    hostname = (
        parts.hostname
        or ""
    ).casefold()

    port = parts.port

    default_port = (
        (
            parts.scheme.casefold()
            == "http"
            and port == 80
        )
        or (
            parts.scheme.casefold()
            == "https"
            and port == 443
        )
    )

    netloc = (
        hostname
        if (
            port is None
            or default_port
        )
        else f"{hostname}:{port}"
    )

    return urllib.parse.urlunsplit(
        (
            parts.scheme.casefold(),
            netloc,
            parts.path or "/",
            urllib.parse.urlencode(
                query,
                doseq=True,
            ),
            "",
        )
    )


def _stable_id(
    prefix: str,
    payload: Any,
    length: int = 24,
) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode(
        "utf-8"
    )

    return (
        f"{prefix}-"
        + hashlib.sha256(
            raw
        ).hexdigest()[:length].upper()
    )


def _strip_html(value: Any) -> str:
    text = str(
        value
        or ""
    )

    text = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        text,
        flags=re.I | re.S,
    )

    text = re.sub(
        r"<style\b[^>]*>.*?</style>",
        " ",
        text,
        flags=re.I | re.S,
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    return _clean(
        html.unescape(
            text
        )
    )


def _local_name(tag: str) -> str:
    return (
        tag.rsplit(
            "}",
            1,
        )[-1]
        .casefold()
    )


def _child_text(
    element: ET.Element,
    names: Sequence[str],
) -> str:
    wanted = {
        name.casefold()
        for name in names
    }

    for child in list(
        element
    ):
        if (
            _local_name(
                child.tag
            )
            in wanted
        ):
            return _clean(
                "".join(
                    child.itertext()
                )
            )

    return ""


def _all_child_text(
    element: ET.Element,
    name: str,
) -> list[str]:
    output: list[str] = []

    for child in list(
        element
    ):
        if (
            _local_name(
                child.tag
            )
            == name.casefold()
        ):
            text = _clean(
                "".join(
                    child.itertext()
                )
            )

            if text:
                output.append(
                    text
                )

    return output


def _parse_time(
    value: str,
) -> str:
    """
    Normalize a feed timestamp to UTC ISO-8601 where possible.

    If a feed supplies an unfamiliar but non-empty temporal string, preserve
    it rather than inventing a date.
    """

    value = _clean(
        value
    )

    if not value:
        return ""

    try:
        parsed = (
            email.utils
            .parsedate_to_datetime(
                value
            )
        )

        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(
                    tzinfo=timezone.utc
                )

            return (
                parsed
                .astimezone(
                    timezone.utc
                )
                .isoformat()
            )

    except Exception:
        pass

    try:
        parsed = datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00",
            )
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return (
            parsed
            .astimezone(
                timezone.utc
            )
            .isoformat()
        )

    except Exception:
        return value


def _contains_prohibited_field(
    value: Any,
    path: str = "",
) -> Optional[str]:
    if isinstance(
        value,
        Mapping,
    ):
        for (
            key,
            item,
        ) in value.items():
            normalized = _enum(
                key
            )

            current = (
                f"{path}.{key}"
                if path
                else str(key)
            )

            if (
                normalized
                in PROHIBITED_PRIVATE_REASONING_FIELDS
            ):
                return current

            nested = (
                _contains_prohibited_field(
                    item,
                    current,
                )
            )

            if nested:
                return nested

    elif isinstance(
        value,
        (
            list,
            tuple,
        ),
    ):
        for (
            index,
            item,
        ) in enumerate(
            value
        ):
            nested = (
                _contains_prohibited_field(
                    item,
                    f"{path}[{index}]",
                )
            )

            if nested:
                return nested

    return None


class RSSService:
    """
    Free/local RSS + Atom discovery service.

    FEED-* and RSSITEM-* are discovery identities only.

    ArticleService is the sole SOURCE-* identity authority.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        max_feed_bytes: int = (
            5 * 1024 * 1024
        ),
        user_agent: str = (
            DEFAULT_USER_AGENT
        ),
    ) -> None:
        if timeout_seconds <= 0:
            raise RSSValidationError(
                "timeout_seconds must be > 0."
            )

        if max_feed_bytes <= 0:
            raise RSSValidationError(
                "max_feed_bytes must be > 0."
            )

        self.timeout_seconds = float(
            timeout_seconds
        )

        self.max_feed_bytes = int(
            max_feed_bytes
        )

        self.user_agent = (
            _clean(
                user_agent
            )
            or DEFAULT_USER_AGENT
        )

    # ------------------------------------------------------------------
    # HTTP fetching
    # ------------------------------------------------------------------

    def fetch_feed(
        self,
        feed_url: str,
        *,
        etag: str = "",
        last_modified: str = "",
        timeout_seconds: Optional[
            float
        ] = None,
    ) -> Dict[str, Any]:
        feed_url = (
            _canonical_url(
                feed_url
            )
        )

        if not feed_url:
            raise RSSValidationError(
                "feed_url cannot be empty."
            )

        parts = (
            urllib.parse.urlsplit(
                feed_url
            )
        )

        if (
            parts.scheme.casefold()
            not in {
                "http",
                "https",
            }
        ):
            raise RSSValidationError(
                "RSS feed URL must be HTTP(S)."
            )

        timeout = (
            float(
                timeout_seconds
            )
            if timeout_seconds
            is not None
            else self.timeout_seconds
        )

        if timeout <= 0:
            raise RSSValidationError(
                "timeout_seconds must be > 0."
            )

        headers = {
            "User-Agent": (
                self.user_agent
            ),
            "Accept": (
                "application/rss+xml, "
                "application/atom+xml, "
                "application/xml, "
                "text/xml;q=0.9, "
                "*/*;q=0.2"
            ),
        }

        if _clean(
            etag
        ):
            headers[
                "If-None-Match"
            ] = _clean(
                etag
            )

        if _clean(
            last_modified
        ):
            headers[
                "If-Modified-Since"
            ] = _clean(
                last_modified
            )

        request = (
            urllib.request.Request(
                feed_url,
                headers=headers,
            )
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout,
            ) as response:
                raw = response.read(
                    self.max_feed_bytes
                    + 1
                )

                if (
                    len(raw)
                    > self.max_feed_bytes
                ):
                    raise RSSFetchError(
                        "Feed exceeded max_feed_bytes; "
                        "refusing silent truncation."
                    )

                content_type = _clean(
                    response.headers.get(
                        "Content-Type"
                    )
                )

                final_url = (
                    _canonical_url(
                        response.geturl()
                    )
                )

                parsed = (
                    self.parse_feed(
                        raw,
                        feed_url=(
                            final_url
                        ),
                    )
                )

                parsed[
                    "fetch"
                ] = {
                    "requested_url": (
                        feed_url
                    ),
                    "final_url": (
                        final_url
                    ),
                    "status_code": int(
                        getattr(
                            response,
                            "status",
                            200,
                        )
                    ),
                    "content_type": (
                        content_type
                    ),
                    "etag": _clean(
                        response.headers.get(
                            "ETag"
                        )
                    ),
                    "last_modified": (
                        _clean(
                            response.headers.get(
                                "Last-Modified"
                            )
                        )
                    ),
                    "content_sha256": (
                        hashlib.sha256(
                            raw
                        ).hexdigest()
                    ),
                    "not_modified": (
                        False
                    ),
                }

                return parsed

        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return {
                    "feed": {
                        "feed_id": (
                            _stable_id(
                                "FEED",
                                {
                                    "url": (
                                        feed_url
                                    )
                                },
                            )
                        ),
                        "url": (
                            feed_url
                        ),
                    },
                    "items": [],
                    "fetch": {
                        "requested_url": (
                            feed_url
                        ),
                        "final_url": (
                            feed_url
                        ),
                        "status_code": 304,
                        "etag": (
                            _clean(
                                etag
                            )
                        ),
                        "last_modified": (
                            _clean(
                                last_modified
                            )
                        ),
                        "not_modified": (
                            True
                        ),
                    },
                    "metadata": {
                        "service_version": (
                            SERVICE_VERSION
                        ),
                        "feed_item_is_grounding_evidence": (
                            False
                        ),
                        "feed_item_is_source": (
                            False
                        ),
                        "source_identity_owned_here": (
                            False
                        ),
                    },
                }

            raise RSSFetchError(
                "RSS fetch returned HTTP "
                f"{exc.code}."
            ) from exc

        except urllib.error.URLError as exc:
            raise RSSFetchError(
                "Could not fetch RSS feed: "
                f"{exc.reason}"
            ) from exc

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def parse_feed(
        self,
        xml_content: bytes | str,
        *,
        feed_url: str = "",
    ) -> Dict[str, Any]:
        if isinstance(
            xml_content,
            str,
        ):
            raw = (
                xml_content.encode(
                    "utf-8"
                )
            )

        elif isinstance(
            xml_content,
            (
                bytes,
                bytearray,
            ),
        ):
            raw = bytes(
                xml_content
            )

        else:
            raise RSSValidationError(
                "xml_content must be bytes or str."
            )

        try:
            root = ET.fromstring(
                raw
            )

        except ET.ParseError as exc:
            raise RSSValidationError(
                "Invalid RSS/Atom XML."
            ) from exc

        root_name = (
            _local_name(
                root.tag
            )
        )

        if root_name == "feed":
            (
                feed_meta,
                item_elements,
            ) = self._parse_atom_root(
                root
            )

            feed_type = "atom"

        else:
            (
                feed_meta,
                item_elements,
            ) = self._parse_rss_root(
                root
            )

            feed_type = "rss"

        feed_url = (
            _canonical_url(
                feed_url
            )
        )

        feed_link = (
            _canonical_url(
                feed_meta.get(
                    "link"
                )
                or feed_url
            )
        )

        feed_id = (
            _stable_id(
                "FEED",
                {
                    "url": (
                        feed_link
                        or feed_url
                    ),
                    "title": (
                        feed_meta.get(
                            "title",
                            "",
                        )
                    ),
                },
            )
        )

        items: list[
            Dict[
                str,
                Any,
            ]
        ] = []

        seen: set[
            str
        ] = set()

        for element in (
            item_elements
        ):
            item = (
                self._parse_atom_item(
                    element,
                    feed_id,
                )
                if feed_type
                == "atom"
                else self._parse_rss_item(
                    element,
                    feed_id,
                )
            )

            if (
                item[
                    "item_id"
                ]
                in seen
            ):
                continue

            seen.add(
                item[
                    "item_id"
                ]
            )

            items.append(
                item
            )

        items.sort(
            key=lambda item: (
                item.get(
                    "published_at"
                )
                or "",
                item[
                    "item_id"
                ],
            ),
            reverse=True,
        )

        return {
            "feed": {
                "feed_id": (
                    feed_id
                ),
                "feed_type": (
                    feed_type
                ),
                "url": (
                    feed_link
                    or feed_url
                ),
                "title": (
                    _clean(
                        feed_meta.get(
                            "title"
                        )
                    )
                ),
                "description": (
                    _clean(
                        feed_meta.get(
                            "description"
                        )
                    )
                ),
                "language": (
                    _clean(
                        feed_meta.get(
                            "language"
                        )
                    )
                ),
            },
            "items": (
                items
            ),
            "metadata": {
                "service_version": (
                    SERVICE_VERSION
                ),
                "item_count": (
                    len(
                        items
                    )
                ),
                "search_result_is_evidence": (
                    False
                ),
                "feed_item_is_grounding_evidence": (
                    False
                ),
                "feed_item_is_source": (
                    False
                ),
                "source_identity_owned_here": (
                    False
                ),
                "llm_called": (
                    False
                ),
                "paid_api_used": (
                    False
                ),
            },
        }

    # ------------------------------------------------------------------
    # Article / source-fetch handoff
    # ------------------------------------------------------------------

    def to_article_payload(
        self,
        item: Mapping[str, Any],
        *,
        feed_title: str = "",
        require_full_content: bool = True,
    ) -> Dict[str, Any]:
        """
        Build an ArticleService payload only when usable article content is
        already present in the feed.

        RSSService never supplies source_id. ArticleService owns SOURCE-*.

        Feed summary remains discovery metadata and is never silently used as
        Article.content.
        """

        if not isinstance(
            item,
            Mapping,
        ):
            raise RSSValidationError(
                "item must be an object."
            )

        prohibited = (
            _contains_prohibited_field(
                item
            )
        )

        if prohibited:
            raise RSSValidationError(
                "Feed item contains prohibited "
                "private/raw reasoning field at "
                f"{prohibited!r}."
            )

        content = _clean(
            item.get(
                "content"
            )
        )

        if (
            require_full_content
            and not content
        ):
            raise RSSValidationError(
                "Feed item does not contain usable full content; "
                "fetch the source URL first."
            )

        return {
            "url": (
                _clean(
                    item.get(
                        "url"
                    )
                )
            ),
            "title": (
                _clean(
                    item.get(
                        "title"
                    )
                )
            ),
            "content": (
                content
            ),
            "source_name": (
                _clean(
                    feed_title
                    or item.get(
                        "feed_title"
                    )
                )
            ),
            "published_at": (
                _clean(
                    item.get(
                        "published_at"
                    )
                )
            ),
            "raw_metadata": {
                "feed_id": (
                    _clean(
                        item.get(
                            "feed_id"
                        )
                    )
                ),
                "feed_item_id": (
                    _clean(
                        item.get(
                            "item_id"
                        )
                    )
                ),
                "feed_guid": (
                    _clean(
                        item.get(
                            "guid"
                        )
                    )
                ),
                "author": (
                    _clean(
                        item.get(
                            "author"
                        )
                    )
                ),
                "categories": list(
                    item.get(
                        "categories"
                    )
                    or []
                ),
                "feed_summary": (
                    _clean(
                        item.get(
                            "summary"
                        )
                    )
                ),
                "feed_updated_at": (
                    _clean(
                        item.get(
                            "updated_at"
                        )
                    )
                ),
                "feed_provided_full_content": (
                    bool(
                        content
                    )
                ),
                "feed_item_is_grounding_evidence": (
                    False
                ),
                "feed_item_is_source": (
                    False
                ),
                "source_identity_owner": (
                    "ArticleService"
                ),
            },
        }

    def to_source_fetch_request(
        self,
        item: Mapping[str, Any],
        *,
        feed_title: str = "",
    ) -> Dict[str, Any]:
        """
        Build a SourceFetchService discovery handoff for a feed item whose
        article body is not present.

        This object is not a SOURCE-* record and is not evidence.
        """

        if not isinstance(
            item,
            Mapping,
        ):
            raise RSSValidationError(
                "item must be an object."
            )

        url = _canonical_url(
            item.get(
                "url"
            )
            or ""
        )

        if not url:
            raise RSSValidationError(
                "Feed item has no HTTP(S) URL that "
                "SourceFetchService can fetch."
            )

        parts = (
            urllib.parse.urlsplit(
                url
            )
        )

        if (
            parts.scheme.casefold()
            not in {
                "http",
                "https",
            }
        ):
            raise RSSValidationError(
                "Feed item source URL must be HTTP(S)."
            )

        return {
            "discovery_type": (
                "rss_feed_item"
            ),
            "url": (
                url
            ),
            "title_hint": (
                _clean(
                    item.get(
                        "title"
                    )
                )
            ),
            "published_at_hint": (
                _clean(
                    item.get(
                        "published_at"
                    )
                )
            ),
            "source_name_hint": (
                _clean(
                    feed_title
                    or item.get(
                        "feed_title"
                    )
                )
            ),
            "discovery_metadata": {
                "feed_id": (
                    _clean(
                        item.get(
                            "feed_id"
                        )
                    )
                ),
                "feed_item_id": (
                    _clean(
                        item.get(
                            "item_id"
                        )
                    )
                ),
                "feed_guid": (
                    _clean(
                        item.get(
                            "guid"
                        )
                    )
                ),
                "feed_summary": (
                    _clean(
                        item.get(
                            "summary"
                        )
                    )
                ),
                "author": (
                    _clean(
                        item.get(
                            "author"
                        )
                    )
                ),
                "categories": list(
                    item.get(
                        "categories"
                    )
                    or []
                ),
                "is_grounding_evidence": (
                    False
                ),
            },
        }

    def partition_items(
        self,
        *,
        parsed_feed: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """
        Partition parsed feed items into:

            article_ready_items
                feed supplied usable full content

            source_fetch_required_items
                URL exists but content is absent

            unusable_items
                neither usable full content nor fetchable URL
        """

        if not isinstance(
            parsed_feed,
            Mapping,
        ):
            raise RSSValidationError(
                "parsed_feed must be an object."
            )

        feed = (
            parsed_feed.get(
                "feed"
            )
            or {}
        )

        items = (
            parsed_feed.get(
                "items"
            )
            or []
        )

        if not isinstance(
            items,
            list,
        ):
            raise RSSValidationError(
                "parsed_feed.items must be a list."
            )

        feed_title = _clean(
            feed.get(
                "title"
            )
            if isinstance(
                feed,
                Mapping,
            )
            else ""
        )

        article_ready = []
        fetch_required = []
        unusable = []

        for (
            index,
            item,
        ) in enumerate(
            items
        ):
            if not isinstance(
                item,
                Mapping,
            ):
                unusable.append(
                    {
                        "index": (
                            index
                        ),
                        "reason": (
                            "item_not_object"
                        ),
                    }
                )

                continue

            enriched = {
                **dict(
                    item
                ),
                "feed_title": (
                    feed_title
                ),
            }

            if _clean(
                item.get(
                    "content"
                )
            ):
                article_ready.append(
                    enriched
                )

                continue

            url = _canonical_url(
                item.get(
                    "url"
                )
                or ""
            )

            if url:
                enriched[
                    "url"
                ] = url

                fetch_required.append(
                    enriched
                )

            else:
                unusable.append(
                    {
                        "index": (
                            index
                        ),
                        "item_id": (
                            _clean(
                                item.get(
                                    "item_id"
                                )
                            )
                        ),
                        "reason": (
                            "no_full_content_and_no_url"
                        ),
                    }
                )

        return {
            "article_ready_items": (
                article_ready
            ),
            "source_fetch_required_items": (
                fetch_required
            ),
            "unusable_items": (
                unusable
            ),
            "article_ready_count": (
                len(
                    article_ready
                )
            ),
            "source_fetch_required_count": (
                len(
                    fetch_required
                )
            ),
            "unusable_count": (
                len(
                    unusable
                )
            ),
        }

    def ingest_items_to_article_service(
        self,
        *,
        parsed_feed: Mapping[str, Any],
        article_service: Any,
    ) -> Dict[str, Any]:
        """
        Persist only feed items containing usable full content.

        Summary/link-only items are NOT persisted as Articles.

        They are returned as explicit source_fetch_requests so the orchestrator
        can route them through SourceFetchService first.
        """

        if not hasattr(
            article_service,
            "upsert_article",
        ):
            raise RSSValidationError(
                "article_service must expose "
                "upsert_article(...)."
            )

        partition = (
            self.partition_items(
                parsed_feed=(
                    parsed_feed
                )
            )
        )

        feed = (
            parsed_feed.get(
                "feed"
            )
            or {}
        )

        feed_title = _clean(
            feed.get(
                "title"
            )
            if isinstance(
                feed,
                Mapping,
            )
            else ""
        )

        created = 0
        updated = 0
        source_ids = []

        for item in (
            partition[
                "article_ready_items"
            ]
        ):
            payload = (
                self.to_article_payload(
                    item,
                    feed_title=(
                        feed_title
                    ),
                    require_full_content=True,
                )
            )

            result = (
                article_service
                .upsert_article(
                    **payload
                )
            )

            source_id = _clean(
                getattr(
                    result,
                    "source_id",
                    "",
                )
                or (
                    result.get(
                        "source_id"
                    )
                    if isinstance(
                        result,
                        Mapping,
                    )
                    else ""
                )
            )

            if source_id:
                if not (
                    source_id
                    .upper()
                    .startswith(
                        "SOURCE-"
                    )
                ):
                    raise RSSValidationError(
                        "ArticleService returned "
                        "a non-canonical source_id."
                    )

                source_ids.append(
                    source_id
                )

            was_created = bool(
                result.get(
                    "created",
                    False,
                )
                if isinstance(
                    result,
                    Mapping,
                )
                else getattr(
                    result,
                    "created",
                    False,
                )
            )

            created += int(
                was_created
            )

            updated += int(
                not was_created
            )

        source_fetch_requests = [
            self.to_source_fetch_request(
                item,
                feed_title=(
                    feed_title
                ),
            )
            for item
            in partition[
                "source_fetch_required_items"
            ]
        ]

        return {
            "item_count": (
                len(
                    parsed_feed.get(
                        "items"
                    )
                    or []
                )
            ),
            "article_ready_count": (
                partition[
                    "article_ready_count"
                ]
            ),
            "created_count": (
                created
            ),
            "updated_count": (
                updated
            ),
            "source_fetch_required_count": (
                len(
                    source_fetch_requests
                )
            ),
            "source_fetch_requests": (
                source_fetch_requests
            ),
            "unusable_items": (
                partition[
                    "unusable_items"
                ]
            ),
            "source_ids": (
                source_ids
            ),
            "feed_items_are_grounding_evidence": (
                False
            ),
            "rss_service_created_source_ids": (
                False
            ),
        }

    # ------------------------------------------------------------------
    # RSS root parsing
    # ------------------------------------------------------------------

    def _parse_rss_root(
        self,
        root: ET.Element,
    ):
        channel = None

        for child in root.iter():
            if (
                _local_name(
                    child.tag
                )
                == "channel"
            ):
                channel = child
                break

        container = (
            channel
            or root
        )

        meta = {
            "title": (
                _child_text(
                    container,
                    (
                        "title",
                    ),
                )
            ),
            "link": (
                _child_text(
                    container,
                    (
                        "link",
                    ),
                )
            ),
            "description": (
                _child_text(
                    container,
                    (
                        "description",
                        "subtitle",
                    ),
                )
            ),
            "language": (
                _child_text(
                    container,
                    (
                        "language",
                    ),
                )
            ),
        }

        items = [
            child
            for child
            in root.iter()
            if (
                _local_name(
                    child.tag
                )
                == "item"
            )
        ]

        return (
            meta,
            items,
        )

    def _parse_atom_root(
        self,
        root: ET.Element,
    ):
        link = ""

        for child in list(
            root
        ):
            if (
                _local_name(
                    child.tag
                )
                == "link"
            ):
                href = _clean(
                    child.attrib.get(
                        "href"
                    )
                )

                rel = _clean(
                    child.attrib.get(
                        "rel"
                    )
                    or "alternate"
                )

                if (
                    href
                    and rel
                    in {
                        "alternate",
                        "",
                    }
                ):
                    link = href
                    break

        meta = {
            "title": (
                _child_text(
                    root,
                    (
                        "title",
                    ),
                )
            ),
            "link": (
                link
            ),
            "description": (
                _child_text(
                    root,
                    (
                        "subtitle",
                    ),
                )
            ),
            "language": (
                _clean(
                    root.attrib.get(
                        "{http://www.w3.org/XML/1998/namespace}lang"
                    )
                )
            ),
        }

        items = [
            child
            for child
            in list(
                root
            )
            if (
                _local_name(
                    child.tag
                )
                == "entry"
            )
        ]

        return (
            meta,
            items,
        )

    # ------------------------------------------------------------------
    # RSS item parsing
    # ------------------------------------------------------------------

    def _parse_rss_item(
        self,
        element: ET.Element,
        feed_id: str,
    ) -> Dict[str, Any]:
        title = (
            _child_text(
                element,
                (
                    "title",
                ),
            )
        )

        link = (
            _canonical_url(
                _child_text(
                    element,
                    (
                        "link",
                    ),
                )
            )
        )

        guid = (
            _child_text(
                element,
                (
                    "guid",
                    "id",
                ),
            )
        )

        summary = (
            _strip_html(
                _child_text(
                    element,
                    (
                        "description",
                        "summary",
                    ),
                )
            )
        )

        content = ""

        for child in list(
            element
        ):
            if (
                _local_name(
                    child.tag
                )
                in {
                    "encoded",
                    "content",
                }
            ):
                content = (
                    _strip_html(
                        "".join(
                            child.itertext()
                        )
                    )
                )

                if content:
                    break

        published = (
            _parse_time(
                _child_text(
                    element,
                    (
                        "pubdate",
                        "published",
                        "updated",
                        "date",
                    ),
                )
            )
        )

        author = (
            _child_text(
                element,
                (
                    "author",
                    "creator",
                ),
            )
        )

        categories = (
            _all_child_text(
                element,
                "category",
            )
        )

        identity = (
            guid
            or link
            or f"{title}|{published}"
        )

        return {
            "item_id": (
                _stable_id(
                    "RSSITEM",
                    {
                        "feed_id": (
                            feed_id
                        ),
                        "identity": (
                            identity
                        ),
                    },
                )
            ),
            "feed_id": (
                feed_id
            ),
            "title": (
                title
            ),
            "url": (
                link
            ),
            "guid": (
                guid
            ),
            "summary": (
                summary
            ),
            "content": (
                content
            ),
            "requires_source_fetch": (
                not bool(
                    content
                )
            ),
            "published_at": (
                published
            ),
            "updated_at": (
                ""
            ),
            "author": (
                author
            ),
            "categories": (
                categories
            ),
            "is_grounding_evidence": (
                False
            ),
            "is_source": (
                False
            ),
        }

    # ------------------------------------------------------------------
    # Atom item parsing
    # ------------------------------------------------------------------

    def _parse_atom_item(
        self,
        element: ET.Element,
        feed_id: str,
    ) -> Dict[str, Any]:
        title = (
            _child_text(
                element,
                (
                    "title",
                ),
            )
        )

        entry_id = (
            _child_text(
                element,
                (
                    "id",
                ),
            )
        )

        link = ""

        for child in list(
            element
        ):
            if (
                _local_name(
                    child.tag
                )
                != "link"
            ):
                continue

            href = _clean(
                child.attrib.get(
                    "href"
                )
            )

            rel = _clean(
                child.attrib.get(
                    "rel"
                )
                or "alternate"
            )

            if (
                href
                and rel
                in {
                    "alternate",
                    "",
                }
            ):
                link = (
                    _canonical_url(
                        href
                    )
                )

                break

        summary = (
            _strip_html(
                _child_text(
                    element,
                    (
                        "summary",
                    ),
                )
            )
        )

        content = (
            _strip_html(
                _child_text(
                    element,
                    (
                        "content",
                    ),
                )
            )
        )

        published = (
            _parse_time(
                _child_text(
                    element,
                    (
                        "published",
                        "updated",
                    ),
                )
            )
        )

        updated = (
            _parse_time(
                _child_text(
                    element,
                    (
                        "updated",
                    ),
                )
            )
        )

        author = ""

        for child in list(
            element
        ):
            if (
                _local_name(
                    child.tag
                )
                == "author"
            ):
                author = (
                    _child_text(
                        child,
                        (
                            "name",
                            "email",
                        ),
                    )
                )

                break

        categories = []

        for child in list(
            element
        ):
            if (
                _local_name(
                    child.tag
                )
                == "category"
            ):
                term = _clean(
                    child.attrib.get(
                        "term"
                    )
                    or "".join(
                        child.itertext()
                    )
                )

                if term:
                    categories.append(
                        term
                    )

        identity = (
            entry_id
            or link
            or f"{title}|{published}"
        )

        return {
            "item_id": (
                _stable_id(
                    "RSSITEM",
                    {
                        "feed_id": (
                            feed_id
                        ),
                        "identity": (
                            identity
                        ),
                    },
                )
            ),
            "feed_id": (
                feed_id
            ),
            "title": (
                title
            ),
            "url": (
                link
            ),
            "guid": (
                entry_id
            ),
            "summary": (
                summary
            ),
            "content": (
                content
            ),
            "requires_source_fetch": (
                not bool(
                    content
                )
            ),
            "published_at": (
                published
            ),
            "updated_at": (
                updated
            ),
            "author": (
                author
            ),
            "categories": (
                categories
            ),
            "is_grounding_evidence": (
                False
            ),
            "is_source": (
                False
            ),
        }


# Narrow class-name compatibility alias.
# It does not restore old SOURCE-* ownership behavior.
FeedService = RSSService


__all__ = [
    "SERVICE_VERSION",
    "DEFAULT_USER_AGENT",
    "RSSServiceError",
    "RSSValidationError",
    "RSSFetchError",
    "RSSService",
    "FeedService",
]