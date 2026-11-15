"""v6.30: whose side the advice takes.

The deep research: products that give specific advice write for ONE known organisation
(Klue/Crayon: your company vs competitors; AlphaSense: chosen companies/watchlists; Feedly:
the user states role and goal). The user names the organisation (dashboard field or
replay --org); otherwise the app picks the named organisation the news involves most
(a fallback - none of those products auto-pick). Targeted research on that organisation
runs first, and its facts lead the context.
"""

from __future__ import annotations

import json
import pathlib

import services.research_orchestrator_service as ros
from services import actor_moves as moves

DEVELOPMENTS = [
    {"title": "OpenAI agent breach", "actors": ["Anthony Albanese", "OpenAI", "Australia"],
     "facts": [{"text": "OpenAI notified Australia three months after its agent accessed Services Australia.",
                "evidence_id": "E1", "source_id": "S1", "date": "2026-09-24"}]},
    {"title": "ChatGPT image leak", "actors": ["OpenAI"],
     "facts": [{"text": "OpenAI revealed that ChatGPT leaked 53 user-provided images.", "evidence_id": "E2",
                "source_id": "S2", "date": "2026-09-29"}]},
    {"title": "Super Intelligence renaming", "actors": ["Donald Trump", "tech CEOs and government officials"],
     "facts": [{"text": "Trump renamed AI 'super intelligence' in official communications.", "evidence_id": "E3",
                "source_id": "S3", "date": "2026-09-22"}]},
]


def test_auto_pick_is_the_named_organisation_the_news_involves_most():
    from services import editorial_briefing as brief
    facts = brief.select_facts(DEVELOPMENTS)
    assert moves.pick_focus(DEVELOPMENTS, facts) == "OpenAI"
    assert moves.pick_focus([{"actors": ["tech CEOs and government officials"]}], facts) == ""


def test_prompt_puts_the_organisation_first_and_in_depth():
    prompt = moves.build_prompt("ai", [{"fact_id": "F1", "development": "d", "text": "t", "evidence_id": "E1"}],
                                focus="Services Australia")
    assert "ORGANISATION TO ADVISE: Services Australia" in prompt
    assert "The first actor MUST be Services Australia" in prompt
    assert "ORGANISATION TO ADVISE" not in moves.build_prompt("ai", [], focus="")


def test_focus_development_asks_for_the_organisations_position():
    dev = moves.focus_development("OpenAI", [{"text": "OpenAI did x", "evidence_id": "E1", "date": "d"},
                                             {"text": "Other news", "evidence_id": "E2"}])
    assert dev["title"].startswith("OpenAI: position, exposure and options") and dev["actors"] == ["OpenAI"]
    assert [f["evidence_id"] for f in dev["facts"]] == ["E1"]


class Grounding:
    def ground_claims(self, *, claims, evidence_records):
        return {"claims": [{"claim_id": c["claim_id"], "grounding_status": (
            "unsupported" if c["claim_id"].startswith("NOVELTY") or c["claim_text"].startswith(
                (moves.ALREADY_PREFIX, moves.SWAP_PREFIX))
            else "supported" if c["epistemic_type"] == "fact" else "inference_supported")} for c in claims]}


def _orchestrator(monkeypatch, focus_env=""):
    monkeypatch.setenv("EVENT_INTELLIGENCE_FOCUS_ORG", focus_env)
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    obj._remaining_discretionary_seconds = lambda: float("inf")
    obj.grounding_service = Grounding()
    seen = {}

    class Service:
        def investigate_gaps(self, *, development, blueprint, search):
            seen["development"] = development
            return {"status": "complete", "queries": [{"gap": "policy", "query": "openai rules"}], "records": [
                {"evidence_id": "EVID-ORG", "source_id": "S9", "gap_type": "policy", "analytical_context_only": True,
                 "gap_for": development["title"], "text": "OpenAI must report serious incidents under the EU AI Act."}]}

    obj._hypothesis_evidence_service = lambda: Service()
    obj._grounded_search = lambda state: None
    prompts = []

    def strong(prompt):
        prompts.append(prompt)
        return json.dumps({"actors": []}), "pro"

    obj._ask_strong_model = strong
    obj._ask_briefing_model = lambda prompt: json.dumps({"takeaways": []})
    return obj, seen, prompts


def test_named_organisation_is_researched_and_advised(monkeypatch):
    obj, seen, prompts = _orchestrator(monkeypatch, "Services Australia")
    state = {"gap_context_records": [{"evidence_id": "EVID-OLD", "source_id": "S8", "gap_type": "policy",
                                      "gap_for": "x", "text": "An older rule that applies to someone else entirely."}]}
    out = obj._actor_moves(topic="ai", developments=DEVELOPMENTS, evidence=[], state=state)
    assert out["focus_organisation"]["name"] == "Services Australia"
    assert out["focus_organisation"]["chosen_by"] == "user"
    assert seen["development"]["actors"] == ["Services Australia"]
    assert out["focus_organisation"]["research"]["records"] == 1
    assert "ORGANISATION TO ADVISE: Services Australia" in prompts[0]
    # facts about the advised organisation lead the context facts
    assert prompts[0].index("EU AI Act") < prompts[0].index("older rule")


def test_auto_picked_organisation_is_labelled(monkeypatch):
    obj, _, prompts = _orchestrator(monkeypatch, "")
    out = obj._actor_moves(topic="ai", developments=DEVELOPMENTS, evidence=[], state={})
    assert out["focus_organisation"]["name"] == "OpenAI" and out["focus_organisation"]["chosen_by"] == "auto"


def test_dashboard_field_and_replay_option():
    root = pathlib.Path(__file__).resolve().parents[1]
    dash = (root / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    assert '"Organisation to advise (optional)"' in dash and 'os.environ["EVENT_INTELLIGENCE_FOCUS_ORG"]' in dash
    assert "**Advising:**" in dash
    replay = (root / "replay_run.py").read_text(encoding="utf-8")
    assert '"--org"' in replay and 'os.environ["EVENT_INTELLIGENCE_FOCUS_ORG"]' in replay
