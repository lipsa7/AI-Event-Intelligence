"""v6.36.7 - GPT review of v6.36.6:
1. The verdict states DIRECTION (profile rated better or worse), never only an absolute gap.
2. Two baseline runs are one observed variation, not a noise floor: shown for reference, with no
   conclusion about chance.
3. Corroboration is counted PER stated fact (publishers backing that same fact), not across facts.
4. --blind-import compares exactly the runs that were exported (saved in the key file), even if a
   newer run finished in between.
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
from contextlib import redirect_stdout

from services import editorial_briefing as brief
from services import insight_ratings
from tests.test_v6_36_6_blind_experiment import A, A2, B, _db, _report, compare


def _rate(report, verdicts):
    texts = [i["text"] for i in report["sections"]["insight_evaluation"]["items"]]
    insight_ratings.save([{"report_id": report["report_id"], "item_key": f"r{n}", "text": t, "rating": v,
                           "blind": True} for n, (t, v) in enumerate(zip(texts, verdicts))])


def test_a_worse_profile_is_reported_as_worse(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_RATINGS", str(tmp_path / "r.jsonl"))
    _rate(A, ["useful", "useful", "useful"])
    _rate(B, ["useful", "poor", "poor"])
    _rate(A2, ["useful", "useful", "poor"])
    lines = "\n".join(compare.verdict(insight_ratings.load(), A, B, A2))
    assert "rated WORSE by -0.67" in lines and "BETTER" not in lines
    assert "is larger than it" in lines and "preliminary" in lines
    assert "noise floor" not in lines.casefold() and "exceeds chance" not in lines


def test_without_a_second_baseline_the_result_is_preliminary(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_RATINGS", str(tmp_path / "r.jsonl"))
    _rate(A, ["useful", "poor", "poor"])
    _rate(B, ["useful", "useful", "poor"])
    lines = "\n".join(compare.verdict(insight_ratings.load(), A, B, None))
    assert "rated BETTER by +0.33" in lines and "preliminary" in lines


def test_per_fact_corroboration_is_recorded_by_the_briefing():
    facts = [{"fact_id": "F1", "development": "d", "evidence_id": "E1", "source_id": "S1", "date": "",
              "text": "Only OpenAI holds the activity logs of the agents."},
             {"fact_id": "F2", "development": "d", "evidence_id": "E2", "source_id": "S2", "date": "",
              "text": "The tax office paused the agent."}]
    draft = brief.parse_draft({"implications": [{
        "basis": "leverage", "text": "OpenAI's logs give it leverage over the tax office.", "fact_ids": ["F1"],
        "conditions": "c", "holder": "OpenAI", "counterparty": "Australian Taxation Office",
        "leverage_source": "the agent activity logs", "mechanism": "the office needs the logs to audit",
        "counterparty_alternatives": "none named in the facts", "holder_dependence": "one customer of many"}]},
        facts, [])
    claims = brief.grounding_claims(draft, facts, [])
    statuses = {c["claim_id"]: c["_bar"][-1] for c in claims}
    supporting = {c["claim_id"]: (["E1", "E2"] if c["_role"] == "fact" else ["E1"]) for c in claims}
    kept = brief.sanitize(draft, claims, statuses, facts, [], supporting=supporting)["implications"][0]
    rows = kept["fact_corroboration"]
    assert rows and all(r["source_ids"] == ["S1", "S2"] for r in rows)
    assert all(r["claim"] for r in rows)


def test_blind_import_compares_the_exported_runs(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setenv("EVENT_INTELLIGENCE_RATINGS", str(tmp_path / "r.jsonl"))
    monkeypatch.setattr(compare, "BLIND_CSV", tmp_path / "blind.csv")
    monkeypatch.setattr(compare, "BLIND_KEY", tmp_path / "key.json")
    monkeypatch.setattr(sys, "argv", ["compare_runs.py", "--db", str(db), "--blind-export"])
    with redirect_stdout(io.StringIO()):
        assert compare.main() == 0
    roles = json.loads((tmp_path / "key.json").read_text(encoding="utf-8"))["roles"]
    assert roles == {"baseline": "REP-A2", "profile": "REP-B", "second_baseline": "REP-A"}
    # a newer profile run finishes after the export
    newer = _report("REP-B2", True, ["Late item."], "9")
    with sqlite3.connect(str(db)) as conn:
        conn.execute("INSERT INTO report_revisions VALUES (?,?,?,?,?,?,?,?)",
                     ("REP-B2-1", "REP-B2", 1, json.dumps({"run_id": "RUN-B2", "sections": newer["sections"]}),
                      "f", "", "s", "9"))
    monkeypatch.setattr(sys, "argv", ["compare_runs.py", "--db", str(db), "--blind-import"])
    out = io.StringIO()
    with redirect_stdout(out):
        assert compare.main() == 0
    text = out.getvalue()
    assert '"report_id": "REP-B"' in text and "REP-B2" not in text
