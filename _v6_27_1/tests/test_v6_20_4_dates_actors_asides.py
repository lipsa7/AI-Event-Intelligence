"""v6.20.4: event dates, recovered actors and page-furniture filtering, measured
on this app's own extracted events (tests/data/)."""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.event_dates import date_event, parse_publication_date, resolve  # noqa: E402
from services.actor_recovery import recover_events  # noqa: E402
from services.aside_filter import aside_reason, strip_furniture  # noqa: E402
from services.research_orchestrator_service import ResearchOrchestratorService as R  # noqa: E402

DATA = Path(__file__).parent / "data"
EVENTS = json.loads((DATA / "dedup_gold_pairs.json").read_text(encoding="utf-8"))["events"]
PUB = date(2026, 10, 1)  # a Thursday


# ---------------------------------------------------------------- dates
@pytest.mark.parametrize("text,status,expected", [
    ("September 22, 2026", "past", ("2026-09-22", "day")),
    ("Sept. 19", "past", ("2026-09-19", "day")),
    ("Oct 7", "future", ("2026-10-07", "day")),
    ("Nov. 9", "future", ("2026-11-09", "day")),
    ("December", "past", ("2025-12", "month")),          # past-tense month after publication -> last year
    ("22 September", "past", ("2026-09-22", "day")),
    ("Tuesday, Sept. 30, 2025", "past", ("2025-09-30", "day")),
    ("yesterday", "past", ("2026-09-30", "day")),
    ("today", "past", ("2026-10-01", "day")),
    ("3 days ago", "past", ("2026-09-28", "day")),
    ("On Tuesday", "past", ("2026-09-29", "day")),
    ("last Thursday", "past", ("2026-09-24", "day")),
    ("Thursday", "past", ("2026-10-01", "day")),
    ("Sunday night", "future", ("2026-10-04", "day")),
    ("last month", "past", ("2026-09", "month")),
    ("earlier this month", "past", ("2026-10", "month")),
    ("March 2025", "past", ("2025-03", "month")),
    ("2024", "past", ("2024", "year")),
    ("2026-10-01T09:09:01.241Z", "past", ("2026-10-01", "day")),
    ("recently", "past", ("", "")),
    ("this summer", "past", ("", "")),
    ("for now", "ongoing", ("", "")),
    ("in the coming years", "future", ("", "")),
])
def test_time_phrases_resolve_against_the_publication_date(text, status, expected):
    assert resolve(text, PUB, status) == expected


def test_publication_dates_in_the_formats_the_app_stores():
    assert parse_publication_date("2026-09-14 10:48:04.000000") == date(2026, 9, 14)
    assert parse_publication_date("Tue, 29 Sep 2026 08:00:00 GMT") == date(2026, 9, 29)
    assert parse_publication_date("") is None


def test_only_day_precision_becomes_the_event_date():
    month = date_event({"event_time_text": "July", "temporal_status": "past"}, "2026-10-01 00:00:00")
    assert month["published_at"] == "2026-10-01" and not month.get("event_start_at")
    assert month["event_time_resolved"] == "2026-07" and month["event_date_precision"] == "month"
    day = date_event({"event_time_text": "Sept. 22", "temporal_status": "past"}, "2026-10-01 00:00:00")
    assert day["event_start_at"] == "2026-09-22" and day["temporal_precision"] == "day"


def test_run_events_get_their_articles_publication_date():
    class Events:
        def list_by_source_id(self, source_id):
            return [{"event_id": "EVENT-1", "source_id": source_id, "event_time_text": "yesterday",
                     "temporal_status": "past", "event_start_at": None}]

    class Articles:
        calls = 0

        def get_by_source_id(self, source_id):
            Articles.calls += 1
            return {"published_at": "2026-10-05 07:00:00"}

    obj = object.__new__(R)
    obj.event_service = Events()
    obj.article_service = Articles()
    records = obj._persisted_event_records("SOURCE-1") + obj._persisted_event_records("SOURCE-1")
    assert records[0]["published_at"] == "2026-10-05" and records[0]["event_start_at"] == "2026-10-04"
    assert Articles.calls == 1  # cached per source


# ---------------------------------------------------------------- actors
def test_actor_recovery_on_real_events_is_precise():
    labels = json.loads((DATA / "actor_recovery_labels.json").read_text(encoding="utf-8"))["labels"]
    events = [dict(e) for e in EVENTS]
    missing = sum(1 for e in events if not R._event_primary_institution(e))
    recover_events(events, lambda e: bool(R._event_primary_institution(e)))
    recovered = {e["event_id"]: e["primary_actor"] for e in events if e.get("actor_recovered_from")}
    assert set(recovered) == set(labels), "a recovery changed: label the new cases before release"
    correct = sum(1 for eid, actor in recovered.items() if labels[eid]["correct"] and labels[eid]["actor"] == actor)
    print(f"missing={missing} recovered={len(recovered)} precision={correct / len(recovered):.2f}")
    assert len(recovered) >= 65 and correct / len(recovered) >= 0.90


