"""
Streamlit research dashboard for the AI Event Intelligence product.

Responsibilities
----------------
- Always expose a new-research entry point so a user can research one topic,
  inspect the result, and immediately start another topic.
- Delegate research execution to the existing research-run / orchestration layer.
- Show RELEASED research reports only; draft/review artifacts stay out of the UI.
- Offer the DecisionAgent as an optional downstream interaction on a selected
  released report.
- Preserve report epistemic structure instead of flattening it into prose.
- Collect explicit report-level and insight-level Like / Dislike signals.
- Collect one optional free-text feedback field without forcing users through
  a long taxonomy of buttons.
- Persist raw user feedback locally so it can be reinterpreted later by an
  LLM-backed UserFeedbackService.
- Emit product telemetry required by product metrics without putting raw
  feedback text into TelemetryService.
- Measure evidence-trail engagement and report views.

Important boundaries
--------------------
- Like / Dislike is a PRODUCT VALUE signal, not factual evidence.
- Written feedback is quality guidance, not grounding evidence.
- Telemetry stores IDs / labels / counts, never raw feedback text.
- ReportService remains the source of truth for released reports.
- The dashboard may initiate a research run, but it does not itself perform
  evidence admission, grounding, red-teaming, policy enforcement, or release.
- This dashboard never authorizes report release.
- DecisionAgent is optional and may operate only on a selected released report.

Local-first identity
--------------------
The default actor identifier is persisted locally in data/ui/local_actor_id.txt.
That is appropriate for the current single-user/local portfolio application.

A multi-user deployment should replace it with authenticated or browser-scoped
identity rather than sharing one local actor ID across users.
"""

from __future__ import annotations

DASHBOARD_VERSION = "research-dashboard-v4.10-terminal-outcome-contract"

import hashlib
import importlib
import inspect
import json
import logging
import sqlite3
import uuid

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import streamlit as st

from services.report_service import (
    ReportService,
    ReportServiceError,
)
from services.telemetry_service import TelemetryService
from services.user_feedback_service import UserFeedbackService


# v6.20.10: under the configured "event_intelligence" logger so the submitted
# question and depth reach data/logs/system.log.
logger = logging.getLogger("event_intelligence.frontend.dashboard")


REPORT_DB_PATH = "data/reports.sqlite3"
TELEMETRY_DB_PATH = "data/telemetry.sqlite3"
FEEDBACK_DB_PATH = "data/user_feedback.sqlite3"

LOCAL_ACTOR_PATH = Path(
    "data/ui/local_actor_id.txt"
)


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def clean_text(
    value: Any,
) -> str:
    if value is None:
        return ""

    return " ".join(
        str(value).split()
    ).strip()


def enum_text(
    value: Any,
) -> str:
    return (
        clean_text(value)
        .casefold()
        .replace(
            "-",
            "_",
        )
        .replace(
            " ",
            "_",
        )
    )


def as_list(
    value: Any,
) -> list[Any]:
    if value is None:
        return []

    if isinstance(
        value,
        list,
    ):
        return value

    if isinstance(
        value,
        tuple,
    ):
        return list(
            value
        )

    return [
        value
    ]


def json_safe(
    value: Any,
) -> Any:
    if (
        value is None
        or isinstance(
            value,
            (
                str,
                int,
                float,
                bool,
            ),
        )
    ):
        return value

    if isinstance(
        value,
        Mapping,
    ):
        return {
            str(
                key
            ): json_safe(
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
            json_safe(
                item
            )
            for item
            in value
        ]

    return str(
        value
    )


def stable_id(
    prefix: str,
    payload: Mapping[
        str,
        Any,
    ],
    length: int = 24,
) -> str:
    raw = json.dumps(
        json_safe(
            payload
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
    ).encode(
        "utf-8"
    )

    digest = hashlib.sha256(
        raw
    ).hexdigest()[
        :length
    ].upper()

    return (
        f"{prefix}-{digest}"
    )


# A research run can outlive the page that started it: if the browser
# reconnects mid-run (screen sleep, Wi-Fi blip), Streamlit opens a new page
# session that knows nothing about the run. This marker file, shared by all
# sessions, says a run is in progress so any open page can wait for it.
RESEARCH_RUN_MARKER = Path(__file__).resolve().parent.parent / "data" / "research_run_in_progress.json"
RESEARCH_RUN_LAST_OUTCOME = Path(__file__).resolve().parent.parent / "data" / "research_run_last_outcome.json"
RESEARCH_RUN_MAX_AGE_SECONDS = 2 * 60 * 60


def mark_research_run_started(question: str, depth: str) -> str:
    token = uuid.uuid4().hex
    try:
        RESEARCH_RUN_MARKER.parent.mkdir(parents=True, exist_ok=True)
        RESEARCH_RUN_MARKER.write_text(
            json.dumps({
                "token": token,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "question": question,
                "depth": depth,
            }),
            encoding="utf-8",
        )
    except OSError:
        pass
    return token


def mark_research_run_finished(token: str, outcome: str) -> None:
    try:
        RESEARCH_RUN_LAST_OUTCOME.write_text(
            json.dumps({"finished_at": datetime.now(timezone.utc).isoformat(), "outcome": outcome}),
            encoding="utf-8",
        )
    except OSError:
        pass
    try:
        current = json.loads(RESEARCH_RUN_MARKER.read_text(encoding="utf-8"))
        if current.get("token") == token:
            RESEARCH_RUN_MARKER.unlink()
    except (OSError, ValueError):
        pass


def research_run_in_progress() -> Optional[dict]:
    """The marker of a run still in progress, or None (absent, unreadable,
    or older than RESEARCH_RUN_MAX_AGE_SECONDS: a crashed run's leftover)."""
    try:
        data = json.loads(RESEARCH_RUN_MARKER.read_text(encoding="utf-8"))
        started = datetime.fromisoformat(str(data.get("started_at")))
    except (OSError, ValueError, TypeError):
        return None
    if (datetime.now(timezone.utc) - started).total_seconds() > RESEARCH_RUN_MAX_AGE_SECONDS:
        return None
    return data


def research_run_last_outcome() -> Optional[dict]:
    try:
        return json.loads(RESEARCH_RUN_LAST_OUTCOME.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def report_generated_label(
    report: Mapping[str, Any],
) -> str:
    """When the report was generated, in this computer's local time
    ("2026-10-04 18:35"). Every report has the same research question, so
    the date is what tells them apart."""
    raw = clean_text(report.get("created_at") or report.get("revision_created_at"))
    if not raw:
        return "unknown date"
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return raw[:16].replace("T", " ")


def report_sections(
    report: Mapping[
        str,
        Any,
    ],
) -> Mapping[
    str,
    Any,
]:
    """
    Resolve canonical report sections across both supported persistence shapes.

    Older/structured report revisions may store display sections under
    ``report["sections"]`` or ``report["payload"]["sections"]``.
    The current ResearchOrchestratorService also emits canonical research
    sections directly at the report root, including honest-abstention reports.

    Returning only nested ``sections`` made valid abstention reports appear
    blank in the dashboard even though their event landscape and evidence trail
    were persisted correctly.  This adapter keeps the dashboard read-only while
    accepting both report contracts.
    """
    sections = report.get(
        "sections"
    )

    if isinstance(
        sections,
        Mapping,
    ):
        return dict(
            sections
        )

    payload = report.get(
        "payload"
    )

    if isinstance(
        payload,
        Mapping,
    ):
        sections = payload.get(
            "sections"
        )

        if isinstance(
            sections,
            Mapping,
        ):
            return dict(
                sections
            )

    canonical_keys = (
        "executive_synthesis",
        "event_landscape",
        "connections",
        "what_is_happening_underneath",
        "hidden_insights",
        "second_order_effects",
        "counterevidence_and_alternative_explanations",
        "what_to_watch",
        "evidence_trail",
        "critical_unknowns",
        "grounding_by_insight",
    )

    root_sections = {
        key: report.get(
            key
        )
        for key in canonical_keys
        if key in report
    }

    if not root_sections and isinstance(
        payload,
        Mapping,
    ):
        root_sections = {
            key: payload.get(
                key
            )
            for key in canonical_keys
            if key in payload
        }

    source = (
        report
        if root_sections
        else (
            payload
            if isinstance(
                payload,
                Mapping,
            )
            else {}
        )
    )

    if (
        "what_is_happening_underneath"
        not in root_sections
        and "underlying_dynamics"
        in source
    ):
        root_sections[
            "what_is_happening_underneath"
        ] = source.get(
            "underlying_dynamics"
        )

    if (
        "counterevidence_and_alternative_explanations"
        not in root_sections
        and "counterevidence_and_alternatives"
        in source
    ):
        root_sections[
            "counterevidence_and_alternative_explanations"
        ] = source.get(
            "counterevidence_and_alternatives"
        )

    if (
        "critical_unknowns"
        not in root_sections
        and "uncertainty"
        in source
    ):
        root_sections[
            "critical_unknowns"
        ] = source.get(
            "uncertainty"
        )

    return root_sections


def report_value(
    report: Mapping[
        str,
        Any,
    ],
    key: str,
    default: Any = "",
) -> Any:
    if key in report:
        return report.get(
            key,
            default,
        )

    payload = report.get(
        "payload"
    )

    if (
        isinstance(
            payload,
            Mapping,
        )
        and key in payload
    ):
        return payload.get(
            key,
            default,
        )

    return default


def evidence_ids(
    item: Any,
) -> list[str]:
    """
    Extract only evidence IDs already present
    in the report item.
    """

    if not isinstance(
        item,
        Mapping,
    ):
        return []

    candidate_fields = (
        "evidence_ids",
        "supporting_evidence_ids",
        "counterevidence_ids",
        "counter_signal_ids",
        "references",
    )

    result: list[str] = []
    seen: set[str] = set()

    for field in candidate_fields:
        raw = item.get(
            field,
            [],
        )

        if isinstance(
            raw,
            str,
        ):
            raw = [
                raw
            ]

        if not isinstance(
            raw,
            (
                list,
                tuple,
            ),
        ):
            continue

        for value in raw:
            if isinstance(
                value,
                Mapping,
            ):
                value = (
                    value.get(
                        "evidence_id"
                    )
                    or value.get(
                        "id"
                    )
                    or ""
                )

            normalized = clean_text(
                value
            )

            if (
                normalized
                and normalized not in seen
            ):
                seen.add(
                    normalized
                )
                result.append(
                    normalized
                )

    return result


def item_text(
    item: Any,
) -> str:
    if isinstance(
        item,
        str,
    ):
        return clean_text(
            item
        )

    if not isinstance(
        item,
        Mapping,
    ):
        return clean_text(
            item
        )

    for field in (
        "text",
        "statement",
        "claim",
        "summary",
        "description",
        "insight",
        "development",
        "connection",
        "shift",
        "effect",
        "signal",
        "title",
    ):
        value = item.get(
            field
        )

        if (
            isinstance(
                value,
                str,
            )
            and clean_text(
                value
            )
        ):
            return clean_text(
                value
            )

    return ""


def insight_id_from_item(
    item: Any,
) -> str:
    if not isinstance(
        item,
        Mapping,
    ):
        return ""

    return clean_text(
        item.get(
            "insight_id"
        )
        or item.get(
            "id"
        )
    )


def insight_revision_id_from_item(
    item: Any,
) -> str:
    if not isinstance(
        item,
        Mapping,
    ):
        return ""

    return clean_text(
        item.get(
            "insight_revision_id"
        )
    )


def insight_event_ids(
    item: Any,
) -> list[str]:
    if not isinstance(
        item,
        Mapping,
    ):
        return []

    raw = (
        item.get(
            "event_ids"
        )
        or item.get(
            "source_event_ids"
        )
        or []
    )

    if isinstance(
        raw,
        str,
    ):
        raw = [
            raw
        ]

    if not isinstance(
        raw,
        (
            list,
            tuple,
        ),
    ):
        return []

    result: list[str] = []
    seen: set[str] = set()

    for value in raw:
        text = clean_text(
            value
        )

        if (
            text
            and text not in seen
        ):
            seen.add(
                text
            )
            result.append(
                text
            )

    return result


def insight_has_mechanism(
    item: Any,
) -> bool:
    if not isinstance(
        item,
        Mapping,
    ):
        return False

    explicit = item.get(
        "mechanism_present"
    )

    if isinstance(
        explicit,
        bool,
    ):
        return explicit

    mechanism = item.get(
        "mechanism"
    )

    if isinstance(
        mechanism,
        Mapping,
    ):
        return bool(
            clean_text(
                mechanism.get(
                    "description"
                )
            )
            or mechanism
        )

    return bool(
        clean_text(
            mechanism
        )
    )


def available_insights(
    report: Mapping[
        str,
        Any,
    ],
) -> list[
    dict[
        str,
        Any,
    ]
]:
    hidden = as_list(
        report_sections(
            report
        ).get(
            "hidden_insights"
        )
    )

    output: list[
        dict[
            str,
            Any,
        ]
    ] = []

    for item in hidden:
        insight_id = (
            insight_id_from_item(
                item
            )
        )

        if not insight_id:
            # Do not invent semantic INSIGHT-* IDs in UI.
            continue

        output.append(
            {
                "insight_id": (
                    insight_id
                ),
                "text": (
                    item_text(
                        item
                    )
                ),
            }
        )

    return output


# ---------------------------------------------------------------------------
# Local actor / session identity
# ---------------------------------------------------------------------------


def get_or_create_local_actor_id(
    path: Path = LOCAL_ACTOR_PATH,
) -> str:
    """
    Persistent pseudonymous local actor identity.

    This makes repeat usage / retention measurable
    without requiring an account in the local-first
    version of the product.
    """

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if path.exists():
        try:
            existing = clean_text(
                path.read_text(
                    encoding="utf-8"
                )
            )

            if existing:
                return existing

        except OSError:
            pass

    actor_id = (
        "LOCALUSER-"
        + uuid.uuid4().hex[
            :20
        ].upper()
    )

    try:
        path.write_text(
            actor_id,
            encoding="utf-8",
        )
    except OSError:
        # App still works, but cross-restart retention
        # cannot be measured from the fallback identity.
        pass

    return actor_id


def session_id() -> str:
    if (
        "dashboard_session_id"
        not in st.session_state
    ):
        st.session_state[
            "dashboard_session_id"
        ] = (
            "SESSION-"
            + uuid.uuid4().hex[
                :20
            ].upper()
        )

    return str(
        st.session_state[
            "dashboard_session_id"
        ]
    )


# ---------------------------------------------------------------------------
# Durable raw feedback journal
# ---------------------------------------------------------------------------


class LocalFeedbackJournal:
    """
    Durable local journal for explicit user feedback.

    Raw free-text feedback belongs here, NOT in
    aggregate telemetry.

    interpreted_json stores the current output from
    UserFeedbackService.

    Because raw_feedback is preserved, historical
    feedback can later be reprocessed through an
    LLM-based feedback interpreter.
    """

    def __init__(
        self,
        db_path: str = FEEDBACK_DB_PATH,
    ) -> None:
        self.db_path = (
            clean_text(
                db_path
            )
            or ":memory:"
        )

        if (
            self.db_path
            != ":memory:"
        ):
            Path(
                self.db_path
            ).parent.mkdir(
                parents=True,
                exist_ok=True,
            )

        self._db = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
        )

        self._db.row_factory = (
            sqlite3.Row
        )

        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_feedback_events(
                journal_no INTEGER PRIMARY KEY AUTOINCREMENT,
                feedback_id TEXT UNIQUE NOT NULL,
                actor_id TEXT NOT NULL,
                session_id TEXT NOT NULL,

                feedback_scope TEXT NOT NULL,

                report_id TEXT NOT NULL,
                report_revision_id TEXT NOT NULL,
                run_id TEXT NOT NULL,

                insight_id TEXT NOT NULL,
                insight_revision_id TEXT NOT NULL,

                rating_label TEXT NOT NULL,
                rating_numeric INTEGER,

                raw_feedback TEXT NOT NULL,
                interpreted_json TEXT NOT NULL,

                created_at TEXT NOT NULL
            )
            """
        )

        self._db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_feedback_actor_report
            ON user_feedback_events(
                actor_id,
                report_id,
                journal_no
            )
            """
        )

        self._db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_feedback_insight
            ON user_feedback_events(
                actor_id,
                insight_id,
                journal_no
            )
            """
        )

        self._db.commit()

    def append(
        self,
        *,
        feedback_event: Mapping[
            str,
            Any,
        ],
        actor_id: str,
        session_id_value: str,
        report_revision_id: str,
        insight_id: str = "",
        insight_revision_id: str = "",
        rating_label: str = "",
    ) -> dict[
        str,
        Any,
    ]:
        feedback_id = clean_text(
            feedback_event.get(
                "feedback_id"
            )
        )

        if not feedback_id:
            raise ValueError(
                "feedback_event is missing feedback_id"
            )

        rating = feedback_event.get(
            "rating"
        )

        if rating is not None:
            rating = int(
                rating
            )

        record = {
            "feedback_id": (
                feedback_id
            ),
            "actor_id": clean_text(
                actor_id
            ),
            "session_id": clean_text(
                session_id_value
            ),
            "feedback_scope": enum_text(
                feedback_event.get(
                    "feedback_scope"
                )
            ),
            "report_id": clean_text(
                feedback_event.get(
                    "target_report_id"
                )
            ),
            "report_revision_id": clean_text(
                report_revision_id
            ),
            "run_id": clean_text(
                feedback_event.get(
                    "target_run_id"
                )
            ),
            "insight_id": clean_text(
                insight_id
            ),
            "insight_revision_id": clean_text(
                insight_revision_id
            ),
            "rating_label": enum_text(
                rating_label
            ),
            "rating_numeric": (
                rating
            ),
            "raw_feedback": str(
                feedback_event.get(
                    "raw_feedback"
                )
                or ""
            ),
            "interpreted_json": (
                json.dumps(
                    json_safe(
                        dict(
                            feedback_event
                        )
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            ),
            "created_at": (
                clean_text(
                    feedback_event.get(
                        "recorded_at"
                    )
                )
                or utc_now_iso()
            ),
        }

        self._db.execute(
            """
            INSERT OR IGNORE INTO user_feedback_events(
                feedback_id,
                actor_id,
                session_id,
                feedback_scope,
                report_id,
                report_revision_id,
                run_id,
                insight_id,
                insight_revision_id,
                rating_label,
                rating_numeric,
                raw_feedback,
                interpreted_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record[
                    "feedback_id"
                ],
                record[
                    "actor_id"
                ],
                record[
                    "session_id"
                ],
                record[
                    "feedback_scope"
                ],
                record[
                    "report_id"
                ],
                record[
                    "report_revision_id"
                ],
                record[
                    "run_id"
                ],
                record[
                    "insight_id"
                ],
                record[
                    "insight_revision_id"
                ],
                record[
                    "rating_label"
                ],
                record[
                    "rating_numeric"
                ],
                record[
                    "raw_feedback"
                ],
                record[
                    "interpreted_json"
                ],
                record[
                    "created_at"
                ],
            ),
        )

        self._db.commit()

        return record

    def latest_rating(
        self,
        *,
        actor_id: str,
        report_id: str,
        feedback_scope: str,
        insight_id: str = "",
    ) -> str:
        """
        Latest explicit rating wins.

        This allows users to change their mind without
        treating every historical click as the current
        product-value judgement.
        """

        row = self._db.execute(
            """
            SELECT rating_label
            FROM user_feedback_events

            WHERE actor_id=?
              AND report_id=?
              AND feedback_scope=?
              AND insight_id=?
              AND rating_label<>''

            ORDER BY journal_no DESC
            LIMIT 1
            """,
            (
                clean_text(
                    actor_id
                ),
                clean_text(
                    report_id
                ),
                enum_text(
                    feedback_scope
                ),
                clean_text(
                    insight_id
                ),
            ),
        ).fetchone()

        if row is None:
            return ""

        return clean_text(
            row[
                "rating_label"
            ]
        )

    def close(
        self,
    ) -> None:
        self._db.close()


