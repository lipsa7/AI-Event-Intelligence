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


def test_exploration_reads_several_distinct_stories_and_merges_copies_of_one():
    """v13.6 (20 Nov 2026): exploration read at most 2 stories (so the limit of 5 never applied), and one
    story reported by several outlets appeared as several stories. Each top story now gets its own
    reading up to the limit, and copies of one story are merged (services/same_story.py) - without chaining."""
    events, evidence = _corpus()
    service = ResearchOrchestratorService.__new__(ResearchOrchestratorService)
    combos = service._development_combinations(events, evidence, (), limit=5, topic="AI governance and regulation")
    assert len(combos) >= 4, [c["labels"] for c in combos]
    by_id = {e["event_id"]: e for e in events}
    def australian_breach(text: str) -> bool:
        text = text.casefold()
        return ("albanese" in text or "australia" in text) and ("openai" in text or "breach" in text or "hack" in text)
    breach = [c for c in combos if sum(australian_breach(by_id[e]["title"] or "") for e in c["event_ids"]) >= 2]
    assert len(breach) == 1, "the breach story should be one merged story, not several"
    assert all(len(c["event_ids"]) <= 40 for c in combos), "merging must not chain into a giant group"
    # The merged story must be coherent: every event in it is about the breach (the first v13.6
    # attempt let New York data-centre permits and an OpenAI resignation into it).
    story = breach[0]["event_ids"]
    assert len(story) >= 8, len(story)
    assert all(australian_breach((by_id[e]["title"] or "") + " " + (by_id[e].get("summary") or "")) for e in story), \
        [by_id[e]["title"][:60] for e in story]


def test_same_story_ignores_outlet_names_and_bylines_and_does_not_chain():
    """v13.6: unrelated funding rounds from one outlet (shared outlet name and reporter) are not one
    story; the corpus never forms a story larger than the size guard; the breach is one story."""
    from services import same_story
    events, _ = _corpus()
    titles = [e["title"] or "" for e in events]
    stories = same_story.story_components(events)
    def story_of(prefix: str) -> set[int]:
        return {stories[i] for i, t in enumerate(titles) if t.startswith(prefix)}
    funding = story_of("AI agent developer Manus raises") | story_of("Jev creator TypeSafe closes") \
        | story_of("EliseAI raises")
    assert len(funding) == 3, "three unrelated funding rounds must stay three stories"
    sizes: dict[int, int] = {}
    for story in stories:
        sizes[story] = sizes.get(story, 0) + 1
    assert max(sizes.values()) <= 25
    breach = story_of("Australia reveals OpenAI agent") | story_of("Albanese chides US company") \
        | story_of("OpenAI agents hacked an Australian")
    assert len(breach) == 1


def test_group_spanning_two_stories_loses_no_events(monkeypatch):
    """v13.6 review (20 Nov 2026): group A shares one story with a bigger group B and another with a
    smaller group C. All three must end up in one group; before, C was copied into A after A had
    already been merged into B, so C's events were lost."""
    from services import same_story
    from services.research_orchestrator_service import ResearchOrchestratorService as S

    def event(eid, underlying):
        return {"event_id": eid, "underlying_event_id": underlying, "title": f"{eid} story", "summary": eid}
    events = [event("E1", "A"), event("E2", "A"), event("E3", "B"), event("E4", "B"), event("E5", "C")]
    evidence = [{"event_id": e, "evidence_id": f"V{e}{n}"} for e, n_ in
                (("E1", 1), ("E2", 1), ("E3", 3), ("E4", 3), ("E5", 1)) for n in range(n_)]
    # story 1 (seen first): E1 (A) with E5 (C); story 2: E2 (A) with E3 (B)
    monkeypatch.setattr(same_story, "story_components", lambda evs, max_size=None: [
        {"E1": 1, "E5": 1, "E2": 2, "E3": 2, "E4": 3}[e["event_id"]] for e in evs])
    service = S.__new__(S)
    combos = service._development_combinations(events, evidence, limit=5, topic="t")
    seen = {eid for combo in combos for eid in (combo.get("event_ids") or [])}
    assert {"E1", "E2", "E3", "E4", "E5"} <= seen, combos


