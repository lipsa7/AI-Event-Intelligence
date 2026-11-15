"""
Local embedding service for the Event Intelligence pipeline.

Location:
    services/vector_service.py

The previous file at this path contained a copy of the RSS feed reader and no
VectorService class, so the orchestrator's optional vector stage never loaded.
The RSS names are re-exported below from services.rss_service when that module
exists, so any import of them from this path keeps working.

What it does
------------
- Embeds text through the same local Ollama endpoint LLMService uses
  (POST /api/embed; falls back to the older /api/embeddings).
- Relevance: cosine similarity between a query and documents.
- Diversity: Maximal Marginal Relevance (Carbonell & Goldstein, 1998), so a
  selection is relevant but not ten copies of one story.
- Grouping: simple centroid clustering by cosine threshold.
- Event index: upsert_event()/search(), the interface the orchestrator's
  existing _index_events stage already calls.

Defaults
--------
Model: nomic-embed-text (run `ollama pull nomic-embed-text` once). Override with
EVENT_INTELLIGENCE_EMBED_MODEL. Nomic models get their documented task prefixes
("search_query: ", "search_document: ", "clustering: ").

Local-only by default, like LLMService: non-loopback base URLs are refused
unless allow_remote_base_url=True. No paid API. Standard library only.

Failure behaviour
-----------------
available() never raises; it returns False when Ollama or the model is
unreachable, and callers fall back to their non-embedding path. Raw texts are
cached in memory only for the life of the process.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping, Optional, Sequence


SERVICE_VERSION = "vector-service-v1.0-ollama-embeddings"
DEFAULT_EMBED_MODEL = "nomic-embed-text"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"

_NOMIC_PREFIXES = {
    "query": "search_query: ",
    "document": "search_document: ",
    "clustering": "clustering: ",
}


class VectorServiceError(RuntimeError):
    """Embedding request failed or returned unusable output."""


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(float(x) * float(x) for x in vector))
    if norm <= 0:
        raise VectorServiceError("Embedding has zero length.")
    return [float(x) / norm for x in vector]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine of two already-normalised vectors."""
    return float(sum(x * y for x, y in zip(a, b)))


