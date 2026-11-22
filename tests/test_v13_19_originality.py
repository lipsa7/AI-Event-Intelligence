"""v13.19 (22 Nov 2026): one story per exploration slot, expected view for the generator,
precedent review by mechanism, batch size 4. All offline (no model calls)."""
import json
import os
from pathlib import Path

import pytest

from services import story_families
from services.research_orchestrator_service import ResearchOrchestratorService as S

CHECKPOINT = Path("data/checkpoints/RUN-4367E5FE6BE84506BBC92CBB.json")


def group(key, label, summary, actors=("openai",)):
    return {"key": key, "label": label, "event_ids": [key + "-E"], "evidence_ids": [key + "-V"],
            "underlying": {key}, "actors": set(actors),
            "events": [{"event_id": key + "-E", "title": label, "summary": summary}]}


GROUPS = [
    group("A", "California sues OpenAI over agents hacking Hugging Face", "California sued OpenAI on Monday."),
    group("B", "Blade Runner producer settles with Warner Bros", "Alcon settled.", actors=("alcon",)),
    group("C", "change, chatgpt-maker, practices", "California's attorney general filed a lawsuit against OpenAI."),
    group("D", "internal, agents, evaluation", "In July OpenAI agents compromised systems and hacked Hugging Face."),
]


def test_families_parsed_and_joined_into_first_member():
    asked = {}

    def ask(prompt):
        asked["prompt"] = prompt
        return json.dumps({"families": [[1, 3, 4]]})
    found = story_families.families(GROUPS, ask)
    assert found == [[0, 2, 3]]
    joined, log = story_families.join(GROUPS, found)
    assert [g["key"] for g in joined] == ["A", "B"]
    assert joined[0]["event_ids"] == ["A-E", "C-E", "D-E"]
    assert log[0]["joined"] == ["change, chatgpt-maker, practices", "internal, agents, evaluation"]
    assert "different stories" in asked["prompt"] and "lawsuit or investigation" in asked["prompt"]


@pytest.mark.parametrize("answer", ["not json", {"families": [[1]]}, {"families": [[9, 10]]}, {}, None])
def test_unusable_answers_leave_developments_alone(answer):
    assert story_families.families(GROUPS, lambda p: answer) == []


def test_overlapping_families_never_chain():
    found = story_families.families(GROUPS, lambda p: {"families": [[1, 3], [3, 4], [2, 4]]})
    assert found == [[0, 2], [1, 3]]


def test_orchestrator_join_is_one_call_per_run_and_fail_open():
    service = S.__new__(S)
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return {"families": [[1, 3, 4]]}
    service._ask_briefing_model = ask
    ranked = [dict(g, potential={"score": 1.0, "parts": []}) for g in GROUPS]
    first = service._join_story_families(ranked, topic="artificial intelligence")
    again = service._join_story_families(ranked, topic="artificial intelligence")
    assert len(calls) == 1 and [g["key"] for g in first] == [g["key"] for g in again] == ["A", "B"]

    broken = S.__new__(S)
    broken._ask_briefing_model = lambda prompt: (_ for _ in ()).throw(RuntimeError("model down"))
    assert [g["key"] for g in broken._join_story_families(ranked, topic="x")] == ["A", "B", "C", "D"]


@pytest.mark.skipif(not CHECKPOINT.exists(), reason="saved checkpoint of RUN-4367 not in this folder")
def test_checkpoint_lawsuit_story_gets_one_slot():
    """RUN-ACC0E1ED gave 3 of 4 slots to the California lawsuit; with the family joined, one slot."""
    data = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    service = S.__new__(S)

    def ask(prompt):
        lines = [line for line in prompt.splitlines() if line[:3].strip().rstrip(".").isdigit()]
        family = [int(line.split(".")[0]) for line in lines
                  if ("California" in line and "OpenAI" in line) or "internal, agents" in line
                  or "Hugging Face during a July" in line]
        return {"families": [family]}
    service._ask_briefing_model = ask
    combos = service._development_combinations(data["event_records"], data["evidence_records"], [], 5,
                                                topic="artificial intelligence")
    lawsuit = [c for c in combos if any("OpenAI" in label and ("Hugging" in label or "practices" in label
                                                               or "internal" in label) for label in c["labels"])]
    assert len(lawsuit) == 1
    assert len(combos) == 5


def test_expected_view_lists_commentary_and_reports():
    service = S.__new__(S)
    evidence = [{"evidence_id": "C1", "gap_type": "commentary", "analytical_context_only": True,
                 "text": "Analysts say the suit treats careless AI testing as a public nuisance."},
                {"evidence_id": "R1", "text": "California sues OpenAI over agents hacking Hugging Face."}]
    block = service._expected_view_block(evidence, evidence)
    assert "EXPECTED VIEW" in block
    assert "[published analysis] Analysts say the suit treats careless AI testing" in block
    assert "[report] California sues OpenAI" in block
    assert "must NOT be stated by" in block
    assert service._expected_view_block([], []) == ""


def test_precedent_review_prompt_judges_roles_with_examples():
    import inspect
    from services import hypothesis_evidence_service as h
    source = inspect.getsource(h)
    for text in ("HOW TO JUDGE A PAST CASE", "Kia and Hyundai", "-> ACCEPT as historical_case",
                 "antitrust suit against", "-> REJECT", "leave outcome_quote empty"):
        assert text in source
    assert "MECHANISM with roles instead of names" in source and '\\"mechanism\\"' in source


def test_grounding_batch_default_is_four(monkeypatch):
    from services.grounding_service import GroundingService
    monkeypatch.delenv("EVENT_INTELLIGENCE_GROUNDING_BATCH", raising=False)
    service = GroundingService(support_classifier=lambda p: "{}", model_name="x")
    service.classifier_provider = "gemini"
    assert service._batch_size() == 4
