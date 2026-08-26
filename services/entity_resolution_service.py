from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, asdict
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set

PROMPT_VERSION = "entity-resolution-v1"

class EntityResolutionServiceError(RuntimeError):
    pass

class EntityResolutionValidationError(EntityResolutionServiceError):
    pass

@dataclass(frozen=True)
class EntityResolutionResult:
    mention: str
    normalized_mention: str
    entity_type: str
    status: str
    method: str
    canonical_entity_id: str
    canonical_name: str
    candidate_entity_ids: List[str]
    needs_review: bool
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()

def normalize_entity_text(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())

def normalize_entity_type(value: Any) -> str:
    text = normalize_entity_text(value)
    return text.replace(" ", "_") if text else ""

def _unique_strings(values: Any) -> List[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    out, seen = [], set()
    for value in values:
        text = _clean(value)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out

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

class EntityResolutionService:
    """
    Deterministic-first entity resolution.

    Resolution order:
      1. explicit canonical entity ID
      2. exact canonical-name match
      3. exact alias match
      4. normalized canonical-name match
      5. normalized alias match
      6. optional registry candidate search
      7. optional LLM choice among supplied candidates only

    The LLM cannot invent a new canonical entity.
    Persistence remains the responsibility of EntityService.
    """

    def __init__(
        self,
        *,
        registry_provider: Callable[[], Sequence[Mapping[str, Any]]],
        candidate_provider: Optional[Callable[..., Sequence[Mapping[str, Any]]]] = None,
        llm_candidate_selector: Optional[Callable[[str], str | Mapping[str, Any]]] = None,
        model_name: str = "",
        prompt_version: str = PROMPT_VERSION,
    ) -> None:
        if not callable(registry_provider):
            raise EntityResolutionValidationError("registry_provider must be callable.")
        if candidate_provider is not None and not callable(candidate_provider):
            raise EntityResolutionValidationError("candidate_provider must be callable.")
        if llm_candidate_selector is not None and not callable(llm_candidate_selector):
            raise EntityResolutionValidationError("llm_candidate_selector must be callable.")
        self.registry_provider = registry_provider
        self.candidate_provider = candidate_provider
        self.llm_candidate_selector = llm_candidate_selector
        self.model_name = _clean(model_name)
        self.prompt_version = _clean(prompt_version)
        if not self.prompt_version:
            raise EntityResolutionValidationError("prompt_version cannot be empty.")

    def resolve(
        self,
        *,
        mention: str,
        entity_type: str = "",
        context: Optional[Mapping[str, Any]] = None,
        canonical_entity_id: str = "",
        allow_llm_disambiguation: bool = True,
    ) -> EntityResolutionResult:
        mention = _clean(mention)
        normalized_mention = normalize_entity_text(mention)
        normalized_type = normalize_entity_type(entity_type)
        context = dict(context or {})
        canonical_entity_id = _clean(canonical_entity_id)

        if not mention:
            return self._result(mention, normalized_mention, normalized_type, "invalid", "none", [], "", None)

        registry = self._load_registry()
        fingerprint = self._registry_fingerprint(registry)
        by_id = {item["entity_id"]: item for item in registry}

        if canonical_entity_id:
            candidate = by_id.get(canonical_entity_id)
            if candidate and self._type_compatible(candidate, normalized_type):
                return self._result(
                    mention, normalized_mention, normalized_type,
                    "resolved", "canonical_id", [canonical_entity_id],
                    fingerprint, candidate
                )

        matches = self._deterministic_matches(
            mention, normalized_mention, normalized_type, registry
        )
        if len(matches) == 1:
            candidate, method = matches[0]
            return self._result(
                mention, normalized_mention, normalized_type,
                "resolved", method, [candidate["entity_id"]],
                fingerprint, candidate
            )
        if len(matches) > 1:
            return self._resolve_ambiguity(
                mention, normalized_mention, normalized_type,
                context, [c for c, _ in matches], fingerprint,
                allow_llm_disambiguation
            )

        candidates = []
        if self.candidate_provider is not None:
            raw = self.candidate_provider(
                mention=mention,
                entity_type=normalized_type,
                context=context,
            )
            candidates = self._validate_external_candidates(raw, registry, normalized_type)

        if len(candidates) == 1:
            return self._result(
                mention, normalized_mention, normalized_type,
                "resolved", "registry_candidate",
                [candidates[0]["entity_id"]], fingerprint, candidates[0]
            )
        if len(candidates) > 1:
            return self._resolve_ambiguity(
                mention, normalized_mention, normalized_type,
                context, candidates, fingerprint, allow_llm_disambiguation
            )

        return self._result(
            mention, normalized_mention, normalized_type,
            "unresolved", "none", [], fingerprint, None
        )

    def resolve_many(
        self,
        mentions: Sequence[Mapping[str, Any]],
        *,
        allow_llm_disambiguation: bool = True,
    ) -> List[EntityResolutionResult]:
        if not isinstance(mentions, Sequence) or isinstance(mentions, (str, bytes)):
            raise EntityResolutionValidationError("mentions must be a sequence of objects.")
        results = []
        for index, item in enumerate(mentions):
            if not isinstance(item, Mapping):
                raise EntityResolutionValidationError(f"mentions[{index}] must be an object.")
            results.append(
                self.resolve(
                    mention=item.get("mention") or item.get("name") or "",
                    entity_type=item.get("entity_type") or item.get("type") or "",
                    context=item.get("context") if isinstance(item.get("context"), Mapping) else {},
                    canonical_entity_id=item.get("canonical_entity_id") or "",
                    allow_llm_disambiguation=allow_llm_disambiguation,
                )
            )
        if len(results) != len(mentions):
            raise EntityResolutionServiceError("Resolution completeness invariant failed.")
        return results

    def _load_registry(self) -> List[Dict[str, Any]]:
        raw = self.registry_provider()
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise EntityResolutionValidationError("registry_provider must return a sequence.")
        result, seen = [], set()
        for index, item in enumerate(raw):
            if not isinstance(item, Mapping):
                raise EntityResolutionValidationError(f"registry[{index}] must be an object.")
            entity_id = _clean(item.get("entity_id") or item.get("id"))
            name = _clean(item.get("name"))
            entity_type = normalize_entity_type(item.get("entity_type") or item.get("type"))
            if not entity_id or not name:
                raise EntityResolutionValidationError(f"registry[{index}] is missing entity_id or name.")
            if entity_id in seen:
                raise EntityResolutionValidationError(f"Duplicate entity_id={entity_id!r}.")
            seen.add(entity_id)
            result.append({
                "entity_id": entity_id,
                "name": name,
                "entity_type": entity_type,
                "aliases": _unique_strings(item.get("aliases")),
                "attributes": dict(item.get("attributes") or {}) if isinstance(item.get("attributes"), Mapping) else {},
            })
        return result

    def _registry_fingerprint(self, registry: Sequence[Mapping[str, Any]]) -> str:
        payload = {
            "registry": sorted(
                [{
                    "entity_id": x["entity_id"],
                    "name": x["name"],
                    "entity_type": x["entity_type"],
                    "aliases": sorted(x.get("aliases", [])),
                } for x in registry],
                key=lambda x: x["entity_id"],
            )
        }
        return _sha256_hex(_canonical_json_bytes(payload))

    def _type_compatible(self, candidate: Mapping[str, Any], requested_type: str) -> bool:
        return not requested_type or normalize_entity_type(candidate.get("entity_type")) == requested_type

    def _deterministic_matches(
        self,
        mention: str,
        normalized_mention: str,
        entity_type: str,
        registry: Sequence[Mapping[str, Any]],
    ):
        buckets = {
            "name_exact": [],
            "alias_exact": [],
            "normalized_name": [],
            "normalized_alias": [],
        }
        for raw in registry:
            candidate = dict(raw)
            if not self._type_compatible(candidate, entity_type):
                continue
            name = _clean(candidate["name"])
            aliases = _unique_strings(candidate.get("aliases"))
            if mention == name:
                buckets["name_exact"].append(candidate)
            elif mention in aliases:
                buckets["alias_exact"].append(candidate)
            elif normalized_mention == normalize_entity_text(name):
                buckets["normalized_name"].append(candidate)
            elif normalized_mention in {normalize_entity_text(a) for a in aliases}:
                buckets["normalized_alias"].append(candidate)

        for method in ("name_exact", "alias_exact", "normalized_name", "normalized_alias"):
            if buckets[method]:
                return [(c, method) for c in buckets[method]]
        return []

    def _validate_external_candidates(
        self,
        raw_candidates: Any,
        registry: Sequence[Mapping[str, Any]],
        entity_type: str,
    ) -> List[Dict[str, Any]]:
        if raw_candidates is None:
            return []
        if not isinstance(raw_candidates, Sequence) or isinstance(raw_candidates, (str, bytes)):
            raise EntityResolutionValidationError("candidate_provider must return a sequence.")
        by_id = {item["entity_id"]: dict(item) for item in registry}
        result, seen = [], set()
        for item in raw_candidates:
            if not isinstance(item, Mapping):
                continue
            entity_id = _clean(item.get("entity_id") or item.get("id"))
            candidate = by_id.get(entity_id)
            if candidate and entity_id not in seen and self._type_compatible(candidate, entity_type):
                seen.add(entity_id)
                result.append(candidate)
        return result

    def _resolve_ambiguity(
        self,
        mention: str,
        normalized_mention: str,
        entity_type: str,
        context: Mapping[str, Any],
        candidates: Sequence[Mapping[str, Any]],
        registry_fingerprint: str,
        allow_llm_disambiguation: bool,
    ) -> EntityResolutionResult:
        candidates = self._dedupe_candidates(candidates)
        candidate_ids = [c["entity_id"] for c in candidates]

        if not allow_llm_disambiguation or self.llm_candidate_selector is None:
            return self._result(
                mention, normalized_mention, entity_type,
                "ambiguous", "none", candidate_ids,
                registry_fingerprint, None
            )

        prompt = self._build_prompt(mention, entity_type, context, candidates)
        raw = self.llm_candidate_selector(prompt)
        parsed = dict(raw) if isinstance(raw, Mapping) else json.loads(str(raw))
        for prohibited in ("confidence", "confidence_score", "probability", "score"):
            if prohibited in parsed:
                raise EntityResolutionValidationError(
                    f"LLM selector returned prohibited field {prohibited!r}."
                )

        selected_id = _clean(parsed.get("entity_id"))
        if selected_id.upper() in {"", "NONE", "UNRESOLVED"}:
            return self._result(
                mention, normalized_mention, entity_type,
                "ambiguous", "none", candidate_ids,
                registry_fingerprint, None
            )

        allowed = {c["entity_id"]: dict(c) for c in candidates}
        if selected_id not in allowed:
            raise EntityResolutionValidationError(
                f"LLM selected entity_id outside supplied candidates: {selected_id!r}."
            )

        return self._result(
            mention, normalized_mention, entity_type,
            "resolved", "llm_candidate_choice", candidate_ids,
            registry_fingerprint, allowed[selected_id]
        )

    def _dedupe_candidates(self, candidates):
        result, seen = [], set()
        for c in candidates:
            entity_id = _clean(c.get("entity_id"))
            if entity_id and entity_id not in seen:
                seen.add(entity_id)
                result.append(dict(c))
        return result

    def _build_prompt(self, mention, entity_type, context, candidates) -> str:
        safe_candidates = [{
            "entity_id": c["entity_id"],
            "name": c["name"],
            "entity_type": c["entity_type"],
            "aliases": c.get("aliases", []),
            "attributes": c.get("attributes", {}),
        } for c in candidates]

        return f"""You are an entity disambiguation component.

Choose ONLY among the supplied candidate entity IDs.
If the context is insufficient to choose one candidate, return NONE.
Do not invent a new canonical entity.
Do not return a confidence score.

Mention: {mention}
Entity type: {entity_type or "unspecified"}

Context:
{json.dumps(dict(context), ensure_ascii=False, indent=2, default=str)}

Candidates:
{json.dumps(safe_candidates, ensure_ascii=False, indent=2, default=str)}

Return exactly:
{{"entity_id": "one supplied entity_id or NONE"}}
"""

    def _result(
        self,
        mention: str,
        normalized_mention: str,
        entity_type: str,
        status: str,
        method: str,
        candidate_ids: Sequence[str],
        registry_fingerprint: str,
        candidate: Optional[Mapping[str, Any]],
    ) -> EntityResolutionResult:
        return EntityResolutionResult(
            mention=mention,
            normalized_mention=normalized_mention,
            entity_type=entity_type,
            status=status,
            method=method,
            canonical_entity_id=_clean(candidate.get("entity_id")) if candidate else "",
            canonical_name=_clean(candidate.get("name")) if candidate else "",
            candidate_entity_ids=_unique_strings(candidate_ids),
            needs_review=status in {"ambiguous", "unresolved"},
            metadata={
                "registry_fingerprint": registry_fingerprint,
                "candidate_count": len(_unique_strings(candidate_ids)),
                "model": self.model_name if method == "llm_candidate_choice" else "",
                "prompt_version": self.prompt_version if method == "llm_candidate_choice" else "",
            },
        )