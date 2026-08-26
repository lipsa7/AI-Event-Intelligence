"""
Central logging configuration for the event-intelligence application.

This module owns only ordinary application logging.

It owns
-------
- standard Python logging configuration;
- application logger naming;
- console logging;
- optional rotating local file logging.

It does NOT own
---------------
- product telemetry;
- research traces;
- audit receipts;
- evaluation records;
- user feedback;
- grounding records;
- provenance records.

Those have dedicated services/stores.

Privacy / security boundary
---------------------------
Ordinary logs should contain operational information such as:

    IDs
    component/stage names
    statuses
    counts
    durations
    sanitized exceptions

Do NOT intentionally log:

    secrets / API keys
    raw prompts
    raw model responses
    hidden/private reasoning
    complete source bodies
    raw user-written feedback
    authentication tokens

Those are either prohibited entirely or belong in their
explicitly designed storage systems.
"""

from __future__ import annotations

import logging
import os

from logging.handlers import (
    RotatingFileHandler,
)
from pathlib import Path


LOGGER_NAMESPACE = (
    "event_intelligence"
)

DEFAULT_LOG_DIR = Path(
    "data/logs"
)

DEFAULT_LOG_FILENAME = (
    "system.log"
)

DEFAULT_LOG_LEVEL = (
    "INFO"
)

DEFAULT_MAX_BYTES = (
    5 * 1024 * 1024
)

DEFAULT_BACKUP_COUNT = 3

LOG_FORMAT = (
    "%(asctime)s | "
    "%(levelname)s | "
    "%(name)s | "
    "%(message)s"
)

DATE_FORMAT = (
    "%Y-%m-%dT%H:%M:%S%z"
)

_HANDLER_MARKER = (
    "_event_intelligence_handler"
)


class LoggingConfigurationError(
    ValueError
):
    """
    Raised when application logging configuration
    is invalid.
    """


def _resolve_level(
    level: int | str | None,
) -> int:
    """
    Resolve a Python logging level.

    If `level` is not supplied:

        EVENT_INTELLIGENCE_LOG_LEVEL

    is consulted before falling back to INFO.
    """

    if level is None:
        level = os.getenv(
            "EVENT_INTELLIGENCE_LOG_LEVEL",
            DEFAULT_LOG_LEVEL,
        )

    if isinstance(
        level,
        int,
    ):
        if level < 0:
            raise LoggingConfigurationError(
                "Logging level cannot "
                "be negative."
            )

        return level

    if isinstance(
        level,
        str,
    ):
        normalized = (
            level
            .strip()
            .upper()
        )

        resolved = getattr(
            logging,
            normalized,
            None,
        )

        if isinstance(
            resolved,
            int,
        ):
            return resolved

    raise LoggingConfigurationError(
        "Unsupported logging level: "
        f"{level!r}"
    )


def _build_formatter(
) -> logging.Formatter:
    return logging.Formatter(
        fmt=LOG_FORMAT,
        datefmt=DATE_FORMAT,
    )


def _is_owned_handler(
    handler: logging.Handler,
) -> bool:
    """
    Return whether the handler was installed
    by this utility.
    """

    return bool(
        getattr(
            handler,
            _HANDLER_MARKER,
            False,
        )
    )


def _mark_owned_handler(
    handler: logging.Handler,
) -> logging.Handler:
    """
    Mark a handler so repeated configuration does
    not duplicate application handlers.
    """

    setattr(
        handler,
        _HANDLER_MARKER,
        True,
    )

    return handler


