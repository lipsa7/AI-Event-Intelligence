"""v6.24: fixes from replay RUN-FB8AB22DBDD548B5BB6D4898 (first run on v6.23).

1. Search allowance spent mid-run. Google-grounded search was capped at 12
   prompts for gap research and 6 for challenge searches. With 6 gap searches
   per development plus follow-ups, the third development got no searches,
   the expert search for the second never ran, and the challenge search for a
   strong candidate ("renaming all AI 'Super Intelligence' collides with the
   Ban Artificial Superintelligence Act") came back DISABLED, so it failed
   closed. Caps: 30 and 24; a spent budget is logged and reported.
2. Three of ten gap-research slots for one development went to YouTube videos.
   Video and social pages are dropped before fetching.
3. The pattern grid reported "Trump is reported acting on 11 different targets:
   the notion; delegates; the lunch ...". A target must be a named party and a
   repeat needs the same action.
"""

from __future__ import annotations

import logging

import services.research_orchestrator_service as ros
from services import grounded_search as gs
from services import pattern_grid as grid


# 1 search allowance ---------------------------------------------------------

def test_caps_cover_the_searches_a_run_makes():
    from services.hypothesis_evidence_service import HypothesisEvidenceService as H
    gap_prompts = ros.ResearchOrchestratorService.PRECEDENT_DEVELOPMENTS * (H.GAP_MAX_QUERIES + 1)  # + follow-up
    assert gs.GeminiGroundedSearch(llm=None).max_calls >= gap_prompts
    assert ros.ResearchOrchestratorService.CHALLENGE_GROUNDED_PROMPTS >= ros.DEEP_FINAL_CLAIM_RESEARCH_MAX * 6


def test_spent_budget_is_logged_once_and_counted(caplog):
    search = gs.GeminiGroundedSearch(llm=None, max_calls=0)
    with caplog.at_level(logging.WARNING):
        out1 = search.search("first query")
        out2 = search.search("second query")
    assert out1["provider_stats"]["gemini_search"]["status"] == "disabled" and out2["results"] == []
    assert search.exhausted_queries == ["first query", "second query"]
    assert sum("grounded_search_budget_exhausted" in r.getMessage() for r in caplog.records) == 1


def test_run_log_reports_the_spent_budget():
    import inspect
    source = inspect.getsource(ros.ResearchOrchestratorService)
    assert source.count('"unsearched_budget_spent": len(getattr(') == 2


# 2 non-article results ------------------------------------------------------

def test_video_and_social_results_are_dropped():
    assert not gs.is_article_source({"url": "https://www.youtube.com/watch?v=fbSY5JeDCLI", "title": "youtube.com"})
    assert not gs.is_article_source({"url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/X",
                                     "title": "youtube.com"})        # grounded: the site is in the title
    assert not gs.is_article_source({"url": "https://m.facebook.com/post", "title": ""})
    assert gs.is_article_source({"url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/Y",
                                 "title": "insurancebusinessmag.com"})
    assert gs.is_article_source({"url": "https://www.xbox.com/news", "title": ""})   # not x.com


def test_grounded_results_skip_videos_before_the_result_limit():
    class Response:
        candidates = [{"grounding_metadata": {"grounding_chunks": [
            {"web": {"uri": "https://r/1", "title": "youtube.com"}},
            {"web": {"uri": "https://r/2", "title": "youtube.com"}},
            {"web": {"uri": "https://r/3", "title": "theguardian.com"}},
            {"web": {"uri": "https://r/4", "title": "reuters.com"}}]}}]

    class Models:
        def generate_content(self, **kwargs):
            return Response()

    class Types:
        class Tool:
            def __init__(self, **kwargs):
                pass

        class GoogleSearch:
            pass

        class GenerateContentConfig:
            def __init__(self, **kwargs):
                pass

    class LLM:
        _client = type("C", (), {"models": Models()})()
        _types = Types

    search = gs.GeminiGroundedSearch(LLM(), max_calls=2, model_name="gemini-3.5-flash-lite")
    out = search.search("q", max_results=2)
    assert [r["title"] for r in out["results"]] == ["theguardian.com", "reuters.com"]


# 3 pattern grid ---------------------------------------------------------------

# The replay's junk, reduced: one actor, many verbs, unnamed "targets".
JUNK = [
    {"evidence_id": "J1", "actor": "Trump", "action": "dismissed", "target": "the notion"},
    {"evidence_id": "J2", "actor": "Trump", "action": "told", "target": "delegates"},
    {"evidence_id": "J3", "actor": "Trump", "action": "addressed", "target": "the UN General Assembly"},
    {"evidence_id": "J4", "actor": "Trump", "action": "called", "target": 'the lunch "very productive"'},
    {"evidence_id": "J5", "actor": "Trump", "action": "said", "target": "that the US government would rename"},
]


def test_unnamed_targets_and_different_actions_make_no_pattern():
    assert [p for p in grid.find_patterns(JUNK) if p["kind"] == "repeat_actor"] == []


def test_named_parties():
    assert grid.is_named_party("Services Australia") and grid.is_named_party("NSW DCCEEW")
    assert grid.is_named_party("the UN General Assembly") and grid.is_named_party("AIHW")
    for junk in ("the notion", "delegates", "that the US government would rename", 'the lunch "very productive"'):
        assert not grid.is_named_party(junk), junk


def test_same_action_across_named_parties_is_a_pattern():
    rows = [{"evidence_id": f"R{i}", "actor": "OpenAI agent", "action": action, "target": target}
            for i, (action, target) in enumerate([("accessed", "Services Australia"), ("also accessed", "AIHW"),
                                                  ("accessed", "NSW DCCEEW"), ("praised", "Sam Altman")])]
    repeat = [p for p in grid.find_patterns(rows) if p["kind"] == "repeat_actor"]
    assert len(repeat) == 1 and repeat[0]["n"] == 3 and repeat[0]["evidence_ids"] == ["R0", "R1", "R2"]
    assert repeat[0]["text"].startswith("Across 3 reports, OpenAI agent accessed 3 different named parties:")
