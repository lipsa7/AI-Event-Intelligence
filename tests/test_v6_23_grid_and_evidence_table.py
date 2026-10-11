"""v6.23: what established intelligence products do, built in.

1. Pattern grid (AlphaSense "Generative Grid"; event/entity extraction in
   Recorded Future / GDELT): ask the same questions of every report of a
   development, validate each cell is copied from its report, then COMPUTE
   patterns across rows - a repeated actor across targets, a target hit by
   several actors, a reporting delay across cases, one rule named across
   actors. The last run missed that five agencies were hit; this is the
   mechanism that counts it.
2. Claims-and-evidence table (Consensus shows each claim with the papers that
   support or dispute it; AlphaSense cites the exact snippet): every released
   insight shows each claim, whether a source states it, it follows from
   sources, or it is an assumption, with publisher, date, quote and link, and
   the records pointing the other way.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import services.research_orchestrator_service as ros
from services import claim_support_gate as gate
from services import editorial_briefing as brief
from services import non_obviousness as nob
from services import pattern_grid as grid


@pytest.fixture(autouse=True)
def _private_ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_WATCH_ITEMS", str(tmp_path / "watch_items.sqlite3"))
    monkeypatch.setenv("EVENT_INTELLIGENCE_EVENT_MEMORY", str(tmp_path / "event_memory.sqlite3"))


# Shaped on the run's reports (RUN-7CD0B58AA15D4B5E83571188 and the coverage it missed).
RECORDS = [
    {"evidence_id": "E1", "source_id": "S1", "event_id": "EV1", "published_at": "2026-09-24",
     "text": "An OpenAI agent accessed Services Australia systems in June, and OpenAI notified the government on September 10."},
    {"evidence_id": "E2", "source_id": "S2", "event_id": "EV2", "published_at": "2026-10-05",
     "text": "An OpenAI agent accessed NSW DCCEEW systems in June; DCCEEW was notified in October."},
    {"evidence_id": "E3", "source_id": "S3", "event_id": "EV3", "published_at": "2026-10-02",
     "text": "An OpenAI agent also accessed the Australian Institute of Health and Welfare (AIHW) in July."},
    {"evidence_id": "E4", "source_id": "S4", "event_id": "EV4", "published_at": "2026-09-30",
     "text": "Operators must report cyber incidents under the SOCI Act, Home Affairs said of the Services Australia breach."},
]
ROWS = {"rows": [
    {"evidence_id": "E1", "actor": "OpenAI agent", "action": "accessed", "target": "Services Australia",
     "event_when": "June", "reported_when": "September 10", "event_date": "2026-06", "reported_date": "2026-09-10"},
    {"evidence_id": "E2", "actor": "OpenAI agent", "action": "accessed", "target": "NSW DCCEEW",
     "event_when": "June", "reported_when": "October", "event_date": "2026-06", "reported_date": "2026-10"},
    {"evidence_id": "E3", "actor": "OpenAI agent", "action": "also accessed",
     "target": "Australian Institute of Health and Welfare", "event_when": "July", "event_date": "2026-07"},
    {"evidence_id": "E4", "actor": "Operators", "action": "must report", "rule": "SOCI Act",
     "target": "Services Australia"},
]}


# 1 grid ---------------------------------------------------------------------

def test_cells_must_be_copied_from_their_report():
    raw = json.loads(json.dumps(ROWS))
    raw["rows"][0]["target"] = "the Australian tax office"          # not in E1's text
    raw["rows"][1]["reported_date"] = "2026-11"                      # disagrees with "October"
    raw["rows"].append({"evidence_id": "E-UNKNOWN", "actor": "x"})   # not a supplied record
    rows, dropped = grid.parse_rows(raw, RECORDS)
    assert [r["evidence_id"] for r in rows] == ["E1", "E2", "E3", "E4"]
    assert "target" not in rows[0] and "reported_date" not in rows[1] and dropped == 2


def test_dates_must_agree_with_the_copied_text():
    assert grid._date_agrees("2026-09-10", "September 10")
    assert not grid._date_agrees("2026-09-11", "September 10")
    assert not grid._date_agrees("2026-06", "earlier this month")
    assert not grid._date_agrees("2025-06", "June 2026")


def test_patterns_are_computed_with_their_rows():
    rows, _ = grid.parse_rows(ROWS, RECORDS)
    patterns = {p["kind"]: p for p in grid.find_patterns(rows)}
    repeat = patterns["repeat_actor"]
    assert repeat["n"] == 3 and repeat["evidence_ids"] == ["E1", "E2", "E3"]
    assert "3 different named parties" in repeat["text"] and "NSW DCCEEW" in repeat["text"]  # v6.24 wording
    assert patterns["shared_target"]["n"] == 2                       # Services Australia: agent + operators
    assert patterns["reporting_lag"]["n"] == 2 and "about 3 months" in patterns["reporting_lag"]["text"]


def test_pattern_records_are_citeable_but_not_observations():
    rows, _ = grid.parse_rows(ROWS, RECORDS)
    records = grid.pattern_records(grid.find_patterns(rows), "OpenAI agent breaches")
    record = records[0]
    assert record["gap_type"] == "pattern" and record["event_id"] == "" and record["derived_from_evidence_ids"]
    assert record["text"].startswith("Computed across this run's reports:")
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    events_map, _ = obj._convergence_maps(records, [])
    assert record["evidence_id"] not in events_map                   # never an independent observation
    assert nob.is_constraint(record)                                  # can anchor the constraint route
    facts = brief.select_context_facts(records)
    assert facts and facts[0]["development"].startswith("Pattern across reports")


def test_grid_runs_per_development_and_feeds_the_context(caplog):
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    obj._remaining_discretionary_seconds = lambda: float("inf")
    prompts = []
    obj._ask_briefing_model = lambda prompt: prompts.append(prompt) or json.dumps(ROWS)
    state = {}
    development = {"title": "OpenAI agent breaches", "event_ids": ["EV1", "EV2", "EV3", "EV4"]}
    with caplog.at_level("INFO"):
        found = obj._pattern_grid([development], RECORDS, state)
    assert len(prompts) == 1 and found
    assert {r["evidence_id"] for r in state["gap_context_records"]} == {r["evidence_id"] for r in found}
    assert any(m.getMessage().startswith("pattern_grid=") for m in caplog.records)


def test_grid_failure_is_never_fatal():
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    obj._remaining_discretionary_seconds = lambda: float("inf")

    def broken(prompt):
        raise TimeoutError("slow")

    obj._ask_briefing_model = broken
    state = {}
    assert obj._pattern_grid([{"title": "t", "event_ids": ["EV1", "EV2"]}], RECORDS, state) == []
    assert state["pattern_grid"][0]["status"] == "failed:TimeoutError"


def test_grid_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_PATTERN_GRID", "0")
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    assert obj._pattern_grid([{"title": "t", "event_ids": ["EV1"]}], RECORDS, {}) == []


# 2 evidence table -----------------------------------------------------------

def _released_grounding():
    return {"review_status": "complete", "claims": [
        {"claim_id": "P1", "epistemic_type": "fact", "grounding_status": "supported",
         "claim_text": "OpenAI notified Australia on September 10.", "supporting_evidence_ids": ["E1"]},
        {"claim_id": "I1", "epistemic_type": "hidden_insight", "grounding_status": "inference_supported",
         "claim_text": "Operators carry a reporting duty they cannot meet on time.",
         "supporting_evidence_ids": ["E1", "E4", "EVID-PATTERN-X"]},
        {"claim_id": "S1", "epistemic_type": "second_order_effect", "grounding_status": "unsupported",
         "claim_text": "Pruned sentence.", "supporting_evidence_ids": []},
    ]}


def test_evidence_table_rows_sources_and_against():
    pattern = {"evidence_id": "EVID-PATTERN-X", "source_id": "COMPUTED-PATTERN", "gap_type": "pattern",
               "text": "Computed across this run's reports: ..."}
    counter = {"evidence_id": "E9", "source_id": "S9", "source_url": "https://www.example.org/a",
               "text": "OpenAI says it notified affected agencies within days of confirming each incident."}
    refs = {"S1": {"publisher": "The Guardian", "url": "https://theguardian.com/x", "published_at": "2026-09-24"}}
    table = gate.evidence_table(_released_grounding(), RECORDS + [pattern, counter],
                                assumptions=[{"text": "Detection takes months for every vendor."}],
                                counter_ids=["E9"], reference=lambda sid: refs.get(sid, {}))
    assert [r["role"] for r in table["rows"]] == ["Fact", "Reasoning step", "Conclusion"]
    assert table["rows"][0]["status"] == "Stated by the sources"
    assert table["rows"][0]["sources"][0]["publisher"] == "The Guardian"
    conclusion = table["rows"][2]
    assert conclusion["status"] == "Follows from the sources"
    assert [s["kind"] for s in conclusion["sources"]] == ["news", "news", "computed"]
    assert table["rows"][1]["status"] == "Assumption (no source states it)"
    assert table["against"][0]["publisher"] == "example.org"
    assert table["summary"] == {"records_for": 3, "publishers": 2, "records_against": 1, "stated": 1,
                                "inferred": 1, "assumptions": 1}


def test_released_insight_carries_the_table(monkeypatch):
    from tests.test_claim_support_gate import DEEP_PROFILE, PASSING, candidate, make_orchestrator
    from tests.test_v6_21_4_search_coverage import _events_and_evidence
    monkeypatch.setattr(ros, "DEEP_CLAIM_GATE_EXTRA_SYNTHESES", 0)
    obj = make_orchestrator(verdicts={"INSIGHTREV-T": PASSING})
    obj._source_reference = lambda sid: {"publisher": "Pub " + sid}
    events, evidence = _events_and_evidence()
    released, _, _ = obj._claim_supported_release(
        topic="ai", candidates=[candidate("T")], grounded={"INSIGHTREV-T": PASSING()}, events=events,
        evidence=evidence, graph_context={}, constraints={}, blueprint={}, profile=DEEP_PROFILE,
        state={"warnings": [], "run_id": "RUN-T"})
    table = released[0]["metadata"]["evidence_table"]
    assert table["rows"] and table["summary"]["records_for"] >= 1


def test_report_and_dashboard_show_the_table():
    import inspect
    source = inspect.getsource(ros.ResearchOrchestratorService)
    assert '"evidence_table": _json_safe(insight_metadata.get("evidence_table", {}))' in source
    dash = (pathlib.Path(__file__).resolve().parents[1] / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    assert "**Claims and the evidence behind each**" in dash and "render_evidence_table(item)" in dash
    assert "**Evidence pointing the other way**" in dash
    import agents.strategic_inference_agent as agent
    assert "PATTERNS ACROSS REPORTS" in inspect.getsource(agent)


def test_dashboard_table_markdown_is_well_formed():
    import ast
    dash = (pathlib.Path(__file__).resolve().parents[1] / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    tree = ast.parse(dash)
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert {"evidence_table_markdown", "render_evidence_table"} <= names
