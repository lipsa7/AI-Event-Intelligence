"""v6.17: the report is never blank (docs/PRODUCT_SPEC.md).

Landscape and the editorial briefing (with forward-looking implications)
always render; historical analogy renders only from a cited, retrieved case;
the hidden insight section abstains on its own.
"""

from __future__ import annotations

import pytest

import json
import time
from types import SimpleNamespace

from services import editorial_briefing as brief
from services.research_orchestrator_service import ResearchOrchestratorService as R

DEVELOPMENTS = [
    {"title": "companies adding kill switches for AI agents", "facts": [
        {"text": "Okta added a kill switch that stops an AI agent's access in real time.",
         "evidence_id": "EVID-1", "source_id": "SOURCE-1", "date": "2026-09-29"},
        {"text": "Equals Money limits its MCP server so AI agents can read data but not make payments.",
         "evidence_id": "EVID-2", "source_id": "SOURCE-2", "date": "2026-10-01"},
    ]},
    {"title": "Anthropic files for an IPO", "facts": [
        {"text": "Anthropic has filed confidentially for an initial public offering, according to a report.",
         "evidence_id": "EVID-3", "source_id": "SOURCE-3", "date": "2026-10-02"},
    ]},
]
HISTORY = [{"evidence_id": "EVID-H1", "source_id": "SOURCE-H", "source_url": "https://example.com/2014",
            "text": "In 2014 banks added transaction limits for third-party payment apps.",
            "historical_case": {"year": 2014, "outcome_quote": "limits became standard",
                                "similarity": "a platform capped what outside software could do with accounts",
                                "difference": "the apps were human-operated, not autonomous agents"}}]
DRAFT = {
    "headline": "Companies are putting hard limits on what AI agents can do.",
    "paragraphs": [[
        {"text": "Okta and Equals Money both restricted what AI agents can do.", "fact_ids": ["F1", "F2"]},
        {"text": "Agents will replace all payment staff by 2027.", "fact_ids": ["F2"]},
    ], [
        {"text": "Anthropic has reportedly filed for an IPO.", "fact_ids": ["F3"]},
    ]],
    "implications": [
        {"text": "More vendors may ship agent permission controls.", "fact_ids": ["F1", "F2"], "conditions": "enterprises keep deploying AI agents",
         "watch_for": "agent kill-switch launches", "horizon": "6-12 months"},
        {"text": "Invented implication citing nothing valid.", "fact_ids": ["F99"]},
    ],
    "analogies": [
        {"case_id": "H1", "fact_ids": ["F2"], "comparison": "Payment limits for third-party apps",
         "difference": "Agents act autonomously"},
        {"case_id": "H9", "fact_ids": ["F1"], "comparison": "Invented case"},
    ],
}



@pytest.fixture(autouse=True)
def _private_ledgers(tmp_path, monkeypatch):
    """v6.20.5: tests never write to the real forecast ledger or event memory."""
    monkeypatch.setenv("EVENT_INTELLIGENCE_WATCH_ITEMS", str(tmp_path / "watch_items.sqlite3"))
    monkeypatch.setenv("EVENT_INTELLIGENCE_EVENT_MEMORY", str(tmp_path / "event_memory.sqlite3"))

def setup():
    facts = brief.select_facts(DEVELOPMENTS)
    cases = brief.select_cases(HISTORY)
    return facts, cases


def test_facts_and_cases_get_ids():
    facts, cases = setup()
    assert [f["fact_id"] for f in facts] == ["F1", "F2", "F3"]
    assert cases[0]["case_id"] == "H1" and cases[0]["evidence_id"] == "EVID-H1"
    assert brief.select_cases([{"evidence_id": "EVID-X", "text": "no case"}]) == []


def test_prompt_uses_only_supplied_facts_and_cases():
    facts, cases = setup()
    prompt = brief.build_prompt("artificial intelligence", facts, cases)
    assert "[F1]" in prompt and "[H1]" in prompt
    assert "ONLY from the historical cases listed above" in prompt


def test_draft_drops_uncited_and_invented_items():
    facts, cases = setup()
    draft = brief.parse_draft(json.dumps(DRAFT), facts, cases)
    assert len(draft["implications"]) == 1
    assert [a["case_id"] for a in draft["analogies"]] == ["H1"]


def test_failing_sentences_are_removed_and_passing_kept():
    facts, cases = setup()
    draft = brief.parse_draft(DRAFT, facts, cases)
    claims = brief.grounding_claims(draft, facts, cases)
    statuses = {c["claim_id"]: "supported" for c in claims}
    bad = next(c for c in claims if c["claim_text"].startswith("Agents will replace"))
    statuses[bad["claim_id"]] = "unsupported"
    out = brief.sanitize(draft, claims, statuses, facts, cases)
    texts = [s["text"] for p in out["paragraphs"] for s in p]
    assert "Agents will replace all payment staff by 2027." not in texts
    assert "Okta and Equals Money both restricted what AI agents can do." in texts
    assert out["implications"][0]["label"] == "analysis"
    assert out["analogies"][0]["case"]["year"] == 2014
    assert out["sentences_removed_by_check"] == 1


