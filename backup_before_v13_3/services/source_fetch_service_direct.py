from __future__ import annotations

"""
Direct HTTP source retrieval for AI Event Intelligence.

Recommended location:
    services/source_fetch_service_direct.py

This module does ONE thing: attempt to fetch and extract the publisher/source
page directly. It contains no GDELT fallback logic. The public wrapper lives in
services/source_fetch_service.py.

Epistemic boundary:
    SEARCH RESULT != FETCHED SOURCE != EVIDENCE

A successful fetch is still only a SOURCE. Downstream extraction, provenance,
grounding and evidence admission remain responsible for deciding what may
support a claim.

Free-only:
    - Python standard library HTTP
    - no paid scraping API
    - no proxy service
    - no browser automation
"""

import gzip
import hashlib
import html
import inspect
import ipaddress
import json
import re
import socket
import time
import unicodedata
import zlib
from html.parser import HTMLParser
from typing import Any, Callable, Dict, Mapping, Optional, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

SERVICE_VERSION = "source-fetch-direct-v2.0-free-only"

DEFAULT_USER_AGENT = (
    "AIEventIntelligenceCaseStudy/2.0 "
    "(bounded direct source retrieval)"
)

DEFAULT_ALLOWED_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "application/json",
    "application/xml",
    "text/xml",
    "application/pdf",
}

TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SourceFetchError(RuntimeError):
    """Base exception for direct source retrieval."""


class SourceFetchValidationError(SourceFetchError):
    """Raised when request/transport data violates the service contract."""


class SourceFetchNetworkError(SourceFetchError):
    """Raised for HTTP/network failures."""


class SourceFetchTimeoutError(SourceFetchNetworkError):
    """Raised when retrieval times out."""


class SourceTooLargeError(SourceFetchError):
    """Raised when a source exceeds max_download_bytes."""


class UnsupportedContentTypeError(SourceFetchError):
    """Raised when the media type is unsupported."""


class UnsafeSourceURLError(SourceFetchValidationError):
    """Raised when a URL fails the SSRF/public-network safety policy."""


class RedirectLimitError(SourceFetchNetworkError):
    """Raised when the redirect limit is exceeded."""


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def _clean(value: Any) -> str:
    if value is None:
        return ""

    return " ".join(
        str(value).split()
    ).strip()


def _sha256_bytes(
    value: bytes,
) -> str:

    return hashlib.sha256(
        value
    ).hexdigest()


def _stable_id(
    prefix: str,
    payload: Mapping[str, Any],
    length: int = 24,
) -> str:

    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")

    digest = (
        hashlib.sha256(raw)
        .hexdigest()[:length]
        .upper()
    )

    return f"{prefix}-{digest}"


