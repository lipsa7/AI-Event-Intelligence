from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from utils.deduplicate import (
    DeduplicationError,
    deduplicate_by_key,
    stable_unique,
)
from utils.logger import (
    LOGGER_NAMESPACE,
    configure_logging,
    get_logger,
)


def _reset_application_logger() -> None:
    """
    Keep logging tests isolated from each other.
    """

    logger = logging.getLogger(
        LOGGER_NAMESPACE
    )

    for handler in list(
        logger.handlers
    ):
        logger.removeHandler(
            handler
        )

        try:
            handler.close()

        except Exception:
            pass


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_stable_unique_preserves_first_seen_order() -> None:
    assert stable_unique(
        [
            "A",
            "B",
            "A",
            "C",
            "B",
        ]
    ) == [
        "A",
        "B",
        "C",
    ]


def test_stable_unique_rejects_unhashable_values() -> None:
    with pytest.raises(
        DeduplicationError,
        match="hashable",
    ):
        stable_unique(
            [
                [
                    "not",
                    "hashable",
                ],
            ]
        )


def test_deduplicate_by_key_keep_first() -> None:
    records = [
        {
            "id": "A",
            "value": 1,
        },
        {
            "id": "B",
            "value": 2,
        },
        {
            "id": "A",
            "value": 3,
        },
    ]

    result = deduplicate_by_key(
        records,
        key=(
            lambda item:
            item["id"]
        ),
        keep="first",
    )

    assert result == [
        {
            "id": "A",
            "value": 1,
        },
        {
            "id": "B",
            "value": 2,
        },
    ]


def test_deduplicate_by_key_keep_last_preserves_survivor_positions() -> None:
    records = [
        {
            "id": "A",
            "value": 1,
        },
        {
            "id": "B",
            "value": 2,
        },
        {
            "id": "A",
            "value": 3,
        },
    ]

    result = deduplicate_by_key(
        records,
        key=(
            lambda item:
            item["id"]
        ),
        keep="last",
    )

    # The surviving entries occur at:
    #
    # index 1 -> B
    # index 2 -> final A
    #
    # so output order remains B, A.
    assert result == [
        {
            "id": "B",
            "value": 2,
        },
        {
            "id": "A",
            "value": 3,
        },
    ]


def test_deduplicate_by_key_rejects_invalid_keep_mode() -> None:
    with pytest.raises(
        DeduplicationError,
        match="keep must be",
    ):
        deduplicate_by_key(
            [
                1,
                2,
                1,
            ],
            key=(
                lambda value:
                value
            ),
            keep="middle",  # type: ignore[arg-type]
        )


def test_deduplicate_by_key_rejects_unhashable_key() -> None:
    with pytest.raises(
        DeduplicationError,
        match="hashable",
    ):
        deduplicate_by_key(
            [
                {
                    "id": "A",
                }
            ],
            key=(
                lambda item: [
                    item["id"]
                ]
            ),
        )


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def test_importing_logger_does_not_create_data_log_directory(
    tmp_path: Path,
) -> None:
    """
    Importing utils.logger itself must not create
    data/logs or otherwise mutate the filesystem.

    This uses another Python process so the test really
    exercises module import rather than a module that
    pytest has already imported.
    """

    project_root = (
        Path(__file__)
        .resolve()
        .parents[1]
    )

    env = (
        os.environ.copy()
    )

    existing_pythonpath = (
        env.get(
            "PYTHONPATH",
            "",
        )
    )

    env["PYTHONPATH"] = (
        os.pathsep.join(
            item
            for item in (
                str(
                    project_root
                ),
                existing_pythonpath,
            )
            if item
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "import utils.logger; "
                "print("
                "Path('data/logs').exists()"
                ")"
            ),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert (
        result.stdout.strip()
        == "False"
    )


def test_configure_logging_does_not_modify_root_logger(
    tmp_path: Path,
) -> None:
    _reset_application_logger()

    root_logger = (
        logging.getLogger()
    )

    root_handlers_before = list(
        root_logger.handlers
    )

    configure_logging(
        log_dir=(
            tmp_path
            / "logs"
        ),
        console=False,
        file_logging=True,
        force=True,
    )

    assert list(
        root_logger.handlers
    ) == (
        root_handlers_before
    )

    _reset_application_logger()


def test_configure_logging_is_idempotent(
    tmp_path: Path,
) -> None:
    _reset_application_logger()

    first = configure_logging(
        log_dir=(
            tmp_path
            / "logs"
        ),
        console=True,
        file_logging=False,
        force=True,
    )

    first_handlers = list(
        first.handlers
    )

    second = configure_logging(
        log_dir=(
            tmp_path
            / "logs"
        ),
        console=True,
        file_logging=False,
        force=False,
    )

    assert (
        second
        is first
    )

    assert list(
        second.handlers
    ) == (
        first_handlers
    )

    _reset_application_logger()


def test_file_logging_is_created_only_when_configured(
    tmp_path: Path,
) -> None:
    _reset_application_logger()

    log_dir = (
        tmp_path
        / "logs"
    )

    logger = configure_logging(
        log_dir=log_dir,
        log_filename="test.log",
        console=False,
        file_logging=True,
        force=True,
    )

    assert (
        log_dir.exists()
    )

    logger.warning(
        "test message"
    )

    for handler in (
        logger.handlers
    ):
        handler.flush()

    assert (
        log_dir
        / "test.log"
    ).exists()

    _reset_application_logger()


def test_get_logger_returns_namespaced_child() -> None:
    logger = get_logger(
        "agents.strategic_inference"
    )

    assert logger.name == (
        "event_intelligence."
        "agents.strategic_inference"
    )