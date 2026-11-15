"""
v6 extraction -> real Strategic Event Admission gate.

Runs v6 frames (stub model, no network) through
ResearchOrchestratorService._strategic_event_assessment and checks that
multi-sentence events reach the gate with their actor and AI link, and
that only AI links verified in context count at the gate.

Run:  python -m tests.test_v6_admission_integration
"""

from __future__ import annotations

import json
import re

from agents.extraction_agent_v6 import ExtractionAgentV6
from services.research_orchestrator_service import ResearchOrchestratorService as Gate
from tests.v6_model_stubs import FramesAndVerifier


BODY = (
    "Zeta Labs is a Bengaluru fintech. The company raised $40 million in Series B "
    "funding for its AI underwriting engine.\n\n"
    "Acme Inc. announced a partnership with\n"
    "Beta Corp to deploy AI agents across its claims unit.\n\n"
    "Infosys signed a deal with a European bank last week. The rollout covers "
    "generative AI agents in 40 branches.\n\n"
    "Sarvam launched a new Indic LLM for banks."
)
ARTICLE = {"source_id": "SOURCE-GATE", "title": "Banks step up AI deployments", "body": BODY}

FRAMES = [
    {"taxonomy": "observed_event", "event_type": "funding round",
     "actor": {"mention": "Zeta Labs", "sentence_id": "S2", "entity_type": "organization"},
     "action_sentence_ids": ["S3"], "ai_link_sentence_ids": ["S3"]},
    {"taxonomy": "observed_event", "event_type": "partnership",
     "actor": {"mention": "Acme Inc.", "sentence_id": "S4", "entity_type": "organization"},
     "action_sentence_ids": ["S4"], "ai_link_sentence_ids": ["S4"]},
    {"taxonomy": "observed_event", "event_type": "contract",
     "actor": {"mention": "Infosys", "sentence_id": "S5", "entity_type": "organization"},
     "action_sentence_ids": ["S5"], "ai_link_sentence_ids": ["S6"]},
    {"taxonomy": "observed_event", "event_type": "product launch",
     "actor": {"mention": "Sarvam", "sentence_id": "S7", "entity_type": "organization"},
     "action_sentence_ids": ["S7"], "ai_link_sentence_ids": ["S7"]},
]


def StubLLM():
    # Canned frames plus table verdicts for the AI-link verification call.
    return FramesAndVerifier(FRAMES)


def assess(event):
    evidence = [{"text": snippet} for snippet in event["snippets"]]
    return Gate._strategic_event_assessment(
        topic="artificial intelligence", event=event, evidence_items=evidence
    )


def test_v6_events_are_admitted_with_correct_actor():
    events = ExtractionAgentV6(llm_service=StubLLM(), entity_tagger=None).extract(ARTICLE)["events"]
    results = {e["roles"]["actor"]["mention"]: assess(e) for e in events}
    assert set(results) == {"Zeta Labs", "Acme Inc.", "Infosys", "Sarvam"}
    for actor, assessment in results.items():
        assert assessment["admit"], (actor, assessment["reasons"])
        assert assessment["actor"] == actor


def test_admission_survives_persistence_that_keeps_only_contract_fields():
    contract_fields = {
        "event_id", "source_id", "title", "event_type", "summary", "claim_status",
        "temporal_status", "event_time_text", "event_start_at", "event_end_at",
        "temporal_precision", "entity_mentions", "quantitative_claims",
        "relationship_mentions", "snippets", "extraction_metadata",
    }
    events = ExtractionAgentV6(llm_service=StubLLM(), entity_tagger=None).extract(ARTICLE)["events"]
    for event in events:
        stripped = {key: value for key, value in event.items() if key in contract_fields}
        assessment = assess(stripped)
        assert assessment["admit"], (event["roles"]["actor"]["mention"], assessment["reasons"])
        assert assessment["actor"] == event["roles"]["actor"]["mention"]


def test_gate_input_contains_actor_and_ai_link_sentences():
    events = ExtractionAgentV6(llm_service=StubLLM(), entity_tagger=None).extract(ARTICLE)["events"]
    infosys = next(e for e in events if e["roles"]["actor"]["mention"] == "Infosys")
    gate_input = Gate._strategic_event_text(infosys, [])
    assert "Infosys signed a deal" in gate_input
    assert "generative AI agents in 40 branches" in gate_input


def test_v5_shaped_event_ignores_ai_link_rule():
    v5_event = {
        "event_id": "EVENT-V5", "source_id": "SOURCE-GATE",
        "title": "Acme signed a deal.", "summary": "Acme Corp signed a deal with a bank.",
        "event_type": "contract", "temporal_status": "current",
        "entity_mentions": [{"mention": "Acme Corp", "entity_type": "organization"}],
        "snippets": ["Acme Corp signed a deal with a bank."], "quantitative_claims": [],
        # No `roles`: an ai_link list alone must not count.
        "ai_link_sentence_ids": ["S1"],
    }
    assert Gate._cited_ai_link_present(v5_event) is False
    v5_event["extraction_metadata"] = {"event_taxonomy": "observed_event"}
    assert Gate._cited_action_present(v5_event) is False
    assert not assess(v5_event)["admit"]


