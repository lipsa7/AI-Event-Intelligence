"""Stage test of the variant-view generator on a SAVED run - no new news retrieval (v13.24, 22 Nov 2026).

    python try_variants.py RUN-4367E5FE6BE84506BBC92CBB --slots 2 --max-calls 50

For the top --slots stories of the saved run it does what a live run does in an exploration slot:
the cited consensus map (with one Google search for published analysis), the primary records (Federal
Register, SEC EDGAR, CourtListener if COURTLISTENER_API_TOKEN is set - free, not Gemini), up to three
variant candidates through different lenses, the structure / plain-language / novelty / pre-mortem
checks and the tournament. It prints every candidate, why each dropped one was dropped, and the winner.
It does NOT run the claim-support gate, the editor's bar or the report - it answers one question
before a full paid run: does the generator now write variant views worth gating?

Hard ceiling on Gemini API attempts: --max-calls (default 50). One slot costs about 10-15 calls, one of
them a Pro call. Research report of 22 Nov, offline test plan step 2 (replay generation on saved
checkpoints so only generation changes).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_id", help="checkpoint RUN-... id")
    parser.add_argument("--slots", type=int, default=2, help="how many stories to try (default 2)")
    parser.add_argument("--max-calls", type=int, default=50, help="hard ceiling on Gemini API attempts")
    args = parser.parse_args()
    os.environ.setdefault("EVENT_INTELLIGENCE_VARIANT_VIEW", "1")
    from utils.logger import configure_logging
    configure_logging()
    from services import gemini_llm_service as gemini
    from services.research_orchestrator_service import ResearchOrchestratorService
    orchestrator = ResearchOrchestratorService()
    checkpoint = orchestrator._load_checkpoint(args.run_id)
    topic = checkpoint.get("topic") or "artificial intelligence"
    state = {"run_id": "STAGE-VARIANTS-" + checkpoint["run_id"], "topic": topic, "research_depth": "deep"}
    _, events, evidence, admitted_events, admitted_evidence, _ = orchestrator._restore_checkpoint(checkpoint, state)
    events = list(admitted_events or events)
    evidence = list(admitted_evidence or evidence)
    gemini.usage_snapshot(reset=True)
    gemini.set_call_budget(args.max_calls)
    log: dict = {"exploration": {"combinations": []}}
    try:
        combos = orchestrator._development_combinations(events, evidence, [], args.slots, topic=topic)[: args.slots]
        for combo in combos:
            print("\n" + "=" * 100 + "\nSTORY: " + " | ".join(combo["labels"]))
            candidates = orchestrator._exploration_candidates(
                combo=combo, topic=topic, events=events, evidence=evidence, graph_context={},
                constraints=[], blueprint={}, state=state, tried=[], tried_fingerprints=set(), log=log)
            entry = ((log.get("exploration") or {}).get("combinations") or [{}])[-1]
            print("CONSENSUS:", entry.get("consensus"))
            print(f"Consensus claims: {entry.get('consensus_claims')}; primary records added: {entry.get('primary_records')}")
            for call in entry.get("lens_calls") or []:      # v13.26: one short generation call per lens
                print(f"  LENS {call.get('lens')}: {call.get('result')} ({call.get('prompt_characters')} prompt characters)")
            print(f"Inputs: {entry.get('computed_facts', 0)} computed facts, {entry.get('consensus_sources', 0)} consensus "
                  f"sources, {entry.get('past_cases', 0)} past cases")
            for dropped in entry.get("variant_dropped") or []:
                score = (dropped.get("rubric") or {}).get("score")
                print(f"  DRAFT [{dropped.get('move')}] rubric {score} {dropped['problems']}: {dropped['insight']}")
            for item in candidates:
                meta = item.get("metadata") or {}
                text = item.get("insight") or item.get("text") or ""
                # v13.31.4: every field printed in full - judging truncated text led to guessed endings
                # (the rival view of 23 Nov was cut at 600 characters and filled in by hand).
                print(f"  WINNER ({len(text.split())} words):", text)
                print("    rubric:", json.dumps(meta.get("rubric")))
                print("    call:", json.dumps(item.get("forecast_contract") or meta.get("forecast_contract"), ensure_ascii=False))
                print("    lens:", item.get("analytical_lenses"), "| novelty:", json.dumps(meta.get("variant_novelty"), ensure_ascii=False))
                print("    pre-mortem:", json.dumps(meta.get("variant_premortem"), ensure_ascii=False))
                print("    variant perception:")
                print(json.dumps(item.get("variant_perception") or meta.get("variant_perception"), ensure_ascii=False, indent=6))
                print("    forecast diagnosticity:", json.dumps({k: v for k, v in (meta.get("variant_diagnosticity") or {}).items()
                                                              if k != "answers"}, ensure_ascii=False))
                try:    # v13.31.4: the full candidate kept on disk, so the text is never lost to a screen buffer
                    from datetime import datetime as _dt
                    out = Path(__file__).resolve().parent / "data" / "stage_candidates.jsonl"
                    with out.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps({"when": _dt.now().isoformat(timespec="seconds"),
                                                 "run_id": checkpoint["run_id"], "story": combo["labels"],
                                                 "candidate": item}, ensure_ascii=False, default=str) + "\n")
                except OSError as error:
                    print(f"    (candidate not saved: {error})")
            if not candidates:
                print("  No candidate survived (abstained for this story).")
    finally:
        used = gemini.calls_used()
        gemini.set_call_budget(None)
    # v13.31: the quality scorecard of this stage test, appended to data/stage_scores.csv for the trend
    # (python tools/quality_scorecard.py --stage).
    from services import quality_scorecard
    card = quality_scorecard.run_scorecard(log)
    print("\n" + "\n".join(quality_scorecard.lines(card)))
    try:
        import csv
        from datetime import datetime
        path = Path(__file__).resolve().parent / "data" / "stage_scores.csv"
        new = not path.exists()
        with path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["when", "run_id", "candidates", "mean_rubric", "every_item",
                                                        "kept", "most_failed"])
            if new:
                writer.writeheader()
            writer.writerow({"when": datetime.now().strftime("%Y-%m-%d %H:%M"), "run_id": checkpoint["run_id"],
                             "candidates": card.get("candidates"), "mean_rubric": card.get("mean_rubric_score"),
                             "every_item": card.get("share_meeting_every_item"),
                             "kept": sum(int(c.get("variant_kept") or 0) for c in log["exploration"]["combinations"]),
                             "most_failed": json.dumps(card.get("most_failed_items"))})
    except OSError as error:
        print(f"(stage score not saved: {error})")
    print(f"\nGemini API attempts used by this test: {used} of at most {args.max_calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
