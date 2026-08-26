import hashlib
from datetime import datetime, timezone

import pytest
from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    sessionmaker,
)

from services.article_service import ArticleService
from services.rss_service import RSSService
from services.source_fetch_service_direct import (
    CallableHTTPTransport,
    SourceFetchService,
    UnsupportedContentTypeError,
)
from services.source_provenance_service import SourceProvenanceService
from services.web_search_service import (
    CallableSearchProvider,
    WebSearchService,
    build_default_web_search_service,
)


class Base(DeclarativeBase):
    pass


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    source_id: Mapped[str] = mapped_column(
        String,
        unique=True,
        nullable=False,
    )
    url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    canonical_url: Mapped[str] = mapped_column(
        Text,
        unique=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    source_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    published_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    fetched_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    content_hash: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    raw_metadata: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )


@pytest.fixture
def article_service():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
    )

    Base.metadata.create_all(
        engine
    )

    SessionLocal = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        future=True,
    )

    return ArticleService(
        session_factory=SessionLocal,
        article_model=Article,
    )


def test_rss_parse_and_article_handoff(article_service):
    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
    <title>Example Feed</title>
    <link>https://example.com</link>
    <item>
      <title>Story</title>
      <link>https://example.com/story?utm_source=test</link>
      <guid>story-1</guid>
      <description><![CDATA[<p>Summary text</p>]]></description>
      <pubDate>Thu, 20 Aug 2026 10:00:00 GMT</pubDate>
    </item>
    </channel></rss>
    """

    parsed = RSSService().parse_feed(
        xml,
        feed_url=(
            "https://example.com/feed"
        ),
    )

    assert (
        parsed["items"][0]["url"]
        == "https://example.com/story"
    )

    assert (
        parsed["items"][0][
            "is_grounding_evidence"
        ]
        is False
    )

    result = (
        RSSService()
        .ingest_items_to_article_service(
            parsed_feed=parsed,
            article_service=article_service,
        )
    )

    assert result["created_count"] == 1
    assert article_service.count_articles() == 1


def test_source_fetch_callable_transport_returns_source_not_evidence():
    html = b"""
    <html>
      <head><title>Example</title></head>
      <body><main>Useful source body.</main></body>
    </html>
    """

    transport = CallableHTTPTransport(
        lambda **kwargs: {
            "status_code": 200,
            "final_url": (
                "https://example.com/article"
            ),
            "headers": {
                "content-type": (
                    "text/html; charset=utf-8"
                )
            },
            "body": html,
            "redirect_history": [],
        }
    )

    fetched = SourceFetchService(
        transport=transport,
        max_download_bytes=100_000,
    ).fetch(
        "https://example.com/article"
    )

    assert (
        fetched["canonical_url"]
        == "https://example.com/article"
    )

    assert (
        "Useful source body."
        in fetched["text_content"]
    )

    assert (
        fetched[
            "is_grounding_evidence"
        ]
        is False
    )


def test_source_fetch_rejects_unsupported_content_type():
    transport = CallableHTTPTransport(
        lambda **kwargs: {
            "status_code": 200,
            "final_url": (
                "https://example.com/file.bin"
            ),
            "headers": {
                "content-type": (
                    "application/octet-stream"
                )
            },
            "body": b"\x00\x01",
            "redirect_history": [],
        }
    )

    service = SourceFetchService(
        transport=transport
    )

    with pytest.raises(
        UnsupportedContentTypeError
    ):
        service.fetch(
            "https://example.com/file.bin"
        )


def test_article_service_source_fetch_integration(article_service):
    body = "Full article body."

    fetched = {
        "source_fetch_id": "SOURCEFETCH-1",
        "requested_url": "https://example.com/a",
        "final_url": "https://example.com/a",
        "canonical_url": "https://example.com/a",
        "status_code": 200,
        "content_type": "text/html",
        "text_content": body,
        "raw_sha256": hashlib.sha256(
            body.encode()
        ).hexdigest(),
        "text_sha256": hashlib.sha256(
            body.encode()
        ).hexdigest(),
        "redirect_history": [],
        "metadata": {
            "title": "Example",
            "source_name": "Example News",
            "published_at": (
                "2026-08-20T10:00:00+00:00"
            ),
        },
        "is_grounding_evidence": False,
    }

    write = article_service.upsert_fetched_source(
        fetched
    )

    payload = (
        article_service
        .get_extraction_payload_by_source_id(
            write.source_id
        )
    )

    assert (
        payload["source_id"]
        == write.source_id
    )

    assert (
        body
        in payload["article_text"]
    )

    assert (
        payload["metadata"][
            "source_is_evidence"
        ]
        is False
    )


def test_source_provenance_persists_and_unknown_independence(
    tmp_path,
):
    body_a = b"Original article A."
    body_b = b"Original article B."

    service = SourceProvenanceService(
        db_path=str(
            tmp_path
            / "provenance.sqlite3"
        )
    )

    a = service.register_source(
        {
            "requested_url": (
                "https://a.example/story"
            ),
            "final_url": (
                "https://a.example/story"
            ),
            "canonical_url": (
                "https://a.example/story"
            ),
            "raw_sha256": hashlib.sha256(
                body_a
            ).hexdigest(),
            "text_sha256": hashlib.sha256(
                body_a
            ).hexdigest(),
            "text_content": body_a.decode(),
            "content_bytes": body_a,
            "content_length_bytes": len(
                body_a
            ),
            "content_type": "text/html",
            "metadata": {
                "title": "A"
            },
        }
    )

    b = service.register_source(
        {
            "requested_url": (
                "https://b.example/story"
            ),
            "final_url": (
                "https://b.example/story"
            ),
            "canonical_url": (
                "https://b.example/story"
            ),
            "raw_sha256": hashlib.sha256(
                body_b
            ).hexdigest(),
            "text_sha256": hashlib.sha256(
                body_b
            ).hexdigest(),
            "text_content": body_b.decode(),
            "content_bytes": body_b,
            "content_length_bytes": len(
                body_b
            ),
            "content_type": "text/html",
            "metadata": {
                "title": "B"
            },
        }
    )

    comparison = service.compare_sources(
        a["source_id"],
        b["source_id"],
    )

    assert comparison[
        "assessment"
    ] in {
        "unknown",
        "independent",
        "same_origin",
    }

    # Different IDs alone must not force independence.
    assert (
        comparison["basis"]
        != [
            "different_source_ids"
        ]
    )

    source_id = a[
        "source_id"
    ]

    service.close()

    restored = SourceProvenanceService(
        db_path=str(
            tmp_path
            / "provenance.sqlite3"
        )
    )

    assert (
        restored.get_source(
            source_id
        )["source_id"]
        == source_id
    )

    restored.close()


def test_web_search_search_result_is_not_evidence():
    provider = CallableSearchProvider(
        lambda **kwargs: {
            "results": [
                {
                    "title": "Result",
                    "url": (
                        "https://example.com/a"
                    ),
                    "snippet": "Search snippet",
                }
            ],
            "usage": {},
        },
        name="fake",
    )

    service = WebSearchService(
        providers=[
            provider
        ],
        allow_failover=False,
    )

    result = service.search(
        query="test query",
        max_results=5,
    )

    assert (
        result["results"][0][
            "is_grounding_evidence"
        ]
        is False
    )


def test_default_web_search_is_searxng_only():
    service = (
        build_default_web_search_service(
            searxng_base_url=(
                "http://127.0.0.1:8080"
            )
        )
    )

    assert len(
        service.providers
    ) == 1

    assert (
        service.providers[0].provider_name
        == "searxng"
    )