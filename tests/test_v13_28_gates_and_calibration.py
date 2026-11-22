"""v13.28 (research report of 22 Nov, Methods 0 and 8): per-gate funnel, calibrated editor's bar."""
import json

from services import gate_funnel, par_calibration, par_check, proof_line

ROWS = [{"no": str(i), "my_rating": "good" if i % 3 == 0 else "generic restatement",
         "insight": ("OpenAI must file by the court deadline, a forced move" if i % 3 == 0
                     else "AI companies face growing scrutiny from regulators") + f" variant {i}"} for i in range(30)]


def test_nearest_examples_are_similar_and_never_the_candidate_itself():
    target = ROWS[3]["insight"]
    picked = par_check.nearest_examples(target, ROWS)
    assert target not in [r["insight"] for r in picked]
    assert sum(par_check.is_pass_label(r["my_rating"]) for r in picked) == 3 and len(picked) == 6
    assert "court deadline" in picked[0]["insight"]


def test_judge_returns_the_six_item_scores():
    reply = {"scores": {i: 7 for i in par_check.ITEMS}, "verdict": "pass", "missing": "none"}
    out = par_check.judge("x", ask=lambda _p: json.dumps(reply), examples=ROWS[:4])
    assert out["scores"]["earned"] == 7.0 and out["passed"]
    partial = par_check.judge("x", ask=lambda _p: json.dumps({"verdict": "pass", "scores": {"earned": 3}}),
                              examples=ROWS[:4])
    assert partial["scores"] == {}


def _data():
    x, y = [], []
    for i in range(80):
        good = i % 3 == 0
        base = 0.8 if good else 0.3
        x.append([base + (i % 5) * 0.02] * 6 + [1.0 if good or i % 7 == 0 else 0.0])
        y.append(1 if good else 0)
    return x, y


def test_calibration_fits_cross_validates_and_is_trusted_only_for_its_model():
    x, y = _data()
    cal = par_calibration.build(x, y, model="qwen3:14b", items=par_check.ITEMS, exemplar_mode="nearest")
    assert cal["cv_precision"] >= 0.7 and cal["threshold"] is not None
    assert par_calibration.trusted(cal, "qwen3:14b") and not par_calibration.trusted(cal, "llama3.1:8b")
    high = par_calibration.probability(cal, {i: 9 for i in par_check.ITEMS}, True)
    low = par_calibration.probability(cal, {i: 2 for i in par_check.ITEMS}, False)
    assert high > cal["threshold"] > low


def test_noise_is_not_trusted():
    x = [[0.5] * 6 + [float(i % 2)] for i in range(80)]
    y = [1 if i % 4 == 0 else 0 for i in range(80)]
    cal = par_calibration.build(x, y, model="m", items=par_check.ITEMS, exemplar_mode="nearest")
    assert not par_calibration.trusted(cal, "m")


def test_rate_next_picks_the_least_sure():
    rows = [{"text": "a", "probability": 0.95}, {"text": "b", "probability": 0.52}, {"text": "c", "probability": 0.1}]
    assert [r["text"] for r in par_calibration.rate_next(rows, limit=1)] == ["b"]


def test_trusted_calibration_decides_by_probability(monkeypatch, tmp_path):
    from services import outside_judge
    from services.research_orchestrator_service import ResearchOrchestratorService as S
    x, y = _data()
    cal = par_calibration.build(x, y, model="qwen3:14b", items=par_check.ITEMS, exemplar_mode="nearest")
    path = tmp_path / "cal.json"
    par_calibration.save(cal, path)
    monkeypatch.setenv(par_calibration.PATH_ENV, str(path))
    monkeypatch.setenv("EVENT_INTELLIGENCE_PAR_CHECK", "1")
    monkeypatch.setattr(par_check, "load_labels", lambda path=None: ROWS)
    monkeypatch.setattr(outside_judge, "available", lambda get=None: True)
    monkeypatch.setattr(outside_judge, "model", lambda get=None: "qwen3:14b")
    answers = {"score": 2}

    def ask(prompt):
        if "Which insight would" in prompt:
            return json.dumps({"better": "A"})
        return json.dumps({"scores": {i: answers["score"] for i in par_check.ITEMS}, "verdict": "pass", "missing": "none"})
    monkeypatch.setattr(outside_judge, "ask", ask)
    service = S.__new__(S)
    low = service._par_check({"text": "California sued OpenAI."}, [])
    assert low["trusted"] and not low["passed"] and low["calibrated_probability"] < cal["threshold"]
    answers["score"] = 9
    assert service._par_check({"text": "California sued OpenAI."}, [])["passed"]
    assert len(service._par_scored) == 2


def test_gate_funnel_counts_each_gate_and_lists_rejects():
    log = {"exploration": {"combinations": [
        {"result": "candidate", "variant_kept": 1, "variant_dropped": [{"insight": "d", "problems": ["no_number"]}],
         "lens_calls": [{"lens": "constraints", "result": "candidate"}, {"lens": "value_chain", "result": "failed:TypeError"}]},
        {"result": "no_candidate"}]},
        "attempts": [{"passed": False, "insight": "i1", "reasons": ["premise_not_supported_by_cited_evidence:C1"]},
                     {"passed": True, "insight": "i2"}]}
    precedents = [{"case_count": 0, "development": "dev", "queries": [{"gap": "precedent", "query": "q"}],
                   "limitations": ["precedent: direct_precedent: newsapi: OUT_OF_COVERAGE"]}]
    funnel = gate_funnel.compute(log, precedents)
    assert funnel["stories_explored"] == {"in": 2, "out": 1, "rejected": {"no_candidate": 1}, "pass_rate": 0.5}
    assert funnel["lens_generation"]["out"] == 1 and funnel["variant_checks"]["rejected"] == {"no_number": 1}
    assert funnel["claim_and_editor_checks"]["rejected"] == {"premise_not_supported_by_cited_evidence": 1}
    assert funnel["precedent_search"]["rejected"] == {"newsapi: OUT_OF_COVERAGE": 1}
    rows = gate_funnel.reject_rows(log, precedents, "R1")
    assert [r["stage"] for r in rows] == ["variant_checks", "claim_and_editor_checks", "precedent_search"]
    assert gate_funnel.total([funnel, funnel])["stories_explored"]["in"] == 4


def test_proof_line_shows_an_advisory_verdict():
    item = {"text": "x", "par_check": {"available": True, "trusted": False, "advisory_verdict": "fail",
                                       "missing": "no_specific_mechanism"}}
    assert "not yet checked against your ratings): fail - missing no specific mechanism" in proof_line.line(item)
