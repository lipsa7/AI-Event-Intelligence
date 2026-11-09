"""Test ONLY the precedent stage on a saved checkpoint (v6.36) - a few dozen Gemini calls,
not a full replay.

    python try_precedents.py RUN-ED24092548084234B2300689

It runs ONLY the precedent searches for the top developments (v6.36.1: policy, dependency,
numbers and commentary searches are switched off here), names past cases with the same
mechanism, checks each against a dated source with a quoted outcome, then shows which cases
would reach the briefing and why any were rejected. Nothing is saved as a report.
Limits per development (3 developments): 2 precedent searches + 1 query plan + at most 4 pages
reviewed, and if no usable case is found, 1 retry plan + 1 more search + at most 4 more pages.
HARD CEILING (v6.36.2): at most --max-calls Gemini API attempts in total (default 40), counting
every model call, every grounded search and every retry. When it is reached, no further call is
made and the test reports what it found so far. The real count is printed at the end.
"""

from __future__ import annotations

import argparse
import json
import sys

from utils.logger import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_id", help="checkpoint RUN-... id")
    parser.add_argument("--max-calls", type=int, default=40, help="hard ceiling on Gemini API attempts (default 40)")
    args = parser.parse_args()
    configure_logging()
    from services import editorial_briefing as brief
    from services.research_orchestrator_service import ResearchOrchestratorService
    orchestrator = ResearchOrchestratorService()
    checkpoint = orchestrator._load_checkpoint(args.run_id)
    state = {"run_id": "STAGE-PRECEDENTS-" + checkpoint["run_id"], "topic": checkpoint.get("topic"),
             "research_depth": "deep"}
    _, events, evidence, admitted_events, admitted_evidence, _ = orchestrator._restore_checkpoint(checkpoint, state)
    developments = orchestrator._developments_to_watch(admitted_events or events, admitted_evidence or evidence, state)
    print("Developments searched:")
    for dev in developments[: orchestrator.PRECEDENT_DEVELOPMENTS]:
        print("  -", dev.get("title"))
    from services import gemini_llm_service as gemini
    gemini.usage_snapshot(reset=True)
    gemini.set_call_budget(args.max_calls)
    receipts = orchestrator._precedent_research(developments, state, only_gaps=("precedent",), max_sources=4)
    for receipt in receipts:
        queries = [q["query"] for q in receipt.get("queries") or [] if q.get("gap") == "precedent"]
        print(f"\n{receipt.get('development')}: precedent searches {queries}")
    historical = list((state.get("historical_context") or {}).get("records") or [])
    usable = brief.select_cases(historical)
    print(f"\nPast cases found: {len(historical)}; usable in the briefing: {len(usable)}")
    for case in usable:
        print(f"  [{case['year']}] {case['text'][:160]}\n     similar: {case['similarity'][:160]}"
              f"\n     different: {case['difference'][:160]}\n     outcome: {case['outcome'][:160]}\n     {case['source_url']}")
    rejected = [r for r in brief.case_rejections(historical) if not r["usable"]]
    if rejected:
        print("Rejected cases: " + json.dumps(rejected, ensure_ascii=False)[:1500])
    usage = gemini.usage_snapshot()
    print(f"\nGemini API attempts used by this test: {gemini.calls_used()} of at most {args.max_calls} "
          + json.dumps(usage))
    gemini.set_call_budget(None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
