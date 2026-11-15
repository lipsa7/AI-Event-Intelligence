"""8 Nov 2026 (Claude-proxy replay of the 25 Oct run): the analysis packet held only funding rounds and
every support cluster was a pair of them linked by one generic tag, so the main development never
reached the analysis. Also: stop asking more models once two have returned no candidate."""
from __future__ import annotations

import agents.strategic_inference_agent as sia
from services.research_orchestrator_service import ResearchOrchestratorService as ROS


def _agent():
    return sia.StrategicInferenceAgent.__new__(sia.StrategicInferenceAgent)


def _funding(i):
    return {"id": f"EVID-F{i}", "event_id": f"EVENT-F{i}", "source_id": f"S-F{i}", "evidence_type": "quantitative_claim",
            "support_strength": "strong", "temporal_status": "current",
            "text": f"Startup{i} today announced it raised $1 billion in funding at a $10 billion valuation."}


def test_top_development_reaches_the_packet_despite_numbers_heavy_rivals():
    agent = _agent()
    records = [_funding(i) for i in range(30)] + [
        {"id": "EVID-ACCORD", "event_id": "EVENT-ACCORD", "source_id": "S-A", "evidence_type": "source_excerpt",
         "text": "The companies agreed to a voluntary framework with no penalties."}]
    agent._event_development_rank_by_id = {"EVENT-ACCORD": 1}
    review = agent._review_all_evidence(research_question="artificial intelligence", evidence_records=records,
                                        auxiliary_context={}, reasoning_guidance={}, active_budget=None,
                                        execution_stage="t")
    packet = agent._strategic_evidence_subset(research_question="artificial intelligence", evidence_records=records,
                                              evidence_review=review, maximum=24)
    assert "EVID-ACCORD" in {p["evidence_id"] for p in packet}
    agent._event_development_rank_by_id = {}
    packet = agent._strategic_evidence_subset(research_question="artificial intelligence", evidence_records=records,
                                              evidence_review=review, maximum=24)
    assert "EVID-ACCORD" not in {p["evidence_id"] for p in packet}   # the defect this fixes


def test_orchestrator_tags_events_with_development_rank():
    events = [{"event_id": "EVENT-A"}, {"event_id": "EVENT-B"}, {"event_id": "EVENT-C"}]
    evidence = [{"evidence_id": "E1", "event_id": "EVENT-A"}, {"evidence_id": "E2", "event_id": "EVENT-B"}]
    devs = [{"facts": [{"evidence_id": "E2"}]}, {"facts": [{"evidence_id": "E1"}]}]
    state = {}
    ROS._tag_development_rank(devs, events, evidence, state)
    assert events[1]["development_rank"] == 1 and events[0]["development_rank"] == 2
    assert "development_rank" not in events[2]


def _digest_item(i, actor, claim, tags=("capital_access",)):
    return {"evidence_id": f"EVID-{i}", "source_id": f"S{i}", "event_id": f"EVENT-{i}",
            "underlying_event_id": f"U{i}", "claim": claim, "primary_acting_institution": actor,
            "strategic_cluster_id": f"dev_{i}", "dependency_tags": list(tags), "named_signals": [actor]}


def test_clusters_are_diverse_and_common_words_do_not_link_records():
    agent = _agent()
    items = [_digest_item(i, f"Startup{i % 3}", f"Startup{i % 3} today raised $1 billion in funding at a valuation")
             for i in range(9)]
    items += [_digest_item(20, "OpenAI", "OpenAI signed the voluntary safety accord at the White House", ("model_provider",)),
              _digest_item(21, "Simons Foundation", "Simons Foundation and OpenAI fund mathematicians' AI standards",
                           ("model_provider",))]
    items[-1]["development_rank"] = 1
    clusters = agent._supportable_evidence_clusters(items, research_question="artificial intelligence",
                                                    maximum_clusters=10)
    pairs = [tuple(sorted(c["acting_institutions"])) for c in clusters]
    assert len(pairs) == len(set(pairs))                       # the same two institutions at most once
    assert any("Simons Foundation" in p for p in pairs)
    for c in clusters:
        assert not {"billion", "funding", "today", "raised"} & set(c["shared_terms"])


def test_rotation_stops_after_two_models_return_no_candidate():
    assert sia.STRATEGIC_MAX_ABSTENTION_OPINIONS == 2
    assert not sia.StrategicInferenceAgent._returned_candidates({"candidate_insights": [], "analysis_summary": "x"})
    assert sia.StrategicInferenceAgent._returned_candidates({"candidate_insights": [{"insight": "y"}]})


def test_per_call_context_comes_after_the_shared_evidence_prefix():
    import inspect
    source = inspect.getsource(sia.StrategicInferenceAgent._generate_synthesis)
    assert source.index("ADMITTED EVIDENCE DIGEST") < source.index("Refinement context (NOT evidence)")
    assert source.index("ADMITTED EVIDENCE DIGEST") < source.index("Prior report context (NOT evidence)")
