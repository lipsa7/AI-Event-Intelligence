import pytest

from services.rss_service import (
    RSSService,
    RSSValidationError,
)


def test_atom_feed_parses_without_treating_item_as_evidence():
    xml = """<?xml version='1.0'?>
    <feed xmlns='http://www.w3.org/2005/Atom'>
      <title>Atom Example</title>
      <link href='https://example.com/' rel='alternate'/>
      <entry>
        <id>tag:example.com,2026:1</id>
        <title>Atom Story</title>
        <link href='https://example.com/story?utm_source=x' rel='alternate'/>
        <updated>2026-08-20T10:00:00Z</updated>
        <summary>Useful summary</summary>
      </entry>
    </feed>
    """

    result = RSSService().parse_feed(
        xml,
        feed_url="https://example.com/atom",
    )

    assert (
        result["feed"]["feed_type"]
        == "atom"
    )

    assert (
        result["items"][0]["url"]
        == "https://example.com/story"
    )

    assert (
        result["items"][0][
            "is_grounding_evidence"
        ]
        is False
    )


def test_require_full_content_rejects_summary_only_item():
    item = {
        "source_id": "SOURCE-1",
        "url": "https://example.com/a",
        "title": "A",
        "summary": "summary",
        "content": "",
    }

    with pytest.raises(
        RSSValidationError
    ):
        RSSService().to_article_payload(
            item,
            require_full_content=True,
        )