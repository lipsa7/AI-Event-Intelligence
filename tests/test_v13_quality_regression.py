"""v13 (18 Nov 2026): quality regression tests on the app's own saved news.

Each test pins an OUTCOME the owner decided on, not an implementation detail, so a later change that
keeps a mechanism running but defeats its purpose fails here (the research report "Fixing AI Event
Intelligence", 18 Nov 2026: tie every owner decision to an outcome-level check).

The corpus is data/event_intelligence.db (in the repo): the articles of 10 Sep - 25 Oct 2026, which
include the AI-agent breach that governments learned of about three months late - the story behind
the one insight the owner rated good. No model calls.
"""
import sqlite3
from pathlib import Path

import pytest

from services import insight_potential
from services.research_orchestrator_service import ResearchOrchestratorService, _is_page_boilerplate

DB = Path(__file__).resolve().parent.parent / "data" / "event_intelligence.db"


def _corpus(start="2026-09-10", end="2026-10-26"):
    if not DB.exists():
        pytest.skip("saved corpus not present")
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "select e.event_id, e.source_id, e.title, e.summary, a.published_at, a.source_name "
        "from events e join articles a on a.source_id = e.source_id "
        "where a.published_at >= ? and a.published_at < ?", (start, end))]
    events, evidence = [], []
    for row in rows:
        event = {"event_id": row["event_id"].upper(), "title": row["title"], "summary": row["summary"],
                 "underlying_event_id": row["event_id"].upper(), "source_id": row["source_id"],
                 "published_at": row["published_at"]}
        if _is_page_boilerplate(event):
            continue
        events.append(event)
        evidence.append({"evidence_id": "EVID-" + row["event_id"].upper(), "event_id": row["event_id"].upper(),
                         "evidence_type": "source_excerpt",
                         "source_id": row["source_id"], "text": row["summary"] or row["title"],
                         "published_at": row["published_at"]})
    return events, evidence


BREACH = ("breach", "broken into", "hacked", "notified the government", "alert")


def _is_breach_story(text: str) -> bool:
    text = text.casefold()
    return ("openai" in text or "albanese" in text or "hugging face" in text) and any(w in text for w in BREACH)


def test_canary_the_breach_story_is_chosen_for_deep_analysis():
    """Decision (Oct 2026): the stories analysed in depth are those with insight potential. The breach
    story must rank in the top 5 developments; with coverage-based ranking it did not."""
    events, evidence = _corpus()
    service = ResearchOrchestratorService.__new__(ResearchOrchestratorService)
    developments = service._developments_to_watch(events, evidence, {}, limit=5)
    texts = [" ".join([d.get("title", "")] + [f.get("text", "") for f in d.get("facts") or []]) for d in developments]
    assert any(_is_breach_story(t) for t in texts), [d.get("title", "")[:90] for d in developments]


def test_top_stories_are_not_funding_rounds():
    """Decision (18 Nov 2026): funding-only stories are not the run's main developments."""
    events, evidence = _corpus()
    service = ResearchOrchestratorService.__new__(ResearchOrchestratorService)
    developments = service._developments_to_watch(events, evidence, {}, limit=3)
    for development in developments:
        members = [e for e in events if e["event_id"] in set(development.get("event_ids") or [])]
        assert "funding_only" not in insight_potential.score(members)["parts"], development.get("title")


def test_exploration_reads_the_highest_potential_stories_first():
    """Decision: the per-story analysis (exploration) starts with stories that can yield an insight."""
    events, evidence = _corpus()
    service = ResearchOrchestratorService.__new__(ResearchOrchestratorService)
    combos = service._development_combinations(events, evidence, (), limit=5)
    assert combos, "no stories to explore"
    first = combos[0]
    members = [e for e in events if e["event_id"] in set(first["event_ids"])]
    parts = insight_potential.score(members)["parts"]
    assert {"official_actor", "incident_or_enforcement"} & set(parts), first["labels"]


