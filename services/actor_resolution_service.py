"""
Deterministic role-first actor resolution for AI Event Intelligence.

Recommended location:
    services/actor_resolution_service.py

Pipeline position
-----------------
Persisted Event anchor + persisted Source text
        ↓
ActorResolutionService
        ↓
structured role + identity resolution
        ↓
offline evaluation / later Strategic Admission integration

V1 design principles
--------------------
1. NO LLM adjudication.
2. spaCy NER proposes entity spans/types.
3. spaCy dependency parsing determines grammatical role.
4. Source-local acronym definitions resolve aliases without a global acronym list.
5. role_status and identity_status are independent axes.
6. Publisher metadata is provenance-only and never creates an entity candidate.
7. Generic placeholders such as "The Company" retain grammatical agency but fail
   identity resolution.
8. Unknown subjects such as "SK" or "R&D" can retain action_subject role while
   identity remains unresolved.
9. The service is sidecar/read-only. It does not mutate EventService, ExtractionAgent,
   Strategic Admission, or StrategicInference.

The service intentionally does NOT infer that a candidate is an institution merely
because it is capitalized or an acronym.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence


SERVICE_VERSION = "actor-resolution-service-v1.0-parser-ner-alias-two-axis"
DEFAULT_SPACY_MODEL = "en_core_web_trf"

ROLE_ACTION_SUBJECT = "action_subject"
ROLE_COUNTERPARTY = "counterparty"
ROLE_PARTICIPANT = "participant"
ROLE_TEMPORAL = "temporal"
ROLE_LOCATION = "location"
ROLE_NON_ACTOR = "non_actor"
ROLE_UNRESOLVED = "unresolved"

ALLOWED_ROLE_STATUSES = {
    ROLE_ACTION_SUBJECT,
    ROLE_COUNTERPARTY,
    ROLE_PARTICIPANT,
    ROLE_TEMPORAL,
    ROLE_LOCATION,
    ROLE_NON_ACTOR,
    ROLE_UNRESOLVED,
}

IDENTITY_SOURCE_SUPPORTED = "source_supported"
IDENTITY_SOURCE_LOCAL_ALIAS = "source_local_alias"
IDENTITY_UNRESOLVED = "unresolved"

ALLOWED_IDENTITY_STATUSES = {
    IDENTITY_SOURCE_SUPPORTED,
    IDENTITY_SOURCE_LOCAL_ALIAS,
    IDENTITY_UNRESOLVED,
}

SPACY_ENTITY_TYPE_MAP = {
    "ORG": "organization",
    "GPE": "geopolitical_entity",
    "LOC": "location",
    "FAC": "facility",
    "PERSON": "person",
    "NORP": "group",
    "DATE": "date",
    "TIME": "time",
    "MONEY": "money",
    "PERCENT": "percent",
    "QUANTITY": "quantity",
    "ORDINAL": "ordinal",
    "CARDINAL": "cardinal",
    "PRODUCT": "product",
    "LAW": "law",
    "EVENT": "event",
    "WORK_OF_ART": "work_of_art",
    "LANGUAGE": "language",
}

TEMPORAL_LABELS = {"DATE", "TIME"}
NON_ACTOR_LABELS = {
    "DATE", "TIME", "MONEY", "PERCENT", "QUANTITY", "ORDINAL", "CARDINAL",
    "PRODUCT", "LAW", "EVENT", "WORK_OF_ART", "LANGUAGE",
}
INSTITUTIONAL_ENTITY_TYPES = {
    "organization",
    "geopolitical_entity",
    "facility",
}

PLACEHOLDER_EXACT = {
    "actor",
    "company",
    "firm",
    "operator",
    "provider",
    "issuer",
    "business",
    "organization",
    "organisation",
    "institution",
    "entity",
    "party",
    "startup",
    "the actor",
    "the company",
    "the firm",
    "the operator",
    "the provider",
    "the issuer",
    "the business",
    "the organization",
    "the organisation",
    "the institution",
    "the entity",
    "the party",
    "the startup",
}

LOCATION_PREPOSITIONS = {
    "in", "at", "within", "across", "throughout", "near", "inside", "outside",
}
COUNTERPARTY_PREPOSITIONS = {
    "with", "against", "from", "to", "into",
}

SUBJECT_DEPS = {"nsubj", "csubj"}
PASSIVE_AGENT_DEPS = {"agent"}
SUBJECT_EXPANSION_DEPS = {"compound", "flat", "fixed"}

# This is not an acronym dictionary. It detects explicit source-local definitions:
#     China Medical System (CMS)
#     Hong Kong Exchanges and Clearing (HKEX)
_ALIAS_DEFINITION_RE = re.compile(
    r"(?P<long>"
    r"[A-Z][A-Za-z0-9&'’\-]*"
    r"(?:\s+(?:[A-Z][A-Za-z0-9&'’\-]*|and|of|the|for|in|on|to)){1,11}"
    r")\s*\("
    r"(?P<acro>[A-Z][A-Z0-9&.\-]{1,14})"
    r"\)"
)


class ActorResolutionError(RuntimeError):
    """Base actor-resolution exception."""


class ActorResolutionConfigurationError(ActorResolutionError):
    """Raised when the local NLP pipeline is unavailable or incomplete."""


@dataclass(frozen=True)
class ResolvedMention:
    surface_form: str
    canonical_name: str
    entity_type: str
    ner_label: str
    role_status: str
    identity_status: str
    resolution_basis: str
    source_span: str
    sentence_text: str
    action_text: str
    action_lemma: str
    char_start: int
    char_end: int
    publisher_match: bool
    publisher_exclusion_applied: bool
    placeholder: bool
    institutional_actor_eligible: bool
    actor_ready: bool
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["notes"] = list(self.notes)
        return data


@dataclass(frozen=True)
class SourceAlias:
    alias: str
    canonical_name: str
    entity_type: str
    ner_label: str
    source_span: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _normalized_key(value: Any) -> str:
    text = _clean(value).casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


def _is_placeholder(value: Any) -> bool:
    text = _clean(value).casefold()
    text = re.sub(r"\s+", " ", text)
    if text in PLACEHOLDER_EXACT:
        return True
    return bool(
        re.fullmatch(
            r"(?:the\s+)?(?:unnamed|unknown|unidentified|another|separate)\s+"
            r"(?:actor|company|firm|issuer|operator|provider|entity|organization|organisation)",
            text,
        )
    )


def _country_canonical_name(surface: str) -> str:
    """
    Standards-based normalization only.

    No hand-maintained country acronym dictionary is used. If pycountry can resolve
    the exact source surface, use its canonical name. Otherwise preserve the source
    surface unchanged.
    """
    try:
        import pycountry  # type: ignore
    except Exception:
        return surface

    try:
        country = pycountry.countries.lookup(surface)
    except Exception:
        return surface

    return _clean(getattr(country, "name", "") or surface)


class ActorResolutionService:
    """
    Resolve action roles and identities from event/source text.

    Inject `nlp=` in tests. Production/offline evaluation normally loads a pinned
    spaCy model by name.
    """

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_SPACY_MODEL,
        nlp: Any | None = None,
    ) -> None:
        self.model_name = _clean(model_name) or DEFAULT_SPACY_MODEL
        self._nlp = nlp if nlp is not None else self._load_nlp(self.model_name)
        self._validate_pipeline(self._nlp)
        self._source_alias_cache: dict[str, dict[str, SourceAlias]] = {}

    @staticmethod
    def _load_nlp(model_name: str) -> Any:
        try:
            import spacy  # type: ignore
        except Exception as exc:
            raise ActorResolutionConfigurationError(
                "spaCy is required. Install it with `pip install spacy`."
            ) from exc

        try:
            return spacy.load(model_name)
        except Exception as exc:
            raise ActorResolutionConfigurationError(
                (
                    f"spaCy model {model_name!r} is unavailable. "
                    f"Install it with `python -m spacy download {model_name}`. "
                    "V1 intentionally fails closed rather than silently switching models."
                )
            ) from exc

    @staticmethod
    def _validate_pipeline(nlp: Any) -> None:
        """
        Production spaCy pipelines need NER + dependency annotations.

        Injected test doubles are accepted if they explicitly declare
        `_actor_resolution_test_double = True`.
        """
        if getattr(nlp, "_actor_resolution_test_double", False):
            return

        pipe_names = set(getattr(nlp, "pipe_names", []) or [])
        missing = {"ner", "parser"} - pipe_names
        if missing:
            raise ActorResolutionConfigurationError(
                "ActorResolutionService requires spaCy NER and dependency parsing. "
                f"Missing pipeline components: {sorted(missing)}"
            )

    def model_metadata(self) -> dict[str, Any]:
        meta = getattr(self._nlp, "meta", {}) or {}
        return {
            "service_version": SERVICE_VERSION,
            "model_name": self.model_name,
            "model_version": _clean(meta.get("version")),
            "language": _clean(meta.get("lang")),
            "pipeline": list(getattr(self._nlp, "pipe_names", []) or []),
            "llm_used": False,
        }

    def _ner_type_for_phrase(self, phrase: str) -> tuple[str, str]:
        """
        Classify only the explicit alias long form, not arbitrary source context.
        """
        phrase = _clean(phrase)
        if not phrase:
            return "", "unknown"

        try:
            doc = self._nlp(phrase)
        except Exception:
            return "", "unknown"

        for ent in getattr(doc, "ents", []) or []:
            if _normalized_key(ent.text) == _normalized_key(phrase):
                label = _clean(getattr(ent, "label_", ""))
                return label, SPACY_ENTITY_TYPE_MAP.get(label, "unknown")

        return "", "unknown"

    def build_source_aliases(self, source_text: str) -> dict[str, SourceAlias]:
        source_text = str(source_text or "")
        fingerprint = hashlib.sha256(source_text.encode("utf-8", errors="replace")).hexdigest()
        cached = self._source_alias_cache.get(fingerprint)
        if cached is not None:
            return dict(cached)

        aliases: dict[str, SourceAlias] = {}

        for match in _ALIAS_DEFINITION_RE.finditer(source_text):
            long_form = _clean(match.group("long"))
            acronym = _clean(match.group("acro"))
            if not long_form or not acronym:
                continue
            if _is_placeholder(long_form):
                continue

            ner_label, entity_type = self._ner_type_for_phrase(long_form)
            key = _normalized_key(acronym)
            if not key:
                continue

            aliases.setdefault(
                key,
                SourceAlias(
                    alias=acronym,
                    canonical_name=long_form,
                    entity_type=entity_type,
                    ner_label=ner_label,
                    source_span=_clean(match.group(0)),
                ),
            )

        self._source_alias_cache[fingerprint] = dict(aliases)
        return aliases

    @staticmethod
    def _entity_covering_token(doc: Any, token: Any) -> Any | None:
        for ent in getattr(doc, "ents", []) or []:
            if ent.start <= token.i < ent.end:
                return ent
        return None

    @staticmethod
    def _entity_label_for_span(doc: Any, start: int, end: int) -> str:
        for ent in getattr(doc, "ents", []) or []:
            if ent.start <= start and ent.end >= end:
                return _clean(getattr(ent, "label_", ""))
            if start <= ent.start < end or start < ent.end <= end:
                return _clean(getattr(ent, "label_", ""))
        return ""

    @staticmethod
    def _expand_subject_span(doc: Any, token: Any) -> Any:
        indices = {token.i}
        for child in getattr(token, "children", []):
            if _clean(getattr(child, "dep_", "")).casefold() in SUBJECT_EXPANSION_DEPS:
                indices.add(child.i)
                for grandchild in getattr(child, "children", []):
                    if _clean(getattr(grandchild, "dep_", "")).casefold() in SUBJECT_EXPANSION_DEPS:
                        indices.add(grandchild.i)

        start = min(indices)
        end = max(indices) + 1
        return doc[start:end]

    @staticmethod
    def _action_for_span(span: Any, role_status: str) -> Any | None:
        tokens = list(span)
        if not tokens:
            return None

        if role_status == ROLE_ACTION_SUBJECT:
            for token in tokens:
                dep = _clean(getattr(token, "dep_", "")).casefold()
                if dep in SUBJECT_DEPS:
                    return token.head
                if dep == "conj":
                    head_dep = _clean(getattr(token.head, "dep_", "")).casefold()
                    if head_dep in SUBJECT_DEPS:
                        return token.head.head

            # Passive agent: "by the US"
            for token in tokens:
                dep = _clean(getattr(token, "dep_", "")).casefold()
                if dep in {"pobj", "obl"}:
                    prep = token.head
                    prep_text = _clean(getattr(prep, "text", "")).casefold()
                    prep_dep = _clean(getattr(prep, "dep_", "")).casefold()
                    if prep_text == "by" or prep_dep in PASSIVE_AGENT_DEPS:
                        return prep.head

        return None

    @classmethod
    def _role_for_span(cls, span: Any, ner_label: str) -> tuple[str, str]:
        label = _clean(ner_label).upper()

        # Semantic type overrides malformed grammar. A DATE tagged as the syntactic
        # subject still cannot become an institutional actor in V1.
        if label in TEMPORAL_LABELS:
            return ROLE_TEMPORAL, "spacy_ner_temporal"

        tokens = list(span)
        if not tokens:
            return ROLE_UNRESOLVED, "empty_span"

        for token in tokens:
            dep = _clean(getattr(token, "dep_", "")).casefold()

            if dep in SUBJECT_DEPS:
                return ROLE_ACTION_SUBJECT, f"dependency_{dep}"

            if dep == "conj":
                head_dep = _clean(getattr(token.head, "dep_", "")).casefold()
                if head_dep in SUBJECT_DEPS:
                    return ROLE_ACTION_SUBJECT, "dependency_coordinated_subject"

            if dep in {"pobj", "obl"}:
                prep = token.head
                prep_text = _clean(getattr(prep, "text", "")).casefold()
                prep_dep = _clean(getattr(prep, "dep_", "")).casefold()

                if prep_text == "by" or prep_dep in PASSIVE_AGENT_DEPS:
                    return ROLE_ACTION_SUBJECT, "dependency_passive_agent"

                if label in {"GPE", "LOC", "FAC"} and prep_text in LOCATION_PREPOSITIONS:
                    return ROLE_LOCATION, f"dependency_location_{prep_text}"

                if prep_text in COUNTERPARTY_PREPOSITIONS:
                    return ROLE_COUNTERPARTY, f"dependency_counterparty_{prep_text}"

        if label in {"GPE", "LOC", "FAC"}:
            return ROLE_LOCATION, "spacy_ner_geography_without_agency"

        if label in NON_ACTOR_LABELS:
            return ROLE_NON_ACTOR, f"spacy_ner_{label.casefold()}"

        if label:
            return ROLE_PARTICIPANT, "spacy_ner_participant"

        return ROLE_UNRESOLVED, "dependency_or_ner_role_unresolved"

    @staticmethod
    def _identity_for_candidate(
        *,
        surface: str,
        ner_label: str,
        aliases: Mapping[str, SourceAlias],
    ) -> tuple[str, str, str, str, tuple[str, ...]]:
        """
        Return:
            canonical_name,
            entity_type,
            identity_status,
            identity_basis,
            notes
        """
        surface = _clean(surface)
        label = _clean(ner_label).upper()
        notes: list[str] = []

        if _is_placeholder(surface):
            return (
                "",
                SPACY_ENTITY_TYPE_MAP.get(label, "unknown"),
                IDENTITY_UNRESOLVED,
                "placeholder_reference",
                ("generic_placeholder_requires_source_coreference",),
            )

        alias = aliases.get(_normalized_key(surface))
        if alias is not None:
            entity_type = alias.entity_type or "unknown"
            return (
                alias.canonical_name,
                entity_type,
                IDENTITY_SOURCE_LOCAL_ALIAS,
                "explicit_source_local_alias",
                (f"alias_source_span={alias.source_span}",),
            )

        if label:
            entity_type = SPACY_ENTITY_TYPE_MAP.get(label, "unknown")
            canonical = surface
            if label == "GPE":
                canonical = _country_canonical_name(surface)

            return (
                canonical,
                entity_type,
                IDENTITY_SOURCE_SUPPORTED,
                f"spacy_ner_{label.casefold()}",
                tuple(notes),
            )

        # Important V1 boundary:
        # grammar may establish agency even when identity is unresolved.
        # Do not manufacture a canonical identity from capitalization/acronym shape.
        return (
            "",
            "unknown",
            IDENTITY_UNRESOLVED,
            "no_ner_or_source_alias_identity",
            ("agency_may_be_resolved_but_identity_is_not",),
        )

    @staticmethod
    def _institutional_actor_eligible(entity_type: str) -> bool:
        return _clean(entity_type).casefold() in INSTITUTIONAL_ENTITY_TYPES

    @staticmethod
    def _publisher_rule(
        *,
        surface: str,
        role_status: str,
        source_publisher: str,
    ) -> tuple[bool, bool, tuple[str, ...]]:
        """
        Publisher metadata never creates a candidate.

        If an independently extracted text candidate matches the publisher:
        - allow it only when grammar says it is the explicit action subject/agent;
        - otherwise structurally demote it from actor eligibility.
        """
        publisher = _clean(source_publisher)
        if not publisher:
            return False, False, ()

        if _normalized_key(surface) != _normalized_key(publisher):
            return False, False, ()

        if role_status == ROLE_ACTION_SUBJECT:
            return True, False, ("publisher_match_allowed_only_due_explicit_action_role",)

        return True, True, ("publisher_provenance_only_not_action_actor",)

    def _resolve_span(
        self,
        *,
        doc: Any,
        span: Any,
        forced_role: str | None,
        forced_basis: str | None,
        aliases: Mapping[str, SourceAlias],
        source_publisher: str,
    ) -> ResolvedMention:
        surface = _clean(getattr(span, "text", ""))
        start = int(getattr(span, "start_char", 0) or 0)
        end = int(getattr(span, "end_char", 0) or 0)
        ner_label = self._entity_label_for_span(doc, span.start, span.end)

        if forced_role:
            role_status = forced_role
            role_basis = forced_basis or "forced_dependency_role"
            if ner_label in TEMPORAL_LABELS:
                role_status = ROLE_TEMPORAL
                role_basis = "spacy_ner_temporal"
        else:
            role_status, role_basis = self._role_for_span(span, ner_label)

        (
            canonical_name,
            entity_type,
            identity_status,
            identity_basis,
            identity_notes,
        ) = self._identity_for_candidate(
            surface=surface,
            ner_label=ner_label,
            aliases=aliases,
        )

        placeholder = _is_placeholder(surface)

        publisher_match, publisher_exclusion, publisher_notes = self._publisher_rule(
            surface=surface,
            role_status=role_status,
            source_publisher=source_publisher,
        )

        notes = list(identity_notes) + list(publisher_notes)

        if publisher_exclusion:
            role_status = ROLE_NON_ACTOR
            role_basis = "publisher_provenance_only"

        action = self._action_for_span(span, ROLE_ACTION_SUBJECT if role_status == ROLE_ACTION_SUBJECT else role_status)
        action_text = _clean(getattr(action, "text", "")) if action is not None else ""
        action_lemma = _clean(getattr(action, "lemma_", "")) if action is not None else ""
        if not action_lemma:
            action_lemma = action_text.casefold()

        sentence_text = ""
        try:
            sentence_text = _clean(span.sent.text)
        except Exception:
            sentence_text = ""

        eligible = self._institutional_actor_eligible(entity_type)
        actor_ready = bool(
            role_status == ROLE_ACTION_SUBJECT
            and identity_status in {IDENTITY_SOURCE_SUPPORTED, IDENTITY_SOURCE_LOCAL_ALIAS}
            and canonical_name
            and eligible
            and not placeholder
            and not publisher_exclusion
        )

        resolution_basis = "+".join(
            part for part in (role_basis, identity_basis) if part
        )

        return ResolvedMention(
            surface_form=surface,
            canonical_name=canonical_name,
            entity_type=entity_type,
            ner_label=ner_label,
            role_status=role_status,
            identity_status=identity_status,
            resolution_basis=resolution_basis,
            source_span=surface,
            sentence_text=sentence_text,
            action_text=action_text,
            action_lemma=action_lemma,
            char_start=start,
            char_end=end,
            publisher_match=publisher_match,
            publisher_exclusion_applied=publisher_exclusion,
            placeholder=placeholder,
            institutional_actor_eligible=eligible,
            actor_ready=actor_ready,
            notes=tuple(notes),
        )

    @staticmethod
    def _span_key(span: Any) -> tuple[int, int]:
        return int(span.start), int(span.end)

    def resolve_event(
        self,
        *,
        event_text: str,
        source_text: str = "",
        source_publisher: str = "",
    ) -> dict[str, Any]:
        event_text = str(event_text or "")
        source_text = str(source_text or "")
        source_publisher = _clean(source_publisher)

        aliases = self.build_source_aliases(source_text)
        doc = self._nlp(event_text)

        resolved: list[ResolvedMention] = []
        seen_spans: set[tuple[int, int, str]] = set()

        # 1. NER mentions: type first, role from dependency structure.
        for ent in getattr(doc, "ents", []) or []:
            mention = self._resolve_span(
                doc=doc,
                span=ent,
                forced_role=None,
                forced_basis=None,
                aliases=aliases,
                source_publisher=source_publisher,
            )
            key = (ent.start, ent.end, mention.role_status)
            if key not in seen_spans:
                seen_spans.add(key)
                resolved.append(mention)

        # 2. Dependency subjects/agents missed by NER.
        for token in doc:
            dep = _clean(getattr(token, "dep_", "")).casefold()
            forced_role = None
            forced_basis = None
            candidate_span = None

            if dep in SUBJECT_DEPS:
                forced_role = ROLE_ACTION_SUBJECT
                forced_basis = f"dependency_{dep}"
                candidate_span = self._entity_covering_token(doc, token)
                if candidate_span is None:
                    candidate_span = self._expand_subject_span(doc, token)

            elif dep == "conj":
                head_dep = _clean(getattr(token.head, "dep_", "")).casefold()
                if head_dep in SUBJECT_DEPS:
                    forced_role = ROLE_ACTION_SUBJECT
                    forced_basis = "dependency_coordinated_subject"
                    candidate_span = self._entity_covering_token(doc, token)
                    if candidate_span is None:
                        candidate_span = self._expand_subject_span(doc, token)

            elif dep in {"pobj", "obl"}:
                prep = token.head
                prep_text = _clean(getattr(prep, "text", "")).casefold()
                prep_dep = _clean(getattr(prep, "dep_", "")).casefold()
                if prep_text == "by" or prep_dep in PASSIVE_AGENT_DEPS:
                    forced_role = ROLE_ACTION_SUBJECT
                    forced_basis = "dependency_passive_agent"
                    candidate_span = self._entity_covering_token(doc, token)
                    if candidate_span is None:
                        candidate_span = self._expand_subject_span(doc, token)

            if candidate_span is None or forced_role is None:
                continue

            mention = self._resolve_span(
                doc=doc,
                span=candidate_span,
                forced_role=forced_role,
                forced_basis=forced_basis,
                aliases=aliases,
                source_publisher=source_publisher,
            )
            key = (candidate_span.start, candidate_span.end, mention.role_status)
            if key not in seen_spans:
                seen_spans.add(key)
                resolved.append(mention)

        # Stable ordering: source order, then actor-role mentions before secondary roles
        # at the same offset.
        role_rank = {
            ROLE_ACTION_SUBJECT: 0,
            ROLE_COUNTERPARTY: 1,
            ROLE_PARTICIPANT: 2,
            ROLE_LOCATION: 3,
            ROLE_TEMPORAL: 4,
            ROLE_NON_ACTOR: 5,
            ROLE_UNRESOLVED: 6,
        }
        resolved.sort(
            key=lambda item: (
                item.char_start,
                role_rank.get(item.role_status, 99),
                item.surface_form.casefold(),
            )
        )

        actor_candidates = [
            item for item in resolved if item.role_status == ROLE_ACTION_SUBJECT
        ]
        ready_candidates = [item for item in actor_candidates if item.actor_ready]

        ready_candidates.sort(
            key=lambda item: (
                0 if item.identity_status == IDENTITY_SOURCE_LOCAL_ALIAS else 1,
                item.char_start,
                item.canonical_name.casefold(),
            )
        )

        primary_actor = ready_candidates[0] if ready_candidates else None

        gate_reasons: list[str] = []
        if primary_actor is None:
            if not actor_candidates:
                gate_reasons.append("actor_role_unresolved")
            else:
                if all(
                    item.identity_status == IDENTITY_UNRESOLVED
                    for item in actor_candidates
                ):
                    gate_reasons.append("actor_identity_unresolved")

                if any(item.placeholder for item in actor_candidates):
                    gate_reasons.append("actor_placeholder_unresolved")

                if any(
                    item.identity_status != IDENTITY_UNRESOLVED
                    and not item.institutional_actor_eligible
                    for item in actor_candidates
                ):
                    gate_reasons.append("actor_entity_type_not_institutional")

                if any(item.publisher_exclusion_applied for item in actor_candidates):
                    gate_reasons.append("publisher_provenance_only")

                if not gate_reasons:
                    gate_reasons.append("actor_not_ready")

        requires_adjudication = bool(
            primary_actor is None
            and actor_candidates
            and any(
                item.role_status == ROLE_ACTION_SUBJECT
                for item in actor_candidates
            )
        )

        primary_action = None
        chosen_for_action = primary_actor or (actor_candidates[0] if actor_candidates else None)
        if chosen_for_action is not None:
            primary_action = {
                "text": chosen_for_action.action_text,
                "lemma": chosen_for_action.action_lemma,
                "sentence_text": chosen_for_action.sentence_text,
                "resolution_basis": chosen_for_action.resolution_basis,
            }

        return {
            "service_version": SERVICE_VERSION,
            "model": self.model_metadata(),
            "event_text": event_text,
            "source_publisher": source_publisher,
            "publisher_is_candidate_source": False,
            "source_aliases": {
                alias.alias: alias.to_dict()
                for alias in sorted(aliases.values(), key=lambda item: item.alias)
            },
            "primary_actor": primary_actor.to_dict() if primary_actor else None,
            "primary_action": primary_action,
            "mentions": [item.to_dict() for item in resolved],
            "actor_role_candidate_count": len(actor_candidates),
            "resolved_actor_candidate_count": len(ready_candidates),
            "admission_actor_ready": primary_actor is not None,
            "actor_gate_failure_reasons": gate_reasons,
            "requires_adjudication": requires_adjudication,
            "llm_used": False,
        }
