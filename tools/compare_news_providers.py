"""
Bounded trial of news discovery providers (free ones by default).

Every provider gets the SAME queries, the SAME date window and the SAME request
budget, and is measured on its own (not merged):

  supply      requests, failures by type, median latency, results, unique URLs,
              distinct domains (independent sources), URLs no other provider
              found, date coverage, share of real publisher dates
  usable text a random sample of each provider's links is fetched directly from
              the publisher (no GDELT-context fallback) and counted usable when
              the page is real article text, not a stub/interstitial
  relevance   (a) the pipeline's own embedding relevance, reported separately and
              NOT treated as truth; (b) a blind human review sheet, provider
              hidden, scored afterwards with --score-review
  breadth     distinct headline groups (embedding proxy for distinct
              developments; real developments need the full pipeline)
  cost        0 for free providers; Exa uses the cost it reports (else estimate)

This measures evidence SUPPLY only. Whether a provider improves a specific
insight or forecast has to be tested separately, by running the same topic
through the full pipeline with each source set and comparing the reports.

Run from the project folder:
    python -m tools.compare_news_providers --topic "artificial intelligence"
    python -m tools.compare_news_providers --score-review evaluation/provider_trial/<run>/review_sheet.csv

Outputs go to evaluation/provider_trial/<timestamp>/: report.md, report.json,
review_sheet.csv (fill in the y/n columns), review_key.json (do not open while
reviewing: it says which provider each row came from).
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

OUT_ROOT = Path("evaluation") / "provider_trial"
USABLE_TEXT_MIN_CHARS = 1500
EXA_USD_PER_REQUEST_ESTIMATE = 0.007


def default_queries(topic: str) -> list[str]:
    """The same initial queries a Deep run sends (topic + anchored cue groups)."""
    try:
        from services.research_orchestrator_service import ResearchOrchestratorService as R
        return list(R._build_initial_discovery_queries(topic=topic, research_depth="deep", rotation=0))
    except Exception:
        return [topic, f"({topic}) AND (launch OR deployment OR partnership)",
                f"({topic}) AND (funding OR acquisition OR investment)",
                f"({topic}) AND (regulation OR approval OR ban)"]


def run_provider(provider: Any, queries: list[str], *, per_query: int, start: datetime, end: datetime) -> dict[str, Any]:
    calls, rows = [], []
    for query in queries:
        began = time.perf_counter()
        try:
            result = provider.search(
                query, limit=per_query, max_results=per_query, timeout_seconds=20.0,
                from_date=start.isoformat(), to_date=end.isoformat(),
            )
            items = [dict(r) for r in (result.get("results") or []) if isinstance(r, dict)]
            call = {"query": query, "status": "ok", "results": len(items),
                    "cost_usd": result.get("cost_estimate_usd")}
            for item in items:
                item["_query"] = query
            rows.extend(items)
        except Exception as error:
            call = {"query": query, "status": "failed", "error": type(error).__name__,
                    "message": str(error)[:200]}
        call.update(_timing(provider, began))
        calls.append(call)
    return {"calls": calls, "rows": rows}


def _timing(provider: Any, began: float) -> dict[str, Any]:
    """Wall time, plus HTTP attempts and deliberate waits when the provider
    reports them (GDELT spaces its requests and retries after a rate limit:
    the waits are its pacing, not its response time, so they are shown apart
    and retries count as extra HTTP requests)."""
    wall = round((time.perf_counter() - began) * 1000, 1)
    attempts = getattr(provider, "last_http_attempts", None)
    waited = float(getattr(provider, "last_wait_seconds", 0.0) or 0.0)
    return {
        "latency_ms": round(max(0.0, wall - waited * 1000), 1),
        "wall_ms": wall,
        "pacing_wait_ms": round(waited * 1000, 1),
        "http_attempts": int(attempts) if isinstance(attempts, (int, float)) else 1,
    }


def url_key(url: str) -> str:
    return (url or "").casefold().split("#", 1)[0].rstrip("/")


def supply_metrics(name: str, run: dict[str, Any], other_urls: set[str]) -> dict[str, Any]:
    calls, rows = run["calls"], run["rows"]
    urls = {url_key(r.get("url", "")) for r in rows if r.get("url")}
    dates = [r.get("published_at") for r in rows if r.get("published_at")]
    basis = Counter(r.get("published_at_basis") or ("publisher" if r.get("published_at") else "unknown") for r in rows)
    latencies = [c["latency_ms"] for c in calls]
    cost = sum(float(c.get("cost_usd") or 0) for c in calls)
    if name == "exa" and not cost:
        cost = EXA_USD_PER_REQUEST_ESTIMATE * sum(1 for c in calls if c["status"] == "ok")
    return {
        "requests": len(calls),
        "http_attempts": sum(int(c.get("http_attempts", 1)) for c in calls),
        "failures": sum(1 for c in calls if c["status"] != "ok"),
        "failure_types": dict(Counter(c.get("error") for c in calls if c["status"] != "ok")),
        "median_latency_ms": round(statistics.median(latencies), 1) if latencies else None,
        "pacing_wait_s": round(sum(float(c.get("pacing_wait_ms", 0)) for c in calls) / 1000, 1),
        "results": len(rows),
        "unique_urls": len(urls),
        "only_this_provider": len(urls - other_urls),
        "distinct_domains": len({r.get("domain") for r in rows if r.get("domain")}),
        "oldest": min(dates) if dates else "",
        "newest": max(dates) if dates else "",
        "date_basis": dict(basis),
        "cost_usd": round(cost, 4),
    }


def fetch_usable(rows: list[dict[str, Any]], sample: int, rng: random.Random) -> dict[str, Any]:
    try:
        from services.source_fetch_service_direct import SourceFetchService
    except Exception as error:  # pragma: no cover - project dependency
        return {"status": f"skipped: {type(error).__name__}: {error}"}
    try:
        from services.source_fetch_service import _looks_like_interstitial
    except Exception:
        def _looks_like_interstitial(text: str) -> bool:
            return len((text or "").strip()) < USABLE_TEXT_MIN_CHARS
    fetcher = SourceFetchService()
    picked = rng.sample(rows, min(sample, len(rows)))
    outcomes = []
    for row in picked:
        try:
            page = fetcher.fetch(row["url"], timeout_seconds=12.0)
            text = str(page.get("text_content") or "")
            usable = len(text) >= USABLE_TEXT_MIN_CHARS and not _looks_like_interstitial(text)
            outcomes.append({"url": row["url"], "usable": usable, "chars": len(text)})
        except Exception as error:
            outcomes.append({"url": row["url"], "usable": False, "error": type(error).__name__})
    usable = sum(1 for o in outcomes if o["usable"])
    return {"sampled": len(outcomes), "usable": usable,
            "usable_rate": round(usable / len(outcomes), 3) if outcomes else None,
            "errors": dict(Counter(o.get("error") for o in outcomes if o.get("error"))),
            "details": outcomes}


def pipeline_relevance_and_breadth(topic: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    # Embedding trouble (Ollama stopped, model missing, a timeout) must not
    # end the trial: this section is then reported as skipped.
    try:
        from services.research_orchestrator_service import ResearchOrchestratorService as R
        from services.vector_service import VectorService
        service = VectorService()
        if not service.available():
            return {"status": f"skipped: embeddings unavailable ({getattr(service, 'last_error', '')})"}
        titles = [" ".join(filter(None, [r.get("title"), r.get("snippet")])) or r.get("title", "") for r in rows]
        if not titles:
            return {"status": "no results"}
        threshold = R._semantic_min_relevance()
        relevance = list(service.similarities(topic, titles) or [])
        vectors = service.embed([r.get("title", "") for r in rows], purpose="clustering")
        if not relevance or not vectors or len(vectors) != len(rows):
            return {"status": "skipped: embeddings returned nothing usable"}
        groups = R._average_linkage_clusters(vectors, R._development_cluster_min_similarity())
    except Exception as error:
        return {"status": f"skipped: {type(error).__name__}: {str(error)[:160]}"}
    return {
        "pipeline_relevance_threshold": threshold,
        "share_above_threshold": round(sum(1 for v in relevance if v >= threshold) / len(relevance), 3),
        "distinct_headline_groups": len(groups),
        "note": "embedding proxies, not ground truth",
    }


def _review_date(value: Any) -> str:
    """Day only, one format for every provider, so the date's style (GDELT
    seen-time vs publisher time with zone) does not reveal the provider."""
    text = str(value or "").strip()
    if not text:
        return "unknown"
    try:
        from services.free_news_providers import _parse_date
        parsed = _parse_date(text)
        if parsed:
            return f"{parsed:%Y-%m-%d}"
    except Exception:
        pass
    return text[:10] if len(text) >= 10 and text[4:5] == "-" else "unknown"


def write_review_sheet(out: Path, runs: dict[str, dict[str, Any]], per_provider: int, rng: random.Random) -> int:
    rows, key = [], {}
    for name, run in runs.items():
        for item in rng.sample(run["rows"], min(per_provider, len(run["rows"]))):
            rows.append((name, item))
    rng.shuffle(rows)
    with (out / "review_sheet.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["review_id", "title", "domain", "date", "url",
                         "relevant_to_topic (y/n)", "specific_event (y/n)", "notes"])
        for index, (name, item) in enumerate(rows, start=1):
            review_id = f"R{index:03d}"
            key[review_id] = name
            writer.writerow([review_id, item.get("title", ""), item.get("domain", ""),
                             _review_date(item.get("published_at")), item.get("url", ""), "", "", ""])
    (out / "review_key.json").write_text(json.dumps(key, indent=2), encoding="utf-8")
    return len(rows)


def score_review(sheet: Path) -> int:
    key = json.loads((sheet.parent / "review_key.json").read_text(encoding="utf-8"))
    tally: dict[str, Counter] = {}
    # utf-8-sig: Excel adds a byte-order mark when it saves a CSV.
    with sheet.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            provider = key.get((row.get("review_id") or "").strip(), "?")
            counts = tally.setdefault(provider, Counter())
            for column, label in (("relevant_to_topic (y/n)", "relevant"), ("specific_event (y/n)", "event")):
                answer = (row.get(column) or "").strip().casefold()
                if answer in {"y", "n"}:
                    counts[f"{label}_answered"] += 1
                    counts[f"{label}_yes"] += answer == "y"
    print("provider | relevant | specific event")
    for provider, counts in sorted(tally.items()):
        def rate(label: str) -> str:
            answered = counts[f"{label}_answered"]
            return f"{counts[f'{label}_yes']}/{answered}" if answered else "not reviewed"
        print(f"{provider} | {rate('relevant')} | {rate('event')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--topic", default="artificial intelligence")
    parser.add_argument("--providers", default="newsapi,gdelt,rss,exa")
    parser.add_argument("--days", type=float, default=14.0, help="same date window for every provider")
    parser.add_argument("--requests-per-provider", type=int, default=6)
    parser.add_argument("--results-per-query", type=int, default=25)
    parser.add_argument("--fetch-sample", type=int, default=8, help="links per provider fetched to test usable text")
    parser.add_argument("--review-sample", type=int, default=15, help="links per provider in the blind review sheet")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--score-review", type=Path, help="score a filled review_sheet.csv and exit")
    args = parser.parse_args()
    if args.score_review:
        return score_review(args.score_review)

    from services.web_search_service import build_discovery_providers

    rng = random.Random(args.seed)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)
    queries = default_queries(args.topic)[: max(1, args.requests_per_provider)]
    providers, skipped = build_discovery_providers(args.providers)
    out = OUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    print(f"Trial: {len(queries)} queries x {len(providers)} providers, window {start:%Y-%m-%d}..{end:%Y-%m-%d}")
    for name, reason in skipped.items():
        print(f"  skipped {name}: {reason}")

    runs: dict[str, dict[str, Any]] = {}
    for provider in providers:
        name = getattr(provider, "provider_name", type(provider).__name__)
        print(f"  {name} ...", flush=True)
        runs[name] = run_provider(provider, queries, per_query=args.results_per_query, start=start, end=end)
        if hasattr(provider, "feed_status"):
            runs[name]["feed_status"] = provider.feed_status

    report: dict[str, Any] = {
        "topic": args.topic, "queries": queries, "window": [start.isoformat(), end.isoformat()],
        "requests_per_provider": len(queries), "results_per_query": args.results_per_query,
        "skipped_providers": skipped, "providers": {},
    }
    for name, run in runs.items():
        others = {url_key(r.get("url", "")) for other, o in runs.items() if other != name for r in o["rows"]}
        print(f"  measuring {name} ...", flush=True)
        report["providers"][name] = {
            "supply": supply_metrics(name, run, others),
            "usable_text": fetch_usable(run["rows"], args.fetch_sample, rng),
            "pipeline_proxies": pipeline_relevance_and_breadth(args.topic, run["rows"]),
            "calls": run["calls"],
            "feed_status": run.get("feed_status"),
        }
    reviewed = write_review_sheet(out, runs, args.review_sample, rng)
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    lines = [f"# Provider trial: {args.topic}", "",
             f"Window {start:%Y-%m-%d} to {end:%Y-%m-%d}; {len(queries)} identical queries per provider; "
             f"{args.results_per_query} results per query.", "",
             "| provider | requests | HTTP attempts | failures | results | unique URLs | only here | domains | usable text | pipeline-relevant | headline groups | median ms | pacing s | cost $ |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, data in report["providers"].items():
        s, u, p = data["supply"], data["usable_text"], data["pipeline_proxies"]
        usable = f"{u.get('usable')}/{u.get('sampled')}" if "sampled" in u else u.get("status", "")
        lines.append(
            f"| {name} | {s['requests']} | {s['http_attempts']} | {s['failures']} | {s['results']} | "
            f"{s['unique_urls']} | {s['only_this_provider']} | {s['distinct_domains']} | {usable} | "
            f"{p.get('share_above_threshold', p.get('status', ''))} | {p.get('distinct_headline_groups', '')} | "
            f"{s['median_latency_ms']} | {s['pacing_wait_s']} | {s['cost_usd']} |")
    lines += ["", f"Blind review sheet: {reviewed} rows in review_sheet.csv. Fill in y/n, then run:",
              f"    python -m tools.compare_news_providers --score-review {out / 'review_sheet.csv'}", "",
              "median ms excludes deliberate waits (GDELT allows one request per 5 s); those are under pacing s.",
              "Domains in the review sheet can still hint at the provider (RSS only covers its feed sites).",
              "Supply only: test downstream value separately (same topic through the full pipeline per source set)."]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nSaved to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
