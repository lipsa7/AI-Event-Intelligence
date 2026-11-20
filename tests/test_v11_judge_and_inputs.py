"""v11 (15 Nov 2026): free open-model judge aligned to the owner's ratings, tournament ranking,
automatic prompt tuning, and better research inputs (primary documents, questions first)."""
import csv
import json

import pytest

from services import hypothesis_evidence_service as hes
from services import insight_judge as judge
from services.research_orchestrator_service import ResearchOrchestratorService

GOOD = "Governments learned of the breach three months late, in September."
BAD = "Liability shifts entirely to deployers because accords are voluntary."


def concrete_judge(prompt: str) -> str:
    """Stub model: passes / prefers the text that mentions a month."""
    if "Which insight is better?" in prompt:
        a = prompt.split("A:\n", 1)[1].split("\n\nB:\n", 1)[0]
        b = prompt.split("\n\nB:\n", 1)[1]
        better = "A" if "September" in a and "September" not in b else "B" if "September" in b and "September" not in a else "tie"
        return json.dumps({"better": better, "reason": "x"})
    insight = prompt.split("INSIGHT:\n", 1)[1]
    return json.dumps({"pass": "September" in insight or "October" in insight, "reason": "x"})


def test_judge_is_never_gemini_and_off_without_a_free_model(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_JUDGE_MODEL", raising=False)
    monkeypatch.setenv("INSIGHT_JUDGE_PROVIDER", "auto")
    assert not judge.available()
    with pytest.raises(judge.JudgeUnavailable):
        judge.default_asker()
    monkeypatch.setenv("GROQ_API_KEY", "k")
    assert judge.provider() == "groq" and judge.model_name() == "openai/gpt-oss-120b"
    monkeypatch.setenv("INSIGHT_JUDGE_MODEL", "gemini-3.5-flash")
    with pytest.raises(judge.JudgeUnavailable):
        judge.default_asker()


def test_compare_needs_both_orders_to_agree():
    assert judge.compare(GOOD, BAD, concrete_judge, "std") == "a"
    assert judge.compare(BAD, GOOD, concrete_judge, "std") == "b"
    always_first = lambda prompt: json.dumps({"better": "A"})        # position bias only
    assert judge.compare(GOOD, BAD, always_first, "std") == "tie"


def test_tournament_ranks_concrete_insights_first_and_keeps_the_tail():
    items = [{"insight": BAD}, {"insight": GOOD}, {"insight": "Another vague claim."}]
    ranked, scores = judge.tournament(items, lambda x: x["insight"], concrete_judge, "std", max_items=2)
    assert ranked[0]["insight"] == GOOD and ranked[-1]["insight"] == "Another vague claim."
    assert scores == [1.0, 0.0]


def _ratings(tmp_path, rows):
    path = tmp_path / "ratings.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["no", "insight", "your_rating", "your_note"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_ratings_split_and_agreement_with_kappa(tmp_path):
    rows = ([{"no": str(i), "insight": f"Officials acted in October, case {i}.", "your_rating": "pass",
              "your_note": ""} for i in range(12)]
            + [{"no": str(100 + i), "insight": f"A vague theory {i}.", "your_rating": "fail", "your_note": ""}
               for i in range(12)]
            + [{"no": "999", "insight": "unrated", "your_rating": "", "your_note": ""}])
    rated = judge.load_ratings(_ratings(tmp_path, rows))
    assert len(rated) == 24
    tuning, held_out = judge.split(rated)
    assert len(tuning) == len(held_out) == 12 and not {r["id"] for r in tuning} & {r["id"] for r in held_out}
    result = judge.agreement(held_out, concrete_judge, "std")
    assert result["accuracy"] == 1.0 and result["kappa"] == 1.0
    contrary = lambda prompt: json.dumps({"pass": "October" not in prompt.split("INSIGHT:\n", 1)[1]})
    assert judge.agreement(held_out, contrary, "std")["kappa"] < 0


def test_alignment_requires_enough_rows_good_agreement_and_the_same_model(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.delenv("INSIGHT_JUDGE_MODEL", raising=False)
    path = tmp_path / "state.json"
    state = judge.save_state("std", {"n": 12, "accuracy": 0.9, "balanced_accuracy": 0.85, "kappa": 0.8,
                                     "rated_pass": 4, "rated_fail": 8}, path=path)
    assert state["aligned"]
    assert not judge.is_aligned({**state, "held_out_n": 6})
    assert not judge.is_aligned({**state, "held_out_pass": 1})          # v12: both kinds needed
    assert not judge.is_aligned({**state, "held_out_balanced_accuracy": 0.6})
    assert not judge.is_aligned({**state, "held_out_kappa": 0.4})
    monkeypatch.setenv("INSIGHT_JUDGE_MODEL", "llama-3.3-70b-versatile")
    assert not judge.is_aligned(state)


def test_tuning_keeps_a_rewrite_only_when_it_helps(monkeypatch, tmp_path):
    monkeypatch.setattr(judge, "STATE_PATH", tmp_path / "state.json")
    rows = ([{"id": str(i), "text": f"Officials acted in October, case {i}.", "label": True, "note": ""}
             for i in range(10)]
            + [{"id": str(100 + i), "text": f"Vague theory {i}.", "label": False, "note": "no fact"}
               for i in range(10)])

    def model(prompt):
        if prompt.startswith("You maintain the instructions"):
            return json.dumps({"instructions": "STRICT: pass only with a month named. " + "x" * 250})
        if prompt.startswith("STRICT"):
            return concrete_judge(prompt)
        return json.dumps({"pass": True, "reason": "lenient"})      # the default instructions pass everything

    result = judge.tune(rows, model, rounds=2, proposals=1)
    assert result["kept_new_instructions"] is True
    assert result["held_out_after"]["accuracy"] == 1.0 > result["held_out_before"]["accuracy"]
    assert judge.current_instructions().startswith("STRICT")


def test_runs_rank_with_the_judge_only_when_aligned(monkeypatch):
    service = ResearchOrchestratorService.__new__(ResearchOrchestratorService)
    items = [{"insight": BAD}, {"insight": GOOD}]
    monkeypatch.setattr(judge, "available", lambda: True)
    monkeypatch.setattr(judge, "is_aligned", lambda state=None: False)
    stats = {}
    assert service._judge_rank(items, lambda x: x["insight"], "released", stats) == items
    assert stats["judge_released"] == "not_used"
    monkeypatch.setattr(judge, "is_aligned", lambda state=None: True)
    monkeypatch.setattr(judge, "default_asker", lambda: concrete_judge)
    monkeypatch.setattr(judge, "current_instructions", lambda: "std")
    ranked = service._judge_rank(items, lambda x: x["insight"], "released", stats)
    assert ranked[0]["insight"] == GOOD and stats["judge_released"] == "ranked"
    assert ranked[0]["metadata"]["judge_tournament_score"] == 1.0
    monkeypatch.setattr(judge, "default_asker", lambda: (_ for _ in ()).throw(judge.JudgeUnavailable("x")))
    assert service._judge_rank(items, lambda x: x["insight"], "released", stats) == items   # never fatal


def test_research_reads_the_primary_document_first():
    assert hes.HypothesisEvidenceService.GAP_TYPES[0] == "primary"
    assert hes.HypothesisEvidenceService.GAP_MODE["primary"] == "premise_support"
    import inspect
    source = inspect.getsource(hes.HypothesisEvidenceService.investigate_gaps)
    assert "ORIGINAL DOCUMENT this development is about" in source
    assert "FIRST write" in source and '"questions"' in source


def test_short_on_time_runs_a_small_research_pass_instead_of_skipping(monkeypatch):
    service = ResearchOrchestratorService.__new__(ResearchOrchestratorService)
    service._remaining_discretionary_seconds = lambda: service.ANALYSIS_RESERVE_SECONDS + 10
    service._developments_to_watch = lambda events, output, state: [{"title": "A"}, {"title": "B"}]
    service._tag_development_rank = lambda *a, **k: None
    service._pattern_grid = lambda *a, **k: None
    service._stage_ok = lambda *a, **k: None
    calls = []

    def research(developments, state, only_gaps=None, max_sources=None):
        calls.append((len(developments), only_gaps, max_sources))
        return []
    service._precedent_research = research
    state = {}
    service._pre_analysis_gap_research(events=[], evidence=[], state=state)
    assert calls == [(1, ["primary", "precedent"], 4)]
    assert {"type": "gap_research_light_time_budget"} in state["warnings"]
    service._remaining_discretionary_seconds = lambda: 10.0           # far too little: deferred as before
    calls.clear()
    state = {}
    service._pre_analysis_gap_research(events=[], evidence=[], state=state)
    assert calls == [] and {"type": "gap_research_deferred_time_budget"} in state["warnings"]
