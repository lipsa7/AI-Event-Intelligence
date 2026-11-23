"""Calibration layer for the editor's-bar judge, fitted on the user's own ratings (v13.28, 22 Nov 2026).

Research report of 22 Nov, Method 8 and Method 6:
* Few-shot examples alone barely move a judge (van den Burg et al. 2025: +0.8 to +1.1%); learning a simple
  mapping from the judge's scores to human labels on about 100 examples raised agreement by about 142% on
  average across 29 tasks. So the judge returns six item scores (the rubric in par_check.py) and a logistic
  regression maps them to "would the user rate this good?", fitted with 5-fold cross-validation on the
  labelled rows (data/insight_review_90.csv).
* Small judges grade single items unreliably (Thakur et al. 2025: Llama-3-8B Scott's pi about 59) even
  when they rank well, so the judge is a GATE only when its cross-validated precision on the user's labels
  reaches 0.7 (the report's target) for the model now installed. Until then its verdict is advisory: the
  insight is shown with the judge's verdict beside it, never held back by an unproven judge.
* Active learning: each run lists the candidates whose probability is nearest 0.5 - rating those teaches
  the calibration most.

The fitted model is written by tools/calibrate_par.py (offline) to data/par_calibration.json; a run only
reads it. Pure numpy, no new package.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

VERSION = "par-calibration-v1"   # unchanged: saved files stay readable
PATH_ENV = "EVENT_INTELLIGENCE_PAR_CALIBRATION"
DEFAULT_PATH = os.path.join("data", "par_calibration.json")
MIN_PRECISION = 0.7      # research report of 22 Nov, Method 8: "target precision of at least 0.7"
MIN_ROWS = 60
# v13.28.1: at least 15 predicted-good rows before precision counts. With 5, scanning every threshold on
# 90 rows found precision >= 0.7 by luck for a judge whose scores carry NO information in 13 of 200
# simulated trials (6.5%) - and a trusted judge holds insights back. With 15: 1 in 150, while a judge
# with a real signal is still trusted (tests/test_v13_28_1_calibration_trust.py).
MIN_PREDICTED_POSITIVE = 15
L2 = 1.0


def features(scores: Mapping[str, Any], verdict_pass: bool, items: Sequence[str]) -> list[float] | None:
    out = []
    for item in items:
        value = scores.get(item) if isinstance(scores, Mapping) else None
        if not isinstance(value, (int, float)):
            return None
        out.append(max(0.0, min(10.0, float(value))) / 10.0)
    return out + [1.0 if verdict_pass else 0.0]


def fit(x: Sequence[Sequence[float]], y: Sequence[int], l2: float = L2, steps: int = 50) -> tuple[list[float], float]:
    """L2-regularised logistic regression by Newton's method."""
    a = np.asarray(x, dtype=float)
    t = np.asarray(y, dtype=float)
    design = np.hstack([a, np.ones((len(a), 1))])
    w = np.zeros(design.shape[1])
    penalty = np.eye(design.shape[1]) * l2
    penalty[-1, -1] = 0.0                       # the intercept is not penalised
    for _ in range(steps):
        p = 1.0 / (1.0 + np.exp(-design @ w))
        gradient = design.T @ (p - t) + penalty @ w
        hessian = design.T @ (design * (p * (1 - p))[:, None]) + penalty
        step = np.linalg.solve(hessian + 1e-9 * np.eye(len(w)), gradient)
        w -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return [float(v) for v in w[:-1]], float(w[-1])


def _prob(weights: Sequence[float], bias: float, row: Sequence[float]) -> float:
    return float(1.0 / (1.0 + np.exp(-(np.dot(weights, row) + bias))))


def cross_validate(x: Sequence[Sequence[float]], y: Sequence[int], folds: int = 5, seed: int = 22) -> list[float]:
    """Out-of-fold probability for every row (stratified folds)."""
    rng = np.random.default_rng(seed)
    out = [0.0] * len(y)
    groups: list[list[int]] = [[] for _ in range(folds)]
    for label in (0, 1):
        idx = [i for i, v in enumerate(y) if v == label]
        rng.shuffle(idx)
        for n, i in enumerate(idx):
            groups[n % folds].append(i)
    for held in groups:
        train = [i for i in range(len(y)) if i not in set(held)]
        if not held or len({y[i] for i in train}) < 2:
            continue
        weights, bias = fit([x[i] for i in train], [y[i] for i in train])
        for i in held:
            out[i] = _prob(weights, bias, x[i])
    return out


def choose_threshold(probabilities: Sequence[float], y: Sequence[int]) -> dict[str, Any]:
    """Lowest threshold whose cross-validated precision is at least MIN_PRECISION (most recall at that
    precision). Returns precision/recall there, or the best precision found if none reaches it."""
    best: dict[str, Any] = {"threshold": None, "precision": 0.0, "recall": 0.0, "predicted_positive": 0}
    for threshold in sorted(set(round(p, 4) for p in probabilities)):
        chosen = [i for i, p in enumerate(probabilities) if p >= threshold]
        if len(chosen) < MIN_PREDICTED_POSITIVE:
            continue
        hits = sum(y[i] for i in chosen)
        precision, recall = hits / len(chosen), hits / max(1, sum(y))
        row = {"threshold": threshold, "precision": round(precision, 3), "recall": round(recall, 3),
               "predicted_positive": len(chosen)}
        if precision >= MIN_PRECISION:
            return row
        if precision > best["precision"]:
            best = row
    return best