def test_top_story_dossier_has_depth_without_duplicates():
    """Decision (18 Nov 2026): the generator gets the top stories in depth (at least 6 different records
    for the first) and never the same sentence twice."""
    from agents.strategic_inference_agent import StrategicInferenceAgent, _near_duplicate_key
    events, evidence = _corpus()
    service = ResearchOrchestratorService.__new__(ResearchOrchestratorService)
    developments = service._developments_to_watch(events, evidence, {}, limit=3)
    ranks = ResearchOrchestratorService._tag_development_rank(developments, events, evidence, {})
    agent = object.__new__(StrategicInferenceAgent)
    agent._event_development_rank_by_id = ranks
    records = agent._extract_evidence_records([{**r, "claim": r["text"]} for r in evidence])
    digest = agent._synthesis_digest(research_question="AI governance and regulation", evidence_records=records,
                                     evidence_review={}, maximum=36)
    keys = [_near_duplicate_key(item["claim"]) for item in digest if _near_duplicate_key(item["claim"])]
    assert len(keys) == len(set(keys)), "the same sentence reached the generator twice"
    top = sum(1 for item in digest if item.get("development_rank") == 1)
    available = len({_near_duplicate_key(r["text"]) for r in evidence if ranks.get(r["event_id"]) == 1})
    assert top >= min(6, available), (top, available)


def test_deep_runs_can_release_several_insights_and_keep_exploring():
    """Decision (18 Nov 2026): multiple insights per run; exploration runs even when the main generator
    returns nothing, and continues until the run's limit."""
    import inspect
    from services import research_orchestrator_service as orchestrator
    assert orchestrator.DEEP_PROFILE.max_selected_insights >= 3
    assert orchestrator.INSIGHT_EXPLORATION_COMBINATIONS >= 5
    source = inspect.getsource(orchestrator.ResearchOrchestratorService.run)
    assert "exploration_after_empty_generator" in source
    release = inspect.getsource(orchestrator.ResearchOrchestratorService._claim_supported_release)
    assert "original_released() >= limit" in release


def test_lab_prompt_and_code_agree_the_model_does_not_filter_its_own_ideas():
    """Decision (12 Nov 2026): the self-score does not filter lab ideas. The code was changed then but the
    prompt still told the model to return only ideas it scored 7 or more, and a simulated run's lab
    returned nothing. Prompt and code must agree."""
    from services import strategic_hypothesis_lab as lab
    prompt = lab.evolution_prompt("ai", [{"id": "B1H1", "thesis": "t"}], [])
    assert "Only return candidates with total critic score" not in prompt
    assert "do not drop a candidate for its score" in prompt


def test_run_quality_gates_flag_a_useless_run_and_pass_a_good_one():
    from services import run_quality
    funding = [{"event_id": "E1", "title": "Acme raises $50M Series B led by Big Ventures at a $1B valuation",
                "summary": "Acme raised $50 million in a Series B round led by Big Ventures, valuing it at $1 billion."}]
    governance = [{"event_id": "E2", "title": "FTC opens investigation into OpenAI after breach disclosure",
                   "summary": "The Federal Trade Commission is investigating OpenAI over a breach it disclosed in September."}]
    useless = run_quality.gates({}, {"developments_to_watch": [{"title": "Acme", "event_ids": ["E1"]}], "hidden_insights": []},
                                funding, [], depth="deep")
    assert not useless["passed"]
    assert {"multiple_insights", "top_stories_not_funding_only", "exploration_ran"} <= {
        name for name, r in useless["gates"].items() if not r["passed"]}
    good = run_quality.gates({"exploration_after_empty_generator": 2},
                             {"developments_to_watch": [{"title": "FTC", "event_ids": ["E2"]}],
                              "hidden_insights": [{"insight": "a"}, {"insight": "b"}]}, governance, [], depth="deep")
    assert good["passed"], good["gates"]


def test_canary_the_breach_delay_is_computed_from_the_saved_article():
    """The owner's good insight was the three-month delay. The code must compute it from the saved record:
    evidence records now carry their article's publication date, and relative dates ("earlier this month")
    and timestamps ("2026-09-24T11:52:32") are read."""
    from services import delta_extractor
    from services.research_orchestrator_service import _attach_publication_dates
    if not DB.exists():
        pytest.skip("saved corpus not present")
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = c.execute("select e.source_id, e.snippets, a.published_at from events e join articles a "
                    "on a.source_id = e.source_id where e.snippets like '%breach happening in June%' limit 1").fetchone()
    assert row, "breach record missing from the saved corpus"
    import json as _json
    sentence = next(s for s in _json.loads(row[1]) if "breach happening in June" in s)
    records = [{"evidence_id": "EVID-B", "source_id": row[0], "text": sentence}]
    _attach_publication_dates(records, [{"source_id": row[0], "source_metadata": {"published_at": row[2]}}])
    assert records[0]["source_published_at"].startswith("2026-09")
    gaps = delta_extractor.compute([{"fact_id": "EVID-B", "development": "breach", "text": sentence,
                                     "date": records[0]["source_published_at"]}])
    assert gaps and gaps[0]["days"] >= 80 and "3 months" in gaps[0]["text"], gaps


