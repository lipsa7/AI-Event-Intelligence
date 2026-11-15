"""v6.36.6 - research review of v6.36.5 ("are these best practice?"):
1. Blind rating (QUEST framework; Si et al. blind review): compare_runs.py --blind-export writes the items
   of all runs shuffled and unlabelled; --blind-import reads the ratings back, marked blind.
2. Noise floor (agentic-eval randomness study): if a second baseline replay exists, the baseline-vs-
   baseline gap is shown next to the baseline-vs-profile gap, and the verdict says which is larger.
3. The user's own hypotheses go into the evolution prompt UNLABELLED (SycEval: models side with users).
4. A base-rate count says it is a small sample found by search (reference-class forecasting).
5. Corroboration (Admiralty / NATO system): how many different publishers back an implication's facts.
"""

from __future__ import annotations

import csv
import importlib
import io
import json
import sqlite3
import sys
import types
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from services import editorial_briefing as brief
from services import insight_ratings
from services import strategic_hypothesis_lab as lab
from tests.test_v6_32_hypothesis_lab import FACTS as LAB_FACTS, rich_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
compare = importlib.import_module("compare_runs")


def _report(report_id, profile, texts, created):
    sections = {
        "insight_evaluation": {"items": [{"ref": f"r{n}", "text": t, "rank": n + 1, "kind": "implication",
                                          "provenance_label": "Leverage decision"} for n, t in enumerate(texts)]},
        "analysis_metadata": {
            "experiment": {"replay_of": "RUN-1", "focus_organisation": "OpenAI", "advice_produced": True,
                           "service_version": "v", "topic": "t", "user_hypotheses": ""},
            "organisation_profile": {"used": profile, "name": "OpenAI" if profile else ""}}}
    return {"report_id": report_id, "run_id": "RUN-" + report_id, "sections": sections, "_created": created}


A = _report("REP-A", False, ["Shared item about logs.", "Alpha one.", "Alpha two."], "1")
B = _report("REP-B", True, ["Shared item about logs.", "Beta one.", "Beta two."], "2")
A2 = _report("REP-A2", False, ["Gamma one.", "Alpha one.", "Gamma two."], "3")


def _db(tmp_path):
    db = tmp_path / "reports.sqlite3"
    with sqlite3.connect(str(db)) as conn:
        conn.execute("CREATE TABLE report_revisions(report_revision_id TEXT, report_id TEXT, revision_no INTEGER, "
                     "payload_json TEXT, report_fingerprint TEXT, release_decision_id TEXT, state TEXT, created_at TEXT)")
        for r in (A, B, A2):
            conn.execute("INSERT INTO report_revisions VALUES (?,?,?,?,?,?,?,?)",
                         (r["report_id"] + "-1", r["report_id"], 1,
                          json.dumps({"run_id": r["run_id"], "sections": r["sections"]}), "f", "", "s", r["_created"]))
    return db