class VectorService:
    def __init__(
        self,
        *,
        model_name: str = "",
        base_url: str = "",
        allow_remote_base_url: bool = False,
        timeout_seconds: float = 30.0,
        batch_size: int = 32,
        max_text_characters: int = 2_000,
        transport: Optional[Callable[[str, Mapping[str, Any]], Mapping[str, Any]]] = None,
    ) -> None:
        self.model_name = (
            _clean(model_name)
            or _clean(os.getenv("EVENT_INTELLIGENCE_EMBED_MODEL"))
            or DEFAULT_EMBED_MODEL
        )
        self.base_url = (
            _clean(base_url) or _clean(os.getenv("OLLAMA_BASE_URL")) or DEFAULT_BASE_URL
        ).rstrip("/")
        self._validate_base_url(self.base_url, allow_remote=allow_remote_base_url)
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.batch_size = max(1, int(batch_size))
        self.max_text_characters = max(100, int(max_text_characters))
        self._transport = transport or self._http_post_json
        self._cache: dict[str, list[float]] = {}
        self._events: dict[str, dict[str, Any]] = {}
        self._available: Optional[bool] = None
        self._legacy_endpoint = False
        self.last_error = ""

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def _prefix(self, purpose: str) -> str:
        if "nomic" in self.model_name.casefold():
            return _NOMIC_PREFIXES.get(purpose, "")
        return ""

    def embed(self, texts: Sequence[Any], *, purpose: str = "document") -> list[list[float]]:
        """Return one normalised vector per text. Raises VectorServiceError."""
        prepared = [
            self._prefix(purpose) + _clean(text)[: self.max_text_characters]
            for text in texts
        ]
        keys = [
            hashlib.sha256(f"{self.model_name}|{text}".encode("utf-8")).hexdigest()
            for text in prepared
        ]
        missing = [i for i, key in enumerate(keys) if key not in self._cache]
        for start in range(0, len(missing), self.batch_size):
            batch = missing[start : start + self.batch_size]
            vectors = self._embed_batch([prepared[i] for i in batch])
            for i, vector in zip(batch, vectors):
                self._cache[keys[i]] = _normalize(vector)
        return [self._cache[key] for key in keys]

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not self._legacy_endpoint:
            try:
                response = self._transport(
                    "/api/embed", {"model": self.model_name, "input": texts}
                )
                vectors = response.get("embeddings")
                if isinstance(vectors, list) and len(vectors) == len(texts):
                    return [list(map(float, vector)) for vector in vectors]
                raise VectorServiceError("Ollama /api/embed returned no embeddings.")
            except VectorServiceError as error:
                if "HTTP 404" not in str(error) or "model" in str(error).casefold():
                    raise
                self._legacy_endpoint = True  # Older Ollama: /api/embeddings
        output = []
        for text in texts:
            response = self._transport(
                "/api/embeddings", {"model": self.model_name, "prompt": text}
            )
            vector = response.get("embedding")
            if not isinstance(vector, list) or not vector:
                raise VectorServiceError("Ollama /api/embeddings returned no embedding.")
            output.append(list(map(float, vector)))
        return output

    def available(self) -> bool:
        """True if the embedding model answers. Checked once; never raises."""
        if self._available is None:
            try:
                self.embed(["availability check"], purpose="document")
                self._available = True
                self.last_error = ""
            except Exception as error:
                self._available = False
                self.last_error = f"{type(error).__name__}: {error}"[:300]
        return bool(self._available)

    # ------------------------------------------------------------------
    # Relevance, diversity, grouping
    # ------------------------------------------------------------------

    def similarities(self, query: str, documents: Sequence[Any]) -> list[float]:
        if not documents:
            return []
        query_vector = self.embed([query], purpose="query")[0]
        return [cosine(query_vector, vector) for vector in self.embed(documents, purpose="document")]

    def mmr_select(
        self,
        query: str,
        documents: Sequence[Any],
        *,
        k: Optional[int] = None,
        lambda_: float = 0.7,
        min_relevance: float = 0.0,
    ) -> list[dict[str, float]]:
        """Maximal Marginal Relevance selection.

        Returns [{"index", "relevance", "mmr"}] in selection order, only for
        documents with relevance >= min_relevance. mmr = lambda*relevance -
        (1-lambda)*max similarity to anything already selected.
        """
        if not documents:
            return []
        query_vector = self.embed([query], purpose="query")[0]
        vectors = self.embed(documents, purpose="document")
        relevance = [cosine(query_vector, vector) for vector in vectors]
        pool = [i for i, score in enumerate(relevance) if score >= min_relevance]
        limit = len(pool) if k is None else max(0, min(int(k), len(pool)))
        selected: list[dict[str, float]] = []
        chosen: list[int] = []
        while pool and len(selected) < limit:
            best_index, best_score = -1, -math.inf
            for i in pool:
                redundancy = max((cosine(vectors[i], vectors[j]) for j in chosen), default=0.0)
                score = lambda_ * relevance[i] - (1.0 - lambda_) * redundancy
                if score > best_score:
                    best_index, best_score = i, score
            chosen.append(best_index)
            pool.remove(best_index)
            selected.append({
                "index": best_index,
                "relevance": round(relevance[best_index], 4),
                "mmr": round(best_score, 4),
            })
        return selected

    def cluster(self, texts: Sequence[Any], *, threshold: float = 0.75) -> list[int]:
        """Centroid clustering: each text joins the most similar cluster whose
        centroid similarity >= threshold, otherwise starts a new one."""
        vectors = self.embed(texts, purpose="clustering")
        centroids: list[list[float]] = []
        sizes: list[int] = []
        labels: list[int] = []
        for vector in vectors:
            best, best_score = -1, -math.inf
            for label, centroid in enumerate(centroids):
                score = cosine(vector, centroid)
                if score > best_score:
                    best, best_score = label, score
            if best >= 0 and best_score >= threshold:
                count = sizes[best]
                merged = [(c * count + v) / (count + 1) for c, v in zip(centroids[best], vector)]
                centroids[best] = _normalize(merged)
                sizes[best] += 1
                labels.append(best)
            else:
                centroids.append(list(vector))
                sizes.append(1)
                labels.append(len(centroids) - 1)
        return labels

    # ------------------------------------------------------------------
    # Event index (interface used by the orchestrator's _index_events)
    # ------------------------------------------------------------------

    def upsert_event(
        self,
        event_id: str = "",
        title: str = "",
        summary: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
        **_: Any,
    ) -> dict[str, Any]:
        event_id = _clean(event_id)
        if not event_id:
            raise VectorServiceError("event_id is required.")
        text = _clean(f"{title}. {summary}") if title else _clean(summary)
        vector = self.embed([text], purpose="document")[0]
        self._events[event_id] = {
            "event_id": event_id,
            "text": text,
            "vector": vector,
            "metadata": dict(metadata or {}),
        }
        return {"event_id": event_id, "indexed": True, "model": self.model_name}

    add_event = upsert_event

    def search(self, query: str, k: int = 10) -> list[dict[str, Any]]:
        if not self._events:
            return []
        query_vector = self.embed([query], purpose="query")[0]
        scored = sorted(
            (
                (cosine(query_vector, row["vector"]), row)
                for row in self._events.values()
            ),
            key=lambda pair: -pair[0],
        )
        return [
            {"event_id": row["event_id"], "score": round(score, 4), "metadata": row["metadata"]}
            for score, row in scored[: max(1, int(k))]
        ]

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _http_post_json(self, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                decoded = json.loads(response.read(64 * 1024 * 1024).decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = ""
            try:
                body = error.read(2_000).decode("utf-8", errors="replace")
            except Exception:
                pass
            raise VectorServiceError(f"Ollama returned HTTP {error.code}: {_clean(body)[:300]}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise VectorServiceError(f"Could not reach Ollama at {self.base_url!r}: {error}") from error
        except json.JSONDecodeError as error:
            raise VectorServiceError("Ollama returned invalid JSON.") from error
        if not isinstance(decoded, Mapping):
            raise VectorServiceError("Ollama returned a non-object response.")
        if decoded.get("error"):
            raise VectorServiceError(_clean(decoded.get("error"))[:300])
        return decoded

    @staticmethod
    def _validate_base_url(base_url: str, *, allow_remote: bool) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise VectorServiceError("base_url must be an http(s) URL.")
        if not allow_remote and parsed.hostname.casefold() not in {"localhost", "127.0.0.1", "::1"}:
            raise VectorServiceError(
                "Remote embedding endpoints are disabled by default; "
                "pass allow_remote_base_url=True for an approved self-hosted endpoint."
            )


# Compatibility: this path previously held the RSS feed reader. Re-export its
# names from services.rss_service if present, so existing imports keep working.
try:  # pragma: no cover - depends on project layout
    from services.rss_service import (  # type: ignore  # noqa: F401
        DEFAULT_USER_AGENT,
        FeedService,
        RSSFetchError,
        RSSService,
        RSSServiceError,
        RSSValidationError,
    )
except Exception:  # pragma: no cover
    pass


__all__ = [
    "SERVICE_VERSION",
    "DEFAULT_EMBED_MODEL",
    "VectorService",
    "VectorServiceError",
    "cosine",
]
