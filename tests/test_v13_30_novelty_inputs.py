"""v13.30 (research report of 22 Nov, Methods 5-6 and P2): wider consensus, meaning-based novelty, a forecast
judge from another developer. No model or network calls."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from services import outside_judge, semantic_novelty, variant_view


class _FakeVectors:
    """Cosine = shared words / all words, enough to rank paraphrases above unrelated text."""
    def similarities(self, query, documents):
        q = set(query.casefold().split())
        return [len(q & set(d.casefold().split())) / max(1, len(q | set(d.casefold().split()))) for d in documents]


def test_reference_texts_take_consensus_published_analysis_and_earlier_insights():
    view = {"claims": [{"claim": "AI firms face a patchwork of state and international rules"}],
            "analysis": [{"snippet": "Think tank: the patchwork raises compliance cost", "angle": "think_tank"}]}
    refs = semantic_novelty.reference_texts(view, ["OpenAI faces liability for agent harm"])
    assert [r["kind"] for r in refs] == ["consensus", "published:think_tank", "earlier insight"]


def test_a_restatement_is_too_close_and_the_nearest_are_named(monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_NOVELTY_REVISE_AT", "0.5")
    monkeypatch.setenv("EVENT_INTELLIGENCE_NOVELTY_BLOCK_AT", "0.95")
    refs = [{"kind": "consensus", "text": "AI firms face a patchwork of state and international rules"},
            {"kind": "earlier insight", "text": "Nvidia sells chips to China"}]
    out = semantic_novelty.check("AI firms face a patchwork of state and international rules now", refs, _FakeVectors())
    assert out["checked"] and out["too_close"] and not out["near_copy"]
    assert out["nearest"][0]["kind"] == "consensus" and "patchwork" in semantic_novelty.revision_brief(out)


def test_no_embeddings_means_not_checked_never_faked():
    assert semantic_novelty.check("x", [{"kind": "c", "text": "y"}], service=None)["checked"] is False


def test_consensus_counts_how_widely_each_claim_is_held():
    reply = {"claims": [{"claim": "one outlet only", "source": "CONS-1", "sources": ["CONS-1"]},
                        {"claim": "everyone says this", "source": "CONS-1", "sources": ["CONS-1", "CONS-2", "EVID-1"]}]}
    view = variant_view.consensus(["story"], [], ask=lambda p: reply,
                                  analysis=[{"snippet": "a", "url": "u1"}, {"snippet": "b", "url": "u2", "angle": "market"}])
    assert view["claims"][0]["claim"] == "everyone says this" and view["claims"][0]["independent_sources"] == 3
    assert "prediction market" in variant_view.consensus_prompt(["s"], [], [{"snippet": "odds", "angle": "market"}])


def test_wider_consensus_search_uses_five_angles(monkeypatch):
    from services.research_orchestrator_service import ResearchOrchestratorService as S
    queries = []

    class Helper:
        def search(self, query, max_results=4):
            queries.append(query)
            return {"results": [{"url": f"https://x/{len(queries)}", "snippet": query}]}
    service = S.__new__(S)
    service._consensus_search_helper = Helper()
    found = service._analysis_search(["California sued OpenAI - Ars Technica"])
    assert len(queries) == 5 and {r["angle"] for r in found} == {a for a, _ in S.CONSENSUS_ANGLES}
    assert any("Polymarket" in q for q in queries)


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.headers = status, body, {}

    def json(self):
        return self._body


def test_other_developer_judge_falls_back_when_a_model_is_not_served(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    outside_judge.reset()
    outside_judge._RECENT_TOKENS.clear()
    seen = []

    def post(url, headers, data, timeout):
        name = json.loads(data)["model"]
        seen.append(name)
        if name == "qwen/qwen3.8-27b":
            return _Resp(404, {"error": {"message": "model not found"}})
        return _Resp(200, {"choices": [{"message": {"content": '{"p": 1}'}}], "usage": {"total_tokens": 5}})
    assert outside_judge.ask_other_developer("x", post=post) == '{"p": 1}'
    assert seen[:2] == ["qwen/qwen3.8-27b", "qwen/qwen3-32b"]   # Llama is gone from Groq's free tier
    outside_judge.ask_other_developer("y", post=post)
    assert seen[-1] == "qwen/qwen3-32b"          # the working model is remembered
    outside_judge.reset()
    outside_judge._RECENT_TOKENS.clear()


def test_select_revises_a_restatement_against_its_nearest(monkeypatch):
    from services.research_orchestrator_service import ResearchOrchestratorService as S
    monkeypatch.setenv("EVENT_INTELLIGENCE_VARIANT_VIEW", "1")
    monkeypatch.setenv("EVENT_INTELLIGENCE_VARIANT_PERCEPTION", "1")
    monkeypatch.setenv("EVENT_INTELLIGENCE_NOVELTY_REVISE_AT", "0.5")
    monkeypatch.setattr(semantic_novelty, "_service", lambda: _FakeVectors())
    near = (datetime.now(timezone.utc) + timedelta(days=40)).isoformat()
    records = [{"evidence_id": "E1", "event_id": "V1", "text": "Texas, one of 50 states, sued OpenAI demanding an evaluator."},
               {"evidence_id": "E2", "event_id": "V1", "text": "The evaluator must approve each model before release."}]
    vp = {"consensus_quote": "", "overlooked_evidence_id": "E2", "rival_hypothesis": "r", "load_bearing_assumption": "a",
          "mechanism_chain": [{"step": "OpenAI must clear the evaluator", "evidence_ids": ["E1"],
                               "quote": "sued OpenAI demanding an evaluator"},
                              {"step": "OpenAI delays releases in Texas", "evidence_ids": ["E2"],
                               "quote": "The evaluator must approve each model before release"}]}
    restating = {"insight": "OpenAI faces a patchwork of rules across 50 states and abroad.", "variant_perception": vp,
                 "leverage_analysis": {"holder": "OpenAI"}, "forecast_contract": {"question": "q", "deadline": near}}
    service = S.__new__(S)
    service._semantic_earlier = ["OpenAI faces a patchwork of rules across 50 states and abroad"]
    seen = []

    def gemini(prompt):
        seen.append(prompt)
        return {"insight": "Texas makes OpenAI hold each model for an evaluator; 50 states could copy that gate.",
                "variant_perception": vp}
    service._ask_briefing_model = gemini
    entry = {}
    kept = service._variant_select([restating], {"claims": []}, entry, records)
    assert any("closest first" in p for p in seen)
    assert kept and kept[0]["metadata"]["semantic_novelty"]["max"] < 0.5