def test_non_action_taxonomy_gets_no_structural_credit():
    events = ExtractionAgentV6(llm_service=StubLLM(), entity_tagger=None).extract(ARTICLE)["events"]
    zeta = next(e for e in events if e["roles"]["actor"]["mention"] == "Zeta Labs")
    zeta["extraction_metadata"]["event_taxonomy"] = "advice"
    assert Gate._cited_action_present(zeta) is False


# ----------------------------------------------------------------------
# A citation, a pronoun or a nearby sentence is not proof of an AI link.
# The gate counts only links verified in context during extraction.
# ----------------------------------------------------------------------

HOTEL_FRAME = {
    "taxonomy": "observed_event", "event_type": "acquisition",
    "actor": {"mention": "Hotel Group", "sentence_id": "S1", "entity_type": "organization"},
    "action_sentence_ids": ["S1"], "ai_link_sentence_ids": ["S2"],
}


def extract_hotel(body, frame=HOTEL_FRAME, **stub_kwargs):
    stub = FramesAndVerifier([frame], filter_by_window=False, **stub_kwargs)
    events = ExtractionAgentV6(llm_service=stub, entity_tagger=None).extract(
        {"source_id": "SOURCE-HOTEL", "title": "", "body": body}
    )["events"]
    assert len(events) == 1
    return events[0]


def test_reviewer_pronoun_case_is_not_admitted_end_to_end():
    event = extract_hotel("Hotel Group acquired a resort. Separately, OpenAI released its new generative AI model.")
    assert Gate._cited_ai_link_present(event) is False
    assessment = assess(event)
    assert not assessment["admit"], assessment
    assert "topic_relevance_below_threshold" in assessment["reasons"]


def test_reviewer_paragraph_case_is_admitted_end_to_end():
    event = extract_hotel("Hotel Group acquired a resort.\n\nThe deal includes an AI concierge for guests.")
    assert Gate._cited_ai_link_present(event) is True
    assessment = assess(event)
    assert assessment["admit"], assessment["reasons"]
    assert assessment["actor"] == "Hotel Group"


BACKGROUND_FRAME = {
    "taxonomy": "observed_event", "event_type": "acquisition",
    "actor": {"mention": "Hotel Group", "sentence_id": "S1", "entity_type": "organization"},
    "action_sentence_ids": ["S2"], "ai_link_sentence_ids": ["S1"],
}
BACKGROUND_BODY = "Hotel Group uses generative AI for internal payroll. The company acquired a beach resort."


def test_reviewer_rejected_ai_link_is_not_overridden_by_background_keywords():
    # Reviewer case 1: the verifier rejects the AI link; v6.5's keyword checks
    # still found "generative AI" in the actor's background sentence and admitted it.
    event = extract_hotel(BACKGROUND_BODY, frame=BACKGROUND_FRAME)
    assert "generative AI" in event["description"]          # the background is still cited (actor)
    assert event["roles"]["ai_link_sentence_ids"] == []
    assessment = assess(event)
    assert not assessment["admit"], assessment
    assert "ai_link_not_verified" in assessment["reasons"]
    assert assessment["topic_linkage_type"] == "none"


def test_verdict_is_final_for_every_ai_field_topic():
    # Not only "AI"/"artificial intelligence": ML, LLM, robotics... topics too.
    background = "Hotel Group uses machine learning, large language models and robotics for payroll."
    event = extract_hotel(f"{background} The company acquired a beach resort.", frame=BACKGROUND_FRAME,
                          verdicts={background: ("not_supported", "")})
    for topic in ("machine learning", "large language models", "robotics", "computer vision",
                  "deep learning", "generative AI in retail"):
        assessment = Gate._strategic_event_assessment(
            topic=topic, event=event, evidence_items=[{"text": t} for t in event["snippets"]])
        assert not assessment["admit"], (topic, assessment["reasons"])
        assert "ai_link_not_verified" in assessment["reasons"], topic


def test_rejected_sentences_do_not_shape_relation_tags():
    background = "Hotel Group signed an OpenAI agreement to automate internal payroll with GPU compute capacity."
    body = f"{background} Hotel Group acquired a beach resort.\n\nThe deal includes an AI concierge for guests."
    frame = dict(BACKGROUND_FRAME, ai_link_sentence_ids=["S1", "S3"])
    event = extract_hotel(body, frame=frame, verdicts={background: ("not_supported", "")})
    assert event["roles"]["ai_link_sentence_ids"] == ["S3"]
    assessment = assess(event)
    assert assessment["admit"]
    signature = assessment["relation_signature"]
    assert signature["mechanism_tags"] == [] and signature["dependency_tags"] == []


