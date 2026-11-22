"""v13.27.2 (22 Nov 2026): bugs found reviewing the paths the test setup switches off."""
from datetime import datetime, timedelta, timezone

from services import editorial_briefing, variant_view
from services.research_orchestrator_service import ResearchOrchestratorService as S

TODAY = datetime(2026, 11, 22, tzinfo=timezone.utc)


def _cand(text):
    return {"insight": text, "leverage_analysis": {"holder": "OpenAI"},
            "forecast_contract": {"question": "Will Australia set a deadline?",
                                  "deadline": (TODAY + timedelta(days=40)).isoformat(), "resolution_criteria": "notice"}}


def test_a_date_or_number_word_counts_as_a_number():
    # Digits only: "the June breach was disclosed in September, three months later" failed "no_number".
    assert "no_number" not in variant_view.structure_problems(
        _cand("Australia learned of the June breach in September, three months late."), today=TODAY)
    assert "no_number" not in variant_view.structure_problems(_cand("OpenAI waited a dozen weeks."), today=TODAY)
    assert "no_number" in variant_view.structure_problems(_cand("OpenAI waited and then told Australia."), today=TODAY)


def test_two_per_story_go_on_and_the_rest_are_drafts(monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_VARIANT_VIEW", "1")
    monkeypatch.setenv("OUTSIDE_JUDGE", "0")
    service = S.__new__(S)
    texts = [f"Lens {n}: OpenAI told Australia 3 months late, so a deadline follows." for n in range(4)]
    entry = {}
    kept = service._variant_select([_cand(t) for t in texts], {}, entry, [])
    assert [k["insight"] for k in kept] == texts[:2]
    assert [d["insight"] for d in entry["variant_dropped"]] == texts[2:]
    assert all(d["problems"] == ["ranked_below_slot_top"] for d in entry["variant_dropped"])


def test_a_single_watch_signal_is_not_split_into_letters():
    lines = editorial_briefing.anchor_lines([{"text": "x", "metadata": {"what_to_watch": "Australian notice"}}])
    assert lines == ["- x | watch: Australian notice"]


def test_base_rate_ignores_malformed_entries():
    assert variant_view.base_rate([None, "x", {"similarity": 8, "outcome_followed": "yes"}])["cases_used"] == 1


def test_ordinary_words_do_not_count_as_numbers():
    # review of v13.27.2: "may" and "one" are not a cited number or date.
    assert "no_number" in variant_view.structure_problems(
        _cand("OpenAI may face one more suit and a double challenge."), today=TODAY)
    assert "no_number" not in variant_view.structure_problems(_cand("OpenAI must answer by May 2027."), today=TODAY)
