"""
Tests for agents/extraction_agent_v6.py.

No model or network needed: a stub LLM returns canned event frames for
whichever sentence IDs appear in the prompt, and table verdicts for the
AI-link verification call (tests/v6_model_stubs.py).

Run:  python -m tests.test_extraction_agent_v6
"""

from __future__ import annotations

import json
import re

from agents.extraction_agent import ExtractionAgent as V5
from agents.extraction_agent_v6 import ExtractionAgentV6, build_windows, segment_source
from tests.v6_model_stubs import (
    FramesAndVerifier, actor_action_reply, is_actor_action_prompt, is_verification_prompt, parse_claims,
    verification_reply,
)


TITLE = "Zeta Labs raises funding for AI underwriting"
BODY = (
    "Zeta Labs is a Bengaluru fintech. The company raised $40 million in Series B "
    "funding for its AI underwriting engine.\n\n"
    "Acme Inc. announced a partnership with\n"
    "Beta Corp to deploy AI agents across its claims unit.\n\n"
    "In 2026, RBI-regulated banks should increase investment in AI governance."
)
ARTICLE = {"source_id": "SOURCE-TEST", "title": TITLE, "body": BODY}
SOURCE_TEXT = TITLE + "\n\n" + BODY

ZETA = {
    "taxonomy": "observed_event",
    "event_type": "funding round",
    "summary": "Zeta Labs raised a Series B.",
    "actor": {"mention": "Zeta Labs", "sentence_id": "S2", "entity_type": "organization"},
    "action_sentence_ids": ["S3"],
    "ai_link_sentence_ids": ["S3"],
    "claim_status": "directly_stated",
    "temporal_status": "past",
    "quantities": [{"name": "Series B", "value": "$40 million", "unit": "", "sentence_id": "S3"}],
}
ACME = {
    "taxonomy": "observed_event",
    "event_type": "partnership",
    "summary": "Acme partnered with Beta Corp on AI agents.",
    "actor": {"mention": "Acme Inc.", "sentence_id": "S4", "entity_type": "organization"},
    "action_sentence_ids": ["S4"],
    "object": {"mention": "Beta Corp", "sentence_id": "S4"},
    "ai_link_sentence_ids": ["S4"],
    "relationships": [{
        "source_mention": "Acme Inc.", "relation_type": "PARTNERS_WITH",
        "target_mention": "Beta Corp", "sentence_id": "S4",
    }],
}
ADVICE = {"taxonomy": "advice", "event_type": "recommendation", "action_sentence_ids": ["S5"]}


# Answers extraction prompts with canned frames and verification prompts
# from a verdict table (tests/v6_model_stubs.py).
StubLLM = FramesAndVerifier


def org_tagger(text):
    spans = []
    for name in ("Zeta Labs", "Acme Inc.", "Beta Corp", "Hotel Group", "OpenAI"):
        position = text.find(name)
        if position >= 0:
            spans.append((position, position + len(name), "ORG"))
    return spans


def make_agent(frames, fail_on=None, filter_by_window=True, verdicts=None, **kwargs):
    # Citation/segmentation tests use explicit independent NER fixtures.
    kwargs.setdefault("entity_tagger", org_tagger)
    stub = StubLLM(frames, fail_on=fail_on, filter_by_window=filter_by_window, verdicts=verdicts)
    return ExtractionAgentV6(llm_service=stub, **kwargs)


def normalized(text):
    return " ".join(text.casefold().split())


# ----------------------------------------------------------------------
# Segmentation
# ----------------------------------------------------------------------

def test_segmentation_ids_offsets_and_wrap_join():
    sentences, paragraphs, stats = segment_source(SOURCE_TEXT)
    assert [s["id"] for s in sentences] == ["S1", "S2", "S3", "S4", "S5"]
    assert len(paragraphs) == 4
    assert stats["soft_wraps_joined"] == 1
    assert sentences[3]["text"].startswith("Acme Inc. announced a partnership with Beta Corp")
    for sentence in sentences:
        assert " ".join(SOURCE_TEXT[sentence["start"]:sentence["end"]].split()) == sentence["text"]


