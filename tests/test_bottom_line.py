"""12 Nov 2026: the briefing opens with the conclusion (BLUF), the move and what to watch."""
import json
from pathlib import Path

from frontend import dashboard


def test_bottom_line_prefers_released_insight_then_move_then_watch():
    sections = {
        "hidden_insights": [{"insight": "OpenAI will settle with the FTC.",
                             "likelihood_assessment": {"label": "moderate_high"}}],
        "editorial_briefing": {"implications": [{"basis": "trend", "text": "Trend."}]},
        "actor_moves": {"actors": [{"actor": "OpenAI", "choice": {"move": "Publish the incident log."}}]},
        "what_to_watch": [{"signal": "FTC issues civil investigative demands."}],
    }
    lines = dashboard.bottom_line_lines(sections)
    assert lines[0] == "**Bottom line:** OpenAI will settle with the FTC. *Likelihood: likely (55-80%).*"
    assert lines[1].startswith("**What to do next (OpenAI):** Publish") and lines[2].startswith("**Watch for:** FTC")


def test_bottom_line_falls_back_to_leverage_implication_and_is_empty_without_a_judgment():
    sections = {"editorial_briefing": {"implications": [{"basis": "trend", "text": "Trend."},
                                                        {"basis": "leverage", "text": "CAISI holds the reviews."}]}}
    assert dashboard.bottom_line_lines(sections) == ["**Bottom line:** CAISI holds the reviews."]
    assert dashboard.bottom_line_lines({"editorial_briefing": {}}) == []