def test_same_text_without_v6_structure_keeps_v5_behaviour():
    # v5 events have no verification structure; their admission is unchanged.
    event = extract_hotel(BACKGROUND_BODY, frame=BACKGROUND_FRAME)
    v5_shaped = {key: value for key, value in event.items() if key not in {"roles", "cited_sentences"}}
    v5_shaped["extraction_metadata"] = {"event_taxonomy": "observed_event"}
    assert Gate._verified_ai_relationship_text(v5_shaped) is None
    # Known v5 limitation, kept for comparability: AI keywords anywhere in the
    # text count toward relevance (v6 events: only verified links do).
    assert assess(v5_shaped)["relevance_score"] == 3.0
    assert assess(event)["relevance_score"] == 0.0


def test_verified_link_admits_and_drives_the_ai_signals():
    event = extract_hotel(BACKGROUND_BODY, frame=BACKGROUND_FRAME, verdicts={
        "Hotel Group uses generative AI for internal payroll.": ("supported", "uses generative AI")})
    assert Gate._verified_ai_relationship_text(event) == (
        "Hotel Group uses generative AI for internal payroll. The company acquired a beach resort."
    )
    assert assess(event)["admit"]


def hand_built_event(ai_link_text, verification=None, paragraph="P1"):
    """A v6-shaped event that skipped the extractor (defence in depth)."""
    cited = [
        {"id": "S1", "paragraph_id": "P1", "start": 0, "end": 30, "text": "Hotel Group acquired a resort."},
        {"id": "S2", "paragraph_id": paragraph, "start": 31, "end": 80, "text": ai_link_text},
    ]
    return {
        "event_id": "EVENT-HOTEL", "source_id": "SOURCE-HOTEL",
        "title": cited[0]["text"], "summary": cited[0]["text"], "description": "",
        "event_type": "acquisition", "temporal_status": "past",
        "entity_mentions": [{"mention": "Hotel Group", "entity_type": "organization"}],
        "actor": {"name": "Hotel Group", "entity_type": "organization"},
        "snippets": [cited[0]["text"]], "quantitative_claims": [],
        "cited_sentences": cited,
        "roles": {"actor": {"mention": "Hotel Group", "sentence_id": "S1"},
                  "action_sentence_ids": ["S1"], "object": None,
                  "ai_link_sentence_ids": ["S2"], "time": None,
                  "ai_link_verification": list(verification or [])},
        "extraction_metadata": {"event_taxonomy": "observed_event"},
    }


def supported(linking_text, sentence_id="S2"):
    return [{"sentence_id": sentence_id, "verdict": "supported", "linking_text": linking_text}]


def test_gate_ignores_pronouns_and_proximity_without_verification():
    # v6.4 admitted this through "its" + adjacency. No verification record -> no link.
    event = hand_built_event("Separately, OpenAI released its new generative AI model.")
    assert Gate._cited_ai_link_present(event) is False


def test_gate_accepts_verified_link_across_a_paragraph_break():
    event = hand_built_event("The deal includes an AI concierge for guests.",
                             verification=supported("The deal includes"), paragraph="P2")
    assert Gate._cited_ai_link_present(event) is True


def test_gate_rejects_unverified_or_badly_verified_links():
    text = "The deal includes an AI concierge for guests."
    cases = {
        "no record": hand_built_event(text),
        "not supported": hand_built_event(text, verification=[
            {"sentence_id": "S2", "verdict": "not_supported", "linking_text": ""}]),
        "quote not in sentence": hand_built_event(text, verification=supported("The acquisition adds AI")),
        "one-word quote": hand_built_event(text, verification=supported("The")),
        "record for another sentence": hand_built_event(text, verification=supported("The deal includes", "S7")),
    }
    for name, event in cases.items():
        assert Gate._cited_ai_link_present(event) is False, name
        assert not assess(event)["admit"], name


def test_ai_words_in_event_text_do_not_admit_without_verification():
    event = hand_built_event("The deal includes an AI concierge for guests.")
    event["description"] = "Hotel Group uses generative AI agents for payroll."
    event["snippets"].append(event["description"])
    assessment = assess(event)
    assert not assessment["admit"]
    assert "ai_link_not_verified" in assessment["reasons"]


def test_gate_has_no_ai_keyword_requirement_for_verified_links():
    # The verdict decides; unusual wording with no AI keyword still counts.
    event = hand_built_event("The deal adds software that learns each guest's preferences.",
                             verification=supported("The deal adds software"))
    assert Gate._cited_ai_link_present(event) is True


if __name__ == "__main__":
    failures = 0
    for name, function in list(globals().items()):
        if name.startswith("test_") and callable(function):
            try:
                function()
                print(f"PASS {name}")
            except Exception as error:
                failures += 1
                print(f"FAIL {name}: {type(error).__name__}: {error}")
    raise SystemExit(1 if failures else 0)
