"""
Statistical utilities for offline AI evaluation.

Recommended location:
    evaluation/statistics.py

Keep statistical primitives here instead of duplicating them in utils/.

Includes:
- Wilson confidence interval for binary rates
- percentile bootstrap CI
- paired bootstrap CI for A/B deltas
- exact McNemar test for paired binary outcomes
- exact paired sign test
- Expected Calibration Error (ECE)
- Brier score

These functions measure uncertainty/comparison. They do not make release or
product-governance decisions.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any, Callable, Dict, List, Optional, Sequence


DEFAULT_CONFIDENCE = 0.95
DEFAULT_BOOTSTRAP_SAMPLES = 10_000
DEFAULT_SEED = 42


@dataclass(frozen=True)
class ConfidenceInterval:
    estimate: float
    lower: float
    upper: float
    confidence_level: float
    method: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _validate_confidence(confidence_level: float) -> None:
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be between 0 and 1.")


def _clean_numeric(values: Sequence[float | int], name: str) -> List[float]:
    result: List[float] = []

    for index, value in enumerate(values):
        if isinstance(value, bool):
            raise TypeError(f"{name}[{index}] must be numeric, not bool.")

        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"{name}[{index}] is not numeric.") from exc

        if not math.isfinite(number):
            raise ValueError(f"{name}[{index}] must be finite.")

        result.append(number)

    return result


def _quantile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("Cannot compute quantile of empty sequence.")

    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be between 0 and 1.")

    if len(sorted_values) == 1:
        return float(sorted_values[0])

    position = probability * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)

    if lower == upper:
        return float(sorted_values[lower])

    fraction = position - lower

    return (
        float(sorted_values[lower]) * (1.0 - fraction)
        + float(sorted_values[upper]) * fraction
    )


def _normal_quantile(probability: float) -> float:
    """
    Inverse standard-normal CDF using Acklam's rational approximation.
    """
    if not 0.0 < probability < 1.0:
        raise ValueError("probability must be strictly between 0 and 1.")

    a = (
        -39.69683028665376,
        220.9460984245205,
        -275.9285104469687,
        138.3577518672690,
        -30.66479806614716,
        2.506628277459239,
    )
    b = (
        -54.47609879822406,
        161.5858368580409,
        -155.6989798598866,
        66.80131188771972,
        -13.28068155288572,
    )
    c = (
        -0.007784894002430293,
        -0.3223964580411365,
        -2.400758277161838,
        -2.549732539343734,
        4.374664141464968,
        2.938163982698783,
    )
    d = (
        0.007784695709041462,
        0.3224671290700398,
        2.445134137142996,
        3.754408661907416,
    )

    p_low = 0.02425
    p_high = 1.0 - p_low

    if probability < p_low:
        q = math.sqrt(-2.0 * math.log(probability))
        return (
            (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])
            / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
        )

    if probability <= p_high:
        q = probability - 0.5
        r = q * q
        return (
            (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5]) * q
            / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1.0)
        )

    q = math.sqrt(-2.0 * math.log(1.0 - probability))
    return -(
        (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])
        / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    )


def wilson_interval(
    successes: int,
    total: int,
    confidence_level: float = DEFAULT_CONFIDENCE,
) -> ConfidenceInterval:
    """
    Wilson score interval for a binomial rate.
    """
    _validate_confidence(confidence_level)

    if total <= 0:
        raise ValueError("total must be > 0.")
    if successes < 0 or successes > total:
        raise ValueError("successes must be between 0 and total.")

    p = successes / total
    alpha = 1.0 - confidence_level
    z = _normal_quantile(1.0 - alpha / 2.0)
    z2 = z * z

    denominator = 1.0 + z2 / total
    center = (p + z2 / (2.0 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            p * (1.0 - p) / total
            + z2 / (4.0 * total * total)
        )
        / denominator
    )

    return ConfidenceInterval(
        estimate=p,
        lower=max(0.0, center - margin),
        upper=min(1.0, center + margin),
        confidence_level=confidence_level,
        method="wilson_score",
    )


def bootstrap_confidence_interval(
    values: Sequence[float | int],
    statistic: Callable[[Sequence[float]], float] = mean,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    confidence_level: float = DEFAULT_CONFIDENCE,
    random_seed: int = DEFAULT_SEED,
) -> ConfidenceInterval:
    """
    Percentile bootstrap CI for a scalar statistic.
    """
    _validate_confidence(confidence_level)

    if bootstrap_samples <= 0:
        raise ValueError("bootstrap_samples must be > 0.")

    data = _clean_numeric(values, "values")

    if not data:
        raise ValueError("values cannot be empty.")

    estimate = float(statistic(data))
    rng = random.Random(random_seed)
    n = len(data)

    samples: List[float] = []

    for _ in range(bootstrap_samples):
        resample = [data[rng.randrange(n)] for _ in range(n)]
        value = float(statistic(resample))

        if not math.isfinite(value):
            raise ValueError("statistic returned a non-finite value.")

        samples.append(value)

    samples.sort()
    alpha = 1.0 - confidence_level

    return ConfidenceInterval(
        estimate=estimate,
        lower=_quantile(samples, alpha / 2.0),
        upper=_quantile(samples, 1.0 - alpha / 2.0),
        confidence_level=confidence_level,
        method="percentile_bootstrap",
    )


def paired_bootstrap_difference(
    baseline: Sequence[float | int],
    candidate: Sequence[float | int],
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    confidence_level: float = DEFAULT_CONFIDENCE,
    random_seed: int = DEFAULT_SEED,
) -> Dict[str, Any]:
    """
    Paired bootstrap CI for mean(candidate - baseline).

    Use when A and B are scored on the SAME evaluation cases.
    """
    _validate_confidence(confidence_level)

    if bootstrap_samples <= 0:
        raise ValueError("bootstrap_samples must be > 0.")

    a = _clean_numeric(baseline, "baseline")
    b = _clean_numeric(candidate, "candidate")

    if len(a) != len(b):
        raise ValueError("baseline and candidate must have equal length.")
    if not a:
        raise ValueError("paired samples cannot be empty.")

    deltas = [bv - av for av, bv in zip(a, b)]
    observed_delta = mean(deltas)

    rng = random.Random(random_seed)
    n = len(deltas)
    boot: List[float] = []
    better = 0

    for _ in range(bootstrap_samples):
        sampled = [deltas[rng.randrange(n)] for _ in range(n)]
        sampled_mean = mean(sampled)
        boot.append(sampled_mean)

        if sampled_mean > 0:
            better += 1

    boot.sort()
    alpha = 1.0 - confidence_level

    return {
        "baseline_mean": mean(a),
        "candidate_mean": mean(b),
        "mean_delta": observed_delta,
        "lower_delta": _quantile(boot, alpha / 2.0),
        "upper_delta": _quantile(boot, 1.0 - alpha / 2.0),
        "confidence_level": confidence_level,
        "bootstrap_samples": bootstrap_samples,
        "probability_candidate_better": better / bootstrap_samples,
        "method": "paired_bootstrap",
    }


def _binomial_probability(n: int, k: int, p: float = 0.5) -> float:
    if not 0 <= k <= n:
        return 0.0

    log_probability = (
        math.lgamma(n + 1)
        - math.lgamma(k + 1)
        - math.lgamma(n - k + 1)
        + k * math.log(p)
        + (n - k) * math.log(1.0 - p)
    )

    return math.exp(log_probability)


def exact_two_sided_binomial_pvalue(
    successes: int,
    trials: int,
    null_probability: float = 0.5,
) -> float:
    """
    Exact two-sided binomial test using probability ordering.
    """
    if trials < 0:
        raise ValueError("trials must be >= 0.")
    if successes < 0 or successes > trials:
        raise ValueError("successes must be between 0 and trials.")
    if not 0.0 < null_probability < 1.0:
        raise ValueError("null_probability must be between 0 and 1.")

    if trials == 0:
        return 1.0

    observed = _binomial_probability(
        trials,
        successes,
        null_probability,
    )

    p_value = 0.0

    for k in range(trials + 1):
        probability = _binomial_probability(
            trials,
            k,
            null_probability,
        )

        if probability <= observed + 1e-15:
            p_value += probability

    return min(1.0, p_value)


def mcnemar_exact_test(
    baseline_correct: Sequence[bool | int],
    candidate_correct: Sequence[bool | int],
) -> Dict[str, Any]:
    """
    Exact McNemar test for paired binary outcomes.
    """
    if len(baseline_correct) != len(candidate_correct):
        raise ValueError(
            "baseline_correct and candidate_correct must have equal length."
        )
    if not baseline_correct:
        raise ValueError("paired outcomes cannot be empty.")

    both_correct = 0
    baseline_only = 0
    candidate_only = 0
    both_wrong = 0

    for a, b in zip(baseline_correct, candidate_correct):
        a_bool = bool(a)
        b_bool = bool(b)

        if a_bool and b_bool:
            both_correct += 1
        elif a_bool and not b_bool:
            baseline_only += 1
        elif not a_bool and b_bool:
            candidate_only += 1
        else:
            both_wrong += 1

    discordant = baseline_only + candidate_only

    p_value = (
        exact_two_sided_binomial_pvalue(
            candidate_only,
            discordant,
            0.5,
        )
        if discordant
        else 1.0
    )

    return {
        "n": len(baseline_correct),
        "both_correct": both_correct,
        "baseline_only_correct": baseline_only,
        "candidate_only_correct": candidate_only,
        "both_wrong": both_wrong,
        "discordant_pairs": discordant,
        "candidate_net_gain_cases": candidate_only - baseline_only,
        "p_value": p_value,
        "method": "mcnemar_exact",
    }


def paired_sign_test(
    baseline: Sequence[float | int],
    candidate: Sequence[float | int],
) -> Dict[str, Any]:
    """
    Exact paired sign test. Ties are excluded.
    """
    a = _clean_numeric(baseline, "baseline")
    b = _clean_numeric(candidate, "candidate")

    if len(a) != len(b):
        raise ValueError("baseline and candidate must have equal length.")
    if not a:
        raise ValueError("paired samples cannot be empty.")

    wins = losses = ties = 0

    for av, bv in zip(a, b):
        if bv > av:
            wins += 1
        elif bv < av:
            losses += 1
        else:
            ties += 1

    non_ties = wins + losses

    p_value = (
        exact_two_sided_binomial_pvalue(wins, non_ties, 0.5)
        if non_ties
        else 1.0
    )

    return {
        "n": len(a),
        "candidate_wins": wins,
        "candidate_losses": losses,
        "ties": ties,
        "non_tied_pairs": non_ties,
        "p_value": p_value,
        "method": "paired_sign_test_exact",
    }


def expected_calibration_error(
    probabilities: Sequence[float | int],
    outcomes: Sequence[bool | int],
    n_bins: int = 10,
) -> Dict[str, Any]:
    """
    Equal-width Expected Calibration Error.

    ECE should be interpreted together with Brier score and reliability plots.
    """
    if len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must have equal length.")
    if not probabilities:
        raise ValueError("calibration inputs cannot be empty.")
    if n_bins <= 0:
        raise ValueError("n_bins must be > 0.")

    probs = _clean_numeric(probabilities, "probabilities")

    for index, value in enumerate(probs):
        if not 0.0 <= value <= 1.0:
            raise ValueError(
                f"probabilities[{index}] must be between 0 and 1."
            )

    labels = [1.0 if bool(value) else 0.0 for value in outcomes]
    bins = []
    ece = 0.0
    total = len(probs)

    for bin_index in range(n_bins):
        lower = bin_index / n_bins
        upper = (bin_index + 1) / n_bins

        indices = [
            i
            for i, probability in enumerate(probs)
            if (
                lower <= probability < upper
                or (
                    bin_index == n_bins - 1
                    and probability == 1.0
                )
            )
        ]

        if not indices:
            bins.append(
                {
                    "bin_index": bin_index,
                    "count": 0,
                    "mean_confidence": None,
                    "empirical_accuracy": None,
                    "absolute_gap": None,
                }
            )
            continue

        confidence = mean(probs[i] for i in indices)
        accuracy = mean(labels[i] for i in indices)
        gap = abs(confidence - accuracy)

        ece += (len(indices) / total) * gap

        bins.append(
            {
                "bin_index": bin_index,
                "count": len(indices),
                "mean_confidence": confidence,
                "empirical_accuracy": accuracy,
                "absolute_gap": gap,
            }
        )

    return {
        "n": total,
        "n_bins": n_bins,
        "ece": ece,
        "bins": bins,
        "method": "equal_width_ece",
    }


def brier_score(
    probabilities: Sequence[float | int],
    outcomes: Sequence[bool | int],
) -> float:
    """
    Brier score for binary probabilistic predictions. Lower is better.
    """
    if len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must have equal length.")
    if not probabilities:
        raise ValueError("inputs cannot be empty.")

    probs = _clean_numeric(probabilities, "probabilities")
    labels = [1.0 if bool(value) else 0.0 for value in outcomes]

    for index, value in enumerate(probs):
        if not 0.0 <= value <= 1.0:
            raise ValueError(
                f"probabilities[{index}] must be between 0 and 1."
            )

    return mean(
        (probability - label) ** 2
        for probability, label in zip(probs, labels)
    )


def compare_paired_binary_variants(
    baseline_correct: Sequence[bool | int],
    candidate_correct: Sequence[bool | int],
    confidence_level: float = DEFAULT_CONFIDENCE,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    random_seed: int = DEFAULT_SEED,
) -> Dict[str, Any]:
    """
    Strong default comparison for binary evaluator outcomes.
    """
    if len(baseline_correct) != len(candidate_correct):
        raise ValueError(
            "baseline_correct and candidate_correct must have equal length."
        )
    if not baseline_correct:
        raise ValueError("paired outcomes cannot be empty.")

    baseline_numeric = [1.0 if bool(v) else 0.0 for v in baseline_correct]
    candidate_numeric = [1.0 if bool(v) else 0.0 for v in candidate_correct]

    baseline_ci = wilson_interval(
        int(sum(baseline_numeric)),
        len(baseline_numeric),
        confidence_level,
    )
    candidate_ci = wilson_interval(
        int(sum(candidate_numeric)),
        len(candidate_numeric),
        confidence_level,
    )

    return {
        "baseline": baseline_ci.to_dict(),
        "candidate": candidate_ci.to_dict(),
        "paired_bootstrap_delta": paired_bootstrap_difference(
            baseline_numeric,
            candidate_numeric,
            bootstrap_samples=bootstrap_samples,
            confidence_level=confidence_level,
            random_seed=random_seed,
        ),
        "mcnemar": mcnemar_exact_test(
            baseline_correct,
            candidate_correct,
        ),
        "interpretation_note": (
            "Statistical significance is not product significance. "
            "Inspect the effect size, error types, and user value as well."
        ),
    }