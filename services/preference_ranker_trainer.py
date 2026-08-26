"""
Local preference-ranker trainer.

Recommended location:
    training/preference_ranker_trainer.py

Purpose
-------
PreferenceDataService
        ↓
pointwise / pairwise preference records
        ↓
PreferenceRankerTrainer
        ↓
local preference model artifact
        ↓
LocalPreferenceScorer
        ↓
InsightRankerService.learned_preference_scorer

This is a deliberately small, transparent preference-learning layer for the
research product. It trains a local linear ranker from explicit user preference
data without requiring a paid API, hosted model, RLHF stack, or external ML
service.

What it learns
--------------
The trainer supports:

1. Pointwise preference records
       positive insight/example  -> target 1.0
       negative insight/example  -> target 0.0
       neutral insight/example   -> target 0.5

2. Pairwise preference records
       chosen insight > rejected insight

Pairwise records are trained through a logistic preference objective over:

       features(chosen) - features(rejected)

A mirrored negative example is also added:

       features(rejected) - features(chosen)

This makes the learned relation directionally symmetric.

What it does NOT learn
----------------------
The model predicts USER PREFERENCE / USEFULNESS tendency.

It does NOT predict:
- factual truth;
- grounding confidence;
- source credibility;
- report release safety;
- whether an insight is supported;
- whether counterevidence exists.

Therefore:

    learned preference score
        != truth probability
        != evidence confidence
        != release authorization

InsightRankerService remains responsible for combining this score with quality
gating, and a high learned preference score must never resurrect an insight
whose quality/grounding status is ineligible.

Critical epistemic boundary
---------------------------
PREFERENCE DATA != FACTUAL EVIDENCE

The trainer rejects any input record marked:

    is_grounding_evidence = True

and rejects evidence payload fields in preference context.

No training example produced here should be written into the factual evidence
store.

Model
-----
The default model is a deterministic local hashed sparse linear logistic model.

Why this choice:
- no mandatory external dependency;
- fast enough for a portfolio/local product;
- easy to inspect;
- deterministic feature hashing;
- supports pairwise and pointwise learning;
- serializes to JSON;
- easy to replace later with a stronger local model without changing the
  InsightRankerService scorer contract.

Features
--------
Text is converted to hashed unigram/bigram features using SHA-256 rather than
Python's process-randomized hash().

Optional context text may include:
- research_question
- topic
- report_section
- cluster_id
- analysis_lens
- report_id
- run_id

Context is prefixed by field name before hashing so the same token in an insight
and in a research question does not become exactly the same feature.

The model intentionally does not consume:
- research_depth;
- runtime policy;
- search budgets;
- evidence IDs;
- source IDs;
- release policy.

Training / validation / test
----------------------------
PreferenceDataService already assigns deterministic group-aware splits.

This trainer:
- trains on `train`;
- optionally uses `validation` for early stopping;
- never uses `test` for optimization;
- can evaluate `validation` and `test` separately.

If a dataset has no train records, training fails closed rather than silently
training on test data.

Free/local constraint
---------------------
Pure Python standard library only.

No:
- network calls;
- API keys;
- paid services;
- external telemetry;
- model upload.

Integration
-----------
The generated scorer directly matches InsightRankerService:

    scorer.score(
        insight=...,
        quality_assessment=...,
        preference_profile=...,
        context=...
    )

and returns:

{
    "score": 0.0 .. 1.0,
    "model_version": "...",
    "metadata": {...}
}

The scorer deliberately ignores quality_assessment for model prediction because
quality is already a separate, stronger component in InsightRankerService.
This avoids double-counting quality as if it were a learned preference signal.

No private chain-of-thought is stored.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple


SERVICE_VERSION = "preference-ranker-trainer-v1.0"
MODEL_FORMAT_VERSION = "local-preference-linear-v1"
FEATURE_EXTRACTOR_VERSION = "hashed-token-ngram-v1"

ALLOWED_RECORD_TYPES = {
    "pointwise_preference",
    "pairwise_preference",
    "quality_example",
}

ALLOWED_SPLITS = {
    "train",
    "validation",
    "test",
}

ALLOWED_LABELS = {
    "positive",
    "negative",
    "neutral",
}

ALLOWED_CONTEXT_FIELDS = {
    "research_question",
    "topic",
    "report_section",
    "cluster_id",
    "analysis_lens",
    "report_id",
    "run_id",
}

PROHIBITED_CONTEXT_FIELDS = {
    "research_depth",
    "analysis_mode",
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
    "max_search_rounds",
    "max_search_queries",
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

PROHIBITED_PRIVATE_REASONING_FIELDS = {
    "chain_of_thought",
    "chainofthought",
    "cot",
    "scratchpad",
    "hidden_reasoning",
    "hiddenreasoning",
    "internal_monologue",
    "internalmonologue",
    "raw_reasoning",
    "rawreasoning",
    "private_reasoning",
    "privatereasoning",
    "reasoning_tokens",
    "reasoningtokens",
    "deliberation",
    "deliberation_trace",
    "deliberationtrace",
    "raw_prompt",
    "rawprompt",
    "raw_completion",
    "rawcompletion",
    "raw_response",
    "rawresponse",
}

DEFAULT_CONFIG = {
    "feature_dimension": 2048,
    "ngram_min": 1,
    "ngram_max": 2,
    "lowercase": True,
    "l2_regularization": 0.0005,
    "learning_rate": 0.08,
    "learning_rate_decay": 0.04,
    "epochs": 120,
    "early_stopping_patience": 12,
    "minimum_improvement": 1e-5,
    "random_seed": 7,
    "neutral_weight": 0.25,
    "pairwise_weight": 1.0,
    "pointwise_weight": 1.0,
    "include_context_features": True,
    "max_tokens_per_text": 512,
}

TOKEN_PATTERN = re.compile(
    r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?"
)


class PreferenceRankerTrainerError(RuntimeError):
    """Base trainer exception."""


class PreferenceRankerTrainerValidationError(
    PreferenceRankerTrainerError
):
    """Raised when training data/config violates the contract."""


class PreferenceRankerTrainerIntegrityError(
    PreferenceRankerTrainerError
):
    """Raised when record/model identity or lineage is inconsistent."""


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
    text = re.sub(
        r"[^a-z0-9_]+",
        "",
        text,
    )
    return text.strip("_")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(
        value,
        (str, int, float, bool),
    ):
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


def _stable_id(
    prefix: str,
    payload: Any,
    length: int = 24,
) -> str:
    return (
        f"{prefix}-"
        + _sha256(
            _canonical_json_bytes(
                payload
            )
        )[:length].upper()
    )


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


def _sigmoid(value: float) -> float:
    # Numerically stable sigmoid.
    if value >= 0.0:
        z = math.exp(
            -value
        )
        return 1.0 / (
            1.0
            + z
        )

    z = math.exp(
        value
    )
    return z / (
        1.0
        + z
    )


def _clip_probability(value: float) -> float:
    return max(
        1e-9,
        min(
            1.0 - 1e-9,
            float(value),
        ),
    )


def _log_loss(
    probability: float,
    target: float,
) -> float:
    probability = _clip_probability(
        probability
    )

    return -(
        target
        * math.log(
            probability
        )
        + (
            1.0
            - target
        )
        * math.log(
            1.0
            - probability
        )
    )


def _contains_prohibited_field(
    value: Any,
    path: str = "",
) -> Optional[str]:
    prohibited = (
        PROHIBITED_CONTEXT_FIELDS
        | PROHIBITED_PRIVATE_REASONING_FIELDS
    )

    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = _normalize_enum(
                key
            )

            current_path = (
                f"{path}.{key}"
                if path
                else str(key)
            )

            if normalized_key in prohibited:
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
# Deterministic feature extractor
# ---------------------------------------------------------------------------


class HashedTextFeatureExtractor:
    """
    Deterministic sparse text feature extractor.

    Feature index uses SHA-256 so feature placement is stable across Python
    processes and machines.
    """

    def __init__(
        self,
        *,
        feature_dimension: int,
        ngram_min: int,
        ngram_max: int,
        lowercase: bool,
        include_context_features: bool,
        max_tokens_per_text: int,
    ) -> None:
        if feature_dimension <= 0:
            raise PreferenceRankerTrainerValidationError(
                "feature_dimension must be > 0."
            )

        if ngram_min <= 0:
            raise PreferenceRankerTrainerValidationError(
                "ngram_min must be >= 1."
            )

        if ngram_max < ngram_min:
            raise PreferenceRankerTrainerValidationError(
                "ngram_max must be >= ngram_min."
            )

        if max_tokens_per_text <= 0:
            raise PreferenceRankerTrainerValidationError(
                "max_tokens_per_text must be > 0."
            )

        self.feature_dimension = int(
            feature_dimension
        )
        self.ngram_min = int(
            ngram_min
        )
        self.ngram_max = int(
            ngram_max
        )
        self.lowercase = bool(
            lowercase
        )
        self.include_context_features = bool(
            include_context_features
        )
        self.max_tokens_per_text = int(
            max_tokens_per_text
        )

    def transform(
        self,
        text: str,
        *,
        context: Optional[
            Mapping[str, Any]
        ] = None,
    ) -> Dict[int, float]:
        text = _clean(
            text
        )

        features: Dict[int, float] = {}

        if text:
            self._add_text_features(
                features,
                text=text,
                prefix="text",
            )

        if (
            self.include_context_features
            and context
        ):
            normalized_context = self._normalize_context(
                context
            )

            for key in sorted(
                normalized_context
            ):
                value = normalized_context[
                    key
                ]

                if not value:
                    continue

                self._add_text_features(
                    features,
                    text=value,
                    prefix=(
                        "context:"
                        + key
                    ),
                )

        return self._l2_normalize(
            features
        )

    def pairwise_difference(
        self,
        chosen_text: str,
        rejected_text: str,
        *,
        context: Optional[
            Mapping[str, Any]
        ] = None,
    ) -> Dict[int, float]:
        chosen = self.transform(
            chosen_text,
            context=context,
        )
        rejected = self.transform(
            rejected_text,
            context=context,
        )

        result = dict(
            chosen
        )

        for index, value in rejected.items():
            result[
                index
            ] = (
                result.get(
                    index,
                    0.0,
                )
                - value
            )

            if abs(
                result[
                    index
                ]
            ) < 1e-15:
                result.pop(
                    index,
                    None,
                )

        return result

    def _add_text_features(
        self,
        features: Dict[int, float],
        *,
        text: str,
        prefix: str,
    ) -> None:
        tokens = self._tokens(
            text
        )

        if not tokens:
            return

        for n in range(
            self.ngram_min,
            self.ngram_max + 1,
        ):
            if len(
                tokens
            ) < n:
                continue

            for start in range(
                0,
                len(tokens) - n + 1,
            ):
                gram = " ".join(
                    tokens[
                        start:start + n
                    ]
                )

                feature_name = (
                    prefix
                    + "::"
                    + str(n)
                    + "::"
                    + gram
                )

                index, sign = self._hash_feature(
                    feature_name
                )

                features[
                    index
                ] = (
                    features.get(
                        index,
                        0.0,
                    )
                    + sign
                )

    def _tokens(
        self,
        text: str,
    ) -> list[str]:
        if self.lowercase:
            text = text.casefold()

        tokens = TOKEN_PATTERN.findall(
            text
        )

        return tokens[
            : self.max_tokens_per_text
        ]

    def _hash_feature(
        self,
        feature_name: str,
    ) -> Tuple[int, float]:
        digest = hashlib.sha256(
            feature_name.encode(
                "utf-8"
            )
        ).digest()

        index = (
            int.from_bytes(
                digest[:8],
                byteorder="big",
                signed=False,
            )
            % self.feature_dimension
        )

        sign = (
            1.0
            if (
                digest[8]
                & 1
            ) == 0
            else -1.0
        )

        return (
            index,
            sign,
        )

    def _l2_normalize(
        self,
        features: Dict[int, float],
    ) -> Dict[int, float]:
        if not features:
            return {}

        norm = math.sqrt(
            sum(
                value
                * value
                for value
                in features.values()
            )
        )

        if norm <= 0.0:
            return {}

        return {
            index: (
                value
                / norm
            )
            for index, value
            in features.items()
        }

    def _normalize_context(
        self,
        context: Mapping[str, Any],
    ) -> Dict[str, str]:
        if not isinstance(
            context,
            Mapping,
        ):
            raise PreferenceRankerTrainerValidationError(
                "context must be an object."
            )

        prohibited = _contains_prohibited_field(
            context
        )

        if prohibited:
            raise PreferenceRankerTrainerValidationError(
                "Context contains prohibited execution/evidence/private field "
                f"at {prohibited!r}."
            )

        unknown = (
            set(
                context
            )
            - ALLOWED_CONTEXT_FIELDS
        )

        if unknown:
            raise PreferenceRankerTrainerValidationError(
                "Unsupported context fields: "
                + ", ".join(
                    sorted(
                        unknown
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

    def config_dict(
        self,
    ) -> Dict[str, Any]:
        return {
            "feature_dimension": self.feature_dimension,
            "ngram_min": self.ngram_min,
            "ngram_max": self.ngram_max,
            "lowercase": self.lowercase,
            "include_context_features": (
                self.include_context_features
            ),
            "max_tokens_per_text": self.max_tokens_per_text,
            "feature_extractor_version": (
                FEATURE_EXTRACTOR_VERSION
            ),
        }


# ---------------------------------------------------------------------------
# Inference scorer
# ---------------------------------------------------------------------------


class LocalPreferenceScorer:
    """
    Loaded local scorer compatible with InsightRankerService.
    """

    def __init__(
        self,
        model: Mapping[str, Any],
    ) -> None:
        self.model = self._validate_model(
            model
        )

        config = self.model[
            "feature_extractor"
        ]

        self.extractor = HashedTextFeatureExtractor(
            feature_dimension=int(
                config[
                    "feature_dimension"
                ]
            ),
            ngram_min=int(
                config[
                    "ngram_min"
                ]
            ),
            ngram_max=int(
                config[
                    "ngram_max"
                ]
            ),
            lowercase=bool(
                config[
                    "lowercase"
                ]
            ),
            include_context_features=bool(
                config[
                    "include_context_features"
                ]
            ),
            max_tokens_per_text=int(
                config[
                    "max_tokens_per_text"
                ]
            ),
        )

        self.weights = {
            int(
                index
            ): float(
                value
            )
            for index, value
            in self.model[
                "weights"
            ].items()
        }

        self.intercept = float(
            self.model[
                "intercept"
            ]
        )

    @classmethod
    def load(
        cls,
        path: str | os.PathLike[str],
    ) -> "LocalPreferenceScorer":
        path = Path(
            path
        )

        if not path.exists():
            raise PreferenceRankerTrainerValidationError(
                f"Model path does not exist: {path}"
            )

        payload = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

        return cls(
            payload
        )

    def score(
        self,
        *,
        insight: Mapping[str, Any],
        quality_assessment: Optional[
            Mapping[str, Any]
        ] = None,
        preference_profile: Optional[
            Mapping[str, Any]
        ] = None,
        context: Optional[
            Mapping[str, Any]
        ] = None,
    ) -> Dict[str, Any]:
        """
        InsightRankerService-compatible scorer.

        `quality_assessment` and `preference_profile` are intentionally not fed
        into the learned text model. The outer InsightRankerService already
        combines those dimensions separately.
        """
        if not isinstance(
            insight,
            Mapping,
        ):
            raise PreferenceRankerTrainerValidationError(
                "insight must be an object."
            )

        text = _clean(
            insight.get(
                "text"
            )
            or insight.get(
                "insight"
            )
            or insight.get(
                "statement"
            )
        )

        if not text:
            raise PreferenceRankerTrainerValidationError(
                "insight text cannot be empty."
            )

        normalized_context = self.extractor._normalize_context(
            context
            or {}
        )

        features = self.extractor.transform(
            text,
            context=normalized_context,
        )

        raw_score = self.intercept

        for index, value in features.items():
            raw_score += (
                self.weights.get(
                    index,
                    0.0,
                )
                * value
            )

        probability = _sigmoid(
            raw_score
        )

        return {
            "score": probability,
            "model_version": self.model[
                "model_id"
            ],
            "metadata": {
                "model_format_version": MODEL_FORMAT_VERSION,
                "feature_extractor_version": (
                    FEATURE_EXTRACTOR_VERSION
                ),
                "prediction_semantics": (
                    "learned_user_preference_tendency_not_truth_probability"
                ),
                "quality_assessment_used_by_model": False,
                "preference_profile_used_by_model": False,
                "is_grounding_evidence": False,
                "authorizes_release": False,
                "private_chain_of_thought_stored": False,
            },
        }

    def score_text(
        self,
        text: str,
        *,
        context: Optional[
            Mapping[str, Any]
        ] = None,
    ) -> float:
        return float(
            self.score(
                insight={
                    "text": text,
                },
                context=context,
            )[
                "score"
            ]
        )

    def pairwise_probability(
        self,
        *,
        chosen_text: str,
        rejected_text: str,
        context: Optional[
            Mapping[str, Any]
        ] = None,
    ) -> float:
        """
        Probability that chosen is preferred over rejected under the learned
        pairwise margin. Intercept is intentionally omitted from pairwise
        comparison because it cancels between item scores.
        """
        features = self.extractor.pairwise_difference(
            chosen_text,
            rejected_text,
            context=context,
        )

        margin = 0.0

        for index, value in features.items():
            margin += (
                self.weights.get(
                    index,
                    0.0,
                )
                * value
            )

        return _sigmoid(
            margin
        )

    def _validate_model(
        self,
        model: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(
            model,
            Mapping,
        ):
            raise PreferenceRankerTrainerValidationError(
                "model must be an object."
            )

        payload = deepcopy(
            dict(
                model
            )
        )

        if (
            payload.get(
                "model_format_version"
            )
            != MODEL_FORMAT_VERSION
        ):
            raise PreferenceRankerTrainerValidationError(
                "Unsupported model_format_version."
            )

        if not _clean(
            payload.get(
                "model_id"
            )
        ):
            raise PreferenceRankerTrainerValidationError(
                "model is missing model_id."
            )

        extractor = payload.get(
            "feature_extractor"
        )

        if not isinstance(
            extractor,
            Mapping,
        ):
            raise PreferenceRankerTrainerValidationError(
                "model.feature_extractor must be an object."
            )

        weights = payload.get(
            "weights"
        )

        if not isinstance(
            weights,
            Mapping,
        ):
            raise PreferenceRankerTrainerValidationError(
                "model.weights must be an object."
            )

        try:
            float(
                payload.get(
                    "intercept",
                    0.0,
                )
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise PreferenceRankerTrainerValidationError(
                "model.intercept must be numeric."
            ) from exc

        supplied_hash = _clean(
            payload.get(
                "model_sha256"
            )
        )

        if supplied_hash:
            computed = _sha256(
                _canonical_json_bytes(
                    {
                        key: value
                        for key, value
                        in payload.items()
                        if key != "model_sha256"
                    }
                )
            )

            if supplied_hash != computed:
                raise PreferenceRankerTrainerIntegrityError(
                    "Model fingerprint mismatch."
                )

        return payload


# ---------------------------------------------------------------------------
# Trainer
# ---------------------------------------------------------------------------


class PreferenceRankerTrainer:
    """
    Train a local linear preference scorer from PreferenceDataService records.
    """

    def __init__(
        self,
        *,
        config: Optional[
            Mapping[str, Any]
        ] = None,
        clock: Any = _utc_now_iso,
    ) -> None:
        if not callable(
            clock
        ):
            raise PreferenceRankerTrainerValidationError(
                "clock must be callable."
            )

        self.clock = clock
        self.config = self._normalize_config(
            config
        )

        self.extractor = HashedTextFeatureExtractor(
            feature_dimension=self.config[
                "feature_dimension"
            ],
            ngram_min=self.config[
                "ngram_min"
            ],
            ngram_max=self.config[
                "ngram_max"
            ],
            lowercase=self.config[
                "lowercase"
            ],
            include_context_features=self.config[
                "include_context_features"
            ],
            max_tokens_per_text=self.config[
                "max_tokens_per_text"
            ],
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_jsonl(
        self,
        paths: (
            str
            | os.PathLike[str]
            | Sequence[
                str | os.PathLike[str]
            ]
        ),
    ) -> list[Dict[str, Any]]:
        if isinstance(
            paths,
            (str, os.PathLike),
        ):
            paths = [
                paths
            ]

        if not isinstance(
            paths,
            Sequence,
        ):
            raise PreferenceRankerTrainerValidationError(
                "paths must be a path or sequence of paths."
            )

        records = []

        for raw_path in paths:
            path = Path(
                raw_path
            )

            if not path.exists():
                raise PreferenceRankerTrainerValidationError(
                    f"Training data path does not exist: {path}"
                )

            with path.open(
                "r",
                encoding="utf-8",
            ) as handle:
                for line_number, line in enumerate(
                    handle,
                    start=1,
                ):
                    line = line.strip()

                    if not line:
                        continue

                    try:
                        payload = json.loads(
                            line
                        )
                    except json.JSONDecodeError as exc:
                        raise PreferenceRankerTrainerValidationError(
                            f"Malformed JSONL at {path}:{line_number}."
                        ) from exc

                    if not isinstance(
                        payload,
                        Mapping,
                    ):
                        raise PreferenceRankerTrainerValidationError(
                            f"JSONL record at {path}:{line_number} "
                            "must be an object."
                        )

                    records.append(
                        dict(
                            payload
                        )
                    )

        return records

    def train(
        self,
        *,
        records: Optional[
            Sequence[
                Mapping[str, Any]
            ]
        ] = None,
        jsonl_paths: Optional[
            (
                str
                | os.PathLike[str]
                | Sequence[
                    str | os.PathLike[str]
                ]
            )
        ] = None,
        model_path: Optional[
            str | os.PathLike[str]
        ] = None,
    ) -> Dict[str, Any]:
        """
        Train and optionally save a model.

        Exactly one of `records` or `jsonl_paths` must be supplied.
        """
        if (
            records is None
            and jsonl_paths is None
        ):
            raise PreferenceRankerTrainerValidationError(
                "Provide records or jsonl_paths."
            )

        if (
            records is not None
            and jsonl_paths is not None
        ):
            raise PreferenceRankerTrainerValidationError(
                "Provide records or jsonl_paths, not both."
            )

        if jsonl_paths is not None:
            raw_records = self.load_jsonl(
                jsonl_paths
            )
        else:
            raw_records = list(
                records
                or []
            )

        normalized = self._normalize_records(
            raw_records
        )

        model = self._fit(
            normalized
        )

        if model_path is not None:
            self.save_model(
                model,
                model_path,
            )

        return model

    def save_model(
        self,
        model: Mapping[str, Any],
        path: str | os.PathLike[str],
    ) -> Dict[str, Any]:
        # Validate using scorer contract before persistence.
        scorer = LocalPreferenceScorer(
            model
        )

        path = Path(
            path
        )

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        serialized = json.dumps(
            scorer.model,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )

        path.write_text(
            serialized,
            encoding="utf-8",
        )

        return {
            "path": str(
                path
            ),
            "model_id": scorer.model[
                "model_id"
            ],
            "sha256": _sha256(
                path.read_bytes()
            ),
            "bytes": path.stat().st_size,
        }

    def load_model(
        self,
        path: str | os.PathLike[str],
    ) -> Dict[str, Any]:
        return deepcopy(
            LocalPreferenceScorer.load(
                path
            ).model
        )

    def scorer_from_model(
        self,
        model_or_path: (
            Mapping[str, Any]
            | str
            | os.PathLike[str]
        ),
    ) -> LocalPreferenceScorer:
        if isinstance(
            model_or_path,
            Mapping,
        ):
            return LocalPreferenceScorer(
                model_or_path
            )

        return LocalPreferenceScorer.load(
            model_or_path
        )

    def evaluate(
        self,
        *,
        model: Mapping[str, Any],
        records: Sequence[
            Mapping[str, Any]
        ],
        splits: Sequence[str] = (
            "validation",
            "test",
        ),
    ) -> Dict[str, Any]:
        normalized = self._normalize_records(
            records
        )
        scorer = LocalPreferenceScorer(
            model
        )

        requested_splits = []

        for split in splits:
            split = _normalize_enum(
                split
            )

            if split not in ALLOWED_SPLITS:
                raise PreferenceRankerTrainerValidationError(
                    f"Unsupported evaluation split={split!r}."
                )

            if split not in requested_splits:
                requested_splits.append(
                    split
                )

        by_split = {}

        for split in requested_splits:
            subset = [
                record
                for record in normalized
                if record[
                    "dataset_split"
                ] == split
            ]

            by_split[
                split
            ] = self._evaluate_records(
                scorer,
                subset,
            )

        return {
            "model_id": model[
                "model_id"
            ],
            "by_split": by_split,
            "metadata": {
                "evaluation_uses_preference_labels_not_truth_labels": True,
                "is_grounding_evidence": False,
                "private_chain_of_thought_stored": False,
            },
        }

    # ------------------------------------------------------------------
    # Fit
    # ------------------------------------------------------------------

    def _fit(
        self,
        records: Sequence[
            Mapping[str, Any]
        ],
    ) -> Dict[str, Any]:
        train_records = [
            record
            for record in records
            if record[
                "dataset_split"
            ] == "train"
        ]

        validation_records = [
            record
            for record in records
            if record[
                "dataset_split"
            ] == "validation"
        ]

        test_records = [
            record
            for record in records
            if record[
                "dataset_split"
            ] == "test"
        ]

        if not train_records:
            raise PreferenceRankerTrainerValidationError(
                "No train records are available. The trainer will not "
                "silently train on validation/test data."
            )

        train_examples = self._records_to_training_examples(
            train_records
        )

        validation_examples = self._records_to_training_examples(
            validation_records
        )

        if not train_examples:
            raise PreferenceRankerTrainerValidationError(
                "Train split produced no usable learning examples."
            )

        dimension = self.config[
            "feature_dimension"
        ]

        weights = [
            0.0
            for _ in range(
                dimension
            )
        ]
        intercept = 0.0

        rng = random.Random(
            self.config[
                "random_seed"
            ]
        )

        best_weights = list(
            weights
        )
        best_intercept = intercept
        best_epoch = 0
        best_validation_loss = math.inf
        epochs_without_improvement = 0

        history = []

        for epoch in range(
            1,
            self.config[
                "epochs"
            ] + 1,
        ):
            order = list(
                range(
                    len(
                        train_examples
                    )
                )
            )
            rng.shuffle(
                order
            )

            learning_rate = (
                self.config[
                    "learning_rate"
                ]
                / (
                    1.0
                    + self.config[
                        "learning_rate_decay"
                    ]
                    * (
                        epoch - 1
                    )
                )
            )

            train_loss = 0.0
            train_weight = 0.0

            for example_index in order:
                example = train_examples[
                    example_index
                ]

                features = example[
                    "features"
                ]
                target = example[
                    "target"
                ]
                sample_weight = example[
                    "weight"
                ]

                logit = intercept

                for feature_index, value in features.items():
                    logit += (
                        weights[
                            feature_index
                        ]
                        * value
                    )

                probability = _sigmoid(
                    logit
                )

                train_loss += (
                    sample_weight
                    * _log_loss(
                        probability,
                        target,
                    )
                )
                train_weight += sample_weight

                error = (
                    probability
                    - target
                )

                # Intercept has no L2 regularization.
                intercept -= (
                    learning_rate
                    * sample_weight
                    * error
                )

                l2 = self.config[
                    "l2_regularization"
                ]

                # Sparse SGD: regularize touched dimensions.
                for feature_index, value in features.items():
                    gradient = (
                        sample_weight
                        * error
                        * value
                        + l2
                        * weights[
                            feature_index
                        ]
                    )

                    weights[
                        feature_index
                    ] -= (
                        learning_rate
                        * gradient
                    )

            train_loss = (
                train_loss
                / train_weight
                if train_weight > 0.0
                else 0.0
            )

            validation_loss = None

            if validation_examples:
                validation_loss = self._example_loss(
                    weights=weights,
                    intercept=intercept,
                    examples=validation_examples,
                )

                improved = (
                    best_validation_loss
                    - validation_loss
                    > self.config[
                        "minimum_improvement"
                    ]
                )

                if improved:
                    best_validation_loss = validation_loss
                    best_weights = list(
                        weights
                    )
                    best_intercept = intercept
                    best_epoch = epoch
                    epochs_without_improvement = 0
                else:
                    epochs_without_improvement += 1

            else:
                # Without validation split, final epoch is accepted.
                best_weights = list(
                    weights
                )
                best_intercept = intercept
                best_epoch = epoch

            history.append(
                {
                    "epoch": epoch,
                    "learning_rate": round(
                        learning_rate,
                        8,
                    ),
                    "train_log_loss": round(
                        train_loss,
                        8,
                    ),
                    "validation_log_loss": (
                        round(
                            validation_loss,
                            8,
                        )
                        if validation_loss
                        is not None
                        else None
                    ),
                }
            )

            if (
                validation_examples
                and epochs_without_improvement
                >= self.config[
                    "early_stopping_patience"
                ]
            ):
                break

        # Sparse serialization.
        sparse_weights = {
            str(index): round(
                value,
                12,
            )
            for index, value in enumerate(
                best_weights
            )
            if abs(
                value
            ) > 1e-15
        }

        trained_at = self.clock()

        dataset_fingerprint = _sha256(
            _canonical_json_bytes(
                [
                    {
                        "preference_record_id": record[
                            "preference_record_id"
                        ],
                        "record_type": record[
                            "record_type"
                        ],
                        "dataset_split": record[
                            "dataset_split"
                        ],
                        "record_sha256": record.get(
                            "record_sha256",
                            "",
                        ),
                    }
                    for record in sorted(
                        records,
                        key=lambda item: (
                            item[
                                "dataset_split"
                            ],
                            item[
                                "preference_record_id"
                            ],
                        ),
                    )
                ]
            )
        )

        model_id = _stable_id(
            "PREFMODEL",
            {
                "model_format_version": MODEL_FORMAT_VERSION,
                "dataset_fingerprint": dataset_fingerprint,
                "config": self.config,
                "best_epoch": best_epoch,
                "intercept": round(
                    best_intercept,
                    12,
                ),
                "weights": sparse_weights,
            },
        )

        model = {
            "model_id": model_id,
            "model_format_version": MODEL_FORMAT_VERSION,
            "service_version": SERVICE_VERSION,
            "trained_at": trained_at,
            "prediction_semantics": (
                "learned_user_preference_tendency_not_truth_probability"
            ),
            "feature_extractor": self.extractor.config_dict(),
            "training_config": deepcopy(
                self.config
            ),
            "intercept": round(
                best_intercept,
                12,
            ),
            "weights": sparse_weights,
            "training_summary": {
                "dataset_fingerprint": dataset_fingerprint,
                "record_count": len(
                    records
                ),
                "train_record_count": len(
                    train_records
                ),
                "validation_record_count": len(
                    validation_records
                ),
                "test_record_count": len(
                    test_records
                ),
                "train_learning_example_count": len(
                    train_examples
                ),
                "validation_learning_example_count": len(
                    validation_examples
                ),
                "best_epoch": best_epoch,
                "epochs_completed": len(
                    history
                ),
                "best_validation_log_loss": (
                    None
                    if not validation_examples
                    else round(
                        best_validation_loss,
                        8,
                    )
                ),
                "training_record_ids": [
                    record[
                        "preference_record_id"
                    ]
                    for record in train_records
                ],
                "validation_record_ids": [
                    record[
                        "preference_record_id"
                    ]
                    for record in validation_records
                ],
                "test_record_ids": [
                    record[
                        "preference_record_id"
                    ]
                    for record in test_records
                ],
            },
            "training_history": history,
            "metadata": {
                "preference_data_is_grounding_evidence": False,
                "model_predicts_truth": False,
                "model_authorizes_release": False,
                "test_split_used_for_optimization": False,
                "external_training_service_used": False,
                "paid_service_used": False,
                "private_chain_of_thought_stored": False,
                "raw_training_text_embedded_in_model": False,
            },
        }

        scorer = LocalPreferenceScorer(
            {
                **model,
                "model_sha256": "",
            }
        )

        # Evaluate using final/best model.
        model[
            "evaluation"
        ] = {
            "train": self._evaluate_records(
                scorer,
                train_records,
            ),
            "validation": self._evaluate_records(
                scorer,
                validation_records,
            ),
            "test": self._evaluate_records(
                scorer,
                test_records,
            ),
        }

        model[
            "model_sha256"
        ] = _sha256(
            _canonical_json_bytes(
                {
                    key: value
                    for key, value in model.items()
                    if key != "model_sha256"
                }
            )
        )

        # Final self-validation.
        LocalPreferenceScorer(
            model
        )

        return model

    # ------------------------------------------------------------------
    # Training examples
    # ------------------------------------------------------------------

    def _records_to_training_examples(
        self,
        records: Sequence[
            Mapping[str, Any]
        ],
    ) -> list[Dict[str, Any]]:
        examples = []

        for record in records:
            record_type = record[
                "record_type"
            ]

            context = record[
                "context"
            ]

            if record_type in {
                "pointwise_preference",
                "quality_example",
            }:
                label = record[
                    "label"
                ]

                target = {
                    "positive": 1.0,
                    "negative": 0.0,
                    "neutral": 0.5,
                }[
                    label
                ]

                sample_weight = self.config[
                    "pointwise_weight"
                ]

                if label == "neutral":
                    sample_weight *= self.config[
                        "neutral_weight"
                    ]

                features = self.extractor.transform(
                    record[
                        "text"
                    ],
                    context=context,
                )

                if not features:
                    continue

                examples.append(
                    {
                        "source_record_id": record[
                            "preference_record_id"
                        ],
                        "example_type": "pointwise",
                        "features": features,
                        "target": target,
                        "weight": sample_weight,
                    }
                )

            elif record_type == "pairwise_preference":
                difference = self.extractor.pairwise_difference(
                    record[
                        "chosen"
                    ][
                        "text"
                    ],
                    record[
                        "rejected"
                    ][
                        "text"
                    ],
                    context=context,
                )

                if not difference:
                    continue

                pair_weight = self.config[
                    "pairwise_weight"
                ]

                examples.append(
                    {
                        "source_record_id": record[
                            "preference_record_id"
                        ],
                        "example_type": "pairwise",
                        "features": difference,
                        "target": 1.0,
                        "weight": pair_weight,
                    }
                )

                mirrored = {
                    index: -value
                    for index, value in difference.items()
                }

                examples.append(
                    {
                        "source_record_id": record[
                            "preference_record_id"
                        ],
                        "example_type": "pairwise_mirror",
                        "features": mirrored,
                        "target": 0.0,
                        "weight": pair_weight,
                    }
                )

        return examples

    def _example_loss(
        self,
        *,
        weights: Sequence[float],
        intercept: float,
        examples: Sequence[
            Mapping[str, Any]
        ],
    ) -> float:
        total = 0.0
        total_weight = 0.0

        for example in examples:
            logit = intercept

            for index, value in example[
                "features"
            ].items():
                logit += (
                    weights[
                        index
                    ]
                    * value
                )

            probability = _sigmoid(
                logit
            )

            sample_weight = float(
                example[
                    "weight"
                ]
            )

            total += (
                sample_weight
                * _log_loss(
                    probability,
                    float(
                        example[
                            "target"
                        ]
                    ),
                )
            )
            total_weight += sample_weight

        return (
            total
            / total_weight
            if total_weight > 0.0
            else 0.0
        )

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def _evaluate_records(
        self,
        scorer: LocalPreferenceScorer,
        records: Sequence[
            Mapping[str, Any]
        ],
    ) -> Dict[str, Any]:
        pointwise_count = 0
        pointwise_decisive_count = 0
        pointwise_correct = 0
        pointwise_loss = 0.0

        pairwise_count = 0
        pairwise_correct = 0
        pairwise_ties = 0
        pairwise_margin_sum = 0.0
        pairwise_loss = 0.0

        for record in records:
            if record[
                "record_type"
            ] in {
                "pointwise_preference",
                "quality_example",
            }:
                pointwise_count += 1

                probability = scorer.score_text(
                    record[
                        "text"
                    ],
                    context=record[
                        "context"
                    ],
                )

                target = {
                    "positive": 1.0,
                    "negative": 0.0,
                    "neutral": 0.5,
                }[
                    record[
                        "label"
                    ]
                ]

                pointwise_loss += _log_loss(
                    probability,
                    target,
                )

                if record[
                    "label"
                ] in {
                    "positive",
                    "negative",
                }:
                    pointwise_decisive_count += 1

                    predicted = (
                        "positive"
                        if probability >= 0.5
                        else "negative"
                    )

                    if predicted == record[
                        "label"
                    ]:
                        pointwise_correct += 1

            elif record[
                "record_type"
            ] == "pairwise_preference":
                pairwise_count += 1

                chosen_score = scorer.score_text(
                    record[
                        "chosen"
                    ][
                        "text"
                    ],
                    context=record[
                        "context"
                    ],
                )

                rejected_score = scorer.score_text(
                    record[
                        "rejected"
                    ][
                        "text"
                    ],
                    context=record[
                        "context"
                    ],
                )

                difference = (
                    chosen_score
                    - rejected_score
                )

                pairwise_margin_sum += (
                    difference
                )

                pair_probability = scorer.pairwise_probability(
                    chosen_text=record[
                        "chosen"
                    ][
                        "text"
                    ],
                    rejected_text=record[
                        "rejected"
                    ][
                        "text"
                    ],
                    context=record[
                        "context"
                    ],
                )

                pairwise_loss += _log_loss(
                    pair_probability,
                    1.0,
                )

                if abs(
                    difference
                ) < 1e-12:
                    pairwise_ties += 1

                elif difference > 0.0:
                    pairwise_correct += 1

        return {
            "record_count": len(
                records
            ),
            "pointwise": {
                "count": pointwise_count,
                "decisive_count": pointwise_decisive_count,
                "accuracy": (
                    round(
                        pointwise_correct
                        / pointwise_decisive_count,
                        6,
                    )
                    if pointwise_decisive_count
                    else None
                ),
                "mean_log_loss": (
                    round(
                        pointwise_loss
                        / pointwise_count,
                        6,
                    )
                    if pointwise_count
                    else None
                ),
            },
            "pairwise": {
                "count": pairwise_count,
                "accuracy": (
                    round(
                        pairwise_correct
                        / pairwise_count,
                        6,
                    )
                    if pairwise_count
                    else None
                ),
                "tie_rate": (
                    round(
                        pairwise_ties
                        / pairwise_count,
                        6,
                    )
                    if pairwise_count
                    else None
                ),
                "mean_chosen_minus_rejected_score": (
                    round(
                        pairwise_margin_sum
                        / pairwise_count,
                        6,
                    )
                    if pairwise_count
                    else None
                ),
                "mean_pairwise_log_loss": (
                    round(
                        pairwise_loss
                        / pairwise_count,
                        6,
                    )
                    if pairwise_count
                    else None
                ),
            },
        }

    # ------------------------------------------------------------------
    # Input normalization
    # ------------------------------------------------------------------

    def _normalize_records(
        self,
        records: Sequence[
            Mapping[str, Any]
        ],
    ) -> list[Dict[str, Any]]:
        if (
            not isinstance(
                records,
                Sequence,
            )
            or isinstance(
                records,
                (str, bytes),
            )
        ):
            raise PreferenceRankerTrainerValidationError(
                "records must be a sequence."
            )

        result = []
        seen = {}

        for index, record in enumerate(
            records
        ):
            normalized = self._normalize_record(
                record,
                index=index,
            )

            record_id = normalized[
                "preference_record_id"
            ]

            fingerprint = _sha256(
                _canonical_json_bytes(
                    normalized
                )
            )

            if record_id in seen:
                if seen[
                    record_id
                ] != fingerprint:
                    raise PreferenceRankerTrainerIntegrityError(
                        "Duplicate preference_record_id has different content: "
                        f"{record_id!r}."
                    )
                continue

            seen[
                record_id
            ] = fingerprint
            result.append(
                normalized
            )

        return result

    def _normalize_record(
        self,
        record: Mapping[str, Any],
        *,
        index: int,
    ) -> Dict[str, Any]:
        if not isinstance(
            record,
            Mapping,
        ):
            raise PreferenceRankerTrainerValidationError(
                f"records[{index}] must be an object."
            )

        prohibited = _contains_prohibited_field(
            record.get(
                "context"
            )
            or {}
        )

        if prohibited:
            raise PreferenceRankerTrainerValidationError(
                f"records[{index}].context contains prohibited field at "
                f"{prohibited!r}."
            )

        if bool(
            record.get(
                "is_grounding_evidence"
            )
        ):
            raise PreferenceRankerTrainerIntegrityError(
                "Preference training records cannot be grounding evidence."
            )

        metadata = record.get(
            "metadata"
        ) or {}

        if (
            metadata
            and not isinstance(
                metadata,
                Mapping,
            )
        ):
            raise PreferenceRankerTrainerValidationError(
                f"records[{index}].metadata must be an object."
            )

        if bool(
            metadata.get(
                "preference_data_is_grounding_evidence"
            )
        ):
            raise PreferenceRankerTrainerIntegrityError(
                "Preference training metadata cannot mark data as evidence."
            )

        private = _contains_prohibited_field(
            metadata
        )

        if private:
            raise PreferenceRankerTrainerValidationError(
                f"records[{index}].metadata contains prohibited field at "
                f"{private!r}."
            )

        record_id = _clean(
            record.get(
                "preference_record_id"
            )
        )

        if not record_id:
            raise PreferenceRankerTrainerValidationError(
                f"records[{index}] is missing preference_record_id."
            )

        record_type = _normalize_enum(
            record.get(
                "record_type"
            )
        )

        if record_type not in ALLOWED_RECORD_TYPES:
            raise PreferenceRankerTrainerValidationError(
                f"records[{index}] has unsupported record_type="
                f"{record_type!r}."
            )

        split = _normalize_enum(
            record.get(
                "dataset_split"
            )
        )

        if split not in ALLOWED_SPLITS:
            raise PreferenceRankerTrainerValidationError(
                f"records[{index}] has unsupported dataset_split={split!r}."
            )

        context = self.extractor._normalize_context(
            record.get(
                "context"
            )
            or {}
        )

        common = {
            "preference_record_id": record_id,
            "record_type": record_type,
            "dataset_split": split,
            "feedback_id": _clean(
                record.get(
                    "feedback_id"
                )
            ),
            "context": context,
            "record_sha256": _clean(
                record.get(
                    "record_sha256"
                )
            ),
            "is_grounding_evidence": False,
        }

        if record_type == "pairwise_preference":
            chosen = self._normalize_item(
                record.get(
                    "chosen"
                ),
                field_name=(
                    f"records[{index}].chosen"
                ),
            )

            rejected = self._normalize_item(
                record.get(
                    "rejected"
                ),
                field_name=(
                    f"records[{index}].rejected"
                ),
            )

            if (
                chosen[
                    "insight_id"
                ]
                and rejected[
                    "insight_id"
                ]
                and chosen[
                    "insight_id"
                ]
                == rejected[
                    "insight_id"
                ]
            ):
                raise PreferenceRankerTrainerValidationError(
                    "Pairwise chosen and rejected insight IDs must differ."
                )

            if (
                chosen["text"]
                == rejected["text"]
            ):
                raise PreferenceRankerTrainerValidationError(
                    "Pairwise chosen and rejected text must differ."
                )

            return {
                **common,
                "chosen": chosen,
                "rejected": rejected,
            }

        label = _normalize_enum(
            record.get(
                "label"
            )
        )

        if label not in ALLOWED_LABELS:
            raise PreferenceRankerTrainerValidationError(
                f"records[{index}] has unsupported label={label!r}."
            )

        text = _clean(
            record.get(
                "text"
            )
        )

        if not text:
            raise PreferenceRankerTrainerValidationError(
                f"records[{index}] is missing text."
            )

        return {
            **common,
            "label": label,
            "insight_id": _clean(
                record.get(
                    "insight_id"
                )
            ),
            "text": text,
        }

    def _normalize_item(
        self,
        item: Any,
        *,
        field_name: str,
    ) -> Dict[str, str]:
        if not isinstance(
            item,
            Mapping,
        ):
            raise PreferenceRankerTrainerValidationError(
                f"{field_name} must be an object."
            )

        text = _clean(
            item.get(
                "text"
            )
            or item.get(
                "insight"
            )
            or item.get(
                "statement"
            )
        )

        if not text:
            raise PreferenceRankerTrainerValidationError(
                f"{field_name} is missing text."
            )

        return {
            "insight_id": _clean(
                item.get(
                    "insight_id"
                )
                or item.get(
                    "id"
                )
            ),
            "insight_revision_id": _clean(
                item.get(
                    "insight_revision_id"
                )
            ),
            "text": text,
        }

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
                raise PreferenceRankerTrainerValidationError(
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
                raise PreferenceRankerTrainerValidationError(
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
            "feature_dimension",
            "ngram_min",
            "ngram_max",
            "epochs",
            "early_stopping_patience",
            "random_seed",
            "max_tokens_per_text",
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
                raise PreferenceRankerTrainerValidationError(
                    f"{field} must be an integer."
                ) from exc

        if result[
            "feature_dimension"
        ] <= 0:
            raise PreferenceRankerTrainerValidationError(
                "feature_dimension must be > 0."
            )

        if result[
            "ngram_min"
        ] <= 0:
            raise PreferenceRankerTrainerValidationError(
                "ngram_min must be >= 1."
            )

        if (
            result[
                "ngram_max"
            ]
            < result[
                "ngram_min"
            ]
        ):
            raise PreferenceRankerTrainerValidationError(
                "ngram_max must be >= ngram_min."
            )

        if result[
            "epochs"
        ] <= 0:
            raise PreferenceRankerTrainerValidationError(
                "epochs must be > 0."
            )

        if result[
            "early_stopping_patience"
        ] <= 0:
            raise PreferenceRankerTrainerValidationError(
                "early_stopping_patience must be > 0."
            )

        if result[
            "max_tokens_per_text"
        ] <= 0:
            raise PreferenceRankerTrainerValidationError(
                "max_tokens_per_text must be > 0."
            )

        for field in (
            "l2_regularization",
            "learning_rate",
            "learning_rate_decay",
            "minimum_improvement",
            "neutral_weight",
            "pairwise_weight",
            "pointwise_weight",
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
                raise PreferenceRankerTrainerValidationError(
                    f"{field} must be numeric."
                ) from exc

            if not math.isfinite(
                result[
                    field
                ]
            ):
                raise PreferenceRankerTrainerValidationError(
                    f"{field} must be finite."
                )

            if result[field] < 0.0:
                raise PreferenceRankerTrainerValidationError(
                    f"{field} cannot be negative."
                )

        if result[
            "learning_rate"
        ] <= 0.0:
            raise PreferenceRankerTrainerValidationError(
                "learning_rate must be > 0."
            )

        if result[
            "pairwise_weight"
        ] <= 0.0:
            raise PreferenceRankerTrainerValidationError(
                "pairwise_weight must be > 0."
            )

        if result[
            "pointwise_weight"
        ] <= 0.0:
            raise PreferenceRankerTrainerValidationError(
                "pointwise_weight must be > 0."
            )

        result[
            "lowercase"
        ] = bool(
            result[
                "lowercase"
            ]
        )
        result[
            "include_context_features"
        ] = bool(
            result[
                "include_context_features"
            ]
        )

        return result


# Compatibility aliases.
PreferenceTrainer = PreferenceRankerTrainer
PreferenceScorer = LocalPreferenceScorer