def _canonicalize_url(
    url: str,
) -> str:

    url = _clean(url)

    if not url:
        return ""

    try:
        parts = urlsplit(url)
    except ValueError:
        return url

    if (
        parts.scheme.casefold()
        not in {"http", "https"}
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
            or lowered in TRACKING_PARAMETERS
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


def _parse_content_type(
    raw: str,
) -> tuple[str, str]:

    raw = _clean(raw)

    if not raw:
        return "", ""

    parts = [
        part.strip()
        for part in raw.split(";")
        if part.strip()
    ]

    mime = parts[0].casefold()
    charset = ""

    for part in parts[1:]:
        if "=" not in part:
            continue

        key, value = part.split(
            "=",
            1,
        )

        if (
            key.strip().casefold()
            == "charset"
        ):
            charset = (
                value.strip()
                .strip('"')
                .strip("'")
            )

    return mime, charset


def _decode_text(
    body: bytes,
    *,
    charset: str,
) -> tuple[str, str]:

    candidates = []

    if charset:
        candidates.append(
            charset
        )

    candidates.extend(
        [
            "utf-8",
            "utf-8-sig",
            "cp1252",
            "latin-1",
        ]
    )

    seen = set()

    for encoding in candidates:
        key = (
            encoding.casefold()
        )

        if key in seen:
            continue

        seen.add(key)

        try:
            return (
                body.decode(
                    encoding
                ),
                encoding,
            )

        except (
            UnicodeDecodeError,
            LookupError,
        ):
            continue

    return (
        body.decode(
            "utf-8",
            errors="replace",
        ),
        "utf-8-replace",
    )


def _normalize_headers(
    headers: Any,
) -> Dict[str, str]:

    if headers is None:
        return {}

    if (
        isinstance(
            headers,
            Mapping,
        )
        or hasattr(
            headers,
            "items",
        )
    ):
        items = headers.items()

    else:
        raise SourceFetchValidationError(
            "Transport headers must be mapping-like."
        )

    result: Dict[
        str,
        str,
    ] = {}

    for key, value in items:
        key_text = (
            _clean(key)
            .casefold()
        )

        value_text = (
            _clean(value)
        )

        if key_text:
            result[
                key_text
            ] = value_text

    return result


def _decompress_body(
    body: bytes,
    *,
    content_encoding: str,
    max_output_bytes: int,
) -> bytes:

    encoding = (
        _clean(
            content_encoding
        )
        .casefold()
    )

    if (
        not encoding
        or encoding == "identity"
    ):
        return body

    if encoding == "gzip":
        try:
            data = gzip.decompress(
                body
            )

        except OSError as exc:
            raise SourceFetchNetworkError(
                "Invalid gzip response body."
            ) from exc

    elif encoding == "deflate":
        try:
            data = zlib.decompress(
                body
            )

        except zlib.error:
            try:
                data = zlib.decompress(
                    body,
                    -zlib.MAX_WBITS,
                )

            except zlib.error as exc:
                raise SourceFetchNetworkError(
                    "Invalid deflate response body."
                ) from exc

    else:
        raise SourceFetchNetworkError(
            f"Unsupported Content-Encoding={encoding!r}."
        )

    if (
        len(data)
        > max_output_bytes
    ):
        raise SourceTooLargeError(
            "Decompressed response exceeds max_download_bytes."
        )

    return data


def _is_ip_globally_routable(
    address: str,
) -> bool:

    try:
        return bool(
            ipaddress
            .ip_address(address)
            .is_global
        )

    except ValueError:
        return False


def _assert_safe_public_http_url(
    url: str,
    *,
    allow_private_networks: bool,
    resolve_dns: bool = True,
) -> None:

    try:
        parts = urlsplit(
            url
        )

    except ValueError as exc:
        raise UnsafeSourceURLError(
            "Malformed source URL."
        ) from exc

    scheme = (
        parts.scheme.casefold()
    )

    if scheme not in {
        "http",
        "https",
    }:
        raise UnsafeSourceURLError(
            "Only http:// and https:// source URLs are allowed."
        )

    host = _clean(
        parts.hostname
    )

    if not host:
        raise UnsafeSourceURLError(
            "Source URL is missing a hostname."
        )

    if allow_private_networks:
        return

    try:
        literal = (
            ipaddress
            .ip_address(host)
        )

    except ValueError:
        literal = None

    if literal is not None:
        if not literal.is_global:
            raise UnsafeSourceURLError(
                "Private/loopback/link-local/reserved source URLs are blocked."
            )

        return

    if not resolve_dns:
        return

    try:
        addresses = (
            socket.getaddrinfo(
                host,
                parts.port
                or (
                    443
                    if scheme == "https"
                    else 80
                ),
                type=socket.SOCK_STREAM,
            )
        )

    except socket.gaierror as exc:
        raise SourceFetchNetworkError(
            f"Could not resolve source hostname {host!r}."
        ) from exc

    resolved_ips = {
        item[4][0]
        for item in addresses
        if (
            item
            and len(item) >= 5
            and item[4]
        )
    }

    if not resolved_ips:
        raise SourceFetchNetworkError(
            f"Hostname {host!r} resolved to no addresses."
        )

    if any(
        not _is_ip_globally_routable(
            ip
        )
        for ip in resolved_ips
    ):
        raise UnsafeSourceURLError(
            "Source hostname resolves to a non-public address; "
            "request blocked by SSRF policy."
        )


# ---------------------------------------------------------------------------
# HTML extraction
# ---------------------------------------------------------------------------


class _HTMLSourceParser(
    HTMLParser
):
    """
    Bounded deterministic main/article text extractor.
    """

    HARD_SKIP_TAGS = {
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "nav",
        "form",
        "button",
        "select",
        "option",
        "iframe",
        "dialog",
    }

    VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    BLOCK_TAGS = {
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }

    BOILERPLATE_HINTS = {
        "nav",
        "navigation",
        "menu",
        "breadcrumb",
        "footer",
        "sidebar",
        "cookie",
        "consent",
        "share",
        "social",
        "subscribe",
        "newsletter",
        "related",
        "recommended",
        "pagination",
        "pager",
        "toc",
        "table-of-contents",
        "table_of_contents",
        "promo",
        "promotion",
        "modal",
        "popup",
        "toolbar",
        "utility",
        "skip-link",
        "skip_link",
        "masthead",
        "site-header",
        "site_header",
        "site-footer",
        "site_footer",
    }

    VISUAL_FURNITURE_HINTS = {
        "timeline",
        "roadmap",
        "infographic",
        "carousel",
        "slider",
        "swiper",
        "stepper",
        "milestones",
        "milestone-track",
    }

    def __init__(
        self,
    ) -> None:

        super().__init__(
            convert_charrefs=True
        )

        self.skip_stack: list[
            str
        ] = []

        self.in_title = False

        self.title_parts: list[
            str
        ] = []

        self.all_parts: list[
            str
        ] = []

        self.main_parts: list[
            str
        ] = []

        self.article_parts: list[
            str
        ] = []

        self.main_depth = 0
        self.article_depth = 0
        self.anchor_depth = 0

        self.anchor_parts: list[
            str
        ] = []

        self.anchor_texts: list[
            str
        ] = []

        self.metadata: Dict[
            str,
            str,
        ] = {}

        self.canonical_href = ""

        self.extraction_scope = (
            "body_cleaned"
        )

        self.page_type = (
            "article_or_content"
        )

        self.listing_page_score = 0.0

    @classmethod
    def _looks_like_boilerplate_container(
        cls,
        tag: str,
        attrs_map: Mapping[
            str,
            str,
        ],
    ) -> bool:

        if tag in {
            "footer",
            "aside",
        }:
            return True

        haystack = " ".join(
            _clean(
                attrs_map.get(
                    key,
                    "",
                )
            ).casefold()
            for key in (
                "id",
                "class",
                "role",
                "aria-label",
                "data-testid",
            )
        )

        if not haystack:
            return False

        tokens = set(
            re.findall(
                r"[a-z0-9_-]+",
                haystack,
            )
        )

        if (
            tokens
            & cls.BOILERPLATE_HINTS
        ):
            return True

        if tag in {
            "div",
            "section",
            "figure",
            "ul",
            "ol",
        }:
            if (
                tokens
                & cls.VISUAL_FURNITURE_HINTS
            ):
                return True

        return any(
            hint in haystack
            for hint
            in cls.BOILERPLATE_HINTS
        )

    def _append_boundary(
        self,
    ) -> None:

        self.all_parts.append(
            "\n"
        )

        if self.main_depth > 0:
            self.main_parts.append(
                "\n"
            )

        if self.article_depth > 0:
            self.article_parts.append(
                "\n"
            )

    def handle_starttag(
        self,
        tag: str,
        attrs: Sequence[
            tuple[
                str,
                Optional[str],
            ]
        ],
    ) -> None:

        tag = tag.casefold()

        attrs_map = {
            _clean(key).casefold():
                _clean(value)
            for key, value in attrs
            if key
        }

        if (
            not self.skip_stack
            and tag == "title"
        ):
            self.in_title = True

        if (
            not self.skip_stack
            and tag == "meta"
        ):
            key = (
                attrs_map.get(
                    "property"
                )
                or attrs_map.get(
                    "name"
                )
                or attrs_map.get(
                    "itemprop"
                )
                or ""
            ).casefold()

            content = (
                attrs_map.get(
                    "content",
                    "",
                )
            )

            if key and content:
                self.metadata.setdefault(
                    key,
                    content,
                )

        if (
            not self.skip_stack
            and tag == "link"
        ):
            rel = (
                attrs_map
                .get(
                    "rel",
                    "",
                )
                .casefold()
            )

            href = (
                attrs_map.get(
                    "href",
                    "",
                )
            )

            if (
                "canonical"
                in rel.split()
                and href
            ):
                self.canonical_href = (
                    href
                )

        if self.skip_stack:
            if (
                tag
                not in self.VOID_TAGS
            ):
                self.skip_stack.append(
                    tag
                )

            return

        if (
            tag in self.HARD_SKIP_TAGS
            or self
            ._looks_like_boilerplate_container(
                tag,
                attrs_map,
            )
        ):
            if (
                tag
                not in self.VOID_TAGS
            ):
                self.skip_stack.append(
                    tag
                )

            return

        if tag == "main":
            self.main_depth += 1

        if tag == "article":
            self.article_depth += 1

        if tag == "a":
            self.anchor_depth += 1
            self.anchor_parts = []

        if tag in self.BLOCK_TAGS:
            self._append_boundary()

    def handle_endtag(
        self,
        tag: str,
    ) -> None:

        tag = tag.casefold()

        if self.skip_stack:
            if tag in self.skip_stack:
                while self.skip_stack:
                    opened = (
                        self.skip_stack.pop()
                    )

                    if opened == tag:
                        break

            return

        if tag == "title":
            self.in_title = False

        if (
            tag == "a"
            and self.anchor_depth > 0
        ):
            text = _clean(
                " ".join(
                    self.anchor_parts
                )
            )

            if text:
                self.anchor_texts.append(
                    text
                )

            self.anchor_depth -= 1
            self.anchor_parts = []

        if tag in self.BLOCK_TAGS:
            self._append_boundary()

        if (
            tag == "article"
            and self.article_depth > 0
        ):
            self.article_depth -= 1

        if (
            tag == "main"
            and self.main_depth > 0
        ):
            self.main_depth -= 1

    def handle_data(
        self,
        data: str,
    ) -> None:

        if self.skip_stack:
            return

        text = _clean(
            data
        )

        if not text:
            return

        if self.in_title:
            self.title_parts.append(
                text
            )

        if self.anchor_depth > 0:
            self.anchor_parts.append(
                text
            )

        self.all_parts.extend(
            (
                text,
                " ",
            )
        )

        if self.main_depth > 0:
            self.main_parts.extend(
                (
                    text,
                    " ",
                )
            )

        if self.article_depth > 0:
            self.article_parts.extend(
                (
                    text,
                    " ",
                )
            )

    @staticmethod
    def _normalize_lines(
        parts: Sequence[str],
    ) -> list[str]:

        lines: list[
            str
        ] = []

        previous = ""

        for raw_line in (
            "".join(parts)
            .splitlines()
        ):
            line = _clean(
                raw_line
            )

            if not line:
                continue

            key = (
                line
                .casefold()
                .strip(
                    " .:;|-"
                )
            )

            if key == previous:
                continue

            if (
                len(line) <= 2
                and not re.search(
                    r"\d",
                    line,
                )
            ):
                continue

            if re.fullmatch(
                (
                    r"(?:learn more|read more|share|back to top|"
                    r"sign in|log in|subscribe|sign up|"
                    r"cookie settings|privacy settings|"
                    r"skip to (?:main )?content)"
                ),
                key,
                flags=re.I,
            ):
                continue

            lines.append(
                line
            )

            previous = key

        return lines

    @staticmethod
    def _substantial(
        lines: Sequence[str],
    ) -> bool:

        text = " ".join(
            lines
        )

        return (
            len(text) >= 220
            and len(
                re.findall(
                    r"\b\w+\b",
                    text,
                )
            )
            >= 35
        )

    def _classify_page_type(
        self,
        chosen: Sequence[str],
    ) -> tuple[
        str,
        float,
    ]:

        text = " ".join(
            chosen
        )

        words = max(
            1,
            len(
                re.findall(
                    r"\b\w+\b",
                    text,
                )
            ),
        )

        anchors = [
            _clean(x)
            for x
            in self.anchor_texts
            if _clean(x)
        ]

        anchor_words = sum(
            len(x.split())
            for x in anchors
        )

        anchor_density = min(
            1.0,
            anchor_words / words,
        )

        title = (
            _clean(
                " ".join(
                    self.title_parts
                )
            )
            .casefold()
        )

        score = 0.0

        if re.search(
            (
                r"\b(?:roundup|top \d+|best \d+|"
                r"monthly digest|monthly roundup)\b"
            ),
            title,
        ):
            score += 3.0

        if (
            len(
                set(
                    x.casefold()
                    for x
                    in anchors
                )
            )
            >= 12
        ):
            score += 1.0

        if anchor_density >= 0.38:
            score += 1.25

        if re.search(
            (
                r"\b(?:latest|more|related) "
                r"(?:stories|news|articles)|"
                r"(?:view|see|browse) all\b"
            ),
            text,
            flags=re.I,
        ):
            score += 1.0

        self.page_type = (
            "listing_or_roundup"
            if score >= 3.0
            else "article_or_content"
        )

        self.listing_page_score = (
            round(
                score,
                3,
            )
        )

        return (
            self.page_type,
            self.listing_page_score,
        )

    @property
    def title(
        self,
    ) -> str:

        return _clean(
            " ".join(
                self.title_parts
            )
        )

    @property
    def text(
        self,
    ) -> str:

        article_lines = (
            self._normalize_lines(
                self.article_parts
            )
        )

        main_lines = (
            self._normalize_lines(
                self.main_parts
            )
        )

        all_lines = (
            self._normalize_lines(
                self.all_parts
            )
        )

        if self._substantial(
            article_lines
        ):
            self.extraction_scope = (
                "article"
            )

            chosen = article_lines

        elif self._substantial(
            main_lines
        ):
            self.extraction_scope = (
                "main"
            )

            chosen = main_lines

        else:
            self.extraction_scope = (
                "body_cleaned"
            )

            chosen = all_lines

        self._classify_page_type(
            chosen
        )

        return "\n".join(
            chosen
        )


def _extract_html(
    raw_html: str,
    *,
    base_url: str,
) -> tuple[
    str,
    Dict[str, Any],
]:

    parser = (
        _HTMLSourceParser()
    )

    try:
        parser.feed(
            raw_html
        )

        parser.close()

    except Exception as exc:
        raise SourceFetchValidationError(
            "HTML parsing failed."
        ) from exc

    text = parser.text
    metadata = parser.metadata

    canonical_url = ""

    if parser.canonical_href:
        canonical_url = urljoin(
            base_url,
            parser.canonical_href,
        )

    title = (
        metadata.get(
            "og:title"
        )
        or metadata.get(
            "twitter:title"
        )
        or parser.title
    )

    author = (
        metadata.get(
            "author"
        )
        or metadata.get(
            "article:author"
        )
        or metadata.get(
            "byl"
        )
        or ""
    )

    published_at = (
        metadata.get(
            "article:published_time"
        )
        or metadata.get(
            "date"
        )
        or metadata.get(
            "datepublished"
        )
        or metadata.get(
            "publishdate"
        )
        or metadata.get(
            "pubdate"
        )
        or ""
    )

    description = (
        metadata.get(
            "og:description"
        )
        or metadata.get(
            "description"
        )
        or metadata.get(
            "twitter:description"
        )
        or ""
    )

    language = (
        metadata.get(
            "language"
        )
        or ""
    )

    return (
        text,
        {
            "title": (
                _clean(title)
            ),
            "author": (
                _clean(author)
            ),
            "published_at": (
                _clean(
                    published_at
                )
            ),
            "description": (
                _clean(
                    description
                )
            ),
            "declared_canonical_url": (
                _clean(
                    canonical_url
                )
            ),
            "language": (
                _clean(
                    language
                )
            ),
            "content_extraction_scope": (
                parser.extraction_scope
            ),
            "content_page_type": (
                parser.page_type
            ),
            "listing_page_score": (
                parser.listing_page_score
            ),
            "anchor_text_count": (
                len(
                    parser.anchor_texts
                )
            ),
            "distinct_anchor_text_count": (
                len(
                    set(
                        x.casefold()
                        for x
                        in parser.anchor_texts
                        if _clean(x)
                    )
                )
            ),
        },
    )


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------


class HTTPTransport(
    Protocol
):
    def fetch(
        self,
        *,
        url: str,
        timeout_seconds: float,
        max_download_bytes: int,
    ) -> Mapping[str, Any]:
        ...


class _SafeRedirectHandler(
    HTTPRedirectHandler
):
    def __init__(
        self,
        *,
        allow_private_networks: bool,
        max_redirects: int,
    ) -> None:

        super().__init__()

        self.allow_private_networks = bool(
            allow_private_networks
        )

        self.max_redirects = int(
            max_redirects
        )

        self.redirect_history: list[
            str
        ] = []

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Optional[Request]:

        if (
            len(
                self.redirect_history
            )
            >= self.max_redirects
        ):
            raise RedirectLimitError(
                "Maximum redirect count exceeded."
            )

        resolved = urljoin(
            req.full_url,
            newurl,
        )

        _assert_safe_public_http_url(
            resolved,
            allow_private_networks=(
                self.allow_private_networks
            ),
            resolve_dns=True,
        )

        self.redirect_history.append(
            resolved
        )

        return (
            super()
            .redirect_request(
                req,
                fp,
                code,
                msg,
                headers,
                resolved,
            )
        )


class UrllibHTTPTransport:
    def __init__(
        self,
        *,
        user_agent: str = (
            DEFAULT_USER_AGENT
        ),
        allow_private_networks: bool = False,
        max_redirects: int = 5,
    ) -> None:

        self.user_agent = (
            _clean(
                user_agent
            )
            or DEFAULT_USER_AGENT
        )

        self.allow_private_networks = bool(
            allow_private_networks
        )

        self.max_redirects = int(
            max_redirects
        )

        if self.max_redirects < 0:
            raise SourceFetchValidationError(
                "max_redirects must be >= 0."
            )

    def fetch(
        self,
        *,
        url: str,
        timeout_seconds: float,
        max_download_bytes: int,
    ) -> Mapping[str, Any]:

        _assert_safe_public_http_url(
            url,
            allow_private_networks=(
                self.allow_private_networks
            ),
            resolve_dns=True,
        )

        redirect_handler = (
            _SafeRedirectHandler(
                allow_private_networks=(
                    self.allow_private_networks
                ),
                max_redirects=(
                    self.max_redirects
                ),
            )
        )

        opener = build_opener(
            redirect_handler
        )

        request = Request(
            url=url,
            headers={
                "User-Agent": (
                    self.user_agent
                ),
                "Accept": (
                    "text/html,"
                    "application/xhtml+xml,"
                    "application/json,"
                    "text/plain,"
                    "application/xml,"
                    "application/pdf;q=0.9,"
                    "*/*;q=0.1"
                ),
                "Accept-Encoding": (
                    "gzip, deflate"
                ),
            },
            method="GET",
        )

        try:
            with opener.open(
                request,
                timeout=float(
                    timeout_seconds
                ),
            ) as response:

                final_url = (
                    response.geturl()
                )

                status_code = int(
                    getattr(
                        response,
                        "status",
                        response.getcode(),
                    )
                )

                headers = (
                    _normalize_headers(
                        response.headers
                    )
                )

                declared_length = (
                    headers.get(
                        "content-length"
                    )
                )

                if declared_length:
                    try:
                        declared_length_int = int(
                            declared_length
                        )

                    except ValueError:
                        declared_length_int = (
                            None
                        )

                    if (
                        declared_length_int
                        is not None
                        and declared_length_int
                        > max_download_bytes
                    ):
                        raise SourceTooLargeError(
                            "Response Content-Length exceeds "
                            "max_download_bytes."
                        )

                body = response.read(
                    max_download_bytes
                    + 1
                )

        except TimeoutError as exc:
            raise SourceFetchTimeoutError(
                "Source fetch timed out."
            ) from exc

        except HTTPError as exc:
            raise SourceFetchNetworkError(
                f"Source returned HTTP {exc.code}."
            ) from exc

        except URLError as exc:
            reason = getattr(
                exc,
                "reason",
                "",
            )

            if isinstance(
                reason,
                TimeoutError,
            ):
                raise SourceFetchTimeoutError(
                    "Source fetch timed out."
                ) from exc

            raise SourceFetchNetworkError(
                f"Source fetch failed: {_clean(reason)}"
            ) from exc

        if (
            len(body)
            > max_download_bytes
        ):
            raise SourceTooLargeError(
                "Response body exceeds max_download_bytes."
            )

        _assert_safe_public_http_url(
            final_url,
            allow_private_networks=(
                self.allow_private_networks
            ),
            resolve_dns=True,
        )

        body = _decompress_body(
            body,
            content_encoding=(
                headers.get(
                    "content-encoding",
                    "",
                )
            ),
            max_output_bytes=(
                max_download_bytes
            ),
        )

        return {
            "status_code": (
                status_code
            ),
            "final_url": (
                final_url
            ),
            "headers": headers,
            "body": body,
            "redirect_history": (
                list(
                    redirect_handler
                    .redirect_history
                )
            ),
        }


class CallableHTTPTransport:
    """
    Injectable transport for tests or another
    free/local transport.
    """

    def __init__(
        self,
        fetch_callable: Callable[
            ...,
            Any,
        ],
    ) -> None:

        if not callable(
            fetch_callable
        ):
            raise SourceFetchValidationError(
                "fetch_callable must be callable."
            )

        self.fetch_callable = (
            fetch_callable
        )

    def fetch(
        self,
        *,
        url: str,
        timeout_seconds: float,
        max_download_bytes: int,
    ) -> Mapping[str, Any]:

        func = (
            self.fetch_callable
        )

        try:
            signature = (
                inspect.signature(
                    func
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            result = func(
                url=url,
                timeout_seconds=(
                    timeout_seconds
                ),
                max_download_bytes=(
                    max_download_bytes
                ),
            )

        else:
            kwargs = {
                "url": url,
                "timeout_seconds": (
                    timeout_seconds
                ),
                "max_download_bytes": (
                    max_download_bytes
                ),
            }

            accepts_var_kw = any(
                parameter.kind
                == inspect.Parameter.VAR_KEYWORD
                for parameter
                in signature
                .parameters
                .values()
            )

            if not accepts_var_kw:
                kwargs = {
                    key: value
                    for key, value
                    in kwargs.items()
                    if key
                    in signature.parameters
                }

            result = func(
                **kwargs
            )

        if not isinstance(
            result,
            Mapping,
        ):
            raise SourceFetchValidationError(
                "CallableHTTPTransport must return a mapping."
            )

        return result


# ---------------------------------------------------------------------------
# Direct service
# ---------------------------------------------------------------------------


class SourceFetchService:
    def __init__(
        self,
        *,
        transport: Optional[
            HTTPTransport
        ] = None,
        max_download_bytes: int = (
            6_000_000
        ),
        allowed_content_types: Optional[
            Sequence[str]
        ] = None,
        document_text_extractor: Optional[
            Callable[..., str]
        ] = None,
        include_raw_text: bool = True,
    ) -> None:

        try:
            self.max_download_bytes = int(
                max_download_bytes
            )

        except (
            TypeError,
            ValueError,
        ) as exc:
            raise SourceFetchValidationError(
                "max_download_bytes must be an integer."
            ) from exc

        if (
            self.max_download_bytes
            <= 0
        ):
            raise SourceFetchValidationError(
                "max_download_bytes must be > 0."
            )

        self.transport = (
            transport
            or UrllibHTTPTransport()
        )

        if (
            allowed_content_types
            is None
        ):
            self.allowed_content_types = (
                set(
                    DEFAULT_ALLOWED_CONTENT_TYPES
                )
            )

        else:
            self.allowed_content_types = {
                _clean(value).casefold()
                for value
                in allowed_content_types
                if _clean(value)
            }

        if not self.allowed_content_types:
            raise SourceFetchValidationError(
                "allowed_content_types cannot be empty."
            )

        if (
            document_text_extractor
            is not None
            and not callable(
                document_text_extractor
            )
        ):
            raise SourceFetchValidationError(
                "document_text_extractor must be callable when supplied."
            )

        self.document_text_extractor = (
            document_text_extractor
        )

        self.include_raw_text = bool(
            include_raw_text
        )

    def fetch(
        self,
        url: str,
        timeout_seconds: float = 12.0,
    ) -> Dict[str, Any]:

        requested_url = _clean(
            url
        )

        if not requested_url:
            raise SourceFetchValidationError(
                "url cannot be empty."
            )

        try:
            timeout_seconds = float(
                timeout_seconds
            )

        except (
            TypeError,
            ValueError,
        ) as exc:
            raise SourceFetchValidationError(
                "timeout_seconds must be numeric."
            ) from exc

        if timeout_seconds <= 0:
            raise SourceFetchValidationError(
                "timeout_seconds must be > 0."
            )

        started_at = (
            time.monotonic()
        )

        raw = self.transport.fetch(
            url=requested_url,
            timeout_seconds=(
                timeout_seconds
            ),
            max_download_bytes=(
                self.max_download_bytes
            ),
        )

        elapsed = (
            time.monotonic()
            - started_at
        )

        if not isinstance(
            raw,
            Mapping,
        ):
            raise SourceFetchValidationError(
                "HTTP transport must return a mapping."
            )

        try:
            status_code = int(
                raw.get(
                    "status_code"
                )
            )

        except (
            TypeError,
            ValueError,
        ) as exc:
            raise SourceFetchValidationError(
                "Transport result is missing a valid status_code."
            ) from exc

        if not (
            200
            <= status_code
            < 300
        ):
            raise SourceFetchNetworkError(
                "Source returned non-success HTTP "
                f"status {status_code}."
            )

        final_url = _clean(
            raw.get(
                "final_url"
            )
            or requested_url
        )

        headers = (
            _normalize_headers(
                raw.get(
                    "headers"
                )
                or {}
            )
        )

        body = raw.get(
            "body"
        )

        if isinstance(
            body,
            str,
        ):
            body = body.encode(
                "utf-8"
            )

        if not isinstance(
            body,
            (
                bytes,
                bytearray,
            ),
        ):
            raise SourceFetchValidationError(
                "Transport result body must be bytes or text."
            )

        body = bytes(
            body
        )

        if (
            len(body)
            > self.max_download_bytes
        ):
            raise SourceTooLargeError(
                "Transport returned a body larger than "
                "max_download_bytes."
            )

        (
            content_type,
            charset,
        ) = _parse_content_type(
            headers.get(
                "content-type",
                "",
            )
        )

        if not content_type:
            content_type = (
                self
                ._sniff_content_type(
                    body
                )
            )

        if (
            content_type
            not in self.allowed_content_types
        ):
            raise UnsupportedContentTypeError(
                f"Unsupported content type {content_type!r}."
            )

        raw_sha256 = (
            _sha256_bytes(
                body
            )
        )

        raw_text = ""
        text_content = ""

        extraction_metadata: Dict[
            str,
            Any,
        ] = {}

        if content_type in {
            "text/html",
            "application/xhtml+xml",
        }:
            (
                raw_text,
                used_charset,
            ) = _decode_text(
                body,
                charset=charset,
            )

            charset = (
                charset
                or used_charset
            )

            (
                text_content,
                extraction_metadata,
            ) = _extract_html(
                raw_text,
                base_url=(
                    final_url
                ),
            )

        elif content_type in {
            "text/plain",
            "application/json",
            "application/xml",
            "text/xml",
        }:
            (
                raw_text,
                used_charset,
            ) = _decode_text(
                body,
                charset=charset,
            )

            charset = (
                charset
                or used_charset
            )

            text_content = (
                raw_text
            )

        elif (
            content_type
            == "application/pdf"
            and self.document_text_extractor
        ):
            text_content = (
                self
                ._invoke_document_text_extractor(
                    body=body,
                    content_type=(
                        content_type
                    ),
                    final_url=(
                        final_url
                    ),
                    timeout_seconds=max(
                        0.25,
                        timeout_seconds
                        - elapsed,
                    ),
                )
            )

        text_content = (
            self
            ._normalize_extracted_text(
                text_content
            )
        )

        declared_canonical = _clean(
            extraction_metadata.get(
                "declared_canonical_url"
            )
        )

        canonical_url = (
            _canonicalize_url(
                declared_canonical
                or final_url
            )
        )

        fetch_id = _stable_id(
            "SOURCEFETCH",
            {
                "requested_url": (
                    _canonicalize_url(
                        requested_url
                    )
                ),
                "final_url": (
                    _canonicalize_url(
                        final_url
                    )
                ),
                "raw_sha256": (
                    raw_sha256
                ),
            },
        )

        redirect_history = (
            raw.get(
                "redirect_history"
            )
            or []
        )

        if (
            not isinstance(
                redirect_history,
                Sequence,
            )
            or isinstance(
                redirect_history,
                (
                    str,
                    bytes,
                ),
            )
        ):
            raise SourceFetchValidationError(
                "redirect_history must be a sequence when supplied."
            )

        return {
            "source_fetch_id": (
                fetch_id
            ),
            "requested_url": (
                requested_url
            ),
            "final_url": (
                final_url
            ),
            "canonical_url": (
                canonical_url
            ),
            "status_code": (
                status_code
            ),
            "content_type": (
                content_type
            ),
            "charset": (
                charset
            ),
            "content_length_bytes": (
                len(body)
            ),
            "content_bytes": (
                body
            ),
            "raw_sha256": (
                raw_sha256
            ),
            "raw_text": (
                raw_text
                if self.include_raw_text
                else ""
            ),
            "text_content": (
                text_content
            ),
            "text_sha256": (
                _sha256_bytes(
                    text_content.encode(
                        "utf-8"
                    )
                )
                if text_content
                else ""
            ),
            "redirect_history": [
                _clean(item)
                for item
                in redirect_history
                if _clean(item)
            ],
            "metadata": {
                "service_version": (
                    SERVICE_VERSION
                ),
                "elapsed_seconds": (
                    elapsed
                ),
                "title": _clean(
                    extraction_metadata.get(
                        "title"
                    )
                ),
                "author": _clean(
                    extraction_metadata.get(
                        "author"
                    )
                ),
                "published_at": _clean(
                    extraction_metadata.get(
                        "published_at"
                    )
                ),
                "description": _clean(
                    extraction_metadata.get(
                        "description"
                    )
                ),
                "language": _clean(
                    extraction_metadata.get(
                        "language"
                    )
                ),
                "declared_canonical_url": (
                    declared_canonical
                ),
                "content_extraction_scope": (
                    _clean(
                        extraction_metadata
                        .get(
                            "content_extraction_scope"
                        )
                    )
                ),
                "content_page_type": (
                    _clean(
                        extraction_metadata
                        .get(
                            "content_page_type"
                        )
                    )
                    or "article_or_content"
                ),
                "listing_page_score": (
                    extraction_metadata
                    .get(
                        "listing_page_score",
                        0.0,
                    )
                ),
                "anchor_text_count": (
                    extraction_metadata
                    .get(
                        "anchor_text_count",
                        0,
                    )
                ),
                "distinct_anchor_text_count": (
                    extraction_metadata
                    .get(
                        "distinct_anchor_text_count",
                        0,
                    )
                ),
                "content_was_truncated": (
                    False
                ),
                "document_text_extractor_used": (
                    bool(
                        content_type
                        == "application/pdf"
                        and self.document_text_extractor
                        is not None
                    )
                ),
                "retrieval_mode": (
                    "direct_publisher_fetch"
                ),
                "limited_context": (
                    False
                ),
                "support_ceiling": (
                    "normal"
                ),
                "paid_external_service_required": (
                    False
                ),
                "free_only": (
                    True
                ),
            },
            "is_grounding_evidence": (
                False
            ),
        }

    @staticmethod
    def _sniff_content_type(
        body: bytes,
    ) -> str:

        prefix = (
            body[:512]
            .lstrip()
        )

        if prefix.startswith(
            b"%PDF-"
        ):
            return (
                "application/pdf"
            )

        lowered = (
            prefix.casefold()
        )

        if (
            lowered.startswith(
                b"<!doctype html"
            )
            or lowered.startswith(
                b"<html"
            )
            or b"<html"
            in lowered[:256]
        ):
            return "text/html"

        if (
            lowered.startswith(
                b"{"
            )
            or lowered.startswith(
                b"["
            )
        ):
            return (
                "application/json"
            )

        if lowered.startswith(
            b"<?xml"
        ):
            return (
                "application/xml"
            )

        if prefix:
            control_bytes = sum(
                1
                for value in prefix
                if (
                    value < 9
                    or 13 < value < 32
                )
            )

            if (
                control_bytes
                / len(prefix)
                < 0.02
            ):
                return (
                    "text/plain"
                )

        return (
            "application/octet-stream"
        )

    @staticmethod
    def _normalize_extracted_text(
        value: str,
    ) -> str:

        if not value:
            return ""

        value = unicodedata.normalize(
            "NFKC",
            html.unescape(
                value
            ),
        )

        value = (
            value
            .replace(
                "\u200b",
                "",
            )
            .replace(
                "\u200c",
                "",
            )
            .replace(
                "\u200d",
                "",
            )
            .replace(
                "\ufeff",
                "",
            )
        )

        value = value.translate(
            str.maketrans(
                {
                    "“": '"',
                    "”": '"',
                    "„": '"',
                    "‟": '"',
                    "‘": "'",
                    "’": "'",
                    "‚": "'",
                    "‛": "'",
                    "–": "-",
                    "—": "-",
                    "−": "-",
                    "\u00a0": " ",
                }
            )
        )

        lines: list[
            str
        ] = []

        previous = ""

        for raw_line in (
            value.splitlines()
        ):
            line = _clean(
                raw_line
            )

            if not line:
                continue

            key = (
                line.casefold()
            )

            if key == previous:
                continue

            previous = key

            lines.append(
                line
            )

        return "\n".join(
            lines
        )

    def _invoke_document_text_extractor(
        self,
        *,
        body: bytes,
        content_type: str,
        final_url: str,
        timeout_seconds: float,
    ) -> str:

        extractor = (
            self.document_text_extractor
        )

        if extractor is None:
            return ""

        try:
            signature = (
                inspect.signature(
                    extractor
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            result = extractor(
                body=body,
                content_type=(
                    content_type
                ),
                url=(
                    final_url
                ),
                timeout_seconds=(
                    timeout_seconds
                ),
            )

        else:
            kwargs = {
                "body": body,
                "content_type": (
                    content_type
                ),
                "url": (
                    final_url
                ),
                "timeout_seconds": (
                    timeout_seconds
                ),
            }

            accepts_var_kw = any(
                parameter.kind
                == inspect.Parameter.VAR_KEYWORD
                for parameter
                in signature
                .parameters
                .values()
            )

            if not accepts_var_kw:
                kwargs = {
                    key: value
                    for key, value
                    in kwargs.items()
                    if key
                    in signature.parameters
                }

            result = extractor(
                **kwargs
            )

        if result is None:
            return ""

        if not isinstance(
            result,
            str,
        ):
            raise SourceFetchValidationError(
                "document_text_extractor must return text or None."
            )

        return result


FetchService = (
    SourceFetchService
)


__all__ = [
    "SERVICE_VERSION",
    "DEFAULT_USER_AGENT",
    "DEFAULT_ALLOWED_CONTENT_TYPES",
    "TRACKING_PARAMETERS",
    "SourceFetchError",
    "SourceFetchValidationError",
    "SourceFetchNetworkError",
    "SourceFetchTimeoutError",
    "SourceTooLargeError",
    "UnsupportedContentTypeError",
    "UnsafeSourceURLError",
    "RedirectLimitError",
    "HTTPTransport",
    "UrllibHTTPTransport",
    "CallableHTTPTransport",
    "SourceFetchService",
    "FetchService",
]