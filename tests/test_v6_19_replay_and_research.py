"""v6.19: checkpoint + replay, gap research before analysis, Google-backed
challenge research, undated policy pages, and the setup check."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from services import research_orchestrator_service as module
from services.research_orchestrator_service import ResearchOrchestratorService as R


EVENTS = [
    {"event_id": "EVENT-1", "underlying_event_id": "U1", "strategic_cluster_id": "dev",
     "strategic_cluster_status": "verified", "strategic_cluster_development": "agent kill switches",
     "primary_actor": "Okta", "event_start_at": "2026-09-29", "summary": "Okta adds kill switch"},
    {"event_id": "EVENT-2", "underlying_event_id": "U2", "strategic_cluster_id": "dev",
     "strategic_cluster_status": "verified", "strategic_cluster_development": "agent kill switches",
     "primary_actor": "Equals Money", "event_start_at": "2026-10-01", "summary": "Equals Money limits MCP"},
]
EVIDENCE = [
    {"evidence_id": "EVID-1", "event_id": "EVENT-1", "source_id": "SOURCE-1", "evidence_type": "source_excerpt",
     "claim": "Okta added a kill switch that stops an AI agent's access in real time."},
    {"evidence_id": "EVID-2", "event_id": "EVENT-2", "source_id": "SOURCE-2", "evidence_type": "source_excerpt",
     "claim": "Equals Money limits its MCP server so AI agents can read data but not make payments."},
]


class Stop(Exception):
    pass


@pytest.fixture(autouse=True)
def _private_ledgers(tmp_path, monkeypatch):
    """v6.20.5: tests never write to the real forecast ledger or event memory."""
    monkeypatch.setenv("EVENT_INTELLIGENCE_WATCH_ITEMS", str(tmp_path / "watch_items.sqlite3"))
    monkeypatch.setenv("EVENT_INTELLIGENCE_EVENT_MEMORY", str(tmp_path / "event_memory.sqlite3"))


def bare_orchestrator():
    instance = object.__new__(R)
    instance._compile_blueprint = lambda **k: {"blueprint_id": "BP"}
    instance._bind_blueprint_to_profile = lambda blueprint, profile: blueprint
    instance._emit_telemetry = lambda state: None
    instance.execution_budget_service = None
    instance.web_search_service = None
    instance.extraction_agent = None
    instance.article_service = None

    def never(*args, **kwargs):
        raise AssertionError("a paid collection stage ran during replay")

    instance._discover_sources = never
    instance._extract_events = never
    instance._build_evidence_with_authoritative_analysis = never
    instance._refill_strategic_corpus = never
    instance._expand_promising_strategic_clusters = never
    return instance


def save(instance, tmp_path, monkeypatch, *, evidence):
    monkeypatch.setenv("EVENT_INTELLIGENCE_CHECKPOINT_DIR", str(tmp_path))
    state = {"depth_integrity": {"extraction": "deep"}, "strategic_event_admission": {"admitted": 2},
             "run_dedup": {"processed_source_ids": ["SOURCE-1"]}, "warnings": [], "blueprint": {"x": 1}}
    path = instance._save_checkpoint(
        run_id="RUN-ORIGINAL", topic="artificial intelligence", depth="deep", state=state,
        selected_source_ids=["SOURCE-1", "SOURCE-2"], event_records=EVENTS, evidence_records=evidence,
        admitted_event_records=EVENTS, admitted_evidence_records=evidence, evidence_payload={"evidence_records": evidence})
    return path, state


def test_checkpoint_round_trip(tmp_path, monkeypatch):
    instance = bare_orchestrator()
    path, saved_state = save(instance, tmp_path, monkeypatch, evidence=EVIDENCE)
    assert path.endswith("RUN-ORIGINAL.json") and saved_state["checkpoint_path"] == path
    data = json.loads(open(path, encoding="utf-8").read())
    assert "blueprint" not in data["state"] and data["extraction_depth"] == "deep"
    checkpoint = instance._load_checkpoint("RUN-ORIGINAL")
    state = {"stage_status": {}, "depth_integrity": {}}
    sources, events, evidence, admitted_events, admitted_evidence, payload = instance._restore_checkpoint(checkpoint, state)
    assert sources == ["SOURCE-1", "SOURCE-2"] and [e["event_id"] for e in events] == ["EVENT-1", "EVENT-2"]
    assert len(evidence) == 2 and payload["evidence_records"][0]["evidence_id"] == "EVID-1"
    assert state["depth_integrity"]["extraction"] == "deep"
    assert state["strategic_event_admission"] == {"admitted": 2}
    assert state["replayed_from"]["run_id"] == "RUN-ORIGINAL"


def test_only_the_latest_checkpoints_are_kept(tmp_path, monkeypatch):
    import os
    import time
    instance = bare_orchestrator()
    monkeypatch.setenv("EVENT_INTELLIGENCE_CHECKPOINT_DIR", str(tmp_path))
    for index in range(R.CHECKPOINT_KEEP + 2):
        instance._save_checkpoint(run_id=f"RUN-{index}", topic="t", depth="deep", state={},
                                  selected_source_ids=[], event_records=[], evidence_records=[],
                                  admitted_event_records=[], admitted_evidence_records=[], evidence_payload={})
    assert len(list(tmp_path.glob("RUN-*.json"))) == R.CHECKPOINT_KEEP


def test_unknown_checkpoint_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_CHECKPOINT_DIR", str(tmp_path))
    with pytest.raises(module.ResearchConfigurationError):
        bare_orchestrator()._load_checkpoint("RUN-MISSING")


def test_replay_skips_collection_and_feeds_saved_corpus_to_analysis(tmp_path, monkeypatch):
    instance = bare_orchestrator()
    path, _ = save(instance, tmp_path, monkeypatch, evidence=EVIDENCE)
    seen = {}
    instance._build_relationships = lambda **k: {"relationship_count": 0}
    instance._rebuild_graph = lambda **k: {}

    def gap(*, events, evidence, state):
        seen["gap_events"] = [e["event_id"] for e in events]
        return list(evidence) + [{"evidence_id": "EVID-RULE", "source_id": "SOURCE-RULE", "gap_type": "policy"}]

    instance._pre_analysis_gap_research = gap

    def analyze(**kwargs):
        seen["analysis_events"] = [e["event_id"] for e in kwargs["current_events"]]
        seen["analysis_evidence"] = [e["evidence_id"] for e in kwargs["evidence"]]
        raise Stop()

    instance.strategic_inference_agent = NS(analyze=analyze)
    with pytest.raises(Stop):
        instance.run("artificial intelligence", research_depth="deep", replay_checkpoint=path)
    assert seen["gap_events"] == ["EVENT-1", "EVENT-2"]
    assert seen["analysis_events"] == ["EVENT-1", "EVENT-2"]
    assert seen["analysis_evidence"] == ["EVID-1", "EVID-2", "EVID-RULE"]   # gap context reaches analysis


def test_replay_with_empty_corpus_abstains_without_collecting(tmp_path, monkeypatch):
    instance = bare_orchestrator()
    path, _ = save(instance, tmp_path, monkeypatch, evidence=[])
    captured = {}
    instance._complete_abstention = lambda **kwargs: captured.update(kwargs) or {"status": "abstained"}
    result = instance.run("artificial intelligence", research_depth="deep", replay_checkpoint=path)
    assert result == {"status": "abstained"}
    assert [e["event_id"] for e in captured["events"]] == ["EVENT-1", "EVENT-2"]
    assert captured["state"]["replayed_from"]["run_id"] == "RUN-ORIGINAL"


# ----------------------------------------------------------------------
# Gap research before analysis
# ----------------------------------------------------------------------

def test_pre_analysis_gap_research_adds_context_and_marks_done():
    instance = bare_orchestrator()

    def research(developments, state):
        state["gap_context_records"] = [{"evidence_id": "EVID-RULE", "source_id": "SOURCE-RULE", "gap_type": "policy",
                                         "source_excerpt": "The rule remains in effect."}]
        state["historical_context"] = {"records": [{"evidence_id": "EVID-H", "historical_case": {"year": 2014}}]}
        return [{"development": "agent kill switches", "status": "complete",
                 "records": state["gap_context_records"] + state["historical_context"]["records"]}]

    instance._precedent_research = research
    instance._evidence_manifest = lambda evidence: {"count": len(evidence)}
    state = {"stage_status": {}}
    out = instance._pre_analysis_gap_research(events=EVENTS, evidence=EVIDENCE, state=state)
    assert [e["evidence_id"] for e in out] == ["EVID-1", "EVID-2", "EVID-RULE"]
    assert state["gap_research_done"] is True and state["stage_status"]["gap_research"]["context_records"] == 1
    assert state["gap_research_summary"][0]["case_count"] == 1


def test_gap_research_is_not_repeated_at_report_time():
    instance = bare_orchestrator()
    instance._precedent_research = lambda *a, **k: (_ for _ in ()).throw(AssertionError("searched twice"))
    instance._editorial_briefing = lambda **k: {"mode": "checked_analysis", "paragraphs": [], "implications": []}
    report = {"research_depth": "deep", "developments_to_watch": []}
    state = {"gap_research_done": True, "gap_research_summary": [{"development": "d", "case_count": 1}]}
    instance._attach_spec_sections(report=report, topic="t", events=[], evidence=[], state=state)
    assert report["analysis_metadata"]["precedent_research"] == [{"development": "d", "case_count": 1}]


def test_pre_analysis_gap_research_failure_keeps_the_original_evidence():
    instance = bare_orchestrator()
    instance._precedent_research = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    state = {"stage_status": {}, "warnings": []}
    out = instance._pre_analysis_gap_research(events=EVENTS, evidence=EVIDENCE, state=state)
    assert [e["evidence_id"] for e in out] == ["EVID-1", "EVID-2"]
    assert state["warnings"][0]["type"] == "gap_research_failed"


# ----------------------------------------------------------------------
# Challenge research uses Google with its own budget
# ----------------------------------------------------------------------

def test_challenge_research_gets_combined_search_with_its_own_budget():
    from services.grounded_search import CombinedSearch
    instance = bare_orchestrator()
    instance.extraction_agent = NS(llm=NS(_client=object(), _types=NS(Tool=1, GoogleSearch=1), model_name="m"))
    instance.web_search_service = NS(search=lambda q, **k: {"results": []})
    instance.source_fetch_service = None
    instance._challenge_search_cache = None
    search = instance._challenge_search()
    assert isinstance(search, CombinedSearch) and search.grounded.max_calls == R.CHALLENGE_GROUNDED_PROMPTS
    assert instance._challenge_search() is search                         # one budget per run
    service = instance._hypothesis_evidence_service()
    assert service.search is search
    gap = instance._grounded_search({"run_id": "RUN-X"})
    assert gap.grounded is not search.grounded                             # separate budgets


# ----------------------------------------------------------------------
# Undated policy pages
# ----------------------------------------------------------------------

def _review_service(reply):
    from services.hypothesis_evidence_service import HypothesisEvidenceService

    class Model:
        model_name = "m"
        timeout_seconds = 30

        def ask(self, prompt, **kwargs):
            reply["prompt"] = prompt
            return json.dumps(reply["data"])

    return HypothesisEvidenceService(web_search_service=None, source_fetch_service=None, llm_service=Model())


BODY = ("Payments Regulation 2023/11. Automated systems that initiate payments must be registered. "
        "This regulation has been in force since 1 March 2024 and continues to apply. " + "Detail. " * 200)
RULE = "Automated systems that initiate payments must be registered."
SOURCE = {"canonical_url": "https://regulator.example/rule", "text_content": BODY,
          "metadata": {"content_was_truncated": False}}
CONTEXT = {"query": "automated payment rules", "search_result": {"title": "Rule"}, "to_date": "2026-10-09T00:00:00+00:00"}
NOW = __import__("datetime").datetime(2026, 10, 9, tzinfo=__import__("datetime").timezone.utc)


def test_undated_policy_page_is_accepted_with_a_quoted_in_force_statement():
    reply = {"data": {"review_complete": True, "records": [{
        "quote": RULE, "relation": "context", "claim_kind": "observation", "connection": "rule",
        "historical_case": None, "in_force_quote": "This regulation has been in force since 1 March 2024"}]}}
    out = _review_service(reply)._process_source(source=SOURCE, context=CONTEXT, candidate={"insight_id": "X"},
                                                 now=NOW, timeout=30, allow_undated_in_force=True)
    assert out["status"] == "complete" and out["accepted_evidence"][0]["publication_date_basis"] == "in_force_statement"
    assert "in_force_quote" in reply["prompt"]


def test_undated_policy_record_without_in_force_statement_is_rejected():
    reply = {"data": {"review_complete": True, "records": [{
        "quote": RULE, "relation": "context", "claim_kind": "observation", "connection": "rule",
        "historical_case": None, "in_force_quote": "made up statement 2024"}]}}
    out = _review_service(reply)._process_source(source=SOURCE, context=CONTEXT, candidate={"insight_id": "X"},
                                                 now=NOW, timeout=30, allow_undated_in_force=True)
    assert out["accepted_evidence"] == [] and out["rejected_candidates"][0]["reason"] == "undated_policy_without_in_force_statement"


def test_undated_page_is_still_rejected_outside_policy_research():
    reply = {"data": {"review_complete": True, "records": []}}
    with pytest.raises(ValueError):
        _review_service(reply)._process_source(source=SOURCE, context=CONTEXT, candidate={"insight_id": "X"},
                                               now=NOW, timeout=30)


# ----------------------------------------------------------------------
# Scripts
# ----------------------------------------------------------------------

def test_scripts_compile():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for name in ("check_setup.py", "replay_run.py"):
        compile((root / name).read_text(encoding="utf-8"), name, "exec")


def test_every_implication_can_state_its_key_assumption_and_it_is_checked():
    from services import editorial_briefing as brief
    facts = [{"fact_id": "F1", "development": "d", "text": "Okta added a kill switch for AI agents.", "date": "",
              "evidence_id": "EVID-1", "source_id": "SOURCE-1"}]
    draft = brief.parse_draft({"headline": "", "paragraphs": [], "analogies": [], "implications": [
        {"basis": "trend", "text": "More identity vendors may add agent controls.", "fact_ids": ["F1"],
         "conditions": "enterprises keep deploying agents with payment access"}]}, facts, [])
    claims = brief.grounding_claims(draft, facts, [])
    condition = next(c for c in claims if c["_target"][0] == "implication_conditions")
    statuses = {c["claim_id"]: "inference_supported" for c in claims}
    assert brief.sanitize(draft, claims, statuses, facts, [])["implications"][0]["conditions"]
    statuses[condition["claim_id"]] = "unsupported"
    # v6.21.1 (replaces v6.20.1): a "could be wrong if" condition is a hypothetical, which no source can
    # state; when it is not backed it stays, labelled as an assumption. A contradicted one removes it.
    kept = brief.sanitize(draft, claims, statuses, facts, [])["implications"]
    assert kept and kept[0]["conditions"] and kept[0]["assumption_fields"] == ["conditions"]
    statuses[condition["claim_id"]] = "contradicted"
    assert brief.sanitize(draft, claims, statuses, facts, [])["implications"] == []
    missing = brief.parse_draft({"headline": "", "paragraphs": [], "analogies": [], "implications": [
        {"basis": "trend", "text": "More identity vendors may add agent controls.", "fact_ids": ["F1"]}]}, facts, [])
    assert missing["implications"] == []                                   # no key assumption -> not eligible
    assert "Key Assumptions Check" in brief.build_prompt("t", facts, [])


def test_gap_research_defers_when_the_run_budget_is_short():
    instance = bare_orchestrator()
    instance.execution_budget_service = NS(remaining_discretionary_seconds=lambda: 300.0)
    instance._precedent_research = lambda *a, **k: (_ for _ in ()).throw(AssertionError("should defer"))
    state = {"stage_status": {}, "warnings": []}
    out = instance._pre_analysis_gap_research(events=EVENTS, evidence=EVIDENCE, state=state)
    assert len(out) == 2 and not state.get("gap_research_done")
    assert state["warnings"][0]["type"] == "gap_research_deferred_time_budget"


# ----------------------------------------------------------------------
# v6.20.1 (GPT review of v6.19)
# ----------------------------------------------------------------------

def test_replay_never_runs_the_collection_rescue(tmp_path, monkeypatch):
    instance = bare_orchestrator()
    path, _ = save(instance, tmp_path, monkeypatch, evidence=EVIDENCE)
    instance._build_relationships = lambda **k: {"relationship_count": 0}
    instance._rebuild_graph = lambda **k: {}
    instance._pre_analysis_gap_research = lambda *, events, evidence, state: list(evidence)

    def never(*args, **kwargs):
        raise AssertionError("collection rescue ran during replay")

    instance._attempt_targeted_strategic_evidence_rescue = never
    instance._discover_sources_with_run_circuit_breaker = never
    rejected = {"insight": "A promising but evidence-incomplete candidate", "finding_tier": "hidden_insight"}
    instance.strategic_inference_agent = NS(analyze=lambda **k: {
        "insights": [], "connections": [],
        "metadata": {"desired_depth": "deep", "rejected_candidate_diagnostics": [{"candidate_snapshot": rejected}]}})
    seen = {}

    def research(**kwargs):
        seen["replay"] = kwargs["state"].get("replayed_from", {}).get("run_id")
        seen["state"] = kwargs["state"]
        return [], kwargs["evidence"], kwargs["inference_result"]

    instance._research_hypotheses = research

    monkeypatch.setattr(R, "_has_hidden_insight_candidate", staticmethod(lambda candidates: False), raising=False)
    instance._evidence_manifest = lambda evidence: {"count": len(evidence)}
    try:
        instance.run("artificial intelligence", research_depth="deep", replay_checkpoint=path)
    except Stop:
        pass
    except AssertionError:
        raise
    except Exception:
        pass  # later stages are not stubbed; the rescue guard is what is tested
    assert seen["replay"] == "RUN-ORIGINAL"
    assert any(w.get("type") == "strategic_rescue_skipped_replay" for w in seen["state"]["warnings"])


def test_discovery_wrapper_refuses_to_collect_during_replay():
    instance = bare_orchestrator()
    discovery_state = {}
    assert instance._discover_sources_with_run_circuit_breaker(
        topic="t", max_sources=5, parent_state={"replayed_from": {"run_id": "RUN-1"}},
        discovery_state=discovery_state, discovery_phase="strategic_rescue") == []
    assert discovery_state["replay_no_collection"] is True


def test_early_hypothesis_research_uses_the_google_backed_challenge_search():
    from services.grounded_search import CombinedSearch
    instance = bare_orchestrator()
    instance.extraction_agent = NS(llm=NS(_client=object(), _types=NS(Tool=1, GoogleSearch=1), model_name="m"))
    instance.web_search_service = NS(search=lambda q, **k: {"results": []})
    instance.source_fetch_service = None
    instance._challenge_search_cache = None
    instance.hypothesis_evidence_service = None
    used = {}
    import services.hypothesis_evidence_service as hes

    class Service:
        def __init__(self, **kwargs):
            used["search"] = kwargs["web_search_service"]

        def investigate(self, *, candidate, evidence, blueprint):
            used["investigated"] = candidate["insight"]
            return {"claim_fingerprint": "x", "assessment": {"status": "completed_none_found"}, "accepted_evidence": []}

    original = hes.HypothesisEvidenceService
    hes.HypothesisEvidenceService = Service
    try:
        try:
            instance._research_hypotheses(topic="t", candidates=[{"insight": "claim"}], inference_result={},
                                          events=EVENTS, evidence=EVIDENCE, graph_context={}, constraints={},
                                          blueprint={}, state={"warnings": []})
        except Exception:
            pass  # only the search wiring is checked here
    finally:
        hes.HypothesisEvidenceService = original
    assert isinstance(used["search"], CombinedSearch)
    assert used["search"] is instance._challenge_search()          # shares the per-run challenge budget
    assert used["search"].grounded.max_calls == R.CHALLENGE_GROUNDED_PROMPTS
    assert used["investigated"] == "claim"


def test_gap_research_records_reach_the_synthesis_prompt():
    from agents.strategic_inference_agent import StrategicInferenceAgent
    agent = object.__new__(StrategicInferenceAgent)
    ordinary = [{"evidence_id": f"FACT-{i:03d}", "event_id": f"EVENT-{i}", "source_id": f"SRC-{i}",
                 "evidence_type": "source_excerpt", "claim": f"Acme item {i} reported chip supply news {i}",
                 "published_at": "2026-10-01"} for i in range(30)]
    gaps = [{"evidence_id": f"EVID-GAP{t.upper()}", "source_id": f"SRC-{t}", "evidence_type": "source_excerpt",
             "claim": f"{t} context about export rules", "analytical_context_only": True, "gap_type": t,
             "gap_for": "chip supply", "published_at": "2026-09-01"}
            for t in ("policy", "dependency", "numbers")]
    records = agent._extract_evidence_records(ordinary + gaps)
    digest = agent._synthesis_digest(research_question="AI chips", evidence_records=records,
                                     evidence_review={}, maximum=24)
    ids = [item["evidence_id"] for item in digest]
    assert {"EVID-GAPPOLICY", "EVID-GAPDEPENDENCY", "EVID-GAPNUMBERS"} <= set(ids)
    assert sum(1 for item in digest if not item.get("gap_type")) == 24           # event evidence not crowded out
    many = [dict(gaps[0], evidence_id=f"EVID-POL{i}") for i in range(20)]
    digest = agent._synthesis_digest(research_question="AI chips",
                                     evidence_records=agent._extract_evidence_records(ordinary + many),
                                     evidence_review={}, maximum=24)
    assert sum(1 for item in digest if item.get("gap_type")) == StrategicInferenceAgent.GAP_CONTEXT_SYNTHESIS_MAX


def test_with_the_real_deep_setup_gap_research_runs_before_analysis():
    # The app builds the orchestrator without a run budget object (ExecutionBudgetService
    # needs a run_id, so the optional construction yields None). Time gates then never
    # defer gap research; its own caps (queries, sources, seconds per development) bound it.
    budget = R._construct_optional("services.execution_budget_service", "ExecutionBudgetService")
    assert budget is None
    instance = bare_orchestrator()
    instance.execution_budget_service = budget
    ran = {}

    def research(developments, state):
        ran["yes"] = True
        state["gap_context_records"] = []
        return []

    instance._precedent_research = research
    instance._evidence_manifest = lambda evidence: {}
    state = {"stage_status": {}, "warnings": []}
    instance._pre_analysis_gap_research(events=EVENTS, evidence=EVIDENCE, state=state)
    assert ran and state["gap_research_done"] is True
    assert state["remaining_seconds_at_gap_start"] == "unbounded"

# ----------------------------------------------------------------------
# v6.20.2 objective-integrity regressions
# ----------------------------------------------------------------------

def test_replay_is_read_only_for_cross_run_memory(tmp_path, monkeypatch):
    instance = bare_orchestrator()
    path, _ = save(instance, tmp_path, monkeypatch, evidence=EVIDENCE)
    instance._remember_run = lambda **k: (_ for _ in ()).throw(AssertionError("replay wrote event memory"))
    instance._build_relationships = lambda **k: {"relationship_count": 0}
    instance._rebuild_graph = lambda **k: {}
    instance._pre_analysis_gap_research = lambda *, events, evidence, state: list(evidence)
    instance.strategic_inference_agent = NS(analyze=lambda **k: (_ for _ in ()).throw(Stop()))
    with pytest.raises(Stop):
        instance.run("artificial intelligence", research_depth="deep", replay_checkpoint=path)


def test_old_checkpoint_underlying_ids_are_recomputed_with_current_resolver(tmp_path, monkeypatch):
    instance = bare_orchestrator()
    monkeypatch.setenv("EVENT_INTELLIGENCE_CHECKPOINT_DIR", str(tmp_path))
    old_events = [
        {"event_id": "EVENT-A", "underlying_event_id": "OLD-U-1", "primary_actor": "Acme",
         "event_type": "acquisition", "title": "Acme acquires Widgetco robotics startup",
         "event_start_at": "2026-10-01", "source_id": "SRC-A"},
        {"event_id": "EVENT-B", "underlying_event_id": "OLD-U-2", "primary_actor": "Acme",
         "event_type": "acquisition", "title": "Acme buys Widgetco robotics startup",
         "event_start_at": "2026-10-01", "source_id": "SRC-B"},
    ]
    payload = {
        "version": "pre-analysis-checkpoint-v1", "run_id": "RUN-OLD", "topic": "robotics",
        "research_depth": "deep", "saved_at": "2026-10-08T00:00:00+00:00",
        "selected_source_ids": ["SRC-A", "SRC-B"], "event_records": old_events,
        "evidence_records": [], "admitted_event_records": old_events, "admitted_evidence_records": [],
        "evidence_payload": {}, "state": {}, "extraction_depth": "deep", "_path": "fixture",
    }
    state = {"stage_status": {}, "topic": "robotics"}
    _, events, _, admitted, _, _ = instance._restore_checkpoint(payload, state)
    ids = {e["underlying_event_id"] for e in events}
    assert len(ids) == 1
    assert "OLD-U-1" not in ids
    assert "OLD-U-2" not in ids
    assert {e["underlying_event_id"] for e in admitted} == ids
    assert state["checkpoint_resolver_migration"]["recomputed"] is True