# ---------------------------------------------------------------------------
# Streamlit resources
# ---------------------------------------------------------------------------


@st.cache_resource(
    show_spinner=False
)
def get_report_service(
) -> ReportService:
    return ReportService(
        db_path=REPORT_DB_PATH
    )


@st.cache_resource(
    show_spinner=False
)
def get_telemetry_service(
) -> TelemetryService:
    return TelemetryService(
        db_path=TELEMETRY_DB_PATH
    )


@st.cache_resource(
    show_spinner=False
)
def get_feedback_service(
) -> UserFeedbackService:
    """
    Today:
        deterministic interpretation

    Later:
        inject a local LLM interpreter here.

    Example:

        UserFeedbackService(
            llm_interpreter=my_local_feedback_interpreter
        )

    The UI does not need to change.
    """

    return UserFeedbackService()


@st.cache_resource(
    show_spinner=False
)
def get_feedback_journal(
) -> LocalFeedbackJournal:
    return LocalFeedbackJournal(
        db_path=FEEDBACK_DB_PATH
    )


# ---------------------------------------------------------------------------
# Research-run and optional DecisionAgent integration
# ---------------------------------------------------------------------------


class ResearchRunnerUnavailableError(RuntimeError):
    """Raised when the UI cannot locate a compatible research orchestrator."""


class DecisionAgentUnavailableError(RuntimeError):
    """Raised when the optional DecisionAgent cannot be imported."""


def _missing_required_parameters(
    callable_obj: Any,
    supplied: Mapping[str, Any],
) -> list[str]:
    try:
        signature = inspect.signature(
            callable_obj
        )
    except (
        TypeError,
        ValueError,
    ):
        return []

    missing: list[str] = []

    for name, parameter in signature.parameters.items():
        if name in {
            "self",
            "cls",
        }:
            continue

        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue

        if (
            parameter.default
            is inspect.Parameter.empty
            and name not in supplied
        ):
            missing.append(
                name
            )

    return missing


def _call_research_callable(
    callable_obj: Any,
    *,
    research_question: str,
    research_depth: str,
) -> Any:
    """
    Call a project research runner without forcing the dashboard to own
    pipeline logic.

    Supported conventional parameter names are intentionally small and
    explicit. If a runner requires more arguments, the UI reports the missing
    contract instead of silently constructing a partial research pipeline.
    """
    try:
        signature = inspect.signature(
            callable_obj
        )
    except (
        TypeError,
        ValueError,
    ):
        return callable_obj(
            research_question,
            research_depth,
        )

    parameters = signature.parameters
    has_var_kwargs = any(
        parameter.kind
        == inspect.Parameter.VAR_KEYWORD
        for parameter
        in parameters.values()
    )

    kwargs: dict[
        str,
        Any,
    ] = {}

    question_names = (
        "research_question",
        "question",
        "topic",
        "query",
    )

    depth_names = (
        "research_depth",
        "depth",
        "profile",
    )

    for name in question_names:
        if name in parameters:
            kwargs[name] = (
                research_question
            )
            break

    for name in depth_names:
        if name in parameters:
            kwargs[name] = (
                research_depth
            )
            break

    if has_var_kwargs:
        kwargs.setdefault(
            "research_question",
            research_question,
        )
        kwargs.setdefault(
            "research_depth",
            research_depth,
        )

    if not any(
        name in kwargs
        for name in question_names
    ):
        positional = [
            parameter
            for parameter
            in parameters.values()
            if parameter.kind
            in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            }
            and parameter.name
            not in {
                "self",
                "cls",
            }
        ]

        if positional:
            args = [
                research_question
            ]

            if (
                len(
                    positional
                ) >= 2
                and positional[1].default
                is inspect.Parameter.empty
            ):
                args.append(
                    research_depth
                )

            return callable_obj(
                *args
            )

    missing = (
        _missing_required_parameters(
            callable_obj,
            kwargs,
        )
    )

    if missing:
        raise ResearchRunnerUnavailableError(
            "A research runner was found, but its UI contract "
            "requires additional arguments: "
            + ", ".join(
                missing
            )
            + ". Keep orchestration dependencies inside the runner "
            "and expose run_research(research_question, research_depth)."
        )

    return callable_obj(
        **kwargs
    )


def _research_runner_candidates(
) -> list[
    tuple[
        str,
        str,
    ]
]:
    return [
        (
            "services.research_run_service",
            "ResearchRunService",
        ),
        (
            "services.research_orchestrator",
            "ResearchOrchestrator",
        ),
        (
            "research_run_service",
            "ResearchRunService",
        ),
        (
            "research_orchestrator",
            "ResearchOrchestrator",
        ),
        (
            "orchestrator",
            "ResearchOrchestrator",
        ),
    ]


