from __future__ import annotations

"""
Public resilient SourceFetchService for AI Event Intelligence.

Location:
    services/source_fetch_service.py

Flow:
    direct publisher fetch
        ↓
    usable article text?
        ├─ yes → return full source
        └─ no
            ↓
        GDELT Context fallback available?
            ├─ yes → return LIMITED-CONTEXT source
            └─ no → fail this source so orchestrator can try another one

Important:
    - tiny/interstitial pages are NOT treated as articles
    - Google News RSS snippets are NOT treated as evidence
    - GDELT Context remains limited-context source material
    - source != evidence
"""

import hashlib
import json
from typing import Any, Dict, Mapping


try:
    from services.source_fetch_service_direct import (
        DEFAULT_ALLOWED_CONTENT_TYPES,
        DEFAULT_USER_AGENT,
        TRACKING_PARAMETERS,
        CallableHTTPTransport,
        HTTPTransport,
        RedirectLimitError,
        SourceFetchError,
        SourceFetchNetworkError,
        SourceFetchTimeoutError,
        SourceFetchValidationError,
        SourceTooLargeError,
        UnsupportedContentTypeError,
        UnsafeSourceURLError,
        UrllibHTTPTransport,
        SourceFetchService as _DirectSourceFetchService,
    )

    from services.free_news_context_cache import (
        canonicalize_url,
        get_free_news_context,
    )

except ModuleNotFoundError:
    from source_fetch_service_direct import (
        DEFAULT_ALLOWED_CONTENT_TYPES,
        DEFAULT_USER_AGENT,
        TRACKING_PARAMETERS,
        CallableHTTPTransport,
        HTTPTransport,
        RedirectLimitError,
        SourceFetchError,
        SourceFetchNetworkError,
        SourceFetchTimeoutError,
        SourceFetchValidationError,
        SourceTooLargeError,
        UnsupportedContentTypeError,
        UnsafeSourceURLError,
        UrllibHTTPTransport,
        SourceFetchService as _DirectSourceFetchService,
    )

    from free_news_context_cache import (
        canonicalize_url,
        get_free_news_context,
    )


SERVICE_VERSION = "source-fetch-service-v2.1-free-resilient"

# An event-intelligence source needs enough material
# to contain an actual event/claim.
#
# This specifically prevents tiny redirect/interstitial pages
# such as the 24-character pages seen in the failed run.
MIN_USABLE_DIRECT_TEXT_CHARS = 180

MIN_GDELT_CONTEXT_CHARS = 60


INTERSTITIAL_MARKERS = (
    "please enable javascript",
    "enable javascript",
    "javascript is disabled",
    "javascript is not enabled",
    "before you continue",
    "checking your browser",
    "verify you are human",
    "are you a human",
    "access denied",
    "enable cookies",
    "cookies are disabled",
    "just a moment",
    "temporarily unavailable",
    "service unavailable",
    "unsupported browser",
    "redirecting",
)


def _clean(value: Any) -> str:
    if value is None:
        return ""

    return " ".join(
        str(value).split()
    ).strip()


