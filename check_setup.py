"""One-call pre-check before spending a Deep run (v6.19).

Makes ONE Google-grounded Gemini call and fetches ONE returned page, then
reports whether your API key and model support Google Search grounding and
whether the fetched page yields a publication date. Cost: one grounded prompt
(it may run one or two Google queries) and no other model calls.

Usage (project folder, venv active):
    python check_setup.py
"""

from __future__ import annotations

import sys


def main() -> int:
    from services.gemini_llm_service import GeminiLLMService
    from services.grounded_search import GeminiGroundedSearch
    from services.source_fetch_service import SourceFetchService

    try:
        llm = GeminiLLMService()
    except Exception as error:
        print(f"FAIL  Gemini service could not start: {type(error).__name__}: {error}")
        return 1
    search = GeminiGroundedSearch(llm, max_calls=1)
    print(f"Search model: {search.model_name}")
    if not search.available:
        print("FAIL  This google-genai SDK has no Google Search tool. Update it: pip install -U google-genai")
        return 1
    result = search.search("central bank interest rate decision", timeout_seconds=40)
    stats = result["provider_stats"].get("gemini_search", {})
    if stats.get("status") != "ok":
        print(f"FAIL  Google-grounded call failed: {stats}. Check that the key's project allows "
              f"Search grounding for {search.model_name}, or set GEMINI_SEARCH_MODEL to a model on Google's list.")
        return 1
    print(f"OK    Google Search grounding works: {len(result['results'])} links, "
          f"{len(search.web_search_queries)} Google queries run.")
    if not result["results"]:
        print("WARN  No links returned for the test query; grounding works but returned no sources.")
        return 0
    fetcher = SourceFetchService()
    for item in result["results"][:3]:
        try:
            page = fetcher.fetch(item["url"])
        except Exception as error:
            print(f"WARN  Could not fetch {item.get('title') or item['url'][:60]}: {type(error).__name__}")
            continue
        metadata = page.get("metadata") or {}
        published = page.get("published_at") or metadata.get("published_at") or ""
        text = page.get("text_content") or ""
        print(f"OK    Fetched {page.get('canonical_url') or item['url']}")
        print(f"      text: {len(text)} characters; publication date: {published or 'NOT FOUND'}")
        if published:
            print("OK    Pages from Google links can become evidence. You can run Deep.")
        else:
            print("WARN  This page had no date tag; such pages are only used for policy facts that "
                  "state an in-force date. Try running Deep; the log line gap_research= will show yields.")
        return 0
    print("WARN  None of the returned pages could be fetched (they may block automated access).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