def test_call_budget_keeps_calls_for_the_briefing_and_stops_optional_work_before_refusals(monkeypatch):
    """v13.7 (20 Nov 2026): the insight loop decided it could start more work until 85% of the cap,
    but the ceiling refused it once only the lab and final-check reserves were left (much earlier),
    and nothing was held for the briefing (14 Nov: 45 briefing calls refused). Now the briefing's
    calls are held, and optional work stops when this stage cannot make enough calls to finish."""
    from services import gemini_llm_service as g
    from services.research_orchestrator_service import ResearchOrchestratorService as S
    monkeypatch.setenv("EVENT_INTELLIGENCE_MAX_GEMINI_CALLS", "480")
    service = S.__new__(S)
    service._apply_call_cap("deep")
    try:
        held = service._lab_reserved_calls + service._final_check_reserved_calls + service._after_insights_reserved_calls
        assert service._after_insights_reserved_calls == 70 and held == 170
        assert g.calls_available() == 480 - held
        # Spend ordinary calls until fewer than MIN_CALLS_TO_START_OPTIONAL_WORK remain for this stage.
        for _ in range(480 - held - S.MIN_CALLS_TO_START_OPTIONAL_WORK + 1):
            g.consume_call()
        assert service._call_cap_exhausted(), "optional work must not start when its calls would be refused"
        # The briefing can still make its calls: they were held for it.
        with g.stage("briefing"):
            assert g.calls_available() >= 70
            for _ in range(70):
                g.consume_call()
        # Once the after-insight stages are done their leftover goes back to the pool.
        g.reserve_calls("after_insights", 0)
    finally:
        g.set_call_budget(None)


def test_lone_breach_sentence_joins_the_breach_story_and_nothing_unrelated_joins():
    """v13.8 (20 Nov 2026): a one-sentence report ("Despite the breach happening in June, Albanese
    said...") was too short to match and took an exploration slot of its own (about 15 calls).
    It now joins the breach story; on the saved corpus nothing else is attached."""
    from services import same_story
    events, evidence = _corpus()
    stories = same_story.story_components(events)
    titles = [e["title"] or "" for e in events]
    def story_of(prefix: str) -> int:
        return next(stories[i] for i, t in enumerate(titles) if t.startswith(prefix))
    breach = story_of("Australia reveals OpenAI agent")
    assert story_of("Despite the breach happening in June, Albanese") == breach
    original = same_story._lone_reports_to_attach
    attached: dict[int, int] = {}
    def spy(evs, story_of_list):
        found = original(evs, story_of_list)
        attached.update(found)
        return found
    same_story._lone_reports_to_attach = spy
    try:
        same_story.story_components(events)
    finally:
        same_story._lone_reports_to_attach = original
    joined = [titles[i] for i in attached]
    assert all("Albanese" in t or "Australia" in t for t in joined), joined
    assert len(joined) <= 3, joined
    # Exploration no longer spends a slot on the breach twice.
    service = ResearchOrchestratorService.__new__(ResearchOrchestratorService)
    combos = service._development_combinations(events, evidence, (), limit=5, topic="AI governance and regulation")
    by_id = {e["event_id"]: e for e in events}
    breach_slots = [c for c in combos if any("Albanese" in ((by_id[e]["title"] or "") + (by_id[e].get("summary") or ""))
                                             for e in c["event_ids"])]
    assert len(breach_slots) == 1, [c["labels"] for c in breach_slots]


def _deep_cap(monkeypatch, cap):
    from services.research_orchestrator_service import ResearchOrchestratorService as S
    monkeypatch.setenv("EVENT_INTELLIGENCE_MAX_GEMINI_CALLS", str(cap))
    for name in ("EVENT_INTELLIGENCE_FINAL_CHECK_RESERVED_CALLS", "EVENT_INTELLIGENCE_LAB_RESERVED_CALLS",
                 "EVENT_INTELLIGENCE_AFTER_INSIGHTS_RESERVED_CALLS", "EVENT_INTELLIGENCE_MIN_CALLS_OPTIONAL_WORK"):
        monkeypatch.delenv(name, raising=False)
    service = S.__new__(S)
    service.execution_budget_service = None
    service._apply_call_cap("deep")
    return service


def test_call_budget_at_480_keeps_every_reserve_and_stops_optional_work_in_time(monkeypatch):
    """v13.8 review: the whole call budget of a deep run at the recommended cap. Main path gets
    480 - 40 (final checks) - 60 (lab) - 70 (briefing etc.) = 310; optional work stops when fewer than
    15 of those remain; every reserved stage still gets all of its calls afterwards."""
    import pytest
    from services import gemini_llm_service as g
    service = _deep_cap(monkeypatch, 480)
    try:
        assert (service._final_check_reserved_calls, service._lab_reserved_calls,
                service._after_insights_reserved_calls) == (40, 60, 70)
        with g.stage("hidden_insights"):
            assert g.calls_available() == 310
            for _ in range(295):
                g.consume_call()
            assert not service._call_cap_exhausted()          # 15 left: one more story may start
            g.consume_call()
            assert service._call_cap_exhausted()              # 14 left: no new story is started
            for _ in range(14):
                g.consume_call()
            with pytest.raises(g.GeminiCallBudgetExceeded):
                g.consume_call()                              # the reserves are never touched
        for stage_name, held in (("briefing", 70), ("hypothesis_lab", 60), ("final_checks", 40)):
            with g.stage(stage_name):
                assert not service._call_cap_exhausted()
                for _ in range(held):
                    g.consume_call()
        assert g.call_budget()["used"] == 480
    finally:
        g.set_call_budget(None)


