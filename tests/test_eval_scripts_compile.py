import importlib.util
from pathlib import Path

import pytest


EVAL_FILES = [
    "extraction_eval.py",
    "clustering_eval.py",
    "retrieval_eval.py",
    "grounding_eval.py",
    "reasoning_eval.py",
    "red_team_eval.py",
    "report_eval.py",
    "selection_eval.py",
    "evidence_discovery_eval.py",
    "refinement_eval.py",
    "preference_eval.py",
    "evaluation_runner.py",
]


@pytest.mark.parametrize(
    "filename",
    EVAL_FILES,
)
def test_eval_script_compiles(filename):
    path = (
        Path(__file__).resolve().parents[1]
        / filename
    )

    source = path.read_text(
        encoding="utf-8"
    )

    compile(
        source,
        str(path),
        "exec",
    )