"""
Deterministic source provenance, duplicate-origin and independence service.

Recommended location:
    services/source_provenance_service.py

Purpose
-------
A strategic-research system must not count:

    one press release
        ↓
    five republished articles
        ↓
    as five independent confirmations.

This service creates and maintains the provenance layer between fetched sources
and evidence records.

It answers questions such as:
- Is this the same canonical source seen before?
- Is this a new version of an existing source?
- Is this exact content republished at another URL?
- Do two sources share a known underlying origin?
- Are two sources only *possibly* syndicated?
- Is source independence actually established, or still unknown?
- Which evidence records ultimately depend on the same origin?

Critical epistemic rule
-----------------------
Different URLs != independent sources.
Different domains != independent sources.
Different publishers != automatically independent sources.
High semantic similarity != automatically the same origin.
Search result metadata != provenance proof.

By default this service can establish SAME ORIGIN when it has deterministic
evidence such as:
- the same canonical source URL;
- the same exact content hash;
- an explicit trusted origin key;
- an explicit derived-from relationship to a registered source.

It does NOT declare two sources independent merely because their URLs/domains
differ. Pairwise independence must be established by an injected resolver or a
trusted deterministic assertion.

This conservative behavior is important for grounding.

Free-project constraint
-----------------------
The default service is local and deterministic:
- no paid API;
- no cloud dependency;
- no mandatory LLM.

An optional similarity function can be backed by the project's existing local
EmbeddingService.

An optional independence_resolver can be supplied, but its decisions are
recorded with a basis and are never silently treated as ground truth.

Integration
-----------
SourceFetchService
        ↓
SourceProvenanceService.register_source(...)
        ↓
ArticleService / ExtractionAgent / EvidenceBuilderAgent
        ↓
SourceProvenanceService.decorate_evidence(...)
        ↓
Grounding / EvidenceDiscovery / StrategicInference

Important integration note
--------------------------
Consumers should call `compare_sources()` when they need pairwise independence.
They should NOT infer:

    group_a != group_b  => independent

because absence of a known common origin is not proof of independence.

Output records contain no fetched body bytes. Raw source content remains owned
by SourceFetchService / ArticleService.

No private chain-of-thought is stored. Provenance traces contain only auditable
research artifacts and deterministic/resolver decisions.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Set
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SERVICE_VERSION = "source-provenance-service-v1.0"

ALLOWED_SOURCE_TYPES = {
    "unknown",
    "news_article",
    "company_announcement",
    "government_source",
    "regulatory_filing",
    "academic_source",
    "industry_report",
    "financial_filing",
    "blog",
    "press_release",
    "wire_story",
    "social_post",
    "webpage",
    "document",
}

ALLOWED_ORIGIN_TYPES = {
    "unknown",
    "publisher_original",
    "primary_source",
    "press_release",
    "wire_story",
    "regulatory_filing",
    "government_source",
    "company_announcement",
    "academic_source",
    "document",
    "derived_source",
}

ALLOWED_RELATIONSHIPS = {
    "same_source",
    "same_origin",
    "derived_from",
    "exact_republication",
    "possible_syndication",
    "independent",
    "unknown",
}

ALLOWED_INDEPENDENCE_RESULTS = {
    "same_origin",
    "independent",
    "unknown",
}

TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}

DEFAULT_SIMILARITY_THRESHOLD = 0.92


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SourceProvenanceError(RuntimeError):
    """Base exception for provenance errors."""


class SourceProvenanceValidationError(SourceProvenanceError):
    """Raised when an input violates the provenance contract."""


class SourceProvenanceIntegrityError(SourceProvenanceError):
    """Raised when stable identity or lineage becomes inconsistent."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _normalize_enum(value: Any) -> str:
    text = _clean(value).casefold()
    text = text.replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "", text)
    return text.strip("_")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

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


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _stable_id(prefix: str, payload: Any, length: int = 24) -> str:
    return (
        f"{prefix}-"
        + _sha256(
            _canonical_json_bytes(payload)
        )[:length].upper()
    )


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


def _canonicalize_url(url: str) -> str:
    url = _clean(url)

    if not url:
        return ""

    try:
        parts = urlsplit(url)
    except ValueError:
        return url

    if not parts.scheme or not parts.netloc:
        return url

    query_items = []

    for key, value in parse_qsl(
        parts.query,
        keep_blank_values=True,
    ):
        lowered = key.casefold()

        if lowered in TRACKING_PARAMETERS:
            continue

        if lowered.startswith("utm_"):
            continue

        query_items.append((key, value))

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


def _domain(url: str) -> str:
    try:
        return (
            urlsplit(url)
            .netloc
            .casefold()
            .removeprefix("www.")
        )
    except ValueError:
        return ""


def _lexical_similarity(left: str, right: str) -> float:
    """
    Deterministic fallback only.

    Production may inject local embedding similarity instead.
    """
    tokens = re.compile(
        r"[a-z0-9]+",
        re.IGNORECASE,
    )

    left_set = {
        item.casefold()
        for item in tokens.findall(left)
    }
    right_set = {
        item.casefold()
        for item in tokens.findall(right)
    }

    if not left_set and not right_set:
        return 1.0

    if not left_set or not right_set:
        return 0.0

    return (
        len(left_set & right_set)
        / len(left_set | right_set)
    )


def _source_text(fetch_record: Mapping[str, Any]) -> str:
    return _clean(
        fetch_record.get("text_content")
        or fetch_record.get("raw_text")
    )


