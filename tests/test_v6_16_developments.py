"""
v6.16: groups need a shared subject, every top development is researched with
short queries written from its events, connections are checked part by part,
and a run with no insight still shows its strongest developments.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from agents.extraction_agent_v6 import ExtractionAgentV6 as Agent
from services import claim_support_gate as gate
from services import grounding_service
from services import research_orchestrator_service as module
from services.research_orchestrator_service import ResearchOrchestratorService as R


# ----------------------------------------------------------------------
# 1. A shared deal type is not one development
# ----------------------------------------------------------------------

def test_deal_type_alone_is_not_a_specific_subject():
    assert not Agent.names_specific_subject("raising venture capital funding rounds", "")
    assert not Agent.names_specific_subject("raising venture capital funding for AI startups", "AI startups")
    assert not Agent.names_specific_subject("funding rounds", "enterprise AI")
    assert Agent.names_specific_subject("banks adopting AI for loan underwriting", "AI for loan underwriting")
    assert Agent.names_specific_subject("new export rules on AI chips", "AI chip export licences")


class Reply:
    model_name = "stub"

    def __init__(self, development, subject, members):
        self.development, self.subject, self.members = development, subject, members

    def ask(self, prompt, system_prompt=None, **kwargs):
        return json.dumps({"groups": [{
            "group": 0, "verdict": "shared", "development": self.development, "subject": self.subject,
            "members": self.members}]})


# Two unrelated funding rounds (the 14:39 failure).
FUNDING = [[
    {"id": "EVENT-1", "actor": "Ascerta", "event_type": "funding",
     "text": "Ascerta raised $18 million to help companies measure returns on AI spending."},
    {"id": "EVENT-2", "actor": "EliseAI", "event_type": "funding",
     "text": "EliseAI raised $350 million for AI tools used by property managers."},
]]
FUNDING_QUOTES = [{"event": "E0.1", "quote": "raised $18 million", "subject_quote": "raised $18 million"},
                  {"event": "E0.2", "quote": "raised $350 million", "subject_quote": "raised $350 million"}]

# Two banks doing the same specific thing.
UNDERWRITING = [[
    {"id": "EVENT-3", "actor": "HDFC Bank", "event_type": "deployment",
     "text": "HDFC Bank deployed an AI model for small-business loan underwriting across 400 branches."},
    {"id": "EVENT-4", "actor": "Axis Bank", "event_type": "deployment",
     "text": "Axis Bank began using AI underwriting for personal loans, cutting approval time to minutes."},
]]
UNDERWRITING_QUOTES = [
    {"event": "E0.1", "quote": "deployed an AI model", "subject_quote": "small-business loan underwriting"},
    {"event": "E0.2", "quote": "began using AI underwriting", "subject_quote": "AI underwriting for personal loans"},
]


def verify(reply, group):
    return Agent(llm_service=reply, entity_tagger=None).verify_shared_development(group)[0]


def test_funding_rounds_group_is_rejected_even_when_the_model_says_shared():
    result = verify(Reply("raising venture capital funding rounds", "AI startups", FUNDING_QUOTES), FUNDING)
    assert result["verified"] is False and result["rejection"] == "shared_deal_type_only"


def test_invented_specific_subject_for_unrelated_funding_rounds_is_rejected():
    # The model names a specific-sounding subject, but the member quotes only
    # show the money, so the subject is not shown in the events.
    result = verify(Reply("startups raising money for AI ROI measurement",
                          "tools that measure returns on AI spending", FUNDING_QUOTES), FUNDING)
    assert result["verified"] is False and result["rejection"] == "subject_not_shown_in_two_members"
    # Same with "enterprise AI monetization" (GPT's example).
    result = verify(Reply("startups monetizing enterprise AI", "enterprise AI monetization", FUNDING_QUOTES), FUNDING)
    assert result["verified"] is False


def test_subject_quoted_from_only_one_member_is_rejected():
    quotes = [dict(FUNDING_QUOTES[0], subject_quote="measure returns on AI spending"),
              dict(FUNDING_QUOTES[1], subject_quote="AI tools used by property managers")]
    result = verify(Reply("startups raising money for AI ROI measurement",
                          "tools that measure returns on AI spending", quotes), FUNDING)
    assert result["verified"] is False and result["rejection"] == "subject_not_shown_in_two_members"


def test_subject_stitched_from_two_unrelated_events_is_rejected():
    quotes = [dict(FUNDING_QUOTES[0], subject_quote="measure returns on AI spending"),
              dict(FUNDING_QUOTES[1], subject_quote="property managers")]
    result = verify(Reply("AI for ROI measurement and property management",
                          "AI ROI measurement and property management", quotes), FUNDING)
    assert result["verified"] is False


def test_subject_quote_not_in_the_event_text_is_rejected():
    quotes = [dict(UNDERWRITING_QUOTES[0]), dict(UNDERWRITING_QUOTES[1], subject_quote="mortgage underwriting")]
    result = verify(Reply("banks adopting AI for loan underwriting", "AI loan underwriting", quotes), UNDERWRITING)
    assert result["verified"] is False


def test_genuinely_shared_subject_is_verified():
    result = verify(Reply("banks adopting AI for loan underwriting", "AI loan underwriting",
                          UNDERWRITING_QUOTES), UNDERWRITING)
    assert result["verified"] is True
    assert result["members"] == ["EVENT-3", "EVENT-4"]
    assert set(result["subject_evidence"]) == {"EVENT-3", "EVENT-4"}


def test_prompt_asks_for_the_shared_subject():
    agent = Agent(llm_service=Reply("x", "y", []), entity_tagger=None)
    prompt = agent._build_shared_development_prompt([(0, FUNDING[0])])
    assert '"subject"' in prompt and '"subject_quote"' in prompt
    assert "NOT one development" in agent._build_shared_development_system()
    assert agent.SHARED_DEVELOPMENT_PROMPT_VERSION == "shared-development-v6.16.1-subject-quotes"


# ----------------------------------------------------------------------
# 2. Research queries
# ----------------------------------------------------------------------

PROFILE = {
    "cluster_id": "dev_ai_roi",
    "cluster_label": "measuring returns on AI spending — Ascerta Inc.",
    "cluster_terms": [],
    "actors": ["Salesforce", "AppDirect"],
    "mechanism_tags": ["compute_capacity", "customer_commitment"],
    "dependency_tags": ["compute_supply", "customer_demand"],
    "event_ids": ["EVENT-1", "EVENT-2"],
    "missing_dimensions": ["magnitude_or_capacity"],
}
EVENTS = [
    {"event_id": "EVENT-1", "title": "Salesforce agrees to buy AppDirect to sell AI agents through partners",
     "event_start_at": "2026-10-01"},
    {"event_id": "EVENT-2", "title": "AppDirect marketplace adds Salesforce Agentforce", "event_start_at": "2026-10-02"},
]


class QueryLLM:
    timeout_seconds = 60
    max_retries = 3

    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def ask(self, prompt, **kwargs):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def orchestrator(llm):
    instance = R.__new__(R)
    instance.extraction_agent = SimpleNamespace(llm=llm)
    instance.execution_budget_service = None
    return instance


def test_template_queries_are_short_and_carry_no_taxonomy_tags():
    for round_number in (1, 2, 3, 4):
        queries = R._cluster_expansion_queries(topic="artificial intelligence", profile=PROFILE, round_number=round_number)
        assert queries
        for query in queries:
            assert len(query.split()) <= 8, query
            assert "compute" not in query and "commitment" not in query and "milestones" not in query


def test_model_written_queries_are_used():
    llm = QueryLLM(json.dumps({"subject": "AI agent marketplaces",
                               "queries": ["Salesforce AppDirect acquisition", "AppDirect AI agent marketplace",
                                           "Salesforce AppDirect antitrust"]}))
    queries, source = orchestrator(llm)._cluster_research_queries(
        topic="artificial intelligence", profile=PROFILE, events=EVENTS, round_number=1, used=())
    assert source == "llm"
    assert queries == ["Salesforce AppDirect acquisition", "AppDirect AI agent marketplace",
                       "Salesforce AppDirect antitrust"]
    assert "Salesforce agrees to buy AppDirect" in llm.prompts[0]


def test_model_failure_falls_back_to_the_template():
    queries, source = orchestrator(QueryLLM(TimeoutError("slow")))._cluster_research_queries(
        topic="artificial intelligence", profile=PROFILE, events=EVENTS, round_number=1, used=())
    assert source == "template" and queries


def test_queries_already_sent_are_not_repeated():
    llm = QueryLLM(json.dumps({"queries": ["Salesforce AppDirect acquisition", "AppDirect partner channel"]}))
    queries, _ = orchestrator(llm)._cluster_research_queries(
        topic="artificial intelligence", profile=PROFILE, events=EVENTS, round_number=2,
        used={"Salesforce AppDirect acquisition"})
    assert queries == ["AppDirect partner channel"]


def test_invalid_model_queries_are_dropped():
    kept = R._valid_cluster_queries([
        "AI",                                             # too short
        "latest developments outcomes",                   # generic only
        "one two three four five six seven eight nine",   # too long
        'Okta "agent kill switch',                         # broken quote
        "Okta AI agent kill switch",
        "okta ai agent kill switch",                       # duplicate
    ])
    assert kept == ["Okta AI agent kill switch"]


# ----------------------------------------------------------------------
# 3. Connections checked part by part
# ----------------------------------------------------------------------

LEAK = ("While vertical AI startups like EliseAI and Arceus Legal secure massive funding rounds, the concurrent "
        "absence of enterprise tools to quantify AI ROI creates a direct demand for platforms like Ascerta.")


def test_connection_is_split_into_clauses():
    parts = gate.connection_parts(LEAK)
    assert len(parts) == 2
    assert parts[1].startswith("the concurrent absence")


def test_connection_gets_a_link_claim_that_must_pass():
    connection = {"statement": LEAK, "evidence_ids": ["EVID-A", "EVID-B", "EVID-C"]}
    claims = gate.display_claims({"insight_revision_id": "C"}, [], [connection])
    texts = [claim["claim_text"] for claim in claims]
    assert any(text.startswith(gate.CONNECTION_LINK_PREFIX) for text in texts)
    link = next(claim for claim in claims if claim["claim_text"].startswith(gate.CONNECTION_LINK_PREFIX))
    assert link["multi_evidence_required"] is True
    statuses = {claim["claim_id"]: "supported" for claim in claims}
    statuses[link["claim_id"]] = "partially_supported"
    _, kept, notes = gate.sanitize_display({"insight_revision_id": "C"}, claims, statuses, [connection])
    assert kept == [] and notes["connections_removed"] == 1
    statuses[link["claim_id"]] = "inference_supported"
    _, kept, _ = gate.sanitize_display({"insight_revision_id": "C"}, claims, statuses, [connection])
    assert len(kept) == 1


def test_connection_needs_two_events_and_two_sources():
    evidence = [
        {"evidence_id": "EVID-A", "event_id": "EVENT-1", "source_id": "SOURCE-1"},
        {"evidence_id": "EVID-B", "event_id": "EVENT-1", "source_id": "SOURCE-2"},
        {"evidence_id": "EVID-C", "event_id": "EVENT-2", "source_id": "SOURCE-1"},
        {"evidence_id": "EVID-D", "event_id": "EVENT-3", "source_id": "SOURCE-3"},
    ]
    kept, dropped = R._connections_with_independent_support([
        {"statement": "one event, two sources", "evidence_ids": ["EVID-A", "EVID-B"]},
        {"statement": "two events, one source", "evidence_ids": ["EVID-A", "EVID-C"]},
        {"statement": "two events, two sources", "evidence_ids": ["EVID-A", "EVID-D"]},
    ], evidence)
    assert [item["statement"] for item in kept] == ["two events, two sources"] and dropped == 2


def test_two_reports_of_one_underlying_development_count_once():
    evidence = [
        {"evidence_id": "EVID-A", "event_id": "EVENT-1", "source_id": "SOURCE-1"},
        {"evidence_id": "EVID-B", "event_id": "EVENT-2", "source_id": "SOURCE-2"},
        {"evidence_id": "EVID-C", "event_id": "EVENT-3", "source_id": "SOURCE-3"},
    ]
    events = [
        {"event_id": "EVENT-1", "underlying_event_id": "UNDERLYING-X"},
        {"event_id": "EVENT-2", "underlying_event_id": "UNDERLYING-X"},   # same story, other outlet
        {"event_id": "EVENT-3", "underlying_event_id": "UNDERLYING-Y"},
    ]
    kept, dropped = R._connections_with_independent_support([
        {"statement": "duplicate reports", "evidence_ids": ["EVID-A", "EVID-B"]},
        {"statement": "two developments", "evidence_ids": ["EVID-A", "EVID-C"]},
    ], evidence, events)
    assert [item["statement"] for item in kept] == ["two developments"] and dropped == 1


def test_grounding_treats_a_company_pitch_as_the_company_claim():
    assert grounding_service.GROUNDING_PROMPT_VERSION == "grounding-v1.2-company-claims"
    service = grounding_service.GroundingService.__new__(grounding_service.GroundingService)
    prompt = grounding_service.GroundingService._build_prompt(
        service, claim_id="C1", claim_text="Demand exists for Ascerta.", epistemic_type="fact",
        evidence_records=[], multi_evidence_required=False)
    assert "Company claims" in prompt and "company's" in prompt


# ----------------------------------------------------------------------
# 4. Developments to watch
# ----------------------------------------------------------------------

def development_events():
    return [
        {"event_id": "EVENT-1", "underlying_event_id": "U1", "strategic_cluster_id": "dev_agents",
         "strategic_cluster_status": "verified", "strategic_cluster_development": "companies adding kill switches for AI agents",
         "primary_actor": "Okta", "acting_entity": "Okta", "event_start_at": "2026-09-29", "strategic_materiality_score": 3.0},
        {"event_id": "EVENT-2", "underlying_event_id": "U2", "strategic_cluster_id": "dev_agents",
         "strategic_cluster_status": "verified", "strategic_cluster_development": "companies adding kill switches for AI agents",
         "primary_actor": "Equals Money", "acting_entity": "Equals Money", "event_start_at": "2026-10-01",
         "strategic_materiality_score": 2.0},
        {"event_id": "EVENT-3", "underlying_event_id": "U3", "strategic_cluster_id": "dev_single",
         "strategic_cluster_status": "single", "title": "Anthropic files for an IPO",
         "primary_actor": "Anthropic", "acting_entity": "Anthropic", "event_start_at": "2026-10-02",
         "strategic_materiality_score": 4.0},
        {"event_id": "EVENT-4", "underlying_event_id": "U4", "title": "No evidence for this one"},
    ]


def development_evidence():
    return [
        {"evidence_id": "EVID-1", "event_id": "EVENT-1", "source_id": "SOURCE-1", "evidence_type": "source_excerpt",
         "claim": "Okta added a kill switch that stops an AI agent's access in real time."},
        {"evidence_id": "EVID-2", "event_id": "EVENT-2", "source_id": "SOURCE-2", "evidence_type": "source_excerpt",
         "claim": "Equals Money limits its MCP server so AI agents can read data but not make payments."},
        {"evidence_id": "EVID-3", "event_id": "EVENT-3", "source_id": "SOURCE-3", "evidence_type": "source_excerpt",
         "claim": "Anthropic has filed confidentially for an initial public offering, according to a report."},
    ]


def test_developments_list_verified_groups_first_with_source_statements():
    instance = R.__new__(R)
    instance.article_service = None
    output = instance._developments_to_watch(
        development_events(), development_evidence(), {"cluster_search_added_event_ids": ["EVENT-2"]})
    assert [item["kind"] for item in output] == ["shared_development", "single_event"]
    first = output[0]
    assert first["title"] == "companies adding kill switches for AI agents"
    assert {fact["evidence_id"] for fact in first["facts"]} == {"EVID-1", "EVID-2"}
    assert first["events_found_by_follow_up_search"] == 1
    assert any(fact["found_by_follow_up_search"] for fact in first["facts"])
    assert any(entry.startswith("Problems") for entry in first["what_to_watch"])
    single = output[1]
    assert any(entry.startswith("Independent confirmation") for entry in single["what_to_watch"])
    assert all(fact["text"] for item in output for fact in item["facts"])


def test_developments_show_only_source_statements():
    instance = R.__new__(R)
    instance.article_service = None
    evidence = development_evidence() + [
        {"evidence_id": "EVID-REL", "event_id": "EVENT-1", "source_id": "SOURCE-1",
         "evidence_type": "relationship_assertion",
         "claim": "Okta's kill switch shows that enterprises are shifting control of autonomous agents "
                  "from model vendors to identity providers across the whole security market."},
        {"evidence_id": "EVID-ANALYSIS", "event_id": "EVENT-1", "source_id": "SOURCE-1",
         "evidence_type": "institutional_analysis", "claim": "Analysts expect most enterprises to adopt agent kill switches."},
        {"evidence_id": "EVID-QTY", "event_id": "EVENT-2", "source_id": "SOURCE-2", "evidence_type": "quantitative_claim",
         "text": "Equals Money payments value: 0 agent-initiated payments (2026)",
         "source_excerpt": "Equals Money said no payments can be initiated by AI agents through the server."},
        {"evidence_id": "EVID-QTY-NOEXCERPT", "event_id": "EVENT-2", "source_id": "SOURCE-2",
         "evidence_type": "quantitative_claim", "text": "Equals Money server count: 1 constructed statement only"},
    ]
    output = instance._developments_to_watch(development_events(), evidence, {})
    shown = {fact["evidence_id"]: fact["text"] for item in output for fact in item["facts"]}
    assert "EVID-REL" not in shown and "EVID-ANALYSIS" not in shown and "EVID-QTY-NOEXCERPT" not in shown
    assert shown["EVID-1"].startswith("Okta added a kill switch")
    # A quantity is shown as the source's own excerpt, not the constructed statement.
    assert shown["EVID-QTY"] == "Equals Money said no payments can be initiated by AI agents through the server."


def test_development_without_any_source_statement_is_not_shown():
    instance = R.__new__(R)
    instance.article_service = None
    evidence = [{"evidence_id": "EVID-REL", "event_id": "EVENT-3", "source_id": "SOURCE-3",
                 "evidence_type": "relationship_assertion",
                 "claim": "Anthropic's IPO filing implies a wave of AI lab listings next year."}]
    assert instance._developments_to_watch(development_events(), evidence, {}) == []


def test_developments_use_article_titles_when_available():
    instance = R.__new__(R)
    instance.article_service = SimpleNamespace(get_by_source_id=lambda source_id: SimpleNamespace(
        title="Okta adds AI agent kill switch", publisher="SiliconANGLE",
        canonical_url="https://example.com/okta", published_at="2026-09-29"))
    output = instance._developments_to_watch(development_events(), development_evidence(), {})
    reference = output[0]["sources"][0]
    assert reference["publisher"] == "SiliconANGLE" and reference["url"] == "https://example.com/okta"


def test_developments_never_break_the_report():
    instance = R.__new__(R)
    instance.article_service = SimpleNamespace(get_by_source_id=lambda source_id: 1 / 0)
    output = instance._developments_to_watch(development_events(), development_evidence(), {})
    assert output and output[0]["sources"][0]["url"] == ""
    assert R._developments_to_watch(instance, [None, 5], "not evidence", {}) == []


def test_abstention_report_carries_developments_to_watch(tmp_path, monkeypatch):
    import time
    monkeypatch.chdir(tmp_path)
    obj = object.__new__(R)
    obj.report_service = None
    obj.policy_enforcement_service = None
    obj.article_service = None
    for name in ("telemetry_service", "insight_registry_service", "audit_receipt_service",
                 "execution_budget_service", "insight_trace_service"):
        setattr(obj, name, None)
    state = {"warnings": [], "claim_support_gate": {"attempts": []}}
    obj._complete_abstention(
        state=state, topic="artificial intelligence", run_id="RUN-T16", depth="deep",
        events=development_events()[:3], evidence=development_evidence(), constraints={}, graph_context={},
        reasons=["claim_support_gate:x"], started_at=time.monotonic(), stage="claim_support_gate")
    developments = state["report"]["developments_to_watch"]
    assert [item["kind"] for item in developments] == ["shared_development", "single_event"]
