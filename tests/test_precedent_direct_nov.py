"""8 Nov 2026: precedent search asks for the direct precedent first (reference-class method)
and an 'outcome' must say what followed the case, not restate it."""
import inspect

from services import hypothesis_evidence_service as hes


def test_plan_asks_for_the_direct_precedent_and_what_followed():
    source = inspect.getsource(hes)
    assert "DIRECT precedent" in source and "what FOLLOWED it" in source
    assert "the case itself (who signed or announced what) is" in source


def test_outcome_that_restates_the_case_is_not_an_outcome():
    case = "In 2023 seven AI companies signed voluntary safety commitments at the White House."
    assert hes.outcome_restates_case("Seven AI companies signed the voluntary safety commitments at the White House.",
                                     case)
    assert not hes.outcome_restates_case("A year later only two had published third-party audit results.", case)
    assert not hes.outcome_restates_case("", case)
