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
import os
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
    parser.add_argument("--org", default="", help="organisation to advise in depth (v6.30); default: app picks")
    parser.add_argument("--profile", default="",
                        help="v6.35 experiment: organisation profile JSON (e.g. profiles/openai.json); omit for the baseline")
    parser.add_argument("--hypotheses", default="",
                        help="v6.36.5: a text file of your own hypotheses (one per paragraph, at most 5) to test "
                             "alongside the app's; use the SAME file (or none) for both runs of an experiment")
    args = parser.parse_args()
    if args.org:
        os.environ["EVENT_INTELLIGENCE_FOCUS_ORG"] = args.org.strip()
    os.environ.pop("EVENT_INTELLIGENCE_PROFILE", None)
    if args.profile:
        from services import org_profile
        try:
            profile = org_profile.load(args.profile)   # a broken file stops here, not halfway through a run
        except org_profile.ProfileError as error:
            print(f"Profile error: {error}")
            return 2
        os.environ["EVENT_INTELLIGENCE_PROFILE"] = str(Path(args.profile).resolve())
        print(f"Using organisation profile for {profile['name']} (goal: {profile['decision_goal'][:120]})")
    else:
        print("No organisation profile: baseline run.")
    os.environ.pop("EVENT_INTELLIGENCE_USER_HYPOTHESES", None)
    if args.hypotheses:
        from services import strategic_hypothesis_lab as lab
        try:
            mine = lab.load_user_hypotheses(args.hypotheses)
        except OSError as error:
            print(f"Hypotheses file error: {error}")
            return 2
        if not mine:
            print("Hypotheses file has no hypothesis of 20 characters or more.")
            return 2
        os.environ["EVENT_INTELLIGENCE_USER_HYPOTHESES"] = str(Path(args.hypotheses).resolve())
        print(f"Testing {len(mine)} hypothesis(es) of your own: " + "; ".join(h["text"][:60] for h in mine))
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
