"""
Embedding generation service.

Recommended location:
    services/embedding_service.py

Role
----
Text / structured records
        ↓
EmbeddingService
        ↓
dense vectors + deterministic metadata
        ↓
VectorService / clustering / diversity / retrieval

Responsibilities
----------------
- Generate embeddings from text.
- Batch inputs without silently dropping records.
- Validate vector dimensions and finite numeric values.
- Optionally L2-normalize vectors.
- Produce deterministic input fingerprints for telemetry/evaluation.
- Support dependency injection so the service is not tied to one provider.

Non-responsibilities
--------------------
- No vector database persistence.
- No similarity search.
- No event clustering.
- No strategic inference.
- No model routing policy.
- No arbitrary text truncation.

Architecture distinction
------------------------
EmbeddingService:
    text -> vector

VectorService:
    vectors -> store / index / search

ModelRouterService may choose WHICH embedding model to use. This service
executes the selected embedding backend.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, asdict
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence


DEFAULT_PROMPT_VERSION = "embedding-v1"


class EmbeddingServiceError(RuntimeError):
    """Base exception for embedding failures."""


class EmbeddingValidationError(EmbeddingServiceError):
    """Raised when input or model output violates the embedding contract."""


@dataclass(frozen=True)
class EmbeddingResult:
    text: str
    embedding: List[float]
    dimension: int
    normalized: bool
    input_fingerprint: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def _sha256_hex(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")

    return hashlib.sha256(value).hexdigest()


def _l2_normalize(vector: Sequence[float]) -> List[float]:
    norm = math.sqrt(
        sum(float(value) ** 2 for value in vector)
    )

    if math.isclose(norm, 0.0, abs_tol=1e-15):
        raise EmbeddingValidationError(
            "Cannot normalize a zero embedding vector."
        )

    return [
        float(value) / norm
        for value in vector
    ]


def _validate_vector(
    vector: Any,
    *,
    expected_dimension: Optional[int] = None,
) -> List[float]:
    if not isinstance(
        vector,
        (list, tuple),
    ):
        # numpy arrays and similar objects often support tolist().
        if hasattr(vector, "tolist"):
            vector = vector.tolist()
        else:
            raise EmbeddingValidationError(
                "Embedding backend must return a vector-like sequence."
            )

    result: List[float] = []

    for index, value in enumerate(vector):
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise EmbeddingValidationError(
                f"Embedding value at position {index} is not numeric."
            ) from exc

        if not math.isfinite(numeric):
            raise EmbeddingValidationError(
                f"Embedding value at position {index} is not finite."
            )

        result.append(numeric)

    if not result:
        raise EmbeddingValidationError(
            "Embedding vector cannot be empty."
        )

    if (
        expected_dimension is not None
        and len(result) != expected_dimension
    ):
        raise EmbeddingValidationError(
            "Embedding dimension mismatch: "
            f"expected {expected_dimension}, received {len(result)}."
        )

    return result


class EmbeddingService:
    """
    Provider-agnostic embedding service.

    Parameters
    ----------
    embed_callable:
        Callable receiving List[str] and returning one vector per input text.

        Example:
            def embed_batch(texts: list[str]) -> list[list[float]]:
                ...

    model_name:
        Metadata identifying the embedding model/version.

    normalize:
        When True, every returned vector is L2-normalized.

    batch_size:
        Number of records sent to the backend in one call.
        Every input record is still processed; batching is not truncation.

    expected_dimension:
        Optional fixed dimension contract. If omitted, the dimension of the
        first successful vector becomes the service-level runtime expectation
        for that call.
    """

    def __init__(
        self,
        *,
        embed_callable: Callable[
            [List[str]],
            Sequence[Sequence[float]],
        ],
        model_name: str,
        normalize: bool = True,
        batch_size: int = 32,
        expected_dimension: Optional[int] = None,
        prompt_version: str = DEFAULT_PROMPT_VERSION,
    ) -> None:
        if not callable(embed_callable):
            raise EmbeddingValidationError(
                "embed_callable must be callable."
            )

        model_name = _clean_text(model_name)

        if not model_name:
            raise EmbeddingValidationError(
                "model_name cannot be empty."
            )

        if batch_size <= 0:
            raise EmbeddingValidationError(
                "batch_size must be > 0."
            )

        if (
            expected_dimension is not None
            and expected_dimension <= 0
        ):
            raise EmbeddingValidationError(
                "expected_dimension must be > 0."
            )

        prompt_version = _clean_text(
            prompt_version
        )

        if not prompt_version:
            raise EmbeddingValidationError(
                "prompt_version cannot be empty."
            )

        self.embed_callable = embed_callable
        self.model_name = model_name
        self.normalize = bool(normalize)
        self.batch_size = int(batch_size)
        self.expected_dimension = expected_dimension
        self.prompt_version = prompt_version

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed_text(
        self,
        text: str,
    ) -> EmbeddingResult:
        return self.embed_texts(
            [text]
        )[0]

    def embed_texts(
        self,
        texts: Sequence[str],
    ) -> List[EmbeddingResult]:
        """
        Embed every supplied text.

        No hidden top-k or character truncation occurs here.
        Empty strings fail explicitly instead of silently disappearing.
        """
        if not isinstance(
            texts,
            Sequence,
        ) or isinstance(
            texts,
            (str, bytes),
        ):
            raise EmbeddingValidationError(
                "texts must be a sequence of strings."
            )

        if not texts:
            return []

        normalized_texts: List[str] = []

        for index, text in enumerate(texts):
            cleaned = _clean_text(text)

            if not cleaned:
                raise EmbeddingValidationError(
                    f"texts[{index}] cannot be empty."
                )

            normalized_texts.append(
                cleaned
            )

        results: List[EmbeddingResult] = []
        runtime_dimension = self.expected_dimension

        for start in range(
            0,
            len(normalized_texts),
            self.batch_size,
        ):
            batch = normalized_texts[
                start:start + self.batch_size
            ]

            raw_vectors = self.embed_callable(
                list(batch)
            )

            if hasattr(
                raw_vectors,
                "tolist",
            ):
                raw_vectors = (
                    raw_vectors.tolist()
                )

            if not isinstance(
                raw_vectors,
                (list, tuple),
            ):
                raise EmbeddingValidationError(
                    "Embedding backend must return one vector per input text."
                )

            if len(raw_vectors) != len(batch):
                raise EmbeddingValidationError(
                    "Embedding backend returned the wrong number of vectors: "
                    f"expected {len(batch)}, received {len(raw_vectors)}."
                )

            for text, raw_vector in zip(
                batch,
                raw_vectors,
            ):
                vector = _validate_vector(
                    raw_vector,
                    expected_dimension=(
                        runtime_dimension
                    ),
                )

                if runtime_dimension is None:
                    runtime_dimension = len(
                        vector
                    )

                if self.normalize:
                    vector = _l2_normalize(
                        vector
                    )

                fingerprint_payload = {
                    "text": text,
                    "model_name": self.model_name,
                    "prompt_version": self.prompt_version,
                    "normalized": self.normalize,
                }

                results.append(
                    EmbeddingResult(
                        text=text,
                        embedding=vector,
                        dimension=len(vector),
                        normalized=self.normalize,
                        input_fingerprint=_sha256_hex(
                            _canonical_json_bytes(
                                fingerprint_payload
                            )
                        ),
                    )
                )

        if len(results) != len(normalized_texts):
            raise EmbeddingServiceError(
                "Embedding completeness invariant failed: "
                "not every input text produced an embedding."
            )

        return results

    def embed_records(
        self,
        records: Sequence[
            Mapping[str, Any]
        ],
        *,
        id_field: str,
        text_field: str,
    ) -> List[Dict[str, Any]]:
        """
        Embed structured records while preserving their IDs.

        This is useful for:
        - events
        - clusters
        - evidence records
        - report-history items

        The service never reorders or drops records.
        """
        if not isinstance(
            records,
            Sequence,
        ) or isinstance(
            records,
            (str, bytes),
        ):
            raise EmbeddingValidationError(
                "records must be a sequence of objects."
            )

        record_ids: List[str] = []
        texts: List[str] = []

        seen_ids = set()

        for index, record in enumerate(
            records
        ):
            if not isinstance(
                record,
                Mapping,
            ):
                raise EmbeddingValidationError(
                    f"records[{index}] must be an object."
                )

            record_id = _clean_text(
                record.get(
                    id_field
                )
            )

            text = _clean_text(
                record.get(
                    text_field
                )
            )

            if not record_id:
                raise EmbeddingValidationError(
                    f"records[{index}] is missing {id_field!r}."
                )

            if record_id in seen_ids:
                raise EmbeddingValidationError(
                    f"Duplicate record ID {record_id!r}."
                )

            if not text:
                raise EmbeddingValidationError(
                    f"records[{index}] has empty {text_field!r}."
                )

            seen_ids.add(
                record_id
            )
            record_ids.append(
                record_id
            )
            texts.append(
                text
            )

        embeddings = self.embed_texts(
            texts
        )

        if len(embeddings) != len(record_ids):
            raise EmbeddingServiceError(
                "Embedding completeness invariant failed for structured records."
            )

        return [
            {
                "record_id": record_id,
                "text": result.text,
                "embedding": result.embedding,
                "dimension": result.dimension,
                "normalized": result.normalized,
                "input_fingerprint": result.input_fingerprint,
                "model_name": self.model_name,
                "prompt_version": self.prompt_version,
            }
            for record_id, result in zip(
                record_ids,
                embeddings,
            )
        ]

    def metadata(
        self,
    ) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "normalize": self.normalize,
            "batch_size": self.batch_size,
            "expected_dimension": self.expected_dimension,
            "prompt_version": self.prompt_version,
        }


class SentenceTransformerEmbeddingBackend:
    """
    Optional local backend for sentence-transformers.

    Import/model loading is lazy so importing EmbeddingService does not load a
    model or require sentence-transformers unless this backend is used.

    Example
    -------
    backend = SentenceTransformerEmbeddingBackend(
        "sentence-transformers/all-MiniLM-L6-v2"
    )

    service = EmbeddingService(
        embed_callable=backend,
        model_name=backend.model_name,
        normalize=True,
        expected_dimension=backend.dimension,
    )
    """

    def __init__(
        self,
        model_name: str,
        *,
        device: Optional[str] = None,
    ) -> None:
        model_name = _clean_text(
            model_name
        )

        if not model_name:
            raise EmbeddingValidationError(
                "model_name cannot be empty."
            )

        try:
            from sentence_transformers import (
                SentenceTransformer,
            )
        except ImportError as exc:
            raise EmbeddingServiceError(
                "sentence-transformers is not installed. "
                "Install it or inject another embed_callable."
            ) from exc

        self.model_name = model_name

        kwargs: Dict[str, Any] = {}

        if device:
            kwargs[
                "device"
            ] = device

        self.model = SentenceTransformer(
            model_name,
            **kwargs,
        )

        dimension = (
            self.model.get_sentence_embedding_dimension()
        )

        if (
            dimension is None
            or int(dimension) <= 0
        ):
            raise EmbeddingServiceError(
                "Could not determine embedding dimension."
            )

        self.dimension = int(
            dimension
        )

    def __call__(
        self,
        texts: List[str],
    ) -> List[List[float]]:
        vectors = self.model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=False,
            show_progress_bar=False,
        )

        return vectors.tolist()