def test_implications_are_checked_as_inference_and_analogies_need_two_records():
    facts, cases = setup()
    claims = brief.grounding_claims(brief.parse_draft(DRAFT, facts, cases), facts, cases)
    implication = next(c for c in claims if c["claim_text"].startswith(brief.IMPLICATION_PREFIX))
    analogy = next(c for c in claims if c["claim_text"].startswith(brief.ANALOGY_PREFIX))
    assert set(implication["_bar"]) == {"supported", "inference_supported"}
    assert analogy["multi_evidence_required"] and "EVID-H1" in analogy["evidence_ids"]


def test_grounding_unavailable_fails_closed():
    facts, cases = setup()
    draft = brief.parse_draft(DRAFT, facts, cases)
    out = brief.sanitize(draft, brief.grounding_claims(draft, facts, cases), None, facts, cases)
    assert not brief.has_content(out)


def test_plain_briefing_is_source_statements():
    facts, _ = setup()
    out = brief.plain_briefing(facts, "model_unavailable")
    assert out["mode"] == "source_statements_only" and len(out["paragraphs"]) == 2
    assert out["implications"] == []


# ----------------------------------------------------------------------
# Orchestrator
# ----------------------------------------------------------------------

class FakeLLM:
    timeout_seconds = 60
    max_retries = 3

    def __init__(self, reply):
        self.reply = reply

    def ask(self, prompt, **kwargs):
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class FakeGrounding:
    def __init__(self, fail=False):
        self.fail = fail

    def ground_claims(self, *, claims, evidence_records):
        if self.fail:
            raise RuntimeError("down")
        return {"claims": [{"claim_id": c["claim_id"],  # v6.21.6: novelty tests find nothing already said
                            "grounding_status": "unsupported" if c["claim_id"].startswith("NOVELTY") else "supported"}
                           for c in claims]}


def orchestrator(reply, grounding=None):
    instance = R.__new__(R)
    instance.extraction_agent = SimpleNamespace(llm=FakeLLM(reply))
    instance.grounding_service = grounding or FakeGrounding()
    instance.execution_budget_service = None
    instance.article_service = None
    return instance


def test_briefing_with_checked_analysis():
    out = orchestrator(json.dumps(DRAFT))._editorial_briefing(
        topic="AI", developments=DEVELOPMENTS, evidence=[], state={"historical_context": {"records": HISTORY}})
    assert out["mode"] == "checked_analysis"
    assert out["implications"] and out["analogies"]


def test_model_failure_still_gives_a_briefing():
    out = orchestrator(TimeoutError("slow"))._editorial_briefing(
        topic="AI", developments=DEVELOPMENTS, evidence=[], state={})
    assert out["mode"] == "source_statements_only" and out["paragraphs"]


def test_grounding_failure_still_gives_a_briefing():
    out = orchestrator(json.dumps(DRAFT), FakeGrounding(fail=True))._editorial_briefing(
        topic="AI", developments=DEVELOPMENTS, evidence=[], state={})
    assert out["mode"] == "source_statements_only" and out["paragraphs"]


def test_no_analogy_without_retrieved_case():
    out = orchestrator(json.dumps(DRAFT))._editorial_briefing(
        topic="AI", developments=DEVELOPMENTS, evidence=[], state={})
    assert out["analogies"] == []


def events_and_evidence():
    events = [
        {"event_id": "EVENT-1", "underlying_event_id": "U1", "strategic_cluster_id": "dev",
         "strategic_cluster_status": "verified", "strategic_cluster_development": "agent kill switches",
         "primary_actor": "Okta", "event_start_at": "2026-09-29", "summary": "Okta adds kill switch"},
        {"event_id": "EVENT-2", "underlying_event_id": "U2", "strategic_cluster_id": "dev",
         "strategic_cluster_status": "verified", "strategic_cluster_development": "agent kill switches",
         "primary_actor": "Equals Money", "event_start_at": "2026-10-01", "summary": "Equals Money limits MCP"},
    ]
    evidence = [
        {"evidence_id": "EVID-1", "event_id": "EVENT-1", "source_id": "SOURCE-1", "evidence_type": "source_excerpt",
         "claim": "Okta added a kill switch that stops an AI agent's access in real time."},
        {"evidence_id": "EVID-2", "event_id": "EVENT-2", "source_id": "SOURCE-2", "evidence_type": "source_excerpt",
         "claim": "Equals Money limits its MCP server so AI agents can read data but not make payments."},
    ]
    return events, evidence


def test_abstained_run_still_has_landscape_and_briefing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    draft = {"headline": "Firms limit AI agents.",
             "paragraphs": [[{"text": "Okta and Equals Money restricted AI agents.", "fact_ids": ["F1", "F2"]}]],
             "implications": [{"text": "More firms may add agent limits.", "fact_ids": ["F1", "F2"], "conditions": "enterprises keep deploying AI agents",
                               "watch_for": "launches", "horizon": "months"}], "analogies": []}
    obj = orchestrator(json.dumps(draft))
    obj.report_service = None
    obj.policy_enforcement_service = None
    for name in ("telemetry_service", "insight_registry_service", "audit_receipt_service", "insight_trace_service"):
        setattr(obj, name, None)
    events, evidence = events_and_evidence()
    state = {"warnings": [], "claim_support_gate": {"attempts": []}}
    obj._complete_abstention(
        state=state, topic="artificial intelligence", run_id="RUN-T17", depth="deep", events=events,
        evidence=evidence, constraints={}, graph_context={}, reasons=["claim_support_gate:x"],
        started_at=time.monotonic(), stage="claim_support_gate")
    report = state["report"]
    assert report["event_landscape"]
    assert report["editorial_briefing"]["mode"] == "checked_analysis"
    assert report["editorial_briefing"]["implications"]
    sections = report["analysis_metadata"]["report_sections"]
    assert sections["event_landscape"] and sections["forward_looking_implications"] == 1
    assert sections["hidden_insight"] == 0
    assert report["analysis_metadata"]["hidden_insight_section"] == "no_insight_passed"
    assert "No hidden cross-actor insight" in report["abstention_summary"]


