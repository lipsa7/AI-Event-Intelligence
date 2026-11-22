"""v13.22: the editor's bar - original insights are judged against the user's own approved and
rejected examples before release. No model calls (fake judge)."""
import json

from services import par_check

ROWS = [
    {"no": "68", "my_rating": "good", "insight": "Model providers, under 3% revenue penalties, will shift to defensive reporting."},
    {"no": "74", "my_rating": "good", "insight": "Australia's 30-day breach rule vs a 3-month disclosure."},
    {"no": "43", "my_rating": "good", "insight": "Telemetry blind spot conflicts with 12-to-72-hour reporting rules."},
    {"no": "86", "my_rating": "good", "insight": "Voluntary accords shift liability to deployers."},
    {"no": "1", "my_rating": "explanatory/speculative", "insight": "Developers lobby for deceleration."},
    {"no": "2", "my_rating": "generic restatement", "insight": "Nvidia opposes deceleration warnings."},
    {"no": "3", "my_rating": "generic restatement", "insight": "OpenAI signed an accord."},
    {"no": "5", "my_rating": "explanatory/speculative", "insight": "Structural asymmetries are emerging."},
    {"no": "9", "my_rating": "good", "insight": "Held-out good."},
    {"no": "11", "my_rating": "generic restatement", "insight": "Held-out generic."},
]


def test_examples_and_held_out_never_overlap():
    examples, held = par_check.split(ROWS)
    assert {r["no"] for r in examples} == {"68", "74", "43", "86", "1", "5", "2", "3"}
    assert {r["no"] for r in held} == {"9", "11"}


def test_prompt_shows_approved_and_rejected_examples_and_the_rubric():
    examples, _ = par_check.split(ROWS)
    text = par_check.prompt("CANDIDATE TEXT", examples, sources_summary="already said: X")
    assert "APPROVED: Model providers, under 3% revenue penalties" in text
    assert "REJECTED (generic restatement): Nvidia opposes" in text
    assert "SPECIFIC MECHANISM" in text and "CHECKABLE SIGNAL" in text and "already said: X" in text
    assert text.index("CANDIDATE TEXT") > text.index("APPROVED")


def test_judge_pass_fail_and_unanswerable():
    examples, _ = par_check.split(ROWS)
    ok = par_check.judge("x", lambda p: {"verdict": "pass", "missing": "none"}, examples=examples)
    assert ok["passed"] and ok["available"]
    bad = par_check.judge("x", lambda p: json.dumps({"verdict": "fail", "missing": "no_checkable_signal",
                                                     "fix": "Add a dated forecast."}), examples=examples)
    assert not bad["passed"] and bad["missing"] == "no_checkable_signal"
    assert "no checkable signal" in par_check.brief(bad) and "Add a dated forecast." in par_check.brief(bad)
    # A quality bar does not fail open: an unjudged insight is held back.
    for answer in ("garbage", {"verdict": "maybe"}):
        held = par_check.judge("x", lambda p, a=answer: a, examples=examples)
        assert not held["passed"] and not held["available"]
    broken = par_check.judge("x", lambda p: (_ for _ in ()).throw(RuntimeError("down")), examples=examples)
    assert not broken["passed"] and broken["reason"].startswith("par_check_unavailable")


def test_no_labels_file_means_no_bar(tmp_path, monkeypatch):
    monkeypatch.setenv(par_check.LABELS_ENV, str(tmp_path / "missing.csv"))
    result = par_check.judge("x", lambda p: {"verdict": "fail"})
    assert result["passed"] and result["reason"] == "no_labels"


def test_orchestrator_par_check_off_switch_and_on(monkeypatch):
    from services.research_orchestrator_service import ResearchOrchestratorService as S
    service = S.__new__(S)
    assert service._par_check({"text": "x"}, [])["reason"] == "off"      # conftest turns it off
    monkeypatch.setenv("EVENT_INTELLIGENCE_PAR_CHECK", "1")
    monkeypatch.setattr(par_check, "load_labels", lambda path=None: ROWS)
    from services import outside_judge
    # v13.25c: no outside model -> not judged at all (never Gemini judging Gemini)
    service._ask_briefing_model = lambda p: (_ for _ in ()).throw(AssertionError("Gemini must not judge"))
    assert service._par_check({"text": "x"}, [])["reason"] == "not_judged:no_outside_model"
    seen = {}

    def ask(prompt):
        seen["prompt"] = prompt
        return json.dumps({"verdict": "fail", "missing": "no_specific_mechanism", "fix": "Name the rule."})
    monkeypatch.setattr(outside_judge, "available", lambda get=None: True)
    monkeypatch.setattr(outside_judge, "ask", ask)
    result = service._par_check({"text": "California sued OpenAI."},
                                [{"gap_type": "commentary", "text": "Analysts say testing is now a nuisance."}])
    assert not result["passed"] and result["missing"] == "no_specific_mechanism"
    assert "California sued OpenAI." in seen["prompt"] and "Analysts say testing" in seen["prompt"]


def test_real_labels_file_has_the_example_rows():
    rows = par_check.load_labels()
    if not rows:
        return    # labels not in this folder
    examples, held = par_check.split(rows)
    assert len([r for r in examples if par_check.is_pass_label(r["my_rating"])]) == 4
    assert len(held) == len(rows) - len(examples)