def test_focus_research_decides_inside_its_own_path(monkeypatch):
    """v13.8 review: the early focus research is one of the after-insight stages; its go/no-go check
    must see the calls held for it (before, it ran outside its path and was skipped)."""
    from services import gemini_llm_service as g
    from services.research_orchestrator_service import ResearchOrchestratorService as S
    service = _deep_cap(monkeypatch, 480)
    seen = {}
    try:
        with g.stage("hidden_insights"):
            for _ in range(310):
                g.consume_call()                              # main path fully used
        original = S.__dict__["_focus_research_before_briefing"]
        assert getattr(original, "_stage_path", "") == "focus_research"
        monkeypatch.setattr(service, "_choose_focus", lambda *a, **k: {"name": "OpenAI"})

        def decide():
            seen["available"] = g.calls_available()
            return 0.0                                        # stop right after the decision
        monkeypatch.setattr(service, "_remaining_discretionary_seconds", decide)
        state = {}
        original(service, [], state)
        assert state["focus_research_early"]["reason"] == "time_budget"
        assert seen.get("available") == 70
    finally:
        g.set_call_budget(None)


def test_no_after_insight_reserve_below_480(monkeypatch):
    service = _deep_cap(monkeypatch, 260)
    from services import gemini_llm_service as g
    try:
        assert service._after_insights_reserved_calls == 0
        with g.stage("hidden_insights"):
            assert g.calls_available() == 160                 # 260 - 40 - 60: insights first
    finally:
        g.set_call_budget(None)


def test_follow_up_searches_go_to_distinct_stories():
    """v13.9 (20 Nov 2026): copies of one story in different clusters could take several of the
    three follow-up search slots. On the saved corpus (one cluster per event) the top eligible
    clusters held the FTC story twice and the breach twice; now each story gets one slot."""
    events, evidence = _corpus()
    events = [dict(e, strategic_cluster_id="c_" + e["event_id"][-8:]) for e in events]
    S = ResearchOrchestratorService
    eligible = [p for p in S._strategic_cluster_expansion_profiles(topic="artificial intelligence", events=events,
                                                                    evidence=evidence) if p.get("eligible")]
    from services import same_story
    stories = same_story.story_components(events)
    story_of = {e["event_id"].upper(): stories[i] for i, e in enumerate(events)}
    def story_set(profiles):
        return [frozenset(story_of[e] for e in p["event_ids"]) for p in profiles]
    old = story_set(eligible[:8])
    assert len(set(old)) < len(old), "the corpus should contain duplicate stories among the top clusters"
    chosen = story_set(S._distinct_story_targets(eligible, events, 8))
    assert len(chosen) == 8 and len(set(chosen)) == 8


def test_backup_search_template_uses_insight_angles_in_every_round():
    """v13.9: when the model cannot write queries, rounds 3+ of the template searched generic
    business angles ("launched", "partnership", "investment", "expands")."""
    profile = {"actors": ["OpenAI"], "cluster_terms": ["agent breach", "Medicare"], "cluster_label": "breach"}
    generic = {"launched", "partnership", "investment", "expands", "results", "announced"}
    for round_number in range(1, 8):
        queries = ResearchOrchestratorService._cluster_expansion_queries(
            topic="artificial intelligence", profile=profile, round_number=round_number)
        words = {w.casefold() for q in queries for w in q.replace('"', " ").split()}
        assert not (words & generic), (round_number, queries)


def test_funding_announcement_saying_disclosed_is_not_an_incident():
    """v13.9: "Arceus Legal today disclosed that it has raised $17M" counted as incident language,
    so a funding story escaped the funding-only penalty. A breach disclosure still counts."""
    from services import insight_potential
    funding = insight_potential.score([{"title": "Arceus Legal raises $17M", "summary":
        "Arceus Legal today disclosed that it has raised $17 million from investors in a funding round."}],
        topic="artificial intelligence")
    assert "funding_only" in funding["parts"] and "incident_or_enforcement" not in funding["parts"]
    breach = insight_potential.score([{"title": "OpenAI disclosed its agents escaped containment",
                                       "summary": "OpenAI disclosed in July that two models escaped a sandbox."}])
    assert "incident_or_enforcement" in breach["parts"]
