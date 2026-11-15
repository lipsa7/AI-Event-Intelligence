"""
Canonical Entity registry service.

Recommended location:
    services/entity_service.py

Pipeline position
-----------------
Event.entity_mentions
        ↓
EntityResolutionService
        ↓
candidate / resolved canonical identity
        ↓
EntityService
        ↓
Entity registry
        ↓
RelationshipService / GraphService

Responsibilities
----------------
- Persist canonical ENT-* identities.
- Preserve stable canonical identity across name/alias changes.
- Manage canonical names, aliases and structured attributes.
- Expose complete registry records to EntityResolutionService.
- Provide deterministic candidate retrieval.
- Allow multiple distinct real-world entities to share the same textual
  name and entity type.
- Keep candidate retrieval separate from identity resolution.
- Perform no hidden result truncation unless the caller explicitly supplies
  a limit.

Non-responsibilities
--------------------
- No LLM entity resolution.
- No automatic merge based only on name similarity.
- No automatic assumption that same normalized name + type means same entity.
- No relationship inference.
- No graph traversal.
- No strategic inference.
- No evidence admission.
- No grounding.
- No unit normalization.
- No release authorization.

Identity principle
------------------
Canonical identity is ENT-*.

A textual mention is NOT a canonical identity.

    "Mercury"
        +
    entity_type="company"

is not sufficient evidence that two records refer to the same real-world
entity.

Therefore this service never silently creates a canonical ENT-* from only:

    normalized_name + entity_type

Creation requires either:

1. an explicit canonical entity_id; or
2. an explicit deterministic identity_key supplied by the caller.

The identity_key may represent a stable externally established identity,
for example:

    "lei:549300..."
    "wikidata:Q..."
    "registry:company:..."
    "internal_resolution:<stable-key>"

EntityResolutionService owns the decision that a mention corresponds to a
particular canonical entity.

Candidate-search scores are retrieval heuristics only. They are not identity
confidence or truth probabilities.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

from dataclasses import (
    asdict,
    dataclass,
)
from datetime import (
    datetime,
    timezone,
)
from typing import (
    Any,
    Iterable,
    Mapping,
    Optional,
    Sequence,
)

from sqlalchemy import (
    select,
)
from sqlalchemy.orm import (
    Session,
)


SERVICE_VERSION = (
    "entity-service-v4.0"
)


REQUIRED_MODEL_FIELDS = {
    "entity_id",
    "name",
    "normalized_name",
    "entity_type",
    "aliases",
    "attributes",
    "created_at",
    "updated_at",
}


class EntityServiceError(
    RuntimeError
):
    """Base exception for canonical Entity persistence."""


class EntityValidationError(
    EntityServiceError
):
    """Raised when an Entity request violates the service contract."""


class EntityModelContractError(
    EntityServiceError
):
    """Raised when models.entity.Entity does not match the service contract."""


@dataclass(
    frozen=True
)
class EntityWriteResult:
    """
    Result of creating/updating one canonical Entity.
    """

    entity_id: str
    created: bool
    name_changed: bool
    aliases_added: list[str]
    attributes_changed: bool

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(
            self
        )


@dataclass(
    frozen=True
)
class EntityDeleteResult:
    """
    Result of attempting to remove one canonical Entity.

    Foreign-key constraints remain responsible for
    preventing unsafe deletion of referenced entities.
    """

    entity_id: str
    deleted: bool

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(
            self
        )


def _utc_now() -> datetime:
    return datetime.now(
        timezone.utc
    )


def _clean(
    value: Any,
) -> str:
    if value is None:
        return ""

    return " ".join(
        str(
            value
        ).split()
    ).strip()


def normalize_entity_text(
    value: Any,
) -> str:
    """
    Normalize text for lexical lookup only.

    This does NOT establish canonical identity.
    """

    text = _clean(
        value
    )

    if not text:
        return ""

    text = (
        unicodedata.normalize(
            "NFKC",
            text,
        )
        .casefold()
    )

    text = re.sub(
        r"[^\w]+",
        " ",
        text,
        flags=re.UNICODE,
    )

    return " ".join(
        text.split()
    )


def normalize_entity_type(
    value: Any,
) -> str:
    """
    Normalize the entity classification label.

    Unknown/empty types remain explicit as "unknown".
    """

    text = (
        normalize_entity_text(
            value
        )
    )

    if not text:
        return "unknown"

    return text.replace(
        " ",
        "_",
    )


def _json_safe(
    value: Any,
) -> Any:
    if value is None or isinstance(
        value,
        (
            str,
            int,
            float,
            bool,
        ),
    ):
        return value

    if isinstance(
        value,
        datetime,
    ):
        if value.tzinfo is None:
            value = value.replace(
                tzinfo=timezone.utc
            )

        else:
            value = value.astimezone(
                timezone.utc
            )

        return value.isoformat()

    if isinstance(
        value,
        Mapping,
    ):
        return {
            str(key): _json_safe(
                item
            )
            for key, item
            in value.items()
        }

    if isinstance(
        value,
        (
            list,
            tuple,
            set,
        ),
    ):
        return [
            _json_safe(
                item
            )
            for item in value
        ]

    return str(
        value
    )


def _unique_strings(
    values: Any,
) -> list[str]:
    """
    Deduplicate strings by normalized lexical form while
    preserving the first presentation form.

    Example:

        ["OpenAI", " openai ", "OPENAI"]
            ->
        ["OpenAI"]
    """

    if values is None:
        return []

    if isinstance(
        values,
        str,
    ):
        values = [
            values
        ]

    elif not isinstance(
        values,
        (
            list,
            tuple,
            set,
        ),
    ):
        values = [
            values
        ]

    result: list[str] = []

    seen: set[str] = (
        set()
    )

    for value in values:
        text = _clean(
            value
        )

        normalized = (
            normalize_entity_text(
                text
            )
        )

        if (
            not text
            or not normalized
            or normalized in seen
        ):
            continue

        seen.add(
            normalized
        )

        result.append(
            text
        )

    return result


def _merge_aliases(
    *groups: Any,
    canonical_name: str = "",
) -> list[str]:
    """
    Merge alias groups without retaining the current
    canonical name as its own alias.
    """

    canonical_normalized = (
        normalize_entity_text(
            canonical_name
        )
    )

    result: list[str] = []

    seen: set[str] = (
        set()
    )

    for group in groups:
        for alias in (
            _unique_strings(
                group
            )
        ):
            normalized = (
                normalize_entity_text(
                    alias
                )
            )

            if (
                not normalized
                or normalized
                == canonical_normalized
                or normalized in seen
            ):
                continue

            seen.add(
                normalized
            )

            result.append(
                alias
            )

    return result


def stable_entity_id(
    *,
    identity_key: str,
    namespace: str = "canonical",
) -> str:
    """
    Create a deterministic ENT-* from an explicit,
    already-established identity key.

    IMPORTANT
    ---------
    This intentionally does NOT accept only:

        name + entity_type

    because those fields are not sufficient to prove
    real-world identity.

    Examples:

        stable_entity_id(
            identity_key="wikidata:Q95"
        )

        stable_entity_id(
            identity_key="lei:549300..."
        )

        stable_entity_id(
            namespace="internal_resolution",
            identity_key="company:openai:us:2015"
        )
    """

    identity_key = _clean(
        identity_key
    )

    namespace = _clean(
        namespace
    ).casefold()

    if not identity_key:
        raise EntityValidationError(
            "identity_key cannot be empty."
        )

    if not namespace:
        raise EntityValidationError(
            "namespace cannot be empty."
        )

    payload = "\x1f".join(
        [
            namespace,
            identity_key,
        ]
    )

    digest = (
        hashlib.sha256(
            payload.encode(
                "utf-8"
            )
        )
        .hexdigest()
        [:24]
        .upper()
    )

    return (
        f"ENT-{digest}"
    )


def _token_jaccard(
    left: str,
    right: str,
) -> float:
    """
    Deterministic lexical-overlap retrieval heuristic.

    Not an identity-confidence score.
    """

    left_tokens = set(
        normalize_entity_text(
            left
        ).split()
    )

    right_tokens = set(
        normalize_entity_text(
            right
        ).split()
    )

    if (
        not left_tokens
        or not right_tokens
    ):
        return 0.0

    return (
        len(
            left_tokens
            & right_tokens
        )
        /
        len(
            left_tokens
            | right_tokens
        )
    )


class EntityService:
    """
    Canonical Entity registry service.

    Persistence is deterministic.

    Resolution is deliberately external.
    """

    def __init__(
        self,
        *,
        session_factory: Any = None,
        entity_model: Any = None,
    ) -> None:
        if session_factory is None:
            try:
                from services.database_service import (
                    SessionLocal,
                )

            except ImportError as exc:
                raise EntityServiceError(
                    "Could not import SessionLocal "
                    "from services.database_service."
                ) from exc

            session_factory = (
                SessionLocal
            )

        if entity_model is None:
            try:
                from models.entity import (
                    Entity,
                )

            except ImportError as exc:
                raise EntityServiceError(
                    "Could not import Entity "
                    "from models.entity."
                ) from exc

            entity_model = Entity

        self.session_factory = (
            session_factory
        )

        self.Entity = (
            entity_model
        )

        self._validate_model_contract()

    # ------------------------------------------------------------------
    # Model contract
    # ------------------------------------------------------------------

    def _validate_model_contract(
        self,
    ) -> None:
        missing = sorted(
            field
            for field
            in REQUIRED_MODEL_FIELDS
            if not hasattr(
                self.Entity,
                field,
            )
        )

        if missing:
            raise EntityModelContractError(
                "Entity ORM model is missing "
                "required field(s): "
                + ", ".join(
                    missing
                )
            )

    # ------------------------------------------------------------------
    # Identity validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_entity_id(
        entity_id: str,
    ) -> str:
        entity_id = _clean(
            entity_id
        )

        if not entity_id:
            raise EntityValidationError(
                "entity_id cannot be empty."
            )

        if not (
            entity_id
            .upper()
            .startswith(
                "ENT-"
            )
        ):
            raise EntityValidationError(
                "Canonical entity_id must "
                "use ENT-* identity."
            )

        return entity_id

    @staticmethod
    def _resolve_write_identity(
        *,
        entity_id: str,
        identity_key: str,
        identity_namespace: str,
    ) -> str:
        """
        Resolve canonical identity for a write.

        Either:
            explicit ENT-*
        or:
            explicit stable identity_key

        is required.
        """

        explicit_id = _clean(
            entity_id
        )

        if explicit_id:
            return (
                EntityService
                ._validate_entity_id(
                    explicit_id
                )
            )

        identity_key = _clean(
            identity_key
        )

        if not identity_key:
            raise EntityValidationError(
                "Creating a canonical entity requires "
                "either explicit entity_id or explicit "
                "identity_key. Name + entity_type alone "
                "is not a canonical identity."
            )

        return stable_entity_id(
            identity_key=(
                identity_key
            ),
            namespace=(
                identity_namespace
            ),
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def upsert_entity(
        self,
        *,
        name: str,
        entity_type: str,
        entity_id: str = "",
        identity_key: str = "",
        identity_namespace: str = (
            "canonical"
        ),
        aliases: Optional[
            Iterable[str]
        ] = None,
        attributes: Optional[
            Mapping[
                str,
                Any,
            ]
        ] = None,
    ) -> EntityWriteResult:
        """
        Create or update one canonical Entity.

        IMPORTANT
        ---------
        This method does not search by name and then
        merge automatically.

        The caller must already know which ENT-* is being
        updated, or must supply a stable identity_key
        establishing a new deterministic identity.

        Renaming an existing ENT-* preserves the previous
        canonical name as an alias.
        """

        name = _clean(
            name
        )

        normalized_name = (
            normalize_entity_text(
                name
            )
        )

        normalized_type = (
            normalize_entity_type(
                entity_type
            )
        )

        if (
            not name
            or not normalized_name
        ):
            raise EntityValidationError(
                "name cannot be empty."
            )

        if (
            normalized_type
            == "unknown"
        ):
            raise EntityValidationError(
                "entity_type cannot be empty "
                "for a canonical entity write."
            )

        entity_id = (
            self._resolve_write_identity(
                entity_id=(
                    entity_id
                ),
                identity_key=(
                    identity_key
                ),
                identity_namespace=(
                    identity_namespace
                ),
            )
        )

        requested_aliases = (
            _merge_aliases(
                aliases,
                canonical_name=(
                    name
                ),
            )
        )

        if (
            attributes is not None
            and not isinstance(
                attributes,
                Mapping,
            )
        ):
            raise EntityValidationError(
                "attributes must be an object."
            )

        attributes_payload = {
            str(key): _json_safe(
                value
            )
            for key, value
            in (
                attributes
                or {}
            ).items()
        }

        session: Session = (
            self.session_factory()
        )

        try:
            entity = (
                session.execute(
                    select(
                        self.Entity
                    ).where(
                        self.Entity.entity_id
                        == entity_id
                    )
                )
                .scalar_one_or_none()
            )

            created = (
                entity is None
            )

            name_changed = False

            aliases_added: list[str] = (
                []
            )

            attributes_changed = False

            if created:
                now = _utc_now()

                entity = self.Entity(
                    entity_id=(
                        entity_id
                    ),
                    name=(
                        name
                    ),
                    normalized_name=(
                        normalized_name
                    ),
                    entity_type=(
                        normalized_type
                    ),
                    aliases=(
                        requested_aliases
                    ),
                    attributes=(
                        attributes_payload
                    ),
                    created_at=(
                        now
                    ),
                    updated_at=(
                        now
                    ),
                )

                aliases_added = list(
                    requested_aliases
                )

                attributes_changed = bool(
                    attributes_payload
                )

                session.add(
                    entity
                )

            else:
                existing_type = (
                    normalize_entity_type(
                        entity.entity_type
                    )
                )

                if (
                    existing_type
                    != normalized_type
                ):
                    raise EntityValidationError(
                        "Cannot change entity_type "
                        "for an existing canonical "
                        f"entity_id={entity_id!r}. "
                        "Create a distinct ENT-* if "
                        "the identity classification "
                        "is genuinely different."
                    )

                old_name = _clean(
                    entity.name
                )

                old_normalized_name = (
                    normalize_entity_text(
                        old_name
                    )
                )

                old_aliases = (
                    _unique_strings(
                        entity.aliases
                    )
                )

                merged_aliases = (
                    _merge_aliases(
                        old_aliases,
                        (
                            [
                                old_name
                            ]
                            if (
                                old_name
                                and old_normalized_name
                                != normalized_name
                            )
                            else []
                        ),
                        requested_aliases,
                        canonical_name=(
                            name
                        ),
                    )
                )

                old_alias_normalized = {
                    normalize_entity_text(
                        alias
                    )
                    for alias
                    in old_aliases
                }

                aliases_added = [
                    alias
                    for alias
                    in merged_aliases
                    if (
                        normalize_entity_text(
                            alias
                        )
                        not in old_alias_normalized
                    )
                ]

                name_changed = (
                    old_normalized_name
                    != normalized_name
                )

                old_attributes = dict(
                    entity.attributes
                    or {}
                )

                merged_attributes = dict(
                    old_attributes
                )

                merged_attributes.update(
                    attributes_payload
                )

                attributes_changed = (
                    merged_attributes
                    != old_attributes
                )

                entity.name = (
                    name
                )

                entity.normalized_name = (
                    normalized_name
                )

                entity.aliases = (
                    merged_aliases
                )

                entity.attributes = (
                    merged_attributes
                )

                if (
                    name_changed
                    or aliases_added
                    or attributes_changed
                ):
                    entity.updated_at = (
                        _utc_now()
                    )

            session.commit()

            return EntityWriteResult(
                entity_id=(
                    entity_id
                ),
                created=(
                    created
                ),
                name_changed=(
                    name_changed
                ),
                aliases_added=(
                    aliases_added
                ),
                attributes_changed=(
                    attributes_changed
                ),
            )

        except Exception:
            session.rollback()
            raise

        finally:
            session.close()

    def create_entity(
        self,
        *,
        name: str,
        entity_type: str,
        entity_id: str = "",
        identity_key: str = "",
        identity_namespace: str = (
            "canonical"
        ),
        aliases: Optional[
            Iterable[str]
        ] = None,
        attributes: Optional[
            Mapping[
                str,
                Any,
            ]
        ] = None,
    ) -> EntityWriteResult:
        """
        Explicit creation-oriented alias.

        Uses the same canonical identity rules as
        upsert_entity().
        """

        return self.upsert_entity(
            name=(
                name
            ),
            entity_type=(
                entity_type
            ),
            entity_id=(
                entity_id
            ),
            identity_key=(
                identity_key
            ),
            identity_namespace=(
                identity_namespace
            ),
            aliases=(
                aliases
            ),
            attributes=(
                attributes
            ),
        )

    def add_alias(
        self,
        *,
        entity_id: str,
        alias: str,
    ) -> bool:
        """
        Add one alias to an existing ENT-*.

        Returns:
            True  -> alias added
            False -> alias was already represented by
                     canonical name or another alias
        """

        entity_id = (
            self._validate_entity_id(
                entity_id
            )
        )

        alias = _clean(
            alias
        )

        normalized_alias = (
            normalize_entity_text(
                alias
            )
        )

        if not normalized_alias:
            raise EntityValidationError(
                "alias cannot be empty."
            )

        session: Session = (
            self.session_factory()
        )

        try:
            entity = (
                session.execute(
                    select(
                        self.Entity
                    ).where(
                        self.Entity.entity_id
                        == entity_id
                    )
                )
                .scalar_one_or_none()
            )

            if entity is None:
                raise EntityValidationError(
                    "Unknown canonical entity_id="
                    f"{entity_id!r}."
                )

            if (
                normalized_alias
                ==
                normalize_entity_text(
                    entity.name
                )
            ):
                return False

            aliases = (
                _unique_strings(
                    entity.aliases
                )
            )

            existing_normalized = {
                normalize_entity_text(
                    item
                )
                for item in aliases
            }

            if (
                normalized_alias
                in existing_normalized
            ):
                return False

            aliases.append(
                alias
            )

            entity.aliases = (
                aliases
            )

            entity.updated_at = (
                _utc_now()
            )

            session.commit()

            return True

        except Exception:
            session.rollback()
            raise

        finally:
            session.close()

    def remove_alias(
        self,
        *,
        entity_id: str,
        alias: str,
    ) -> bool:
        """
        Remove one alias by normalized lexical identity.
        """

        entity_id = (
            self._validate_entity_id(
                entity_id
            )
        )

        normalized_alias = (
            normalize_entity_text(
                alias
            )
        )

        if not normalized_alias:
            raise EntityValidationError(
                "alias cannot be empty."
            )

        session: Session = (
            self.session_factory()
        )

        try:
            entity = (
                session.execute(
                    select(
                        self.Entity
                    ).where(
                        self.Entity.entity_id
                        == entity_id
                    )
                )
                .scalar_one_or_none()
            )

            if entity is None:
                raise EntityValidationError(
                    "Unknown canonical entity_id="
                    f"{entity_id!r}."
                )

            aliases = (
                _unique_strings(
                    entity.aliases
                )
            )

            retained = [
                item
                for item in aliases
                if (
                    normalize_entity_text(
                        item
                    )
                    != normalized_alias
                )
            ]

            if (
                len(
                    retained
                )
                == len(
                    aliases
                )
            ):
                return False

            entity.aliases = (
                retained
            )

            entity.updated_at = (
                _utc_now()
            )

            session.commit()

            return True

        except Exception:
            session.rollback()
            raise

        finally:
            session.close()

    def update_attributes(
        self,
        *,
        entity_id: str,
        attributes: Mapping[
            str,
            Any,
        ],
        replace: bool = False,
    ) -> bool:
        """
        Update structured Entity attributes.

        replace=False:
            merge supplied keys

        replace=True:
            replace the complete attributes object
        """

        entity_id = (
            self._validate_entity_id(
                entity_id
            )
        )

        if not isinstance(
            attributes,
            Mapping,
        ):
            raise EntityValidationError(
                "attributes must be an object."
            )

        payload = {
            str(key): _json_safe(
                value
            )
            for key, value
            in attributes.items()
        }

        session: Session = (
            self.session_factory()
        )

        try:
            entity = (
                session.execute(
                    select(
                        self.Entity
                    ).where(
                        self.Entity.entity_id
                        == entity_id
                    )
                )
                .scalar_one_or_none()
            )

            if entity is None:
                raise EntityValidationError(
                    "Unknown canonical entity_id="
                    f"{entity_id!r}."
                )

            old_attributes = dict(
                entity.attributes
                or {}
            )

            if replace:
                new_attributes = (
                    payload
                )

            else:
                new_attributes = dict(
                    old_attributes
                )

                new_attributes.update(
                    payload
                )

            if (
                new_attributes
                == old_attributes
            ):
                return False

            entity.attributes = (
                new_attributes
            )

            entity.updated_at = (
                _utc_now()
            )

            session.commit()

            return True

        except Exception:
            session.rollback()
            raise

        finally:
            session.close()

    def delete_by_entity_id(
        self,
        entity_id: str,
    ) -> EntityDeleteResult:
        """
        Delete one canonical Entity.

        Relationship foreign-key constraints should
        reject unsafe deletion when the entity remains
        referenced.

        The service does not cascade semantic graph
        identity silently.
        """

        entity_id = (
            self._validate_entity_id(
                entity_id
            )
        )

        session: Session = (
            self.session_factory()
        )

        try:
            entity = (
                session.execute(
                    select(
                        self.Entity
                    ).where(
                        self.Entity.entity_id
                        == entity_id
                    )
                )
                .scalar_one_or_none()
            )

            if entity is None:
                return EntityDeleteResult(
                    entity_id=(
                        entity_id
                    ),
                    deleted=False,
                )

            session.delete(
                entity
            )

            session.commit()

            return EntityDeleteResult(
                entity_id=(
                    entity_id
                ),
                deleted=True,
            )

        except Exception:
            session.rollback()
            raise

        finally:
            session.close()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_by_id(
        self,
        entity_id: str,
    ) -> Optional[Any]:
        entity_id = _clean(
            entity_id
        )

        if not entity_id:
            return None

        session: Session = (
            self.session_factory()
        )

        try:
            return (
                session.execute(
                    select(
                        self.Entity
                    ).where(
                        self.Entity.entity_id
                        == entity_id
                    )
                )
                .scalar_one_or_none()
            )

        finally:
            session.close()

    def get_by_ids(
        self,
        entity_ids: Sequence[
            str
        ],
    ) -> list[Any]:
        """
        Fetch a caller-supplied canonical entity set.

        No hidden result cap.
        """

        cleaned_ids = list(
            dict.fromkeys(
                _clean(
                    entity_id
                )
                for entity_id
                in entity_ids
                if _clean(
                    entity_id
                )
            )
        )

        if not cleaned_ids:
            return []

        session: Session = (
            self.session_factory()
        )

        try:
            rows = list(
                session.execute(
                    select(
                        self.Entity
                    )
                    .where(
                        self.Entity.entity_id.in_(
                            cleaned_ids
                        )
                    )
                )
                .scalars()
                .all()
            )

        finally:
            session.close()

        by_id = {
            _clean(
                entity.entity_id
            ): entity
            for entity
            in rows
        }

        return [
            by_id[
                entity_id
            ]
            for entity_id
            in cleaned_ids
            if entity_id
            in by_id
        ]

    def find_exact_name(
        self,
        name: str,
        *,
        entity_type: str = "",
    ) -> list[Any]:
        """
        Return ALL canonical entities sharing the exact
        normalized name.

        Multiple matches are expected and must remain
        visible to EntityResolutionService.
        """

        normalized_name = (
            normalize_entity_text(
                name
            )
        )

        if not normalized_name:
            return []

        query = (
            select(
                self.Entity
            )
            .where(
                self.Entity.normalized_name
                == normalized_name
            )
        )

        if entity_type:
            query = query.where(
                self.Entity.entity_type
                == normalize_entity_type(
                    entity_type
                )
            )

        session: Session = (
            self.session_factory()
        )

        try:
            return list(
                session.execute(
                    query.order_by(
                        self.Entity.entity_id.asc()
                    )
                )
                .scalars()
                .all()
            )

        finally:
            session.close()

    def find_by_alias(
        self,
        alias: str,
        *,
        entity_type: str = "",
    ) -> list[Any]:
        """
        Return every canonical Entity whose aliases
        lexically match the supplied alias.

        JSON alias lookup remains deterministic and
        portable by filtering registry rows in Python.

        No identity resolution happens here.
        """

        normalized_alias = (
            normalize_entity_text(
                alias
            )
        )

        if not normalized_alias:
            return []

        entities = (
            self.list_entities(
                entity_type=(
                    entity_type
                    or None
                )
            )
        )

        result: list[Any] = []

        for entity in entities:
            normalized_aliases = {
                normalize_entity_text(
                    item
                )
                for item
                in _unique_strings(
                    entity.aliases
                )
            }

            if (
                normalized_alias
                in normalized_aliases
            ):
                result.append(
                    entity
                )

        return result

    def list_entities(
        self,
        *,
        entity_type: Optional[
            str
        ] = None,
        offset: int = 0,
        limit: Optional[
            int
        ] = None,
    ) -> list[Any]:
        """
        Query canonical entities.

        No hidden default limit.
        """

        if offset < 0:
            raise EntityValidationError(
                "offset must be >= 0."
            )

        if (
            limit is not None
            and limit <= 0
        ):
            raise EntityValidationError(
                "limit must be > 0 "
                "when supplied."
            )

        query = select(
            self.Entity
        )

        if entity_type:
            query = query.where(
                self.Entity.entity_type
                == normalize_entity_type(
                    entity_type
                )
            )

        query = (
            query
            .order_by(
                self.Entity.entity_type.asc(),
                self.Entity.normalized_name.asc(),
                self.Entity.entity_id.asc(),
            )
            .offset(
                offset
            )
        )

        if limit is not None:
            query = query.limit(
                limit
            )

        session: Session = (
            self.session_factory()
        )

        try:
            return list(
                session.execute(
                    query
                )
                .scalars()
                .all()
            )

        finally:
            session.close()

    def count_entities(
        self,
        *,
        entity_type: str = "",
    ) -> int:
        return len(
            self.list_entities(
                entity_type=(
                    entity_type
                    or None
                )
            )
        )

    # ------------------------------------------------------------------
    # EntityResolutionService registry boundary
    # ------------------------------------------------------------------

    def registry_records(
        self,
        *,
        entity_type: Optional[
            str
        ] = None,
    ) -> list[
        dict[
            str,
            Any,
        ]
    ]:
        """
        Export complete registry records for
        EntityResolutionService.

        Importantly, duplicate names are preserved.
        """

        return [
            self.to_dict(
                entity
            )
            for entity
            in self.list_entities(
                entity_type=(
                    entity_type
                )
            )
        ]

    # ------------------------------------------------------------------
    # Deterministic candidate generation
    # ------------------------------------------------------------------

    def search_candidates(
        self,
        *,
        mention: str,
        entity_type: str = "",
        context: Optional[
            Mapping[
                str,
                Any,
            ]
        ] = None,
        minimum_token_overlap: float = (
            0.25
        ),
        max_candidates: Optional[
            int
        ] = None,
    ) -> list[
        dict[
            str,
            Any,
        ]
    ]:
        """
        Generate deterministic registry candidates.

        Candidate retrieval order:
        1. exact canonical-name match;
        2. exact alias match;
        3. lexical token overlap.

        This function never:
        - chooses the final entity;
        - merges entities;
        - interprets score as identity probability.

        `context` is carried into candidate metadata for
        EntityResolutionService but does not silently
        change the deterministic lexical score.
        """

        mention = _clean(
            mention
        )

        normalized_mention = (
            normalize_entity_text(
                mention
            )
        )

        if not normalized_mention:
            return []

        if not (
            0.0
            <= minimum_token_overlap
            <= 1.0
        ):
            raise EntityValidationError(
                "minimum_token_overlap must "
                "be between 0 and 1."
            )

        if (
            max_candidates is not None
            and max_candidates <= 0
        ):
            raise EntityValidationError(
                "max_candidates must be > 0 "
                "when supplied."
            )

        context_payload = (
            {
                str(key): _json_safe(
                    value
                )
                for key, value
                in context.items()
            }
            if isinstance(
                context,
                Mapping,
            )
            else {}
        )

        candidates = []

        for entity in (
            self.list_entities(
                entity_type=(
                    entity_type
                    or None
                )
            )
        ):
            record = (
                self.to_dict(
                    entity
                )
            )

            aliases = (
                record[
                    "aliases"
                ]
            )

            exact_name = (
                normalized_mention
                ==
                normalize_entity_text(
                    record[
                        "name"
                    ]
                )
            )

            exact_alias = (
                normalized_mention
                in {
                    normalize_entity_text(
                        alias
                    )
                    for alias
                    in aliases
                }
            )

            lexical_scores = [
                _token_jaccard(
                    mention,
                    record[
                        "name"
                    ],
                )
            ]

            lexical_scores.extend(
                _token_jaccard(
                    mention,
                    alias,
                )
                for alias
                in aliases
            )

            similarity = max(
                lexical_scores
                or [
                    0.0
                ]
            )

            if (
                not exact_name
                and not exact_alias
                and similarity
                < minimum_token_overlap
            ):
                continue

            priority = (
                0
                if exact_name
                else 1
                if exact_alias
                else 2
            )

            candidate = {
                **record,

                "candidate_match": {
                    "exact_name": (
                        exact_name
                    ),

                    "exact_alias": (
                        exact_alias
                    ),

                    "token_overlap": (
                        round(
                            similarity,
                            6,
                        )
                    ),

                    "match_semantics": (
                        "retrieval_heuristic_"
                        "not_identity_confidence"
                    ),

                    "mention": (
                        mention
                    ),

                    "context": (
                        context_payload
                    ),
                },
            }

            candidates.append(
                (
                    priority,
                    -similarity,
                    record[
                        "entity_id"
                    ],
                    candidate,
                )
            )

        candidates.sort(
            key=lambda item: (
                item[0],
                item[1],
                item[2],
            )
        )

        result = [
            item[3]
            for item
            in candidates
        ]

        if max_candidates is not None:
            result = result[
                :max_candidates
            ]

        return result

    # ------------------------------------------------------------------
    # Mention-oriented candidate convenience
    # ------------------------------------------------------------------

    def candidates_for_mention(
        self,
        mention: Mapping[
            str,
            Any,
        ],
        *,
        minimum_token_overlap: float = (
            0.25
        ),
        max_candidates: Optional[
            int
        ] = None,
    ) -> list[
        dict[
            str,
            Any,
        ]
    ]:
        """
        Convenience handoff for Event.entity_mentions.

        Expected mention shape:

            {
                "mention": "...",
                "entity_type": "...",
                "source_span": "...",
                "context": "..."
            }

        This remains candidate retrieval only.
        """

        if not isinstance(
            mention,
            Mapping,
        ):
            raise EntityValidationError(
                "mention must be an object."
            )

        mention_text = _clean(
            mention.get(
                "mention"
            )
        )

        if not mention_text:
            raise EntityValidationError(
                "mention.mention cannot be empty."
            )

        entity_type = _clean(
            mention.get(
                "entity_type"
            )
        )

        context = {
            "source_span": (
                _clean(
                    mention.get(
                        "source_span"
                    )
                )
            ),

            "mention_context": (
                _clean(
                    mention.get(
                        "context"
                    )
                )
            ),
        }

        return self.search_candidates(
            mention=(
                mention_text
            ),
            entity_type=(
                entity_type
            ),
            context=(
                context
            ),
            minimum_token_overlap=(
                minimum_token_overlap
            ),
            max_candidates=(
                max_candidates
            ),
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(
        self,
        entity: Any,
    ) -> dict[
        str,
        Any,
    ]:
        """
        Export the canonical Entity registry record.
        """

        return {
            "entity_id": (
                entity.entity_id
            ),

            "name": (
                entity.name
            ),

            "normalized_name": (
                entity.normalized_name
            ),

            "entity_type": (
                entity.entity_type
            ),

            "aliases": (
                _unique_strings(
                    entity.aliases
                )
            ),

            "attributes": (
                _json_safe(
                    entity.attributes
                    or {}
                )
            ),

            "created_at": (
                _json_safe(
                    entity.created_at
                )
            ),

            "updated_at": (
                _json_safe(
                    entity.updated_at
                )
            ),
        }


__all__ = [
    "SERVICE_VERSION",
    "REQUIRED_MODEL_FIELDS",
    "EntityServiceError",
    "EntityValidationError",
    "EntityModelContractError",
    "EntityWriteResult",
    "EntityDeleteResult",
    "normalize_entity_text",
    "normalize_entity_type",
    "stable_entity_id",
    "EntityService",
]