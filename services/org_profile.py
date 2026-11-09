"""Organisation profile and decision goal (v6.35) - an experiment behind a switch.

Products that give organisation-specific advice tailor it to a profile the customer supplies:
FiscalNote's impact summaries use "industry, operational footprint, policy priorities"; Klue's
Compete Agent is "contextualized by your actual business info and GTM strategy". InsightBench
(ICLR 2025) found a specific goal raised insight quality from 0.40 to 0.60 - on structured
datasets, not strategy from news - so here it is an experiment: the same checkpoint is replayed
with and without a profile and compared on the user's ratings.

The profile is the user's own background and goal. It steers what the analysis looks for; it is
NEVER evidence: nothing in it can be cited as a fact or make a claim "supported". v6.35.1 (GPT
review): keep it to the decision and widely documented background; a dependency or leverage claim
in the profile would bias the very thing the experiment measures.

Switch: `python replay_run.py RUN-... --profile profiles/openai.json`
(or EVENT_INTELLIGENCE_PROFILE=path). No switch = baseline, unchanged behaviour.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

VERSION = "org-profile-v1"
ENV = "EVENT_INTELLIGENCE_PROFILE"
LIST_FIELDS = ("business_lines", "revenue_sources", "depends_on", "others_depend_on_it_for",
               "operating_footprint", "constraints", "priorities")
LABELS = {
    "business_lines": "Business lines", "revenue_sources": "Where its revenue comes from",
    "depends_on": "What it depends on, and from whom", "others_depend_on_it_for": "What others depend on it for",
    "operating_footprint": "Where it operates", "constraints": "Constraints it works under",
    "priorities": "Its priorities",
}


class ProfileError(ValueError):
    pass


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def load(path: str | Path) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError as error:
        raise ProfileError(f"Profile file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ProfileError(f"Profile file is not valid JSON: {path} ({error.msg}, line {error.lineno})") from error
    if not isinstance(raw, Mapping):
        raise ProfileError("A profile must be a JSON object.")
    profile = {"name": _clean(raw.get("name"))[:120], "decision_goal": _clean(raw.get("decision_goal"))[:600]}
    if not profile["name"] or not profile["decision_goal"]:
        raise ProfileError("A profile needs at least 'name' and 'decision_goal'.")
    for field in LIST_FIELDS:
        values = raw.get(field) or []
        values = [values] if isinstance(values, str) else values
        profile[field] = [_clean(v)[:300] for v in values if _clean(v)][:8]
    profile["notes"] = _clean(raw.get("notes"))[:600]
    return profile


def fingerprint(profile: Mapping[str, Any] | None) -> str:
    if not profile:
        return ""
    return hashlib.sha256(json.dumps(profile, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def active() -> dict[str, Any] | None:
    """The profile for this run, or None (baseline). A broken profile file stops the run
    with a clear message rather than silently running the baseline."""
    path = _clean(os.getenv(ENV))
    return load(path) if path else None


def prompt_block(profile: Mapping[str, Any] | None) -> str:
    if not profile:
        return ""
    lines = [
        f"ORGANISATION PROFILE for {profile['name']} (supplied by the user as background and goal; it is NOT "
        "evidence - never cite it, never treat it as a fact, and do not repeat it as a finding):",
        f"DECISION GOAL: {profile['decision_goal']}",
    ]
    for field in LIST_FIELDS:
        if profile.get(field):
            lines.append(f"{LABELS[field]}: " + "; ".join(profile[field]))
    if profile.get("notes"):
        lines.append(f"Notes: {profile['notes']}")
    lines.append(
        "Use the profile only to decide what matters: prefer findings and moves that bear on this decision, using "
        "the facts' own evidence. Do not adopt any dependency, leverage or motive from the profile: establish those "
        "from the facts or not at all. If the facts do not bear on the goal, say so rather than stretching them.")
    return "\n".join(lines)


def summary(profile: Mapping[str, Any] | None, path: str = "") -> dict[str, Any]:
    if not profile:
        return {"used": False, "version": VERSION}
    return {"used": True, "version": VERSION, "name": profile["name"], "decision_goal": profile["decision_goal"],
            "fingerprint": fingerprint(profile), "path": path}
