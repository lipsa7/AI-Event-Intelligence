"""v13.15: headlines are screened for real news events before fetching; Exa tops up and also helps
when free searches return little. Headlines are real ones from RUN-FF8F59D2 (20 Nov 2026)."""
from services import discovery_screen as ds
from services import exa_contents as exa

HEADLINES = [
    ("Andrew Garfield is Sam Altman in Guadagnino's 'Artificial' Full Trailer", "firstshowing.net"),
    ("Artificial Intelligence for the Z80", "dfsantos.com"),
    ("Australia to launch AI safety regulator after OpenAI's breach of a government portal", "abcnews.com"),
    ("Cambridge Dictionary Names 'Human' Its 2026 Word of the Year", "tech.slashdot.org"),
    ("Parents Sue Google Over AI Safety Risks On School Chromebooks", "tech.slashdot.org"),
    ("Federal Register :: Request Access", "federalregister.gov"),
]
CANDS = [{"title": t, "url": f"https://{site}/a{n}", "description": ""} for n, (t, site) in enumerate(HEADLINES)]


def test_one_call_drops_only_what_the_model_names_and_says_why():
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return {"skip": [{"n": 1, "why": "film trailer"}, {"n": 2, "why": "hobby project"},
                         {"n": 4, "why": "dictionary"}, {"n": 6, "why": "access page"}]}
    kept, summary = ds.screen("artificial intelligence", CANDS, ask=ask)
    assert len(prompts) == 1 and "REPORTS A CONCRETE NEWS EVENT" in prompts[0]
    assert [k["title"] for k in kept] == [HEADLINES[2][0], HEADLINES[4][0]]
    assert summary["dropped"] == 4 and summary["dropped_examples"][0]["why"] == "film trailer"


def test_screen_fails_open():
    def broken(prompt):
        raise RuntimeError("down")
    kept, summary = ds.screen("ai", CANDS, ask=broken)
    assert len(kept) == len(CANDS) and summary["status"].startswith("failed")
    kept, summary = ds.screen("ai", CANDS, ask=lambda p: {"skip": [{"n": n} for n in range(1, 7)]})
    assert len(kept) == len(CANDS) and summary["status"] == "kept_all:model_dropped_everything"
    kept, _ = ds.screen("ai", CANDS, ask=lambda p: {"skip": [{"n": 99}, {"n": "x"}]})
    assert len(kept) == len(CANDS)


def test_exa_top_up_adds_new_urls_only_when_short_and_screens_them():
    calls = []

    def search(query):
        calls.append(query)
        return [{"url": "https://abcnews.com/a2", "title": "dup", "snippet": ""},
                {"url": "https://reuters.com/x", "title": "EU fines AI firm", "snippet": "The Commission fined...",
                 "published_at": "2026-11-19"},
                {"url": "https://ign.com/y", "title": "Game review: AI villains", "snippet": ""}]
    kept = CANDS[2:3] + CANDS[4:5]
    added = ds.exa_top_up("artificial intelligence", kept, [], target=12, search=search,
                          ask=lambda p: {"skip": [{"n": 2, "why": "game review"}]})
    assert [a["url"] for a in added] == ["https://reuters.com/x"]
    assert added[0]["provider"] == "exa_search" and "news" in calls[0]
    assert ds.exa_top_up("ai", CANDS * 3, [], target=12, search=search) == []     # enough already


def test_combined_search_uses_exa_when_free_results_are_few_or_a_provider_failed(monkeypatch):
    from services.grounded_search import CombinedSearch
    monkeypatch.setenv("EXA_FALLBACK", "1")
    monkeypatch.setenv("EXA_API_KEY", "k")
    monkeypatch.setenv("EXA_MAX_CALLS_PER_RUN", "600")
    exa.reset_run_budget()
    seen = []
    monkeypatch.setattr(exa, "search", lambda q, **kw: seen.append(q) or [
        {"url": "https://reuters.com/z", "title": "Z", "provider": "exa_search"}])

    class Few:
        def search(self, query, **kw):
            return {"results": [{"url": "https://a.example/1"}], "provider_stats": {}}

    class RateLimited:
        def search(self, query, **kw):
            return {"results": [{"url": f"https://a.example/{n}"} for n in range(5)],
                    "provider_stats": {"gdelt": {"status": "failed", "outcome": "RATE_LIMITED"}}}

    out = CombinedSearch(Few(), None).search("q1")
    assert [r["url"] for r in out["results"]] == ["https://a.example/1", "https://reuters.com/z"]
    out = CombinedSearch(RateLimited(), None).search("q2")
    assert len(out["results"]) == 6 and seen == ["q1", "q2"]


def test_discovery_runs_the_screen_and_logs_it(monkeypatch):
    from services.research_orchestrator_service import ResearchOrchestratorService as S
    obj = object.__new__(S)
    monkeypatch.setattr(S, "_ask_briefing_model", lambda self, prompt: {"skip": [{"n": 1, "why": "film trailer"}]})
    state = {}
    kept = obj._screen_and_top_up_discovery(topic="artificial intelligence", candidates=CANDS, state=state,
                                            max_sources=4, queries=[], phase="initial")
    assert len(kept) == len(CANDS) - 1 and state["source_discovery_screen"][0]["dropped"] == 1, state
