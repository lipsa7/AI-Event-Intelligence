"""
Product metrics for the AI Event Intelligence product.

This module computes product-value metrics from telemetry events.

Responsibilities
----------------
- Define the product metric contract.
- Compute activation, value, trust, engagement, retention, and efficiency metrics.
- Keep product-value metrics separate from offline AI evaluation metrics.

Non-responsibilities
--------------------
This module does NOT:
- write telemetry;
- call an LLM;
- perform grounding/evaluation;
- decide whether an insight is factually supported;
- authorize report release;
- persist user profiles.

TelemetryService should collect the raw events. This module consumes those
events and deterministically computes aggregate product metrics.

Recommended north-star metric
-----------------------------
    useful_research_reports_per_active_user

A report is useful when it is completed and either:
1. receives an explicit useful/positive signal; or
2. receives a configured high-intent behavior signal such as save/copy/share,
   evidence-trail engagement, or a follow-up research action.

Important:
    "reports generated" is an activity metric, not a value metric.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from statistics import median
from typing import Any, Iterable, Mapping, Optional, Sequence


METRICS_VERSION = "product-metrics-v1.0"


class ProductMetricsError(ValueError):
    """Raised when product-metric inputs or configuration are invalid."""


def _clean(value: Any) -> str:
    if value is None:
        return ""

    return " ".join(str(value).split()).strip()


def _enum(value: Any) -> str:
    return (
        _clean(value)
        .casefold()
        .replace("-", "_")
        .replace(" ", "_")
    )


def _as_bool(value: Any) -> Optional[bool]:
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, int) and value in {0, 1}:
        return bool(value)

    if isinstance(value, str):
        normalized = value.strip().casefold()

        if normalized in {
            "true",
            "yes",
            "1",
            "y",
        }:
            return True

        if normalized in {
            "false",
            "no",
            "0",
            "n",
        }:
            return False

    return None


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if number != number:
        # NaN
        return None

    return number


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        dt = value

    elif isinstance(value, date):
        dt = datetime(
            value.year,
            value.month,
            value.day,
            tzinfo=timezone.utc,
        )

    else:
        text = _clean(value)

        if not text:
            return None

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=timezone.utc
        )

    return dt.astimezone(
        timezone.utc
    )


def _safe_ratio(
    numerator: int | float,
    denominator: int | float,
) -> Optional[float]:

    if denominator == 0:
        return None

    return (
        float(numerator)
        / float(denominator)
    )


def _event_value(
    event: Mapping[str, Any],
    *keys: str,
    default: Any = None,
) -> Any:
    """
    Read a value from common telemetry locations.

    Supported locations:
    - event[key]
    - event["dimensions"][key]
    - event["metadata"][key]
    - event["metrics"][key]
    - event["counters"][key]

    This keeps metric computation reasonably tolerant of TelemetryService
    export shape without making this module depend directly on its database.
    """

    containers: list[
        Mapping[str, Any]
    ] = [
        event
    ]

    for container_name in (
        "dimensions",
        "metadata",
        "metrics",
        "counters",
    ):
        candidate = event.get(
            container_name
        )

        if isinstance(
            candidate,
            Mapping,
        ):
            containers.append(
                candidate
            )

    for key in keys:
        for container in containers:
            if key in container:
                value = container.get(
                    key
                )

                if value is not None:
                    return value

    return default


def _event_type(
    event: Mapping[str, Any],
) -> str:
    return _enum(
        _event_value(
            event,
            "event_type",
            "type",
            "name",
            default="",
        )
    )


def _event_time(
    event: Mapping[str, Any],
) -> Optional[datetime]:
    return _parse_datetime(
        _event_value(
            event,
            "timestamp",
            "occurred_at",
            "created_at",
            "event_time",
            "recorded_at",
        )
    )


def _actor_id(
    event: Mapping[str, Any],
) -> str:
    """
    Prefer a pseudonymous product actor identifier where available.
    """

    return _clean(
        _event_value(
            event,
            "actor_id",
            "anonymous_user_id",
            "user_id",
            "session_user_id",
            default="",
        )
    )


def _report_id(
    event: Mapping[str, Any],
) -> str:
    return _clean(
        _event_value(
            event,
            "report_id",
            default="",
        )
    )


def _insight_id(
    event: Mapping[str, Any],
) -> str:
    return _clean(
        _event_value(
            event,
            "insight_id",
            default="",
        )
    )


def _research_id(
    event: Mapping[str, Any],
) -> str:
    return _clean(
        _event_value(
            event,
            "research_id",
            "run_id",
            default="",
        )
    )


def _topic_id(
    event: Mapping[str, Any],
) -> str:
    return _clean(
        _event_value(
            event,
            "topic_id",
            "topic_key",
            "research_topic_id",
            default="",
        )
    )


@dataclass(
    frozen=True
)
class UsefulReportPolicy:
    """
    Defines which product behaviors make a completed report "useful".

    Explicit report_useful/report_marked_useful events qualify immediately.

    Positive feedback also qualifies when:
        useful=True

    or sentiment is one of:
        positive
        useful
        helpful
        good
        accepted

    Behavioral signals should represent intentional user value rather than
    passive exposure.
    """

    explicit_useful_event_types: frozenset[
        str
    ] = frozenset(
        {
            "report_useful",
            "report_marked_useful",
        }
    )

    positive_feedback_event_types: frozenset[
        str
    ] = frozenset(
        {
            "report_feedback",
            "insight_feedback",
        }
    )

    behavioral_signal_event_types: frozenset[
        str
    ] = frozenset(
        {
            "insight_saved",
            "insight_copied",
            "insight_shared",
            "evidence_trail_opened",
            "follow_up_question",
            "report_revisited",
            "research_refined",
            "research_depth_escalated",
        }
    )

    positive_sentiments: frozenset[
        str
    ] = frozenset(
        {
            "positive",
            "useful",
            "helpful",
            "good",
            "accepted",
        }
    )


@dataclass(
    frozen=True
)
class ProductMetricDefinition:
    metric_id: str
    group: str
    description: str

    numerator: str = ""
    denominator: str = ""

    higher_is_better: Optional[
        bool
    ] = None


@dataclass(
    frozen=True
)
class ProductMetricSnapshot:
    """
    Aggregate product metrics for one telemetry slice.

    None means:
        metric cannot be calculated from the available data.

    None does NOT mean zero.
    """

    north_star: Mapping[
        str,
        Optional[float],
    ]

    activation: Mapping[
        str,
        Optional[float],
    ]

    value: Mapping[
        str,
        Optional[float],
    ]

    trust: Mapping[
        str,
        Optional[float],
    ]

    engagement: Mapping[
        str,
        Optional[float],
    ]

    retention: Mapping[
        str,
        Optional[float],
    ]

    efficiency: Mapping[
        str,
        Optional[float],
    ]

    counts: Mapping[
        str,
        int,
    ]

    metadata: Mapping[
        str,
        Any,
    ] = field(
        default_factory=dict
    )

    def to_dict(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        return {
            "north_star": dict(
                self.north_star
            ),
            "activation": dict(
                self.activation
            ),
            "value": dict(
                self.value
            ),
            "trust": dict(
                self.trust
            ),
            "engagement": dict(
                self.engagement
            ),
            "retention": dict(
                self.retention
            ),
            "efficiency": dict(
                self.efficiency
            ),
            "counts": dict(
                self.counts
            ),
            "metadata": dict(
                self.metadata
            ),
        }


class ProductMetricsCalculator:
    """
    Deterministic product-metrics calculator.

    Input is a sequence of telemetry event mappings.

    It deliberately does not import TelemetryService.

    That keeps:

        TelemetryService
            =
        data collection

    separate from:

        ProductMetricsCalculator
            =
        metric computation


    Recommended canonical telemetry events
    --------------------------------------

    Research lifecycle:
        research_started
        report_completed
        report_failed

    User-value signals:
        report_useful
        report_marked_useful
        report_feedback
        insight_feedback
        insight_saved
        insight_copied
        insight_shared
        evidence_trail_opened
        follow_up_question
        report_revisited
        research_refined
        research_depth_escalated

    Insight exposure/value:
        insight_shown
        insight_accepted

    Trust:
        correction_submitted
        unsupported_claim_exposed

    Efficiency:
        search_executed
        source_fetched
        compute_usage

    report_completed may also contain:
        duration_seconds
        compute_seconds
    """

    ACTIVE_EVENT_TYPES = frozenset(
        {
            "research_started",
            "report_completed",
            "report_feedback",
            "report_useful",
            "report_marked_useful",
            "insight_feedback",
            "insight_saved",
            "insight_copied",
            "insight_shared",
            "evidence_trail_opened",
            "follow_up_question",
            "report_revisited",
            "research_refined",
            "research_depth_escalated",
        }
    )

    def __init__(
        self,
        *,
        useful_report_policy: Optional[
            UsefulReportPolicy
        ] = None,
    ) -> None:

        self.useful_report_policy = (
            useful_report_policy
            or UsefulReportPolicy()
        )

    def calculate(
        self,
        events: (
            Sequence[
                Mapping[str, Any]
            ]
            | Iterable[
                Mapping[str, Any]
            ]
        ),
    ) -> ProductMetricSnapshot:

        normalized = [
            dict(event)
            for event in events
        ]

        completed_reports = (
            self._completed_report_ids(
                normalized
            )
        )

        useful_reports = (
            self._useful_report_ids(
                normalized,
                completed_reports=(
                    completed_reports
                ),
            )
        )

        active_users = (
            self._active_user_ids(
                normalized
            )
        )

        started_research = {
            _research_id(
                event
            )
            for event in normalized
            if (
                _event_type(event)
                == "research_started"
                and _research_id(
                    event
                )
            )
        }

        completed_research = {
            _research_id(
                event
            )
            for event in normalized
            if (
                _event_type(event)
                == "report_completed"
                and _research_id(
                    event
                )
            )
        }

        shown_insights = {
            _insight_id(
                event
            )
            for event in normalized
            if (
                _event_type(event)
                == "insight_shown"
                and _insight_id(
                    event
                )
            )
        }

        useful_insights = (
            self._useful_insight_ids(
                normalized
            )
        )

        non_obvious_shown = (
            self._insights_with_flag(
                normalized,
                event_type=(
                    "insight_shown"
                ),
                flag_names=(
                    "non_obvious",
                    "is_non_obvious",
                ),
            )
        )

        non_obvious_accepted = {
            insight_id
            for insight_id
            in useful_insights
            if insight_id
            in non_obvious_shown
        }

        cross_event_useful = (
            self._cross_event_useful_insights(
                normalized,
                useful_insights=(
                    useful_insights
                ),
            )
        )

        reports_with_evidence_trail = {
            _report_id(
                event
            )
            for event in normalized
            if (
                _event_type(event)
                == "evidence_trail_opened"
                and _report_id(
                    event
                )
                in completed_reports
            )
        }

        reports_with_follow_up = {
            _report_id(
                event
            )
            for event in normalized
            if (
                _event_type(event)
                == "follow_up_question"
                and _report_id(
                    event
                )
                in completed_reports
            )
        }

        reports_refined = {
            _report_id(
                event
            )
            for event in normalized
            if (
                _event_type(event)
                == "research_refined"
                and _report_id(
                    event
                )
                in completed_reports
            )
        }

        reports_escalated = {
            _report_id(
                event
            )
            for event in normalized
            if (
                _event_type(event)
                == "research_depth_escalated"
                and _report_id(
                    event
                )
                in completed_reports
            )
        }

        correction_reports = {
            _report_id(
                event
            )
            for event in normalized
            if (
                _event_type(event)
                == "correction_submitted"
                and _report_id(
                    event
                )
                in completed_reports
            )
        }

        unsupported_exposure_reports = {
            _report_id(
                event
            )
            for event in normalized
            if (
                _event_type(event)
                == "unsupported_claim_exposed"
                and _report_id(
                    event
                )
                in completed_reports
            )
        }

        report_latencies = (
            self._report_latencies(
                normalized
            )
        )

        compute_seconds = (
            self._sum_numeric(
                normalized,
                event_types={
                    "compute_usage",
                    "report_completed",
                },
                field_names=(
                    "compute_seconds",
                ),
            )
        )

        search_count = (
            self._count_events(
                normalized,
                {
                    "search_executed"
                },
            )
        )

        source_fetch_count = (
            self._count_events(
                normalized,
                {
                    "source_fetched"
                },
            )
        )

        first_useful_report_rate = (
            self._first_useful_report_rate(
                normalized,
                completed_reports=(
                    completed_reports
                ),
                useful_reports=(
                    useful_reports
                ),
            )
        )

        time_to_first_useful = (
            self._time_to_first_useful_report(
                normalized,
                useful_reports=(
                    useful_reports
                ),
            )
        )

        d7 = self._retention_rate(
            normalized,
            day_offset=7,
        )

        d30 = self._retention_rate(
            normalized,
            day_offset=30,
        )

        repeat_topic_rate = (
            self._repeat_topic_rate(
                normalized
            )
        )

        counts = {
            "event_count": len(
                normalized
            ),
            "active_users": len(
                active_users
            ),
            "research_started": len(
                started_research
            ),
            "research_completed": len(
                completed_research
            ),
            "completed_reports": len(
                completed_reports
            ),
            "useful_reports": len(
                useful_reports
            ),
            "insights_shown": len(
                shown_insights
            ),
            "useful_insights": len(
                useful_insights
            ),
            "non_obvious_insights_shown": len(
                non_obvious_shown
            ),
            "non_obvious_insights_accepted": len(
                non_obvious_accepted
            ),
            "cross_event_useful_insights": len(
                cross_event_useful
            ),
            "reports_with_evidence_trail_engagement": len(
                reports_with_evidence_trail
            ),
            "reports_with_follow_up": len(
                reports_with_follow_up
            ),
            "reports_refined": len(
                reports_refined
            ),
            "reports_fast_to_deep_escalated": len(
                reports_escalated
            ),
            "reports_with_correction": len(
                correction_reports
            ),
            "reports_with_unsupported_claim_exposure": len(
                unsupported_exposure_reports
            ),
            "searches_executed": (
                search_count
            ),
            "sources_fetched": (
                source_fetch_count
            ),
        }

        return ProductMetricSnapshot(
            north_star={
                "useful_research_reports_per_active_user": (
                    _safe_ratio(
                        len(
                            useful_reports
                        ),
                        len(
                            active_users
                        ),
                    )
                )
            },
            activation={
                "first_useful_report_rate": (
                    first_useful_report_rate
                ),
                "median_time_to_first_useful_report_seconds": (
                    median(
                        time_to_first_useful
                    )
                    if time_to_first_useful
                    else None
                ),
                "research_completion_rate": (
                    _safe_ratio(
                        len(
                            completed_research
                        ),
                        len(
                            started_research
                        ),
                    )
                ),
            },
            value={
                "useful_report_rate": (
                    _safe_ratio(
                        len(
                            useful_reports
                        ),
                        len(
                            completed_reports
                        ),
                    )
                ),
                "useful_insight_rate": (
                    _safe_ratio(
                        len(
                            useful_insights
                        ),
                        len(
                            shown_insights
                        ),
                    )
                ),
                "non_obvious_insight_acceptance_rate": (
                    _safe_ratio(
                        len(
                            non_obvious_accepted
                        ),
                        len(
                            non_obvious_shown
                        ),
                    )
                ),
                "cross_event_synthesis_rate": (
                    _safe_ratio(
                        len(
                            cross_event_useful
                        ),
                        len(
                            useful_insights
                        ),
                    )
                ),
            },
            trust={
                "evidence_trail_engagement_rate": (
                    _safe_ratio(
                        len(
                            reports_with_evidence_trail
                        ),
                        len(
                            completed_reports
                        ),
                    )
                ),
                "correction_rate": (
                    _safe_ratio(
                        len(
                            correction_reports
                        ),
                        len(
                            completed_reports
                        ),
                    )
                ),
                "unsupported_claim_exposure_rate": (
                    _safe_ratio(
                        len(
                            unsupported_exposure_reports
                        ),
                        len(
                            completed_reports
                        ),
                    )
                ),
            },
            engagement={
                "follow_up_engagement_rate": (
                    _safe_ratio(
                        len(
                            reports_with_follow_up
                        ),
                        len(
                            completed_reports
                        ),
                    )
                ),
                "fast_to_deep_escalation_rate": (
                    _safe_ratio(
                        len(
                            reports_escalated
                        ),
                        len(
                            completed_reports
                        ),
                    )
                ),
                "refinement_rate": (
                    _safe_ratio(
                        len(
                            reports_refined
                        ),
                        len(
                            completed_reports
                        ),
                    )
                ),
                "repeat_topic_rate": (
                    repeat_topic_rate
                ),
            },
            retention={
                "d7_retention": d7,
                "d30_retention": d30,
            },
            efficiency={
                "median_report_latency_seconds": (
                    median(
                        report_latencies
                    )
                    if report_latencies
                    else None
                ),
                "searches_per_useful_report": (
                    _safe_ratio(
                        search_count,
                        len(
                            useful_reports
                        ),
                    )
                ),
                "source_fetches_per_useful_report": (
                    _safe_ratio(
                        source_fetch_count,
                        len(
                            useful_reports
                        ),
                    )
                ),
                "compute_minutes_per_useful_report": (
                    _safe_ratio(
                        (
                            compute_seconds
                            / 60.0
                        ),
                        len(
                            useful_reports
                        ),
                    )
                ),
            },
            counts=counts,
            metadata={
                "metrics_version": (
                    METRICS_VERSION
                ),
                "north_star_metric": (
                    "useful_research_reports_per_active_user"
                ),
                "null_metric_semantics": (
                    "None means the metric cannot "
                    "be computed because required "
                    "data or its denominator is "
                    "absent. It does not mean zero."
                ),
                "product_value_not_ai_quality": True,
            },
        )

    def _completed_report_ids(
        self,
        events: Sequence[
            Mapping[str, Any]
        ],
    ) -> set[str]:

        return {
            _report_id(
                event
            )
            for event in events
            if (
                _event_type(event)
                == "report_completed"
                and _report_id(
                    event
                )
            )
        }

    def _active_user_ids(
        self,
        events: Sequence[
            Mapping[str, Any]
        ],
    ) -> set[str]:

        return {
            _actor_id(
                event
            )
            for event in events
            if (
                _event_type(event)
                in self.ACTIVE_EVENT_TYPES
                and _actor_id(
                    event
                )
            )
        }

    def _is_positive_feedback(
        self,
        event: Mapping[
            str,
            Any,
        ],
    ) -> bool:

        explicit_useful = (
            _as_bool(
                _event_value(
                    event,
                    "useful",
                    "is_useful",
                    default=None,
                )
            )
        )

        if explicit_useful is True:
            return True

        sentiment = _enum(
            _event_value(
                event,
                "sentiment",
                "feedback_sentiment",
                "rating_label",
                default="",
            )
        )

        return (
            sentiment
            in (
                self
                .useful_report_policy
                .positive_sentiments
            )
        )

    def _useful_report_ids(
        self,
        events: Sequence[
            Mapping[str, Any]
        ],
        *,
        completed_reports: set[str],
    ) -> set[str]:

        useful: set[str] = set()

        for event in events:
            report_id = _report_id(
                event
            )

            if (
                not report_id
                or report_id
                not in completed_reports
            ):
                continue

            event_type = _event_type(
                event
            )

            if (
                event_type
                in (
                    self
                    .useful_report_policy
                    .explicit_useful_event_types
                )
            ):
                useful.add(
                    report_id
                )
                continue

            if (
                event_type
                in (
                    self
                    .useful_report_policy
                    .positive_feedback_event_types
                )
                and self._is_positive_feedback(
                    event
                )
            ):
                useful.add(
                    report_id
                )
                continue

            if (
                event_type
                in (
                    self
                    .useful_report_policy
                    .behavioral_signal_event_types
                )
            ):
                useful.add(
                    report_id
                )

        return useful

    def _useful_insight_ids(
        self,
        events: Sequence[
            Mapping[str, Any]
        ],
    ) -> set[str]:

        useful: set[str] = set()

        direct_types = {
            "insight_accepted",
            "insight_saved",
            "insight_copied",
            "insight_shared",
        }

        for event in events:
            insight_id = _insight_id(
                event
            )

            if not insight_id:
                continue

            event_type = _event_type(
                event
            )

            if event_type in direct_types:
                useful.add(
                    insight_id
                )
                continue

            if (
                event_type
                == "insight_feedback"
                and self._is_positive_feedback(
                    event
                )
            ):
                useful.add(
                    insight_id
                )

        return useful

    def _insights_with_flag(
        self,
        events: Sequence[
            Mapping[str, Any]
        ],
        *,
        event_type: str,
        flag_names: Sequence[str],
    ) -> set[str]:

        result: set[str] = set()

        for event in events:
            if (
                _event_type(event)
                != event_type
            ):
                continue

            insight_id = _insight_id(
                event
            )

            if not insight_id:
                continue

            value = _event_value(
                event,
                *flag_names,
                default=None,
            )

            if _as_bool(
                value
            ) is True:
                result.add(
                    insight_id
                )

        return result

    def _cross_event_useful_insights(
        self,
        events: Sequence[
            Mapping[str, Any]
        ],
        *,
        useful_insights: set[str],
    ) -> set[str]:
        """
        A cross-event useful insight requires:

        - user usefulness signal;
        - >= 2 distinct underlying event IDs;
        - an explicit mechanism/structural-connection marker.

        This module does NOT infer mechanism presence from natural-language
        text. That classification must come from the reasoning/evaluation
        pipeline.
        """

        output: set[str] = set()

        for event in events:
            if (
                _event_type(event)
                != "insight_shown"
            ):
                continue

            insight_id = _insight_id(
                event
            )

            if (
                not insight_id
                or insight_id
                not in useful_insights
            ):
                continue

            event_ids = _event_value(
                event,
                "source_event_ids",
                "event_ids",
                default=[],
            )

            if isinstance(
                event_ids,
                str,
            ):
                event_ids = [
                    event_ids
                ]

            if not isinstance(
                event_ids,
                Sequence,
            ):
                event_ids = []

            distinct_event_ids = {
                _clean(
                    item
                )
                for item
                in event_ids
                if _clean(
                    item
                )
            }

            mechanism_present = (
                _as_bool(
                    _event_value(
                        event,
                        "mechanism_present",
                        "has_mechanism",
                        "structural_connection_present",
                        default=None,
                    )
                )
            )

            if (
                len(
                    distinct_event_ids
                )
                >= 2
                and mechanism_present
                is True
            ):
                output.add(
                    insight_id
                )

        return output

    def _report_latencies(
        self,
        events: Sequence[
            Mapping[str, Any]
        ],
    ) -> list[float]:

        latencies: list[
            float
        ] = []

        for event in events:
            if (
                _event_type(event)
                != "report_completed"
            ):
                continue

            value = _as_float(
                _event_value(
                    event,
                    "duration_seconds",
                    "latency_seconds",
                    "report_latency_seconds",
                    default=None,
                )
            )

            if (
                value is not None
                and value >= 0
            ):
                latencies.append(
                    value
                )

        return latencies

    def _count_events(
        self,
        events: Sequence[
            Mapping[str, Any]
        ],
        event_types: set[str],
    ) -> int:

        return sum(
            1
            for event in events
            if (
                _event_type(event)
                in event_types
            )
        )

    def _sum_numeric(
        self,
        events: Sequence[
            Mapping[str, Any]
        ],
        *,
        event_types: set[str],
        field_names: Sequence[str],
    ) -> float:

        total = 0.0

        for event in events:
            if (
                _event_type(event)
                not in event_types
            ):
                continue

            value = _as_float(
                _event_value(
                    event,
                    *field_names,
                    default=None,
                )
            )

            if (
                value is not None
                and value >= 0
            ):
                total += value

        return total

    def _first_useful_report_rate(
        self,
        events: Sequence[
            Mapping[str, Any]
        ],
        *,
        completed_reports: set[str],
        useful_reports: set[str],
    ) -> Optional[float]:

        completed_by_actor: dict[
            str,
            list[
                tuple[
                    datetime,
                    str,
                ]
            ],
        ] = defaultdict(
            list
        )

        for event in events:
            if (
                _event_type(event)
                != "report_completed"
            ):
                continue

            actor = _actor_id(
                event
            )

            report_id = _report_id(
                event
            )

            timestamp = _event_time(
                event
            )

            if (
                actor
                and report_id
                in completed_reports
                and timestamp
                is not None
            ):
                completed_by_actor[
                    actor
                ].append(
                    (
                        timestamp,
                        report_id,
                    )
                )

        if not completed_by_actor:
            return None

        actors_first_report_useful = 0

        for reports in (
            completed_by_actor.values()
        ):
            reports.sort(
                key=lambda item: (
                    item[0]
                )
            )

            first_report_id = (
                reports[0][1]
            )

            if (
                first_report_id
                in useful_reports
            ):
                actors_first_report_useful += 1

        return _safe_ratio(
            actors_first_report_useful,
            len(
                completed_by_actor
            ),
        )

    def _time_to_first_useful_report(
        self,
        events: Sequence[
            Mapping[str, Any]
        ],
        *,
        useful_reports: set[str],
    ) -> list[float]:

        first_active_by_actor: dict[
            str,
            datetime,
        ] = {}

        completion_by_report: dict[
            str,
            tuple[
                str,
                datetime,
            ],
        ] = {}

        for event in events:
            actor = _actor_id(
                event
            )

            timestamp = _event_time(
                event
            )

            if (
                not actor
                or timestamp
                is None
            ):
                continue

            if (
                _event_type(event)
                in self.ACTIVE_EVENT_TYPES
            ):
                current = (
                    first_active_by_actor.get(
                        actor
                    )
                )

                if (
                    current is None
                    or timestamp < current
                ):
                    first_active_by_actor[
                        actor
                    ] = timestamp

            if (
                _event_type(event)
                == "report_completed"
            ):
                report_id = _report_id(
                    event
                )

                if report_id:
                    completion_by_report[
                        report_id
                    ] = (
                        actor,
                        timestamp,
                    )

        first_useful_completion_by_actor: dict[
            str,
            datetime,
        ] = {}

        for report_id in useful_reports:
            completion = (
                completion_by_report.get(
                    report_id
                )
            )

            if completion is None:
                continue

            actor, timestamp = (
                completion
            )

            current = (
                first_useful_completion_by_actor.get(
                    actor
                )
            )

            if (
                current is None
                or timestamp < current
            ):
                first_useful_completion_by_actor[
                    actor
                ] = timestamp

        durations: list[
            float
        ] = []

        for (
            actor,
            useful_time,
        ) in (
            first_useful_completion_by_actor.items()
        ):
            start = (
                first_active_by_actor.get(
                    actor
                )
            )

            if start is None:
                continue

            seconds = (
                useful_time - start
            ).total_seconds()

            if seconds >= 0:
                durations.append(
                    seconds
                )

        return durations

    def _retention_rate(
        self,
        events: Sequence[
            Mapping[str, Any]
        ],
        *,
        day_offset: int,
    ) -> Optional[float]:
        """
        Exact-day retention.

        An actor is retained when they are active again exactly N calendar
        days after their first active day.

        Users whose target day lies beyond the observed telemetry period are
        excluded from the denominator to avoid right-censoring.
        """

        activity_days: dict[
            str,
            set[date],
        ] = defaultdict(
            set
        )

        observed_dates: list[
            date
        ] = []

        for event in events:
            if (
                _event_type(event)
                not in self.ACTIVE_EVENT_TYPES
            ):
                continue

            actor = _actor_id(
                event
            )

            timestamp = _event_time(
                event
            )

            if (
                not actor
                or timestamp
                is None
            ):
                continue

            day = timestamp.date()

            activity_days[
                actor
            ].add(
                day
            )

            observed_dates.append(
                day
            )

        if (
            not activity_days
            or not observed_dates
        ):
            return None

        max_observed_date = max(
            observed_dates
        )

        eligible = 0
        retained = 0

        for (
            actor,
            days,
        ) in activity_days.items():

            first_day = min(
                days
            )

            target_day = (
                date.fromordinal(
                    first_day.toordinal()
                    + day_offset
                )
            )

            if (
                target_day
                > max_observed_date
            ):
                continue

            eligible += 1

            if target_day in days:
                retained += 1

        return _safe_ratio(
            retained,
            eligible,
        )

    def _repeat_topic_rate(
        self,
        events: Sequence[
            Mapping[str, Any]
        ],
    ) -> Optional[float]:
        """
        Share of users who research at least one topic in two or more
        distinct research runs.
        """

        topic_runs_by_actor: dict[
            str,
            dict[
                str,
                set[str],
            ],
        ] = defaultdict(
            lambda: defaultdict(
                set
            )
        )

        for event in events:
            if (
                _event_type(event)
                != "research_started"
            ):
                continue

            actor = _actor_id(
                event
            )

            topic = _topic_id(
                event
            )

            research_id = (
                _research_id(
                    event
                )
            )

            if (
                actor
                and topic
                and research_id
            ):
                topic_runs_by_actor[
                    actor
                ][
                    topic
                ].add(
                    research_id
                )

        if not topic_runs_by_actor:
            return None

        repeat_users = 0

        for topic_runs in (
            topic_runs_by_actor.values()
        ):
            if any(
                len(
                    run_ids
                )
                >= 2
                for run_ids
                in topic_runs.values()
            ):
                repeat_users += 1

        return _safe_ratio(
            repeat_users,
            len(
                topic_runs_by_actor
            ),
        )


def metric_catalog(
) -> tuple[
    ProductMetricDefinition,
    ...
]:
    """
    Human-readable metric definitions for documentation, UI, dashboards,
    evaluation analysis, and the product case study.
    """

    return (
        ProductMetricDefinition(
            metric_id=(
                "useful_research_reports_per_active_user"
            ),
            group="north_star",
            description=(
                "Completed reports with a usefulness signal divided "
                "by active users."
            ),
            numerator=(
                "useful_reports"
            ),
            denominator=(
                "active_users"
            ),
            higher_is_better=True,
        ),
        ProductMetricDefinition(
            metric_id=(
                "first_useful_report_rate"
            ),
            group="activation",
            description=(
                "Share of users whose first completed report receives "
                "a usefulness signal."
            ),
            numerator=(
                "users_whose_first_completed_report_is_useful"
            ),
            denominator=(
                "users_with_a_completed_report"
            ),
            higher_is_better=True,
        ),
        ProductMetricDefinition(
            metric_id=(
                "median_time_to_first_useful_report_seconds"
            ),
            group="activation",
            description=(
                "Median elapsed time from first observed active use to "
                "the user's first useful completed report."
            ),
            higher_is_better=False,
        ),
        ProductMetricDefinition(
            metric_id=(
                "research_completion_rate"
            ),
            group="activation",
            description=(
                "Distinct research runs producing completed reports "
                "divided by distinct research runs started."
            ),
            numerator=(
                "completed_research_runs"
            ),
            denominator=(
                "research_runs_started"
            ),
            higher_is_better=True,
        ),
        ProductMetricDefinition(
            metric_id=(
                "useful_report_rate"
            ),
            group="value",
            description=(
                "Useful completed reports divided by completed reports."
            ),
            numerator=(
                "useful_reports"
            ),
            denominator=(
                "completed_reports"
            ),
            higher_is_better=True,
        ),
        ProductMetricDefinition(
            metric_id=(
                "useful_insight_rate"
            ),
            group="value",
            description=(
                "Insights receiving a positive or high-intent usefulness "
                "signal divided by insights shown."
            ),
            numerator=(
                "useful_insights"
            ),
            denominator=(
                "insights_shown"
            ),
            higher_is_better=True,
        ),
        ProductMetricDefinition(
            metric_id=(
                "non_obvious_insight_acceptance_rate"
            ),
            group="value",
            description=(
                "Useful insights among insights explicitly classified "
                "as non-obvious when shown."
            ),
            numerator=(
                "accepted_non_obvious_insights"
            ),
            denominator=(
                "non_obvious_insights_shown"
            ),
            higher_is_better=True,
        ),
        ProductMetricDefinition(
            metric_id=(
                "cross_event_synthesis_rate"
            ),
            group="value",
            description=(
                "Useful insights combining at least two distinct events "
                "and containing a mechanism or structural connection, "
                "divided by all useful insights."
            ),
            numerator=(
                "cross_event_useful_insights"
            ),
            denominator=(
                "useful_insights"
            ),
            higher_is_better=True,
        ),
        ProductMetricDefinition(
            metric_id=(
                "evidence_trail_engagement_rate"
            ),
            group="trust",
            description=(
                "Completed reports whose evidence trail is opened "
                "divided by completed reports."
            ),
            numerator=(
                "reports_with_evidence_trail_engagement"
            ),
            denominator=(
                "completed_reports"
            ),
            higher_is_better=None,
        ),
        ProductMetricDefinition(
            metric_id=(
                "correction_rate"
            ),
            group="trust",
            description=(
                "Completed reports receiving a user correction divided "
                "by completed reports."
            ),
            numerator=(
                "reports_with_correction"
            ),
            denominator=(
                "completed_reports"
            ),
            higher_is_better=False,
        ),
        ProductMetricDefinition(
            metric_id=(
                "unsupported_claim_exposure_rate"
            ),
            group="trust",
            description=(
                "Completed reports with a user-visible unsupported claim "
                "exposure divided by completed reports."
            ),
            numerator=(
                "reports_with_unsupported_claim_exposure"
            ),
            denominator=(
                "completed_reports"
            ),
            higher_is_better=False,
        ),
        ProductMetricDefinition(
            metric_id=(
                "follow_up_engagement_rate"
            ),
            group="engagement",
            description=(
                "Completed reports that trigger a follow-up research "
                "question divided by completed reports."
            ),
            numerator=(
                "reports_with_follow_up"
            ),
            denominator=(
                "completed_reports"
            ),
            higher_is_better=True,
        ),
        ProductMetricDefinition(
            metric_id=(
                "fast_to_deep_escalation_rate"
            ),
            group="engagement",
            description=(
                "Completed reports escalated from Fast to Deep research "
                "divided by completed reports."
            ),
            numerator=(
                "reports_fast_to_deep_escalated"
            ),
            denominator=(
                "completed_reports"
            ),
            higher_is_better=None,
        ),
        ProductMetricDefinition(
            metric_id=(
                "refinement_rate"
            ),
            group="engagement",
            description=(
                "Completed reports explicitly refined or regenerated "
                "divided by completed reports."
            ),
            numerator=(
                "reports_refined"
            ),
            denominator=(
                "completed_reports"
            ),
            higher_is_better=None,
        ),
        ProductMetricDefinition(
            metric_id=(
                "repeat_topic_rate"
            ),
            group="engagement",
            description=(
                "Users researching at least one topic in two or more "
                "distinct runs divided by users with topic-tagged research."
            ),
            higher_is_better=True,
        ),
        ProductMetricDefinition(
            metric_id=(
                "d7_retention"
            ),
            group="retention",
            description=(
                "Exact-day D7 active-user retention."
            ),
            higher_is_better=True,
        ),
        ProductMetricDefinition(
            metric_id=(
                "d30_retention"
            ),
            group="retention",
            description=(
                "Exact-day D30 active-user retention."
            ),
            higher_is_better=True,
        ),
        ProductMetricDefinition(
            metric_id=(
                "median_report_latency_seconds"
            ),
            group="efficiency",
            description=(
                "Median latency of completed research reports."
            ),
            higher_is_better=False,
        ),
        ProductMetricDefinition(
            metric_id=(
                "searches_per_useful_report"
            ),
            group="efficiency",
            description=(
                "Search executions divided by useful reports."
            ),
            numerator=(
                "searches_executed"
            ),
            denominator=(
                "useful_reports"
            ),
            higher_is_better=False,
        ),
        ProductMetricDefinition(
            metric_id=(
                "source_fetches_per_useful_report"
            ),
            group="efficiency",
            description=(
                "Fetched sources divided by useful reports."
            ),
            numerator=(
                "sources_fetched"
            ),
            denominator=(
                "useful_reports"
            ),
            higher_is_better=False,
        ),
        ProductMetricDefinition(
            metric_id=(
                "compute_minutes_per_useful_report"
            ),
            group="efficiency",
            description=(
                "Observed compute minutes divided by useful reports."
            ),
            denominator=(
                "useful_reports"
            ),
            higher_is_better=False,
        ),
    )


def calculate_product_metrics(
    events: (
        Sequence[
            Mapping[str, Any]
        ]
        | Iterable[
            Mapping[str, Any]
        ]
    ),
    *,
    useful_report_policy: Optional[
        UsefulReportPolicy
    ] = None,
) -> dict[
    str,
    Any,
]:
    """
    Convenience function returning a JSON-serializable metric snapshot.
    """

    calculator = (
        ProductMetricsCalculator(
            useful_report_policy=(
                useful_report_policy
            ),
        )
    )

    return (
        calculator
        .calculate(
            events
        )
        .to_dict()
    )


__all__ = [
    "METRICS_VERSION",
    "ProductMetricsError",
    "UsefulReportPolicy",
    "ProductMetricDefinition",
    "ProductMetricSnapshot",
    "ProductMetricsCalculator",
    "metric_catalog",
    "calculate_product_metrics",
]