def test_blind_export_hides_which_run_and_import_restores_it(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "BLIND_CSV", tmp_path / "blind.csv")
    monkeypatch.setattr(compare, "BLIND_KEY", tmp_path / "key.json")
    count = compare.blind_export([A, B], seed=7)
    assert count == 5                                         # the shared item appears once
    text = (tmp_path / "blind.csv").read_text(encoding="utf-8-sig")
    for leak in ("REP-A", "REP-B", "profile", "baseline", "Leverage decision", "#1"):
        assert leak.casefold() not in text.casefold()
    rows = list(csv.reader(io.StringIO(text)))[1:]
    with open(tmp_path / "blind.csv", "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["no", "text", "rating", "reason"])
        for row in rows:
            no, item_text = row[:2]
            writer.writerow([no, item_text, "useful" if "Beta" in item_text or "Shared" in item_text else
                             "maybe" if item_text == "Alpha two." else "poor", ""])
    target = tmp_path / "ratings.jsonl"
    saved, problems = compare.blind_import(target=target)
    assert saved == 5 and len(problems) == 1 and "maybe" in problems[0]   # shared item saved for both runs
    ratings = insight_ratings.load(target)
    assert all(r["blind"] for r in ratings)
    assert insight_ratings.summary(ratings, "REP-B")["useful"] == 3


def test_noise_floor_is_shown_and_decides(tmp_path, monkeypatch):
    db = _db(tmp_path)
    ratings = tmp_path / "ratings.jsonl"
    monkeypatch.setenv("EVENT_INTELLIGENCE_RATINGS", str(ratings))
    assert compare.noise_problems(A, A2) == [] and compare.noise_problems(A, B)
    assert compare.noise_partner(db, A)["report_id"] == "REP-A2"

    def rate(report, verdicts):
        insight_ratings.save([{"report_id": report["report_id"], "item_key": f"r{n}", "text": t, "rating": v,
                               "blind": True}
                              for n, (t, v) in enumerate(zip([i["text"] for i in report["sections"]
                                                              ["insight_evaluation"]["items"]], verdicts))])
    rate(A, ["useful", "poor", "poor"])
    rate(B, ["useful", "useful", "poor"])
    rate(A2, ["useful", "useful", "useful"])                  # two identical setups differ a lot: noise
    monkeypatch.setattr(sys, "argv", ["compare_runs.py", "REP-A", "REP-B", "--db", str(db)])
    out = io.StringIO()
    with redirect_stdout(out):
        assert compare.main() == 0
    text = out.getvalue()
    # v6.36.7 (GPT review): direction is stated; two baselines are a reference, not a noise verdict
    assert "Rated blind" in text and "rated BETTER by +0.33" in text
    assert "two baseline runs differed by +0.67" in text and "not a reliable estimate of chance" in text


def test_user_hypotheses_are_mixed_in_unlabelled():
    rows = [{"id": "U1", "text": "Governments will demand direct access to agent logs in AI contracts."}]
    raw = lab.parse_generation({"hypotheses": [rich_row()]}, LAB_FACTS)
    prompt = lab.evolution_prompt("q", raw, LAB_FACTS, user_hypotheses=rows)
    assert '"id": "B0H1"' in prompt and "U1" not in prompt and "user" not in prompt.casefold().split("raw hypotheses")[1]
    assert lab.user_id_from_source("B0H1") == "U1" and lab.user_id_from_source("B1H1") == ""


def test_base_rate_is_worded_as_a_small_search_sample():
    assert "never present the count as a measured probability" in brief.build_prompt("ai", [], [])
    text = (ROOT / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    assert "a small sample found by search, not a full base rate; indicative only" in text


@pytest.fixture
def dash(monkeypatch):
    fake = types.ModuleType("streamlit")
    fake.__getattr__ = lambda name: (lambda *a, **k: None)
    fake.cache_resource = fake.cache_data = lambda *a, **k: (lambda function: function)
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    monkeypatch.delitem(sys.modules, "frontend.dashboard", raising=False)
    yield importlib.import_module("frontend.dashboard")
    sys.modules.pop("frontend.dashboard", None)


def test_corroboration_counts_different_publishers_per_fact(dash):
    # v6.36.7 (GPT review): two publishers backing two DIFFERENT facts is not corroboration.
    sources = {"S1": {"publisher": "Reuters"}, "S2": {"url": "https://www.abc.net.au/news/x"},
               "S3": {"publisher": "reuters"}}
    assert dash.corroboration_note([{"claim": "logs", "source_ids": ["S1"]},
                                    {"claim": "pause", "source_ids": ["S2"]}], sources) \
        == "its stated facts each rest on one publisher (not independently confirmed)"
    assert dash.corroboration_note([{"claim": "logs", "source_ids": ["S1", "S2"]}], sources) \
        == "its stated fact is backed by 2 or more different publishers"
    assert dash.corroboration_note([{"claim": "logs", "source_ids": ["S1", "S3"]}], sources) \
        == "its stated fact rests on one publisher (not independently confirmed)"   # same publisher twice


def test_rating_items_are_shared_by_dashboard_and_blind_export():
    items = insight_ratings.rating_items(A["sections"])
    assert [i["text"] for i in items][:1] == ["Shared item about logs."]