def build(x: Sequence[Sequence[float]], y: Sequence[int], *, model: str, items: Sequence[str],
          exemplar_mode: str) -> dict[str, Any]:
    oof = cross_validate(x, y)
    chosen = choose_threshold(oof, y)
    weights, bias = fit(x, y)
    return {"version": VERSION, "model": model, "items": list(items), "exemplar_mode": exemplar_mode,
            "weights": weights, "bias": bias, "threshold": chosen["threshold"],
            "cv_precision": chosen["precision"], "cv_recall": chosen["recall"],
            "cv_predicted_positive": chosen["predicted_positive"], "rows": len(y), "good_rows": int(sum(y)),
            "fitted_at": datetime.now(timezone.utc).isoformat()}


def _path() -> Path:
    return Path(os.getenv(PATH_ENV) or DEFAULT_PATH)


def save(calibration: Mapping[str, Any], path: Path | None = None) -> Path:
    target = path or _path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(calibration, indent=2), encoding="utf-8")
    return target


def load(path: Path | None = None) -> dict[str, Any] | None:
    target = path or _path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("version") == VERSION else None


def trusted(calibration: Mapping[str, Any] | None, model: str) -> bool:
    return bool(calibration and calibration.get("model") == model and calibration.get("threshold") is not None
                and float(calibration.get("cv_precision") or 0) >= MIN_PRECISION
                and int(calibration.get("rows") or 0) >= MIN_ROWS)


def probability(calibration: Mapping[str, Any], scores: Mapping[str, Any], verdict_pass: bool) -> float | None:
    row = features(scores, verdict_pass, calibration.get("items") or [])
    if row is None:
        return None
    return round(_prob(calibration.get("weights") or [], float(calibration.get("bias") or 0.0), row), 3)


def rate_next(scored: Sequence[Mapping[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    """The candidates the judge is least sure about - the ratings that teach it most."""
    rows = [dict(r) for r in scored if isinstance(r.get("probability"), (int, float))]
    rows.sort(key=lambda r: abs(float(r["probability"]) - 0.5))
    return [{"text": str(r.get("text") or "")[:400], "probability": r["probability"]} for r in rows[:limit]]


# ---------------------------------------------------------------- v13.31: held-out metrics (Yan; Husain)
# Research report of 22 Nov (v13.30 plan): report a judge's fail-class recall, precision and Cohen's kappa on a
# held-out test set (Eugene Yan: 75% of labels to align, 25% held out; kappa 0.4-0.6 substantial, above 0.7
# excellent; human raters are often only 0.2-0.3), never raw agreement - a judge that always says "pass"
# looks accurate when failures are rare (Husain & Shankar: TPR and TNR both above 80%).
def kappa(pred: Sequence[int], truth: Sequence[int]) -> float | None:
    n = len(truth)
    if not n:
        return None
    observed = sum(1 for p, t in zip(pred, truth) if p == t) / n
    p_yes = (sum(pred) / n) * (sum(truth) / n)
    p_no = (1 - sum(pred) / n) * (1 - sum(truth) / n)
    expected = p_yes + p_no
    return round((observed - expected) / (1 - expected), 3) if expected < 1 else None


def classification(pred: Sequence[int], truth: Sequence[int]) -> dict[str, Any]:
    tp = sum(1 for p, t in zip(pred, truth) if p and t)
    tn = sum(1 for p, t in zip(pred, truth) if not p and not t)
    fp = sum(1 for p, t in zip(pred, truth) if p and not t)
    fn = sum(1 for p, t in zip(pred, truth) if not p and t)
    return {"tpr": round(tp / (tp + fn), 3) if tp + fn else None, "tnr": round(tn / (tn + fp), 3) if tn + fp else None,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "fail_recall": round(tn / (tn + fp), 3) if tn + fp else None, "kappa": kappa(pred, truth), "n": len(truth)}


def holdout(x: Sequence[Sequence[float]], y: Sequence[int], share: float = 0.25, seed: int = 22) -> dict[str, Any]:
    """Fit on 75%, score the held-out 25% (stratified) at the threshold chosen on the 75%."""
    rng = np.random.default_rng(seed)
    test: list[int] = []
    for label in (0, 1):
        idx = [i for i, v in enumerate(y) if v == label]
        rng.shuffle(idx)
        test += idx[: max(1, round(len(idx) * share))]
    train = [i for i in range(len(y)) if i not in set(test)]
    if len({y[i] for i in train}) < 2:
        return {}
    xs, ys = [x[i] for i in train], [y[i] for i in train]
    threshold = choose_threshold(cross_validate(xs, ys), ys).get("threshold")
    weights, bias = fit(xs, ys)
    if threshold is None:
        return {"threshold": None, "n": len(test)}
    pred = [1 if _prob(weights, bias, x[i]) >= threshold else 0 for i in test]
    return dict(classification(pred, [y[i] for i in test]), threshold=threshold)
