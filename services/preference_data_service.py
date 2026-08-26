"""
Training/ranking-ready preference data service.

Recommended location:
    services/preference_data_service.py

Purpose
-------
UserFeedbackService
        ↓
structured feedback event
        ↓
UserPreferenceService
        ↓
PreferenceDataService
        ↓
pointwise preference records
pairwise chosen/rejected records
quality-example records
dataset exports
        ↓
InsightRankerService / PreferenceEval / future local trainer

The service creates durable learning records from explicit user preference
signals while keeping them completely separate from factual evidence.

Critical epistemic boundary
---------------------------
PREFERENCE DATA != FACTUAL EVIDENCE

No record emitted by this service may be used as:
- FACT evidence;
- METRIC evidence;
- TRIPLE evidence;
- source corroboration;
- grounding support.

Every exported record is explicitly marked:
    "is_grounding_evidence": False

Learning boundary
-----------------
This service prepares training/ranking data. It does NOT:
- train a model;
- perform RL/RLHF;
- modify model weights;
- change PolicyRegistry;
- change Blueprint budgets;
- authorize research execution;
- authorize report release;
- lower grounding standards.

The service supports the project's staged preference-learning path:

1. structured feedback;
2. persistent preference profile;
3. pointwise/pairwise ranking data;
4. local insight ranker;
5. optional future preference-model training.

Pairwise example
----------------
User:
    "Give me more like INSIGHT-3 than INSIGHT-1."

UserFeedbackService:
    preferred_insight_ids = ["INSIGHT-3"]
    rejected_insight_ids  = ["INSIGHT-1"]

PreferenceDataService:
{
    "record_type": "pairwise_preference",
    "chosen": {
        "insight_id": "INSIGHT-3",
        "text": "..."
    },
    "rejected": {
        "insight_id": "INSIGHT-1",
        "text": "..."
    },
    "preference_basis": "explicit_feedback",
    "is_grounding_evidence": false
}

Standalone quality example
--------------------------
User:
    "This is a good insight because it connects regulation to incentives."

If supplied as a positive example, the service creates a pointwise quality
example even when it is not tied to an insight ID.

Dataset splitting
-----------------
Train/validation/test assignment is deterministic and group-aware.

By default records from the same:
    report_id -> run_id -> feedback_id
group are assigned to the same split. This reduces leakage between nearly
identical records from one feedback episode.

Split ratios are configuration, not quality thresholds.

Free/local constraint
---------------------
- pure Python;
- no paid API;
- no LLM required;
- no external transmission;
- JSON/JSONL export supported locally.

No private chain-of-thought is stored.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Set


SERVICE_VERSION = "preference-data-service-v2.0"

ALLOWED_RECORD_TYPES = {
    "pointwise_preference",
    "pairwise_preference",
    "quality_example",
}

ALLOWED_LABELS = {
    "positive",
    "negative",
    "neutral",
}

ALLOWED_SENTIMENTS = {
    "positive",
    "negative",
    "mixed",
    "neutral",
}

ALLOWED_SPLITS = {
    "train",
    "validation",
    "test",
}

ALLOWED_EXAMPLE_ORIGINS = {
    "insight_feedback",
    "explicit_positive_example",
    "explicit_negative_example",
    "rating",
    "pairwise_feedback",
}

# Preference learning records must not carry control-plane instructions or
# evidence payloads. Provenance IDs that identify the feedback/report/insight
# are allowed, but evidence IDs/content are intentionally excluded.
PROHIBITED_CONTEXT_FIELDS = {
    "blueprint",
    "blueprint_id",
    "policy",
    "policy_registry",
    "execution_policy",
    "runtime_policy",
    "quality_policy",
    "release_policy",
    "release_gate",
    "model_route",
    "model_name",
    "search_budget",
    "token_budget",
    "cost_budget",
    "max_run_seconds",
    "research_depth",
    "skip_grounding",
    "skip_red_team",
    "evidence_id",
    "evidence_ids",
    "fact_ids",
    "metric_ids",
    "triple_ids",
    "source_ids",
    "source_provenance",
}

ALLOWED_CONTEXT_FIELDS = {
    "research_question",
    "topic",
    "report_section",
    "report_id",
    "run_id",
    "cluster_id",
    "analysis_lens",
}

DEFAULT_CONFIG = {
    "train_ratio": 0.80,
    "validation_ratio": 0.10,
    "test_ratio": 0.10,
    "max_text_characters": 12_000,
    "max_reason_characters": 2_000,
    "max_records_per_feedback_event": 100,
}


class PreferenceDataError(RuntimeError):
    """Base exception."""


class PreferenceDataValidationError(PreferenceDataError):
    """Raised when preference-learning input violates the contract."""


class PreferenceDataIntegrityError(PreferenceDataError):
    """Raised when stable record identity or lineage is inconsistent."""


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

    return hashlib.sha256(
        value
    ).hexdigest()


def _stable_id(prefix: str, payload: Any, length: int = 24) -> str:
    return (
        f"{prefix}-"
        + _sha256(
            _canonical_json_bytes(
                payload
            )
        )[:length].upper()
    )


def _fingerprint(value: Any) -> str:
    return _sha256(
        _canonical_json_bytes(
            value
        )
    )


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


def _stable_unique_strings(values: Any) -> list[str]:
    if values is None:
        return []

    if isinstance(values, str):
        values = [values]

    if not isinstance(
        values,
        (list, tuple, set),
    ):
        raise PreferenceDataValidationError(
            "Expected a string or sequence of strings."
        )

    result = []
    seen = set()

    for value in values:
        text = _clean(
            value
        )

        if text and text not in seen:
            seen.add(
                text
            )
            result.append(
                text
            )

    return result


def _truncate_checked(
    value: str,
    *,
    limit: int,
    field_name: str,
) -> str:
    """
    Preference text is user guidance, not evidence, so bounded context storage is
    acceptable. Still, truncation is explicit in the record metadata.
    """
    value = _clean(
        value
    )

    if len(value) <= limit:
        return value

    return value[:limit].rstrip()


def _contains_prohibited_field(
    value: Any,
    path: str = "",
) -> Optional[str]:
    if isinstance(
        value,
        Mapping,
    ):
        for key, item in value.items():
            normalized_key = _normalize_enum(
                key
            )

            current_path = (
                f"{path}.{key}"
                if path
                else str(key)
            )

            if normalized_key in PROHIBITED_CONTEXT_FIELDS:
                return current_path

            nested = _contains_prohibited_field(
                item,
                current_path,
            )

            if nested:
                return nested

    elif isinstance(
        value,
        (list, tuple),
    ):
        for index, item in enumerate(
            value
        ):
            nested = _contains_prohibited_field(
                item,
                f"{path}[{index}]",
            )

            if nested:
                return nested

    return None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PreferenceDataService:
    """
    Build local ranking/training records from structured feedback.

    The service is thread-safe for normal in-process use.
    """

    def __init__(
        self,
        *,
        dataset_name: str = "insight_preferences",
        config: Optional[Mapping[str, Any]] = None,
        clock: Any = _utc_now_iso,
    ) -> None:
        self.dataset_name = (
            _clean(
                dataset_name
            )
            or "insight_preferences"
        )

        if not callable(
            clock
        ):
            raise PreferenceDataValidationError(
                "clock must be callable."
            )

        self.clock = clock
        self.config = self._normalize_config(
            config
        )

        self._lock = threading.RLock()

        # preference_record_id -> immutable normalized record
        self._records: Dict[
            str,
            Dict[str, Any],
        ] = {}

        # feedback_id -> generated preference_record_ids
        self._feedback_index: Dict[
            str,
            list[str],
        ] = {}

        # feedback event fingerprint, for idempotency/collision detection.
        self._feedback_fingerprints: Dict[
            str,
            str,
        ] = {}

    # ------------------------------------------------------------------
    # Main ingestion
    # ------------------------------------------------------------------

    def ingest_feedback(
        self,
        feedback_event: Mapping[str, Any],
        *,
        available_insights: Sequence[Mapping[str, Any]] = (),
        context: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Convert one UserFeedbackService event into learning records.

        Records created:
        - pointwise records from preferred/rejected target insight IDs;
        - pointwise records from clearly positive/negative rated targeted
          insights;
        - pairwise records from preferred × rejected insight IDs;
        - quality-example records from explicit positive/negative examples.

        If an insight ID is referenced but its text is unavailable, the record is
        not guessed. It is listed in `skipped_candidates`.
        """
        feedback = self._normalize_feedback_event(
            feedback_event
        )

        inventory = self._normalize_available_insights(
            available_insights
        )

        normalized_context = self._normalize_context(
            context
        )

        feedback_id = feedback[
            "feedback_id"
        ]

        incoming_fingerprint = _fingerprint(
            {
                "feedback": feedback,
                "available_insights": inventory,
                "context": normalized_context,
            }
        )

        with self._lock:
            prior_fingerprint = self._feedback_fingerprints.get(
                feedback_id
            )

            if prior_fingerprint is not None:
                if prior_fingerprint != incoming_fingerprint:
                    raise PreferenceDataIntegrityError(
                        "feedback_id was already ingested with different "
                        "feedback/inventory/context."
                    )

                existing_ids = list(
                    self._feedback_index.get(
                        feedback_id,
                        [],
                    )
                )

                return {
                    "feedback_id": feedback_id,
                    "created_record_ids": [],
                    "existing_record_ids": existing_ids,
                    "skipped_candidates": [],
                    "idempotent_replay": True,
                    "record_count": len(
                        existing_ids
                    ),
                }

            candidates, skipped = self._build_candidates(
                feedback=feedback,
                inventory=inventory,
                context=normalized_context,
            )

            if (
                len(candidates)
                > self.config[
                    "max_records_per_feedback_event"
                ]
            ):
                raise PreferenceDataValidationError(
                    "Feedback event would create more than "
                    "max_records_per_feedback_event records."
                )

            normalized_records = [
                self._finalize_record(
                    candidate,
                    feedback=feedback,
                    context=normalized_context,
                )
                for candidate in candidates
            ]

            created_ids = []

            for record in normalized_records:
                record_id = record[
                    "preference_record_id"
                ]

                existing = self._records.get(
                    record_id
                )

                if existing is not None:
                    if (
                        _fingerprint(
                            existing
                        )
                        != _fingerprint(
                            record
                        )
                    ):
                        raise PreferenceDataIntegrityError(
                            "Preference record ID collision with different "
                            f"content: {record_id!r}."
                        )

                    continue

                self._records[
                    record_id
                ] = record
                created_ids.append(
                    record_id
                )

            all_ids = [
                record[
                    "preference_record_id"
                ]
                for record in normalized_records
            ]

            self._feedback_fingerprints[
                feedback_id
            ] = incoming_fingerprint

            self._feedback_index[
                feedback_id
            ] = all_ids

            return {
                "feedback_id": feedback_id,
                "created_record_ids": created_ids,
                "existing_record_ids": [
                    record_id
                    for record_id in all_ids
                    if record_id not in created_ids
                ],
                "skipped_candidates": skipped,
                "idempotent_replay": False,
                "record_count": len(
                    all_ids
                ),
            }

    # ------------------------------------------------------------------
    # Direct record methods
    # ------------------------------------------------------------------

    def add_pairwise_preference(
        self,
        *,
        chosen_insight: Mapping[str, Any],
        rejected_insight: Mapping[str, Any],
        feedback_id: str,
        reason: str = "",
        context: Optional[Mapping[str, Any]] = None,
        origin: str = "pairwise_feedback",
    ) -> Dict[str, Any]:
        """
        Add one explicit chosen-vs-rejected preference.

        Useful when the UI captures pairwise feedback directly.
        """
        feedback_id = _clean(
            feedback_id
        )

        if not feedback_id:
            raise PreferenceDataValidationError(
                "feedback_id cannot be empty."
            )

        chosen = self._normalize_insight(
            chosen_insight
        )
        rejected = self._normalize_insight(
            rejected_insight
        )

        if (
            chosen["insight_id"]
            == rejected["insight_id"]
        ):
            raise PreferenceDataValidationError(
                "chosen and rejected insight IDs must differ."
            )

        origin = _normalize_enum(
            origin
        )

        if origin not in ALLOWED_EXAMPLE_ORIGINS:
            raise PreferenceDataValidationError(
                f"Unsupported origin={origin!r}."
            )

        fake_feedback = {
            "feedback_id": feedback_id,
            "recorded_at": self.clock(),
            "sentiment": "positive",
            "rating": None,
        }

        candidate = {
            "record_type": "pairwise_preference",
            "origin": origin,
            "chosen": chosen,
            "rejected": rejected,
            "reason": _clean(
                reason
            ),
        }

        record = self._finalize_record(
            candidate,
            feedback=fake_feedback,
            context=self._normalize_context(
                context
            ),
        )

        with self._lock:
            self._insert_record(
                record
            )

        return deepcopy(
            record
        )

    def add_quality_example(
        self,
        *,
        text: str,
        label: str,
        feedback_id: str,
        reason: str = "",
        context: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Add a standalone positive/negative quality example.
        """
        label = _normalize_enum(
            label
        )

        if label not in {
            "positive",
            "negative",
        }:
            raise PreferenceDataValidationError(
                "Standalone quality example label must be positive or negative."
            )

        text = _clean(
            text
        )

        if not text:
            raise PreferenceDataValidationError(
                "text cannot be empty."
            )

        feedback_id = _clean(
            feedback_id
        )

        if not feedback_id:
            raise PreferenceDataValidationError(
                "feedback_id cannot be empty."
            )

        fake_feedback = {
            "feedback_id": feedback_id,
            "recorded_at": self.clock(),
            "sentiment": label,
            "rating": None,
        }

        candidate = {
            "record_type": "quality_example",
            "origin": (
                "explicit_positive_example"
                if label == "positive"
                else "explicit_negative_example"
            ),
            "label": label,
            "text": text,
            "reason": _clean(
                reason
            ),
        }

        record = self._finalize_record(
            candidate,
            feedback=fake_feedback,
            context=self._normalize_context(
                context
            ),
        )

        with self._lock:
            self._insert_record(
                record
            )

        return deepcopy(
            record
        )

    # ------------------------------------------------------------------
    # Retrieval / dataset view
    # ------------------------------------------------------------------

    def get_record(
        self,
        preference_record_id: str,
    ) -> Dict[str, Any]:
        preference_record_id = _clean(
            preference_record_id
        )

        with self._lock:
            record = self._records.get(
                preference_record_id
            )

            if record is None:
                raise PreferenceDataValidationError(
                    "Unknown preference_record_id="
                    f"{preference_record_id!r}."
                )

            return deepcopy(
                record
            )

    def list_records(
        self,
        *,
        record_type: str = "",
        split: str = "",
        label: str = "",
    ) -> list[Dict[str, Any]]:
        record_type = _normalize_enum(
            record_type
        )
        split = _normalize_enum(
            split
        )
        label = _normalize_enum(
            label
        )

        if (
            record_type
            and record_type
            not in ALLOWED_RECORD_TYPES
        ):
            raise PreferenceDataValidationError(
                f"Unsupported record_type={record_type!r}."
            )

        if split and split not in ALLOWED_SPLITS:
            raise PreferenceDataValidationError(
                f"Unsupported split={split!r}."
            )

        if label and label not in ALLOWED_LABELS:
            raise PreferenceDataValidationError(
                f"Unsupported label={label!r}."
            )

        with self._lock:
            records = []

            for record in self._records.values():
                if (
                    record_type
                    and record[
                        "record_type"
                    ] != record_type
                ):
                    continue

                if (
                    split
                    and record[
                        "dataset_split"
                    ] != split
                ):
                    continue

                if label:
                    record_label = _clean(
                        record.get(
                            "label"
                        )
                    )

                    if record_label != label:
                        continue

                records.append(
                    deepcopy(
                        record
                    )
                )

            records.sort(
                key=lambda item: (
                    item[
                        "dataset_split"
                    ],
                    item[
                        "record_type"
                    ],
                    item[
                        "preference_record_id"
                    ],
                )
            )

            return records

    def dataset_stats(
        self,
    ) -> Dict[str, Any]:
        with self._lock:
            by_type = {
                key: 0
                for key in sorted(
                    ALLOWED_RECORD_TYPES
                )
            }

            by_split = {
                key: 0
                for key in (
                    "train",
                    "validation",
                    "test",
                )
            }

            by_label = {
                key: 0
                for key in (
                    "positive",
                    "negative",
                    "neutral",
                )
            }

            for record in self._records.values():
                by_type[
                    record[
                        "record_type"
                    ]
                ] += 1

                by_split[
                    record[
                        "dataset_split"
                    ]
                ] += 1

                label = _clean(
                    record.get(
                        "label"
                    )
                )

                if label in by_label:
                    by_label[
                        label
                    ] += 1

            return {
                "dataset_name": self.dataset_name,
                "record_count": len(
                    self._records
                ),
                "feedback_event_count": len(
                    self._feedback_index
                ),
                "by_record_type": by_type,
                "by_split": by_split,
                "by_label": by_label,
                "metadata": {
                    "service_version": SERVICE_VERSION,
                    "preference_data_is_grounding_evidence": False,
                    "external_transmission": False,
                },
            }

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_dataset(
        self,
        *,
        split: str = "",
        record_type: str = "",
    ) -> Dict[str, Any]:
        records = self.list_records(
            record_type=record_type,
            split=split,
        )

        payload = {
            "dataset_name": self.dataset_name,
            "service_version": SERVICE_VERSION,
            "records": records,
            "stats": self.dataset_stats(),
            "metadata": {
                "preference_data_is_grounding_evidence": False,
                "examples_are_quality_guidance_not_evidence": True,
                "contains_private_chain_of_thought": False,
                "external_transmission": False,
            },
        }

        payload[
            "dataset_sha256"
        ] = _fingerprint(
            {
                key: value
                for key, value
                in payload.items()
                if key != "dataset_sha256"
            }
        )

        return payload

    def export_jsonl(
        self,
        path: str | os.PathLike[str],
        *,
        split: str = "",
        record_type: str = "",
        trainer_format: bool = False,
    ) -> Dict[str, Any]:
        """
        Export local JSONL.

        trainer_format=False
            complete auditable preference records.

        trainer_format=True
            compact records intended for a future local preference trainer /
            InsightRanker. Provenance IDs are retained, but control-plane fields
            and evidence are absent.
        """
        output_path = Path(
            path
        )

        records = self.list_records(
            split=split,
            record_type=record_type,
        )

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with output_path.open(
            "w",
            encoding="utf-8",
        ) as handle:
            for record in records:
                payload = (
                    self._trainer_record(
                        record
                    )
                    if trainer_format
                    else record
                )

                handle.write(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )

        raw = output_path.read_bytes()

        return {
            "path": str(
                output_path
            ),
            "record_count": len(
                records
            ),
            "sha256": _sha256(
                raw
            ),
            "trainer_format": bool(
                trainer_format
            ),
            "split": _normalize_enum(
                split
            ),
            "record_type": _normalize_enum(
                record_type
            ),
        }

    def _trainer_record(
        self,
        record: Mapping[str, Any],
    ) -> Dict[str, Any]:
        record_type = record[
            "record_type"
        ]

        base = {
            "preference_record_id": record[
                "preference_record_id"
            ],
            "record_type": record_type,
            "dataset_split": record[
                "dataset_split"
            ],
            "context": deepcopy(
                record[
                    "context"
                ]
            ),
            "feedback_id": record[
                "feedback_id"
            ],
            "is_grounding_evidence": False,
        }

        if record_type == "pairwise_preference":
            return {
                **base,
                "chosen": deepcopy(
                    record[
                        "chosen"
                    ]
                ),
                "rejected": deepcopy(
                    record[
                        "rejected"
                    ]
                ),
                "reason": record[
                    "reason"
                ],
            }

        return {
            **base,
            "label": record[
                "label"
            ],
            "text": record[
                "text"
            ],
            "insight_id": _clean(
                record.get(
                    "insight_id"
                )
            ),
            "reason": record[
                "reason"
            ],
        }

    # ------------------------------------------------------------------
    # Candidate generation
    # ------------------------------------------------------------------

    def _build_candidates(
        self,
        *,
        feedback: Mapping[str, Any],
        inventory: Mapping[str, Mapping[str, Any]],
        context: Mapping[str, Any],
    ) -> tuple[
        list[Dict[str, Any]],
        list[Dict[str, Any]],
    ]:
        candidates: list[
            Dict[str, Any]
        ] = []
        skipped: list[
            Dict[str, Any]
        ] = []

        preferences = feedback[
            "structured_preferences"
        ]

        preferred_ids = _stable_unique_strings(
            preferences[
                "preferred_insight_ids"
            ]
        )
        rejected_ids = _stable_unique_strings(
            preferences[
                "rejected_insight_ids"
            ]
        )

        overlap = sorted(
            set(
                preferred_ids
            )
            & set(
                rejected_ids
            )
        )

        if overlap:
            raise PreferenceDataIntegrityError(
                "The same insight cannot be preferred and rejected in one "
                "feedback event: "
                + ", ".join(
                    overlap
                )
            )

        # --------------------------------------------------------------
        # Explicit preferred/rejected insight pointwise records.
        # --------------------------------------------------------------
        for insight_id in preferred_ids:
            insight = inventory.get(
                insight_id
            )

            if insight is None:
                skipped.append(
                    {
                        "candidate_type": "pointwise_preference",
                        "insight_id": insight_id,
                        "reason": "preferred_insight_text_unavailable",
                    }
                )
                continue

            candidates.append(
                {
                    "record_type": "pointwise_preference",
                    "origin": "insight_feedback",
                    "label": "positive",
                    "insight_id": insight_id,
                    "text": insight[
                        "text"
                    ],
                    "reason": (
                        feedback[
                            "raw_feedback"
                        ]
                        or "Explicitly preferred insight."
                    ),
                }
            )

        for insight_id in rejected_ids:
            insight = inventory.get(
                insight_id
            )

            if insight is None:
                skipped.append(
                    {
                        "candidate_type": "pointwise_preference",
                        "insight_id": insight_id,
                        "reason": "rejected_insight_text_unavailable",
                    }
                )
                continue

            candidates.append(
                {
                    "record_type": "pointwise_preference",
                    "origin": "insight_feedback",
                    "label": "negative",
                    "insight_id": insight_id,
                    "text": insight[
                        "text"
                    ],
                    "reason": (
                        feedback[
                            "raw_feedback"
                        ]
                        or "Explicitly rejected insight."
                    ),
                }
            )

        # --------------------------------------------------------------
        # Pairwise records: explicit preferred × rejected.
        # --------------------------------------------------------------
        for chosen_id in preferred_ids:
            chosen = inventory.get(
                chosen_id
            )

            if chosen is None:
                continue

            for rejected_id in rejected_ids:
                rejected = inventory.get(
                    rejected_id
                )

                if rejected is None:
                    continue

                if chosen_id == rejected_id:
                    continue

                candidates.append(
                    {
                        "record_type": "pairwise_preference",
                        "origin": "pairwise_feedback",
                        "chosen": deepcopy(
                            chosen
                        ),
                        "rejected": deepcopy(
                            rejected
                        ),
                        "reason": (
                            feedback[
                                "raw_feedback"
                            ]
                            or "Explicit pairwise preference."
                        ),
                    }
                )

        # --------------------------------------------------------------
        # Rating/sentiment on specifically targeted insights.
        #
        # We only convert unambiguous signals:
        # rating 4-5 -> positive
        # rating 1-2 -> negative
        # rating 3   -> neutral
        #
        # If rating is absent, positive/negative sentiment can still be used
        # when the event targets concrete insight IDs.
        # --------------------------------------------------------------
        target_ids = feedback[
            "target_insight_ids"
        ]

        rating = feedback[
            "rating"
        ]

        target_label = ""

        if rating is not None:
            if rating >= 4:
                target_label = "positive"
            elif rating <= 2:
                target_label = "negative"
            else:
                target_label = "neutral"

        elif feedback[
            "sentiment"
        ] in {
            "positive",
            "negative",
        }:
            target_label = feedback[
                "sentiment"
            ]

        if target_label:
            for insight_id in target_ids:
                # Do not duplicate an explicit preferred/rejected pointwise
                # record for the same insight/event.
                if (
                    target_label == "positive"
                    and insight_id in preferred_ids
                ):
                    continue

                if (
                    target_label == "negative"
                    and insight_id in rejected_ids
                ):
                    continue

                insight = inventory.get(
                    insight_id
                )

                if insight is None:
                    skipped.append(
                        {
                            "candidate_type": "rating_pointwise",
                            "insight_id": insight_id,
                            "reason": "target_insight_text_unavailable",
                        }
                    )
                    continue

                candidates.append(
                    {
                        "record_type": "pointwise_preference",
                        "origin": (
                            "rating"
                            if rating is not None
                            else "insight_feedback"
                        ),
                        "label": target_label,
                        "insight_id": insight_id,
                        "text": insight[
                            "text"
                        ],
                        "reason": feedback[
                            "raw_feedback"
                        ],
                    }
                )

        # --------------------------------------------------------------
        # Standalone quality examples.
        # --------------------------------------------------------------
        for example in preferences[
            "positive_examples"
        ]:
            candidates.append(
                {
                    "record_type": "quality_example",
                    "origin": "explicit_positive_example",
                    "label": "positive",
                    "text": example[
                        "text"
                    ],
                    "reason": example[
                        "reason"
                    ],
                }
            )

        for example in preferences[
            "negative_examples"
        ]:
            candidates.append(
                {
                    "record_type": "quality_example",
                    "origin": "explicit_negative_example",
                    "label": "negative",
                    "text": example[
                        "text"
                    ],
                    "reason": example[
                        "reason"
                    ],
                }
            )

        return (
            self._deduplicate_candidates(
                candidates
            ),
            skipped,
        )

    def _deduplicate_candidates(
        self,
        candidates: Sequence[
            Mapping[str, Any]
        ],
    ) -> list[Dict[str, Any]]:
        result = []
        seen = set()

        for candidate in candidates:
            key = _fingerprint(
                candidate
            )

            if key in seen:
                continue

            seen.add(
                key
            )
            result.append(
                deepcopy(
                    dict(
                        candidate
                    )
                )
            )

        return result

    # ------------------------------------------------------------------
    # Final record construction
    # ------------------------------------------------------------------

    def _finalize_record(
        self,
        candidate: Mapping[str, Any],
        *,
        feedback: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> Dict[str, Any]:
        record_type = _normalize_enum(
            candidate.get(
                "record_type"
            )
        )

        if record_type not in ALLOWED_RECORD_TYPES:
            raise PreferenceDataValidationError(
                f"Unsupported record_type={record_type!r}."
            )

        feedback_id = _clean(
            feedback.get(
                "feedback_id"
            )
        )

        if not feedback_id:
            raise PreferenceDataValidationError(
                "Preference record requires feedback_id lineage."
            )

        origin = _normalize_enum(
            candidate.get(
                "origin"
            )
        )

        if origin not in ALLOWED_EXAMPLE_ORIGINS:
            raise PreferenceDataValidationError(
                f"Unsupported origin={origin!r}."
            )

        reason_raw = _clean(
            candidate.get(
                "reason"
            )
        )

        reason = _truncate_checked(
            reason_raw,
            limit=self.config[
                "max_reason_characters"
            ],
            field_name="reason",
        )

        reason_truncated = (
            len(
                reason_raw
            )
            > self.config[
                "max_reason_characters"
            ]
        )

        group_key = self._dataset_group_key(
            feedback=feedback,
            context=context,
        )

        split = self._assign_split(
            group_key
        )

        common = {
            "dataset_name": self.dataset_name,
            "record_type": record_type,
            "origin": origin,
            "feedback_id": feedback_id,
            "recorded_at": _clean(
                feedback.get(
                    "recorded_at"
                )
            ),
            "context": deepcopy(
                dict(
                    context
                )
            ),
            "dataset_group_key": group_key,
            "dataset_split": split,
            "reason": reason,
            "is_grounding_evidence": False,
            "metadata": {
                "service_version": SERVICE_VERSION,
                "reason_truncated": reason_truncated,
                "preference_data_is_grounding_evidence": False,
                "authorizes_execution": False,
                "authorizes_release": False,
                "modifies_policy": False,
                "contains_private_chain_of_thought": False,
                "external_transmission": False,
            },
        }

        if record_type == "pairwise_preference":
            chosen = self._normalize_insight(
                candidate.get(
                    "chosen"
                )
                or {}
            )
            rejected = self._normalize_insight(
                candidate.get(
                    "rejected"
                )
                or {}
            )

            if (
                chosen["insight_id"]
                == rejected[
                    "insight_id"
                ]
            ):
                raise PreferenceDataValidationError(
                    "Pairwise chosen/rejected insights must differ."
                )

            payload = {
                **common,
                "chosen": chosen,
                "rejected": rejected,
                "preference_basis": "explicit_feedback",
            }

        else:
            label = _normalize_enum(
                candidate.get(
                    "label"
                )
            )

            if label not in ALLOWED_LABELS:
                raise PreferenceDataValidationError(
                    f"Unsupported label={label!r}."
                )

            raw_text = _clean(
                candidate.get(
                    "text"
                )
            )

            if not raw_text:
                raise PreferenceDataValidationError(
                    "Pointwise/quality example text cannot be empty."
                )

            text = _truncate_checked(
                raw_text,
                limit=self.config[
                    "max_text_characters"
                ],
                field_name="text",
            )

            payload = {
                **common,
                "label": label,
                "insight_id": _clean(
                    candidate.get(
                        "insight_id"
                    )
                ),
                "text": text,
            }

            payload[
                "metadata"
            ][
                "text_truncated"
            ] = (
                len(
                    raw_text
                )
                > self.config[
                    "max_text_characters"
                ]
            )

        record_id = _stable_id(
            "PREFREC",
            {
                "dataset_name": self.dataset_name,
                "record_type": record_type,
                "feedback_id": feedback_id,
                "candidate": candidate,
                "context": context,
            },
        )

        payload[
            "preference_record_id"
        ] = record_id

        payload[
            "record_sha256"
        ] = _fingerprint(
            {
                key: value
                for key, value
                in payload.items()
                if key != "record_sha256"
            }
        )

        return payload

    # ------------------------------------------------------------------
    # Split assignment
    # ------------------------------------------------------------------

    def _dataset_group_key(
        self,
        *,
        feedback: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> str:
        report_id = _clean(
            context.get(
                "report_id"
            )
            or feedback.get(
                "target_report_id"
            )
        )

        run_id = _clean(
            context.get(
                "run_id"
            )
            or feedback.get(
                "target_run_id"
            )
        )

        feedback_id = _clean(
            feedback.get(
                "feedback_id"
            )
        )

        if report_id:
            return (
                "report:"
                + report_id
            )

        if run_id:
            return (
                "run:"
                + run_id
            )

        return (
            "feedback:"
            + feedback_id
        )

    def _assign_split(
        self,
        group_key: str,
    ) -> str:
        digest = hashlib.sha256(
            (
                self.dataset_name
                + "|"
                + group_key
            ).encode(
                "utf-8"
            )
        ).digest()

        # Deterministic uniform [0, 1).
        value = (
            int.from_bytes(
                digest[:8],
                byteorder="big",
                signed=False,
            )
            / float(
                2**64
            )
        )

        train = self.config[
            "train_ratio"
        ]
        validation = self.config[
            "validation_ratio"
        ]

        if value < train:
            return "train"

        if value < (
            train
            + validation
        ):
            return "validation"

        return "test"

    # ------------------------------------------------------------------
    # Validation / normalization
    # ------------------------------------------------------------------

    def _normalize_feedback_event(
        self,
        event: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(
            event,
            Mapping,
        ):
            raise PreferenceDataValidationError(
                "feedback_event must be an object."
            )

        feedback_id = _clean(
            event.get(
                "feedback_id"
            )
        )

        if not feedback_id:
            raise PreferenceDataValidationError(
                "feedback_event is missing feedback_id."
            )

        sentiment = _normalize_enum(
            event.get(
                "sentiment"
            )
            or "neutral"
        )

        if sentiment not in ALLOWED_SENTIMENTS:
            raise PreferenceDataValidationError(
                f"Unsupported sentiment={sentiment!r}."
            )

        preferences = (
            event.get(
                "structured_preferences"
            )
            or {}
        )

        if not isinstance(
            preferences,
            Mapping,
        ):
            raise PreferenceDataValidationError(
                "structured_preferences must be an object."
            )

        metadata = (
            event.get(
                "metadata"
            )
            or {}
        )

        if (
            metadata
            and not isinstance(
                metadata,
                Mapping,
            )
        ):
            raise PreferenceDataValidationError(
                "feedback_event.metadata must be an object."
            )

        if bool(
            metadata.get(
                "user_feedback_is_grounding_evidence"
            )
        ):
            raise PreferenceDataIntegrityError(
                "PreferenceDataService rejects feedback marked as evidence."
            )

        if bool(
            metadata.get(
                "authorizes_execution"
            )
        ):
            raise PreferenceDataIntegrityError(
                "Preference feedback cannot authorize execution."
            )

        return {
            "feedback_id": feedback_id,
            "recorded_at": _clean(
                event.get(
                    "recorded_at"
                )
            ),
            "target_report_id": _clean(
                event.get(
                    "target_report_id"
                )
            ),
            "target_run_id": _clean(
                event.get(
                    "target_run_id"
                )
            ),
            "target_insight_ids": _stable_unique_strings(
                event.get(
                    "target_insight_ids"
                )
                or []
            ),
            "sentiment": sentiment,
            "rating": self._normalize_rating(
                event.get(
                    "rating"
                )
            ),
            "raw_feedback": _clean(
                event.get(
                    "raw_feedback"
                )
            ),
            "structured_preferences": {
                "preferred_insight_ids": _stable_unique_strings(
                    preferences.get(
                        "preferred_insight_ids"
                    )
                    or []
                ),
                "rejected_insight_ids": _stable_unique_strings(
                    preferences.get(
                        "rejected_insight_ids"
                    )
                    or []
                ),
                "positive_examples": [
                    self._normalize_example(
                        item
                    )
                    for item in (
                        preferences.get(
                            "positive_examples"
                        )
                        or []
                    )
                ],
                "negative_examples": [
                    self._normalize_example(
                        item
                    )
                    for item in (
                        preferences.get(
                            "negative_examples"
                        )
                        or []
                    )
                ],
            },
        }

    def _normalize_available_insights(
        self,
        insights: Sequence[Mapping[str, Any]],
    ) -> Dict[str, Dict[str, str]]:
        if insights is None:
            return {}

        if (
            not isinstance(
                insights,
                Sequence,
            )
            or isinstance(
                insights,
                (str, bytes),
            )
        ):
            raise PreferenceDataValidationError(
                "available_insights must be a sequence."
            )

        result = {}

        for index, item in enumerate(
            insights
        ):
            if not isinstance(
                item,
                Mapping,
            ):
                raise PreferenceDataValidationError(
                    f"available_insights[{index}] must be an object."
                )

            insight = self._normalize_insight(
                item
            )

            insight_id = insight[
                "insight_id"
            ]

            if insight_id in result:
                if (
                    result[
                        insight_id
                    ][
                        "text"
                    ]
                    != insight[
                        "text"
                    ]
                ):
                    raise PreferenceDataIntegrityError(
                        "Duplicate insight_id has different text."
                    )

                continue

            result[
                insight_id
            ] = insight

        return result

    def _normalize_insight(
        self,
        insight: Mapping[str, Any],
    ) -> Dict[str, str]:
        if not isinstance(
            insight,
            Mapping,
        ):
            raise PreferenceDataValidationError(
                "insight must be an object."
            )

        insight_id = _clean(
            insight.get(
                "insight_id"
            )
            or insight.get(
                "id"
            )
        )

        if not insight_id:
            raise PreferenceDataValidationError(
                "insight is missing insight_id."
            )

        raw_text = _clean(
            insight.get(
                "insight"
            )
            or insight.get(
                "text"
            )
            or insight.get(
                "statement"
            )
        )

        if not raw_text:
            raise PreferenceDataValidationError(
                f"Insight {insight_id!r} is missing text."
            )

        text = _truncate_checked(
            raw_text,
            limit=self.config[
                "max_text_characters"
            ],
            field_name="insight.text",
        )

        return {
            "insight_id": insight_id,
            "insight_revision_id": _clean(
                insight.get(
                    "insight_revision_id"
                )
            ),
            "text": text,
        }

    def _normalize_context(
        self,
        context: Optional[
            Mapping[str, Any]
        ],
    ) -> Dict[str, str]:
        if context is None:
            return {}

        if not isinstance(
            context,
            Mapping,
        ):
            raise PreferenceDataValidationError(
                "context must be an object."
            )

        prohibited = _contains_prohibited_field(
            context
        )

        if prohibited:
            raise PreferenceDataValidationError(
                "Context contains prohibited control/evidence field at "
                f"{prohibited!r}."
            )

        unknown_fields = (
            set(
                context
            )
            - ALLOWED_CONTEXT_FIELDS
        )

        if unknown_fields:
            raise PreferenceDataValidationError(
                "Unsupported context fields: "
                + ", ".join(
                    sorted(
                        unknown_fields
                    )
                )
            )

        return {
            key: _clean(
                value
            )
            for key, value
            in context.items()
            if _clean(
                value
            )
        }

    def _normalize_example(
        self,
        item: Any,
    ) -> Dict[str, str]:
        if isinstance(
            item,
            str,
        ):
            text = _clean(
                item
            )

            if not text:
                raise PreferenceDataValidationError(
                    "Example text cannot be empty."
                )

            return {
                "text": text,
                "reason": "",
            }

        if not isinstance(
            item,
            Mapping,
        ):
            raise PreferenceDataValidationError(
                "Example must be text or an object."
            )

        text = _clean(
            item.get(
                "text"
            )
            or item.get(
                "insight"
            )
            or item.get(
                "example"
            )
        )

        if not text:
            raise PreferenceDataValidationError(
                "Example is missing text."
            )

        return {
            "text": text,
            "reason": _clean(
                item.get(
                    "reason"
                )
                or item.get(
                    "why"
                )
            ),
        }

    def _normalize_rating(
        self,
        value: Any,
    ) -> Optional[int]:
        if value is None or value == "":
            return None

        try:
            rating = int(
                value
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise PreferenceDataValidationError(
                "rating must be an integer from 1 to 5."
            ) from exc

        if not 1 <= rating <= 5:
            raise PreferenceDataValidationError(
                "rating must be between 1 and 5."
            )

        return rating

    def _normalize_config(
        self,
        config: Optional[
            Mapping[str, Any]
        ],
    ) -> Dict[str, Any]:
        result = deepcopy(
            DEFAULT_CONFIG
        )

        if config is not None:
            if not isinstance(
                config,
                Mapping,
            ):
                raise PreferenceDataValidationError(
                    "config must be an object."
                )

            unknown = (
                set(
                    config
                )
                - set(
                    DEFAULT_CONFIG
                )
            )

            if unknown:
                raise PreferenceDataValidationError(
                    "Unknown config fields: "
                    + ", ".join(
                        sorted(
                            unknown
                        )
                    )
                )

            result.update(
                dict(
                    config
                )
            )

        for field in (
            "train_ratio",
            "validation_ratio",
            "test_ratio",
        ):
            try:
                result[
                    field
                ] = float(
                    result[
                        field
                    ]
                )
            except (
                TypeError,
                ValueError,
            ) as exc:
                raise PreferenceDataValidationError(
                    f"{field} must be numeric."
                ) from exc

            if (
                result[
                    field
                ]
                < 0
            ):
                raise PreferenceDataValidationError(
                    f"{field} cannot be negative."
                )

        ratio_sum = (
            result[
                "train_ratio"
            ]
            + result[
                "validation_ratio"
            ]
            + result[
                "test_ratio"
            ]
        )

        if abs(
            ratio_sum
            - 1.0
        ) > 1e-9:
            raise PreferenceDataValidationError(
                "train/validation/test ratios must sum to 1.0."
            )

        for field in (
            "max_text_characters",
            "max_reason_characters",
            "max_records_per_feedback_event",
        ):
            try:
                result[
                    field
                ] = int(
                    result[
                        field
                    ]
                )
            except (
                TypeError,
                ValueError,
            ) as exc:
                raise PreferenceDataValidationError(
                    f"{field} must be an integer."
                ) from exc

            if result[field] <= 0:
                raise PreferenceDataValidationError(
                    f"{field} must be > 0."
                )

        return result

    # ------------------------------------------------------------------
    # Internal insertion
    # ------------------------------------------------------------------

    def _insert_record(
        self,
        record: Mapping[str, Any],
    ) -> None:
        record_id = _clean(
            record.get(
                "preference_record_id"
            )
        )

        if not record_id:
            raise PreferenceDataValidationError(
                "Preference record is missing preference_record_id."
            )

        existing = self._records.get(
            record_id
        )

        if existing is not None:
            if (
                _fingerprint(
                    existing
                )
                != _fingerprint(
                    record
                )
            ):
                raise PreferenceDataIntegrityError(
                    "Preference record ID collision with different content."
                )

            return

        self._records[
            record_id
        ] = deepcopy(
            dict(
                record
            )
        )