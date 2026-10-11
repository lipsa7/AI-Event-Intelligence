"""v6.20.5: forecasts (briefing implications) are recorded and followed up in later runs."""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.forecast_followup import ForecastFollowup, horizon_until, MAX_CHECKS_PER_RUN  # noqa: E402
from services.research_orchestrator_service import ResearchOrchestratorService as R  # noqa: E402

MADE = date(2026, 10, 9)
IMPLICATION = {
    "basis": "trend", "text": "More identity vendors may add kill switches for AI agents.",
    "watch_for": "another identity vendor launches an AI agent kill switch", "horizon": "6-12 months",
    "conditions": "enterprises keep giving agents system access",
    "citations": [{"fact_id": "F1", "evidence_id": "EVID-OKTA", "source_id": "SOURCE-1", "date": "2026-10-08"}],
}


def new_record(eid, text, day, event_id="EVENT-9"):
    return {"evidence_id": eid, "source_id": "SOURCE-" + eid, "event_id": event_id,
            "evidence_type": "source_excerpt", "source_excerpt": text, "published_at": day}


def date_of(record):
    return record.get("published_at", "")


class Grounder:
    def __init__(self, status="supported", error=False):
        self.status, self.error, self.calls = status, error, []

    def __call__(self, claims, records):
        self.calls.append(([r["evidence_id"] for r in records], claims[0]["claim_text"]))
        if self.error:
            raise RuntimeError("down")
        ids = [r["evidence_id"] for r in records]
        return {"claims": [{"grounding_status": self.status,
                            "supporting_evidence_ids": ids[:1] if self.status == "supported" else [],
                            "contradicted_evidence_ids": ids[:1] if self.status == "contradicted" else []}]}


@pytest.fixture
def ledger(tmp_path):
    service = ForecastFollowup(tmp_path / "watch.sqlite3")
    service.record(run_id="RUN-1", topic="artificial intelligence", implications=[IMPLICATION], made_on=MADE,
                   development_of=lambda item: ("agent kill switches", ["Okta"]))
    return service


@pytest.mark.parametrize("text,expected,parsed", [
    ("6-12 months", date(2027, 10, 4), True),
    ("next quarter", date(2027, 1, 9), True),
    ("within weeks", date(2027, 4, 7), False),
    ("by the end of 2027", date(2027, 12, 31), True),
    ("2027", date(2027, 12, 31), True),
    ("near term", date(2027, 1, 7), True),
    ("two years", date(2028, 10, 8), True),
    ("", date(2027, 4, 7), False),
])
def test_horizons_become_deadlines(text, expected, parsed):
    assert horizon_until(text, MADE) == (expected, parsed)


def test_same_implication_is_recorded_once(ledger):
    again = ledger.record(run_id="RUN-2", topic="artificial intelligence", implications=[IMPLICATION], made_on=MADE)
    assert again == 0 and len(ledger.all_items()) == 1
    item = ledger.all_items()[0]
    assert item["status"] == "open" and item["development"] == "agent kill switches"
    assert json.loads(item["actors"]) == ["Okta"] and item["horizon_until"] == "2027-10-04"


def test_signal_reported_in_a_later_run_is_observed_with_its_source(ledger):
    grounder = Grounder("supported")
    evidence = [new_record("EVID-NEW", "CyberArk launches an AI agent kill switch for identity teams.", "2026-11-02"),
                new_record("EVID-OFF", "Apple reports quarterly revenue.", "2026-11-02")]
    result = ledger.check(run_id="RUN-2", topic="artificial intelligence", evidence=evidence,
                          today=date(2026, 11, 3), date_of=date_of, ground=grounder)
    assert result["summary"]["observed"] == 1 and result["resolved"][0]["status"] == "observed"
    assert result["resolved"][0]["evidence"][0]["evidence_id"] == "EVID-NEW"
    assert grounder.calls[0][0] == ["EVID-NEW"]                         # unrelated record not sent
    assert grounder.calls[0][1].startswith("This has happened or has been reported:")
    assert ledger.all_items()[0]["status"] == "observed" and ledger.open_items() == []


