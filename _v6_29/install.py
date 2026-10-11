"""Install Event Intelligence v6.29: rivals modelled separately, candidate tournament, best-response choice (deep research); includes v6.28 and earlier.

Built on v6.28 and retaining the full v6.19/v6.20 feature set. The
installer hash-checks every managed input first (line endings ignored), makes
backups before writes, and never reads or changes .env. It accepts the known
upgrade bases carried forward by the manifest, including v6.20, v6.20.1, v6.20.2, v6.20.3, v6.20.4, v6.20.5, either v6.20.6, v6.20.7, v6.20.8, v6.20.9, v6.20.10, v6.20.11, v6.20.12, v6.21, v6.21.1, v6.21.2, v6.21.3, v6.21.4, v6.21.5, v6.21.6, v6.21.7, v6.21.8, v6.22, v6.23, v6.24, v6.25, v6.26, v6.27, v6.27.1 and v6.28.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent


def normalized_hash(path: Path) -> str:
    with open(path, encoding="utf-8-sig", newline=None) as handle:
        return hashlib.sha256(handle.read().encode("utf-8")).hexdigest()


def atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".v6_install_", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def plan(project: Path, manifest: dict) -> list[tuple[Path, Path, str]]:
    changes = []
    for relative, spec in manifest["files"].items():
        source = PACKAGE / relative
        if not source.is_file() or normalized_hash(source) != spec["installed"]:
            raise RuntimeError(f"Package file missing or changed: {relative}")
        if relative.endswith(".py"):
            compile(source.read_text(encoding="utf-8"), str(source), "exec")
        target = project / relative
        if not target.exists():
            if spec["required"]:
                raise RuntimeError(f"Missing {target}. Nothing was installed.")
            changes.append((source, target, relative))
            continue
        actual = normalized_hash(target)
        if actual == spec["installed"]:
            continue
        if actual not in spec["replaceable"] and not spec.get("replace_any"):
            raise RuntimeError(
                f"Your {relative} differs from the version this package was built on "
                "(this package upgrades the known v6.15.2 through v6.21.5 bases only). Nothing was installed."
            )
        changes.append((source, target, relative))
    return changes


def install(project: Path) -> None:
    project = project.resolve()
    manifest = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
    changes = plan(project, manifest)
    if not changes:
        print("v6.29 is already installed.")
        return

    backup = project / "v6_backups" / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_v6_29_" + uuid.uuid4().hex[:6])
    backup.mkdir(parents=True, exist_ok=False)
    existed = {}
    for _, target, relative in changes:
        existed[relative] = target.is_file()
        if existed[relative]:
            (backup / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup / relative)

    written = []
    try:
        for source, target, relative in changes:
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(target, source.read_bytes())
            written.append((target, relative))
    except Exception:
        for target, relative in reversed(written):
            if existed[relative]:
                atomic_write(target, (backup / relative).read_bytes())
            else:
                target.unlink()
        raise

    print("Installed v6.29:")
    for _, _, relative in changes:
        print(f"  {relative}")
    print(f"Backups: {backup}")
    print("Check: python -m pytest tests/test_v6_29_rivals_tournament.py tests/test_v6_28_actor_strategy_method.py tests/test_v6_27_actor_next_steps.py tests/test_v6_26_leverage_incentives.py tests/test_v6_25_disclose_unsearched.py tests/test_v6_24_search_budget_and_patterns.py tests/test_v6_23_grid_and_evidence_table.py tests/test_v6_22_expert_frontier.py tests/test_v6_21_8_constraint_and_so_what.py tests/test_v6_21_7_promotion_and_topup.py tests/test_v6_21_6_combinations_and_old_news.py tests/test_v6_21_5_implication_novelty.py tests/test_v6_21_4_search_coverage.py tests/test_v6_21_3_provenance_and_dates.py tests/test_v6_21_2_citations_and_implications.py tests/test_v6_21_1_cost_and_implications.py tests/test_v6_21_convergent_insights.py tests/test_v6_20_12_model_outage.py tests/test_v6_20_11_release_audit.py tests/test_v6_20_10_replay_fixes.py tests/test_v6_20_9_run_fixes.py tests/test_v6_20_5_forecast_followup.py tests/test_v6_20_4_dates_actors_asides.py tests/test_v6_20_3_dedup_gold.py tests/test_v6_20_resolver_memory_nonobvious.py tests/test_v6_19_replay_and_research.py tests/test_v6_17_report_sections.py tests/test_v6_16_developments.py tests/test_claim_support_gate.py tests/test_cluster_expansion.py tests/test_development_clusters.py -q")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        install(args.project)
    except Exception as exc:
        parser.exit(1, f"ERROR: {exc}\n")
