"""v6.30.2 - settled rule (user, last month and again 2026-10-10): "if an analysis is backed,
give it, with the citations"; "what credible experts or orgs say can always be shown".

Before: backed analysis that experts already argued was moved to "widely expected" (or, for a
hidden insight, relabelled or dropped), and widely expected items were shown without citations.
Now: shown, labelled, with the expert or source citations. The search for an original hidden
insight still runs (hidden-insight loops are not cut).
"""

from __future__ import annotations

import json
import pathlib

import services.research_orchestrator_service as ros
from services import actor_moves as moves
from services import editorial_briefing as brief

EXPERT = {"evidence_id": "EVID-EXPERT", "source_id": "S9", "gap_type": "commentary", "gap_for": "x",
          "source_url": "https://www.lawfaremedia.org/article/agents", "published_at": "2026-10-03",
          "text": "Analysts argue agencies should price vendor-notice delays into AI contracts."}
FACTS = [{"text": "OpenAI notified Australia three months after its agent accessed Services Australia.",
          "evidence_id": "E1", "source_id": "S1", "date": "2026-09-24"}]


def test_expert_backed_step_stays_in_the_advice_with_the_expert_cited(monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_FOCUS_ORG", "")
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    obj._remaining_discretionary_seconds = lambda: float("inf")

    class Grounding:
        def ground_claims(self, *, claims, evidence_records):
            out = []
            for c in claims:
                if c["claim_id"].startswith("NOVELTY-EXPERT"):
                    status = "inference_supported" if "Price" in c["claim_text"] else "unsupported"
                elif c["claim_id"].startswith("NOVELTY-EXPECTED"):
                    status = "supported" if "Tighten" in c["claim_text"] else "unsupported"
                elif c["claim_id"].startswith("NOVELTY") or c["claim_text"].startswith(
                        (moves.ALREADY_PREFIX, moves.SWAP_PREFIX)):
                    status = "unsupported"
                else:
                    status = "supported" if c["epistemic_type"] == "fact" else "inference_supported"
                out.append({"claim_id": c["claim_id"], "grounding_status": status})
            return {"claims": out}

    obj.grounding_service = Grounding()
    draft = {"actors": [{"actor": "Services Australia",
                         "situation": [{"text": "It learned of the breach late.", "fact_ids": ["F1"]}],
                         "leverage": {}, "rules_and_incentives": [{"text": "It must report.", "fact_ids": ["F1"]}],
                         "moves": [{"basis": "incentive", "move": "Tighten security", "why_it_benefits": "b",
                                    "fact_ids": ["F1"]},
                                   {"basis": "precedent", "move": "Price vendor-notice delays into contracts",
                                    "why_it_benefits": "b", "fact_ids": ["F1"]}]}]}
    obj._ask_strong_model = lambda prompt: (json.dumps(draft), "pro")
    obj._ask_briefing_model = lambda prompt: json.dumps({"takeaways": ["Agencies will tighten security."]})
    obj._hypothesis_evidence_service = lambda: None
    obj._grounded_search = lambda state: None
    out = obj._actor_moves(topic="ai", developments=[{"title": "breach", "actors": ["OpenAI"], "facts": FACTS}],
                           evidence=[], state={"gap_context_records": [EXPERT]})
    actor = out["actors"][0]
    assert [m["move"] for m in actor["moves"]] == ["Price vendor-notice delays into contracts"]
    assert actor["moves"][0]["expert_sources"][0]["url"] == EXPERT["source_url"]
    assert [m["move"] for m in actor["expected_moves"]] == ["Tighten security"]
    assert actor["expected_moves"][0]["sources"]          # widely expected still carries its citations


def test_expert_citations_carry_link_publisher_and_quote():
    cites = brief.expert_citations([EXPERT, {"evidence_id": "E2", "gap_type": "policy", "text": "x"}],
                                   "price vendor notice delays into contracts")
    assert cites == [{"evidence_id": "EVID-EXPERT", "source_id": "S9", "fact_id": "expert view",
                      "url": EXPERT["source_url"], "publisher": "lawfaremedia.org", "credibility": "research institution",
                      "quote": EXPERT["text"], "date": "2026-10-03"}]


def test_dashboard_shows_backed_analysis_with_citations():
    dash = (pathlib.Path(__file__).resolve().parents[1] / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    assert "Experts or organisations also argue this:" in dash
    assert "experts or organisations also argue this: " in dash            # implications
    assert "**Backed by its sources:**" in dash and "def source_backed_line" in dash
    assert "**Also backed by the sources, but widely expected" in dash
    assert '_based on: {meta}_' in dash                                     # widely expected with citations


def test_source_backed_insight_reaches_the_report():
    import inspect
    source = inspect.getsource(ros.ResearchOrchestratorService)
    assert '"source_backed": _json_safe(insight_metadata.get("source_backed", {}))' in source
    assert ros.ResearchOrchestratorService.SOURCE_BACKED_MAX_SHOWN == 2