def test_only_news_published_after_the_forecast_can_resolve_it(ledger):
    grounder = Grounder("supported")
    evidence = [new_record("EVID-OLD", "CyberArk launches an AI agent kill switch.", "2026-10-09"),
                new_record("EVID-OKTA", "Okta launches an AI agent kill switch.", "2026-11-01")]  # the forecast's own source
    result = ledger.check(run_id="RUN-2", topic="artificial intelligence", evidence=evidence,
                          today=date(2026, 11, 3), date_of=date_of, ground=grounder)
    assert grounder.calls == [] and result["summary"]["still_open"] == 1


def test_contradiction_is_recorded(ledger):
    evidence = [new_record("EVID-NO", "Identity vendors say they will not launch an AI agent kill switch.", "2026-12-01")]
    result = ledger.check(run_id="RUN-3", topic="artificial intelligence", evidence=evidence,
                          today=date(2026, 12, 2), date_of=date_of, ground=Grounder("contradicted"))
    assert result["resolved"][0]["status"] == "contradicted"


def test_deadline_passed_without_the_signal_expires(ledger):
    result = ledger.check(run_id="RUN-9", topic="artificial intelligence", evidence=[],
                          today=date(2027, 11, 1), date_of=date_of, ground=Grounder())
    assert result["summary"]["expired"] == 1
    assert ledger.track_record()["counts"] == {"expired": 1} and ledger.track_record()["observed_share"] == 0.0


def test_check_failure_leaves_the_forecast_open(ledger):
    evidence = [new_record("EVID-NEW", "CyberArk launches an AI agent kill switch.", "2026-11-02")]
    result = ledger.check(run_id="RUN-2", topic="artificial intelligence", evidence=evidence,
                          today=date(2026, 11, 3), date_of=date_of, ground=Grounder(error=True))
    assert result["summary"]["still_open"] == 1 and ledger.open_items()


def test_unsupported_signal_stays_open(ledger):
    evidence = [new_record("EVID-NEW", "CyberArk discusses AI agent kill switch ideas.", "2026-11-02")]
    result = ledger.check(run_id="RUN-2", topic="artificial intelligence", evidence=evidence,
                          today=date(2026, 11, 3), date_of=date_of, ground=Grounder("partially_supported"))
    assert result["summary"]["still_open"] == 1 and result["resolved"] == []


def test_replay_check_does_not_write(ledger):
    evidence = [new_record("EVID-NEW", "CyberArk launches an AI agent kill switch.", "2026-11-02")]
    result = ledger.check(run_id="RUN-R", topic="artificial intelligence", evidence=evidence,
                          today=date(2026, 11, 3), date_of=date_of, ground=Grounder(), persist=False)
    assert result["summary"]["observed"] == 1 and ledger.all_items()[0]["status"] == "open"


def test_model_calls_per_run_are_capped(tmp_path):
    service = ForecastFollowup(tmp_path / "w.sqlite3")
    items = [dict(IMPLICATION, text=f"Forecast {i} about agent kill switches.") for i in range(MAX_CHECKS_PER_RUN + 4)]
    service.record(run_id="RUN-1", topic="artificial intelligence", implications=items, made_on=MADE)
    grounder = Grounder("partially_supported")
    service.check(run_id="RUN-2", topic="artificial intelligence",
                  evidence=[new_record("EVID-NEW", "A vendor launches an AI agent kill switch.", "2026-11-02")],
                  today=date(2026, 11, 3), date_of=date_of, ground=grounder)
    assert len(grounder.calls) == MAX_CHECKS_PER_RUN


# ---------------------------------------------------------------- orchestrator
def _orchestrator(tmp_path, status="supported"):
    obj = object.__new__(R)
    obj.forecast_followup_service = ForecastFollowup(tmp_path / "watch.sqlite3")
    grounder = Grounder(status)
    obj.grounding_service = type("G", (), {"ground_claims": lambda self, claims, evidence_records:
                                           grounder(claims, evidence_records)})()
    return obj, grounder


