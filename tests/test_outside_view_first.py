"""12 Nov 2026: precedent (outside view) is reasoned before leverage, and the prompt tells the
model that judgments are not source-checked (Good Judgment Project; claim-support gate v4.0)."""
import inspect

import agents.strategic_inference_agent as agent


def test_outside_view_comes_before_leverage_and_judgments_are_not_watered_down():
    source = inspect.getsource(agent)
    assert 0 < source.find("OUTSIDE VIEW FIRST") < source.find("LEVERAGE PLUS INCENTIVES")
    assert "must not water them down to what a source says" in source
    assert "do not invent one" in source