def _stable_fallback_fetch_id(
    url: str,
    text_sha256: str,
) -> str:

    raw = json.dumps(
        {
            "url": url,
            "text_sha256": text_sha256,
            "retrieval_mode": "gdelt_context_fallback",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    digest = (
        hashlib.sha256(raw)
        .hexdigest()[:24]
        .upper()
    )

    return f"SOURCEFETCH-{digest}"


def _looks_like_interstitial(
    text: str,
) -> bool:
    """
    Detect obvious browser/redirect/challenge surfaces.

    We deliberately apply phrase detection only to relatively
    short pages so a normal long article discussing "access denied"
    does not get rejected accidentally.
    """

    normalized = _clean(text)

    if not normalized:
        return True

    if len(normalized) < MIN_USABLE_DIRECT_TEXT_CHARS:
        return True

    lowered = normalized.casefold()

    if len(normalized) <= 1500:
        if any(
            marker in lowered
            for marker in INTERSTITIAL_MARKERS
        ):
            return True

    return False


class SourceFetchService(
    _DirectSourceFetchService
):
    """
    Direct publisher fetch with conservative
    GDELT Context fallback.
    """

    def fetch(
        self,
        url: str,
        timeout_seconds: float = 12.0,
    ) -> Dict[str, Any]:

        requested_url = _clean(url)

        # Let the direct service preserve its normal
        # validation behavior for invalid/empty URLs.
        if not requested_url:
            return super().fetch(
                url,
                timeout_seconds=timeout_seconds,
            )

        direct_error: str = ""

        try:
            direct = super().fetch(
                requested_url,
                timeout_seconds=timeout_seconds,
            )

            direct_text = _clean(
                direct.get("text_content")
                or direct.get("raw_text")
            )

            direct_metadata = (
                direct.get("metadata")
                or {}
            )

            if not isinstance(
                direct_metadata,
                Mapping,
            ):
                direct_metadata = {}

            # ---------------------------------------------------------
            # GOOD DIRECT SOURCE
            # ---------------------------------------------------------

            if (
                direct_text
                and not _looks_like_interstitial(
                    direct_text
                )
            ):
                original_service_version = _clean(
                    direct_metadata.get(
                        "service_version"
                    )
                )

                direct["metadata"] = {
                    **dict(direct_metadata),

                    "service_version": (
                        SERVICE_VERSION
                    ),

                    "direct_service_version": (
                        original_service_version
                    ),

                    "retrieval_mode": (
                        "direct_publisher_fetch"
                    ),

                    "publisher_fetch_failed": (
                        False
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
                }

                return direct

            # ---------------------------------------------------------
            # DIRECT FETCH technically succeeded but returned junk,
            # interstitial or tiny/non-event-bearing content.
            # ---------------------------------------------------------

            direct_error = (
                "Direct source returned unusable article text "
                f"({len(direct_text)} characters)."
            )

        except SourceFetchError as exc:

            direct_error = (
                f"{type(exc).__name__}: "
                f"{_clean(exc)}"
            )

        # -------------------------------------------------------------
        # GDELT CONTEXT FALLBACK
        # -------------------------------------------------------------

        fallback = get_free_news_context(
            requested_url
        )

        if fallback:
            return self._build_context_fallback(
                requested_url=requested_url,
                fallback=fallback,
                direct_error=direct_error,
            )

        # -------------------------------------------------------------
        # NO LEGITIMATE FALLBACK
        #
        # Do NOT return tiny direct text.
        # Let the orchestrator discard this source and continue
        # with another discovery candidate.
        # -------------------------------------------------------------

        raise SourceFetchError(
            (
                direct_error
                or "Direct publisher source could not be retrieved."
            )
            + " No GDELT Context fallback was available."
        )

    @staticmethod
    def _build_context_fallback(
        *,
        requested_url: str,
        fallback: Mapping[str, Any],
        direct_error: str,
    ) -> Dict[str, Any]:

        text = _clean(
            fallback.get("text")
        )

        # Fail closed.
        # Headlines/tiny fragments are not source context.
        if (
            len(text)
            < MIN_GDELT_CONTEXT_CHARS
        ):
            raise SourceFetchError(
                "Cached GDELT Context is too short "
                "to use as a source fallback."
            )

        canonical_url = (
            canonicalize_url(
                _clean(
                    fallback.get("url")
                )
                or requested_url
            )
            or requested_url
        )

        body = text.encode(
            "utf-8"
        )

        digest = (
            hashlib.sha256(body)
            .hexdigest()
        )

        return {
            "source_fetch_id": (
                _stable_fallback_fetch_id(
                    canonical_url,
                    digest,
                )
            ),

            "requested_url": (
                requested_url
            ),

            "final_url": (
                canonical_url
            ),

            "canonical_url": (
                canonical_url
            ),

            # This is successful FALLBACK retrieval.
            # It does not claim publisher HTTP 200.
            "status_code": 200,

            "content_type": (
                "text/plain"
            ),

            "charset": (
                "utf-8"
            ),

            "content_length_bytes": (
                len(body)
            ),

            "content_bytes": (
                body
            ),

            "raw_sha256": (
                digest
            ),

            "raw_text": (
                text
            ),

            "text_content": (
                text
            ),

            "text_sha256": (
                digest
            ),

            "redirect_history": [],

            "metadata": {
                "service_version": (
                    SERVICE_VERSION
                ),

                "title": (
                    _clean(
                        fallback.get(
                            "title"
                        )
                    )
                ),

                "source_name": (
                    _clean(
                        fallback.get(
                            "domain"
                        )
                    )
                ),

                "published_at": (
                    _clean(
                        fallback.get(
                            "published_at"
                        )
                    )
                ),

                "language": (
                    _clean(
                        fallback.get(
                            "language"
                        )
                    )
                ),

                "source_country": (
                    _clean(
                        fallback.get(
                            "source_country"
                        )
                    )
                ),

                "description": "",

                "declared_canonical_url": (
                    canonical_url
                ),

                "content_extraction_scope": (
                    "gdelt_context"
                ),

                "content_page_type": (
                    "article_or_content"
                ),

                "listing_page_score": (
                    0.0
                ),

                "anchor_text_count": (
                    0
                ),

                "distinct_anchor_text_count": (
                    0
                ),

                "retrieval_mode": (
                    "gdelt_context_fallback"
                ),

                "fallback_provider": (
                    _clean(
                        fallback.get(
                            "provider"
                        )
                    )
                    or "gdelt_context"
                ),

                "publisher_fetch_failed": (
                    True
                ),

                "publisher_fetch_error": (
                    _clean(
                        direct_error
                    )
                ),

                "limited_context": (
                    True
                ),

                "context_scope": (
                    _clean(
                        fallback.get(
                            "context_scope"
                        )
                    )
                    or (
                        "matching_sentence_"
                        "with_neighboring_context"
                    )
                ),

                "support_ceiling": (
                    "limited"
                ),

                "content_was_truncated": (
                    False
                ),

                "document_text_extractor_used": (
                    False
                ),

                "paid_external_service_required": (
                    False
                ),

                "free_only": (
                    True
                ),
            },

            # Source material still has to pass
            # downstream evidence admission.
            "is_grounding_evidence": (
                False
            ),
        }


FetchService = SourceFetchService


__all__ = [
    "SERVICE_VERSION",
    "MIN_USABLE_DIRECT_TEXT_CHARS",
    "MIN_GDELT_CONTEXT_CHARS",
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