def configure_logging(
    *,
    level: int | str | None = None,
    log_dir: str | Path = DEFAULT_LOG_DIR,
    log_filename: str = DEFAULT_LOG_FILENAME,
    console: bool = True,
    file_logging: bool = True,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    force: bool = False,
) -> logging.Logger:
    """
    Configure the event-intelligence logger.

    Configuration is idempotent:
    repeated calls do not create duplicate handlers.

    Unlike logging.basicConfig(), this function does NOT
    reconfigure the root logger. That prevents the application
    from accidentally changing Streamlit, SQLAlchemy, urllib,
    or third-party-library logging behavior.

    Parameters
    ----------
    level:
        Python logging level integer or name.

        If omitted:
            EVENT_INTELLIGENCE_LOG_LEVEL
        is consulted, then INFO is used.

    log_dir:
        Directory containing the local log file.

    log_filename:
        File name inside `log_dir`.

    console:
        Whether to emit application logs to stderr.

    file_logging:
        Whether to write rotating local log files.

    max_bytes:
        Maximum size of one active log file before rotation.

    backup_count:
        Number of rotated log files to retain.

    force:
        If True, application handlers previously installed by
        this function are removed and rebuilt using the supplied
        configuration.

        Third-party/root handlers are never modified.

    Returns
    -------
    logging.Logger
        Configured `event_intelligence` parent logger.
    """

    resolved_level = (
        _resolve_level(
            level
        )
    )

    if max_bytes <= 0:
        raise LoggingConfigurationError(
            "max_bytes must be positive."
        )

    if backup_count < 0:
        raise LoggingConfigurationError(
            "backup_count cannot "
            "be negative."
        )

    filename = (
        str(
            log_filename
        )
        .strip()
    )

    if not filename:
        raise LoggingConfigurationError(
            "log_filename cannot be empty."
        )

    # log_filename is a file name, not another path.
    # Directory selection belongs to log_dir.
    if (
        Path(
            filename
        ).name
        != filename
    ):
        raise LoggingConfigurationError(
            "log_filename must contain "
            "a file name only."
        )

    logger = logging.getLogger(
        LOGGER_NAMESPACE
    )

    logger.setLevel(
        resolved_level
    )

    # Child loggers such as:
    #
    # event_intelligence.source_fetch_service
    #
    # propagate into this logger.
    #
    # This parent then stops propagation into the
    # process-wide root logger to avoid double output.
    logger.propagate = False

    owned_handlers = [
        handler
        for handler
        in logger.handlers
        if _is_owned_handler(
            handler
        )
    ]

    if (
        owned_handlers
        and not force
    ):
        # Updating level is safe without rebuilding
        # the handlers.
        for handler in owned_handlers:
            handler.setLevel(
                resolved_level
            )

        return logger

    if force:
        for handler in owned_handlers:
            logger.removeHandler(
                handler
            )

            try:
                handler.close()

            except Exception:
                # Closing logging infrastructure must not
                # crash application startup/shutdown.
                pass

    formatter = (
        _build_formatter()
    )

    if console:
        console_handler = (
            logging.StreamHandler()
        )

        console_handler.setLevel(
            resolved_level
        )

        console_handler.setFormatter(
            formatter
        )

        logger.addHandler(
            _mark_owned_handler(
                console_handler
            )
        )

    if file_logging:
        resolved_log_dir = Path(
            log_dir
        )

        resolved_log_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        file_handler = (
            RotatingFileHandler(
                resolved_log_dir
                / filename,
                maxBytes=max_bytes,
                backupCount=(
                    backup_count
                ),
                encoding="utf-8",
                delay=True,
            )
        )

        file_handler.setLevel(
            resolved_level
        )

        file_handler.setFormatter(
            formatter
        )

        logger.addHandler(
            _mark_owned_handler(
                file_handler
            )
        )

    # Explicitly support configurations where logging
    # output has intentionally been disabled.
    if (
        not console
        and not file_logging
    ):
        null_handler = (
            logging.NullHandler()
        )

        logger.addHandler(
            _mark_owned_handler(
                null_handler
            )
        )

    return logger


def get_logger(
    component: str | None = None,
) -> logging.Logger:
    """
    Return the application logger or a child logger.

    Examples
    --------

    get_logger()

        event_intelligence

    get_logger(
        "source_fetch_service"
    )

        event_intelligence.source_fetch_service

    get_logger(
        "agents.strategic_inference"
    )

        event_intelligence.agents.strategic_inference

    This function deliberately does not configure logging.
    Application startup should call configure_logging() once.
    """

    if component is None:
        component_name = ""

    else:
        component_name = ".".join(
            part
            for part
            in (
                str(
                    component
                )
                .strip()
                .replace(
                    "/",
                    ".",
                )
                .replace(
                    "\\",
                    ".",
                )
                .split(
                    "."
                )
            )
            if part
        )

    if not component_name:
        return logging.getLogger(
            LOGGER_NAMESPACE
        )

    return logging.getLogger(
        (
            f"{LOGGER_NAMESPACE}."
            f"{component_name}"
        )
    )


# Temporary compatibility handle for existing code using:
#
#     from utils.logger import logger
#
# Unlike the previous implementation, importing this module
# does NOT configure the root logger or create data/logs.
#
# New/revised modules should prefer:
#
#     logger = get_logger(__name__)
#
logger = get_logger()


__all__ = [
    "LOGGER_NAMESPACE",
    "DEFAULT_LOG_DIR",
    "DEFAULT_LOG_FILENAME",
    "DEFAULT_LOG_LEVEL",
    "LoggingConfigurationError",
    "configure_logging",
    "get_logger",
    "logger",
]