def test_segmentation_abbreviations_and_hard_breaks():
    cases = {
        "The U.S. Commerce Department issued rules. The rules apply in 2027.": 2,
        "Beta acquired Gamma Inc. The deal closed Monday.": 2,
        "Acme Inc. Beta Corp signed. It was quick.": 2,
        "John F. Kennedy spoke. It rained.": 2,
        "It raised a Series B. The round was led by Nexus.": 2,
        "Headline: Big news\nThe company said it would expand.": 2,
        "Highlights\n- Acme raised funds\n- Beta launched a model": 3,
    }
    for text, expected in cases.items():
        sentences, _, _ = segment_source(text)
        assert len(sentences) == expected, (text, [s["text"] for s in sentences])


# ----------------------------------------------------------------------
# Extraction
# ----------------------------------------------------------------------

def test_multi_sentence_event_is_one_event_with_both_citations():
    agent = make_agent([ZETA, ACME])
    events = {e["roles"]["actor"]["mention"]: e for e in agent.extract(ARTICLE)["events"]}
    zeta = events["Zeta Labs"]
    assert [c["id"] for c in zeta["cited_sentences"]] == ["S2", "S3"]
    assert zeta["summary"].startswith("The company raised $40 million")
    assert zeta["roles"]["ai_link_sentence_ids"] == ["S3"]
    assert zeta["quantitative_claims"][0]["value"] == "$40 million"
    assert zeta["extraction_metadata"]["multi_sentence_event"] is True
    assert zeta["entity_mentions"][0]["entity_type"] == "organization"
    assert zeta["description"] == "Zeta Labs is a Bengaluru fintech."
    assert zeta["actor"] == {"name": "Zeta Labs", "entity_type": "organization"}
    assert events["Acme Inc."]["relationship_mentions"][0]["relation_type"] == "PARTNERS_WITH"


def test_snippets_pass_v5_exact_source_validation():
    agent = make_agent([ZETA, ACME])
    v5 = V5(llm_service=StubLLM([]))
    for event in agent.extract(ARTICLE)["events"]:
        for snippet in event["snippets"]:
            assert v5._validate_source_snippet(snippet, SOURCE_TEXT) == snippet


def test_advice_is_never_an_event():
    agent = make_agent([ZETA, ADVICE])
    result = agent.extract(ARTICLE)
    assert all(e["roles"]["action_sentence_ids"] != ["S5"] for e in result["events"])
    metadata = agent.get_last_run_metadata()
    assert metadata["non_event_taxonomy_distribution"] == {"advice": 1}
    assert metadata["non_event_candidates"][0]["action_sentence_ids"] == ["S5"]


def test_invalid_frames_are_rejected_with_reasons():
    bad_citation = dict(ZETA, action_sentence_ids=["S99"])
    wrong_actor = dict(ZETA, actor={"mention": "Infosys", "sentence_id": "S3"})
    placeholder = dict(ZETA, actor={"mention": "The company", "sentence_id": "S3"})
    agent = make_agent([bad_citation, wrong_actor, placeholder], filter_by_window=False)
    assert agent.extract(ARTICLE)["events"] == []
    diagnostics = agent.get_last_run_metadata()["validation_diagnostics"]
    assert diagnostics["frame_rejected_invalid_action_citation"] == 1
    assert diagnostics["frame_rejected_actor_not_in_cited_sentence"] == 1
    assert diagnostics["frame_rejected_placeholder_actor"] == 1


def test_citation_to_sentence_outside_window_is_rejected():
    # Windows of one paragraph each: S4 (Acme) and S2 (Zeta) never share a window.
    cross_window = dict(ACME, actor={"mention": "Zeta Labs", "sentence_id": "S2"})
    agent = make_agent([cross_window])
    agent.max_window_characters = 150
    assert agent.extract(ARTICLE)["events"] == []
    diagnostics = agent.get_last_run_metadata()["validation_diagnostics"]
    assert diagnostics["frame_rejected_invalid_actor_citation"] == 1


def test_unsupported_quantity_is_dropped_not_the_event():
    agent = make_agent([dict(ZETA, quantities=[{"name": "x", "value": "$45 million", "sentence_id": "S3"}])])
    events = agent.extract(ARTICLE)["events"]
    assert len(events) == 1 and events[0]["quantitative_claims"] == []
    assert agent.get_last_run_metadata()["validation_diagnostics"]["quantity_dropped"] == 1


def test_ner_alignment_rejects_non_entity_actor():
    series_b = dict(ZETA, actor={"mention": "Series B", "sentence_id": "S3"})
    agent = make_agent([ZETA, series_b], entity_tagger=org_tagger)
    events = agent.extract(ARTICLE)["events"]
    assert [e["roles"]["actor"]["mention"] for e in events] == ["Zeta Labs"]
    assert events[0]["roles"]["actor"]["type_source"] == "ner"
    diagnostics = agent.get_last_run_metadata()["validation_diagnostics"]
    assert diagnostics["frame_rejected_actor_not_named_entity"] == 1


