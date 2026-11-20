"""v12.1 (18 Nov 2026): fixes found by a simulated deep run (Claude answering every model call)."""
import inspect

from services.research_orchestrator_service import ResearchOrchestratorService, _is_page_boilerplate


def test_page_boilerplate_is_not_an_event():
    for title in ["Read more here about its fundraise from Wealth Company Alternates and Bessemer Venture Partners.",
                  "READ MORE | MY STORY | I was a prison warden and now I'm building an AI model",
                  "Read more: Nvidia launches AI safety platform after Jensen Huang calls warnings 'odd'",
                  "Test your business creativity with Edition 255 of our weekly quiz!"]:
        assert _is_page_boilerplate({"title": title})
    for title in ["OpenAI reported the breach to Australia on 10 September.",
                  "Readers of the bill note that it reads more strictly than the draft.",
                  "SiMa.ai secured a $150 million Series C round."]:
        assert not _is_page_boilerplate({"title": title})


def test_admission_excludes_boilerplate_events():
    events = [{"event_id": "EVENT-1", "title": "Read more here about its fundraise from Accel.",
               "summary": "Read more here about its fundraise from Accel."}]
    result = ResearchOrchestratorService._admit_strategic_events(topic="artificial intelligence", events=events,
                                                                 evidence=[])
    text = str(result)
    assert "page_boilerplate_not_an_event" in text


def test_computed_gap_route_replaces_the_pair_requirement_only_for_cited_gaps():
    from agents import strategic_inference_agent as agent
    source = inspect.getsource(agent)
    assert "EXCEPTION (computed gaps)" in source
    assert 'f"computed_gap:{computed_gap.get(\'kind\')}"' in source
    assert 'startswith("computed_gap:")' in source
    synthesis = inspect.getsource(agent.StrategicInferenceAgent._generate_synthesis)
    assert 'item.get("claim")' in synthesis and "underlying_event_id" in synthesis


def test_leverage_claims_are_not_built_from_sentences_saying_none_is_named():
    service = ResearchOrchestratorService.__new__(ResearchOrchestratorService)
    garbled = {"leverage_analysis": {
        "holder": "OpenAI", "controls": "how GPT-6.1 Sol answers election questions",
        "dependent": "The sources name no party that depends on OpenAI's choice here; no user is named.",
        "holder_incentive": "The only cited cost is Anthropic's, not OpenAI's. No record states OpenAI's costs.",
        "dependent_incentive": "x would want y", "leverage_evidence_ids": ["EVID-1"]}}
    assert [c["claim_id"] for c in service._leverage_claims(garbled, "REV")] == ["REV-CONTROL"]
    named = {"leverage_analysis": {
        "holder": "OpenAI", "controls": "the breach logs", "dependent": "Australia's Home Affairs department",
        "holder_incentive": "to report on its own timetable", "leverage_evidence_ids": ["EVID-1"]}}
    ids = [c["claim_id"] for c in service._leverage_claims(named, "REV")]
    assert ids == ["REV-CONTROL", "REV-DEPENDENCY", "REV-HOLDER-INCENTIVE"]


def test_packet_gives_the_top_developments_depth():
    from agents.strategic_inference_agent import StrategicInferenceAgent
    agent = object.__new__(StrategicInferenceAgent)
    records = []
    for dev in (1, 2, 3):                       # three main developments, 10 records each, many sources
        for i in range(10):
            records.append({"evidence_id": f"EVID-D{dev}-{i}", "event_id": f"EVENT-D{dev}-{i % 5}",
                            "source_id": f"SRC-D{dev}-{i}", "evidence_type": "source_excerpt",
                            "claim": f"Development {dev} record {i} reports the agency's decision",
                            "published_at": "2026-10-01"})
    for i in range(40):                         # forty unrelated stories with numbers (scored higher)
        records.append({"evidence_id": f"EVID-X{i}", "event_id": f"EVENT-X{i}", "source_id": f"SRC-X{i}",
                        "evidence_type": "source_excerpt", "published_at": "2026-10-01",
                        "claim": f"Startup {i} raised ${i + 10} million in a Series B round led by fund {i}"})
    agent._event_development_rank_by_id = {f"EVENT-D{d}-{k}": d for d in (1, 2, 3) for k in range(5)}
    extracted = agent._extract_evidence_records(records)
    digest = agent._synthesis_digest(research_question="AI governance", evidence_records=extracted,
                                     evidence_review={}, maximum=36)
    per_dev = {d: sum(1 for item in digest if item["evidence_id"].startswith(f"EVID-D{d}-")) for d in (1, 2, 3)}
    assert all(count == 8 for count in per_dev.values()), per_dev        # 24 of 36: depth for the top three
    assert sum(1 for item in digest if item["evidence_id"].startswith("EVID-X")) == 12   # breadth kept for context


def test_every_event_of_a_development_gets_its_rank():
    developments = [{"event_ids": ["EVENT-A", "EVENT-B", "EVENT-C"], "facts": [{"evidence_id": "EVID-A"}]},
                    {"event_ids": ["EVENT-D"], "facts": []}]
    events = [{"event_id": e} for e in ("EVENT-A", "EVENT-B", "EVENT-C", "EVENT-D", "EVENT-E")]
    ranks = ResearchOrchestratorService._tag_development_rank(
        developments, events, [{"evidence_id": "EVID-A", "event_id": "EVENT-A"}], {})
    assert ranks == {"EVENT-A": 1, "EVENT-B": 1, "EVENT-C": 1, "EVENT-D": 2}