def _source_hash(fetch_record: Mapping[str, Any]) -> str:
    existing = _clean(
        fetch_record.get("raw_sha256")
    )

    if existing:
        if not re.fullmatch(r"[a-fA-F0-9]{64}", existing):
            raise SourceProvenanceValidationError(
                "raw_sha256 must be a 64-character hexadecimal SHA-256."
            )
        return existing.casefold()

    body = fetch_record.get("content_bytes")

    if isinstance(body, bytearray):
        body = bytes(body)

    if isinstance(body, bytes):
        return _sha256(body)

    text = _source_text(fetch_record)

    if text:
        return _sha256(text)

    raise SourceProvenanceValidationError(
        "Source record must contain raw_sha256, content_bytes, "
        "text_content or raw_text."
    )


def _call_resolver(
    resolver: Callable[..., Any],
    *,
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> Any:
    try:
        signature = inspect.signature(resolver)
    except (TypeError, ValueError):
        return resolver(
            left=left,
            right=right,
        )

    accepts_var_kw = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )

    kwargs = {
        "left": left,
        "right": right,
    }

    if not accepts_var_kw:
        kwargs = {
            key: value
            for key, value in kwargs.items()
            if key in signature.parameters
        }

    return resolver(**kwargs)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class SourceProvenanceService:
    """
    Local provenance registry.

    The service is thread-safe for normal in-process use so parallel evidence
    discovery can register fetched sources safely.
    """

    def __init__(
        self,
        *,
        similarity_fn: Optional[
            Callable[[str, str], float]
        ] = None,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        independence_resolver: Optional[Callable[..., Any]] = None,
        clock: Callable[[], str] = _utc_now_iso,
    ) -> None:
        if similarity_fn is not None and not callable(similarity_fn):
            raise SourceProvenanceValidationError(
                "similarity_fn must be callable."
            )

        try:
            similarity_threshold = float(similarity_threshold)
        except (TypeError, ValueError) as exc:
            raise SourceProvenanceValidationError(
                "similarity_threshold must be numeric."
            ) from exc

        if not 0.0 <= similarity_threshold <= 1.0:
            raise SourceProvenanceValidationError(
                "similarity_threshold must be in [0, 1]."
            )

        if (
            independence_resolver is not None
            and not callable(independence_resolver)
        ):
            raise SourceProvenanceValidationError(
                "independence_resolver must be callable."
            )

        if not callable(clock):
            raise SourceProvenanceValidationError(
                "clock must be callable."
            )

        self.similarity_fn = (
            similarity_fn
            or _lexical_similarity
        )
        self.similarity_threshold = similarity_threshold
        self.independence_resolver = independence_resolver
        self.clock = clock

        self._lock = threading.RLock()

        # Stable logical source page -> current provenance record.
        self._sources: Dict[str, Dict[str, Any]] = {}

        # Version ID -> immutable source-version record.
        self._versions: Dict[str, Dict[str, Any]] = {}

        # Canonical URL -> source ID.
        self._url_index: Dict[str, str] = {}

        # Exact source content hash -> source/version IDs.
        self._content_hash_index: Dict[str, Set[str]] = {}

        # Canonical origin -> source IDs.
        self._origin_index: Dict[str, Set[str]] = {}

        # Syndication group -> source IDs.
        self._syndication_index: Dict[str, Set[str]] = {}

        # Evidence ID -> source provenance reference.
        self._evidence_index: Dict[str, Dict[str, Any]] = {}

        # Ordered pair source IDs -> pairwise assessment.
        self._pairwise_assessments: Dict[
            tuple[str, str],
            Dict[str, Any],
        ] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_source(
        self,
        fetched_source: Mapping[str, Any],
        *,
        source_type: str = "webpage",
        origin_type: str = "unknown",
        trusted_origin_key: str = "",
        derived_from_source_ids: Sequence[str] = (),
        attribution_text: str = "",
        discovery_context: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Register a fetched source.

        `trusted_origin_key`
        --------------------
        This is only for deterministic/trusted application data such as:
        - a canonical original URL supplied by a known primary source;
        - a deterministic feed/source identifier;
        - a human-reviewed common origin identifier.

        It should NOT be filled by an unconstrained LLM guess.

        `derived_from_source_ids`
        -------------------------
        Explicit lineage links. If source B is known to derive from registered
        source A, B inherits A's canonical origin and same-origin group.
        """
        if not isinstance(fetched_source, Mapping):
            raise SourceProvenanceValidationError(
                "fetched_source must be an object."
            )

        normalized_source_type = (
            _normalize_enum(source_type)
            or "unknown"
        )

        if normalized_source_type not in ALLOWED_SOURCE_TYPES:
            raise SourceProvenanceValidationError(
                f"Unsupported source_type={normalized_source_type!r}."
            )

        normalized_origin_type = (
            _normalize_enum(origin_type)
            or "unknown"
        )

        if normalized_origin_type not in ALLOWED_ORIGIN_TYPES:
            raise SourceProvenanceValidationError(
                f"Unsupported origin_type={normalized_origin_type!r}."
            )

        canonical_url = _canonicalize_url(
            fetched_source.get("canonical_url")
            or fetched_source.get("final_url")
            or fetched_source.get("requested_url")
            or fetched_source.get("url")
        )

        if not canonical_url:
            raise SourceProvenanceValidationError(
                "Source record is missing a usable URL."
            )

        raw_sha256 = _source_hash(
            fetched_source
        )

        source_text = _source_text(
            fetched_source
        )

        metadata = fetched_source.get("metadata") or {}

        if not isinstance(metadata, Mapping):
            raise SourceProvenanceValidationError(
                "fetched_source.metadata must be an object."
            )

        title = _clean(
            metadata.get("title")
            or fetched_source.get("title")
        )

        author = _clean(
            metadata.get("author")
            or fetched_source.get("author")
        )

        published_at = _clean(
            metadata.get("published_at")
            or fetched_source.get("published_at")
        )

        declared_canonical_url = _canonicalize_url(
            metadata.get("declared_canonical_url")
        )

        if declared_canonical_url:
            canonical_url = declared_canonical_url

        source_id = _stable_id(
            "SOURCE",
            {
                "canonical_url": canonical_url,
            },
        )

        version_id = _stable_id(
            "SOURCEVER",
            {
                "source_id": source_id,
                "raw_sha256": raw_sha256,
            },
        )

        derived_ids = self._normalize_source_ids(
            derived_from_source_ids
        )

        trusted_origin_key = _clean(
            trusted_origin_key
        )

        discovery = (
            deepcopy(dict(discovery_context))
            if isinstance(discovery_context, Mapping)
            else {}
        )

        now = self.clock()

        with self._lock:
            for parent_id in derived_ids:
                if parent_id not in self._sources:
                    raise SourceProvenanceValidationError(
                        "derived_from_source_ids contains an unknown source: "
                        f"{parent_id!r}."
                    )

            existing_source_id = self._url_index.get(
                canonical_url
            )

            if (
                existing_source_id
                and existing_source_id != source_id
            ):
                raise SourceProvenanceIntegrityError(
                    "Canonical URL index points to an inconsistent source ID."
                )

            if source_id in self._sources:
                return self._register_source_version(
                    source_id=source_id,
                    version_id=version_id,
                    raw_sha256=raw_sha256,
                    source_text=source_text,
                    fetched_source=fetched_source,
                    source_type=normalized_source_type,
                    origin_type=normalized_origin_type,
                    title=title,
                    author=author,
                    published_at=published_at,
                    attribution_text=attribution_text,
                    discovery=discovery,
                    now=now,
                )

            origin = self._determine_origin_for_new_source(
                canonical_url=canonical_url,
                raw_sha256=raw_sha256,
                trusted_origin_key=trusted_origin_key,
                derived_from_source_ids=derived_ids,
            )

            possible_syndication = self._find_possible_syndication_candidates(
                source_text=source_text,
                title=title,
                author=author,
                exclude_source_id=source_id,
            )

            source_record = {
                "source_id": source_id,
                "canonical_url": canonical_url,
                "domain": _domain(canonical_url),
                "source_type": normalized_source_type,
                "origin_type": normalized_origin_type,
                "canonical_origin_id": origin[
                    "canonical_origin_id"
                ],
                # IMPORTANT:
                # This group only means "known same underlying origin".
                # Different group IDs must not be treated as proof of
                # independence.
                "independence_group_id": origin[
                    "independence_group_id"
                ],
                "syndication_group_id": origin[
                    "syndication_group_id"
                ],
                "origin_basis": origin["origin_basis"],
                "origin_basis_source_ids": origin[
                    "origin_basis_source_ids"
                ],
                "derived_from_source_ids": derived_ids,
                "attribution_text": _clean(attribution_text),
                "possible_syndication_with_source_ids": (
                    possible_syndication
                ),
                "current_version_id": version_id,
                "version_ids": [version_id],
                "first_seen_at": now,
                "last_seen_at": now,
                "metadata": {
                    "title": title,
                    "author": author,
                    "published_at": published_at,
                },
            }

            version_record = {
                "source_version_id": version_id,
                "source_id": source_id,
                "raw_sha256": raw_sha256,
                "text_sha256": _clean(
                    fetched_source.get("text_sha256")
                ),
                "content_length_bytes": self._safe_int(
                    fetched_source.get("content_length_bytes")
                ),
                "content_type": _clean(
                    fetched_source.get("content_type")
                ),
                "title": title,
                "author": author,
                "published_at": published_at,
                "source_text": source_text,
                "fetched_at": now,
                "discovery_context": discovery,
            }

            self._sources[source_id] = source_record
            self._versions[version_id] = version_record
            self._url_index[canonical_url] = source_id
            self._content_hash_index.setdefault(
                raw_sha256,
                set(),
            ).add(source_id)

            if source_record["canonical_origin_id"]:
                self._origin_index.setdefault(
                    source_record[
                        "canonical_origin_id"
                    ],
                    set(),
                ).add(source_id)

            if source_record["syndication_group_id"]:
                self._syndication_index.setdefault(
                    source_record[
                        "syndication_group_id"
                    ],
                    set(),
                ).add(source_id)

            return self.get_source(
                source_id
            )

    def _register_source_version(
        self,
        *,
        source_id: str,
        version_id: str,
        raw_sha256: str,
        source_text: str,
        fetched_source: Mapping[str, Any],
        source_type: str,
        origin_type: str,
        title: str,
        author: str,
        published_at: str,
        attribution_text: str,
        discovery: Mapping[str, Any],
        now: str,
    ) -> Dict[str, Any]:
        source = self._sources[source_id]

        if version_id not in self._versions:
            version_record = {
                "source_version_id": version_id,
                "source_id": source_id,
                "raw_sha256": raw_sha256,
                "text_sha256": _clean(
                    fetched_source.get("text_sha256")
                ),
                "content_length_bytes": self._safe_int(
                    fetched_source.get("content_length_bytes")
                ),
                "content_type": _clean(
                    fetched_source.get("content_type")
                ),
                "title": title,
                "author": author,
                "published_at": published_at,
                "source_text": source_text,
                "fetched_at": now,
                "discovery_context": deepcopy(
                    dict(discovery)
                ),
            }

            self._versions[version_id] = version_record
            source["version_ids"].append(
                version_id
            )

            self._content_hash_index.setdefault(
                raw_sha256,
                set(),
            ).add(source_id)

        source["current_version_id"] = version_id
        source["last_seen_at"] = now

        if source_type != "unknown":
            source["source_type"] = source_type

        if origin_type != "unknown":
            source["origin_type"] = origin_type

        if attribution_text:
            source["attribution_text"] = _clean(
                attribution_text
            )

        source["metadata"] = {
            "title": (
                title
                or source["metadata"].get("title", "")
            ),
            "author": (
                author
                or source["metadata"].get("author", "")
            ),
            "published_at": (
                published_at
                or source["metadata"].get("published_at", "")
            ),
        }

        return self.get_source(
            source_id
        )

    def _determine_origin_for_new_source(
        self,
        *,
        canonical_url: str,
        raw_sha256: str,
        trusted_origin_key: str,
        derived_from_source_ids: Sequence[str],
    ) -> Dict[str, Any]:
        # 1. Explicit trusted origin key.
        if trusted_origin_key:
            origin_id = _stable_id(
                "ORIGIN",
                {
                    "trusted_origin_key": trusted_origin_key,
                },
            )

            group_id = _stable_id(
                "ORIGINGROUP",
                {
                    "canonical_origin_id": origin_id,
                },
            )

            return {
                "canonical_origin_id": origin_id,
                "independence_group_id": group_id,
                "syndication_group_id": "",
                "origin_basis": "trusted_origin_key",
                "origin_basis_source_ids": [],
            }

        # 2. Explicit derivation from already registered source(s).
        if derived_from_source_ids:
            parent_origins = {
                self._sources[source_id].get(
                    "canonical_origin_id",
                    "",
                )
                for source_id in derived_from_source_ids
            }

            parent_origins.discard("")

            if len(parent_origins) == 1:
                origin_id = next(
                    iter(parent_origins)
                )

                group_id = _stable_id(
                    "ORIGINGROUP",
                    {
                        "canonical_origin_id": origin_id,
                    },
                )

                return {
                    "canonical_origin_id": origin_id,
                    "independence_group_id": group_id,
                    "syndication_group_id": "",
                    "origin_basis": "derived_from_registered_source",
                    "origin_basis_source_ids": list(
                        derived_from_source_ids
                    ),
                }

            if len(parent_origins) > 1:
                # Composite derivation means we cannot collapse the source to
                # one origin without losing lineage information.
                return {
                    "canonical_origin_id": "",
                    "independence_group_id": "",
                    "syndication_group_id": "",
                    "origin_basis": "multiple_parent_origins_unknown",
                    "origin_basis_source_ids": list(
                        derived_from_source_ids
                    ),
                }

        # 3. Exact content hash appears elsewhere.
        exact_source_ids = sorted(
            self._content_hash_index.get(
                raw_sha256,
                set(),
            )
        )

        if exact_source_ids:
            strong_origin_bases = {
                "trusted_origin_key",
                "derived_from_registered_source",
                "exact_content_republication",
            }

            strong_origins = {
                self._sources[source_id].get(
                    "canonical_origin_id",
                    "",
                )
                for source_id in exact_source_ids
                if (
                    source_id in self._sources
                    and self._sources[source_id].get(
                        "origin_basis"
                    ) in strong_origin_bases
                )
            }

            strong_origins.discard("")

            if len(strong_origins) == 1:
                # Preserve an already-established stronger origin.
                origin_id = next(
                    iter(strong_origins)
                )
            elif len(strong_origins) > 1:
                raise SourceProvenanceIntegrityError(
                    "Exact-content copies are already attached to conflicting "
                    "strong origins."
                )
            else:
                # Page identity alone is not a sufficiently strong origin once
                # an exact republished copy appears elsewhere. Promote the
                # cluster to an exact-content origin.
                origin_id = _stable_id(
                    "ORIGIN",
                    {
                        "exact_content_sha256": raw_sha256,
                    },
                )

            # Always backfill prior exact copies so they share the same
            # same-origin/syndication group. Without this, the newly observed
            # copy could be grouped while the earlier page remained ungrouped.
            self._backfill_exact_origin(
                source_ids=exact_source_ids,
                origin_id=origin_id,
                raw_sha256=raw_sha256,
            )

            syndication_group_id = _stable_id(
                "SYND",
                {
                    "exact_content_sha256": raw_sha256,
                },
            )

            group_id = _stable_id(
                "ORIGINGROUP",
                {
                    "canonical_origin_id": origin_id,
                },
            )

            return {
                "canonical_origin_id": origin_id,
                "independence_group_id": group_id,
                "syndication_group_id": syndication_group_id,
                "origin_basis": "exact_content_republication",
                "origin_basis_source_ids": exact_source_ids,
            }

        # 4. We know the page identity, but not whether another publisher later
        # turns out to share its underlying origin. Do NOT invent an
        # independence group here.
        origin_id = _stable_id(
            "ORIGIN",
            {
                "canonical_source_url": canonical_url,
            },
        )

        return {
            "canonical_origin_id": origin_id,
            "independence_group_id": "",
            "syndication_group_id": "",
            "origin_basis": "canonical_source_page_only",
            "origin_basis_source_ids": [],
        }

    def _backfill_exact_origin(
        self,
        *,
        source_ids: Sequence[str],
        origin_id: str,
        raw_sha256: str,
    ) -> None:
        group_id = _stable_id(
            "ORIGINGROUP",
            {
                "canonical_origin_id": origin_id,
            },
        )

        syndication_group_id = _stable_id(
            "SYND",
            {
                "exact_content_sha256": raw_sha256,
            },
        )

        for source_id in source_ids:
            source = self._sources.get(
                source_id
            )

            if source is None:
                continue

            old_origin = source.get(
                "canonical_origin_id",
                "",
            )

            if old_origin:
                self._origin_index.get(
                    old_origin,
                    set(),
                ).discard(source_id)

            source["canonical_origin_id"] = origin_id
            source["independence_group_id"] = group_id
            source["syndication_group_id"] = syndication_group_id
            source["origin_basis"] = "exact_content_republication"

            self._origin_index.setdefault(
                origin_id,
                set(),
            ).add(source_id)

            self._syndication_index.setdefault(
                syndication_group_id,
                set(),
            ).add(source_id)

    # ------------------------------------------------------------------
    # Possible syndication
    # ------------------------------------------------------------------

    def _find_possible_syndication_candidates(
        self,
        *,
        source_text: str,
        title: str,
        author: str,
        exclude_source_id: str,
    ) -> list[Dict[str, Any]]:
        if not source_text:
            return []

        candidates = []

        for source_id, source in self._sources.items():
            if source_id == exclude_source_id:
                continue

            version_id = source.get(
                "current_version_id"
            )

            version = self._versions.get(
                version_id,
                {},
            )

            other_text = _clean(
                version.get(
                    "source_text"
                )
            )

            if not other_text:
                continue

            similarity = float(
                self.similarity_fn(
                    source_text,
                    other_text,
                )
            )

            if not 0.0 <= similarity <= 1.0:
                raise SourceProvenanceValidationError(
                    "similarity_fn must return a value in [0, 1]."
                )

            if similarity < self.similarity_threshold:
                continue

            same_title = bool(
                title
                and _clean(
                    source.get(
                        "metadata",
                    ).get(
                        "title"
                    )
                ).casefold()
                == title.casefold()
            )

            same_author = bool(
                author
                and _clean(
                    source.get(
                        "metadata",
                    ).get(
                        "author"
                    )
                ).casefold()
                == author.casefold()
            )

            candidates.append(
                {
                    "source_id": source_id,
                    "semantic_similarity": similarity,
                    "same_title": same_title,
                    "same_author": same_author,
                    "status": "possible_syndication",
                    "same_origin_established": False,
                }
            )

        candidates.sort(
            key=lambda item: (
                -item[
                    "semantic_similarity"
                ],
                item["source_id"],
            )
        )

        return candidates

    # ------------------------------------------------------------------
    # Pairwise provenance / independence
    # ------------------------------------------------------------------

    def compare_sources(
        self,
        left_source_id: str,
        right_source_id: str,
        *,
        use_resolver: bool = True,
    ) -> Dict[str, Any]:
        """
        Compare two registered source pages.

        Possible `assessment` values:
            same_origin
            independent
            unknown

        Different domains are never enough for "independent".
        """
        left_source_id = _clean(
            left_source_id
        )
        right_source_id = _clean(
            right_source_id
        )

        with self._lock:
            left = self._require_source(
                left_source_id
            )
            right = self._require_source(
                right_source_id
            )

            pair_key = self._pair_key(
                left_source_id,
                right_source_id,
            )

            stored = self._pairwise_assessments.get(
                pair_key
            )

            if stored is not None:
                return deepcopy(
                    stored
                )

            deterministic = self._deterministic_pairwise_assessment(
                left,
                right,
            )

            if deterministic["assessment"] != "unknown":
                self._pairwise_assessments[
                    pair_key
                ] = deterministic
                return deepcopy(
                    deterministic
                )

            if (
                use_resolver
                and self.independence_resolver is not None
            ):
                resolver_result = _call_resolver(
                    self.independence_resolver,
                    left=self._public_source_record(left),
                    right=self._public_source_record(right),
                )

                resolved = self._normalize_resolver_result(
                    resolver_result,
                    left_source_id=left_source_id,
                    right_source_id=right_source_id,
                )

                self._pairwise_assessments[
                    pair_key
                ] = resolved

                return deepcopy(
                    resolved
                )

            result = {
                "left_source_id": left_source_id,
                "right_source_id": right_source_id,
                "assessment": "unknown",
                "basis": "insufficient_provenance",
                "details": (
                    "No deterministic common origin was established, but "
                    "independence is also not proven."
                ),
                "assessed_at": self.clock(),
                "resolver_used": False,
            }

            self._pairwise_assessments[
                pair_key
            ] = result

            return deepcopy(
                result
            )

    def _deterministic_pairwise_assessment(
        self,
        left: Mapping[str, Any],
        right: Mapping[str, Any],
    ) -> Dict[str, Any]:
        left_id = left["source_id"]
        right_id = right["source_id"]

        if left_id == right_id:
            return self._pair_result(
                left_id,
                right_id,
                assessment="same_origin",
                basis="same_source_id",
                details="Both references resolve to the same canonical source.",
            )

        if left["canonical_url"] == right["canonical_url"]:
            return self._pair_result(
                left_id,
                right_id,
                assessment="same_origin",
                basis="same_canonical_url",
                details="Both references resolve to the same canonical URL.",
            )

        left_version = self._versions[
            left["current_version_id"]
        ]
        right_version = self._versions[
            right["current_version_id"]
        ]

        if (
            left_version["raw_sha256"]
            == right_version["raw_sha256"]
        ):
            return self._pair_result(
                left_id,
                right_id,
                assessment="same_origin",
                basis="exact_content_hash",
                details=(
                    "Current source versions have identical raw content hashes."
                ),
            )

        left_origin = _clean(
            left.get(
                "canonical_origin_id"
            )
        )
        right_origin = _clean(
            right.get(
                "canonical_origin_id"
            )
        )

        left_basis = _clean(
            left.get(
                "origin_basis"
            )
        )
        right_basis = _clean(
            right.get(
                "origin_basis"
            )
        )

        # canonical_source_page_only is not enough to prove shared/independent
        # origin across different pages.
        strong_origin_bases = {
            "trusted_origin_key",
            "derived_from_registered_source",
            "exact_content_republication",
        }

        if (
            left_origin
            and right_origin
            and left_origin == right_origin
            and (
                left_basis in strong_origin_bases
                or right_basis in strong_origin_bases
            )
        ):
            return self._pair_result(
                left_id,
                right_id,
                assessment="same_origin",
                basis="shared_canonical_origin",
                details="Both sources have a deterministically shared origin.",
            )

        if (
            right_id
            in left.get(
                "derived_from_source_ids",
                []
            )
            or left_id
            in right.get(
                "derived_from_source_ids",
                []
            )
        ):
            return self._pair_result(
                left_id,
                right_id,
                assessment="same_origin",
                basis="explicit_derivation",
                details="One source is explicitly recorded as derived from the other.",
            )

        return self._pair_result(
            left_id,
            right_id,
            assessment="unknown",
            basis="insufficient_provenance",
            details=(
                "Different source pages/domains do not establish independence."
            ),
        )

    def register_pairwise_independence(
        self,
        left_source_id: str,
        right_source_id: str,
        *,
        assessment: str,
        basis: str,
        details: str = "",
        trusted: bool = False,
    ) -> Dict[str, Any]:
        """
        Store a trusted pairwise provenance assessment.

        This method intentionally requires `trusted=True`. It is suitable for a
        deterministic resolver, reviewed dataset, or explicit application-level
        provenance fact. Do not call it with unconstrained LLM guesses.
        """
        if not trusted:
            raise SourceProvenanceValidationError(
                "register_pairwise_independence requires trusted=True."
            )

        assessment = _normalize_enum(
            assessment
        )

        if assessment not in ALLOWED_INDEPENDENCE_RESULTS:
            raise SourceProvenanceValidationError(
                f"Unsupported assessment={assessment!r}."
            )

        left_source_id = _clean(
            left_source_id
        )
        right_source_id = _clean(
            right_source_id
        )

        basis = _clean(
            basis
        )

        if not basis:
            raise SourceProvenanceValidationError(
                "basis cannot be empty."
            )

        with self._lock:
            self._require_source(
                left_source_id
            )
            self._require_source(
                right_source_id
            )

            deterministic = self._deterministic_pairwise_assessment(
                self._sources[
                    left_source_id
                ],
                self._sources[
                    right_source_id
                ],
            )

            if (
                deterministic["assessment"] == "same_origin"
                and assessment == "independent"
            ):
                raise SourceProvenanceIntegrityError(
                    "Cannot override deterministic same-origin evidence with "
                    "an independent assessment."
                )

            result = self._pair_result(
                left_source_id,
                right_source_id,
                assessment=assessment,
                basis=basis,
                details=details,
                resolver_used=False,
                trusted_assertion=True,
            )

            self._pairwise_assessments[
                self._pair_key(
                    left_source_id,
                    right_source_id,
                )
            ] = result

            return deepcopy(
                result
            )

    def _normalize_resolver_result(
        self,
        raw: Any,
        *,
        left_source_id: str,
        right_source_id: str,
    ) -> Dict[str, Any]:
        if isinstance(raw, str):
            raw = {
                "assessment": raw,
            }

        if not isinstance(raw, Mapping):
            raise SourceProvenanceValidationError(
                "independence_resolver must return a string or object."
            )

        assessment = _normalize_enum(
            raw.get(
                "assessment"
            )
            or raw.get(
                "status"
            )
        )

        if assessment not in ALLOWED_INDEPENDENCE_RESULTS:
            raise SourceProvenanceValidationError(
                "independence_resolver returned an invalid assessment."
            )

        basis = _clean(
            raw.get(
                "basis"
            )
        )

        if not basis:
            basis = "external_resolver"

        return self._pair_result(
            left_source_id,
            right_source_id,
            assessment=assessment,
            basis=basis,
            details=_clean(
                raw.get(
                    "details"
                )
                or raw.get(
                    "reason"
                )
            ),
            resolver_used=True,
        )

    def _pair_result(
        self,
        left_source_id: str,
        right_source_id: str,
        *,
        assessment: str,
        basis: str,
        details: str,
        resolver_used: bool = False,
        trusted_assertion: bool = False,
    ) -> Dict[str, Any]:
        return {
            "left_source_id": left_source_id,
            "right_source_id": right_source_id,
            "assessment": assessment,
            "basis": basis,
            "details": _clean(details),
            "assessed_at": self.clock(),
            "resolver_used": bool(
                resolver_used
            ),
            "trusted_assertion": bool(
                trusted_assertion
            ),
        }

    # ------------------------------------------------------------------
    # Evidence decoration / lineage
    # ------------------------------------------------------------------

    def decorate_evidence(
        self,
        evidence_record: Mapping[str, Any],
        *,
        source_id: str,
    ) -> Dict[str, Any]:
        """
        Attach source provenance to an already-created evidence record.

        Does not create/verify the evidence claim itself.
        """
        if not isinstance(evidence_record, Mapping):
            raise SourceProvenanceValidationError(
                "evidence_record must be an object."
            )

        evidence_id = _clean(
            evidence_record.get(
                "evidence_id"
            )
            or evidence_record.get(
                "id"
            )
        )

        if not evidence_id:
            raise SourceProvenanceValidationError(
                "evidence_record must contain evidence_id."
            )

        source_id = _clean(
            source_id
        )

        with self._lock:
            source = self._require_source(
                source_id
            )

            provenance = {
                "source_id": source_id,
                "source_version_id": source[
                    "current_version_id"
                ],
                "canonical_url": source[
                    "canonical_url"
                ],
                "canonical_origin_id": source[
                    "canonical_origin_id"
                ],
                "independence_group_id": source[
                    "independence_group_id"
                ],
                "syndication_group_id": source[
                    "syndication_group_id"
                ],
                "origin_basis": source[
                    "origin_basis"
                ],
                "derived_from_source_ids": list(
                    source[
                        "derived_from_source_ids"
                    ]
                ),
                "possible_syndication_with_source_ids": deepcopy(
                    source[
                        "possible_syndication_with_source_ids"
                    ]
                ),
                "independence_status": (
                    "same_origin_group_known"
                    if source[
                        "independence_group_id"
                    ]
                    else "not_established"
                ),
            }

            decorated = deepcopy(
                dict(
                    evidence_record
                )
            )

            decorated[
                "source_id"
            ] = source_id
            decorated[
                "source_provenance"
            ] = provenance

            # Mirror fields used by older/current callers while preserving the
            # richer nested provenance object.
            decorated[
                "canonical_origin_id"
            ] = source[
                "canonical_origin_id"
            ]
            decorated[
                "independence_group_id"
            ] = source[
                "independence_group_id"
            ]
            decorated[
                "syndication_group_id"
            ] = source[
                "syndication_group_id"
            ]

            existing = self._evidence_index.get(
                evidence_id
            )

            reference = {
                "evidence_id": evidence_id,
                "source_id": source_id,
                "source_version_id": source[
                    "current_version_id"
                ],
                "attached_at": self.clock(),
            }

            if (
                existing is not None
                and (
                    existing["source_id"] != source_id
                    or existing[
                        "source_version_id"
                    ]
                    != source[
                        "current_version_id"
                    ]
                )
            ):
                raise SourceProvenanceIntegrityError(
                    "Evidence ID is already attached to a different source "
                    "or source version."
                )

            self._evidence_index[
                evidence_id
            ] = reference

            return decorated

    def evidence_lineage(
        self,
        evidence_id: str,
    ) -> Dict[str, Any]:
        evidence_id = _clean(
            evidence_id
        )

        with self._lock:
            reference = self._evidence_index.get(
                evidence_id
            )

            if reference is None:
                raise SourceProvenanceValidationError(
                    f"Unknown evidence_id={evidence_id!r}."
                )

            source = self._sources[
                reference[
                    "source_id"
                ]
            ]

            return {
                "evidence_id": evidence_id,
                "source_id": source[
                    "source_id"
                ],
                "source_version_id": reference[
                    "source_version_id"
                ],
                "canonical_url": source[
                    "canonical_url"
                ],
                "canonical_origin_id": source[
                    "canonical_origin_id"
                ],
                "origin_basis": source[
                    "origin_basis"
                ],
                "derived_from_source_ids": list(
                    source[
                        "derived_from_source_ids"
                    ]
                ),
                "syndication_group_id": source[
                    "syndication_group_id"
                ],
                "possible_syndication_with_source_ids": deepcopy(
                    source[
                        "possible_syndication_with_source_ids"
                    ]
                ),
            }

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get_source(
        self,
        source_id: str,
    ) -> Dict[str, Any]:
        source_id = _clean(
            source_id
        )

        with self._lock:
            source = self._require_source(
                source_id
            )

            return self._public_source_record(
                source
            )

    def get_source_by_url(
        self,
        url: str,
    ) -> Optional[Dict[str, Any]]:
        canonical_url = _canonicalize_url(
            url
        )

        with self._lock:
            source_id = self._url_index.get(
                canonical_url
            )

            if source_id is None:
                return None

            return self.get_source(
                source_id
            )

    def get_version(
        self,
        source_version_id: str,
    ) -> Dict[str, Any]:
        source_version_id = _clean(
            source_version_id
        )

        with self._lock:
            record = self._versions.get(
                source_version_id
            )

            if record is None:
                raise SourceProvenanceValidationError(
                    f"Unknown source_version_id={source_version_id!r}."
                )

            # Do not expose complete source text through provenance snapshots.
            return {
                key: deepcopy(value)
                for key, value in record.items()
                if key != "source_text"
            }

    def list_sources_for_origin(
        self,
        canonical_origin_id: str,
    ) -> list[Dict[str, Any]]:
        canonical_origin_id = _clean(
            canonical_origin_id
        )

        with self._lock:
            ids = sorted(
                self._origin_index.get(
                    canonical_origin_id,
                    set(),
                )
            )

            return [
                self.get_source(
                    source_id
                )
                for source_id in ids
            ]

    def list_sources_for_syndication_group(
        self,
        syndication_group_id: str,
    ) -> list[Dict[str, Any]]:
        syndication_group_id = _clean(
            syndication_group_id
        )

        with self._lock:
            ids = sorted(
                self._syndication_index.get(
                    syndication_group_id,
                    set(),
                )
            )

            return [
                self.get_source(
                    source_id
                )
                for source_id in ids
            ]

    # ------------------------------------------------------------------
    # Source-level corroboration summary
    # ------------------------------------------------------------------

    def summarize_evidence_sources(
        self,
        evidence_ids: Sequence[str],
    ) -> Dict[str, Any]:
        """
        Summarize provenance for a set of evidence IDs.

        This is useful for GroundingService / InsightTraceService.

        It intentionally reports pairwise unknowns instead of pretending all
        distinct pages are independent.
        """
        evidence_ids = [
            _clean(item)
            for item in evidence_ids
            if _clean(item)
        ]

        with self._lock:
            references = []

            for evidence_id in evidence_ids:
                reference = self._evidence_index.get(
                    evidence_id
                )

                if reference is None:
                    raise SourceProvenanceValidationError(
                        f"Unknown evidence_id={evidence_id!r}."
                    )

                references.append(
                    deepcopy(
                        reference
                    )
                )

            source_ids = sorted(
                {
                    reference[
                        "source_id"
                    ]
                    for reference
                    in references
                }
            )

            same_origin_pairs = []
            independent_pairs = []
            unknown_pairs = []

            for index, left_id in enumerate(
                source_ids
            ):
                for right_id in source_ids[
                    index + 1:
                ]:
                    assessment = self.compare_sources(
                        left_id,
                        right_id,
                    )

                    compact = {
                        "left_source_id": left_id,
                        "right_source_id": right_id,
                        "basis": assessment[
                            "basis"
                        ],
                    }

                    if (
                        assessment[
                            "assessment"
                        ]
                        == "same_origin"
                    ):
                        same_origin_pairs.append(
                            compact
                        )
                    elif (
                        assessment[
                            "assessment"
                        ]
                        == "independent"
                    ):
                        independent_pairs.append(
                            compact
                        )
                    else:
                        unknown_pairs.append(
                            compact
                        )

            return {
                "evidence_ids": evidence_ids,
                "source_ids": source_ids,
                "source_count": len(
                    source_ids
                ),
                "same_origin_pairs": same_origin_pairs,
                "independent_pairs": independent_pairs,
                "unknown_independence_pairs": unknown_pairs,
                "independence_fully_established": bool(
                    len(source_ids) <= 1
                    or (
                        not same_origin_pairs
                        and not unknown_pairs
                    )
                ),
                "warning": (
                    ""
                    if (
                        len(source_ids) <= 1
                        or (
                            not same_origin_pairs
                            and not unknown_pairs
                        )
                    )
                    else (
                        "Some source pairs share an origin or have unresolved "
                        "independence; do not count distinct URLs as fully "
                        "independent corroboration."
                    )
                ),
            }

    # ------------------------------------------------------------------
    # Snapshot / restoration
    # ------------------------------------------------------------------

    def export_snapshot(
        self,
    ) -> Dict[str, Any]:
        """
        Export provenance metadata without raw fetched content.

        `source_text` used for similarity comparisons is intentionally omitted.
        """
        with self._lock:
            versions = {}

            for version_id, version in self._versions.items():
                versions[
                    version_id
                ] = {
                    key: deepcopy(value)
                    for key, value in version.items()
                    if key != "source_text"
                }

            return {
                "service_version": SERVICE_VERSION,
                "sources": deepcopy(
                    self._sources
                ),
                "versions": versions,
                "evidence_index": deepcopy(
                    self._evidence_index
                ),
                "pairwise_assessments": [
                    deepcopy(value)
                    for _, value
                    in sorted(
                        self._pairwise_assessments.items(),
                        key=lambda item: item[0],
                    )
                ],
                "metadata": {
                    "different_urls_imply_independence": False,
                    "different_domains_imply_independence": False,
                    "semantic_similarity_alone_establishes_same_origin": False,
                    "private_chain_of_thought_stored": False,
                },
            }

    # ------------------------------------------------------------------
    # Internal validation / utilities
    # ------------------------------------------------------------------

    def _public_source_record(
        self,
        source: Mapping[str, Any],
    ) -> Dict[str, Any]:
        return deepcopy(
            dict(
                source
            )
        )

    def _require_source(
        self,
        source_id: str,
    ) -> Dict[str, Any]:
        source = self._sources.get(
            source_id
        )

        if source is None:
            raise SourceProvenanceValidationError(
                f"Unknown source_id={source_id!r}."
            )

        return source

    def _pair_key(
        self,
        left_source_id: str,
        right_source_id: str,
    ) -> tuple[str, str]:
        return tuple(
            sorted(
                (
                    left_source_id,
                    right_source_id,
                )
            )
        )

    def _normalize_source_ids(
        self,
        values: Sequence[str],
    ) -> list[str]:
        if values is None:
            return []

        if isinstance(values, str):
            values = [values]

        if (
            not isinstance(values, Sequence)
            or isinstance(values, (bytes, bytearray))
        ):
            raise SourceProvenanceValidationError(
                "derived_from_source_ids must be a sequence."
            )

        result = []
        seen = set()

        for value in values:
            source_id = _clean(
                value
            )

            if source_id and source_id not in seen:
                seen.add(source_id)
                result.append(
                    source_id
                )

        return result

    def _safe_int(
        self,
        value: Any,
    ) -> Optional[int]:
        if value is None or value == "":
            return None

        try:
            return int(value)
        except (TypeError, ValueError):
            return None