def _run_research_via_existing_orchestrator(
    *,
    research_question: str,
    research_depth: str,
) -> Any:
    """
    Invoke the canonical project orchestrator directly.

    The project's source of truth is:
        services.research_orchestrator_service.ResearchOrchestratorService

    Do not use speculative module discovery here. If the canonical orchestrator
    cannot be imported or constructed, surface the underlying exception so the
    actual dependency problem is visible.
    """
    question = clean_text(research_question)
    depth = clean_text(research_depth).casefold() or "fast"

    if not question:
        raise ValueError("research_question cannot be empty.")

    if depth not in {"fast", "deep"}:
        raise ValueError(
            "research_depth must be 'fast' or 'deep'. "
            f"Received: {research_depth!r}"
        )

    try:
        module = importlib.import_module(
            "services.research_orchestrator_service"
        )
    except Exception as exc:
        raise ResearchRunnerUnavailableError(
            "Could not import canonical orchestrator "
            "services.research_orchestrator_service: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    runner_class = getattr(
        module,
        "ResearchOrchestratorService",
        None,
    )

    if runner_class is None:
        raise ResearchRunnerUnavailableError(
            "services.research_orchestrator_service imported, but "
            "ResearchOrchestratorService is missing."
        )

    try:
        runner = runner_class()
    except Exception as exc:
        raise ResearchRunnerUnavailableError(
            "ResearchOrchestratorService constructor failed: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    run_method = getattr(runner, "run", None)
    if not callable(run_method):
        raise ResearchRunnerUnavailableError(
            "ResearchOrchestratorService exists but has no callable run(...)."
        )

    logger.info(
        "Dashboard research submission | question=%s requested_depth=%s",
        question,
        depth,
    )

    result = run_method(
        topic=question,
        research_depth=depth,
    )

    if isinstance(result, Mapping):
        actual_depth = clean_text(
            result.get("research_depth")
            or result.get("requested_research_depth")
        ).casefold()
        if actual_depth and actual_depth != depth:
            raise ResearchRunnerUnavailableError(
                "Research depth mismatch after orchestrator run: "
                f"requested={depth!r}, returned={actual_depth!r}."
            )

        depth_integrity = result.get("depth_integrity")
        if isinstance(depth_integrity, Mapping):
            if depth == "deep" and not bool(depth_integrity.get("ok")):
                raise ResearchRunnerUnavailableError(
                    "Deep research failed internal depth-integrity checks: "
                    + clean_text(depth_integrity)
                )

    return result

def _extract_report_id_from_run_result(
    value: Any,
) -> str:
    if isinstance(
        value,
        Mapping,
    ):
        direct = clean_text(
            value.get(
                "report_id"
            )
            or value.get(
                "released_report_id"
            )
        )

        if direct:
            return direct

        for key in (
            "terminal_outcome",
            "report",
            "released_report",
            "result",
            "output",
        ):
            nested = value.get(
                key
            )

            nested_id = (
                _extract_report_id_from_run_result(
                    nested
                )
            )

            if nested_id:
                return nested_id

    for attribute in (
        "report_id",
        "released_report_id",
    ):
        candidate = clean_text(
            getattr(
                value,
                attribute,
                "",
            )
        )

        if candidate:
            return candidate

    return ""


@st.cache_resource(
    show_spinner=False
)
def get_decision_agent(
) -> Any:
    candidates = [
        (
            "agents.decision_agent",
            "DecisionAgent",
        ),
        (
            "agents.decision_agent_final",
            "DecisionAgent",
        ),
        (
            "decision_agent",
            "DecisionAgent",
        ),
        (
            "decision_agent_final",
            "DecisionAgent",
        ),
    ]

    errors: list[
        str
    ] = []

    for module_name, class_name in candidates:
        try:
            module = importlib.import_module(
                module_name
            )
            agent_class = getattr(
                module,
                class_name,
            )
            return agent_class()
        except Exception as exc:
            errors.append(
                f"{module_name}: "
                f"{type(exc).__name__}"
            )

    raise DecisionAgentUnavailableError(
        "DecisionAgent could not be imported. Expected it at "
        "agents/decision_agent.py (preferred) or one of the supported fallback "
        "module names."
    )


def _render_decision_list(
    heading: str,
    values: Any,
    *,
    primary_field: str,
) -> None:
    items = as_list(
        values
    )

    if not items:
        return

    st.markdown(
        f"#### {heading}"
    )

    for item in items:
        if isinstance(
            item,
            Mapping,
        ):
            primary = clean_text(
                item.get(
                    primary_field
                )
                or item.get(
                    "statement"
                )
                or item.get(
                    "text"
                )
            )

            if primary:
                st.markdown(
                    f"- {primary}"
                )

            why = clean_text(
                item.get(
                    "why_it_matters"
                )
            )

            action = clean_text(
                item.get(
                    "action_if_observed"
                )
            )

            if why:
                st.caption(
                    "Why it matters: "
                    + why
                )

            if action:
                st.caption(
                    "If observed: "
                    + action
                )

            render_evidence_refs(
                evidence_ids(
                    item
                )
            )
        else:
            text = clean_text(
                item
            )

            if text:
                st.markdown(
                    f"- {text}"
                )


def render_decision_support(
    result: Mapping[
        str,
        Any,
    ],
) -> None:
    assessment = result.get(
        "decision_assessment"
    )

    if isinstance(
        assessment,
        Mapping,
    ):
        st.markdown(
            "#### Decision assessment"
        )

        preferred = clean_text(
            assessment.get(
                "preferred_option_id"
            )
        )

        support = clean_text(
            assessment.get(
                "support_level"
            )
        )

        if preferred:
            st.write(
                "**Preferred option:** "
                + preferred
            )

        if support:
            st.caption(
                "Research support: "
                + support.replace(
                    "_",
                    " ",
                ).title()
            )

        assessment_text = clean_text(
            assessment.get(
                "assessment"
            )
        )

        why = clean_text(
            assessment.get(
                "why"
            )
        )

        if assessment_text:
            st.write(
                assessment_text
            )

        if why:
            st.write(
                "**Why:** "
                + why
            )

        render_evidence_refs(
            evidence_ids(
                assessment
            )
        )

        conditions = as_list(
            assessment.get(
                "conditions_that_would_change_the_assessment"
            )
        )

        if conditions:
            st.write(
                "**What could change the assessment:**"
            )

            for condition in conditions:
                text = clean_text(
                    condition
                )

                if text:
                    st.markdown(
                        f"- {text}"
                    )

        delay_reason = clean_text(
            assessment.get(
                "reason_to_delay_or_pilot"
            )
        )

        if delay_reason:
            st.info(
                "Reason to delay or pilot: "
                + delay_reason
            )

    options = as_list(
        result.get(
            "options"
        )
    )

    if options:
        st.markdown(
            "#### Options"
        )

        for index, option in enumerate(
            options,
            start=1,
        ):
            if not isinstance(
                option,
                Mapping,
            ):
                continue

            option_id = clean_text(
                option.get(
                    "option_id"
                )
            )

            option_name = clean_text(
                option.get(
                    "option"
                )
                or option.get(
                    "description"
                )
            )

            label = (
                f"{option_id} — {option_name}"
                if option_id
                and option_name
                else option_name
                or option_id
                or f"Option {index}"
            )

            with st.expander(
                label
            ):
                description = clean_text(
                    option.get(
                        "description"
                    )
                )

                if (
                    description
                    and description
                    != option_name
                ):
                    st.write(
                        description
                    )

                fit = clean_text(
                    option.get(
                        "fit_summary"
                    )
                )

                if fit:
                    st.write(
                        "**Fit:** "
                        + fit
                    )

                reversibility = clean_text(
                    option.get(
                        "reversibility"
                    )
                )

                if reversibility:
                    st.caption(
                        "Reversibility: "
                        + reversibility.title()
                    )

                for section_name, key in (
                    (
                        "Advantages",
                        "advantages",
                    ),
                    (
                        "Risks",
                        "risks",
                    ),
                    (
                        "Trade-offs",
                        "trade_offs",
                    ),
                    (
                        "Dependencies",
                        "dependencies",
                    ),
                    (
                        "Unknowns",
                        "unknowns",
                    ),
                ):
                    section_values = as_list(
                        option.get(
                            key
                        )
                    )

                    if not section_values:
                        continue

                    st.write(
                        f"**{section_name}:**"
                    )

                    for section_item in section_values:
                        if isinstance(
                            section_item,
                            Mapping,
                        ):
                            text = clean_text(
                                section_item.get(
                                    "statement"
                                )
                                or section_item.get(
                                    "text"
                                )
                            )
                        else:
                            text = clean_text(
                                section_item
                            )

                        if text:
                            st.markdown(
                                f"- {text}"
                            )

                        if isinstance(
                            section_item,
                            Mapping,
                        ):
                            render_evidence_refs(
                                evidence_ids(
                                    section_item
                                )
                            )

    _render_decision_list(
        "Decision tensions",
        result.get(
            "decision_tensions"
        ),
        primary_field="tension",
    )

    _render_decision_list(
        "Decision triggers",
        result.get(
            "decision_triggers"
        ),
        primary_field="signal",
    )

    _render_decision_list(
        "Information to gather",
        result.get(
            "information_to_gather"
        ),
        primary_field="question",
    )


def render_decision_agent_panel(
    *,
    report: Mapping[
        str,
        Any,
    ],
) -> None:
    report_id = clean_text(
        report.get(
            "report_id"
        )
    )

    st.divider()
    st.subheader(
        "Optional Decision Agent"
    )

    st.caption(
        "Ask a decision-specific question using only this selected released "
        "research report and your question. This does not run new research or "
        "add external evidence."
    )

    form_key = (
        "decision_agent_form::"
        + report_id
    )

    with st.form(
        key=form_key,
        clear_on_submit=False,
    ):
        decision_question = st.text_area(
            "What decision do you want help with?",
            placeholder=(
                "For example: Given this research, should an Indian "
                "data-centre operator accelerate capacity expansion over "
                "the next two years?"
            ),
            height=110,
            key=(
                "decision_question::"
                + report_id
            ),
        )

        ask_decision_agent = (
            st.form_submit_button(
                "Ask Decision Agent"
            )
        )

    result_key = (
        "decision_result::"
        + report_id
    )

    if ask_decision_agent:
        question = clean_text(
            decision_question
        )

        if not question:
            st.warning(
                "Enter a decision question first."
            )
        else:
            try:
                agent = get_decision_agent()

                with st.spinner(
                    "Applying the Decision Agent to this released report..."
                ):
                    result = agent.analyze(
                        research_report=dict(
                            report
                        ),
                        decision_context=question,
                    )

            except DecisionAgentUnavailableError as exc:
                st.error(
                    clean_text(
                        exc
                    )
                )

            except Exception as exc:
                st.error(
                    "Decision Agent failed: "
                    + type(
                        exc
                    ).__name__
                    + ": "
                    + clean_text(
                        exc
                    )
                )

            else:
                if isinstance(
                    result,
                    Mapping,
                ):
                    st.session_state[
                        result_key
                    ] = dict(
                        result
                    )
                else:
                    st.session_state[
                        result_key
                    ] = {
                        "decision_assessment": {
                            "assessment": clean_text(
                                result
                            )
                        }
                    }

    saved_result = st.session_state.get(
        result_key
    )

    if isinstance(
        saved_result,
        Mapping,
    ):
        render_decision_support(
            saved_result
        )


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


def ui_telemetry_event_id(
    *,
    event_type: str,
    actor_id: str,
    session_id_value: str,
    report_id: str,
    report_revision_id: str,
    semantic_key: str = "",
) -> str:
    return stable_id(
        "TELEMUI",
        {
            "dashboard_version": (
                DASHBOARD_VERSION
            ),
            "event_type": enum_text(
                event_type
            ),
            "actor_id": (
                actor_id
            ),
            "session_id": (
                session_id_value
            ),
            "report_id": (
                report_id
            ),
            "report_revision_id": (
                report_revision_id
            ),
            "semantic_key": (
                semantic_key
            ),
        },
    )


def record_ui_event(
    telemetry: TelemetryService,
    *,
    event_type: str,
    report: Mapping[
        str,
        Any,
    ],
    actor_id: str,
    session_id_value: str,
    dimensions: Optional[
        Mapping[
            str,
            Any,
        ]
    ] = None,
    semantic_key: str = "",
    once_per_session: bool = False,
) -> None:
    report_id = clean_text(
        report.get(
            "report_id"
        )
    )

    revision_id = clean_text(
        report.get(
            "report_revision_id"
        )
    )

    run_id = (
        clean_text(
            report_value(
                report,
                "run_id",
            )
        )
        or report_id
        or "UI"
    )

    event_id = (
        ui_telemetry_event_id(
            event_type=event_type,
            actor_id=actor_id,
            session_id_value=(
                session_id_value
            ),
            report_id=report_id,
            report_revision_id=(
                revision_id
            ),
            semantic_key=(
                semantic_key
            ),
        )
    )

    state_key = (
        f"telemetry_once::{event_id}"
    )

    if (
        once_per_session
        and st.session_state.get(
            state_key
        )
    ):
        return

    payload = {
        "dashboard_version": (
            DASHBOARD_VERSION
        ),
        "actor_id": (
            actor_id
        ),
        "session_id": (
            session_id_value
        ),
        "report_id": (
            report_id
        ),
        "report_revision_id": (
            revision_id
        ),
        **dict(
            dimensions
            or {}
        ),
    }

    try:
        telemetry.record_event(
            run_id=run_id,
            stage="product_ui",
            event_type=event_type,
            dimensions=payload,
            event_id=event_id,
        )

    except Exception as exc:
        # Duplicate deterministic impressions are harmless.
        # Other telemetry problems should not break report use.
        if (
            "UNIQUE"
            not in str(
                exc
            ).upper()
        ):
            st.sidebar.warning(
                "Telemetry warning: "
                + clean_text(
                    exc
                )
            )

    else:
        if once_per_session:
            st.session_state[
                state_key
            ] = True


# ---------------------------------------------------------------------------
# Feedback submission
# ---------------------------------------------------------------------------


def submit_feedback_event(
    *,
    report: Mapping[
        str,
        Any,
    ],
    actor_id: str,
    session_id_value: str,
    feedback_service: UserFeedbackService,
    feedback_journal: LocalFeedbackJournal,
    telemetry: TelemetryService,
    feedback_scope: str,
    feedback_text: str = "",
    rating_label: str = "",
    insight_item: Optional[
        Mapping[
            str,
            Any,
        ]
    ] = None,
) -> dict[
    str,
    Any,
]:
    report_id = clean_text(
        report.get(
            "report_id"
        )
    )

    report_revision_id = (
        clean_text(
            report.get(
                "report_revision_id"
            )
        )
    )

    run_id = clean_text(
        report_value(
            report,
            "run_id",
        )
    )

    insight_id = (
        insight_id_from_item(
            insight_item
            or {}
        )
    )

    insight_revision_id = (
        insight_revision_id_from_item(
            insight_item
            or {}
        )
    )

    rating_label = enum_text(
        rating_label
    )

    if rating_label not in {
        "",
        "like",
        "dislike",
    }:
        raise ValueError(
            "rating_label must be "
            "like/dislike/empty"
        )

    # UserFeedbackService currently accepts 1–5.
    # UI remains intentionally binary/simple.
    rating_numeric: Optional[
        int
    ]

    if rating_label == "like":
        rating_numeric = 5

    elif rating_label == "dislike":
        rating_numeric = 1

    else:
        rating_numeric = None

    target_ids = (
        [
            insight_id
        ]
        if insight_id
        else []
    )

    feedback_event = (
        feedback_service.interpret(
            feedback_text,
            feedback_scope=(
                feedback_scope
            ),
            report_id=report_id,
            run_id=run_id,
            target_insight_ids=(
                target_ids
            ),
            available_insights=(
                available_insights(
                    report
                )
            ),
            rating=rating_numeric,
            event_nonce=(
                uuid.uuid4().hex
            ),
        )
    )

    # Raw text is persisted here.
    feedback_journal.append(
        feedback_event=(
            feedback_event
        ),
        actor_id=actor_id,
        session_id_value=(
            session_id_value
        ),
        report_revision_id=(
            report_revision_id
        ),
        insight_id=insight_id,
        insight_revision_id=(
            insight_revision_id
        ),
        rating_label=(
            rating_label
        ),
    )

    if (
        feedback_scope
        == "insight"
    ):
        telemetry_event_type = (
            "insight_feedback"
        )

    elif rating_label:
        telemetry_event_type = (
            "report_feedback"
        )

    else:
        telemetry_event_type = (
            "feedback_submitted"
        )

    telemetry_dimensions = {
        "feedback_id": clean_text(
            feedback_event.get(
                "feedback_id"
            )
        ),
        "feedback_scope": enum_text(
            feedback_scope
        ),
        "insight_id": (
            insight_id
        ),
        "insight_revision_id": (
            insight_revision_id
        ),
        "rating_label": (
            rating_label
        ),
        "useful": (
            True
            if rating_label
            == "like"
            else (
                False
                if rating_label
                == "dislike"
                else None
            )
        ),
        "sentiment": clean_text(
            feedback_event.get(
                "sentiment"
            )
        ),
        "feedback_types": list(
            feedback_event.get(
                "feedback_types"
            )
            or []
        ),

        # Only metadata about written feedback.
        # Never raw text.
        "feedback_text_length": len(
            feedback_text
        ),
    }

    record_ui_event(
        telemetry,
        event_type=(
            telemetry_event_type
        ),
        report=report,
        actor_id=actor_id,
        session_id_value=(
            session_id_value
        ),
        dimensions=(
            telemetry_dimensions
        ),
        semantic_key=clean_text(
            feedback_event.get(
                "feedback_id"
            )
        ),
        once_per_session=False,
    )

    return feedback_event


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def render_evidence_refs(
    ids: Iterable[str],
) -> None:
    values = [
        clean_text(
            value
        )
        for value in ids
        if clean_text(
            value
        )
    ]

    if values:
        st.caption(
            "Evidence: "
            + " · ".join(
                f"`{value}`"
                for value in values
            )
        )


def render_structured_item(
    item: Any,
    *,
    numbered: bool = False,
    index: int | None = None,
) -> None:
    if isinstance(
        item,
        str,
    ):
        text = clean_text(
            item
        )

        if text:
            if (
                numbered
                and index
                is not None
            ):
                st.markdown(
                    f"**{index}.** {text}"
                )
            else:
                st.markdown(
                    f"- {text}"
                )

        return

    if not isinstance(
        item,
        Mapping,
    ):
        text = clean_text(
            item
        )

        if text:
            st.write(
                text
            )

        return

    text = item_text(
        item
    )

    if text:
        if (
            numbered
            and index
            is not None
        ):
            st.markdown(
                f"**{index}. {text}**"
            )
        else:
            st.markdown(
                f"**{text}**"
            )

    metadata_parts: list[
        str
    ] = []

    epistemic_type = clean_text(
        item.get(
            "epistemic_type"
        )
        or item.get(
            "insight_type"
        )
    )

    support_level = clean_text(
        item.get(
            "support_level"
        )
        or item.get(
            "epistemic_status"
        )
    )

    prospective_status = (
        clean_text(
            item.get(
                "prospective_status"
            )
        )
    )

    if epistemic_type:
        metadata_parts.append(
            "Type: "
            + epistemic_type
            .replace(
                "_",
                " ",
            )
            .title()
        )

    if support_level:
        metadata_parts.append(
            "Support: "
            + support_level
            .replace(
                "_",
                " ",
            )
            .title()
        )

    if prospective_status:
        metadata_parts.append(
            "Prospective status: "
            + prospective_status
            .replace(
                "_",
                " ",
            )
            .title()
        )

    if metadata_parts:
        st.caption(
            " · ".join(
                metadata_parts
            )
        )

    mechanism = item.get(
        "mechanism"
    )

    if isinstance(
        mechanism,
        Mapping,
    ):
        mechanism_text = (
            clean_text(
                mechanism.get(
                    "description"
                )
                or mechanism.get(
                    "text"
                )
            )
        )
    else:
        mechanism_text = (
            clean_text(
                mechanism
            )
        )

    if (
        mechanism_text
        and mechanism_text
        != text
    ):
        st.write(
            "**Mechanism:** "
            + mechanism_text
        )

    structural_change = (
        clean_text(
            item.get(
                "structural_change"
            )
        )
    )

    if structural_change:
        st.write(
            "**Structural change:** "
            + structural_change
        )

    second_order = clean_text(
        item.get(
            "second_order_effect"
        )
        or item.get(
            "second_order_implication"
        )
    )

    if second_order:
        st.write(
            "**Second-order effect:** "
            + second_order
        )

    counter_signals = (
        item.get(
            "counter_signals"
        )
        or item.get(
            "counterevidence"
        )
        or item.get(
            "counterevidence_ids"
        )
    )

    if counter_signals:
        st.write(
            "**Counterevidence / "
            "counter-signal:**"
        )

        for counter in as_list(
            counter_signals
        ):
            text_value = (
                item_text(
                    counter
                )
                or clean_text(
                    counter
                )
            )

            if text_value:
                st.markdown(
                    f"- {text_value}"
                )

    unknowns = (
        item.get(
            "critical_unknowns"
        )
        or item.get(
            "unknowns"
        )
    )

    if unknowns:
        st.write(
            "**Critical unknowns:**"
        )

        for unknown in as_list(
            unknowns
        ):
            text_value = (
                item_text(
                    unknown
                )
                or clean_text(
                    unknown
                )
            )

            if text_value:
                st.markdown(
                    f"- {text_value}"
                )

    render_evidence_refs(
        evidence_ids(
            item
        )
    )


def render_item_list(
    items: Any,
    *,
    empty_message: str,
) -> None:
    values = as_list(
        items
    )

    if not values:
        st.caption(
            empty_message
        )
        return

    for index, item in enumerate(
        values,
        start=1,
    ):
        with st.container(
            border=True
        ):
            render_structured_item(
                item,
                numbered=True,
                index=index,
            )


# ---------------------------------------------------------------------------
# Insight feedback
# ---------------------------------------------------------------------------


def render_insight_feedback_controls(
    *,
    report: Mapping[
        str,
        Any,
    ],
    insight_item: Mapping[
        str,
        Any,
    ],
    actor_id: str,
    session_id_value: str,
    feedback_service: UserFeedbackService,
    feedback_journal: LocalFeedbackJournal,
    telemetry: TelemetryService,
) -> None:
    insight_id = (
        insight_id_from_item(
            insight_item
        )
    )

    if not insight_id:
        # Final report contract should contain INSIGHT-*.
        # Do not invent IDs in UI.
        return

    # Impression is recorded only once per session.
    record_ui_event(
        telemetry,
        event_type="insight_shown",
        report=report,
        actor_id=actor_id,
        session_id_value=(
            session_id_value
        ),
        dimensions={
            "insight_id": (
                insight_id
            ),
            "insight_revision_id": (
                insight_revision_id_from_item(
                    insight_item
                )
            ),
            "event_ids": (
                insight_event_ids(
                    insight_item
                )
            ),
            "mechanism_present": (
                insight_has_mechanism(
                    insight_item
                )
            ),

            # This is system placement/classification,
            # not user judgement.
            "non_obvious": True,
        },
        semantic_key=insight_id,
        once_per_session=True,
    )

    report_id = clean_text(
        report.get(
            "report_id"
        )
    )

    latest = (
        feedback_journal
        .latest_rating(
            actor_id=actor_id,
            report_id=report_id,
            feedback_scope=(
                "insight"
            ),
            insight_id=insight_id,
        )
    )

    (
        col_like,
        col_dislike,
        col_status,
    ) = st.columns(
        [
            1,
            1,
            5,
        ]
    )

    with col_like:
        if st.button(
            "👍",
            key=(
                "insight_like::"
                f"{report_id}::"
                f"{insight_id}"
            ),
        ):
            submit_feedback_event(
                report=report,
                actor_id=actor_id,
                session_id_value=(
                    session_id_value
                ),
                feedback_service=(
                    feedback_service
                ),
                feedback_journal=(
                    feedback_journal
                ),
                telemetry=(
                    telemetry
                ),
                feedback_scope=(
                    "insight"
                ),
                rating_label="like",
                insight_item=(
                    insight_item
                ),
            )

            st.rerun()

    with col_dislike:
        if st.button(
            "👎",
            key=(
                "insight_dislike::"
                f"{report_id}::"
                f"{insight_id}"
            ),
        ):
            submit_feedback_event(
                report=report,
                actor_id=actor_id,
                session_id_value=(
                    session_id_value
                ),
                feedback_service=(
                    feedback_service
                ),
                feedback_journal=(
                    feedback_journal
                ),
                telemetry=(
                    telemetry
                ),
                feedback_scope=(
                    "insight"
                ),
                rating_label=(
                    "dislike"
                ),
                insight_item=(
                    insight_item
                ),
            )

            st.rerun()

    with col_status:
        if latest == "like":
            st.caption(
                "You marked this insight useful."
            )

        elif latest == "dislike":
            st.caption(
                "You marked this insight not useful."
            )

        else:
            st.caption(
                "Was this insight useful?"
            )


def key_assumption_lines(item: Mapping[str, Any]) -> tuple[str, list[str]]:
    """(confidence line, assumption texts) for a released insight (v6.21)."""
    confidence = item.get("analytic_confidence") if isinstance(item.get("analytic_confidence"), Mapping) else {}
    level = clean_text(confidence.get("level"))
    line = ""
    if level:
        line = f"**Confidence: {level}.** " + clean_text(confidence.get("basis"))
    assumptions = [clean_text(a.get("text") if isinstance(a, Mapping) else a)
                   for a in as_list(item.get("key_assumptions"))]
    return line, [a for a in assumptions if a]


def actor_moves_markdown(section: Mapping[str, Any]) -> list[str]:
    """v6.27: per actor - situation, leverage, rules and incentives, next steps (with sources)."""
    lines: list[str] = []
    refs = section.get("sources") if isinstance(section.get("sources"), Mapping) else {}

    def cites(item: Mapping[str, Any]) -> str:
        out = []
        for source in as_list(item.get("sources")):
            if not isinstance(source, Mapping):
                continue
            ref = refs.get(clean_text(source.get("source_id"))) if isinstance(refs, Mapping) else None
            url = clean_text((ref or {}).get("url")) if isinstance(ref, Mapping) else ""
            label = clean_text(source.get("fact_id"))
            out.append(f"[{label}]({url})" if url.startswith(("http://", "https://")) else label)
        return (" (" + ", ".join(out) + ")") if out else ""

    for actor in as_list(section.get("actors")):
        if not isinstance(actor, Mapping):
            continue
        lines.append(f"##### {_markdown_safe(actor.get('actor'))}")
        for item in as_list(actor.get("situation")):
            if isinstance(item, Mapping):
                lines.append("- " + _markdown_safe(item.get("text")) + cites(item))
        leverage = actor.get("leverage") if isinstance(actor.get("leverage"), Mapping) else {}
        if clean_text(leverage.get("text")):
            lines.append("**Leverage:** " + _markdown_safe(leverage.get("text")) + cites(leverage))
        for item in as_list(actor.get("rules_and_incentives")):
            if isinstance(item, Mapping):
                lines.append("**Rule / incentive:** " + _markdown_safe(item.get("text")) + cites(item))
        lines.append("**Next steps that would benefit it** (analysis, not reported fact):")
        for n, move in enumerate(as_list(actor.get("moves")), start=1):
            if not isinstance(move, Mapping):
                continue
            basis = {"precedent": "Precedent", "leverage": "Leverage", "incentive": "Rules & incentives"}.get(
                clean_text(move.get("basis")), "")
            lines.append(f"{n}. **{_markdown_safe(move.get('move'))}**" + (f" *({basis})*" if basis else "")
                         + f" - {_markdown_safe(move.get('why_it_benefits'))}" + cites(move))
            if clean_text(move.get("risk")):
                lines.append(f"   - Risk: {_markdown_safe(move.get('risk'))}")
            if clean_text(move.get("watch_for")):
                lines.append(f"   - Watch for: {_markdown_safe(move.get('watch_for'))}")
        expected = [m for m in as_list(actor.get("expected_moves")) if isinstance(m, Mapping)]
        if expected:
            lines.append("*Widely expected (headline readers or published experts already expect these):* "
                         + "; ".join(_markdown_safe(m.get("move")) for m in expected[:2]))
    return lines


def render_actor_moves(sections: Mapping[str, Any]) -> None:
    section = sections.get("actor_moves") if isinstance(sections, Mapping) else None
    if not isinstance(section, Mapping):
        return
    st.markdown("#### What each actor should do next")
    lines = actor_moves_markdown(section)
    if lines:
        st.caption("For each main actor: its situation, leverage and the rules or incentives in the sources, and the "
                   "next steps they make advantageous. Every fact is checked against its source; the steps are analysis.")
        st.markdown("\n".join(lines))
    else:
        st.caption("No next step passed its evidence checks this run.")


def expert_commentary_lines(briefing: Mapping[str, Any]) -> list[str]:
    """v6.22: published expert analysis, quoted with its source and date."""
    lines = []
    for item in as_list(briefing.get("expert_commentary")):
        if not isinstance(item, Mapping) or not clean_text(item.get("text")):
            continue
        url = clean_text(item.get("source_url"))
        date = clean_text(item.get("published_at"))
        tail = (f" ({date})" if date else "") + (f" [source]({url})" if url.startswith(("http://", "https://")) else "")
        lines.append("- \u201c" + _markdown_safe(item.get("text")) + "\u201d" + tail)
    return lines


def render_expert_commentary(briefing: Mapping[str, Any]) -> None:
    lines = expert_commentary_lines(briefing)
    if lines:
        st.markdown("#### What experts are already saying")
        st.caption("Published analysis of these developments, quoted. The hidden insight below must go beyond this.")
        for line in lines:
            st.markdown(line)


ROUTE_NOTES = {  # v6.21.8
    "constraint": ("**How this was derived:** a cited rule, deadline, dependency or number, applied to this "
                   "development; the development's own reports do not say this."),
    "cross_development": ("**How this was derived:** it follows from combining different developments; "
                          "no single one of them says this."),
}
FRONTIER_NOTE = ("Checked against published expert analysis of these developments "  # v6.22
                 "({n} quoted passages): not already said there.")


def evidence_table_markdown(table: Mapping[str, Any]) -> tuple[str, list[str], list[str]]:
    """v6.23: (summary line, claim rows as markdown, against lines) for a released insight."""
    summary = table.get("summary") if isinstance(table.get("summary"), Mapping) else {}
    line = ""
    if summary:
        line = (f"**Evidence:** {summary.get('records_for', 0)} records from {summary.get('publishers', 0)} "
                f"publishers back this; {summary.get('records_against', 0)} point the other way. "
                f"{summary.get('stated', 0)} statements are stated by sources, {summary.get('inferred', 0)} follow "
                f"from them, {summary.get('assumptions', 0)} are assumptions.")
    rows = ["| Claim | Type | How it is backed | Sources |", "|---|---|---|---|"]
    for row in as_list(table.get("rows")):
        if not isinstance(row, Mapping):
            continue
        cites = []
        for source in as_list(row.get("sources")):
            if not isinstance(source, Mapping):
                continue
            label = _markdown_safe(source.get("publisher") or "source") + (
                f", {clean_text(source.get('date'))}" if clean_text(source.get("date")) else "")
            url = clean_text(source.get("url"))
            cites.append(f"[{label}]({url})" if url.startswith(("http://", "https://")) else label)
        cell = lambda v: _markdown_safe(v).replace("|", "/")  # noqa: E731
        rows.append(f"| {cell(row.get('claim'))} | {cell(row.get('role'))} | {cell(row.get('status'))} | "
                    + ("; ".join(cites) or "-") + " |")
    against = []
    for source in as_list(table.get("against")):
        if isinstance(source, Mapping) and clean_text(source.get("quote")):
            url = clean_text(source.get("url"))
            against.append("- \u201c" + _markdown_safe(source.get("quote")) + "\u201d "
                           + (f"([{_markdown_safe(source.get('publisher'))}]({url}))"
                              if url.startswith(("http://", "https://")) else ""))
    return line, (rows if len(rows) > 2 else []), against


def render_evidence_table(item: Mapping[str, Any]) -> None:
    table = item.get("evidence_table") if isinstance(item.get("evidence_table"), Mapping) else {}
    line, rows, against = evidence_table_markdown(table)
    if line:
        st.markdown(line)
    if rows:
        # Rendered inside the "Why we think this" expander: Streamlit does not allow nested expanders.
        st.markdown("**Claims and the evidence behind each**")
        st.markdown("\n".join(rows))
        quotes = [s for r in as_list(table.get("rows")) if isinstance(r, Mapping)
                  for s in as_list(r.get("sources")) if isinstance(s, Mapping) and clean_text(s.get("quote"))]
        if quotes:
            st.markdown("**Quoted passages**")
            seen = set()
            for source in quotes:
                if source.get("evidence_id") in seen:
                    continue
                seen.add(source.get("evidence_id"))
                st.markdown("- \u201c" + _markdown_safe(source.get("quote")) + "\u201d \u2014 "
                            + _markdown_safe(source.get("publisher")))
    if against:
        st.markdown("**Evidence pointing the other way**")
        for text in against:
            st.markdown(text)


def leverage_lines(item: Mapping[str, Any]) -> list[str]:
    """v6.26: the core analysis - who holds what, who needs it, incentives, predicted move."""
    lev = item.get("leverage_analysis") if isinstance(item.get("leverage_analysis"), Mapping) else {}
    if not clean_text(lev.get("holder")):
        return []
    lines = [f"**Leverage:** {_markdown_safe(lev.get('holder'))} controls {_markdown_safe(lev.get('controls'))}, "
             f"which {_markdown_safe(lev.get('dependent'))} depends on."]
    if clean_text(lev.get("holder_incentive")):
        lines.append(f"**{_markdown_safe(lev.get('holder'))}'s incentive:** {_markdown_safe(lev.get('holder_incentive'))}")
    if clean_text(lev.get("dependent_incentive")):
        lines.append(f"**{_markdown_safe(lev.get('dependent'))}'s incentive:** "
                     f"{_markdown_safe(lev.get('dependent_incentive'))}")
    if clean_text(lev.get("predicted_move")):
        lines.append(f"**Therefore:** {_markdown_safe(lev.get('predicted_move'))}")
    return lines


def render_key_assumptions(item: Mapping[str, Any]) -> None:
    for line in leverage_lines(item):
        st.markdown(line)
    route = clean_text(item.get("insight_route"))
    if route in ROUTE_NOTES:
        st.markdown(ROUTE_NOTES[route])
    check = item.get("counterevidence_check") if isinstance(item.get("counterevidence_check"), Mapping) else {}
    if clean_text(check.get("note")):
        # v6.25: shown, not hidden, when the contrary-evidence search could not finish.
        st.markdown("**Counter-evidence check incomplete:** " + _markdown_safe(check.get("note")))
    render_evidence_table(item)
    checked = item.get("expert_commentary_checked")
    if isinstance(checked, int) and checked > 0:
        st.caption(FRONTIER_NOTE.format(n=checked))
    line, assumptions = key_assumption_lines(item)
    if line:
        st.markdown(line)
    if assumptions:
        st.markdown("**Key assumptions** (analysis, not stated by any source; "
                    "the judgment is weaker if one fails):")
        for text in assumptions:
            st.markdown("- " + text)


def render_hidden_insights(
    *,
    report: Mapping[
        str,
        Any,
    ],
    items: Any,
    actor_id: str,
    session_id_value: str,
    feedback_service: UserFeedbackService,
    feedback_journal: LocalFeedbackJournal,
    telemetry: TelemetryService,
) -> None:
    values = as_list(
        items
    )

    if not values:
        st.caption(
            "No sufficiently supported hidden "
            "insight was produced."
        )
        return

    for index, item in enumerate(
        values,
        start=1,
    ):
        with st.container(
            border=True
        ):
            render_structured_item(
                item,
                numbered=True,
                index=index,
            )

            if isinstance(
                item,
                Mapping,
            ):
                novel = clean_text(
                    item.get("novel_proposition")
                )
                anomaly = clean_text(
                    item.get("triggering_anomaly")
                )
                quality_check = (
                    item.get("insight_quality_check")
                    if isinstance(
                        item.get("insight_quality_check"),
                        Mapping,
                    )
                    else {}
                )
                derivation = (
                    item.get("derivation_summary")
                    if isinstance(
                        item.get("derivation_summary"),
                        Mapping,
                    )
                    else {}
                )
                likelihood = (
                    item.get("likelihood_assessment")
                    if isinstance(
                        item.get("likelihood_assessment"),
                        Mapping,
                    )
                    else {}
                )

                if novel:
                    st.markdown(
                        "**Novel proposition:** "
                        + novel
                    )

                why_not_obvious = clean_text(
                    quality_check.get(
                        "why_not_obvious"
                    )
                )
                if why_not_obvious:
                    st.markdown(
                        "**Why this is easy to miss:** "
                        + why_not_obvious
                    )

                if anomaly:
                    st.markdown(
                        "**Triggering anomaly / question:** "
                        + anomaly
                    )

                if derivation:
                    with st.expander(
                        "Why we think this",
                        expanded=True,
                    ):
                        observations = as_list(
                            derivation.get(
                                "observations"
                            )
                        )
                        if observations:
                            st.markdown(
                                "**Observed evidence**"
                            )
                            for observation in observations:
                                if isinstance(
                                    observation,
                                    Mapping,
                                ):
                                    observation_text = clean_text(
                                        observation.get(
                                            "text"
                                        )
                                    )
                                else:
                                    observation_text = clean_text(
                                        observation
                                    )
                                if observation_text:
                                    st.markdown(
                                        "- "
                                        + observation_text
                                    )

                        mechanism = clean_text(
                            derivation.get(
                                "mechanism"
                            )
                        )
                        if mechanism:
                            st.markdown(
                                "**Mechanism:** "
                                + mechanism
                            )

                        render_key_assumptions(item)

                        discrimination = clean_text(
                            derivation.get(
                                "discrimination"
                            )
                        )
                        if discrimination:
                            st.markdown(
                                "**Why this explanation fits better:** "
                                + discrimination
                            )

                        analogues = as_list(
                            derivation.get(
                                "historical_or_analogical_support"
                            )
                        )
                        if analogues:
                            st.markdown(
                                "**Historical / analogous support**"
                            )
                            for analogue in analogues:
                                if not isinstance(
                                    analogue,
                                    Mapping,
                                ):
                                    continue
                                case = clean_text(
                                    analogue.get(
                                        "case"
                                    )
                                )
                                match = clean_text(
                                    analogue.get(
                                        "mechanism_match"
                                    )
                                )
                                difference = clean_text(
                                    analogue.get(
                                        "important_difference"
                                    )
                                )
                                text_parts = [
                                    value
                                    for value in (
                                        case,
                                        match,
                                        (
                                            "Difference: "
                                            + difference
                                            if difference
                                            else ""
                                        ),
                                    )
                                    if value
                                ]
                                if text_parts:
                                    st.markdown(
                                        "- "
                                        + " — ".join(
                                            text_parts
                                        )
                                    )

                        counter_cases = as_list(
                            derivation.get(
                                "counter_cases"
                            )
                        )
                        if counter_cases:
                            st.markdown(
                                "**Counter-case / alternative precedent**"
                            )
                            for counter_case in counter_cases:
                                if isinstance(
                                    counter_case,
                                    Mapping,
                                ):
                                    case = clean_text(
                                        counter_case.get(
                                            "case"
                                        )
                                    )
                                    why = clean_text(
                                        counter_case.get(
                                            "why_it_matters"
                                        )
                                    )
                                    counter_text = " — ".join(
                                        value
                                        for value in (
                                            case,
                                            why,
                                        )
                                        if value
                                    )
                                else:
                                    counter_text = clean_text(
                                        counter_case
                                    )
                                if counter_text:
                                    st.markdown(
                                        "- "
                                        + counter_text
                                    )

                        consequence_chain = as_list(
                            derivation.get(
                                "consequence_chain"
                            )
                        )
                        if consequence_chain:
                            st.markdown(
                                "**If this is true, what follows**"
                            )
                            for consequence in consequence_chain:
                                if isinstance(
                                    consequence,
                                    Mapping,
                                ):
                                    consequence_text = clean_text(
                                        consequence.get(
                                            "text"
                                        )
                                    )
                                    epistemic = clean_text(
                                        consequence.get(
                                            "epistemic_level"
                                        )
                                    )
                                    if (
                                        consequence_text
                                        and epistemic
                                    ):
                                        consequence_text += (
                                            " _["
                                            + epistemic
                                            + "]_"
                                        )
                                else:
                                    consequence_text = clean_text(
                                        consequence
                                    )
                                if consequence_text:
                                    st.markdown(
                                        "- "
                                        + consequence_text
                                    )

                        falsifiers = [
                            clean_text(value)
                            for value in as_list(
                                derivation.get(
                                    "falsifiers"
                                )
                            )
                            if clean_text(value)
                        ]
                        if falsifiers:
                            st.markdown(
                                "**What would weaken / falsify it**"
                            )
                            for value in falsifiers:
                                st.markdown(
                                    "- "
                                    + value
                                )

                        watch_signals = [
                            clean_text(value)
                            for value in as_list(
                                derivation.get(
                                    "watch_signals"
                                )
                            )
                            if clean_text(value)
                        ]
                        if watch_signals:
                            st.markdown(
                                "**What to watch next**"
                            )
                            for value in watch_signals:
                                st.markdown(
                                    "- "
                                    + value
                                )

                likelihood_label = clean_text(
                    likelihood.get(
                        "label"
                    )
                )
                likelihood_basis = clean_text(
                    likelihood.get(
                        "basis"
                    )
                )
                if (
                    likelihood_label
                    and likelihood_label
                    != "not_applicable"
                ):
                    st.markdown(
                        "**Likelihood:** "
                        + likelihood_label.replace(
                            "_",
                            " ",
                        ).title()
                    )
                    if likelihood_basis:
                        st.caption(
                            likelihood_basis
                        )

                st.divider()

                render_insight_feedback_controls(
                    report=report,
                    insight_item=item,
                    actor_id=actor_id,
                    session_id_value=(
                        session_id_value
                    ),
                    feedback_service=(
                        feedback_service
                    ),
                    feedback_journal=(
                        feedback_journal
                    ),
                    telemetry=telemetry,
                )


def split_material_landscape(
    sections: Mapping[str, Any],
) -> tuple[list[Any], list[Any]]:
    """Events the findings actually cite, and the rest of the considered corpus.

    Every considered event stays visible for audit, but only events whose
    evidence is cited by a released finding (hidden insight, connection,
    underlying dynamic, second-order effect, counterevidence) lead the
    Event Landscape. With no released finding, all events are shown as before.
    """
    landscape = as_list(sections.get("event_landscape"))
    cited: set[str] = set()
    for key in (
        "hidden_insights",
        "connections",
        "what_is_happening_underneath",
        "second_order_effects",
        "counterevidence_and_alternative_explanations",
    ):
        for item in as_list(sections.get(key)):
            cited.update(evidence_ids(item))
    if not cited:
        return landscape, []
    material, other = [], []
    for item in landscape:
        (material if cited.intersection(evidence_ids(item)) else other).append(item)
    if not material:
        return landscape, []
    return material, other


def render_main_finding(
    report: Mapping[str, Any],
    sections: Mapping[str, Any],
    report_outcome: str,
) -> None:
    """The four-section report contract (docs/PRODUCT_SPEC.md), shown before the tabs.

    Section 2 (briefing + implications + any cited analogy) always renders;
    section 4 is the hidden insight or "None this run". A legacy verified
    connection is listed with the other checked connections, never as a
    separate "main finding".
    """
    render_editorial_briefing(sections)
    hidden = [item for item in as_list(sections.get("hidden_insights")) if isinstance(item, Mapping)]
    findings = [item for item in as_list(sections.get("strategic_findings")) if isinstance(item, Mapping)]
    connections = [item for item in as_list(sections.get("connections")) if isinstance(item, Mapping)]
    developments = [item for item in as_list(sections.get("developments_to_watch")) if isinstance(item, Mapping)]
    st.subheader("Hidden cross-actor insight")
    if hidden:
        st.markdown("**" + clean_text(hidden[0].get("text")) + "**")
        confidence_line, assumptions = key_assumption_lines(hidden[0])
        if confidence_line:
            st.markdown(confidence_line)
        if assumptions:
            st.caption("Every fact and the conclusion were checked against their cited sources; "
                       f"{len(assumptions)} reasoning step(s) are labelled as assumptions. "
                       "Details are in the Hidden Insights tab.")
        else:
            st.caption("Every premise, mechanism step and conclusion sentence was checked against its cited sources. "
                       "Details are in the Hidden Insights tab.")
    else:
        st.markdown("**None this run.**")
        st.caption("No candidate passed every evidence check for a hidden insight. The briefing above is complete.")
    checked_connections = [clean_text(item.get("text") or item.get("connection")) for item in connections]
    checked_connections += [
        clean_text(item.get("text")) for item in findings
        if enum_text(item.get("finding_tier")) == "strategic_connection" and not hidden
    ]
    checked_connections = [text for text in dict.fromkeys(checked_connections) if text]
    if checked_connections:
        st.markdown("**Connections between developments that the sources back:**")
        for text in checked_connections[:5]:
            st.markdown("- " + _markdown_safe(text))
    if developments and not hidden:
        st.markdown("**Developments behind the briefing (sources' own words):**")
        for item in developments[:5]:
            render_development(item, expanded=False)


def _briefing_meta(citations: Any, sources: Mapping[str, Mapping[str, Any]]) -> str:
    labels = []
    for citation in as_list(citations):
        if not isinstance(citation, Mapping):
            continue
        ref = sources.get(clean_text(citation.get("source_id")), {})
        label = _markdown_safe(ref.get("publisher") or ref.get("title")) or clean_text(citation.get("fact_id"))
        url = clean_text(ref.get("url"))
        text = f"[{label}]({url})" if url.startswith(("http://", "https://")) else label
        date = clean_text(citation.get("date"))[:10]
        labels.append(f"{text} {date}".strip())
    unique = list(dict.fromkeys(label for label in labels if label))
    return " · ".join(unique[:4])


def render_editorial_briefing(sections: Mapping[str, Any]) -> None:
    """v6.17 always-present section: briefing, forward-looking implications, analogies."""
    if "editorial_briefing" not in sections:
        return  # report made before v6.17
    briefing = sections.get("editorial_briefing")
    if not isinstance(briefing, Mapping):
        briefing = {}
    sources: dict[str, Mapping[str, Any]] = {}
    extra_sources = briefing.get("sources") if isinstance(briefing.get("sources"), Mapping) else {}
    for source_id, ref in extra_sources.items():
        if isinstance(ref, Mapping):
            sources[clean_text(source_id)] = ref
    for development in as_list(sections.get("developments_to_watch")):
        for ref in as_list(development.get("sources") if isinstance(development, Mapping) else []):
            if isinstance(ref, Mapping):
                sources[clean_text(ref.get("source_id"))] = ref
    paragraphs = [p for p in as_list(briefing.get("paragraphs")) if isinstance(p, list) and p]
    implications = [item for item in as_list(briefing.get("implications")) if isinstance(item, Mapping)]
    analogies = [item for item in as_list(briefing.get("analogies")) if isinstance(item, Mapping)]
    st.subheader("Briefing")
    if not paragraphs and not clean_text(briefing.get("headline")) and not implications and not analogies:
        st.info("No analytical briefing could be produced from the admitted source statements in this run. "
                "The event landscape below shows everything that was found.")
        st.divider()
        return
    if clean_text(briefing.get("headline")):
        st.markdown("### " + _markdown_safe(briefing.get("headline")))
    for paragraph in paragraphs:
        text = " ".join(_markdown_safe(s.get("text")) for s in paragraph if isinstance(s, Mapping))
        cites = [c for s in paragraph if isinstance(s, Mapping) for c in as_list(s.get("citations"))]
        meta = _briefing_meta(cites, sources)
        st.markdown(text + (f"  \n_{meta}_" if meta else ""))
    if briefing.get("mode") == "source_statements_only":
        st.caption("Shown as source statements only; the analysis step was unavailable in this run.")
    elif briefing.get("mode") == "checked_analysis":
        removed = int(briefing.get("sentences_removed_by_check") or 0)
        st.caption("Each sentence was checked against its cited sources"
                   + (f"; {removed} that did not pass were removed." if removed else "."))
    st.markdown("#### Forward-looking implications (analysis, not reported fact)")
    if implications:
        for item in implications:
            basis = enum_text(item.get("basis"))
            label = {"precedent": "**Precedent:** ", "leverage": "**Leverage:** ",
                     "incentive": "**Policy / incentive fit:** "}.get(basis, "**Trend:** ")
            line = "- " + label + _markdown_safe(item.get("text"))
            extra = []
            # v6.21.1: a reasoning step no source states is shown as an assumption.
            assumed = set(as_list(item.get("assumption_fields")))

            def tag(field: str) -> str:
                return " (assumption)" if field in assumed else ""
            case = item.get("case") if isinstance(item.get("case"), Mapping) else {}
            if basis == "leverage" and clean_text(item.get("holder")):
                extra.append(_markdown_safe(item.get("holder")) + " → " + _markdown_safe(item.get("counterparty"))
                             + " via " + _markdown_safe(item.get("leverage_source"))
                             + "; how: " + _markdown_safe(item.get("mechanism")) + tag("mechanism"))
            if basis == "incentive" and clean_text(item.get("actor")):
                extra.append("rule: " + _markdown_safe(item.get("policy_or_regulation"))
                             + "; why it matters to " + _markdown_safe(item.get("actor")) + ": "
                             + _markdown_safe(item.get("company_relevance"))
                             + "; effect: " + _markdown_safe(item.get("economic_or_strategic_effect"))
                             + tag("economic_or_strategic_effect")
                             + "; move: " + _markdown_safe(item.get("attractive_move")) + tag("attractive_move"))

            if clean_text(item.get("conditions")):
                extra.append("could be wrong if: " + _markdown_safe(item.get("conditions")) + tag("conditions"))
            if basis == "precedent" and case:
                if clean_text(case.get("difference")):
                    extra.append("key difference: " + _markdown_safe(case.get("difference")))
                url = clean_text(case.get("source_url"))
                case_text = f"{case.get('year')}: " + _markdown_safe(case.get("text"))[:220]
                extra.append("earlier case " + (f"[{case_text}]({url})" if url.startswith(("http://", "https://"))
                                                else case_text))
            if clean_text(item.get("horizon")):
                extra.append("horizon: " + _markdown_safe(item.get("horizon")))
            if clean_text(item.get("watch_for")):
                extra.append("watch for: " + _markdown_safe(item.get("watch_for")))
            meta = _briefing_meta(item.get("citations"), sources)
            if meta:
                extra.append("based on: " + meta)
            st.markdown(line + ("  \n  _" + " · ".join(extra) + "_" if extra else ""))
    else:
        st.caption("No implication beyond what the headlines and reports already say passed the checks this run.")
    # v6.21.5: implications a headline reader would already expect, or that
    # sources already report, are shown separately and labelled.
    expected = [i for i in as_list(briefing.get("expected_implications")) if isinstance(i, Mapping)]
    if expected:
        st.markdown("**Widely expected** (already implied by the headlines, already reported, or already "
                    "said by published experts):")
        for item in expected:
            note = (" *(experts already say this)*"
                    if "already_in_expert_commentary" in as_list(item.get("novelty_reasons")) else "")
            st.markdown("- " + _markdown_safe(item.get("text")) + note)
    render_expert_commentary(briefing)
    render_actor_moves(sections)
    render_forecast_followups(sections)
    if analogies:
        st.markdown("#### Historical analogy")
        for item in analogies:
            case = item.get("case") if isinstance(item.get("case"), Mapping) else {}
            url = clean_text(case.get("source_url"))
            year = clean_text(case.get("year"))
            head = _markdown_safe(item.get("comparison"))
            st.markdown("- " + head + (f" ({year})" if year else "")
                        + (f" [source]({url})" if url.startswith(("http://", "https://")) else ""))
            if clean_text(item.get("difference")):
                st.caption("Key difference: " + _markdown_safe(item.get("difference")))
    st.divider()


def render_forecast_followups(sections: Mapping[str, Any]) -> None:
    """v6.20.5: earlier runs' forecasts checked against this run's new sources."""
    followups = sections.get("forecast_followups")
    if not isinstance(followups, Mapping) or not followups:
        return
    summary = followups.get("summary") if isinstance(followups.get("summary"), Mapping) else {}
    resolved = [r for r in as_list(followups.get("resolved")) if isinstance(r, Mapping)]
    st.markdown("#### Follow-ups on earlier forecasts")
    if not int(summary.get("open_before") or 0):
        st.caption("No earlier forecasts to follow up yet. This run's implications have been recorded "
                   "and will be checked against later runs' news.")
        return
    labels = {"observed": "**Signal reported:** ", "contradicted": "**Contradicted:** ",
              "expired": "**Time frame passed, not reported:** "}
    for item in resolved:
        status = enum_text(item.get("status"))
        line = ("- " + labels.get(status, "") + _markdown_safe(item.get("implication"))
                + f"  \n  _forecast on {clean_text(item.get('made_on'))}; watch for: "
                + _markdown_safe(item.get("watch_for")) + "_")
        st.markdown(line)
        for ev in as_list(item.get("evidence"))[:2]:
            if isinstance(ev, Mapping):
                source = clean_text(ev.get("source")) or clean_text(ev.get("source_id")) or "source"
                url = clean_text(ev.get("source_url"))
                source_display = (f"[{_markdown_safe(source)}]({url})"
                                  if url.startswith(("http://", "https://"))
                                  else _markdown_safe(source))
                st.caption(f"{clean_text(ev.get('date'))} · {source_display}: "
                           + _markdown_safe(ev.get("text"))[:300])
    still = int(summary.get("still_open") or 0)
    record = followups.get("track_record") if isinstance(followups.get("track_record"), Mapping) else {}
    note = f"{still} earlier forecast(s) still open; their signals were not reported in this run's sources."
    if record.get("decided"):
        share = record.get("observed_share")
        note += (f" Track record so far: {record.get('decided')} decided"
                 + (f", {int(round(float(share) * 100))}% of signals later reported." if share is not None else "."))
    if not resolved:
        note = "No earlier forecast was resolved by this run's sources. " + note
    st.caption(note)


def _markdown_safe(text: Any) -> str:
    return clean_text(text).replace("[", "(").replace("]", ")")


def render_development(item: Mapping[str, Any], *, expanded: bool = False) -> None:
    """One development: source statements with dates and sources, then what to watch."""
    title = _markdown_safe(item.get("title")) or "Development"
    events = len(as_list(item.get("event_ids")))
    found = int(item.get("events_found_by_follow_up_search") or 0)
    kind = (
        f"{events} related events" if enum_text(item.get("kind")) == "shared_development" else "single event"
    ) + (f", {found} found by follow-up search" if found else "")
    sources = {
        clean_text(ref.get("source_id")): ref
        for ref in as_list(item.get("sources")) if isinstance(ref, Mapping)
    }
    with st.expander(f"{title} ({kind})", expanded=expanded):
        actors = [clean_text(actor) for actor in as_list(item.get("actors")) if clean_text(actor)]
        if actors:
            st.caption("Who: " + ", ".join(actors))
        st.markdown("**What the sources report**")
        for fact in as_list(item.get("facts")):
            if not isinstance(fact, Mapping):
                continue
            ref = sources.get(clean_text(fact.get("source_id")), {})
            label = _markdown_safe(ref.get("publisher") or ref.get("title")) or clean_text(fact.get("source_id"))
            url = clean_text(ref.get("url"))
            source = f"[{label}]({url})" if url.startswith(("http://", "https://")) else label
            date = clean_text(fact.get("date"))[:10]
            meta = " · ".join(part for part in (date, source) if part)
            st.markdown("- " + _markdown_safe(fact.get("text")) + (f"  \n  _{meta}_" if meta else ""))
        watch = [clean_text(entry) for entry in as_list(item.get("what_to_watch")) if clean_text(entry)]
        if watch:
            st.markdown("**What to watch**")
            for entry in watch:
                st.markdown("- " + _markdown_safe(entry))


# ---------------------------------------------------------------------------
# Claim-support check (v6.13)
# ---------------------------------------------------------------------------


CLAIM_CHECK_LABELS = {
    "premise_not_supported_by_cited_evidence": "a premise is not stated by its cited source",
    "mechanism_step_not_supported_by_cited_evidence": "a mechanism step is not backed by the cited sources",
    "conclusion_not_supported_by_cited_evidence": "the conclusion is not backed by the cited sources",
    "connection_not_supported_by_cited_evidence": "a connection is not backed by the cited sources",
    "claim_contradicted_by_cited_evidence": "a cited source contradicts a claim",
    "no_conclusion_claim_checked": "no conclusion could be checked",
    "grounding_unavailable": "the claim check could not run",
    "final_quality_insufficient_evidence": "too little evidence",
    "conclusion_restates_sources_not_a_hidden_insight": "the sources already state the conclusion, so it is not a hidden insight",
    "conclusion_not_backed_by_two_independent_sources": "the conclusion is backed by fewer than two independent sources",
    "mechanism_step_not_backed_by_two_independent_sources": "a mechanism step is backed by fewer than two independent sources",
    "forecast_already_answered_by_evidence": "its forecast had already happened according to the sources",
}


def claim_check_label(reason: Any) -> str:
    """Plain-language label for a claim-check reason code."""
    code = clean_text(reason)
    for prefix, lead in (("after_display_check:", "after unsupported text was removed, "),
                         ("claim_support_gate:", "")):
        if code.startswith(prefix):
            return lead + claim_check_label(code[len(prefix):])
    if code in CLAIM_CHECK_LABELS:
        return CLAIM_CHECK_LABELS[code]
    if code.startswith("final_wording_research_incomplete"):
        return "the search for contrary evidence on the final wording could not be completed"
    if code.startswith("final_quality_"):
        return "quality too low (" + code[len("final_quality_"):].replace("_", " ") + ")"
    return code.replace("_", " ")


def render_claim_support_check(
    report: Mapping[str, Any],
) -> None:
    """What the v6.13 claim check did: candidates tried, why rejected, what was removed."""
    metadata = report_value(report, "analysis_metadata", {}) or report_sections(report).get("analysis_metadata")
    if not isinstance(metadata, Mapping):
        return
    gate = metadata.get("claim_support_gate")
    if not isinstance(gate, Mapping):
        return
    attempts = [item for item in as_list(gate.get("attempts")) if isinstance(item, Mapping)]
    if not attempts:
        return
    released = [item for item in attempts if item.get("passed") or item.get("released_as")]
    if released:
        item = released[-1]
        notes = item.get("release_notes") if isinstance(item.get("release_notes"), Mapping) else {}
        removed = len(as_list(notes.get("pruned_second_order_sentences")))
        uncited = len(as_list(notes.get("alternatives_uncited")))
        parts = [
            f"{item.get('core_claims_passed')}/{item.get('core_claims')} premises, mechanism steps "
            "and conclusion sentences backed by their cited sources"
        ]
        if removed:
            parts.append(f"{removed} unsupported second-order sentence(s) removed")
        if uncited:
            parts.append(f"{uncited} alternative explanation(s) shown without a source that did not state them")
        if item.get("released_as") == "verified_connection":
            parts.append("released as a verified connection, not a hidden insight")
        elif len(attempts) > 1:
            parts.append(f"released on attempt {len(attempts)} of {len(attempts)}")
        st.caption("Claim check: " + "; ".join(parts) + ".")
    title = (
        "Candidates checked and why they were not released"
        if not released else "Other candidates checked"
    )
    rejected = [item for item in attempts if not item.get("passed") and not item.get("released_as")]
    if not rejected:
        return
    with st.expander(title, expanded=not released):
        for number, item in enumerate(rejected, start=1):
            reasons = [
                claim_check_label(reason)
                for reason in as_list(item.get("reasons"))
                if clean_text(reason)
            ]
            st.markdown(
                f"**{number}. ({clean_text(item.get('origin')) or 'candidate'})** "
                + clean_text(item.get("insight"))
            )
            if reasons:
                st.caption("Not released: " + "; ".join(reasons))
            for failed in as_list(item.get("failed_claims"))[:6]:
                if not isinstance(failed, Mapping):
                    continue
                detail = "; ".join(
                    clean_text(part) for part in as_list(failed.get("unsupported_parts"))[:1]
                    if clean_text(part)
                )
                st.markdown(
                    "- "
                    + clean_text(failed.get("claim_text"))
                    + (f" — *{detail}*" if detail else "")
                )
        stopped = clean_text(gate.get("stopped"))
        if stopped and not released:
            st.caption(
                "Stopped: "
                + {"retry_limit": "retry limit reached", "time_budget": "not enough run time left"}.get(stopped, stopped)
                + "."
            )


# ---------------------------------------------------------------------------
# Evidence trail
# ---------------------------------------------------------------------------


def render_evidence_trail(
    trail: Any,
    *,
    report: Mapping[
        str,
        Any,
    ],
    actor_id: str,
    session_id_value: str,
    telemetry: TelemetryService,
) -> None:
    """
    We deliberately use an explicit toggle rather than merely
    rendering a Streamlit tab.

    Streamlit executes all tabs during a rerun, so tab rendering
    itself would NOT prove that the user opened the evidence trail.

    The toggle gives us a genuine measurable interaction.
    """

    show = st.toggle(
        "Show evidence trail",
        value=False,
        key=(
            "show_evidence::"
            + clean_text(
                report.get(
                    "report_id"
                )
            )
        ),
    )

    if not show:
        st.caption(
            "Open the evidence trail to inspect "
            "the evidence behind the report."
        )
        return

    record_ui_event(
        telemetry,
        event_type=(
            "evidence_trail_opened"
        ),
        report=report,
        actor_id=actor_id,
        session_id_value=(
            session_id_value
        ),
        once_per_session=True,
    )

    if not trail:
        st.caption(
            "No evidence trail is available."
        )
        return

    if isinstance(
        trail,
        Mapping,
    ):
        entries = list(
            trail.items()
        )

    else:
        entries = []

        for index, item in enumerate(
            as_list(
                trail
            ),
            start=1,
        ):
            if isinstance(
                item,
                Mapping,
            ):
                evidence_id = (
                    clean_text(
                        item.get(
                            "evidence_id"
                        )
                        or item.get(
                            "id"
                        )
                    )
                )

                entries.append(
                    (
                        evidence_id
                        or (
                            f"Evidence {index}"
                        ),
                        item,
                    )
                )

            else:
                entries.append(
                    (
                        f"Evidence {index}",
                        item,
                    )
                )

    for evidence_id, details in entries:
        with st.expander(
            clean_text(
                evidence_id
            )
            or "Evidence"
        ):
            if not isinstance(
                details,
                Mapping,
            ):
                st.write(
                    details
                )
                continue

            source = clean_text(
                details.get(
                    "source_name"
                )
                or details.get(
                    "source"
                )
                or details.get(
                    "source_id"
                )
            )

            snippet = clean_text(
                details.get(
                    "snippet"
                )
                or details.get(
                    "evidence_snippet"
                )
                or details.get(
                    "text"
                )
                or details.get(
                    "claim"
                )
            )

            url = clean_text(
                details.get(
                    "canonical_url"
                )
                or details.get(
                    "source_url"
                )
                or details.get(
                    "url"
                )
            )

            role = clean_text(
                details.get(
                    "evidence_role"
                )
                or details.get(
                    "role"
                )
            )

            if source:
                st.write(
                    f"**Source:** {source}"
                )

            if role:
                st.caption(
                    "Evidence role: "
                    + role.replace(
                        "_",
                        " ",
                    ).title()
                )

            if snippet:
                st.write(
                    snippet
                )

            if url:
                st.link_button(
                    "Open source",
                    url,
                )


# ---------------------------------------------------------------------------
# Report feedback
# ---------------------------------------------------------------------------


def render_report_feedback(
    *,
    report: Mapping[
        str,
        Any,
    ],
    actor_id: str,
    session_id_value: str,
    feedback_service: UserFeedbackService,
    feedback_journal: LocalFeedbackJournal,
    telemetry: TelemetryService,
) -> None:
    report_id = clean_text(
        report.get(
            "report_id"
        )
    )

    st.divider()
    st.subheader(
        "Feedback"
    )

    latest = (
        feedback_journal
        .latest_rating(
            actor_id=actor_id,
            report_id=report_id,
            feedback_scope=(
                "report"
            ),
            insight_id="",
        )
    )

    st.write(
        "**Was this report useful?**"
    )

    (
        col_like,
        col_dislike,
        col_status,
    ) = st.columns(
        [
            1,
            1,
            5,
        ]
    )

    with col_like:
        if st.button(
            "👍",
            key=(
                f"report_like::{report_id}"
            ),
        ):
            submit_feedback_event(
                report=report,
                actor_id=actor_id,
                session_id_value=(
                    session_id_value
                ),
                feedback_service=(
                    feedback_service
                ),
                feedback_journal=(
                    feedback_journal
                ),
                telemetry=telemetry,
                feedback_scope=(
                    "report"
                ),
                rating_label="like",
            )

            st.rerun()

    with col_dislike:
        if st.button(
            "👎",
            key=(
                f"report_dislike::{report_id}"
            ),
        ):
            submit_feedback_event(
                report=report,
                actor_id=actor_id,
                session_id_value=(
                    session_id_value
                ),
                feedback_service=(
                    feedback_service
                ),
                feedback_journal=(
                    feedback_journal
                ),
                telemetry=telemetry,
                feedback_scope=(
                    "report"
                ),
                rating_label=(
                    "dislike"
                ),
            )

            st.rerun()

    with col_status:
        if latest == "like":
            st.caption(
                "You marked this report useful."
            )

        elif latest == "dislike":
            st.caption(
                "You marked this report not useful."
            )

        else:
            st.caption(
                "One click is enough — "
                "no long rating form."
            )

    # One free-text field only.
    # The LLM can decode the meaning later.
    with st.form(
        key=(
            "free_text_feedback::"
            f"{report_id}"
        ),
        clear_on_submit=False,
    ):
        feedback_text = st.text_area(
            "Anything you'd like to add?",
            placeholder=(
                "For example: what was useful, "
                "missing, unclear, too obvious, "
                "or too speculative?"
            ),
            height=110,
        )

        submitted = (
            st.form_submit_button(
                "Submit feedback"
            )
        )

    if submitted:
        text = clean_text(
            feedback_text
        )

        if not text:
            st.warning(
                "Write a short comment "
                "before submitting."
            )

        else:
            event = (
                submit_feedback_event(
                    report=report,
                    actor_id=actor_id,
                    session_id_value=(
                        session_id_value
                    ),
                    feedback_service=(
                        feedback_service
                    ),
                    feedback_journal=(
                        feedback_journal
                    ),
                    telemetry=(
                        telemetry
                    ),
                    feedback_scope=(
                        "report"
                    ),
                    feedback_text=text,
                )
            )

            st.success(
                "Feedback saved."
            )

            feedback_types = list(
                event.get(
                    "feedback_types"
                )
                or []
            )

            if feedback_types:
                st.caption(
                    "Current interpretation: "
                    + ", ".join(
                        value.replace(
                            "_",
                            " ",
                        )
                        for value
                        in feedback_types
                    )
                )


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title=(
            "AI Event Intelligence"
        ),
        page_icon="🔎",
        layout="wide",
    )

    report_service = (
        get_report_service()
    )

    telemetry = (
        get_telemetry_service()
    )

    feedback_service = (
        get_feedback_service()
    )

    feedback_journal = (
        get_feedback_journal()
    )

    actor_id = (
        get_or_create_local_actor_id()
    )

    session_id_value = (
        session_id()
    )

    st.title(
        "AI Event Intelligence"
    )

    st.caption(
        "Evidence-grounded research that connects events, "
        "surfaces underlying dynamics, and keeps major "
        "conclusions traceable to evidence."
    )

    # Load the current released-report catalogue, but never let an empty
    # catalogue remove the user's ability to start new research.
    try:
        reports = list(
            report_service.list_reports(
                state="released"
            )
            or []
        )
        report_load_error = ""

    except ReportServiceError as exc:
        reports = []
        report_load_error = clean_text(
            exc
        )

    st.subheader(
        "Research a new topic"
    )

    st.caption(
        "Run one topic, inspect the released report, then enter another topic "
        "here at any time. Existing reports remain available separately."
    )

    with st.form(
        key="new_research_form",
        clear_on_submit=False,
    ):
        new_research_question = st.text_area(
            "What do you want to research?",
            placeholder=(
                "For example: How will AI data-centre expansion affect "
                "power markets, semiconductors, and regulation in India?"
            ),
            height=110,
        )

        research_depth = st.selectbox(
            "Research depth",
            options=[
                "fast",
                "deep",
            ],
            key="research_depth_selector",
            format_func=(
                lambda value: (
                    "Fast"
                    if value == "fast"
                    else "Deep"
                )
            ),
            help=(
                "Fast and Deep should use the same epistemic pipeline; "
                "the compiled execution profile controls the budget/depth."
            ),
        )

        run_research = (
            st.form_submit_button(
                "Run Research",
                type="primary",
            )
        )

    if run_research:
        question = clean_text(
            new_research_question
        )

        if not question:
            st.warning(
                "Enter a topic or research question first."
            )
        else:
            before_ids = {
                clean_text(
                    item.get(
                        "report_id"
                    )
                )
                for item in reports
                if isinstance(
                    item,
                    Mapping,
                )
                and clean_text(
                    item.get(
                        "report_id"
                    )
                )
            }

            st.caption(
                "Submitted research depth: "
                + (
                    "Deep"
                    if clean_text(research_depth).casefold() == "deep"
                    else "Fast"
                )
            )

            run_token = mark_research_run_started(question, clean_text(research_depth))
            try:
                with st.spinner(
                    "Running the evidence-grounded research pipeline..."
                ):
                    run_outcome = "failed"
                    try:
                        run_result = (
                            _run_research_via_existing_orchestrator(
                                research_question=question,
                                research_depth=(
                                    research_depth
                                ),
                            )
                        )
                        run_outcome = "finished"
                    except Exception as run_error:
                        run_outcome = f"failed: {type(run_error).__name__}: {clean_text(run_error)[:300]}"
                        raise
                    finally:
                        # Cleared even if this page has gone away, so any
                        # other open page sees the run end.
                        mark_research_run_finished(run_token, run_outcome)

            except ResearchRunnerUnavailableError as exc:
                st.error(
                    clean_text(
                        exc
                    )
                )

            except Exception as exc:
                st.error(
                    "Research run failed: "
                    + type(
                        exc
                    ).__name__
                    + ": "
                    + clean_text(
                        exc
                    )
                )

            else:
                try:
                    refreshed_reports = list(
                        report_service.list_reports(
                            state="released"
                        )
                        or []
                    )
                except ReportServiceError as exc:
                    st.error(
                        "Research finished, but released reports "
                        "could not be reloaded: "
                        + clean_text(
                            exc
                        )
                    )
                else:
                    refreshed_by_id = {
                        clean_text(
                            item.get(
                                "report_id"
                            )
                        ): item
                        for item in refreshed_reports
                        if isinstance(
                            item,
                            Mapping,
                        )
                        and clean_text(
                            item.get(
                                "report_id"
                            )
                        )
                    }

                    run_mapping = (
                        dict(run_result)
                        if isinstance(run_result, Mapping)
                        else {}
                    )
                    terminal = (
                        run_mapping.get("terminal_outcome")
                        if isinstance(
                            run_mapping.get("terminal_outcome"),
                            Mapping,
                        )
                        else {}
                    )

                    terminal_status = clean_text(
                        terminal.get("status")
                    ).casefold()
                    terminal_report_id = clean_text(
                        terminal.get("report_id")
                    )
                    result_report_id = (
                        terminal_report_id
                        or _extract_report_id_from_run_result(
                            run_result
                        )
                    )
                    run_id = clean_text(
                        run_mapping.get("run_id")
                    )
                    run_status = clean_text(
                        run_mapping.get("status")
                    )
                    terminal_stage = clean_text(
                        terminal.get("terminal_stage")
                    )
                    terminal_reason = clean_text(
                        terminal.get("reason")
                    )
                    terminal_reason_code = clean_text(
                        terminal.get("reason_code")
                    )
                    terminal_report_released = bool(
                        terminal.get("report_released")
                    )

                    # V4.10 contract: the orchestrator's terminal_outcome is the
                    # authoritative result of THIS run. Do not infer success from
                    # historical/new catalogue differences.
                    if terminal_status in {"released", "abstained"}:
                        if not result_report_id:
                            st.session_state[
                                "latest_run_release_status"
                            ] = {
                                "released": False,
                                "outcome": "contract_error",
                                "run_id": run_id,
                                "report_id": "",
                                "status": run_status,
                                "terminal_stage": terminal_stage,
                                "reason_code": "terminal_report_id_missing",
                                "reason": (
                                    terminal_reason
                                    or "The orchestrator reported a report-bearing "
                                    "terminal outcome without a report_id."
                                ),
                            }
                            st.error(
                                "Research reached a report-bearing terminal outcome "
                                "but returned no report ID. "
                                + (
                                    f"Run: `{run_id}`. "
                                    if run_id
                                    else ""
                                )
                                + (
                                    f"Stage: `{terminal_stage}`. "
                                    if terminal_stage
                                    else ""
                                )
                                + (terminal_reason or "")
                            )

                        elif result_report_id not in refreshed_by_id:
                            st.session_state[
                                "latest_run_release_status"
                            ] = {
                                "released": False,
                                "outcome": "persistence_mismatch",
                                "run_id": run_id,
                                "report_id": result_report_id,
                                "status": run_status,
                                "terminal_stage": terminal_stage,
                                "reason_code": "released_report_not_in_catalogue",
                                "reason": (
                                    terminal_reason
                                    or "The orchestrator returned a released report ID "
                                    "that ReportService could not reload."
                                ),
                            }
                            st.error(
                                "Research completed but the returned report could not "
                                "be loaded from the released-report catalogue. "
                                f"Report: `{result_report_id}`. "
                                + (
                                    f"Run: `{run_id}`. "
                                    if run_id
                                    else ""
                                )
                            )

                        else:
                            st.session_state[
                                "selected_report_id"
                            ] = result_report_id
                            st.session_state[
                                "latest_run_release_status"
                            ] = {
                                "released": True,
                                "outcome": terminal_status,
                                "run_id": run_id,
                                "report_id": result_report_id,
                                "status": run_status,
                                "terminal_stage": terminal_stage,
                                "reason_code": terminal_reason_code,
                                "reason": terminal_reason,
                                "report_released": terminal_report_released,
                            }
                            # No st.rerun(): refreshed_reports already contains the
                            # authoritative report and the renderer below can display it
                            # in this same Streamlit execution.

                    elif terminal_status == "failed":
                        st.session_state[
                            "latest_run_release_status"
                        ] = {
                            "released": False,
                            "outcome": "failed",
                            "run_id": run_id,
                            "report_id": result_report_id,
                            "status": run_status,
                            "terminal_stage": terminal_stage,
                            "reason_code": terminal_reason_code,
                            "reason": terminal_reason,
                            "report_released": terminal_report_released,
                        }
                        detail_parts = []
                        if run_id:
                            detail_parts.append(f"Run: `{run_id}`")
                        if terminal_stage:
                            detail_parts.append(f"Stage: `{terminal_stage}`")
                        if terminal_reason_code:
                            detail_parts.append(
                                f"Code: `{terminal_reason_code}`"
                            )
                        detail = (
                            " · ".join(detail_parts)
                            if detail_parts
                            else ""
                        )
                        st.error(
                            "Research run failed before a report was released."
                            + (" " + detail + "." if detail else "")
                            + (
                                " " + terminal_reason
                                if terminal_reason
                                else ""
                            )
                        )

                    else:
                        st.session_state[
                            "latest_run_release_status"
                        ] = {
                            "released": False,
                            "outcome": "contract_error",
                            "run_id": run_id,
                            "report_id": result_report_id,
                            "status": run_status,
                            "terminal_stage": terminal_stage,
                            "reason_code": "terminal_outcome_missing_or_invalid",
                            "reason": terminal_reason,
                        }
                        st.error(
                            "Research returned without a valid terminal_outcome. "
                            "Expected `released`, `abstained`, or `failed`."
                            + (
                                f" Run: `{run_id}`."
                                if run_id
                                else ""
                            )
                        )

    if report_load_error:
        st.error(
            "Could not load released reports: "
            + report_load_error
        )

    # Re-read after the research form so external/new runs are reflected on rerun.
    if not report_load_error:
        try:
            reports = list(
                report_service.list_reports(
                    state="released"
                )
                or []
            )
        except ReportServiceError as exc:
            reports = []
            report_load_error = clean_text(
                exc
            )
            st.error(
                "Could not refresh released reports: "
                + report_load_error
            )

    report_by_id = {
        clean_text(
            report.get(
                "report_id"
            )
        ): report
        for report in reports
        if isinstance(
            report,
            Mapping,
        )
        and clean_text(
            report.get(
                "report_id"
            )
        )
    }

    latest_run_release_status = st.session_state.get(
        "latest_run_release_status",
        {},
    )
    if (
        isinstance(latest_run_release_status, Mapping)
        and latest_run_release_status
    ):
        latest_run_id = clean_text(
            latest_run_release_status.get("run_id")
        )
        latest_report_id = clean_text(
            latest_run_release_status.get("report_id")
        )

        latest_outcome = clean_text(
            latest_run_release_status.get("outcome")
        ).casefold()
        latest_reason = clean_text(
            latest_run_release_status.get("reason")
        )
        latest_stage = clean_text(
            latest_run_release_status.get("terminal_stage")
        )

        if bool(
            latest_run_release_status.get("released")
        ):
            detail_parts = []
            if latest_report_id:
                detail_parts.append(
                    f"Report: `{latest_report_id}`"
                )
            if latest_run_id:
                detail_parts.append(
                    f"Run: `{latest_run_id}`"
                )

            details = (
                " " + " · ".join(detail_parts) + "."
                if detail_parts
                else ""
            )

            if latest_outcome == "abstained":
                st.info(
                    "Report completed. No hidden cross-actor insight passed its checks this run; "
                    "the briefing, implications and landscape are in the report."
                    + details
                    + " The selected report is loaded below."
                    + (
                        " " + latest_reason
                        if latest_reason
                        else ""
                    )
                )
            else:
                st.success(
                    "Latest research run completed and released a new report."
                    + details
                    + " The selected report is loaded below."
                )
        elif latest_outcome == "failed":
            detail_parts = []
            if latest_run_id:
                detail_parts.append(
                    f"Run: `{latest_run_id}`"
                )
            if latest_stage:
                detail_parts.append(
                    f"Stage: `{latest_stage}`"
                )
            st.error(
                "Latest research run failed before a report was released."
                + (
                    " " + " · ".join(detail_parts) + "."
                    if detail_parts
                    else ""
                )
                + (
                    " " + latest_reason
                    if latest_reason
                    else ""
                )
            )
        else:
            suffix = (
                f" Latest run: `{latest_run_id}`."
                if latest_run_id
                else ""
            )
            st.warning(
                "Latest-run status: no new report was released. "
                "Any report selectable below predates that run."
                + suffix
                + (
                    " " + latest_reason
                    if latest_reason
                    else ""
                )
            )

    # A run started from another page session (or before this page
    # reconnected) is still going: say so and check every 10 s; when it ends,
    # redraw so the newest report is selected below.
    running = research_run_in_progress()
    if running:
        started_label = report_generated_label({"created_at": running.get("started_at")})
        st.info(
            f"A research run started at {started_label} "
            f"({clean_text(running.get('depth')) or 'run'}: "
            f"'{clean_text(running.get('question'))[:80]}') is still running. "
            "Its report will appear here automatically when it finishes."
        )
        if hasattr(st, "fragment"):
            @st.fragment(run_every="10s")
            def wait_for_research_run() -> None:
                if research_run_in_progress() is None:
                    st.rerun()

            wait_for_research_run()
    else:
        last = research_run_last_outcome()
        if last and str(last.get("outcome", "")).startswith("failed"):
            if st.session_state.get("last_run_failure_shown") != last.get("finished_at"):
                st.session_state["last_run_failure_shown"] = last.get("finished_at")
                st.error(
                    "The last research run (ended "
                    + report_generated_label({"created_at": last.get("finished_at")})
                    + ") did not produce a report: "
                    + clean_text(last.get("outcome"))[:400]
                )

    # Newest first, by when each report was generated.
    report_ids = sorted(
        report_by_id,
        key=lambda report_id: clean_text(
            report_by_id[report_id].get("created_at")
        ),
        reverse=True,
    )

    if not report_ids:
        st.info(
            "No released research reports are available yet. "
            "Enter a topic above to start a research run."
        )
        return

    # When a report newer than any this session has seen appears (a run just
    # finished, or the app was reopened), show it. Otherwise a selection kept
    # from earlier would keep an old report on screen after a new run.
    newest_report_id = report_ids[0]
    if st.session_state.get("newest_report_id_seen") != newest_report_id:
        st.session_state["newest_report_id_seen"] = newest_report_id
        st.session_state["selected_report_id"] = newest_report_id

    if (
        st.session_state.get(
            "selected_report_id"
        )
        not in report_by_id
    ):
        st.session_state[
            "selected_report_id"
        ] = report_ids[0]

    selected_report_id = (
        st.sidebar.selectbox(
            "Research report",
            options=report_ids,
            key="selected_report_id",
            format_func=(
                lambda report_id: (
                    report_generated_label(report_by_id[report_id])
                    + " · "
                    + (
                        clean_text(
                            report_by_id[
                                report_id
                            ].get(
                                "research_question"
                            )
                        )
                        or report_id
                    )
                    + (" (newest)" if report_id == newest_report_id else "")
                )
            ),
        )
    )

    report = report_by_id.get(
        selected_report_id
    )

    if report is None:
        selected_report_id = (
            report_ids[0]
        )
        report = report_by_id[
            selected_report_id
        ]

    sections = report_sections(
        report
    )

    # Always say which report is on screen and when it was generated.
    st.caption(
        f"Showing report `{selected_report_id}` · generated "
        f"{report_generated_label(report)}"
    )
    if selected_report_id != newest_report_id:
        st.warning(
            "This is an older report (generated "
            f"{report_generated_label(report)}). The newest report was generated "
            f"{report_generated_label(report_by_id[newest_report_id])}; pick it in "
            "the sidebar under 'Research report'."
        )

    # Actual report-view interaction.
    record_ui_event(
        telemetry,
        event_type="report_viewed",
        report=report,
        actor_id=actor_id,
        session_id_value=(
            session_id_value
        ),
        dimensions={
            "topic_id": clean_text(
                report_value(
                    report,
                    "topic_id",
                )
            ),
            "research_question_present": bool(
                clean_text(
                    report.get(
                        "research_question"
                    )
                )
            ),
        },
        once_per_session=True,
    )

    st.sidebar.caption(
        f"Report: `{selected_report_id}`"
    )

    st.sidebar.caption(
        "Revision: `"
        + (
            clean_text(
                report.get(
                    "report_revision_id"
                )
            )
            or "unknown"
        )
        + "`"
    )

    research_question = (
        clean_text(
            report.get(
                "research_question"
            )
        )
    )

    st.subheader(
        research_question
        or "Research Report"
    )

    report_outcome = enum_text(
        report_value(
            report,
            "outcome",
            "",
        )
        or (
            report_value(
                report,
                "analysis_metadata",
                {},
            ).get(
                "outcome"
            )
            if isinstance(
                report_value(
                    report,
                    "analysis_metadata",
                    {},
                ),
                Mapping,
            )
            else ""
        )
    )
    if not report_outcome:
        # Stored report revisions keep the outcome inside their sections.
        section_metadata = sections.get("analysis_metadata")
        report_outcome = enum_text(
            sections.get("research_outcome")
            or (
                section_metadata.get("outcome")
                if isinstance(section_metadata, Mapping)
                else ""
            )
        )

    if report_outcome == "abstained":
        abstention_summary = clean_text(
            report_value(
                report,
                "abstention_summary",
                "",
            )
            or sections.get("abstention_summary")
        )
        abstention = report_value(
            report,
            "abstention",
            {},
        ) or sections.get("abstention")
        if not isinstance(
            abstention,
            Mapping,
        ):
            abstention = {}

        classification = clean_text(
            report_value(
                report,
                "abstention_classification",
                "",
            )
            or sections.get("abstention_classification")
            or abstention.get(
                "classification"
            )
        )
        abstention_stage = clean_text(
            abstention.get(
                "stage"
            )
        )
        rejection_reasons = [
            clean_text(
                value
            )
            for value in as_list(
                abstention.get(
                    "final_rejection_reasons"
                )
            )
            if clean_text(
                value
            )
        ]

        st.info(
            abstention_summary
            or (
                "No hidden cross-actor insight passed its evidence checks in this run. "
                "The rest of the report is complete."
            )
        )

        abstention_details = []
        if classification:
            abstention_details.append(
                "classification="
                + classification
            )
        if abstention_stage:
            abstention_details.append(
                "stage="
                + abstention_stage
            )
        if abstention_details:
            st.caption(
                "Hidden-insight check: "
                + " | ".join(
                    abstention_details
                )
            )

        if rejection_reasons:
            with st.expander(
                "Why no hidden insight was released",
                expanded=False,
            ):
                for reason in rejection_reasons:
                    st.markdown(
                        "- "
                        + reason
                    )

        render_claim_support_check(report)

    # -----------------------------------------------------------------------
    # Optional executive synthesis
    # -----------------------------------------------------------------------

    executive = (
        sections.get(
            "executive_synthesis"
        )
        or report_value(
            report,
            "executive_synthesis",
            {},
        )
    )

    if executive:
        st.header(
            "Executive Synthesis"
        )

        if isinstance(
            executive,
            Mapping,
        ):
            what = clean_text(
                executive.get(
                    "what_is_happening"
                )
                or executive.get(
                    "summary"
                )
                or executive.get(
                    "text"
                )
            )

            meaning = clean_text(
                executive.get(
                    "what_it_may_mean"
                )
                or executive.get(
                    "interpretation"
                )
            )

            uncertainty = (
                clean_text(
                    executive.get(
                        "main_uncertainty"
                    )
                )
            )

            (
                col_a,
                col_b,
            ) = st.columns(
                2
            )

            with col_a:
                st.subheader(
                    "What is happening"
                )

                st.write(
                    what
                    or (
                        "No executive synthesis "
                        "available."
                    )
                )

            with col_b:
                st.subheader(
                    "What it may mean"
                )

                st.write(
                    meaning
                    or (
                        "No interpretation "
                        "available."
                    )
                )

            if uncertainty:
                st.info(
                    "Main uncertainty: "
                    + uncertainty
                )

            render_evidence_refs(
                evidence_ids(
                    executive
                )
            )

        else:
            st.write(
                executive
            )

    render_main_finding(report, sections, report_outcome)

    # -----------------------------------------------------------------------
    # Canonical report sections
    # -----------------------------------------------------------------------

    (
        tab_landscape,
        tab_connections,
        tab_underneath,
        tab_insights,
        tab_effects,
        tab_counter,
        tab_watch,
        tab_evidence,
    ) = st.tabs(
        [
            "Event Landscape",
            "Connections",
            "What Is Happening Underneath",
            "Hidden Insights",
            "Second-Order Effects",
            "Counterevidence",
            "What to Watch",
            "Evidence Trail",
        ]
    )

    with tab_landscape:
        st.header(
            "Event Landscape"
        )

        st.caption(
            "The developments that matter "
            "to the research question."
        )

        material_events, other_events = split_material_landscape(
            sections
        )

        render_item_list(
            material_events,
            empty_message=(
                "No material events "
                "were included."
            ),
        )

        if other_events:
            with st.expander(
                f"Other events considered ({len(other_events)}) - "
                "not used by the findings above",
                expanded=False,
            ):
                render_item_list(
                    other_events,
                    empty_message="",
                )

    with tab_connections:
        st.header(
            "Connections"
        )

        st.caption(
            "How developments relate to one another "
            "rather than appearing as isolated headlines."
        )

        render_item_list(
            sections.get(
                "connections"
            ),
            empty_message=(
                "No material connections "
                "were established."
            ),
        )

    with tab_underneath:
        st.header(
            "What Is Happening Underneath"
        )

        render_item_list(
            sections.get(
                "what_is_happening_underneath"
            ),
            empty_message=(
                "No material underlying "
                "mechanism was established."
            ),
        )

    with tab_insights:
        st.header(
            "Hidden / Easy-to-Miss Insights"
        )

        st.caption(
            "Cross-event inferences and structural implications. "
            "Each insight can be rated with a simple Like or Dislike."
        )

        render_hidden_insights(
            report=report,
            items=sections.get(
                "hidden_insights"
            ),
            actor_id=actor_id,
            session_id_value=(
                session_id_value
            ),
            feedback_service=(
                feedback_service
            ),
            feedback_journal=(
                feedback_journal
            ),
            telemetry=telemetry,
        )

        if report_outcome != "abstained":
            render_claim_support_check(report)

    with tab_effects:
        st.header(
            "Second-Order Effects"
        )

        render_item_list(
            sections.get(
                "second_order_effects"
            ),
            empty_message=(
                "No material second-order "
                "effect was identified."
            ),
        )

    with tab_counter:
        st.header(
            "Counterevidence & "
            "Alternative Explanations"
        )

        st.caption(
            "Evidence or interpretations that could "
            "weaken the main synthesis."
        )

        render_item_list(
            sections.get(
                "counterevidence_and_alternative_explanations"
            ),
            empty_message=(
                "No material counterevidence "
                "or alternative explanation "
                "was identified."
            ),
        )

    with tab_watch:
        st.header(
            "What to Watch"
        )

        render_item_list(
            sections.get(
                "what_to_watch"
            ),
            empty_message=(
                "No watch signals were included."
            ),
        )

        st.divider()

        st.subheader(
            "Critical Unknowns"
        )

        render_item_list(
            sections.get(
                "critical_unknowns"
            ),
            empty_message=(
                "No critical unknowns "
                "were listed."
            ),
        )

    with tab_evidence:
        st.header(
            "Evidence Trail"
        )

        st.caption(
            "Trace report conclusions back to the evidence "
            "records used during reasoning."
        )

        render_evidence_trail(
            sections.get(
                "evidence_trail"
            ),
            report=report,
            actor_id=actor_id,
            session_id_value=(
                session_id_value
            ),
            telemetry=telemetry,
        )

    # -----------------------------------------------------------------------
    # Optional downstream decision support
    # -----------------------------------------------------------------------

    render_decision_agent_panel(
        report=report,
    )

    # -----------------------------------------------------------------------
    # Report-level feedback
    # -----------------------------------------------------------------------

    render_report_feedback(
        report=report,
        actor_id=actor_id,
        session_id_value=(
            session_id_value
        ),
        feedback_service=(
            feedback_service
        ),
        feedback_journal=(
            feedback_journal
        ),
        telemetry=telemetry,
    )

    # -----------------------------------------------------------------------
    # Metadata
    # -----------------------------------------------------------------------

    metadata = report_value(
        report,
        "metadata",
        {},
    )

    if (
        isinstance(
            metadata,
            Mapping,
        )
        and metadata
    ):
        with st.sidebar.expander(
            "Run metadata"
        ):
            st.json(
                dict(
                    metadata
                )
            )

    with st.sidebar.expander(
        "Measurement"
    ):
        st.caption(
            "Measured automatically: report views, "
            "insight impressions, report ratings, "
            "insight ratings, feedback submissions, "
            "and evidence-trail engagement."
        )

        st.caption(
            "Raw written feedback is stored locally "
            "in the feedback journal and is not copied "
            "into telemetry."
        )

        st.caption(
            f"Dashboard: `{DASHBOARD_VERSION}`"
        )


if __name__ == "__main__":
    main()