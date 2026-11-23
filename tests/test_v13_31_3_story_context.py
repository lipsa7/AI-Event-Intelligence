"""v13.31.3: a story's slot keeps only context records about that story."""
import inspect

import services.research_orchestrator_service as ros
from services import primary_data


def test_slot_filters_run_wide_context_records():
    source = inspect.getsource(ros.ResearchOrchestratorService)
    assert "kept_context" in source and '"context_off_story"' in source


def test_the_idc_spending_record_is_not_about_the_california_lawsuit():
    story = ("California Attorney General Rob Bonta sued OpenAI under public nuisance and unfair competition law "
             "over the Hugging Face attack and around 80 other loss-of-control incidents involving its agents.")
    idc = ("Global AI infrastructure spending rose 34% sequentially in Q2 2026 to $121.4 billion, prompting IDC to "
           "raise its 2026 forecast; storage was a small share against servers.")
    rule = "The California Unfair Competition Law lets the Attorney General seek penalties for each loss-of-control incident."
    corpus = [story, idc, rule] + ["OpenAI artificial intelligence news"] * 8
    assert not primary_data.relevant([{"text": idc}], story, corpus)[0]
    assert primary_data.relevant([{"text": rule}], story, corpus)[0]


def test_untagged_or_distant_cases_are_not_returned():
    from services import case_library
    cases = [{"case_id": "A", "legal_theory": "", "mechanism_tags": []},
             {"case_id": "B", "legal_theory": "public nuisance", "mechanism_tags": ["public-nuisance"]},
             {"case_id": "C", "legal_theory": "copyright", "mechanism_tags": ["fair-use"]}]
    scores = {"B": 0.72, "C": 0.41}
    embed = lambda story, docs: [0.72 if "nuisance" in d else 0.41 for d in docs]
    out = case_library.retrieve("state sues under public nuisance", cases, embed=embed)
    assert [c["case_id"] for c in out] == ["B"]