def test_spec_file_exists():
    import pathlib
    spec = pathlib.Path(__file__).resolve().parents[1] / "docs" / "PRODUCT_SPEC.md"
    assert "The report is never blank" in spec.read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# v6.17.1 (GPT review): every displayed field is checked
# ----------------------------------------------------------------------

def _statuses_failing(claims, *prefixes):
    statuses = {c["claim_id"]: "supported" for c in claims}
    for claim in claims:
        if any(claim["claim_text"].startswith(prefix) for prefix in prefixes):
            statuses[claim["claim_id"]] = "unsupported"
    return statuses


def test_unsupported_watch_for_and_horizon_are_not_shown():
    facts, cases = setup()
    draft = brief.parse_draft({
        "headline": "", "paragraphs": [],
        "implications": [{"text": "More vendors may ship agent permission controls.", "fact_ids": ["F1", "F2"], "conditions": "enterprises keep deploying AI agents",
                          "watch_for": "a mandatory global law next week", "horizon": "by tomorrow"}],
        "analogies": []}, facts, cases)
    claims = brief.grounding_claims(draft, facts, cases)
    assert any(c["claim_text"].startswith(brief.WATCH_PREFIX) for c in claims)
    assert any(c["claim_text"].startswith(brief.HORIZON_PREFIX) for c in claims)
    out = brief.sanitize(draft, claims, _statuses_failing(claims, brief.WATCH_PREFIX, brief.HORIZON_PREFIX),
                         facts, cases)
    item = out["implications"][0]
    assert item["text"] == "More vendors may ship agent permission controls."
    assert item["watch_for"] == "" and item["horizon"] == ""


def test_supported_watch_for_and_horizon_are_kept():
    facts, cases = setup()
    draft = brief.parse_draft(DRAFT, facts, cases)
    claims = brief.grounding_claims(draft, facts, cases)
    out = brief.sanitize(draft, claims, {c["claim_id"]: "inference_supported" for c in claims}, facts, cases)
    assert out["implications"][0]["watch_for"] == "agent kill-switch launches"
    assert out["implications"][0]["horizon"] == "6-12 months"


def test_analogy_with_invented_difference_is_removed():
    facts, cases = setup()
    draft = brief.parse_draft(dict(DRAFT, analogies=[
        {"case_id": "H1", "fact_ids": ["F2"], "comparison": "Payment limits for third-party apps",
         "difference": "The historical case caused a recession."}]), facts, cases)
    claims = brief.grounding_claims(draft, facts, cases)
    out = brief.sanitize(draft, claims, _statuses_failing(claims, brief.DIFFERENCE_PREFIX), facts, cases)
    assert out["analogies"] == []


def test_analogy_without_a_difference_is_not_used():
    facts, cases = setup()
    draft = brief.parse_draft(dict(DRAFT, analogies=[
        {"case_id": "H1", "fact_ids": ["F2"], "comparison": "Payment limits for third-party apps"}]), facts, cases)
    assert draft["analogies"] == []


def test_undated_or_future_case_is_not_eligible():
    undated = [dict(HISTORY[0], historical_case={"year": None})]
    textual = [dict(HISTORY[0], historical_case={"year": "around then"})]
    future = [dict(HISTORY[0], historical_case={"year": 2999})]
    assert brief.select_cases(undated) == [] and brief.select_cases(textual) == []
    assert brief.select_cases(future) == []
    assert brief.select_cases(HISTORY)[0]["year"] == 2014


def test_failed_section_assembly_still_records_a_briefing_status():
    obj = orchestrator(json.dumps(DRAFT))
    obj._developments_to_watch = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    report = {"event_landscape": [{"event_id": "E1"}]}
    obj._attach_spec_sections(report=report, topic="AI", events=[], evidence=[], state={})
    assert report["editorial_briefing"]["mode"] == "failed"
    assert "editorial_briefing" in report