def test_quality_check_reads_the_generators_citations():
    """Simulated run (19 Nov 2026): the quality check read only evidence_ids, the generator writes
    supporting_evidence_ids, and an insight citing 10 records was marked 'missing support'."""
    from services.insight_quality_service import InsightQualityService
    evidence = [{"evidence_id": f"EVID-{i}", "source_id": f"S{i}", "event_id": f"E{i}",
                 "text": f"OpenAI notified the Australian government in September about a June breach ({i})."}
                for i in range(3)]
    result = InsightQualityService().assess(
        insight={"insight_id": "I1", "insight": "Governments learned of the breach three months late.",
                 "supporting_evidence_ids": ["EVID-0", "EVID-1"]},
        evidence=evidence, grounding=None, constraints=[], blueprint={})
    import json as _json
    assert "missing_support" not in _json.dumps(result, default=str)


def test_number_records_do_not_repeat_the_unit_and_show_their_sentence():
    import inspect
    from agents import evidence_builder_agent, strategic_inference_agent
    assert "endswith(unit.casefold().strip())" in inspect.getsource(evidence_builder_agent)
    source = inspect.getsource(strategic_inference_agent.StrategicInferenceAgent._compact_evidence_for_synthesis)
    assert '" - source: "' in source or "- source:" in source


def test_a_watch_for_line_does_not_turn_a_finding_into_a_forecast():
    """Simulated run (19 Nov 2026): the breach insight was blocked as a forecast lacking feasibility
    research, because the writing standard's "Watch for" line made it 'prospective'."""
    from services.insight_quality_service import _forecasts_outside_signposts
    assert not _forecasts_outside_signposts(
        "Australia's government learned of the breach about three months late. OpenAI found it in its own "
        "review. Watch for a fixed reporting deadline; without one, responses will start months behind.")
    assert _forecasts_outside_signposts("The Commission will likely fine OpenAI by 2027. Watch for the first fine.")


def test_any_topic_funding_is_penalised_only_when_the_topic_is_not_about_funding():
    """The app serves any topic (owner, 19 Nov 2026): for a funding topic, funding rounds are the story."""
    round_ = [{"event_id": "E1", "title": "Acme raises $50M Series B led by Big Ventures",
               "summary": "Acme raised $50 million in a Series B round led by Big Ventures at a $1B valuation."}]
    assert "funding_only" in insight_potential.score(round_, topic="AI governance and regulation")["parts"]
    assert "funding_only" not in insight_potential.score(round_, topic="startup funding in India")["parts"]
    assert "funding_only" not in insight_potential.score(round_, topic="venture capital in climate tech")["parts"]


def test_cluster_searches_look_for_what_insights_are_built_from_for_any_topic():
    """Decision (19 Nov 2026): clusters group the news for ANY topic and their targeted searches look for
    the original document, the dates, the official response, comparisons and precedent - not generic
    'customers'/'competitors' coverage - and never fall back to the broad topic when the cluster has names."""
    from services import research_orchestrator_service as orchestrator
    first = " ".join(orchestrator.CLUSTER_QUERY_ANGLES[1])
    second = " ".join(orchestrator.CLUSTER_QUERY_ANGLES[2])
    assert "original document" in first and "dates" in first and "regulatory response" in first
    assert "precedent" in second and "other companies" in second
    queries = ResearchOrchestratorService._cluster_expansion_queries(
        topic="artificial intelligence",
        profile={"cluster_label": "OpenAI agent breach", "actors": ["OpenAI", "Australian Government"],
                 "terms": ["breach", "notification"], "subject": "agent breach"},
        round_number=1)
    assert queries and not any(q.strip().casefold() == "artificial intelligence" for q in queries)