def test_person_actor_is_not_promoted_to_gate_institution():
    person = dict(ZETA, actor={"mention": "Zeta Labs", "sentence_id": "S2", "entity_type": "person"})
    event = make_agent([person], entity_tagger=lambda text: [(0, 9, "PERSON")]).extract(ARTICLE)["events"][0]
    assert "actor" not in event
    assert event["roles"]["actor"]["entity_type"] == "person"


def test_event_id_stable_across_runs_and_supporting_citations():
    first = make_agent([ZETA]).extract(ARTICLE)["events"][0]["event_id"]
    second = make_agent([ZETA]).extract(ARTICLE)["events"][0]["event_id"]
    trimmed = dict(ZETA, ai_link_sentence_ids=[], quantities=[])
    third = make_agent([trimmed]).extract(ARTICLE)["events"][0]["event_id"]
    assert first == second == third


def test_overlapping_windows_merge_duplicates():
    agent = make_agent([ACME])
    agent.max_window_characters = 300
    sentences, paragraphs, _ = segment_source(SOURCE_TEXT)
    windows = build_windows(sentences, paragraphs, max_chars=300, overlap_paragraphs=1)
    assert sum(3 in w for w in windows) == 2  # S4 appears in two windows
    events = agent.extract(ARTICLE)["events"]
    assert len(events) == 1
    assert events[0]["extraction_metadata"]["observed_in_windows"] == [1, 2]


def test_failed_window_keeps_other_windows():
    agent = make_agent([ZETA, ACME], fail_on={"S5"})
    agent.max_window_characters = 150
    events = agent.extract(ARTICLE)["events"]
    assert {e["roles"]["actor"]["mention"] for e in events} == {"Zeta Labs", "Acme Inc."}
    metadata = agent.get_last_run_metadata()
    assert metadata["run_status"] == "partial_ok"
    assert metadata["failed_window_indexes"] == [metadata["window_count"]]
    assert events[0]["extraction_metadata"]["support_scope"] == "successfully_processed_windows_only"


class LLMModelUnavailableError(Exception):
    """Same class name LLMService raises when Ollama reports a missing model."""


class MissingModel:
    model_name = "gemini-missing"

    def ask(self, prompt, system_prompt=None, **kwargs):
        raise LLMModelUnavailableError("model 'gemini-missing' not found")


class OllamaDown:
    model_name = "unreachable"

    def ask(self, prompt, system_prompt=None, **kwargs):
        raise ConnectionError("Could not reach local inference endpoint")


def test_unavailable_model_moves_to_next_in_chain():
    agent = make_agent([ZETA])
    working = agent.llm
    agent._llm_chain = [MissingModel(), working]
    agent._llm_index = 0
    agent.llm = agent._llm_chain[0]
    events = agent.extract(ARTICLE)["events"]
    assert len(events) == 1
    assert agent.get_last_run_metadata()["model_name"] == "stub-model"


def test_no_reachable_model_falls_back_to_v5():
    import os
    os.environ["EVENT_INTELLIGENCE_FAST_SEMANTIC_GATE"] = "0"
    agent = ExtractionAgentV6(llm_service=OllamaDown(), entity_tagger=org_tagger, fallback_to_v5=True)
    result = agent.extract(ARTICLE, research_depth="deep")
    metadata = agent.get_last_run_metadata()
    assert result["source_id"] == "SOURCE-TEST"
    assert metadata["extractor_used"] == "v5_fallback"
    assert metadata["validation_diagnostics"]["research_depth_deep"] == 1
    assert metadata["v6_failure"]["window_errors"]


# ----------------------------------------------------------------------
# Provider wiring (GeminiLLMService) and explicit fallback
# ----------------------------------------------------------------------

class FakeGeminiLLMService:
    """Stands in for the project's GeminiLLMService (constructor takes model_name)."""
    constructed: list = []

    def __init__(self, model_name="", timeout_seconds=60.0, api_key=None):
        self.model_name = model_name
        FakeGeminiLLMService.constructed.append(model_name)

    def ask(self, prompt, system_prompt=None, temperature=0.0, response_format=None):
        if is_actor_action_prompt(prompt):
            return actor_action_reply(prompt)
        if is_verification_prompt(prompt):
            return verification_reply(prompt)
        present = set(re.findall(r"^\[(S\d+)\]", prompt, flags=re.MULTILINE))
        return json.dumps({"events": [f for f in [ZETA] if set(f["action_sentence_ids"]) <= present]})


