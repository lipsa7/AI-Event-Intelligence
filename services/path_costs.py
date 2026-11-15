"""What each analysis path cost in a run, beside how useful you rated its items (Nov 2026).

Anthropic, "Building effective agents": add complexity only when it demonstrably improves outcomes,
and measure. Per-step cost tracing (as Langfuse / LangSmith do per span) shows which step drives
the bill. The app has four paths that each produce strategic items - hidden insights, the
briefing, the hypothesis lab and actor moves - and nothing measured which earns its Gemini calls.

Every Gemini attempt is counted against the outermost path that started the work
(gemini_llm_service.stage); each run appends one line to data/run_costs.jsonl. This module reads
that line and your ratings (data/insight_ratings.jsonl) for one report. No model calls.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

VERSION = "path-costs-v1"
ROOT = Path(__file__).resolve().parents[1]
# rating "kind" (insight_ratings.rating_items) -> the path that produced the item
KIND_PATH = {"implication": "briefing", "strategic_hypothesis": "hypothesis_lab",
             "hidden_insight": "hidden_insights", "strategic_decision": "actor_moves"}
ITEM_PATHS = tuple(dict.fromkeys(KIND_PATH.values()))


def log_path() -> Path:
    return Path(os.getenv("EVENT_INTELLIGENCE_RUN_COSTS") or ROOT / "data" / "run_costs.jsonl")


def run_costs(run_id: str, path: Path | None = None) -> dict[str, Any]:
    """The last cost record for this run, or {} (runs before this change log none)."""
    path = path or log_path()
    if not path.exists():
        return {}
    found: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("run_id") == run_id:
            found = row
    return found


def path_value(report_id: str, costs: Mapping[str, Any], ratings: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per item-producing path, then one for the shared work."""
    by_path = costs.get("by_path") or {}
    rated = [r for r in ratings if r.get("report_id") == report_id]
    rows = []
    for path in ITEM_PATHS:
        mine = [r for r in rated if KIND_PATH.get(r.get("kind")) == path]
        useful = sum(1 for r in mine if r.get("rating") == "useful")
        attempts = int((by_path.get(path) or {}).get("attempts") or 0)
        rows.append({"path": path, "rated": len(mine), "useful": useful, "attempts": attempts,
                     "useful_per_10_attempts": round(10 * useful / attempts, 2) if attempts and mine else None})
    shared = {name: int((row or {}).get("attempts") or 0) for name, row in by_path.items() if name not in ITEM_PATHS}
    rows.append({"path": "shared: " + ", ".join(f"{k} {v}" for k, v in sorted(shared.items(), key=lambda kv: -kv[1])),
                 "rated": 0, "useful": 0, "attempts": sum(shared.values()), "useful_per_10_attempts": None})
    return rows


def lines(report_id: str, run_id: str, ratings: Iterable[Mapping[str, Any]], path: Path | None = None) -> list[str]:
    costs = run_costs(run_id, path)
    if not costs:
        return [f"No cost record for {run_id} (runs before the per-path cost change log none)."]
    out = []
    cap = costs.get("call_cap") or {}
    total = sum(int((row or {}).get("attempts") or 0) for row in (costs.get("by_path") or {}).values())
    out.append(f"{total} Gemini attempts" + (f" (cap {cap.get('limit')})" if cap.get("limit") else "")
               + (" - THE CAP WAS REACHED; later steps were cut short" if costs.get("stopped_at_cap") else ""))
    for row in path_value(report_id, costs, list(ratings)):
        per = row["useful_per_10_attempts"]
        rated = f"useful {row['useful']}/{row['rated']} rated, " if not row["path"].startswith("shared") else ""
        out.append(f"  {row['path']:45s} {rated}{row['attempts']} attempts"
                   + (f", {per} useful per 10 attempts" if per is not None else ""))
    out.append("  A path whose items are rarely rated useful while it takes a large share of calls is a candidate "
               "to cut or merge - once more than one run confirms it.")
    return out
