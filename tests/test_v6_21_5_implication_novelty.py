"""v6.21.5: implications that say something beyond the headlines.

Replay RUN-61A3D6B0BCDE4A67B32EB500 showed two checked implications that any
headline reader already expected ("strict EU enforcement may push providers to
prioritise compliance"; "scrutiny will lead to stricter oversight").

Method (how research-idea generators handle obviousness): over-generate,
remove near-duplicates, then filter with ENTAILMENT tests, not an LLM novelty
score (LLM novelty ratings agree poorly with experts):
  1. a plain "what a headline reader expects next" view already says it -> expected;
  2. the run's most relevant records already state it happened -> already reported.
Those are shown labelled "widely expected", so the section is never blank.
"""

from __future__ import annotations

import json

import pytest

import tests.test_v6_17_report_sections as base
from services import editorial_briefing as brief


@pytest.fixture(autouse=True)
def _private_ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_WATCH_ITEMS", str(tmp_path / "watch_items.sqlite3"))
    monkeypatch.setenv("EVENT_INTELLIGENCE_EVENT_MEMORY", str(tmp_path / "event_memory.sqlite3"))


OBVIOUS = "More vendors may ship agent permission controls."


def test_near_duplicate_implications_are_removed():
    items = [{"text": "More vendors may ship agent permission controls soon."},
             {"text": "More vendors may soon ship agent permission controls."},
             {"text": "Equals Money's read-only MCP design may become the template for UK payment APIs by 2027."}]
    assert [i["text"] for i in brief.dedupe_implications(items)] == [items[0]["text"], items[2]["text"]]


def test_classification_and_specific_first():
    items = [{"text": "A obvious one."}, {"text": "B already happened."}, {"text": "C new without number."},
             {"text": "D new with 3% threshold."}]
    claims = brief.novelty_claims(items, [{"evidence_id": "E1", "text": "B already happened today"}], True)
    statuses = {c["claim_id"]: "unsupported" for c in claims}
    statuses["NOVELTY-EXPECTED-00"] = "inference_supported"
    statuses["NOVELTY-REPORTED-01"] = "supported"
    fresh, expected = brief.apply_novelty(items, claims, statuses)
    assert [i["text"] for i in fresh] == ["D new with 3% threshold.", "C new without number."]
    assert [(i["text"], i["novelty_reasons"]) for i in expected] == [
        ("A obvious one.", ["expected_from_headlines"]), ("B already happened.", ["already_reported"])]


class Model:
    """Briefing model: the draft for the briefing prompt, a reader's view for the expected prompt."""
    timeout_seconds = 60
    max_retries = 3

    def ask(self, prompt, **kwargs):
        if "ordinary informed reader" in prompt:
            return json.dumps({"takeaways": ["Vendors will add more agent permission controls."]})
        return json.dumps(base.DRAFT)


class Grounding:
    def ground_claims(self, *, claims, evidence_records):
        out = []
        for c in claims:
            status = "supported" if c["claim_id"].startswith("NOVELTY-EXPECTED") else (
                "unsupported" if c["claim_id"].startswith("NOVELTY-REPORTED") else
                "supported" if c.get("epistemic_type") == "fact" else "inference_supported")
            out.append({"claim_id": c["claim_id"], "grounding_status": status,
                        "supporting_evidence_ids": list(c.get("evidence_ids") or [])[:1]})
        return {"claims": out}


def test_obvious_implication_is_labelled_and_the_briefing_is_never_blank():
    obj = base.orchestrator("{}", Grounding())
    obj.extraction_agent.llm = Model()
    out = obj._editorial_briefing(topic="AI", developments=base.DEVELOPMENTS, evidence=[],
                                  state={"historical_context": {"records": base.HISTORY}})
    assert out["mode"] == "checked_analysis"
    assert out["implications"] == []
    assert [i["text"] for i in out["expected_implications"]] == [OBVIOUS]
    assert out["expected_implications"][0]["novelty_reasons"] == ["expected_from_headlines"]
    assert out["novelty_check"]["expected_view"] == ["Vendors will add more agent permission controls."]
    assert brief.has_content(out)


def test_novelty_check_failure_keeps_implications():
    class Broken(Model):
        def ask(self, prompt, **kwargs):
            if "ordinary informed reader" in prompt:
                raise TimeoutError("slow")
            return json.dumps(base.DRAFT)

    class OnlyPass:
        def ground_claims(self, *, claims, evidence_records):
            return {"claims": [{"claim_id": c["claim_id"],
                                "grounding_status": "unsupported" if c["claim_id"].startswith("NOVELTY") else
                                ("supported" if c.get("epistemic_type") == "fact" else "inference_supported"),
                                "supporting_evidence_ids": list(c.get("evidence_ids") or [])[:1]} for c in claims]}

    obj = base.orchestrator("{}", OnlyPass())
    obj.extraction_agent.llm = Broken()
    out = obj._editorial_briefing(topic="AI", developments=base.DEVELOPMENTS, evidence=[],
                                  state={"historical_context": {"records": base.HISTORY}})
    assert [i["text"] for i in out["implications"]] == [OBVIOUS]
    assert out["novelty_check"]["expected_view_error"] == "TimeoutError"


def test_prompt_asks_for_specific_second_order_implications():
    prompt = brief.build_prompt("t", brief.select_facts(base.DEVELOPMENTS), [])
    assert "Write 4 to 6 implications" in prompt and "second-order consequence" in prompt
    assert "names who specifically gains or loses" in prompt and "NOT already happened" in prompt


def test_dashboard_shows_widely_expected_separately():
    import pathlib
    source = (pathlib.Path(__file__).resolve().parents[1] / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    assert "**Also backed by the sources, but widely expected**" in source  # v6.30.2: shown with citations
    assert 'briefing.get("expected_implications")' in source
