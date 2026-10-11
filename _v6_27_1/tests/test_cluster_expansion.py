"""
Goal-driven cluster expansion: Deep mode keeps searching around a promising
group until it holds enough distinct events (DEEP_CORPUS_MIN_COHERENT_CLUSTER_EVENTS,
default 4, from >= 2 sources, verified as one shared development), or the
search saturates (DEEP_CLUSTER_EXPANSION_PATIENCE rounds without growth), or a
round / time / provider budget runs out.

Search, fetch, extraction and evidence building are replaced by scripted
stand-ins; admission, clustering, the shared-development check (stub model)
and the expansion loop run for real.

Run:  python -m tests.test_cluster_expansion
"""

from __future__ import annotations

import services.research_orchestrator_service as module
from services.research_orchestrator_service import ResearchOrchestratorService as R
from tests.test_development_clusters import CHIPS, UNDERWRITING, event, evidence_for, orchestrator

TOPIC = "artificial intelligence"
MORE_UNDERWRITING = [
    event("EVENT-A4", "ICICI Bank", "ICICI Bank deployed an AI model for credit underwriting of loans.", source="SOURCE-A4"),
    event("EVENT-A5", "Yes Bank", "Yes Bank launched generative AI loan underwriting for small firms.", source="SOURCE-A5"),
    event("EVENT-A6", "IDFC Bank", "IDFC Bank adopted AI underwriting for retail loan approvals.", source="SOURCE-A6"),
]


def harness(script, *, circuit_open=False):
    """script: list of rounds; each round is the list of events its search finds."""
    instance = orchestrator()
    calls = {"queries": [], "rounds": 0}
    by_source = {}

    def discover(*, topic, max_sources, parent_state, discovery_state, discovery_queries_override, discovery_phase):
        calls["queries"].append(list(discovery_queries_override))
        round_events = script[calls["rounds"]] if calls["rounds"] < len(script) else []
        calls["rounds"] += 1
        for item in round_events:
            by_source[item["source_id"]] = item
        return [item["source_id"] for item in round_events]

    instance._discover_sources_with_run_circuit_breaker = discover
    instance._claim_run_source_ids = lambda *, state, source_ids, phase: (list(source_ids), [])
    instance._extract_events = lambda *, source_ids, state: [dict(by_source[s]) for s in source_ids]
    instance._register_run_event_ids = lambda **kwargs: None
    instance._source_payloads = lambda source_ids: []
    instance._capture_analysis_without_events = lambda *, topic, sources, existing_evidence, state, phase: (existing_evidence, 0)
    instance._build_evidence_with_authoritative_analysis = lambda **kwargs: {"evidence_records": evidence_for(kwargs["events"])}
    instance._validate_evidence_currency = lambda records: None
    instance._fresh_discovery_circuit_is_open = lambda state: circuit_open
    return instance, calls


def seed(instance, events):
    selected, evidence, _ = instance._prepare_strategic_event_context(
        topic=TOPIC, events=events, evidence=evidence_for(events))
    return selected, evidence


def expand(instance, events, evidence):
    state = {"run_id": "RUN-T", "warnings": []}
    result = instance._expand_promising_strategic_clusters(
        topic=TOPIC, selected_source_ids=[e["source_id"] for e in events],
        event_records=events, evidence_records=evidence, state=state)
    return result, result["cluster_runs"][0] if result.get("cluster_runs") else {}


def group_size(result, cluster_id):
    return len({e["event_id"] for e in result["event_records"] if e.get("strategic_cluster_id") == cluster_id})


# ----------------------------------------------------------------------

def test_keeps_searching_until_the_group_has_enough_events():
    instance, calls = harness([[MORE_UNDERWRITING[0]], [MORE_UNDERWRITING[1]], [MORE_UNDERWRITING[2]]])
    events, evidence = seed(instance, UNDERWRITING[:2])
    result, run = expand(instance, events, evidence)
    assert run["seed_basis"] == "verified_shared_development", run
    assert run["stop_reason"] == "target_group_size_reached", run["stop_reason"]
    assert calls["rounds"] == 2                     # 2 -> 3 -> 4 events, then stop
    assert group_size(result, run["cluster_id"]) == module.DEEP_CORPUS_MIN_COHERENT_CLUSTER_EVENTS
    assert [r["target_underlying_events_after"] for r in run["rounds"]] == [3, 4]


