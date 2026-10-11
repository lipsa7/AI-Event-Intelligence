"""Replay the analysis stages of a Deep run from its saved checkpoint (v6.19).

Every Deep run saves a checkpoint just before analysis
(data/checkpoints/RUN-*.json): the collected sources, events and evidence.
A replay re-runs only the later stages (gap research, strategic analysis,
claim checks, briefing, report) on that same data, so a fix can be tested
without paying again for news collection and event extraction.

Usage (project folder, venv active):
    python replay_run.py                 # latest checkpoint
    python replay_run.py RUN-XXXX        # a specific run
    python replay_run.py --list          # show saved checkpoints

The replayed report is saved like any other run; open the dashboard to view it.
Analysis stages still call Gemini (synthesis, grounding, gap research), so a
replay costs less than a full run, not nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from utils.logger import configure_logging


def checkpoints() -> list[Path]:
    from services.research_orchestrator_service import ResearchOrchestratorService
    folder = ResearchOrchestratorService._checkpoint_dir()
    return sorted(folder.glob("RUN-*.json"), key=lambda item: item.stat().st_mtime, reverse=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_id", nargs="?", help="RUN-... id or checkpoint path (default: latest)")
    parser.add_argument("--list", action="store_true", help="list saved checkpoints")
    args = parser.parse_args()
    configure_logging()
    found = checkpoints()
    if args.list:
        for path in found:
            data = json.loads(path.read_text(encoding="utf-8"))
            print(f"{path.stem}  {data.get('saved_at', '')[:19]}  {data.get('research_depth')}  "
                  f"events={len(data.get('event_records') or [])}  topic={data.get('topic')!r}")
        return 0
    if not found and not args.run_id:
        print("No checkpoint found. Run one Deep research first (v6.19 or later saves it).")
        return 1
    reference = args.run_id or str(found[0])
    from services.research_orchestrator_service import ResearchOrchestratorService
    orchestrator = ResearchOrchestratorService()
    checkpoint = orchestrator._load_checkpoint(reference)
    print(f"Replaying {checkpoint['run_id']} ({checkpoint['research_depth']}): {checkpoint['topic']!r}")
    state = orchestrator.run(topic=checkpoint["topic"], research_depth=checkpoint["research_depth"],
                             replay_checkpoint=checkpoint["_path"])
    outcome = state.get("terminal_outcome") or {}
    print(f"New run {state.get('run_id')}: status={outcome.get('status')} report={outcome.get('report_id')} "
          f"released={outcome.get('report_released')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
