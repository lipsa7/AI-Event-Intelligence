"""
Article persistence and normalization service.

Recommended location:
    services/article_service.py

Role
----
RSSService / ingestion
        ↓
ArticleService
        ↓
ExtractionAgent
        ↓
EventService

Responsibilities
----------------
- Canonicalize article URLs deterministically.
- Generate stable SOURCE-* IDs.
- Compute exact content fingerprints.
- Create/update article records transactionally.
- Query persisted articles.
- Produce the exact structured payload handed to ExtractionAgent.

Non-responsibilities
--------------------
- No RSS fetching.
- No LLM calls.
- No event extraction.
- No entity resolution.
- No source-independence / syndication judgement.
- No vector indexing.
- No arbitrary text truncation.

Source-independence belongs to SourceProvenanceService. Two URLs with identical
content are therefore NOT automatically merged here.

Expected Article ORM contract
-----------------------------
The Article model should expose at least:

    source_id: str, unique
    url: str
    canonical_url: str, unique/indexed when present
    title: str
    content: str
    source_name: str
    published_at: datetime | None
    fetched_at: datetime
    content_hash: str
    raw_metadata: JSON/dict

Optional conventional fields:
    id
    created_at
    updated_at

The service accepts injected model/session dependencies for testing, but when
omitted it lazily imports:

    models.article.Article
    services.database_service.SessionLocal
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import (
    parse_qsl,
    urlencode,
    urlsplit,
    urlunsplit,
)

from sqlalchemy import func, select
from sqlalchemy.orm import Session


TRACKING_QUERY_PREFIXES = (
    "utm_",
)

TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "dclid",
    "mc_cid",
    "mc_eid",
    "igshid",
}

REQUIRED_MODEL_FIELDS = {
    "source_id",
    "url",
    "canonical_url",
    "title",
    "content",
    "source_name",
    "published_at",
    "fetched_at",
    "content_hash",
    "raw_metadata",
}


class ArticleServiceError(RuntimeError):
    """Base exception for ArticleService."""


class ArticleValidationError(ArticleServiceError):
    """Raised when article input violates the service contract."""


class ArticleModelContractError(ArticleServiceError):
    """Raised when the ORM model does not match the expected final contract."""


@dataclass(frozen=True)
class ArticleWriteResult:
    source_id: str
    created: bool
    content_changed: bool
    canonical_url: str
    content_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _clean_single_line(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _clean_content(value: Any) -> str:
    """
    Preserve paragraph structure while normalizing line endings and trailing
    whitespace. Do not collapse the article into a single line.
    """
    if value is None:
        return ""

    text = str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    lines = [
        line.rstrip()
        for line in text.split("\n")
    ]

    # Remove outer blank lines only.
    while lines and not lines[0].strip():
        lines.pop(0)

    while lines and not lines[-1].strip():
        lines.pop()

    return "\n".join(lines)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(
        value,
        (str, int, float, bool),
    ):
        return value

    if isinstance(value, datetime):
        return _datetime_to_iso(value)

    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            _json_safe(item)
            for item in value
        ]

    return str(value)


def _coerce_datetime(
    value: Any,
    *,
    field_name: str,
    allow_none: bool = True,
) -> Optional[datetime]:
    if value is None or value == "":
        if allow_none:
            return None
        raise ArticleValidationError(
            f"{field_name} is required."
        )

    if isinstance(value, datetime):
        parsed = value

    elif isinstance(value, str):
        text = value.strip()

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ArticleValidationError(
                f"{field_name} must be an ISO-8601 datetime; "
                f"received {value!r}."
            ) from exc

    else:
        raise ArticleValidationError(
            f"{field_name} must be datetime, ISO string, or None."
        )

    # RSS feeds frequently provide naive timestamps. For this local prototype,
    # interpret them consistently as UTC rather than mixing naive/aware values.
    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )
    else:
        parsed = parsed.astimezone(
            timezone.utc
        )

    return parsed


def _datetime_to_iso(
    value: Optional[datetime],
) -> str:
    if value is None:
        return ""

    if value.tzinfo is None:
        value = value.replace(
            tzinfo=timezone.utc
        )
    else:
        value = value.astimezone(
            timezone.utc
        )

    return value.isoformat()


def canonicalize_url(
    url: str,
    *,
    remove_tracking_parameters: bool = True,
) -> str:
    """
    Canonicalize a URL without trying to infer publisher-specific canonical URLs.

    Transformations:
    - lower-case scheme and hostname;
    - remove fragments;
    - remove default ports;
    - normalize empty path to "/";
    - sort query parameters;
    - optionally remove common tracking parameters.

    This intentionally does NOT:
    - resolve redirects;
    - strip arbitrary query parameters;
    - merge syndication copies.
    """
    raw = _clean_single_line(url)

    if not raw:
        return ""

    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        raise ArticleValidationError(
            f"Invalid URL: {url!r}"
        ) from exc

    if not parts.scheme or not parts.netloc:
        raise ArticleValidationError(
            f"URL must include scheme and host: {url!r}"
        )

    scheme = parts.scheme.casefold()

    try:
        hostname = (
            parts.hostname.casefold()
            if parts.hostname
            else ""
        )
        port = parts.port
    except ValueError as exc:
        raise ArticleValidationError(
            f"Invalid URL port: {url!r}"
        ) from exc

    if not hostname:
        raise ArticleValidationError(
            f"URL must include a hostname: {url!r}"
        )

    username = parts.username or ""
    password = parts.password or ""

    userinfo = ""

    if username:
        userinfo = username

        if password:
            userinfo += f":{password}"

        userinfo += "@"

    default_port = (
        scheme == "http" and port == 80
    ) or (
        scheme == "https" and port == 443
    )

    netloc = (
        f"{userinfo}{hostname}"
        if port is None or default_port
        else f"{userinfo}{hostname}:{port}"
    )

    path = parts.path or "/"

    query_pairs = parse_qsl(
        parts.query,
        keep_blank_values=True,
    )

    if remove_tracking_parameters:
        query_pairs = [
            (key, value)
            for key, value in query_pairs
            if (
                key.casefold()
                not in TRACKING_QUERY_KEYS
                and not key.casefold().startswith(
                    TRACKING_QUERY_PREFIXES
                )
            )
        ]

    query_pairs.sort(
        key=lambda item: (
            item[0].casefold(),
            item[1],
        )
    )

    query = urlencode(
        query_pairs,
        doseq=True,
    )

    return urlunsplit(
        (
            scheme,
            netloc,
            path,
            query,
            "",
        )
    )


def content_fingerprint(
    content: str,
) -> str:
    """
    SHA-256 fingerprint of the normalized captured article content.

    This is an integrity/deduplication signal, not a semantic similarity score.
    """
    normalized = _clean_content(
        content
    )

    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


def stable_source_id(
    *,
    canonical_url: str,
    source_name: str,
    title: str,
    published_at: Optional[datetime],
    content_hash: str,
) -> str:
    """
    Create a deterministic SOURCE-* ID.

    URL is preferred because updated content at the same canonical article URL
    should remain the same source record. When URL is unavailable, fall back to
    a deterministic combination of source/title/date/content fingerprint.
    """
    if canonical_url:
        identity = (
            "url\x1f"
            + canonical_url
        )

    else:
        identity = "\x1f".join(
            [
                "fallback",
                _clean_single_line(
                    source_name
                ).casefold(),
                _clean_single_line(
                    title
                ).casefold(),
                _datetime_to_iso(
                    published_at
                ),
                content_hash,
            ]
        )

    digest = hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()[:24].upper()

    return f"SOURCE-{digest}"


class ArticleService:
    """
    Deterministic article persistence/query service.

    Dependencies may be injected:

        ArticleService(
            session_factory=SessionLocal,
            article_model=Article,
        )

    If omitted, the project defaults are imported lazily.
    """

    def __init__(
        self,
        *,
        session_factory: Any = None,
        article_model: Any = None,
    ) -> None:
        if session_factory is None:
            try:
                from services.database_service import (
                    SessionLocal,
                )
            except ImportError as exc:
                raise ArticleServiceError(
                    "Could not import SessionLocal from "
                    "services.database_service."
                ) from exc

            session_factory = SessionLocal

        if article_model is None:
            try:
                from models.article import (
                    Article,
                )
            except ImportError as exc:
                raise ArticleServiceError(
                    "Could not import Article from models.article."
                ) from exc

            article_model = Article

        self.session_factory = session_factory
        self.Article = article_model

        self._validate_model_contract()

    # ------------------------------------------------------------------
    # Contract validation
    # ------------------------------------------------------------------

    def _validate_model_contract(
        self,
    ) -> None:
        missing = sorted(
            field
            for field in REQUIRED_MODEL_FIELDS
            if not hasattr(
                self.Article,
                field,
            )
        )

        if missing:
            raise ArticleModelContractError(
                "Article ORM model is missing required field(s): "
                + ", ".join(
                    missing
                )
            )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def upsert_article(
        self,
        *,
        url: str = "",
        title: str,
        content: str,
        source_name: str = "",
        published_at: Any = None,
        fetched_at: Any = None,
        raw_metadata: Optional[
            Mapping[str, Any]
        ] = None,
        source_id: Optional[str] = None,
    ) -> ArticleWriteResult:
        """
        Insert or update one article.

        Identity rules:
        - caller-provided source_id wins;
        - otherwise stable SOURCE-* ID is derived deterministically;
        - same content on a different URL is NOT merged automatically.

        Returns metadata about whether a record was created and whether its
        captured content changed.
        """
        title = _clean_single_line(
            title
        )
        content = _clean_content(
            content
        )
        source_name = _clean_single_line(
            source_name
        )
        url = _clean_single_line(
            url
        )

        if not title:
            raise ArticleValidationError(
                "title cannot be empty."
            )

        if not content:
            raise ArticleValidationError(
                "content cannot be empty."
            )

        canonical_url = (
            canonicalize_url(
                url
            )
            if url
            else ""
        )

        published_dt = _coerce_datetime(
            published_at,
            field_name="published_at",
            allow_none=True,
        )

        fetched_dt = _coerce_datetime(
            fetched_at,
            field_name="fetched_at",
            allow_none=True,
        ) or datetime.now(
            timezone.utc
        )

        digest = content_fingerprint(
            content
        )

        source_id = (
            _clean_single_line(
                source_id
            )
            if source_id
            else stable_source_id(
                canonical_url=canonical_url,
                source_name=source_name,
                title=title,
                published_at=published_dt,
                content_hash=digest,
            )
        )

        if not source_id:
            raise ArticleValidationError(
                "source_id could not be established."
            )

        metadata_payload = _json_safe(
            dict(
                raw_metadata or {}
            )
        )

        session: Session = self.session_factory()

        try:
            existing = session.execute(
                select(
                    self.Article
                ).where(
                    self.Article.source_id
                    == source_id
                )
            ).scalar_one_or_none()

            # Protect URL uniqueness if the source_id was supplied manually.
            if (
                existing is None
                and canonical_url
            ):
                by_url = session.execute(
                    select(
                        self.Article
                    ).where(
                        self.Article.canonical_url
                        == canonical_url
                    )
                ).scalar_one_or_none()

                if by_url is not None:
                    existing = by_url
                    source_id = (
                        by_url.source_id
                    )

            created = (
                existing is None
            )

            if created:
                article = self.Article(
                    source_id=source_id,
                    url=url,
                    canonical_url=canonical_url,
                    title=title,
                    content=content,
                    source_name=source_name,
                    published_at=published_dt,
                    fetched_at=fetched_dt,
                    content_hash=digest,
                    raw_metadata=metadata_payload,
                )

                session.add(
                    article
                )

                content_changed = True

            else:
                article = existing

                previous_hash = _clean_single_line(
                    article.content_hash
                )

                content_changed = (
                    previous_hash
                    != digest
                )

                article.url = url
                article.canonical_url = (
                    canonical_url
                )
                article.title = title
                article.content = content
                article.source_name = (
                    source_name
                )
                article.published_at = (
                    published_dt
                )
                article.fetched_at = (
                    fetched_dt
                )
                article.content_hash = (
                    digest
                )
                article.raw_metadata = (
                    metadata_payload
                )

            session.commit()

            return ArticleWriteResult(
                source_id=source_id,
                created=created,
                content_changed=(
                    content_changed
                ),
                canonical_url=(
                    canonical_url
                ),
                content_hash=digest,
            )

        except Exception:
            session.rollback()
            raise

        finally:
            session.close()

    def delete_by_source_id(
        self,
        source_id: str,
    ) -> bool:
        """
        Delete one article record.

        Returns True only when a record existed.
        """
        source_id = _clean_single_line(
            source_id
        )

        if not source_id:
            raise ArticleValidationError(
                "source_id cannot be empty."
            )

        session: Session = self.session_factory()

        try:
            article = session.execute(
                select(
                    self.Article
                ).where(
                    self.Article.source_id
                    == source_id
                )
            ).scalar_one_or_none()

            if article is None:
                return False

            session.delete(
                article
            )
            session.commit()

            return True

        except Exception:
            session.rollback()
            raise

        finally:
            session.close()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_by_source_id(
        self,
        source_id: str,
    ) -> Optional[Any]:
        source_id = _clean_single_line(
            source_id
        )

        if not source_id:
            return None

        session: Session = self.session_factory()

        try:
            return session.execute(
                select(
                    self.Article
                ).where(
                    self.Article.source_id
                    == source_id
                )
            ).scalar_one_or_none()

        finally:
            session.close()

    def get_by_url(
        self,
        url: str,
    ) -> Optional[Any]:
        canonical_url = canonicalize_url(
            url
        )

        session: Session = self.session_factory()

        try:
            return session.execute(
                select(
                    self.Article
                ).where(
                    self.Article.canonical_url
                    == canonical_url
                )
            ).scalar_one_or_none()

        finally:
            session.close()

    def get_by_content_hash(
        self,
        content_hash: str,
    ) -> List[Any]:
        """
        Return every article with the exact same captured content fingerprint.

        Multiple records are intentionally allowed because identical/syndicated
        content can appear at different URLs.
        """
        content_hash = _clean_single_line(
            content_hash
        ).casefold()

        if not content_hash:
            return []

        session: Session = self.session_factory()

        try:
            return list(
                session.execute(
                    select(
                        self.Article
                    ).where(
                        func.lower(
                            self.Article.content_hash
                        )
                        == content_hash
                    )
                ).scalars().all()
            )

        finally:
            session.close()

    def list_articles(
        self,
        *,
        source_name: Optional[str] = None,
        published_after: Any = None,
        published_before: Any = None,
        offset: int = 0,
        limit: Optional[int] = None,
    ) -> List[Any]:
        """
        Query persisted articles.

        There is intentionally no default limit. Callers that need bounded
        context must choose an explicit semantic/query scope rather than lose
        records because of a hidden service-level cap.
        """
        if offset < 0:
            raise ArticleValidationError(
                "offset must be >= 0."
            )

        if (
            limit is not None
            and limit <= 0
        ):
            raise ArticleValidationError(
                "limit must be > 0 when supplied."
            )

        after_dt = _coerce_datetime(
            published_after,
            field_name="published_after",
            allow_none=True,
        )

        before_dt = _coerce_datetime(
            published_before,
            field_name="published_before",
            allow_none=True,
        )

        query = select(
            self.Article
        )

        if source_name:
            query = query.where(
                self.Article.source_name
                == _clean_single_line(
                    source_name
                )
            )

        if after_dt is not None:
            query = query.where(
                self.Article.published_at
                >= after_dt
            )

        if before_dt is not None:
            query = query.where(
                self.Article.published_at
                <= before_dt
            )

        query = query.order_by(
            self.Article.published_at.desc(),
            self.Article.source_id.asc(),
        ).offset(
            offset
        )

        if limit is not None:
            query = query.limit(
                limit
            )

        session: Session = self.session_factory()

        try:
            return list(
                session.execute(
                    query
                ).scalars().all()
            )

        finally:
            session.close()

    def count_articles(
        self,
    ) -> int:
        session: Session = self.session_factory()

        try:
            return int(
                session.execute(
                    select(
                        func.count()
                    ).select_from(
                        self.Article
                    )
                ).scalar_one()
            )

        finally:
            session.close()

    # ------------------------------------------------------------------
    # Serialization / downstream handoff
    # ------------------------------------------------------------------

    def article_to_dict(
        self,
        article: Any,
    ) -> Dict[str, Any]:
        """
        Stable serialization for API/debug/reporting use.
        """
        payload = {
            "source_id": (
                article.source_id
            ),
            "url": (
                article.url
            ),
            "canonical_url": (
                article.canonical_url
            ),
            "title": (
                article.title
            ),
            "content": (
                article.content
            ),
            "source_name": (
                article.source_name
            ),
            "published_at": _datetime_to_iso(
                article.published_at
            ),
            "fetched_at": _datetime_to_iso(
                article.fetched_at
            ),
            "content_hash": (
                article.content_hash
            ),
            "raw_metadata": _json_safe(
                article.raw_metadata
            ),
        }

        if hasattr(
            article,
            "id",
        ):
            payload[
                "id"
            ] = _json_safe(
                article.id
            )

        return payload

    def to_extraction_payload(
        self,
        article: Any,
    ) -> Dict[str, Any]:
        """
        Build the exact source envelope handed to ExtractionAgent.

        `article_text` is deterministic and should be the SAME text exposed to
        the model and later used when validating evidence snippets.

        No character/token truncation is performed here.
        """
        title = _clean_single_line(
            article.title
        )
        content = _clean_content(
            article.content
        )

        article_text = "\n\n".join(
            part
            for part in (
                title,
                content,
            )
            if part
        )

        return {
            "source_id": (
                article.source_id
            ),
            "article_text": (
                article_text
            ),
            "source_metadata": {
                "url": (
                    article.url
                ),
                "canonical_url": (
                    article.canonical_url
                ),
                "source_name": (
                    article.source_name
                ),
                "published_at": _datetime_to_iso(
                    article.published_at
                ),
                "fetched_at": _datetime_to_iso(
                    article.fetched_at
                ),
                "content_hash": (
                    article.content_hash
                ),
                "raw_metadata": _json_safe(
                    article.raw_metadata
                ),
            },
        }