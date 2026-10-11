"""
Research completeness is judged per search family (challenge / support /
history), from per-source outcomes. Offline.

Run:  python -m tests.test_research_completeness
"""

from __future__ import annotations

from agents.evidence_discovery_agent import EvidenceDiscoveryAgent
from services.hypothesis_evidence_service import _ResearchSearch, search_assessment

status_of = EvidenceDiscoveryAgent._search_status


def stats(**outcomes):
    status = {"ok": "ok", "OUT_OF_COVERAGE": "out_of_coverage"}
    return {name: {"status": status.get(o, "failed"), "outcome": o} for name, o in outcomes.items()}


def trace(mode, provider_stats, fetches=()):
    return {"query_id": f"Q-{mode}", "mode": mode, "query": mode, "search_status": status_of(provider_stats),
            "provider_stats": provider_stats, "source_fetches": list(fetches)}


def discovery(*traces, evidence=(), unexecuted=()):
    return {"status": "complete", "search_manifest": list(traces), "discovered_evidence": list(evidence),
            "unexecuted_queries": list(unexecuted), "unprocessed_search_results": []}


COUNTER_A = stats(rss="ok", newsapi="ok", gdelt="RATE_LIMITED")          # GPT's example A
HISTORY_B = stats(newsapi="OUT_OF_COVERAGE", rss="OUT_OF_COVERAGE", gdelt="TIMEOUT", exa="TIMEOUT")  # example B


def test_one_rate_limited_source_is_a_limitation_not_incomplete_research():
    assert status_of(COUNTER_A) == "complete_with_limitations"
    result = search_assessment(discovery(trace("counterevidence", COUNTER_A),
                                         trace("alternative_explanation", stats(rss="ok"))))
    assert result["status"] == "completed_none_found"
    assert result["completeness"] == "complete_with_limitations"
    assert "counterevidence: gdelt: RATE_LIMITED" in result["limitations"]
    assert "Limitations:" in result["note"]


def test_failed_history_search_does_not_contaminate_the_counterevidence_verdict():
    assert status_of(HISTORY_B) == "incomplete"
    result = search_assessment(discovery(
        trace("counterevidence", stats(rss="ok")), trace("alternative_explanation", stats(rss="ok")),
        trace("direct_precedent", HISTORY_B),
    ))
    assert result["status"] == "completed_none_found"                 # challenge family finished
    assert result["families"]["history"]["status"] == "incomplete"     # history did not
    assert any(x.startswith("history: direct_precedent") for x in result["limitations"])


def test_out_of_coverage_sources_do_not_count_either_way():
    assert status_of(stats(newsapi="OUT_OF_COVERAGE", gdelt="ok")) == "complete"
    assert status_of(stats(newsapi="OUT_OF_COVERAGE")) == "incomplete"   # nothing could search it


def test_challenge_search_with_no_answering_source_is_incomplete():
    result = search_assessment(discovery(
        trace("counterevidence", stats(gdelt="RATE_LIMITED", rss="SOURCE_ERROR")),
        trace("alternative_explanation", stats(rss="ok")),
    ))
    assert result["status"] == "incomplete"


def test_unfetchable_page_is_a_limitation_but_an_unreviewed_document_blocks_none_found():
    unfetched = {"url": "https://x.example", "fetch_status": "failed", "processing_status": None}
    unreviewed = {"url": "https://y.example", "fetch_status": "complete", "processing_status": "incomplete"}
    ok = [trace("alternative_explanation", stats(rss="ok"))]
    limited = search_assessment(discovery(trace("counterevidence", stats(rss="ok"), [unfetched]), *ok))
    assert limited["status"] == "completed_none_found" and limited["completeness"] == "complete_with_limitations"
    # A document found by a support search could still be the counterevidence.
    blocked = search_assessment(discovery(trace("counterevidence", stats(rss="ok")), *ok,
                                          trace("feasibility", stats(rss="ok"), [unreviewed])))
    assert blocked["status"] == "incomplete"


def test_challenge_query_not_run_for_budget_is_incomplete():
    result = search_assessment(discovery(
        trace("counterevidence", stats(rss="ok")),
        unexecuted=[{"mode": "alternative_explanation", "reason": "query_budget_exhausted"}],
    ))
    assert result["status"] == "incomplete"


def test_research_search_asks_sources_for_one_attempt():
    seen = {}

    class Inner:
        def search(self, query, **kwargs):
            seen.update(kwargs)
            return {"results": []}

    _ResearchSearch(Inner()).search("q", max_results=2)
    assert seen["max_retries"] == 0 and seen["max_results"] == 2


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
