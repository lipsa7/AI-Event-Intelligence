import hashlib

from services.source_provenance_service import SourceProvenanceService


def _fetched(
    url,
    body,
):
    raw = body.encode()
    digest = hashlib.sha256(
        raw
    ).hexdigest()

    return {
        "requested_url": url,
        "final_url": url,
        "canonical_url": url,
        "raw_sha256": digest,
        "text_sha256": digest,
        "text_content": body,
        "content_bytes": raw,
        "content_length_bytes": len(
            raw
        ),
        "content_type": "text/html",
        "metadata": {
            "title": body
        },
    }


def test_identical_content_is_not_counted_as_independent(
    tmp_path,
):
    service = SourceProvenanceService(
        db_path=str(
            tmp_path
            / "provenance.sqlite3"
        )
    )

    a = service.register_source(
        _fetched(
            "https://a.example/a",
            "same body",
        )
    )

    b = service.register_source(
        _fetched(
            "https://b.example/b",
            "same body",
        )
    )

    comparison = service.compare_sources(
        a["source_id"],
        b["source_id"],
    )

    assert (
        comparison["assessment"]
        != "independent"
    )

    service.close()


def test_explicit_trusted_independence_can_be_registered(
    tmp_path,
):
    service = SourceProvenanceService(
        db_path=str(
            tmp_path
            / "provenance.sqlite3"
        )
    )

    a = service.register_source(
        _fetched(
            "https://a.example/a",
            "body a",
        )
    )

    b = service.register_source(
        _fetched(
            "https://b.example/b",
            "body b",
        )
    )

    service.register_pairwise_independence(
        a["source_id"],
        b["source_id"],
        assessment="independent",
        basis=(
            "manual_verified_distinct_origin"
        ),
        trusted=True,
    )

    comparison = service.compare_sources(
        a["source_id"],
        b["source_id"],
    )

    assert (
        comparison["assessment"]
        == "independent"
    )

    service.close()