def test_gemini_provider_uses_the_configured_client_class():
    import os
    from agents import extraction_agent_v6 as module
    os.environ["EXTRACTION_V6_GEMINI_CLASS"] = f"{__name__}:FakeGeminiLLMService"
    os.environ.pop("EXTRACTION_V6_PROVIDER", None)
    os.environ.pop("EXTRACTION_V6_MODEL", None)
    FakeGeminiLLMService.constructed.clear()
    try:
        agent = ExtractionAgentV6(entity_tagger=org_tagger)
        events = agent.extract(ARTICLE)["events"]
    finally:
        os.environ.pop("EXTRACTION_V6_GEMINI_CLASS", None)
    assert agent.provider == "gemini"
    assert FakeGeminiLLMService.constructed == list(module.DEFAULT_MODEL_CHAIN)
    assert len(events) == 1
    metadata = agent.get_last_run_metadata()
    assert metadata["extractor_used"] == "v6"
    assert metadata["model_name"] == module.DEFAULT_MODEL_CHAIN[0]


def test_missing_gemini_client_falls_back_explicitly_with_reason():
    import os
    os.environ["EXTRACTION_V6_GEMINI_CLASS"] = "services.does_not_exist:GeminiLLMService"
    os.environ["EVENT_INTELLIGENCE_FAST_SEMANTIC_GATE"] = "0"
    try:
        agent = ExtractionAgentV6(entity_tagger=org_tagger, fallback_to_v5=True)
        agent.extract(ARTICLE)
    finally:
        os.environ.pop("EXTRACTION_V6_GEMINI_CLASS", None)
    metadata = agent.get_last_run_metadata()
    assert metadata["extractor_used"] == "v5_fallback"
    assert "not importable" in metadata["v6_failure"]["reason"]
    assert metadata["v6_failure"]["provider"] == "gemini"


def test_missing_provider_with_fallback_disabled_fails_loudly():
    import os
    os.environ["EXTRACTION_V6_GEMINI_CLASS"] = "services.does_not_exist:GeminiLLMService"
    try:
        agent = ExtractionAgentV6(entity_tagger=org_tagger, fallback_to_v5=False)
        try:
            agent.extract(ARTICLE)
        except RuntimeError as error:
            assert "no usable model provider" in str(error)
            return
        raise AssertionError("expected RuntimeError")
    finally:
        os.environ.pop("EXTRACTION_V6_GEMINI_CLASS", None)


# ----------------------------------------------------------------------
# AI-link: decided in context by a verification step, not by word rules
#
# The verifier double answers from a verdict table (the model's judgment);
# these tests check the code around it: what context the verifier sees,
# that "supported" needs a verbatim quote, that failures reject the link,
# and that rejected sentences never enter the event.
# ----------------------------------------------------------------------

HOTEL_FRAME = {
    "taxonomy": "observed_event", "event_type": "acquisition",
    "actor": {"mention": "Hotel Group", "sentence_id": "S1", "entity_type": "organization"},
    "action_sentence_ids": ["S1"],
    "ai_link_sentence_ids": ["S2"],
}
# Reviewer case 1: "its" refers to OpenAI, not to the acquisition.
HOTEL_PRONOUN = {"source_id": "SOURCE-HOTEL-1", "title": "",
                 "body": "Hotel Group acquired a resort. Separately, OpenAI released its new generative AI model."}
# Reviewer case 2: a real link across a paragraph break.
HOTEL_PARAGRAPH = {"source_id": "SOURCE-HOTEL-2", "title": "",
                   "body": "Hotel Group acquired a resort.\n\nThe deal includes an AI concierge for guests."}


def run_hotel(article, **stub_kwargs):
    stub = FramesAndVerifier([HOTEL_FRAME], filter_by_window=False, **stub_kwargs)
    agent = ExtractionAgentV6(llm_service=stub, entity_tagger=org_tagger)
    events = agent.extract(article)["events"]
    assert len(events) == 1
    return events[0], agent.get_last_run_metadata(), stub