def test_orchestrator_records_then_follows_up_in_a_later_run(tmp_path, monkeypatch):
    import services.forecast_followup as module
    obj, grounder = _orchestrator(tmp_path)
    briefing = {"implications": [IMPLICATION]}
    report = {"research_depth": "deep", "developments_to_watch": [
        {"title": "agent kill switches", "actors": ["Okta"], "facts": [{"evidence_id": "EVID-OKTA"}]}]}
    monkeypatch.setattr(module, "today_utc", lambda: MADE)
    first = obj._forecast_followups(report=report, topic="artificial intelligence", events=[], evidence=[],
                                    state={"run_id": "RUN-1"}, briefing=briefing)
    assert first["summary"]["open_before"] == 0 and first["summary"]["recorded_this_run"] == 1
    monkeypatch.setattr(module, "today_utc", lambda: date(2026, 11, 3))
    events = [{"event_id": "EVENT-9", "published_at": "2026-11-02"}]
    evidence = [dict(new_record("EVID-NEW", "CyberArk launches an AI agent kill switch.", ""), event_id="EVENT-9")]
    second = obj._forecast_followups(report={"research_depth": "deep"}, topic="artificial intelligence",
                                     events=events, evidence=evidence, state={"run_id": "RUN-2"},
                                     briefing={"implications": []})
    assert second["summary"]["observed"] == 1                           # date taken from the event
    assert second["resolved"][0]["evidence"][0]["date"] == "2026-11-02"


def test_replay_and_fast_runs_never_record(tmp_path, monkeypatch):
    obj, _ = _orchestrator(tmp_path)
    briefing = {"implications": [IMPLICATION]}
    obj._forecast_followups(report={"research_depth": "deep"}, topic="t", events=[], evidence=[],
                            state={"run_id": "RUN-R", "replayed_from": {"run_id": "RUN-1"}}, briefing=briefing)
    assert obj._forecast_followups(report={"research_depth": "fast"}, topic="t", events=[], evidence=[],
                                   state={"run_id": "RUN-F"}, briefing=briefing) == {}
    assert obj.forecast_followup_service.all_items() == []


def test_follow_up_failure_never_breaks_the_report(tmp_path):
    obj, _ = _orchestrator(tmp_path)
    obj.forecast_followup_service = None
    obj._forecast_service = lambda: (_ for _ in ()).throw(OSError("disk"))
    out = obj._forecast_followups(report={"research_depth": "deep"}, topic="t", events=[], evidence=[],
                                  state={"run_id": "RUN-1"}, briefing={"implications": [IMPLICATION]})
    assert out == {"summary": {"error": "OSError"}}


# ---------------------------------------------------------------- dashboard
def test_dashboard_shows_follow_ups():
    from tests.test_v6_17_report_sections import _render
    sections = {"editorial_briefing": {"headline": "H", "paragraphs": [], "implications": [], "analogies": []},
                "forecast_followups": {
                    "summary": {"open_before": 2, "still_open": 1},
                    "resolved": [{"status": "observed", "implication": "More vendors may add kill switches.",
                                  "made_on": "2026-10-09", "watch_for": "another vendor launches one",
                                  "evidence": [{"date": "2026-11-02", "source_id": "SOURCE-X",
                                                "text": "CyberArk launches an AI agent kill switch."}]}],
                    "track_record": {"decided": 1, "observed_share": 1.0}}}
    calls = _render(sections)
    text = " ".join(str(arg) for _, arg in calls)
    assert "Follow-ups on earlier forecasts" in text and "Signal reported" in text
    assert "CyberArk launches" in text and "100% of signals later reported" in text


def test_dashboard_first_run_message():
    from tests.test_v6_17_report_sections import _render
    calls = _render({"editorial_briefing": {"headline": "H", "paragraphs": [], "implications": [], "analogies": []},
                     "forecast_followups": {"summary": {"open_before": 0}}})
    assert any("No earlier forecasts to follow up yet" in str(arg) for _, arg in calls)


def test_calls_without_a_real_run_id_never_touch_the_ledger(tmp_path):
    obj, _ = _orchestrator(tmp_path)
    assert obj._forecast_followups(report={"research_depth": "deep"}, topic="t", events=[], evidence=[],
                                   state={}, briefing={"implications": [IMPLICATION]}) == {}
    assert obj.forecast_followup_service.all_items() == []

# ---------------------------------------------------------------- v6.20.6 integrity regressions

def test_different_evidence_row_from_same_original_source_cannot_self_confirm(ledger):
    grounder = Grounder("supported")
    evidence = [{
        "evidence_id": "EVID-DIFFERENT-ROW",
        "source_id": "SOURCE-1",  # same source_id as the forecast's original citation
        "event_id": "EVENT-NEW",
        "evidence_type": "source_excerpt",
        "source_excerpt": "Okta launches another AI agent kill switch for identity teams.",
        "published_at": "2026-11-02",
    }]
    result = ledger.check(run_id="RUN-2", topic="artificial intelligence", evidence=evidence,
                          today=date(2026, 11, 3), date_of=date_of, ground=grounder)
    assert grounder.calls == []
    assert result["summary"]["observed"] == 0
    assert result["summary"]["still_open"] == 1