def _event(eid, source, title, mentions):
    return {"event_id": eid, "source_id": source, "title": title, "summary": title,
            "entity_mentions": [{"mention": m, "entity_type": t, "role": r} for m, t, r in mentions]}


def test_actor_recovery_rules():
    events = [
        _event("E1", "S1", "Nvidia's Jensen Huang said AGI has arrived.", [("Jensen Huang", "person", "actor")]),
        _event("E2", "S2", "Pedro Andrade, vice president of AI at Talkdesk, presented the findings.",
               [("Pedro Andrade", "person", "actor")]),
        _event("E3", "S3", "President Donald Trump signed an executive order on AI.", [("Donald Trump", "person", "actor")]),
        _event("E4", "S4", "Trump renames AI 'super intelligence' at the UN", [("Trump", "person", "actor")]),
        _event("E5", "S5", "France and Germany pledged to build an alternative to Palantir.", [("France", "country", "actor")]),
        _event("E6", "S6", "Poke launched in March 2026 as a general-purpose assistant.", [("Poke", "unknown", None)]),
        _event("E7", "S7", "Executives signed a voluntary pledge.", [("executives", "group", "actor")]),
        _event("E8", "S8", "Longtime Microsoft executive Ryan Roslansky will leave.", [("Ryan Roslansky", "person", "actor")]),
        _event("E9", "S9", "Bill Gates said AI is powerful.", [("Bill Gates", "person", "actor")]),
    ]
    recover_events(events, lambda e: bool(e.get("primary_actor")))
    got = {e["event_id"]: e.get("primary_actor", "") for e in events}
    assert got["E1"] == "Nvidia" and got["E2"] == "Talkdesk" and got["E8"] == "Microsoft"
    assert got["E3"] == "Donald Trump"
    assert got["E4"] == "Donald Trump"      # office stated in another article of the run
    assert got["E5"] == "France" and got["E6"] == "Poke"
    assert got["E7"] == "" and got["E9"] == ""  # vague group / person with no stated role stay without actor


# ---------------------------------------------------------------- page furniture
@pytest.mark.parametrize("title,reason", [
    ("WASHINGTON, DC - SEPTEMBER 29: U.S. President Donald Trump waves after speaking during the launch event", "photo_caption"),
    ("From left: House Speaker Mike Johnson; President Donald Trump; Lisa Su, chair and CEO of AMD", "photo_caption"),
    ("President Donald Trump speaks in the Oval Office of the White House before signing an order", "photo_caption"),
    ("It details how AI, energy, and medicine will change by 2030 5 min read", "promotion"),
    ("Boko spoke with theCUBE Research's Christophe Bertrand at NetApp INSIGHT , during an exclusive broadcast on theCUBE", "promotion"),
    ("Read more: Nvidia launches AI safety platform after Jensen Huang calls warnings 'odd'", "related_link"),
])
def test_page_furniture_is_recognised(title, reason):
    assert aside_reason({"title": title}) == reason


@pytest.mark.parametrize("title", [
    "(Kent Nishimura/AFP via Getty Images) · KENT NISHIMURA via Getty Images Amodei published an essay earlier this month",
    "Nvidia's Jensen Huang said AGI has arrived on 7 September.",
    "OpenAI launches Dots, always-on AI agents in ChatGPT with their own cloud computers - SiliconANGLE",
    "Upwind acquires Aegis to build AI Security Labs",
])
def test_real_events_are_not_furniture(title):
    assert aside_reason({"title": title}) == ""


def test_credits_and_labels_are_stripped_from_real_content():
    assert strip_furniture("(Photo by Kevin Dietsch/Getty Images) | Image Credits:Kevin Dietsch / Getty Images "
                           "In December 2025, the U.S. acted").startswith("In December 2025")
    assert strip_furniture("NERMEEN SHAIKH : On Tuesday, President Trump signed").startswith("On Tuesday")


def test_furniture_rate_on_real_events_is_small_and_specific():
    flagged = [e for e in EVENTS if aside_reason(e)]
    assert 8 <= len(flagged) <= 20, len(flagged)  # ~3.5% of 400 extracted events
    assert all(not aside_reason(e) for e in EVENTS if "acquires" in (e.get("title") or "")
               or "raises $" in (e.get("title") or ""))
