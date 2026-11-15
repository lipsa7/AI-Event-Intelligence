"""
Application entry point for AI Event Intelligence.

Run:

    streamlit run app.py

Research execution lives in ResearchOrchestratorService.
User interaction lives in dashboard.py.
"""

from __future__ import annotations

from utils.logger import (
    configure_logging,
    get_logger,
)


APP_VERSION = "event-intelligence-app-v1.0"


def main() -> None:
    configure_logging()

    logger = get_logger(
        "app"
    )

    logger.info(
        "Starting AI Event Intelligence | version=%s",
        APP_VERSION,
    )

    from frontend.dashboard import (
        main as dashboard_main,
    )

    dashboard_main()


if __name__ == "__main__":
    main()