"""v13.26 (22 Nov 2026): one short generation call per lens (research report of 22 Nov, stage D).
No model calls: the generator's model call is replaced by a fake."""
from agents.strategic_inference_agent import StrategicInferenceAgent
from services import variant_view

RECORDS = [
    {"id": "EVID-1", "evidence_id": "EVID-1", "event_id": "EVENT-1", "published_at": "2026-11-16",
     "text": "California sued OpenAI on 16 November 2026 seeking $2,500 per violation."},
    {"id": "EVID-2", "evidence_id": "EVID-2", "event_id": "EVENT-2", "published_at": "2026-10-20",
     "text": "OpenAI disclosed the July breach in October."},
]
LENSES = [{"name": n, "instruction": variant_view.lens_instruction(
    {"claims": [{"claim": "Readers expect a long court fight.", "source": "EVID-1"}]}, n)}
    for n, _ in variant_view.LENSES]


def _generate(calls, fail_lens="", lenses=LENSES):
    agent = StrategicInferenceAgent.__new__(StrategicInferenceAgent)
    agent._strategic_model_used_this_run = ""

    def call_json(**kw):
        calls.append(kw)
        if fail_lens and f"LENS: {fail_lens} " in kw["prompt"]:
            raise RuntimeError("model down")
        return {"candidate_insights": [{"insight": "x " + kw["metadata"].get("lens", "single"),
                                        "analytical_lenses": ["other"]}, {"insight": "second, ignored"}]}
    agent._call_json = call_json
    return agent._generate_synthesis(
        research_question="artificial intelligence", evidence_records=RECORDS, evidence_review={},
        auxiliary_context={}, reasoning_guidance={"desired_depth": "deep"},
        normalized_refinement={"instruction": "story"}, previous_report={}, event_ids=["EVENT-1", "EVENT-2"],
        constraint_ids=[], active_budget=None, execution_stage="refinement", lenses=lenses)


def test_one_short_call_per_lens_with_the_same_output_schema():
    calls = []
    out = _generate(calls)
    assert [c["metadata"]["lens"] for c in calls] == [n for n, _ in variant_view.LENSES]
    for call, (name, _) in zip(calls, variant_view.LENSES):
        assert f"LENS: {name} " in call["prompt"]
        assert "Return exactly this top-level structure:" in call["prompt"]      # same schema as before
        assert "FINAL QUALITY QUESTIONS" not in call["prompt"]                   # the long rule block is gone
        assert "CONSENSUS[1] (EVID-1): Readers expect a long court fight." in call["prompt"]
    assert [c["insight"] for c in out["candidate_insights"]] == ["x " + n for n, _ in variant_view.LENSES]
    assert out["candidate_insights"][0]["analytical_lenses"] == ["constraints", "other"]


def test_lens_prompts_are_much_shorter_than_the_single_prompt():
    single, lensed = [], []
    _generate(single, lenses=None)
    _generate(lensed)
    assert max(len(c["prompt"]) for c in lensed) < len(single[0]["prompt"]) / 2
    for heading in ("NON-NEGOTIABLE EVIDENCE BOUNDARY", "SUPPORT-CLUSTER CONTRACT", "EVIDENCE-LINKAGE CONTRACT"):
        assert heading in lensed[0]["prompt"]          # the mechanically checked contracts are kept


def test_a_failing_lens_is_skipped_and_the_others_still_run():
    calls = []
    out = _generate(calls, fail_lens="value_chain")
    assert len(calls) == 4 and len(out["candidate_insights"]) == 3
    assert [c["result"] for c in out["_lens_calls"]] == ["candidate", "failed:RuntimeError", "candidate", "candidate"]


def test_orchestrator_uses_lenses_only_when_the_variant_stages_are_on(monkeypatch):
    from services.research_orchestrator_service import ResearchOrchestratorService as S
    service = S.__new__(S)
    monkeypatch.setenv("EVENT_INTELLIGENCE_VARIANT_VIEW", "1")
    lenses = service._generation_lenses({"claims": []})
    assert [l["name"] for l in lenses] == [n for n, _ in variant_view.LENSES]
    assert "a week to a" in lenses[0]["instruction"] and "CONSENSUS" not in lenses[0]["instruction"]
    monkeypatch.setenv("EVENT_INTELLIGENCE_LENS_GENERATION", "0")
    assert service._generation_lenses({"claims": []}) == []
    monkeypatch.setenv("EVENT_INTELLIGENCE_LENS_GENERATION", "1")
    monkeypatch.setenv("EVENT_INTELLIGENCE_VARIANT_VIEW", "0")
    assert service._generation_lenses({"claims": []}) == []


def test_the_generator_accepts_the_lenses_the_exploration_slot_passes():
    """v13.27 shipped the exploration side of v13.26 without the generator side: every exploration call
    passed lenses= to an analyze() that did not accept it (TypeError, caught as failed:TypeError), and the
    lab is retired whenever lenses are on - so a default run explored nothing. Guard both sides together."""
    import inspect
    from services.research_orchestrator_service import ResearchOrchestratorService as S
    assert "lenses" in inspect.signature(StrategicInferenceAgent.analyze).parameters
    assert "lenses" in inspect.signature(StrategicInferenceAgent._generate_synthesis).parameters
    assert '"lenses": lenses' in inspect.getsource(S._exploration_candidates)
