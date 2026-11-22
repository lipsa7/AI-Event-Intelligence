"""v13.20: proof line, original vs background split, reader ratings, track record. No model calls."""
import json
import sqlite3

from services import proof_line, reader_ratings, track_record

ORIGINAL = {
    "insight_id": "INSIGHT-1", "insight_revision_id": "REV-1", "text": "Insight one.",
    "analytic_confidence": {"independent_events": 3, "independent_publishers": 4, "key_assumptions": 2},
    "counterevidence_check": {"status": "complete"}, "expert_commentary_checked": 5,
    "forecast_contract": {"question": "Will X happen?", "deadline": "2027-01-31T23:59:59Z"},
}
BACKED = {
    "insight_id": "INSIGHT-2", "insight_revision_id": "REV-2", "text": "California sued OpenAI.",
    "source_backed": {"reasons": ["conclusion_restates_sources"]},
    "analytic_confidence": {"independent_events": 2, "independent_publishers": 3, "key_assumptions": 5},
    "counterevidence_check": {"status": "incomplete"},
}
GROUNDING = {"REV-1": {"claims": [{"grounding_status": "supported"}, {"grounding_status": "inference_supported"},
                                  {"grounding_status": "unsupported"}]}}


def test_proof_line_for_an_original_insight():
    text = proof_line.line(ORIGINAL, sources_read=47, grounding_by_insight=GROUNDING)
    assert text == ("Not stated by any of the 47 sources read · combines 3 stories from 4 publishers · "
                    "claim support 2/3 · 2 reasoning steps are assumptions · contrary-evidence search done · "
                    "checked against 5 passages of published analysis · forecast open until 2027-01-31")


def test_background_finding_is_labelled_and_split_out():
    text = proof_line.line(BACKED, sources_read=47)
    assert text.startswith("Already said by its own sources (background, not an original insight)")
    assert "contrary-evidence search incomplete" in text
    originals, background = proof_line.split([BACKED, ORIGINAL, "junk"])
    assert [i["insight_id"] for i in originals] == ["INSIGHT-1"]
    assert [i["insight_id"] for i in background] == ["INSIGHT-2"]


def test_reader_ratings_latest_answer_counts(tmp_path, monkeypatch):
    monkeypatch.setenv(reader_ratings.PATH_ENV, str(tmp_path / "r.jsonl"))
    assert reader_ratings.answer_for("INSIGHT-1", "new_to_me") is None
    reader_ratings.save(report_id="R", insight_id="INSIGHT-1", question="new_to_me", answer=True)
    reader_ratings.save(report_id="R", insight_id="INSIGHT-1", question="new_to_me", answer=False)
    assert reader_ratings.answer_for("INSIGHT-1", "new_to_me") is False
    try:
        reader_ratings.save(report_id="R", insight_id="X", question="other", answer=True)
        raise AssertionError("unknown question accepted")
    except ValueError:
        pass


def make_data(root):
    (root / "forecasts").mkdir(parents=True)
    with sqlite3.connect(str(root / "reports.sqlite3")) as c:
        c.execute("create table reports (report_id, research_question, current_revision_id, state, created_at, updated_at)")
        c.execute("create table report_revisions (report_revision_id, report_id, revision_no, payload_json, "
                  "report_fingerprint, release_decision_id, state, created_at)")
        payloads = [[ORIGINAL, BACKED], [], [dict(ORIGINAL, insight_id="INSIGHT-3")]]
        for n, items in enumerate(payloads):
            c.execute("insert into reports values (?,?,?,?,?,?)", (f"R{n}", "AI", f"V{n}", "released", f"2026-11-0{n+1}", ""))
            c.execute("insert into report_revisions values (?,?,?,?,?,?,?,?)",
                      (f"V{n}", f"R{n}", 1, json.dumps({"sections": {"hidden_insights": items}}), "", "", "released", ""))
    with sqlite3.connect(str(root / "forecasts" / "forecasts.sqlite3")) as c:
        c.execute("create table forecasts (forecast_id, run_id, created_at, payload)")
        c.execute("create table resolutions (forecast_id, resolved_at, outcome, evidence_url, evidence_note, brier_score)")
        for n in range(4):
            c.execute("insert into forecasts values (?,?,?,?)", (f"F{n}", "RUN", "", "{}"))
        c.execute("insert into resolutions values ('F0','',1,'','',0.09)")
        c.execute("insert into resolutions values ('F1','',0,'','',0.25)")


def test_track_record_counts_only_what_is_on_disk(tmp_path):
    make_data(tmp_path)
    ratings = {("INSIGHT-1", "new_to_me"): {"answer": True, "original": True},
               ("INSIGHT-3", "new_to_me"): {"answer": False, "original": True},
               ("INSIGHT-2", "new_to_me"): {"answer": True, "original": False}}
    record = track_record.compute(tmp_path, ratings=ratings)
    assert record["runs_saved"] == 3
    assert record["runs_with_original_insight"] == 2
    assert record["abstention_share"] == 33.3
    assert record["original_insights"] == 2 and record["background_findings_shown"] == 1
    assert record["repeated_original_insights"] == 1          # same text twice
    assert (record["forecasts_made"], record["forecasts_resolved"], record["brier"]) == (4, 2, 0.17)
    assert record["brier_credible"] is False
    assert record["ratings"]["new_to_me"] == {"n": 2, "yes": 1, "share": 50.0, "plus_minus": 69.3, "credible": False}
    lines = list(track_record.summary_lines(record))
    assert any("not credible below 100 resolved" in line for line in lines)


def test_interval_matches_binomial_arithmetic():
    assert track_record.interval(25, 50) == 13.9          # about +/-14 points at n=50
    assert track_record.interval(50, 100) == 9.8
    assert track_record.interval(0, 0) == 0.0


def test_track_record_with_no_data(tmp_path):
    record = track_record.compute(tmp_path, ratings={})
    assert record["runs_saved"] == 0 and record["abstention_share"] is None and record["brier"] is None