def test_reviewer_pronoun_case_is_not_linked():
    event, metadata, stub = run_hotel(HOTEL_PRONOUN)
    assert event["roles"]["ai_link_sentence_ids"] == []
    assert event["roles"]["ai_link_verification"] == []
    assert "OpenAI" not in " ".join(event["snippets"]) + event["description"]
    assert event["extraction_metadata"]["ai_link_rejected"] == [{
        "sentence_id": "S2", "verdict": "not_supported", "reason": "not_supported",
        "candidate_origin": "model_citation",
    }]
    # The verifier was asked about THIS event with the full context.
    claim = parse_claims(stub.verification_prompts[0])[0]
    assert claim["actor"] == "Hotel Group"
    assert claim["action"] == "Hotel Group acquired a resort."
    assert claim["context"] == [
        "[S1] Hotel Group acquired a resort.",
        "[S2] Separately, OpenAI released its new generative AI model.",
    ]
    assert metadata["ai_link_verification"]["rejected"] == 1


def test_reviewer_paragraph_case_is_linked():
    event, metadata, stub = run_hotel(HOTEL_PARAGRAPH)
    assert event["roles"]["ai_link_sentence_ids"] == ["S2"]
    assert event["roles"]["ai_link_verification"] == [{
        "sentence_id": "S2", "verdict": "supported", "linking_text": "The deal includes",
        "candidate_origin": "model_citation", "verifier_model": "stub-model",
    }]
    assert event["description"] == "The deal includes an AI concierge for guests."
    assert [c["id"] for c in event["cited_sentences"]] == ["S1", "S2"]
    # The paragraph break is shown to the verifier, not used as a rule.
    assert parse_claims(stub.verification_prompts[0])[0]["context"] == [
        "[S1] Hotel Group acquired a resort.", "¶", "[S2] The deal includes an AI concierge for guests.",
    ]
    # The verified link is also in the copy the gate reads after persistence.
    pointer = event["extraction_metadata"]["span_pointer"]
    assert pointer["roles"]["ai_link_verification"][0]["verdict"] == "supported"
    assert metadata["ai_link_verification"]["verified"] == 1


def test_no_word_rule_decides_the_link():
    # Same texts, opposite verdicts: the outcome follows the contextual
    # verdict, not pronouns ("its") or paragraph breaks.
    pronoun_sentence = "Separately, OpenAI released its new generative AI model."
    paragraph_sentence = "The deal includes an AI concierge for guests."
    kept, _, _ = run_hotel(HOTEL_PRONOUN, verdicts={pronoun_sentence: ("supported", "released its new")})
    dropped, _, _ = run_hotel(HOTEL_PARAGRAPH, verdicts={paragraph_sentence: ("not_supported", "")})
    assert kept["roles"]["ai_link_sentence_ids"] == ["S2"]
    assert kept["roles"]["ai_link_verification"][0]["linking_text"] == "released its new"
    assert dropped["roles"]["ai_link_sentence_ids"] == []
    assert dropped["extraction_metadata"]["ai_link_rejected"][0]["verdict"] == "not_supported"


def test_supported_verdict_with_invalid_quote_is_rejected_and_incomplete():
    sentence = "The deal includes an AI concierge for guests."
    for quote, reason in (("The acquisition adds AI", "linking_text_not_in_sentence"),
                          ("", "linking_text_too_short"), ("includes", "linking_text_too_short")):
        event, metadata, _ = run_hotel(HOTEL_PARAGRAPH, verdicts={sentence: ("supported", quote)})
        assert event["roles"]["ai_link_sentence_ids"] == []
        assert event["extraction_metadata"]["ai_link_rejected"][0]["reason"] == reason
        # Not a valid answer: the run is incomplete, like a timeout.
        assert metadata["run_status"] == "partial_ok" and metadata["complete"] is False, quote
        assert metadata["incomplete_reasons"] == ["ai_link_verification_failed"]


def test_one_word_quote_is_rejected():
    event, metadata, _ = run_hotel(
        HOTEL_PRONOUN,
        verdicts={"Separately, OpenAI released its new generative AI model.": ("supported", "its")},
    )
    assert event["roles"]["ai_link_sentence_ids"] == []
    assert event["extraction_metadata"]["ai_link_rejected"][0]["reason"] == "linking_text_too_short"
    assert metadata["complete"] is False


