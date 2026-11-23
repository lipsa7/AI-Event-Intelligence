"""v13.31.2: a repair that lengthens the thesis is shortened again (run of 23 Nov: 5 of 8 failed 'concise')."""
import json

from services import research_orchestrator_service as ros
from services import variant_view

LONG = ("By framing OpenAI's actions under the Texas Deceptive Trade Practices-Consumer Protection Act, Texas seeks "
        "to mandate a court-appointed independent third-party evaluator to approve new ChatGPT models before release.")
SHORT = "Texas wants a court-appointed evaluator to approve new ChatGPT models before release."


def _service(answers):
    cls = next(v for v in vars(ros).values() if isinstance(v, type) and hasattr(v, "_variant_repair"))
    svc = cls.__new__(cls)
    queue = list(answers)
    svc._ask_briefing_model = lambda prompt: queue.pop(0)
    return svc, queue


def test_repair_that_lengthens_thesis_is_shortened_again():
    svc, queue = _service([json.dumps({"insight": LONG + " OpenAI will move to strike it by June 2027."}),
                           json.dumps({"sentence": SHORT})])
    out = svc._variant_repair({"insight": "old"}, ["no_number"], [])
    assert out["insight"].startswith(SHORT)
    assert "June 2027" in out["insight"]
    assert not any(p.startswith("thesis_") for p in variant_view.plain_language_problems(out))
    assert queue == []


def test_short_repair_makes_no_extra_call():
    svc, queue = _service([json.dumps({"insight": SHORT + " More."}), "unused"])
    out = svc._variant_repair({"insight": "old"}, ["no_number"], [])
    assert out["insight"].startswith(SHORT) and queue == ["unused"]


def test_failed_shortening_keeps_the_repair():
    svc, _ = _service([json.dumps({"insight": LONG}), json.dumps({"sentence": LONG})])
    out = svc._variant_repair({"insight": "old"}, ["no_number"], [])
    assert out["insight"] == LONG
