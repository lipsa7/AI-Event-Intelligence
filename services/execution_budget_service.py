"""
Global execution-budget authority for one research run.

Recommended location:
    services/execution_budget_service.py

Purpose
-------
A single research run must have ONE monotonic execution budget.

The service is designed to be injected into orchestration components such as
EvidenceDiscoveryAgent:

    budget.remaining_discretionary_seconds()

    decision = budget.authorize(
        action="web_search",
        expected_seconds=8.0,
        metadata={"query_id": "..."},
    )

    budget.record(
        action="web_search",
        elapsed_seconds=1.42,
        metadata={"query_id": "..."},
    )

The service enforces the difference between:

    discretionary research time
        and
    protected finalization time

Example profile compiled elsewhere:

    max_run_seconds = 300
    research_soft_deadline_seconds = 260
    reserve_finalization_seconds = 40

The first ~260 seconds may be used for normal research/refinement. The final
~40 seconds are protected for final reasoning, grounding, red-team, report,
audit and release work.

Critical architecture boundary
------------------------------
ExecutionBudgetService is an ENFORCEMENT service.

It does NOT decide:
- whether the user should use Fast or Deep mode;
- which research profile is appropriate;
- which model should be used;
- whether evidence is true;
- whether an insight is good;
- whether a report should be released.

PolicyRegistry / BlueprintCompiler decide the configured limits.
ExecutionBudgetService enforces the compiled limits.

Fast / Deep
-----------
There are no separate Fast/Deep code paths.

The exact same class handles:

    Fast: max_run_seconds ~= 300
    Deep: max_run_seconds ~= 1200

or any later profile, as long as the compiled budget policy satisfies the same
contract.

One monotonic timer
-------------------
Wall-clock timestamps are recorded only for audit/UI display.
Deadline decisions use monotonic time only.

This avoids problems caused by:
- system clock adjustments;
- timezone changes;
- daylight-saving changes.

Parallel work
-------------
`authorize()` RESERVES count-based quota before work starts.

This matters for parallel web-search/source-fetch batches. If five threads are
about to start, all five authorizations are counted immediately. The service
does not wait until completion before enforcing quotas.

`record()` then marks the reservation completed and records elapsed action time.

Cumulative action time may exceed wall-clock run time when actions run in
parallel. Therefore:

    cumulative_action_seconds
        !=
    run elapsed_seconds

The hard deadline always uses monotonic wall-clock run time.

Compatibility with EvidenceDiscoveryAgent
-----------------------------------------
The generated EvidenceDiscoveryAgent expects an injected object supporting:

    remaining_discretionary_seconds()
    authorize(action=..., expected_seconds=..., metadata=...)
    record(action=..., elapsed_seconds=..., metadata=...)

This service implements that contract directly.

Finalization reserve
--------------------
Discretionary operations are denied when protected finalization time would be
consumed.

Protected finalization actions/stages may continue after the research soft
deadline until the hard deadline.

By default the protected actions are:

    final_reasoning
    final_grounding
    red_team
    report_generation
    audit_receipt
    release_validation
    report_save

and protected stages are:

    finalization
    final_reasoning
    final_grounding
    red_team
    report
    audit
    release

These names are configurable through the compiled budget policy.

Count budgets
-------------
Common resource aliases:

    web_search                 -> search_queries
    source_fetch               -> sources_fetched
    evidence_discovery_round   -> search_rounds
    refinement_round           -> refinement_rounds

Custom exact action limits can also be configured.

Example:

{
    "action_limits": {
        "search_queries": 12,
        "sources_fetched": 18,
        "search_rounds": 3,
        "refinement_rounds": 1,
        "model_call": 20
    }
}

Reserved quota counts are used for authorization so concurrent work cannot
oversubscribe a limit.

Graceful degradation
--------------------
When budget is exhausted, `authorize()` returns a structured denial. It does not
lower evidence standards, alter grounding thresholds, or silently steal time
from finalization.

The orchestrator decides how to degrade:
- leave a gap unresolved;
- omit an unsupported insight;
- stop discovery;
- proceed to protected finalization.

Trace integration
-----------------
`trace_snapshot()` intentionally returns only fields accepted by
InsightTraceService.record_budget_snapshot().

`full_snapshot()` returns the complete local audit/debug state.

No private chain-of-thought is stored.
No paid API or external service is required.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Mapping, Optional, Sequence


SERVICE_VERSION = "execution-budget-service-v1.0"

RUN_STATES = {
    "active",
    "finalized",
    "cancelled",
}

DEFAULT_PROTECTED_FINALIZATION_ACTIONS = {
    "final_reasoning",
    "final_grounding",
    "red_team",
    "report_generation",
    "audit_receipt",
    "release_validation",
    "report_save",
}

DEFAULT_PROTECTED_FINALIZATION_STAGES = {
    "finalization",
    "final_reasoning",
    "final_grounding",
    "red_team",
    "report",
    "audit",
    "release",
}

DEFAULT_ACTION_RESOURCE_MAP = {
    "web_search": "search_queries",
    "source_fetch": "sources_fetched",
    "evidence_discovery_round": "search_rounds",
    "refinement_round": "refinement_rounds",
}

DEFAULT_POLICY = {
    "max_run_seconds": 300.0,
    "research_soft_deadline_seconds": 260.0,
    "reserve_finalization_seconds": 40.0,
    "minimum_start_seconds": 0.25,
    "action_limits": {},
    "protected_finalization_actions": sorted(
        DEFAULT_PROTECTED_FINALIZATION_ACTIONS
    ),
    "protected_finalization_stages": sorted(
        DEFAULT_PROTECTED_FINALIZATION_STAGES
    ),
    "action_resource_map": deepcopy(
        DEFAULT_ACTION_RESOURCE_MAP
    ),
}


class ExecutionBudgetError(RuntimeError):
    """Base execution-budget exception."""


class ExecutionBudgetValidationError(ExecutionBudgetError):
    """Raised when configuration/input violates the budget contract."""


class ExecutionBudgetIntegrityError(ExecutionBudgetError):
    """Raised when monotonic time or reservation state is inconsistent."""


class ExecutionBudgetStateError(ExecutionBudgetError):
    """Raised when an operation is attempted in an invalid run state."""


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


def _stable_unique_strings(values: Any) -> list[str]:
    if values is None:
        return []

    if isinstance(values, str):
        values = [values]

    if not isinstance(values, (list, tuple, set)):
        raise ExecutionBudgetValidationError(
            "Expected a string or sequence of strings."
        )

    result = []
    seen = set()

    for value in values:
        text = _normalize_enum(value)

        if text and text not in seen:
            seen.add(text)
            result.append(text)

    return result


def _optional_nonnegative_float(
    value: Any,
    *,
    field_name: str,
) -> Optional[float]:
    if value is None or value == "":
        return None

    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ExecutionBudgetValidationError(
            f"{field_name} must be numeric."
        ) from exc

    if not math.isfinite(numeric):
        raise ExecutionBudgetValidationError(
            f"{field_name} must be finite."
        )

    if numeric < 0.0:
        raise ExecutionBudgetValidationError(
            f"{field_name} must be >= 0."
        )

    return numeric


def _positive_int(
    value: Any,
    *,
    field_name: str,
) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError) as exc:
        raise ExecutionBudgetValidationError(
            f"{field_name} must be an integer."
        ) from exc

    if numeric < 0:
        raise ExecutionBudgetValidationError(
            f"{field_name} must be >= 0."
        )

    return numeric


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ExecutionBudgetService:
    """
    One global monotonic budget authority for one research run.

    Instantiate one service per orchestrated run and inject the SAME instance
    into all budget-aware agents/services.
    """

    def __init__(
        self,
        *,
        run_id: str,
        profile: str = "",
        budget_policy: Optional[Mapping[str, Any]] = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], str] = _utc_now_iso,
        auto_start: bool = True,
    ) -> None:
        run_id = _clean(run_id)

        if not run_id:
            raise ExecutionBudgetValidationError(
                "run_id cannot be empty."
            )

        if not callable(monotonic_clock):
            raise ExecutionBudgetValidationError(
                "monotonic_clock must be callable."
            )

        if not callable(wall_clock):
            raise ExecutionBudgetValidationError(
                "wall_clock must be callable."
            )

        self.run_id = run_id
        self.profile = _normalize_enum(profile)
        self.monotonic_clock = monotonic_clock
        self.wall_clock = wall_clock
        self.policy = self._normalize_policy(
            budget_policy
        )

        self._lock = threading.RLock()

        self._state = "active" if auto_start else ""
        self._started_monotonic: Optional[float] = None
        self._started_at = ""
        self._last_monotonic: Optional[float] = None
        self._finalized_at = ""
        self._stop_reason = ""

        self._authorization_sequence = 0
        self._record_sequence = 0

        # reservation_id -> reservation record
        self._reservations: Dict[str, Dict[str, Any]] = {}

        # ordered authorization decisions
        self._authorizations: list[Dict[str, Any]] = []

        # ordered execution records
        self._records: list[Dict[str, Any]] = []

        # count resource -> units authorized/reserved
        self._reserved_units: Dict[str, int] = {}

        # count resource -> units that were recorded/completed
        self._completed_units: Dict[str, int] = {}

        # action -> cumulative execution seconds.
        # May exceed wall-clock elapsed because work can run in parallel.
        self._action_seconds: Dict[str, float] = {}

        # stage -> cumulative action seconds.
        self._stage_seconds: Dict[str, float] = {}

        if auto_start:
            self.start()

    # ------------------------------------------------------------------
    # Construction from Blueprint
    # ------------------------------------------------------------------

    @classmethod
    def from_blueprint(
        cls,
        *,
        run_id: str,
        blueprint: Mapping[str, Any],
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], str] = _utc_now_iso,
        auto_start: bool = True,
    ) -> "ExecutionBudgetService":
        """
        Build from a compiled Blueprint without creating a Fast/Deep code branch.

        Recognized locations:
            blueprint.runtime_policy
            blueprint.runtime_policy.execution_budget
            blueprint.execution_budget_policy

        Explicit nested execution-budget values override runtime-policy values.

        Common fields:
            max_run_seconds
            research_soft_deadline_seconds
            reserve_finalization_seconds
            minimum_start_seconds
            action_limits
            protected_finalization_actions
            protected_finalization_stages
            action_resource_map

        `max_refinement_rounds` is mapped to action limit `refinement_rounds`
        when no explicit action limit exists.
        """
        if not isinstance(blueprint, Mapping):
            raise ExecutionBudgetValidationError(
                "blueprint must be an object."
            )

        runtime = blueprint.get(
            "runtime_policy"
        ) or {}

        if not isinstance(runtime, Mapping):
            raise ExecutionBudgetValidationError(
                "blueprint.runtime_policy must be an object."
            )

        nested = runtime.get(
            "execution_budget"
        ) or blueprint.get(
            "execution_budget_policy"
        ) or {}

        if not isinstance(nested, Mapping):
            raise ExecutionBudgetValidationError(
                "Compiled execution budget policy must be an object."
            )

        policy: Dict[str, Any] = {}

        for field in (
            "max_run_seconds",
            "research_soft_deadline_seconds",
            "reserve_finalization_seconds",
            "minimum_start_seconds",
            "action_limits",
            "protected_finalization_actions",
            "protected_finalization_stages",
            "action_resource_map",
        ):
            if field in runtime:
                policy[field] = deepcopy(
                    runtime[field]
                )

            if field in nested:
                policy[field] = deepcopy(
                    nested[field]
                )

        action_limits = dict(
            policy.get(
                "action_limits"
            )
            or {}
        )

        if (
            "max_refinement_rounds"
            in runtime
            and "refinement_rounds"
            not in action_limits
        ):
            action_limits[
                "refinement_rounds"
            ] = runtime[
                "max_refinement_rounds"
            ]

        policy[
            "action_limits"
        ] = action_limits

        profile = (
            _normalize_enum(
                runtime.get(
                    "research_profile"
                )
                or runtime.get(
                    "profile"
                )
                or blueprint.get(
                    "research_depth"
                )
                or blueprint.get(
                    "profile"
                )
            )
        )

        return cls(
            run_id=run_id,
            profile=profile,
            budget_policy=policy,
            monotonic_clock=monotonic_clock,
            wall_clock=wall_clock,
            auto_start=auto_start,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self._started_monotonic is not None:
                return self.full_snapshot()

            now = float(
                self.monotonic_clock()
            )

            if not math.isfinite(now):
                raise ExecutionBudgetIntegrityError(
                    "monotonic_clock returned a non-finite value."
                )

            self._started_monotonic = now
            self._last_monotonic = now
            self._started_at = self.wall_clock()
            self._state = "active"

            return self.full_snapshot()

    def finalize(
        self,
        *,
        stop_reason: str,
    ) -> Dict[str, Any]:
        stop_reason = _normalize_enum(
            stop_reason
        )

        if not stop_reason:
            raise ExecutionBudgetValidationError(
                "stop_reason cannot be empty."
            )

        with self._lock:
            self._require_started()

            if self._state == "cancelled":
                raise ExecutionBudgetStateError(
                    "Cancelled run cannot be finalized."
                )

            if self._state == "finalized":
                return self.full_snapshot()

            # Observe the clock one last time.
            self._elapsed_seconds_locked()

            self._state = "finalized"
            self._finalized_at = self.wall_clock()
            self._stop_reason = stop_reason

            return self.full_snapshot()

    def cancel(
        self,
        *,
        reason: str,
    ) -> Dict[str, Any]:
        reason = _normalize_enum(
            reason
        )

        if not reason:
            raise ExecutionBudgetValidationError(
                "Cancellation reason cannot be empty."
            )

        with self._lock:
            self._require_started()

            if self._state == "finalized":
                raise ExecutionBudgetStateError(
                    "Finalized run cannot be cancelled."
                )

            if self._state == "cancelled":
                return self.full_snapshot()

            self._elapsed_seconds_locked()

            self._state = "cancelled"
            self._finalized_at = self.wall_clock()
            self._stop_reason = reason

            return self.full_snapshot()

    # ------------------------------------------------------------------
    # Remaining time
    # ------------------------------------------------------------------

    def elapsed_seconds(self) -> float:
        with self._lock:
            return self._elapsed_seconds_locked()

    def remaining_total_seconds(self) -> float:
        with self._lock:
            elapsed = self._elapsed_seconds_locked()

            return max(
                0.0,
                self.policy[
                    "max_run_seconds"
                ] - elapsed,
            )

    def remaining_discretionary_seconds(self) -> float:
        """
        Remaining time available to normal research/refinement work.

        This is the method expected by EvidenceDiscoveryAgent.
        """
        with self._lock:
            elapsed = self._elapsed_seconds_locked()

            return self._remaining_discretionary_locked(
                elapsed
            )

    def remaining_finalization_seconds(self) -> float:
        """
        Remaining hard-deadline time available to protected finalization work.

        Before the soft deadline this can be larger than the configured reserve,
        because finalization is allowed to start early.
        """
        with self._lock:
            elapsed = self._elapsed_seconds_locked()

            return max(
                0.0,
                self.policy[
                    "max_run_seconds"
                ] - elapsed,
            )

    def hard_deadline_reached(self) -> bool:
        return (
            self.remaining_total_seconds()
            <= 0.0
        )

    def soft_deadline_reached(self) -> bool:
        return (
            self.remaining_discretionary_seconds()
            <= 0.0
        )

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    def authorize(
        self,
        *,
        action: str,
        expected_seconds: Optional[float] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        stage: str = "",
        requested_units: int = 1,
        resource: str = "",
        reservation_key: str = "",
        budget_class: str = "",
    ) -> Dict[str, Any]:
        """
        Authorize and reserve quota BEFORE an operation starts.

        Returned decision:
        {
            "allowed": True/False,
            "reason": "...",
            "reservation_id": "...",
            "budget_class": "discretionary|finalization",
            "remaining_total_seconds": ...,
            "remaining_discretionary_seconds": ...,
            "timeout_seconds": ...
        }

        The method is thread-safe and reserves count quota immediately.
        """
        action = _normalize_enum(
            action
        )
        stage = _normalize_enum(
            stage
        )
        resource = _normalize_enum(
            resource
        )
        budget_class = _normalize_enum(
            budget_class
        )
        reservation_key = _clean(
            reservation_key
        )

        if not action:
            raise ExecutionBudgetValidationError(
                "action cannot be empty."
            )

        if metadata is None:
            metadata = {}

        if not isinstance(metadata, Mapping):
            raise ExecutionBudgetValidationError(
                "metadata must be an object."
            )

        expected = _optional_nonnegative_float(
            expected_seconds,
            field_name="expected_seconds",
        )

        units = _positive_int(
            requested_units,
            field_name="requested_units",
        )

        if units == 0:
            raise ExecutionBudgetValidationError(
                "requested_units must be >= 1."
            )

        if budget_class and budget_class not in {
            "discretionary",
            "finalization",
        }:
            raise ExecutionBudgetValidationError(
                "budget_class must be discretionary or finalization."
            )

        with self._lock:
            self._require_active()

            elapsed = self._elapsed_seconds_locked()
            remaining_total = max(
                0.0,
                self.policy[
                    "max_run_seconds"
                ] - elapsed,
            )
            remaining_discretionary = (
                self._remaining_discretionary_locked(
                    elapsed
                )
            )

            effective_budget_class = (
                budget_class
                or self._classify_budget(
                    action=action,
                    stage=stage,
                    metadata=metadata,
                )
            )

            effective_resource = (
                resource
                or self.policy[
                    "action_resource_map"
                ].get(
                    action,
                    action,
                )
            )

            reservation_identity = (
                reservation_key
                or self._authorization_identity(
                    action=action,
                    stage=stage,
                    metadata=metadata,
                )
            )

            # Exact replay of the same authorization identity is idempotent.
            existing = self._find_exact_reservation_by_identity(
                action=action,
                reservation_identity=reservation_identity,
            )

            if existing is not None:
                return deepcopy(
                    existing[
                        "decision"
                    ]
                )

            denial_reason = ""

            if remaining_total <= 0.0:
                denial_reason = "hard_deadline_exhausted"

            elif (
                effective_budget_class == "discretionary"
                and remaining_discretionary
                < self.policy[
                    "minimum_start_seconds"
                ]
            ):
                denial_reason = "discretionary_budget_exhausted"

            elif (
                effective_budget_class == "finalization"
                and remaining_total
                < self.policy[
                    "minimum_start_seconds"
                ]
            ):
                denial_reason = "finalization_budget_exhausted"

            limit = self.policy[
                "action_limits"
            ].get(
                effective_resource
            )

            if not denial_reason and limit is not None:
                already_reserved = int(
                    self._reserved_units.get(
                        effective_resource,
                        0,
                    )
                )

                if (
                    already_reserved
                    + units
                    > limit
                ):
                    denial_reason = (
                        "action_limit_exhausted:"
                        + effective_resource
                    )

            available_time = (
                remaining_total
                if effective_budget_class == "finalization"
                else remaining_discretionary
            )

            if not denial_reason and expected is not None:
                # Expected time is an estimate/timeout bound, not a reservation
                # of wall-clock seconds. Deny only when there is effectively no
                # time to start; the caller can use timeout_seconds to bound work.
                if available_time < self.policy[
                    "minimum_start_seconds"
                ]:
                    denial_reason = "insufficient_time_to_start"

            allowed = not bool(
                denial_reason
            )

            self._authorization_sequence += 1

            reservation_id = _stable_id(
                "BUDGETRES",
                {
                    "run_id": self.run_id,
                    "sequence": self._authorization_sequence,
                    "action": action,
                    "stage": stage,
                    "resource": effective_resource,
                    "reservation_identity": reservation_identity,
                    "metadata": dict(metadata),
                },
            )

            timeout_seconds = max(
                0.0,
                available_time,
            )

            if expected is not None:
                timeout_seconds = min(
                    timeout_seconds,
                    expected,
                )

            decision = {
                "authorization_id": _stable_id(
                    "BUDGETAUTH",
                    {
                        "run_id": self.run_id,
                        "sequence": self._authorization_sequence,
                        "reservation_id": reservation_id,
                        "allowed": allowed,
                        "reason": denial_reason,
                    },
                ),
                "allowed": allowed,
                "reason": denial_reason,
                "reservation_id": reservation_id if allowed else "",
                "action": action,
                "stage": stage,
                "resource": effective_resource,
                "requested_units": units,
                "budget_class": effective_budget_class,
                "expected_seconds": expected,
                "timeout_seconds": timeout_seconds if allowed else 0.0,
                "elapsed_seconds": elapsed,
                "remaining_total_seconds": remaining_total,
                "remaining_discretionary_seconds": (
                    remaining_discretionary
                ),
                "reserved_finalization_seconds": self.policy[
                    "reserve_finalization_seconds"
                ],
                "profile": self.profile,
                "timestamp": self.wall_clock(),
            }

            self._authorizations.append(
                deepcopy(decision)
            )

            if allowed:
                self._reserved_units[
                    effective_resource
                ] = (
                    int(
                        self._reserved_units.get(
                            effective_resource,
                            0,
                        )
                    )
                    + units
                )

                self._reservations[
                    reservation_id
                ] = {
                    "reservation_id": reservation_id,
                    "reservation_identity": reservation_identity,
                    "action": action,
                    "stage": stage,
                    "resource": effective_resource,
                    "requested_units": units,
                    "metadata": deepcopy(
                        dict(metadata)
                    ),
                    "decision": deepcopy(
                        decision
                    ),
                    "status": "reserved",
                    "record_ids": [],
                }

            return deepcopy(decision)

    # ------------------------------------------------------------------
    # Recording completed/attempted work
    # ------------------------------------------------------------------

    def record(
        self,
        *,
        action: str,
        elapsed_seconds: float,
        metadata: Optional[Mapping[str, Any]] = None,
        stage: str = "",
        reservation_id: str = "",
        units_completed: Optional[int] = None,
        status: str = "completed",
    ) -> Dict[str, Any]:
        """
        Record one completed/failed/cancelled operation.

        Current EvidenceDiscoveryAgent does not pass reservation_id. In that
        case this method matches the earliest compatible uncompleted reservation
        using action + overlapping metadata (for example query_id).

        This supports parallel source-fetches where authorize() contained URL
        while record() contains only discovery_id/query_id.
        """
        action = _normalize_enum(
            action
        )
        stage = _normalize_enum(
            stage
        )
        reservation_id = _clean(
            reservation_id
        )
        status = _normalize_enum(
            status
        )

        if not action:
            raise ExecutionBudgetValidationError(
                "action cannot be empty."
            )

        elapsed = _optional_nonnegative_float(
            elapsed_seconds,
            field_name="elapsed_seconds",
        )

        assert elapsed is not None

        if metadata is None:
            metadata = {}

        if not isinstance(metadata, Mapping):
            raise ExecutionBudgetValidationError(
                "metadata must be an object."
            )

        if status not in {
            "completed",
            "failed",
            "cancelled",
            "timed_out",
        }:
            raise ExecutionBudgetValidationError(
                "status must be completed, failed, cancelled or timed_out."
            )

        with self._lock:
            self._require_started()

            if self._state not in {
                "active",
                "finalized",
                "cancelled",
            }:
                raise ExecutionBudgetStateError(
                    "Run is not in a recordable state."
                )

            # Observe clock for consistency but do not require active state:
            # a worker may finish just after the orchestrator reaches deadline.
            run_elapsed = self._elapsed_seconds_locked()

            reservation = None

            if reservation_id:
                reservation = self._reservations.get(
                    reservation_id
                )

                if reservation is None:
                    raise ExecutionBudgetIntegrityError(
                        f"Unknown reservation_id={reservation_id!r}."
                    )

                if reservation[
                    "action"
                ] != action:
                    raise ExecutionBudgetIntegrityError(
                        "Reservation action does not match record action."
                    )

            else:
                reservation = self._match_reservation(
                    action=action,
                    stage=stage,
                    metadata=metadata,
                )

            if reservation is None:
                raise ExecutionBudgetIntegrityError(
                    "No prior authorization reservation matches this record. "
                    "Budgeted work must be authorized before execution."
                )

            if reservation[
                "status"
            ] == "released":
                raise ExecutionBudgetIntegrityError(
                    "Cannot record work against a released reservation."
                )

            max_units = int(
                reservation[
                    "requested_units"
                ]
            )

            if units_completed is None:
                units = max_units
            else:
                units = _positive_int(
                    units_completed,
                    field_name="units_completed",
                )

                if units == 0:
                    raise ExecutionBudgetValidationError(
                        "units_completed must be >= 1."
                    )

            if units > max_units:
                raise ExecutionBudgetIntegrityError(
                    "units_completed exceeds reserved units."
                )

            # A reservation can be recorded once. This is intentional for the
            # current action-level contract. Callers needing partial-unit records
            # should create separate reservations.
            if reservation[
                "status"
            ] == "recorded":
                # Idempotent replay only when same action/metadata/elapsed/status.
                existing_record = self._records[
                    reservation[
                        "record_ids"
                    ][0]
                ] if False else None

                # We store record IDs, not list indexes. Resolve below.
                record_id = reservation[
                    "record_ids"
                ][0]

                for existing in self._records:
                    if existing[
                        "record_id"
                    ] == record_id:
                        replay_payload = {
                            "action": action,
                            "stage": stage,
                            "elapsed_seconds": elapsed,
                            "metadata": deepcopy(
                                dict(metadata)
                            ),
                            "status": status,
                            "units_completed": units,
                        }

                        prior_payload = {
                            "action": existing[
                                "action"
                            ],
                            "stage": existing[
                                "stage"
                            ],
                            "elapsed_seconds": existing[
                                "elapsed_seconds"
                            ],
                            "metadata": existing[
                                "metadata"
                            ],
                            "status": existing[
                                "status"
                            ],
                            "units_completed": existing[
                                "units_completed"
                            ],
                        }

                        if (
                            _canonical_json_bytes(
                                replay_payload
                            )
                            != _canonical_json_bytes(
                                prior_payload
                            )
                        ):
                            raise ExecutionBudgetIntegrityError(
                                "Reservation was already recorded with different "
                                "completion data."
                            )

                        return deepcopy(
                            existing
                        )

                raise ExecutionBudgetIntegrityError(
                    "Reservation references a missing execution record."
                )

            self._record_sequence += 1

            effective_stage = (
                stage
                or reservation[
                    "stage"
                ]
            )

            record_id = _stable_id(
                "BUDGETREC",
                {
                    "run_id": self.run_id,
                    "sequence": self._record_sequence,
                    "reservation_id": reservation[
                        "reservation_id"
                    ],
                    "action": action,
                },
            )

            record = {
                "record_id": record_id,
                "reservation_id": reservation[
                    "reservation_id"
                ],
                "action": action,
                "stage": effective_stage,
                "resource": reservation[
                    "resource"
                ],
                "status": status,
                "units_completed": units,
                "elapsed_seconds": elapsed,
                "run_elapsed_seconds_at_record": run_elapsed,
                "metadata": deepcopy(
                    dict(metadata)
                ),
                "timestamp": self.wall_clock(),
            }

            self._records.append(
                record
            )

            reservation[
                "status"
            ] = "recorded"
            reservation[
                "record_ids"
            ].append(
                record_id
            )

            resource = reservation[
                "resource"
            ]

            self._completed_units[
                resource
            ] = (
                int(
                    self._completed_units.get(
                        resource,
                        0,
                    )
                )
                + units
            )

            self._action_seconds[
                action
            ] = round(
                float(
                    self._action_seconds.get(
                        action,
                        0.0,
                    )
                )
                + elapsed,
                9,
            )

            if effective_stage:
                self._stage_seconds[
                    effective_stage
                ] = round(
                    float(
                        self._stage_seconds.get(
                            effective_stage,
                            0.0,
                        )
                    )
                    + elapsed,
                    9,
                )

            return deepcopy(record)

    def release_reservation(
        self,
        reservation_id: str,
        *,
        reason: str,
    ) -> Dict[str, Any]:
        """
        Release count quota for work that was authorized but never started.

        This should NOT be used after an operation actually consumed external
        work; record it as failed/cancelled instead.
        """
        reservation_id = _clean(
            reservation_id
        )
        reason = _normalize_enum(
            reason
        )

        if not reservation_id:
            raise ExecutionBudgetValidationError(
                "reservation_id cannot be empty."
            )

        if not reason:
            raise ExecutionBudgetValidationError(
                "release reason cannot be empty."
            )

        with self._lock:
            reservation = self._reservations.get(
                reservation_id
            )

            if reservation is None:
                raise ExecutionBudgetValidationError(
                    f"Unknown reservation_id={reservation_id!r}."
                )

            if reservation[
                "status"
            ] == "recorded":
                raise ExecutionBudgetStateError(
                    "Recorded reservation cannot be released."
                )

            if reservation[
                "status"
            ] == "released":
                return deepcopy(
                    reservation
                )

            resource = reservation[
                "resource"
            ]
            units = int(
                reservation[
                    "requested_units"
                ]
            )

            current = int(
                self._reserved_units.get(
                    resource,
                    0,
                )
            )

            if current < units:
                raise ExecutionBudgetIntegrityError(
                    "Reserved-unit counter is inconsistent."
                )

            self._reserved_units[
                resource
            ] = (
                current
                - units
            )

            reservation[
                "status"
            ] = "released"
            reservation[
                "release_reason"
            ] = reason
            reservation[
                "released_at"
            ] = self.wall_clock()

            return deepcopy(
                reservation
            )

    # ------------------------------------------------------------------
    # Snapshots / usage
    # ------------------------------------------------------------------

    def trace_snapshot(
        self,
        *,
        stage: str = "",
        action: str = "",
        authorized: Optional[bool] = None,
        denial_reason: str = "",
    ) -> Dict[str, Any]:
        """
        Return exactly the budget fields accepted by
        InsightTraceService.record_budget_snapshot().
        """
        stage = _normalize_enum(
            stage
        )
        action = _normalize_enum(
            action
        )
        denial_reason = _normalize_enum(
            denial_reason
        )

        with self._lock:
            elapsed = self._elapsed_seconds_locked()

            return {
                "stage": stage,
                "action": action,
                "elapsed_seconds": elapsed,
                "remaining_total_seconds": max(
                    0.0,
                    self.policy[
                        "max_run_seconds"
                    ] - elapsed,
                ),
                "remaining_discretionary_seconds": (
                    self._remaining_discretionary_locked(
                        elapsed
                    )
                ),
                "reserved_finalization_seconds": self.policy[
                    "reserve_finalization_seconds"
                ],
                "search_queries_used": int(
                    self._reserved_units.get(
                        "search_queries",
                        0,
                    )
                ),
                "sources_fetched": int(
                    self._reserved_units.get(
                        "sources_fetched",
                        0,
                    )
                ),
                "refinement_rounds_used": int(
                    self._reserved_units.get(
                        "refinement_rounds",
                        0,
                    )
                ),
                "authorized": authorized,
                "denial_reason": denial_reason,
                "profile": self.profile,
                "timestamp": self.wall_clock(),
            }

    def usage_summary(self) -> Dict[str, Any]:
        with self._lock:
            elapsed = self._elapsed_seconds_locked()

            return {
                "run_id": self.run_id,
                "state": self._state,
                "profile": self.profile,
                "elapsed_seconds": elapsed,
                "remaining_total_seconds": max(
                    0.0,
                    self.policy[
                        "max_run_seconds"
                    ] - elapsed,
                ),
                "remaining_discretionary_seconds": (
                    self._remaining_discretionary_locked(
                        elapsed
                    )
                ),
                "reserved_units": deepcopy(
                    self._reserved_units
                ),
                "completed_units": deepcopy(
                    self._completed_units
                ),
                "action_seconds": deepcopy(
                    self._action_seconds
                ),
                "stage_seconds": deepcopy(
                    self._stage_seconds
                ),
                "authorization_count": len(
                    self._authorizations
                ),
                "execution_record_count": len(
                    self._records
                ),
                "outstanding_reservation_count": sum(
                    1
                    for reservation
                    in self._reservations.values()
                    if reservation[
                        "status"
                    ] == "reserved"
                ),
                "cumulative_action_seconds": round(
                    sum(
                        self._action_seconds.values()
                    ),
                    9,
                ),
                "cumulative_action_seconds_may_exceed_wall_clock": True,
            }

    def full_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            elapsed = (
                self._elapsed_seconds_locked()
                if self._started_monotonic is not None
                else 0.0
            )

            payload = {
                "service_version": SERVICE_VERSION,
                "run_id": self.run_id,
                "profile": self.profile,
                "state": self._state or "not_started",
                "started_at": self._started_at,
                "finalized_at": self._finalized_at,
                "stop_reason": self._stop_reason,
                "policy": deepcopy(
                    self.policy
                ),
                "elapsed_seconds": elapsed,
                "remaining_total_seconds": (
                    max(
                        0.0,
                        self.policy[
                            "max_run_seconds"
                        ] - elapsed,
                    )
                    if self._started_monotonic is not None
                    else self.policy[
                        "max_run_seconds"
                    ]
                ),
                "remaining_discretionary_seconds": (
                    self._remaining_discretionary_locked(
                        elapsed
                    )
                    if self._started_monotonic is not None
                    else self.policy[
                        "research_soft_deadline_seconds"
                    ]
                ),
                "reserved_units": deepcopy(
                    self._reserved_units
                ),
                "completed_units": deepcopy(
                    self._completed_units
                ),
                "action_seconds": deepcopy(
                    self._action_seconds
                ),
                "stage_seconds": deepcopy(
                    self._stage_seconds
                ),
                "authorizations": deepcopy(
                    self._authorizations
                ),
                "reservations": deepcopy(
                    self._reservations
                ),
                "execution_records": deepcopy(
                    self._records
                ),
                "metadata": {
                    "uses_monotonic_deadline": True,
                    "single_global_run_budget": True,
                    "protected_finalization_reserve": True,
                    "parallel_quota_reserved_at_authorization": True,
                    "cumulative_action_seconds_may_exceed_wall_clock": True,
                    "authorizes_release": False,
                    "modifies_policy": False,
                    "chooses_research_profile": False,
                    "private_chain_of_thought_stored": False,
                },
            }

            payload[
                "snapshot_sha256"
            ] = _sha256(
                _canonical_json_bytes(
                    {
                        key: value
                        for key, value
                        in payload.items()
                        if key != "snapshot_sha256"
                    }
                )
            )

            return payload

    # ------------------------------------------------------------------
    # Internals: policy
    # ------------------------------------------------------------------

    def _normalize_policy(
        self,
        policy: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        result = deepcopy(
            DEFAULT_POLICY
        )

        if policy is not None:
            if not isinstance(policy, Mapping):
                raise ExecutionBudgetValidationError(
                    "budget_policy must be an object."
                )

            unknown = (
                set(policy)
                - set(DEFAULT_POLICY)
            )

            if unknown:
                raise ExecutionBudgetValidationError(
                    "Unknown budget policy fields: "
                    + ", ".join(
                        sorted(unknown)
                    )
                )

            for key, value in policy.items():
                result[key] = deepcopy(value)

        for field in (
            "max_run_seconds",
            "research_soft_deadline_seconds",
            "reserve_finalization_seconds",
            "minimum_start_seconds",
        ):
            numeric = _optional_nonnegative_float(
                result[field],
                field_name=field,
            )

            assert numeric is not None
            result[field] = numeric

        if result["max_run_seconds"] <= 0.0:
            raise ExecutionBudgetValidationError(
                "max_run_seconds must be > 0."
            )

        if (
            result[
                "reserve_finalization_seconds"
            ]
            >= result[
                "max_run_seconds"
            ]
        ):
            raise ExecutionBudgetValidationError(
                "reserve_finalization_seconds must be smaller than "
                "max_run_seconds."
            )

        max_discretionary_boundary = (
            result[
                "max_run_seconds"
            ]
            - result[
                "reserve_finalization_seconds"
            ]
        )

        if (
            result[
                "research_soft_deadline_seconds"
            ]
            > max_discretionary_boundary
            + 1e-9
        ):
            raise ExecutionBudgetValidationError(
                "research_soft_deadline_seconds cannot extend into the "
                "protected finalization reserve."
            )

        if (
            result[
                "minimum_start_seconds"
            ]
            <= 0.0
        ):
            raise ExecutionBudgetValidationError(
                "minimum_start_seconds must be > 0."
            )

        raw_limits = result.get(
            "action_limits"
        ) or {}

        if not isinstance(raw_limits, Mapping):
            raise ExecutionBudgetValidationError(
                "action_limits must be an object."
            )

        normalized_limits = {}

        for key, value in raw_limits.items():
            normalized_key = _normalize_enum(
                key
            )

            if not normalized_key:
                raise ExecutionBudgetValidationError(
                    "action_limits contains an empty key."
                )

            normalized_limits[
                normalized_key
            ] = _positive_int(
                value,
                field_name=(
                    "action_limits."
                    + normalized_key
                ),
            )

        result[
            "action_limits"
        ] = normalized_limits

        result[
            "protected_finalization_actions"
        ] = _stable_unique_strings(
            result.get(
                "protected_finalization_actions"
            )
            or []
        )

        result[
            "protected_finalization_stages"
        ] = _stable_unique_strings(
            result.get(
                "protected_finalization_stages"
            )
            or []
        )

        raw_map = result.get(
            "action_resource_map"
        ) or {}

        if not isinstance(raw_map, Mapping):
            raise ExecutionBudgetValidationError(
                "action_resource_map must be an object."
            )

        normalized_map = {}

        for action, resource in raw_map.items():
            normalized_action = _normalize_enum(
                action
            )
            normalized_resource = _normalize_enum(
                resource
            )

            if (
                not normalized_action
                or not normalized_resource
            ):
                raise ExecutionBudgetValidationError(
                    "action_resource_map keys/values cannot be empty."
                )

            normalized_map[
                normalized_action
            ] = normalized_resource

        result[
            "action_resource_map"
        ] = normalized_map

        return result

    # ------------------------------------------------------------------
    # Internals: time
    # ------------------------------------------------------------------

    def _require_started(self) -> None:
        if self._started_monotonic is None:
            raise ExecutionBudgetStateError(
                "Execution budget has not been started."
            )

    def _require_active(self) -> None:
        self._require_started()

        if self._state != "active":
            raise ExecutionBudgetStateError(
                f"Run is not active; current state={self._state!r}."
            )

    def _elapsed_seconds_locked(self) -> float:
        self._require_started()

        now = float(
            self.monotonic_clock()
        )

        if not math.isfinite(now):
            raise ExecutionBudgetIntegrityError(
                "monotonic_clock returned a non-finite value."
            )

        assert self._started_monotonic is not None

        if (
            self._last_monotonic is not None
            and now
            < self._last_monotonic
            - 1e-12
        ):
            raise ExecutionBudgetIntegrityError(
                "monotonic clock moved backwards."
            )

        self._last_monotonic = now

        elapsed = (
            now
            - self._started_monotonic
        )

        if elapsed < -1e-12:
            raise ExecutionBudgetIntegrityError(
                "Computed negative run elapsed time."
            )

        return max(
            0.0,
            elapsed,
        )

    def _remaining_discretionary_locked(
        self,
        elapsed: float,
    ) -> float:
        soft_remaining = (
            self.policy[
                "research_soft_deadline_seconds"
            ]
            - elapsed
        )

        hard_minus_reserve = (
            self.policy[
                "max_run_seconds"
            ]
            - elapsed
            - self.policy[
                "reserve_finalization_seconds"
            ]
        )

        return max(
            0.0,
            min(
                soft_remaining,
                hard_minus_reserve,
            ),
        )

    # ------------------------------------------------------------------
    # Internals: authorization/reservation matching
    # ------------------------------------------------------------------

    def _classify_budget(
        self,
        *,
        action: str,
        stage: str,
        metadata: Mapping[str, Any],
    ) -> str:
        explicit = _normalize_enum(
            metadata.get(
                "budget_class"
            )
        )

        if explicit:
            if explicit not in {
                "discretionary",
                "finalization",
            }:
                raise ExecutionBudgetValidationError(
                    "metadata.budget_class must be discretionary or finalization."
                )

            return explicit

        if (
            action
            in set(
                self.policy[
                    "protected_finalization_actions"
                ]
            )
        ):
            return "finalization"

        if (
            stage
            and stage
            in set(
                self.policy[
                    "protected_finalization_stages"
                ]
            )
        ):
            return "finalization"

        metadata_stage = _normalize_enum(
            metadata.get(
                "stage"
            )
        )

        if (
            metadata_stage
            and metadata_stage
            in set(
                self.policy[
                    "protected_finalization_stages"
                ]
            )
        ):
            return "finalization"

        return "discretionary"

    def _authorization_identity(
        self,
        *,
        action: str,
        stage: str,
        metadata: Mapping[str, Any],
    ) -> str:
        """
        Create a strong reservation identity.

        URL/source/result identifiers are retained when present so parallel
        source_fetch operations from one query reserve separately.
        """
        preferred_keys = (
            "reservation_key",
            "query_id",
            "source_id",
            "result_id",
            "url",
            "round",
            "round_index",
            "insight_id",
            "discovery_id",
            "cluster_id",
            "report_id",
        )

        identity_metadata = {
            key: _json_safe(
                metadata[key]
            )
            for key in preferred_keys
            if key in metadata
            and metadata[key] not in {
                None,
                "",
            }
        }

        if not identity_metadata:
            identity_metadata = _json_safe(
                dict(metadata)
            )

        return _sha256(
            _canonical_json_bytes(
                {
                    "action": action,
                    "stage": stage,
                    "metadata": identity_metadata,
                }
            )
        )

    def _find_exact_reservation_by_identity(
        self,
        *,
        action: str,
        reservation_identity: str,
    ) -> Optional[Dict[str, Any]]:
        for reservation in self._reservations.values():
            if (
                reservation[
                    "action"
                ] == action
                and reservation[
                    "reservation_identity"
                ] == reservation_identity
                and reservation[
                    "status"
                ] != "released"
            ):
                return reservation

        return None

    def _match_reservation(
        self,
        *,
        action: str,
        stage: str,
        metadata: Mapping[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Match the earliest compatible outstanding reservation.

        A record may contain fewer metadata fields than authorization. Every
        non-empty identifying field in the record must agree with the
        reservation, but authorization is allowed to contain extra fields.

        Example:
            authorize source_fetch:
                {query_id: Q1, url: URL-A}

            record source_fetch:
                {query_id: Q1}

        The first outstanding Q1 source_fetch reservation is matched.
        """
        candidates = []

        for reservation in self._reservations.values():
            if reservation[
                "action"
            ] != action:
                continue

            if reservation[
                "status"
            ] != "reserved":
                continue

            if (
                stage
                and reservation[
                    "stage"
                ]
                and stage
                != reservation[
                    "stage"
                ]
            ):
                continue

            if not self._metadata_is_compatible(
                record_metadata=metadata,
                reservation_metadata=reservation[
                    "metadata"
                ],
            ):
                continue

            candidates.append(
                reservation
            )

        if not candidates:
            return None

        # Reservation IDs encode authorization sequence through their source
        # payload only indirectly, so use authorization timestamp/order from
        # stored decision list.
        order = {
            item[
                "reservation_id"
            ]: index
            for index, item
            in enumerate(
                self._authorizations
            )
            if item.get(
                "reservation_id"
            )
        }

        candidates.sort(
            key=lambda item: order.get(
                item[
                    "reservation_id"
                ],
                10**12,
            )
        )

        return candidates[0]

    def _metadata_is_compatible(
        self,
        *,
        record_metadata: Mapping[str, Any],
        reservation_metadata: Mapping[str, Any],
    ) -> bool:
        # Compare stable identifier-like fields when the recorder supplies them.
        identity_keys = (
            "reservation_key",
            "query_id",
            "source_id",
            "result_id",
            "url",
            "round",
            "round_index",
            "insight_id",
            "discovery_id",
            "cluster_id",
            "report_id",
        )

        compared = False

        for key in identity_keys:
            if (
                key not in record_metadata
                or record_metadata[
                    key
                ] in {
                    None,
                    "",
                }
            ):
                continue

            compared = True

            if key not in reservation_metadata:
                # Record knows an identity field that authorization did not.
                return False

            if (
                _json_safe(
                    record_metadata[
                        key
                    ]
                )
                != _json_safe(
                    reservation_metadata[
                        key
                    ]
                )
            ):
                return False

        if compared:
            return True

        # If no stable identity key exists, require exact JSON-safe metadata.
        return (
            _canonical_json_bytes(
                _json_safe(
                    dict(record_metadata)
                )
            )
            == _canonical_json_bytes(
                _json_safe(
                    dict(reservation_metadata)
                )
            )
        )


# Compatibility alias: some orchestrators may prefer the shorter name.
ExecutionBudget = ExecutionBudgetService