def test_verifier_failure_marks_the_run_incomplete():
    # Reviewer case 3: a timeout used to give run_status "ok".
    event, metadata, _ = run_hotel(HOTEL_PARAGRAPH, fail_verification=True)
    assert event["roles"]["ai_link_sentence_ids"] == []
    assert metadata["run_status"] == "partial_ok"
    assert metadata["complete"] is False
    assert metadata["incomplete_reasons"] == ["ai_link_verification_failed"]
    assert event["extraction_metadata"]["ai_link_verification_complete"] is False
    assert metadata["ai_link_verification"]["unanswered"] == 1
    assert "TimeoutError" in metadata["ai_link_verification"]["errors"][0]


def test_unparseable_reply_or_missing_verdict_is_incomplete_not_a_rejection():
    _, unreadable, _ = run_hotel(HOTEL_PARAGRAPH, raw_verification_reply="I think it is supported.")
    _, missing, _ = run_hotel(HOTEL_PARAGRAPH, drop_claims={0})
    for metadata in (unreadable, missing):
        assert metadata["run_status"] == "partial_ok"
        assert metadata["incomplete_reasons"] == ["ai_link_verification_failed"]


def test_answered_verification_is_complete():
    _, metadata, _ = run_hotel(HOTEL_PRONOUN)  # verdict not_supported is an answer
    assert metadata["run_status"] == "ok" and metadata["complete"] is True


def test_reviewer_long_distance_link_is_verified():
    # Reviewer case 2: eight intervening details, then the AI link.
    details = " ".join(f"The resort has feature number {n}." for n in range(1, 9))
    article = {"source_id": "SOURCE-HOTEL-FAR", "title": "",
               "body": f"Hotel Group acquired a resort. {details} The deal includes an AI concierge for guests."}
    frame = dict(HOTEL_FRAME, ai_link_sentence_ids=["S10"])
    stub = FramesAndVerifier([frame], filter_by_window=False)
    event = ExtractionAgentV6(llm_service=stub, entity_tagger=org_tagger).extract(article)["events"][0]
    assert event["roles"]["ai_link_sentence_ids"] == ["S10"]
    context = parse_claims(stub.verification_prompts[0])[0]["context"]
    assert context[0] == "[S1] Hotel Group acquired a resort."
    assert context[-1] == "[S10] The deal includes an AI concierge for guests."
    assert len(context) == 10  # short article: everything in between is shown


def test_long_article_context_keeps_relevant_passages_and_marks_omissions():
    filler = " ".join(f"Tourism statistic {n} rose by {n} percent in the region." for n in range(1, 80))
    body = "\n\n".join([
        "Hotel Group acquired a resort.",               # P1 action
        filler,                                          # P2 unrelated
        "Hotel Group plans to renovate the lobby.",      # P3 names the actor
        filler,                                          # P4 unrelated
        "Integration work starts in March.",             # P5 just before the candidate
        "The deal includes an AI concierge for guests.", # P6 candidate
    ])
    sentences, _, _ = segment_source(body)
    candidate = next(s["id"] for s in sentences if s["text"].startswith("The deal includes"))
    frame = dict(HOTEL_FRAME, ai_link_sentence_ids=[candidate])
    stub = FramesAndVerifier([frame], filter_by_window=False)
    event = ExtractionAgentV6(llm_service=stub, entity_tagger=org_tagger).extract(
        {"source_id": "SOURCE-HOTEL-LONG", "title": "", "body": body})["events"][0]
    context = parse_claims(stub.verification_prompts[0])[0]["context"]
    texts = [line.split("] ", 1)[-1] for line in context]
    assert "Hotel Group acquired a resort." in texts
    assert "Hotel Group plans to renovate the lobby." in texts
    assert "Integration work starts in March." in texts
    assert "The deal includes an AI concierge for guests." in texts
    assert not any("Tourism statistic" in line for line in context)
    assert context.count("[… 1 paragraph(s) omitted …]") == 2
    assert event["roles"]["ai_link_sentence_ids"] == [candidate]


def test_paragraph_before_the_candidate_is_shown_even_when_candidate_comes_first():
    body = ("Rival Corp agreed to buy a hospitality software firm.\n\n"
            "The deal includes an AI concierge for guests. Hotel Group acquired a beach resort.")
    frame = {"taxonomy": "observed_event", "event_type": "acquisition",
             "actor": {"mention": "Hotel Group", "sentence_id": "S3", "entity_type": "organization"},
             "action_sentence_ids": ["S3"], "ai_link_sentence_ids": ["S2"]}
    stub = FramesAndVerifier([frame], filter_by_window=False)
    ExtractionAgentV6(llm_service=stub, entity_tagger=org_tagger).extract(
        {"source_id": "SOURCE-RIVAL", "title": "", "body": body})
    context = parse_claims(stub.verification_prompts[0])[0]["context"]
    assert context[0] == "[S1] Rival Corp agreed to buy a hospitality software firm."