def test_dashboard_never_shows_a_fifth_main_finding_state():
    import pathlib
    source = (pathlib.Path(__file__).resolve().parents[1] / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    start = source.index("def render_main_finding(")
    body = source[start:source.index("def _briefing_meta(", start)]
    assert "Main finding: verified connection" not in body and "Main finding: " not in body
    assert "None this run." in body
    assert "No analytical briefing could be produced" in source


# ----------------------------------------------------------------------
# v6.17.2: precedent and leverage reasoning; per-section independence
# ----------------------------------------------------------------------

PRECEDENT_DRAFT = {
    "headline": "Firms are capping what AI agents can do.",
    "paragraphs": [[{"text": "Okta and Equals Money restricted AI agents.", "fact_ids": ["F1", "F2"]}]],
    "implications": [
        {"conditions": "enterprises keep deploying AI agents", "basis": "precedent", "case_id": "H1", "text": "Agent payment limits may become standard at fintechs.",
         "fact_ids": ["F2"], "watch_for": "other fintechs announcing agent limits", "horizon": "12 months"},
        {"conditions": "enterprises keep deploying AI agents", "basis": "leverage", "text": "Identity vendors like Okta gain leverage over how enterprises deploy agents.",
         "fact_ids": ["F1"], "watch_for": "", "horizon": "", "holder": "Okta", "counterparty": "enterprises using AI agents",
         "leverage_source": "a kill switch that stops an AI agent's access", "mechanism": "agents depend on Okta access"},
        {"conditions": "enterprises keep deploying AI agents", "basis": "precedent", "case_id": "H9", "text": "Invented precedent.", "fact_ids": ["F1"]},
        {"conditions": "enterprises keep deploying AI agents", "basis": "precedent", "case_id": "", "text": "Precedent with no case.", "fact_ids": ["F1"]},
    ],
    "analogies": [],
}


def test_precedent_implication_must_cite_a_retrieved_case():
    facts, cases = setup()
    draft = brief.parse_draft(PRECEDENT_DRAFT, facts, cases)
    assert [(i["basis"], i["case_id"]) for i in draft["implications"]] == [("precedent", "H1"), ("leverage", "")]


def test_precedent_and_leverage_are_checked_with_their_own_wording():
    facts, cases = setup()
    draft = brief.parse_draft(PRECEDENT_DRAFT, facts, cases)
    claims = brief.grounding_claims(draft, facts, cases)
    precedent = next(c for c in claims if c["claim_text"].startswith(brief.PRECEDENT_PREFIX))
    leverage = next(c for c in claims if c["claim_text"].startswith(brief.LEVERAGE_PREFIX))
    assert "EVID-H1" in precedent["evidence_ids"] and precedent["multi_evidence_required"]
    assert leverage["evidence_ids"] == ["EVID-1"]
    out = brief.sanitize(draft, claims, {c["claim_id"]: "inference_supported" for c in claims}, facts, cases)
    assert out["implications"][0]["case"]["year"] == 2014


def test_only_an_analogy_surviving_is_still_content():
    facts, cases = setup()
    draft = brief.parse_draft(DRAFT, facts, cases)
    claims = brief.grounding_claims(draft, facts, cases)
    statuses = {c["claim_id"]: "unsupported" for c in claims}
    for claim in claims:
        if claim["claim_text"].startswith((brief.ANALOGY_PREFIX, brief.DIFFERENCE_PREFIX)):
            statuses[claim["claim_id"]] = "supported"
    out = brief.sanitize(draft, claims, statuses, facts, cases)
    assert not out["paragraphs"] and not out["implications"] and not out["headline"]
    assert out["analogies"] and brief.has_content(out)


def _render(sections):
    import contextlib
    import importlib
    import sys
    import types
    calls = []

    class Ctx(contextlib.nullcontext):
        def __call__(self, fn=None, *a, **k):
            return fn

    class St(types.ModuleType):
        def __getattr__(self, name):
            return lambda *a, **k: calls.append((name, a[0] if a else k)) or Ctx()

    saved = {name: sys.modules.get(name) for name in ("streamlit", "frontend.dashboard")}
    sys.modules["streamlit"] = St("streamlit")
    sys.modules.pop("frontend.dashboard", None)
    try:
        dashboard = importlib.import_module("frontend.dashboard")
        calls.clear()
        dashboard.render_editorial_briefing(sections)
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return calls


def test_dashboard_renders_an_implication_when_paragraphs_failed():
    calls = _render({"editorial_briefing": {"headline": "", "paragraphs": [], "analogies": [], "implications": [
        {"basis": "leverage", "text": "Okta gains leverage over agent deployments.", "citations": []}]}})
    text = " ".join(str(c[1]) for c in calls)
    assert "Okta gains leverage over agent deployments." in text
    assert "No analytical briefing could be produced" not in text


def test_dashboard_renders_an_analogy_when_everything_else_failed():
    calls = _render({"editorial_briefing": {"headline": "", "paragraphs": [], "implications": [], "analogies": [
        {"comparison": "Payment limits for third-party apps", "difference": "Agents act on their own",
         "case": {"year": 2014, "source_url": "https://example.com/2014"}}]}})
    text = " ".join(str(c[1]) for c in calls)
    assert "Payment limits for third-party apps" in text and "Historical analogy" in text


class FakePrecedentService:
    def __init__(self):
        self.calls = []

    def investigate_precedents(self, *, development, blueprint=None):
        self.calls.append(development["title"])
        return {"development": development["title"], "status": "complete", "limitations": [],
                "records": [dict(HISTORY[0], evidence_id="EVID-H-" + str(len(self.calls)))]}


def test_precedents_are_researched_for_top_developments_in_deep_runs():
    obj = orchestrator(json.dumps(PRECEDENT_DRAFT))
    service = FakePrecedentService()
    obj.hypothesis_evidence_service = service
    report = {"research_depth": "deep", "developments_to_watch": DEVELOPMENTS}
    state = {"blueprint": {}}
    obj._attach_spec_sections(report=report, topic="AI", events=[], evidence=[], state=state)
    assert service.calls == ["companies adding kill switches for AI agents", "Anthropic files for an IPO"]
    assert len(state["historical_context"]["records"]) == 2
    assert report["analysis_metadata"]["precedent_research"][0]["case_count"] == 1
    assert report["editorial_briefing"]["case_count"] == 2
    assert any(item.get("basis") == "precedent" for item in report["editorial_briefing"]["implications"])


def test_precedent_failure_does_not_stop_the_briefing():
    obj = orchestrator(json.dumps(DRAFT))

    class Broken:
        def investigate_precedents(self, **kwargs):
            raise TimeoutError("search down")

    obj.hypothesis_evidence_service = Broken()
    report = {"research_depth": "deep", "developments_to_watch": DEVELOPMENTS}
    obj._attach_spec_sections(report=report, topic="AI", events=[], evidence=[], state={"blueprint": {}})
    assert report["editorial_briefing"]["mode"] == "checked_analysis"
    assert report["analysis_metadata"]["precedent_research"][0]["status"] == "failed"


def test_gap_search_plan_covers_precedent_policy_dependency_numbers():
    from services.hypothesis_evidence_service import HypothesisEvidenceService
    import inspect as _inspect
    source = _inspect.getsource(HypothesisEvidenceService.investigate_gaps)
    for gap in ("precedent:", "policy:", "dependency:", "numbers:"):
        assert gap in source
    assert "_process_source" in source  # same quote/date validation as hypothesis research


def test_incentive_implication_is_a_checked_strategic_assessment():
    facts, cases = setup()
    draft = brief.parse_draft({"headline": "", "paragraphs": [], "analogies": [], "implications": [
        {"basis": "incentive", "text": "Given its payment rules, limiting agents to read-only access appears "
         "strategically attractive for Equals Money.", "fact_ids": ["F2"], "actor": "Equals Money",
         "policy_or_regulation": "payments cannot be initiated by AI agents", "company_relevance": "it runs an MCP server",
         "economic_or_strategic_effect": "lower payment risk", "attractive_move": "read-only agent access",
         "conditions": "regulators allow agent payments"}]}, facts, cases)
    claims = brief.grounding_claims(draft, facts, cases)
    prefixes = [c["claim_text"] for c in claims]
    assert prefixes[0].startswith(brief.POLICY_EXISTS_PREFIX)
    exists = claims[0]
    passing = {c["claim_id"]: ("supported" if c["_bar"] == ["supported"] else "inference_supported") for c in claims}
    out = brief.sanitize(draft, claims, passing, facts, cases)
    assert out["implications"][0]["basis"] == "incentive" and out["implications"][0]["conditions"]
    # The rule itself must be a stated fact; an inferred rule fails the whole implication.
    out = brief.sanitize(draft, claims, dict(passing, **{exists["claim_id"]: "inference_supported"}), facts, cases)
    assert out["implications"] == []


def test_unstructured_leverage_and_policy_claims_are_refused():
    facts, cases = setup()
    draft = brief.parse_draft({"headline": "", "paragraphs": [], "analogies": [], "implications": [
        {"conditions": "enterprises keep deploying AI agents", "basis": "leverage", "text": "Okta has leverage over the market.", "fact_ids": ["F1"]},
        {"conditions": "enterprises keep deploying AI agents", "basis": "incentive", "text": "This is the right move for Equals Money.", "fact_ids": ["F2"]},
        {"conditions": "enterprises keep deploying AI agents", "basis": "precedent", "case_id": "H1", "text": "Equals Money will copy the 2014 caps.", "fact_ids": ["F2"]},
    ]}, facts, cases)
    assert draft["implications"] == []


def test_leverage_parts_are_checked_separately():
    facts, cases = setup()
    draft = brief.parse_draft(PRECEDENT_DRAFT, facts, cases)
    claims = brief.grounding_claims(draft, facts, cases)
    lever = [c for c in claims if c["claim_text"].startswith((brief.LEVERAGE_POSITION_PREFIX, "The cited facts state that Okta"))]
    assert len(lever) == 2 and all(c["_bar"] == ["supported"] for c in lever)
    statuses = {c["claim_id"]: "inference_supported" for c in claims}
    statuses.update({c["claim_id"]: "supported" for c in lever})
    out = brief.sanitize(draft, claims, statuses, facts, cases)
    assert any(i["basis"] == "leverage" for i in out["implications"])
    statuses[lever[1]["claim_id"]] = "partially_supported"   # control not established
    out = brief.sanitize(draft, claims, statuses, facts, cases)
    assert not any(i["basis"] == "leverage" for i in out["implications"])


def test_case_without_similarity_or_difference_is_not_a_precedent():
    no_diff = [dict(HISTORY[0], historical_case={"year": 2014, "similarity": "caps"})]
    assert brief.select_cases(no_diff) == []


def test_gap_research_end_to_end_with_offline_doubles():
    """Real EvidenceDiscoveryAgent path: plan -> search -> fetch -> quote/date validation, per gap type."""
    from services.hypothesis_evidence_service import HypothesisEvidenceService
    history = ("In 2014, banks capped third-party payment apps at read-only access.\n\n"
               "Within two years the caps became standard across the industry.")
    policy = ("The payments regulator requires that any automated system initiating a payment be "
              "registered and audited, under rules in force since March 2026.")

    class Model:
        model_name = "offline"
        timeout_seconds = 30

        def ask(self, prompt, **kwargs):
            if kwargs["metadata"]["task"] == "gap_query_plan":
                return json.dumps({"queries": [
                    {"gap": "precedent", "query": "bank payment app access caps"},
                    {"gap": "policy", "query": "automated payment initiation rules"},
                    {"gap": "nonsense", "query": "ignored"}]})
            body = json.loads(prompt.rsplit("\n", 1)[1])["source_text"]
            if body == history:
                first, outcome = history.split("\n\n")
                row = {"quote": first, "claim_kind": "reported_claim", "relation": "supports", "connection": "caps",
                       "historical_case": {"year": 2014, "date_quote": first, "outcome_quote": outcome,
                                           "similarity": "access caps", "difference": "apps vs agents"}}
            else:
                row = {"quote": policy, "claim_kind": "observation", "relation": "context",
                       "connection": "rule applies", "historical_case": None}
            return json.dumps({"review_complete": True, "records": [row]})

    class Search:
        def __init__(self):
            self.queries = []

        def search(self, query, **kwargs):
            self.queries.append(query)
            url, date = (("https://history.example/caps", "2019-03-01T00:00:00Z") if "caps" in query
                         else ("https://rules.example/payments", "2026-09-01T00:00:00Z"))
            return {"results": [{"url": url, "title": "t", "published_at": date}],
                    "provider_stats": {"fixture": {"status": "ok"}}}

    class Fetch:
        def fetch(self, url, **kwargs):
            return {"canonical_url": url, "text_content": history if "history" in url else policy,
                    "metadata": {"content_was_truncated": False}}

    search = Search()
    service = HypothesisEvidenceService(web_search_service=search, source_fetch_service=Fetch(), llm_service=Model())
    receipt = service.investigate_gaps(development=DEVELOPMENTS[0], as_of="2026-10-09T00:00:00+00:00")
    # v6.22: the expert-commentary search is always added when the planner omits it.
    assert search.queries == ["bank payment app access caps", "automated payment initiation rules",
                              DEVELOPMENTS[0]["title"] + " analysis implications"]
    assert receipt["found_by_gap"]["precedent"] == 1 and receipt["found_by_gap"]["policy"] == 1
    past = next(r for r in receipt["records"] if r.get("historical_case"))
    assert past["precedent_for"] == DEVELOPMENTS[0]["title"] and brief.select_cases([past])[0]["year"] == 2014
    rule = next(r for r in receipt["records"] if r["gap_type"] == "policy")
    facts = brief.select_context_facts([rule], start=4)
    assert facts[0]["fact_id"] == "F4" and facts[0]["gap_type"] == "policy"
    assert facts[0]["development"].startswith("Policy / regulation context")
    # Backward-compatible precedent view keeps only past cases.
    service2 = HypothesisEvidenceService(web_search_service=Search(), source_fetch_service=Fetch(), llm_service=Model())
    assert all(r.get("historical_case") for r in
               service2.investigate_precedents(development=DEVELOPMENTS[0], as_of="2026-10-09T00:00:00+00:00")["records"])


def test_grounded_search_returns_only_links_and_respects_the_cap():
    from services.grounded_search import CombinedSearch, GeminiGroundedSearch
    from types import SimpleNamespace as NS
    response = NS(candidates=[NS(grounding_metadata=NS(grounding_chunks=[
        NS(web=NS(uri="https://a.example/1", title="A")), NS(web=NS(uri="https://a.example/1", title="A")),
        NS(web=NS(uri="ftp://bad", title="x")), NS(web=NS(uri="https://b.example/2", title="B"))]))])
    calls = []

    class Models:
        def generate_content(self, *, model, contents, config):
            calls.append(contents)
            return response

    types_ = NS(GenerateContentConfig=lambda **k: k, Tool=lambda **k: k, GoogleSearch=lambda: "gs")
    llm = NS(_client=NS(models=Models()), _types=types_, model_name="gemini-test")
    grounded = GeminiGroundedSearch(llm, max_calls=1)
    out = grounded.search("automated payment rules", from_date="2025-01-01", to_date="2026-10-01")
    assert [r["url"] for r in out["results"]] == ["https://a.example/1", "https://b.example/2"]
    assert grounded.search("second call")["provider_stats"]["gemini_search"]["status"] == "disabled"
    assert len(calls) == 1

    class Free:
        def search(self, query, **kwargs):
            return {"results": [{"url": "https://a.example/1"}], "provider_stats": {"gdelt": {"status": "failed"}}}

    combined = CombinedSearch(Free(), GeminiGroundedSearch(llm, max_calls=5))
    merged = combined.search("q")
    assert [r["url"] for r in merged["results"]] == ["https://a.example/1", "https://b.example/2"]
    assert set(merged["provider_stats"]) == {"gdelt", "gemini_search"}


def test_gap_context_facts_reach_the_briefing_prompt():
    obj = orchestrator(json.dumps(DRAFT))
    state = {"gap_context_records": [{"evidence_id": "EVID-RULE", "source_id": "SOURCE-RULE", "gap_type": "policy",
                                      "gap_for": "agent kill switches", "published_at": "2026-09-01",
                                      "source_url": "https://rules.example/payments",
                                      "source_excerpt": "The payments regulator requires automated payment systems to be registered."}]}
    prompts = []
    obj.extraction_agent.llm.ask = lambda prompt, **k: prompts.append(prompt) or json.dumps(DRAFT)
    out = obj._editorial_briefing(topic="AI", developments=DEVELOPMENTS, evidence=[], state=state)
    assert "Policy / regulation context" in prompts[0] and "[F4]" in prompts[0]
    assert out["context_fact_count"] == 1 and out["sources"]["SOURCE-RULE"]["url"] == "https://rules.example/payments"


# ----------------------------------------------------------------------
# v6.17.3 (GPT review): precedent integrity
# ----------------------------------------------------------------------

def test_case_without_an_observed_outcome_is_not_a_precedent():
    no_outcome = [dict(HISTORY[0], historical_case=dict(HISTORY[0]["historical_case"], outcome_quote=""))]
    assert brief.select_cases(no_outcome) == []
    assert brief.select_cases(HISTORY)[0]["outcome"] == "limits became standard"


def test_unsupported_precedent_difference_is_not_shown():
    facts, cases = setup()
    draft = brief.parse_draft(PRECEDENT_DRAFT, facts, cases)
    claims = brief.grounding_claims(draft, facts, cases)
    difference = [c for c in claims if c["_target"][0] == "implication_case_difference"]
    assert len(difference) == 1 and "EVID-H1" in difference[0]["evidence_ids"]
    statuses = {c["claim_id"]: ("supported" if c["_bar"] == ["supported"] else "inference_supported") for c in claims}
    statuses[difference[0]["claim_id"]] = "unsupported"
    out = brief.sanitize(draft, claims, statuses, facts, cases)
    precedent = next(i for i in out["implications"] if i["basis"] == "precedent")
    assert precedent["text"] == "Agent payment limits may become standard at fintechs."
    assert precedent["case"]["difference"] == ""
    assert cases[0]["difference"]  # the shared case record is not modified


# ----------------------------------------------------------------------
# v6.18.1 (GPT review): gap-research integrity on the live path
# ----------------------------------------------------------------------

GAP_PAGES = {
    "precedent": ("In 2014, banks capped third-party payment apps at read-only access. "
                  "Within two years the caps became standard across the industry.", "2019-03-01T10:00:00Z"),
    "policy": ("The payments regulator requires any automated system that initiates a payment to be registered, "
               "and the rule remains in effect today.", "2021-05-01T10:00:00Z"),   # older than 2 years on purpose
    "dependency": ("Most fintechs rely on Okta for identity, so Okta controls how their agents get access.",
                   "2026-08-01T10:00:00Z"),
    "numbers": ("Equals Money processes 4 billion pounds of payments a year for 9,000 business customers.",
                "2026-07-01T10:00:00Z"),
    # v6.22: published expert analysis of the development
    "commentary": ("Payments lawyers argue that agent kill switches will move liability for rogue agent payments "
                   "onto identity providers.", "2026-09-20T10:00:00Z"),
}
GAP_QUERIES = {"precedent": "bank payment app caps", "policy": "automated payment initiation rule",
               "dependency": "fintech identity provider reliance", "numbers": "Equals Money payment volume",
               "commentary": "agent kill switch analysis implications"}


class GapModel:
    model_name = "offline"
    timeout_seconds = 30

    def ask(self, prompt, **kwargs):
        if kwargs["metadata"]["task"] == "gap_query_plan":
            return json.dumps({"queries": [{"gap": g, "query": q} for g, q in GAP_QUERIES.items()]})
        body = json.loads(prompt.rsplit("\n", 1)[1])["source_text"]
        gap = next(g for g, (text, _) in GAP_PAGES.items() if text in body)
        text = GAP_PAGES[gap][0]
        if gap == "precedent":
            first, outcome = text.split(". ", 1)
            first += "."
            return json.dumps({"review_complete": True, "records": [{
                "quote": first, "claim_kind": "reported_claim", "relation": "supports", "connection": "caps",
                "historical_case": {"year": 2014, "date_quote": first, "outcome_quote": outcome,
                                    "similarity": "access caps", "difference": "apps vs agents"}}]})
        return json.dumps({"review_complete": True, "records": [{
            "quote": text, "claim_kind": "analysis" if gap == "commentary" else "observation",
            "relation": "context", "connection": gap, "historical_case": None}]})


def _html(text, published):
    padding = "<p>Background on the payments industry and its rules for software access.</p>" * 15
    return (f'<html><head><title>Page</title><meta property="article:published_time" content="{published}">'
            f"</head><body><article><p>{text}</p>{padding}</article></body></html>").encode()


def _real_fetch_service(pages_by_redirect):
    """The production fetcher (redirect, HTML parsing, metadata) over a local transport."""
    from services.source_fetch_service_direct import CallableHTTPTransport, SourceFetchService

    def transport(url, **kwargs):
        final_url, (text, published) = pages_by_redirect[url]
        return {"status_code": 200, "final_url": final_url, "headers": {"Content-Type": "text/html; charset=utf-8"},
                "body": _html(text, published), "redirect_history": [url]}

    return SourceFetchService(transport=CallableHTTPTransport(transport))


def _grounded_llm(redirects_by_query):
    from types import SimpleNamespace as NS

    class Models:
        def generate_content(self, *, model, contents, config):
            query = next(q for q in redirects_by_query if q in contents)
            chunks = [NS(web=NS(uri=uri, title="Publisher")) for uri in redirects_by_query[query]]
            return NS(candidates=[NS(grounding_metadata=NS(grounding_chunks=chunks,
                                                           web_search_queries=[query, query + " news"]))])

    types_ = NS(GenerateContentConfig=lambda **k: k, Tool=lambda **k: k, GoogleSearch=lambda: "gs")
    return NS(_client=NS(models=Models()), _types=types_, model_name="gemini-test")


def _gap_setup(results_per_query=1):
    from services.grounded_search import CombinedSearch, GeminiGroundedSearch
    from services.hypothesis_evidence_service import HypothesisEvidenceService
    redirects, pages = {}, {}
    for gap, query in GAP_QUERIES.items():
        uris = []
        for n in range(results_per_query):
            uri = f"https://vertexaisearch.cloud.google.com/grounding-api-redirect/{gap}{n}"
            pages[uri] = (f"https://publisher.example/{gap}/{n}", GAP_PAGES[gap])
            uris.append(uri)
        redirects[query] = uris
    grounded = GeminiGroundedSearch(_grounded_llm(redirects), max_calls=12)

    class NoFreeResults:
        def search(self, query, **kwargs):
            return {"results": [], "provider_stats": {"newsapi": {"status": "out_of_coverage"},
                                                      "gdelt": {"status": "failed", "outcome": "RATE_LIMITED"}}}

    service = HypothesisEvidenceService(web_search_service=NoFreeResults(),
                                        source_fetch_service=_real_fetch_service(pages), llm_service=GapModel())
    return service, CombinedSearch(NoFreeResults(), grounded), grounded


def test_grounded_link_redirect_fetch_date_and_quote_become_evidence():
    service, search, grounded = _gap_setup()
    receipt = service.investigate_gaps(development=DEVELOPMENTS[0], as_of="2026-10-09T00:00:00+00:00", search=search)
    assert receipt["found_by_gap"] == {"precedent": 1, "policy": 1, "dependency": 1, "numbers": 1,
                                       "commentary": 1}, receipt
    for record in receipt["records"]:
        assert record["source_url"].startswith("https://publisher.example/")   # redirect followed
        assert record["published_at"]                                         # date from page metadata
    assert grounded.calls == 5 and len(grounded.web_search_queries) == 10     # prompts vs Google queries


def test_successful_gap_research_is_complete_without_challenge_searches():
    service, search, _ = _gap_setup()
    receipt = service.investigate_gaps(development=DEVELOPMENTS[0], as_of="2026-10-09T00:00:00+00:00", search=search)
    assert set(receipt["gap_status"]) == {"precedent", "policy", "dependency", "numbers", "commentary"}
    assert all(status in {"complete", "complete_with_limitations"} for status in receipt["gap_status"].values())
    assert receipt["status"] in {"complete", "complete_with_limitations"}
    assert not any("required_challenge" in note or "counterevidence" in note for note in receipt["limitations"])


def test_policy_source_older_than_two_years_is_still_accepted():
    service, search, _ = _gap_setup()
    receipt = service.investigate_gaps(development=DEVELOPMENTS[0], as_of="2026-10-09T00:00:00+00:00", search=search)
    rule = next(r for r in receipt["records"] if r["gap_type"] == "policy")
    assert rule["published_at"].startswith("2021")


def test_fetch_budget_reaches_every_gap_type():
    # Four gaps x two results each = 8 candidates, 5 fetches: every gap gets its top result first.
    service, search, _ = _gap_setup(results_per_query=2)
    receipt = service.investigate_gaps(development=DEVELOPMENTS[0], as_of="2026-10-09T00:00:00+00:00", search=search)
    assert all(receipt["found_by_gap"][gap] >= 1 for gap in GAP_QUERIES), receipt["found_by_gap"]


def test_search_uses_a_model_that_supports_google_search():
    from services.grounded_search import DEFAULT_SEARCH_MODEL, GeminiGroundedSearch
    from types import SimpleNamespace as NS
    llm = NS(_client=object(), _types=NS(Tool=1, GoogleSearch=1), model_name="gemini-3.1-flash-lite")
    assert GeminiGroundedSearch(llm).model_name == DEFAULT_SEARCH_MODEL
    assert GeminiGroundedSearch(NS(_client=object(), _types=NS(), model_name="gemini-3.6-flash")).model_name == DEFAULT_SEARCH_MODEL
    assert GeminiGroundedSearch(llm, model_name="gemini-2.5-flash").model_name == "gemini-2.5-flash"


def test_grounded_search_honours_the_timeout():
    import time as _time
    from types import SimpleNamespace as NS
    from services.grounded_search import GeminiGroundedSearch
    seen = {}

    class Slow:
        def generate_content(self, *, model, contents, config):
            seen["config"] = config
            _time.sleep(0.6)
            return NS(candidates=[])

    types_ = NS(GenerateContentConfig=lambda **k: k, Tool=lambda **k: k, GoogleSearch=lambda: "gs",
                HttpOptions=lambda **k: k)
    grounded = GeminiGroundedSearch(NS(_client=NS(models=Slow()), _types=types_, model_name="gemini-3.5-flash-lite"))
    started = _time.monotonic()
    out = grounded.search("slow query", timeout_seconds=0.05)
    assert _time.monotonic() - started < 0.4
    assert out["provider_stats"]["gemini_search"]["outcome"] == "TIMEOUT" and out["results"] == []
    assert seen["config"]["http_options"] == {"timeout": 50}       # passed to the SDK in milliseconds
