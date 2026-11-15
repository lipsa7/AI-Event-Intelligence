import pytest

from services.source_fetch_service_direct import (
    CallableHTTPTransport,
    SourceFetchService,
    SourceTooLargeError,
)


def test_fetch_preserves_full_supported_text_without_marking_evidence():
    body = (
        b"<html><body><main>"
        b"Complete local test article."
        b"</main></body></html>"
    )

    transport = CallableHTTPTransport(
        lambda **kwargs: {
            "status_code": 200,
            "final_url": (
                "https://example.com/a"
            ),
            "headers": {
                "content-type": (
                    "text/html; charset=utf-8"
                )
            },
            "body": body,
            "redirect_history": [],
        }
    )

    result = SourceFetchService(
        transport=transport,
        max_download_bytes=10000,
    ).fetch(
        "https://example.com/a"
    )

    assert (
        "Complete local test article."
        in result["text_content"]
    )

    assert (
        result[
            "is_grounding_evidence"
        ]
        is False
    )


def test_fetch_fails_instead_of_silently_truncating_oversized_source():
    body = (
        b"x"
        * 101
    )

    transport = CallableHTTPTransport(
        lambda **kwargs: {
            "status_code": 200,
            "final_url": (
                "https://example.com/a"
            ),
            "headers": {
                "content-type": "text/html"
            },
            "body": body,
            "redirect_history": [],
        }
    )

    service = SourceFetchService(
        transport=transport,
        max_download_bytes=100,
    )

    with pytest.raises(
        SourceTooLargeError
    ):
        service.fetch(
            "https://example.com/a"
        )