def test_ai_words_in_the_actors_background_are_verified_not_trusted():
    # Reviewer case 1 at extraction: the background sentence is checked even
    # when the model does not cite it as an AI link.
    article = {"source_id": "SOURCE-HOTEL-BG", "title": "",
               "body": "Hotel Group uses generative AI for internal payroll. The company acquired a beach resort."}
    for cited in ([], ["S1"]):
        frame = {"taxonomy": "observed_event", "event_type": "acquisition",
                 "actor": {"mention": "Hotel Group", "sentence_id": "S1", "entity_type": "organization"},
                 "action_sentence_ids": ["S2"], "ai_link_sentence_ids": cited}
        stub = FramesAndVerifier([frame], filter_by_window=False)
        event = ExtractionAgentV6(llm_service=stub, entity_tagger=org_tagger).extract(article)["events"][0]
        assert len(stub.verification_prompts) == 1
        assert event["roles"]["ai_link_sentence_ids"] == []
        rejected = event["extraction_metadata"]["ai_link_rejected"]
        assert [(r["sentence_id"], r["verdict"]) for r in rejected] == [("S1", "not_supported")]
        assert rejected[0]["candidate_origin"] == ("model_citation" if cited else "event_sentence")


def test_cited_link_is_judged_by_the_verifier_not_by_ai_keywords():
    # No keyword pre-filter: a sentence with no AI words is still judged.
    no_ai_words = {"source_id": "SOURCE-HOTEL-3", "title": "",
                   "body": "Hotel Group acquired a resort. The resort has two swimming pools."}
    event, _, stub = run_hotel(no_ai_words)
    assert len(stub.verification_prompts) == 1
    assert event["roles"]["ai_link_sentence_ids"] == []
    assert "swimming" not in event["description"]
    unusual = {"source_id": "SOURCE-HOTEL-4", "title": "",
               "body": "Hotel Group acquired a resort. The deal adds software that learns each guest's preferences."}
    event, _, _ = run_hotel(unusual, verdicts={
        "The deal adds software that learns each guest's preferences.": ("supported", "The deal adds software")})
    assert event["roles"]["ai_link_sentence_ids"] == ["S2"]


def test_separate_verifier_model_answers_verification():
    class Verifier(FramesAndVerifier):
        model_name = "verifier-model"

    extractor, verifier = FramesAndVerifier([HOTEL_FRAME], filter_by_window=False), Verifier([])
    agent = ExtractionAgentV6(llm_service=extractor, verifier_llm_service=verifier, entity_tagger=org_tagger)
    event = agent.extract(HOTEL_PARAGRAPH)["events"][0]
    assert extractor.verification_prompts == [] and len(verifier.verification_prompts) == 1
    assert event["roles"]["ai_link_verification"][0]["verifier_model"] == "verifier-model"
    assert agent.get_last_run_metadata()["ai_link_verification"]["verifier_model"] == "verifier-model"


def test_verifier_model_from_configuration():
    import os
    os.environ["EXTRACTION_V6_GEMINI_CLASS"] = f"{__name__}:FakeGeminiLLMService"
    os.environ["EXTRACTION_V6_VERIFIER_MODEL"] = "gemini-verifier"
    FakeGeminiLLMService.constructed.clear()
    try:
        agent = ExtractionAgentV6(entity_tagger=org_tagger)
    finally:
        os.environ.pop("EXTRACTION_V6_GEMINI_CLASS", None)
        os.environ.pop("EXTRACTION_V6_VERIFIER_MODEL", None)
    assert agent.verifier_llm.model_name == "gemini-verifier"
    assert agent.verifier_error == ""


def test_one_verification_call_per_batch_of_claims():
    stub = FramesAndVerifier([ZETA, ACME])
    ExtractionAgentV6(llm_service=stub, entity_tagger=org_tagger).extract(ARTICLE)
    assert len(stub.verification_prompts) == 1
    assert [c["candidate_id"] for c in parse_claims(stub.verification_prompts[0])] == ["S3", "S4"]


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