def test_grounding_failure_after_deadline_still_leaves_forecast_open(ledger):
    evidence = [new_record("EVID-NEW", "CyberArk launches an AI agent kill switch.", "2027-11-01")]
    result = ledger.check(run_id="RUN-FAIL", topic="artificial intelligence", evidence=evidence,
                          today=date(2027, 11, 2), date_of=date_of, ground=Grounder(error=True))
    assert result["summary"]["check_failures"] == 1
    assert result["summary"]["expired"] == 0
    assert result["summary"]["still_open"] == 1
    assert ledger.open_items(topic="artificial intelligence")


def test_forecasts_deferred_by_check_cap_do_not_false_expire(tmp_path):
    service = ForecastFollowup(tmp_path / "cap.sqlite3")
    implications = [dict(IMPLICATION,
                         text=f"Forecast {i}: identity vendors may add agent kill switches.",
                         watch_for=f"identity vendor {i} launches an AI agent kill switch",
                         horizon="near term")
                    for i in range(MAX_CHECKS_PER_RUN + 2)]
    service.record(run_id="RUN-1", topic="artificial intelligence", implications=implications, made_on=MADE)
    # All forecasts have relevant candidate evidence; only MAX_CHECKS_PER_RUN may be grounded.
    evidence = [new_record("EVID-NEW", "Identity vendor 9 launches an AI agent kill switch.", "2027-02-01")]
    result = service.check(run_id="RUN-2", topic="artificial intelligence", evidence=evidence,
                           today=date(2027, 2, 2), date_of=date_of, ground=Grounder("partially_supported"))
    assert result["summary"]["checked_with_new_evidence"] == MAX_CHECKS_PER_RUN
    assert result["summary"]["deferred_due_check_budget"] == 2
    assert result["summary"]["still_open"] == 2
    assert result["summary"]["expired"] == MAX_CHECKS_PER_RUN


def test_ai_topic_does_not_pull_unrelated_open_forecasts(tmp_path):
    service = ForecastFollowup(tmp_path / "topics.sqlite3")
    service.record(run_id="RUN-A", topic="artificial intelligence", implications=[IMPLICATION], made_on=MADE)
    other = dict(IMPLICATION,
                 text="The central bank may tighten mortgage capital rules.",
                 watch_for="the central bank tightens mortgage capital rules")
    service.record(run_id="RUN-B", topic="banking regulation", implications=[other], made_on=MADE)
    ai_items = service.open_items(topic="artificial intelligence")
    assert len(ai_items) == 1
    assert ai_items[0]["topic"] == "artificial intelligence"
    # A generic shared word such as "regulation" must not cross-contaminate topics.
    assert service.open_items(topic="AI regulation")[0]["topic"] == "artificial intelligence"
    assert all(i["topic"] != "banking regulation" for i in service.open_items(topic="AI regulation"))


def test_resolution_preserves_human_readable_source_metadata(ledger):
    record = new_record("EVID-NEWS", "CyberArk launches an AI agent kill switch.", "2026-11-02")
    record.update({
        "source_name": "Reuters",
        "source_url": "https://www.reuters.com/example/story?utm_source=test",
    })
    result = ledger.check(run_id="RUN-2", topic="artificial intelligence", evidence=[record],
                          today=date(2026, 11, 3), date_of=date_of, ground=Grounder("supported"))
    ev = result["resolved"][0]["evidence"][0]
    assert ev["source"] == "Reuters"
    assert ev["source_url"] == "https://www.reuters.com/example/story"


