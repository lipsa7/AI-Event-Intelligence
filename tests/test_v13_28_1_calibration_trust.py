"""v13.28.1: a judge whose scores carry no information must not become a trusted gate by luck."""
import numpy as np

from services import par_calibration


def _build(x, y):
    return par_calibration.build(x, y, model="m", items=list("abcdef"), exemplar_mode="nearest")


def test_a_judge_with_no_signal_is_almost_never_trusted():
    y = [1] * 33 + [0] * 57                     # the shape of the user's 90 ratings
    trusted = 0
    for seed in range(60):
        x = np.random.default_rng(seed).random((90, 7)).tolist()
        trusted += par_calibration.trusted(_build(x, y), "m")
    assert trusted <= 1, f"a no-signal judge was trusted in {trusted}/60 trials"


def test_a_judge_with_a_clear_signal_is_still_trusted():
    rng = np.random.default_rng(7)
    y = np.array([1] * 33 + [0] * 57)
    x = rng.random((90, 7))
    x[:, 0] = np.clip(0.2 + 0.5 * y + rng.normal(0, 0.15, 90), 0, 1)
    calibration = _build(x.tolist(), y.tolist())
    assert par_calibration.trusted(calibration, "m")
    assert calibration["cv_predicted_positive"] >= par_calibration.MIN_PREDICTED_POSITIVE