def test_a_round_that_does_not_grow_the_group_gets_another_angle():
    # Round 1 finds an unrelated chip event; round 2 (new angle) finds a member.
    instance, calls = harness([[CHIPS[0]], [MORE_UNDERWRITING[0]], [MORE_UNDERWRITING[1]]])
    events, evidence = seed(instance, UNDERWRITING[:2])
    result, run = expand(instance, events, evidence)
    assert run["stop_reason"] == "target_group_size_reached", run["stop_reason"]
    assert calls["rounds"] == 3


def test_stops_when_the_search_saturates():
    # Nothing new joins the group for PATIENCE rounds in a row.
    instance, calls = harness([[CHIPS[0]], [], [MORE_UNDERWRITING[0]]])
    events, evidence = seed(instance, UNDERWRITING[:2])
    _, run = expand(instance, events, evidence)
    assert run["saturated"] is True
    assert calls["rounds"] == module.DEEP_CLUSTER_EXPANSION_PATIENCE == 2
    assert run["stop_reason"] == "saturated_no_new_sources"


def test_provider_limit_stops_at_once():
    instance, calls = harness([[]], circuit_open=True)
    events, evidence = seed(instance, UNDERWRITING[:2])
    _, run = expand(instance, events, evidence)
    assert run["stop_reason"] == "saturated_provider_circuit_open" and calls["rounds"] == 1


def test_queries_are_never_repeated_and_later_rounds_drop_known_actors():
    instance, calls = harness([[CHIPS[0]], [MORE_UNDERWRITING[0]], [], [], [], []])
    original = module.DEEP_CLUSTER_EXPANSION_PATIENCE
    module.DEEP_CLUSTER_EXPANSION_PATIENCE = 6
    try:
        events, evidence = seed(instance, UNDERWRITING[:2])
        expand(instance, events, evidence)
    finally:
        module.DEEP_CLUSTER_EXPANSION_PATIENCE = original
    sent = [query for round_queries in calls["queries"] for query in round_queries]
    assert len(sent) == len(set(sent))
    assert calls["rounds"] >= 3
    assert not any('"HDFC Bank"' in R._anchor_discovery_query(TOPIC, q) for q in calls["queries"][2])


def test_group_already_large_enough_gets_one_context_round():
    # v6.16: a large group is still researched once (background, competitors,
    # problems), then the search stops.
    instance, calls = harness([[], []])
    events, evidence = seed(instance, UNDERWRITING + MORE_UNDERWRITING[:1])
    _, run = expand(instance, events, evidence)
    assert run["stop_reason"] == "target_group_size_reached" and calls["rounds"] == 1


def test_time_budget_stops_the_search():
    instance, calls = harness([[MORE_UNDERWRITING[0]]])
    original = module.DEEP_CLUSTER_EXPANSION_MAX_SECONDS
    module.DEEP_CLUSTER_EXPANSION_MAX_SECONDS = -1
    try:
        events, evidence = seed(instance, UNDERWRITING[:2])
        _, run = expand(instance, events, evidence)
    finally:
        module.DEEP_CLUSTER_EXPANSION_MAX_SECONDS = original
    assert run["stop_reason"] == "expansion_time_budget_exhausted" and calls["rounds"] == 0


def test_broad_search_hands_over_to_group_search_once_wide_enough():
    # Breadth targets met (events, distinct events, sources) and a verified
    # group exists, but no group is big enough yet: broad refill stops and
    # targeted group search takes the remaining search budget.
    instance, _ = harness([])
    filler = [event(f"EVENT-F{n}", f"Firm {n}", f"Firm {n} opened an AI office in city number {n}.",
                    source=f"SOURCE-F{n}") for n in range(1, 11)]
    events, evidence = seed(instance, UNDERWRITING[:2] + filler)
    quality = R._strategic_corpus_sufficiency_assessment(topic=TOPIC, events=events, evidence=evidence)
    assert quality["sufficient"] is False, quality["reasons"]
    assert quality["ready_for_group_expansion"] is True
    state = {"run_id": "RUN-T", "warnings": []}
    result = instance._refill_strategic_corpus(
        topic=TOPIC, selected_source_ids=[e["source_id"] for e in events],
        event_records=events, evidence_records=evidence, state=state)
    assert result["stop_reason"] == "handed_to_group_expansion"
    assert result.get("attempted") is not True


if __name__ == "__main__":
    failures = 0
    for name, function in list(globals().items()):
        if name.startswith("test_") and callable(function):
            try:
                function()
                print(f"PASS {name}")
            except Exception as error:
                failures += 1
                print(f"FAIL {name}: {type(error).__name__}: {error}")
    raise SystemExit(1 if failures else 0)