def test_dashboard_prefers_reporting_source_over_internal_source_id():
    from tests.test_v6_17_report_sections import _render
    sections = {"editorial_briefing": {"headline": "H", "paragraphs": [], "implications": [], "analogies": []},
                "forecast_followups": {
                    "summary": {"open_before": 1, "still_open": 0},
                    "resolved": [{"status": "observed", "implication": "More vendors may add kill switches.",
                                  "made_on": "2026-10-09", "watch_for": "another vendor launches one",
                                  "evidence": [{"date": "2026-11-02", "source_id": "SOURCE-X",
                                                "source": "Reuters", "source_url": "https://reuters.com/example",
                                                "text": "CyberArk launches an AI agent kill switch."}]}],
                    "track_record": {"decided": 1, "observed_share": 1.0}}}
    calls = _render(sections)
    text = " ".join(str(arg) for _, arg in calls)
    assert "Reuters" in text and "https://reuters.com/example" in text


def test_same_original_url_with_different_source_id_cannot_self_confirm(tmp_path):
    service = ForecastFollowup(tmp_path / "url-self.sqlite3")
    implication = dict(IMPLICATION, citations=[{
        "evidence_id": "EVID-ORIGINAL", "source_id": "SOURCE-ORIGINAL",
        "source_url": "https://publisher.example/story?utm_source=forecast"}])
    service.record(run_id="RUN-1", topic="artificial intelligence", implications=[implication], made_on=MADE)
    record = new_record("EVID-DIFFERENT", "CyberArk launches an AI agent kill switch.", "2026-11-02")
    record.update({"source_id": "SOURCE-ALIAS", "source_url": "https://publisher.example/story?utm_source=later"})
    grounder = Grounder("supported")
    result = service.check(run_id="RUN-2", topic="artificial intelligence", evidence=[record],
                           today=date(2026, 11, 3), date_of=date_of, ground=grounder)
    assert grounder.calls == []
    assert result["summary"]["still_open"] == 1


# ---------------------------------------------------------------- v6.20.7 (merged Claude + GPT v6.20.6)
def test_only_forecasts_from_the_same_topic_are_followed_up(tmp_path):
    service = ForecastFollowup(tmp_path / "w.sqlite3")
    service.record(run_id="RUN-1", topic="artificial intelligence", implications=[IMPLICATION], made_on=MADE)
    service.record(run_id="RUN-B", topic="banking regulation",
                   implications=[dict(IMPLICATION, text="Banks may tighten capital rules.")], made_on=MADE)
    assert [i["topic"] for i in service.open_items(topic="Artificial  Intelligence")] == ["artificial intelligence"]
    assert [i["topic"] for i in service.open_items(topic="AI")] == ["artificial intelligence"]
    assert [i["topic"] for i in service.open_items(topic="banking regulation")] == ["banking regulation"]


def test_ledger_connections_are_closed(tmp_path):
    service = ForecastFollowup(tmp_path / "w.sqlite3")
    service.record(run_id="RUN-1", topic="t", implications=[IMPLICATION], made_on=MADE)
    service.open_items()
    service.all_items()
    (tmp_path / "w.sqlite3").unlink()  # fails on Windows if any handle were left open


def test_follow_up_names_the_publisher_from_the_article_store(tmp_path, monkeypatch):
    # Pipeline evidence records carry only a source id; the publisher and link
    # come from the article store, so the report never shows an internal id.
    import services.forecast_followup as module
    obj, _ = _orchestrator(tmp_path)
    obj._source_reference = lambda sid: {"publisher": "TechCrunch", "title": "T", "url": "https://example.com/a"}
    monkeypatch.setattr(module, "today_utc", lambda: MADE)
    obj._forecast_followups(report={"research_depth": "deep"}, topic="artificial intelligence", events=[], evidence=[],
                            state={"run_id": "RUN-1"}, briefing={"implications": [IMPLICATION]})
    monkeypatch.setattr(module, "today_utc", lambda: date(2026, 11, 3))
    out = obj._forecast_followups(
        report={"research_depth": "deep"}, topic="artificial intelligence", events=[],
        evidence=[new_record("EVID-NEW", "CyberArk launches an AI agent kill switch.", "2026-11-02")],
        state={"run_id": "RUN-2"}, briefing={"implications": []})
    ev = out["resolved"][0]["evidence"][0]
    assert ev["source"] == "TechCrunch" and ev["source_url"] == "https://example.com/a"
    from tests.test_v6_17_report_sections import _render
    calls = _render({"editorial_briefing": {"headline": "H", "paragraphs": [], "implications": [], "analogies": []},
                     "forecast_followups": out})
    assert any("[TechCrunch](https://example.com/a)" in str(arg) for _, arg in calls)
