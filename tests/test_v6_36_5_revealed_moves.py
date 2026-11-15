"""v6.36.5 - the three open items from the research review:
1. Precedent as a base rate (Halawi et al. 2024): a precedent implication says how many of the listed
   cases with the same mechanism ended in the outcome it expects ("2 of 3"), checked against those cases.
2. Predicted moves anchored in revealed behaviour (TMGBench; Lamparth et al.): a likely move or
   countermove states what the actor has ALREADY done that it follows from; that is checked as a fact,
   and a move without it is shown as a guess.
3. Your own hypotheses (Co-Scientist): replay_run.py --hypotheses FILE adds them to the evolution stage
   under the same rules and checks; compare_runs.py refuses a pair that used different ones.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

from services import actor_moves as moves
from services import editorial_briefing as brief
from services import strategic_hypothesis_lab as lab
from tests.test_v6_32_hypothesis_lab import FACTS as LAB_FACTS, rich_row

ROOT = Path(__file__).resolve().parents[1]
FACTS = [{"fact_id": "F1", "development": "agents", "evidence_id": "E1", "source_id": "S1", "date": "2026-10-01",
          "text": "Australia's tax office paused an AI agent after it accessed taxpayer records."}]
CASES = [{"case_id": f"H{n}", "evidence_id": f"EC{n}", "difference": "different regulator", "year": 2010 + n,
          "text": f"case {n}", "outcome": "the contract was re-tendered", "similarity": "same dependency"}
         for n in (1, 2, 3)]


def _precedent(**over):
    item = {"basis": "precedent", "case_id": "H1", "text": "Agencies may re-tender AI contracts after incidents.",
            "fact_ids": ["F1"], "conditions": "the pause lasts", "similar_case_ids": ["H1", "H2", "H3", "H9"],
            "outcome_case_ids": ["H1", "H3", "H7"]}
    item.update(over)
    return item


# --- 1. base rate -----------------------------------------------------------------------------

def test_precedent_records_a_base_rate_over_listed_cases_only():
    out = brief.parse_draft({"implications": [_precedent()]}, FACTS, CASES)
    rate = out["implications"][0]["base_rate"]
    assert rate["similar_case_ids"] == ["H1", "H2", "H3"]          # unknown H9 dropped
    assert rate["outcome_case_ids"] == ["H1", "H3"]                # unknown H7 dropped
    single = brief.parse_draft({"implications": [_precedent(similar_case_ids=[], outcome_case_ids=[])]},
                               FACTS, CASES)["implications"][0]["base_rate"]
    assert single["similar_case_ids"] == ["H1"]
    assert "outcome_case_ids" in brief.build_prompt("ai", FACTS, CASES)


def test_base_rate_is_checked_against_every_similar_case():
    draft = brief.parse_draft({"implications": [_precedent()]}, FACTS, CASES)
    rate = [c for c in brief.grounding_claims(draft, FACTS, CASES) if c.get("_field") == "base_rate"]
    assert len(rate) == 1 and rate[0]["_role"] == "step"
    assert "2 of 3" in rate[0]["claim_text"] and {"EC1", "EC2", "EC3"} <= set(rate[0]["evidence_ids"])
    one = brief.parse_draft({"implications": [_precedent(similar_case_ids=["H1"])]}, FACTS, CASES)
    assert not [c for c in brief.grounding_claims(one, FACTS, CASES) if c.get("_field") == "base_rate"]


def test_an_unbacked_base_rate_is_kept_as_a_labelled_assumption():
    draft = brief.parse_draft({"implications": [_precedent()]}, FACTS, CASES)
    claims = brief.grounding_claims(draft, FACTS, CASES)
    statuses = {c["claim_id"]: ("unsupported" if c["_field"] == "base_rate" else c["_bar"][-1]) for c in claims}
    shown = brief.sanitize(draft, claims, statuses, FACTS, CASES)["implications"]
    assert shown and shown[0]["base_rate"]["outcome_case_ids"] == ["H1", "H3"]
    assert "base_rate" in shown[0]["assumption_fields"]


# --- 2. predicted moves rest on revealed behaviour ---------------------------------------------

def test_lab_move_basis_is_asked_parsed_and_checked_as_a_fact():
    assert "actor_move_basis" in lab.generation_prompt("q", LAB_FACTS, ["lens"], batch=1)
    assert "ALREADY done" in lab.evolution_prompt("q", [], LAB_FACTS)
    row = dict(rich_row(), actor_move_basis="B already shifted capacity once in 2024.", countermove_basis="")
    item = lab.parse_generation({"hypotheses": [row]}, LAB_FACTS)[0]
    claims = {c["claim_id"].rsplit("-", 1)[-1]: c for c in lab.grounding_claims(item, ["E1"])}
    assert claims["MOVEBASIS"]["epistemic_type"] == "fact" and "COUNTERBASIS" not in claims
    grounded = [{"claim_id": c["claim_id"], "grounding_status": "unsupported" if c["claim_id"].endswith("-MOVEBASIS")
                 else "supported", "supporting_evidence_ids": ["E1"]} for c in claims.values()]
    final = lab.finalize(item, grounded, LAB_FACTS)
    assert final["field_checks"]["actor_move_basis"] == "unsupported"
    assert final["field_checks"]["likely_actor_move"] == "supported"   # the move itself is checked separately


def test_actor_moves_others_need_a_track_record_or_are_a_guess():
    assert "track_record" in moves.build_prompt("ai", FACTS)
    other = {"actor": "ATO", "likely_move": "re-tender the contract", "track_record": "the ATO re-tendered in 2019",
             "fact_ids": ["F1"]}
    actor = {"actor": "OpenAI", "crux": {}, "choice": {}, "moves": [], "rules_and_incentives": [], "powers": [],
             "must_be_true": [], "others": [other]}
    claims = moves.claims([actor], FACTS)
    record = [c for c in claims if tuple(c["_target"])[1] == "others_record"]
    assert record and record[0]["epistemic_type"] == "fact"
    rule = {"text": "r", "fact_ids": ["F1"]}
    kept_actor = dict(actor, rules_and_incentives=[rule],
                      moves=[{"move": "m", "why_it_benefits": "w", "fact_ids": ["F1"], "basis": "incentive"}])
    claims = moves.claims([kept_actor], FACTS)
    # Everything passes except: the track record is not backed, and the move has not already happened.
    statuses = {c["claim_id"]: "unsupported" if tuple(c["_target"])[1] in {"others_record", "already"} else "supported"
                for c in claims}
    out = moves.sanitize([kept_actor], claims, statuses, FACTS)
    shown = out["actors"][0]["others"][0]
    assert shown["guess"] is True and shown["track_record"] == ""


# --- 3. your own hypotheses -------------------------------------------------------------------

def test_user_hypotheses_file_is_read_and_enters_evolution(tmp_path, monkeypatch):
    path = tmp_path / "mine.txt"
    path.write_text("# my ideas\nGovernments will demand direct access to agent logs in new AI contracts.\n"
                    "short\nOpenAI's enterprise contracts give agencies less audit access than they need.\n",
                    encoding="utf-8")
    rows = lab.load_user_hypotheses(path)
    assert [r["id"] for r in rows] == ["U1", "U2"] and rows[0]["text"].startswith("Governments")
    prompt = lab.evolution_prompt("q", [], LAB_FACTS, user_hypotheses=rows)
    # v6.36.6: mixed in unlabelled (sycophancy), under a raw-looking id mapped back in code
    assert '"id": "B0H1"' in prompt and "Governments will demand" in prompt and "U1" not in prompt
    reply = {"hypotheses": [dict(rich_row(), source_ids=["B1H1", "B0H2"], evolution_note="from U2",
                                 critic_scores={k: 2 for k in ("mental_model_change", "mechanism_depth",
                                                               "surprise_nonobviousness", "strategic_consequence",
                                                               "falsifiability")})]}
    assert lab.parse_evolution(reply, LAB_FACTS)[0]["from_user"] == ["U2"]
    monkeypatch.setenv(lab.USER_HYPOTHESES_ENV, str(path))
    assert lab.user_hypotheses_fingerprint(lab.active_user_hypotheses())
    monkeypatch.delenv(lab.USER_HYPOTHESES_ENV)
    assert lab.active_user_hypotheses() == [] and lab.user_hypotheses_fingerprint([]) == ""


def test_orchestrator_replay_and_compare_wire_the_user_hypotheses():
    text = (ROOT / "services" / "research_orchestrator_service.py").read_text(encoding="utf-8")
    assert "user_hypotheses=user_hypotheses" in text and "if not raw and not user_hypotheses:" in text
    assert '"user_hypotheses": self._user_hypotheses_fingerprint()' in text
    replay = (ROOT / "replay_run.py").read_text(encoding="utf-8")
    assert '"--hypotheses"' in replay and 'os.environ.pop("EVENT_INTELLIGENCE_USER_HYPOTHESES", None)' in replay
    sys.path.insert(0, str(ROOT))
    compare = importlib.import_module("compare_runs")

    def report(profile, mine):
        return {"sections": {"analysis_metadata": {
            "experiment": {"replay_of": "RUN-1", "focus_organisation": "OpenAI", "advice_produced": True,
                           "service_version": "v", "topic": "t", "user_hypotheses": mine},
            "organisation_profile": {"used": profile}}}}
    assert compare.pair_problems(report(False, "abc"), report(True, "abc")) == []
    assert any("different hypotheses" in p for p in compare.pair_problems(report(False, ""), report(True, "abc")))


# --- dashboard --------------------------------------------------------------------------------

@pytest.fixture
def dash(monkeypatch):
    shown: list[str] = []
    fake = types.ModuleType("streamlit")
    fake.__getattr__ = lambda name: (lambda *a, **k: shown.append(" ".join(str(x) for x in a)))
    fake.cache_resource = fake.cache_data = lambda *a, **k: (lambda function: function)
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    monkeypatch.delitem(sys.modules, "frontend.dashboard", raising=False)
    module = importlib.import_module("frontend.dashboard")
    module._shown = shown
    yield module
    sys.modules.pop("frontend.dashboard", None)


def test_dashboard_shows_base_rate_move_basis_guesses_and_your_hypotheses(dash):
    item = dict(_precedent(), base_rate={"similar_case_ids": ["H1", "H2", "H3"], "outcome_case_ids": ["H1", "H3"],
                                         "stated": True}, assumption_fields=["base_rate"])
    single = dict(_precedent(text="Another."), base_rate={"similar_case_ids": ["H1"], "outcome_case_ids": ["H1"],
                                                           "stated": True})
    dash.render_editorial_briefing({"editorial_briefing": {"headline": "H", "implications": [item, single]}})
    shown = "\n".join(dash._shown)
    assert "2 of 3 similar past cases found by search ended this way (H1, H3) (assumption)" in shown
    assert "one past case only - no base rate" in shown
    hyp = dict(rich_row(), actor_move_basis="B shifted capacity once before.", countermove_basis="",
               field_checks={"actor_move_basis": "supported", "likely_actor_move": "inference_supported"},
               from_user=["U1"])
    lines = "\n".join(dash.hypothesis_lines(hyp, verified=True))
    assert "Based on what it has already done:* B shifted capacity once before. *(stated by the sources)*" in lines
    assert "A guess: no past behaviour in the sources is cited for this move." in lines   # the countermove
    assert "Developed from your own hypothesis U1" in lines
    dash._shown.clear()
    dash.render_strategic_hypotheses({"strategic_hypotheses": {
        "verified": [hyp], "user_hypotheses": [{"id": "U1", "text": "x"}, {"id": "U2", "text": "y"}]}})
    shown = "\n".join(dash._shown)
    assert "U1 (developed below)" in shown and "U2 (the facts did not support it" in shown
