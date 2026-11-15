"""Plan fixes A, C, D (docs/PLAN_next_two_weeks.md, 23 Oct 2026)."""
import json
from datetime import datetime, timezone

from services import strategic_hypothesis_lab as lab

FACTS = [{"fact_id": "F7", "text": "Albanese said OpenAI took three months to alert the government."},
         {"fact_id": "F18", "text": "Australia set up a taskforce to examine AI incident reporting."},
         {"fact_id": "H1", "text": "Capital One disclosed its 2019 breach ten days after a tip."}]


# A. Bare fact-ID basis --------------------------------------------------------

def test_basis_of_only_fact_ids_is_resolved_to_fact_text():
    item = lab.resolve_basis_refs({"actor_move_basis": "F7", "countermove_basis": "F18 and H1."}, FACTS)
    assert item["actor_move_basis"] == "F7: " + FACTS[0]["text"]
    assert item["countermove_basis"].startswith("F18: Australia") and "; H1: Capital One" in item["countermove_basis"]
    assert item["actor_move_basis_as_written"] == "F7" and "unresolved_fact_ids" not in item


def test_prose_citing_fact_ids_is_unchanged():
    text = "President Trump announced the rename in official communications (F3, F5)."
    assert lab.resolve_basis_refs({"actor_move_basis": text}, FACTS)["actor_move_basis"] == text


def test_unknown_fact_id_is_flagged_not_dropped_and_not_graded_unsupported():
    item = lab.resolve_basis_refs({"id": "X", "thesis": "t", "mechanism": "m",
                                   "likely_actor_move": "OpenAI explains the delay.",
                                   "actor_move_basis": "F7, F99"}, FACTS)
    assert item["unresolved_fact_ids"] == {"actor_move_basis": ["F99"]}
    assert item["actor_move_basis_as_written"] == "F7, F99"
    claims = lab.grounding_claims(item, ["E1"])
    assert not any(c["claim_id"].endswith("-MOVEBASIS") for c in claims)
    grounded = [{"claim_id": c["claim_id"], "grounding_status": "supported"} for c in claims]
    assert lab.finalize(item, grounded, FACTS)["field_checks"]["actor_move_basis"] == "not_checked"


def test_resolved_basis_is_what_gets_grounded():
    item = lab.resolve_basis_refs({"id": "X", "thesis": "t", "mechanism": "m",
                                   "likely_actor_move": "OpenAI explains the delay.", "actor_move_basis": "F7"}, FACTS)
    basis = next(c for c in lab.grounding_claims(item, ["E1"]) if c["claim_id"].endswith("-MOVEBASIS"))
    assert basis["claim_text"] == "F7: " + FACTS[0]["text"] and basis["epistemic_type"] == "fact"


def test_basis_citing_a_fact_outside_fact_ids_carries_that_facts_evidence():
    # GPT review: basis "F18" on a candidate whose fact_ids are only F7 was grounded on F7's source.
    facts = [{"fact_id": "F7", "text": FACTS[0]["text"], "evidence_id": "EVID-F7"},
             {"fact_id": "F18", "text": FACTS[1]["text"], "evidence_id": "EVID-F18"}]
    item = lab.resolve_basis_refs({"id": "X", "thesis": "t", "mechanism": "m", "fact_ids": ["F7"],
                                   "likely_countermove": "Taskforce reviews reporting.",
                                   "countermove_basis": "F18"}, facts)
    assert item["basis_fact_ids"] == {"countermove_basis": ["F18"]}
    seed = lab.evidence_ids_for_fact_ids(facts, item["fact_ids"])
    basis = {f: lab.evidence_ids_for_fact_ids(facts, ids) for f, ids in item["basis_fact_ids"].items()}
    claims = {c["claim_id"].rsplit("-", 1)[-1]: c for c in lab.grounding_claims(item, seed, basis_evidence_ids=basis)}
    assert claims["COUNTERBASIS"]["evidence_ids"] == ["EVID-F7", "EVID-F18"]
    assert claims["THESIS"]["evidence_ids"] == ["EVID-F7"]   # the thesis is not widened


# D. Inherited critic scores ----------------------------------------------------

def narrowed():
    original = {"id": "EV1", "thesis": "Original overstated claim about paralysis.", "mechanism": "A -> B",
                "fact_ids": ["F7"], "critic_scores": {"mental_model_change": 2}, "critic_total": 10,
                "status": "promising_unverified", "actor_move_basis": "F7: text",
                "actor_move_basis_as_written": "F7"}
    reply = {"thesis": "The rename and the bill use conflicting terms across branches.",
             "mechanism": "Rename -> bill -> conflicting terms", "fact_ids": ["F7"], "what_was_narrowed": "cut paralysis"}
    return lab.parse_narrow(reply, original, FACTS)


def test_narrowed_rewrite_is_unscored_with_original_score_labelled():
    item = narrowed()
    assert "critic_total" not in item and "critic_scores" not in item
    assert item["critic_inherited_from"] == "EV1" and item["critic_total_before_narrowing"] == 10
    assert "actor_move_basis_as_written" not in item


def test_unscored_item_sorts_at_the_median_not_last():
    items = [{"id": "A", "critic_total": 10}, {"id": "B", "critic_total": 8},
             {"id": "N", "critic_inherited_from": "X"}, {"id": "C", "critic_total": 7}]
    assert [x["id"] for x in lab.sort_by_critic(items)] == ["A", "B", "N", "C"]
    assert [x["id"] for x in lab.sort_by_critic([{"id": "N"}])] == ["N"]


# C. Expert view definition -----------------------------------------------------

def _review(records):
    from services.hypothesis_evidence_service import HypothesisEvidenceService
    prompts = []

    class Model:
        model_name = "m"
        timeout_seconds = 30

        def ask(self, prompt, **kwargs):
            prompts.append(prompt)
            return json.dumps({"review_complete": True, "records": records})

    body = ("Experts warn the incident is another canary in the coal mine, said Dr Lee of the Lowy Institute. "
            "Albanese said he expressed extreme concern to OpenAI.")
    service = HypothesisEvidenceService(web_search_service=None, source_fetch_service=None, llm_service=Model())
    out = service._process_source(source={"url": "https://x.example/a", "text_content": body,
                                          "published_at": "2026-09-24T00:00:00Z"},
                                  context={}, candidate={"insight": "i"}, now=datetime(2026, 10, 9, tzinfo=timezone.utc),
                                  timeout=10, enforce_window=False)
    return prompts[0], out["accepted_evidence"]


def test_review_prompt_defines_independent_expert_views():
    prompt, _ = _review([])
    assert "NOT a party to the events" in prompt and "reported_claim" in prompt
    assert "credibility of the website does not change this" in prompt


def test_expert_and_party_statements_keep_their_claim_kind():
    # The model's classification is carried unchanged; investigate_gaps maps analysis -> commentary,
    # reported_claim -> reported. (This proves the contract, not that the model follows it.)
    _, records = _review([
        {"quote": "Experts warn the incident is another canary in the coal mine, said Dr Lee of the Lowy Institute.",
         "relation": "context", "claim_kind": "analysis", "connection": "c"},
        {"quote": "Albanese said he expressed extreme concern to OpenAI.",
         "relation": "context", "claim_kind": "reported_claim", "connection": "c"}])
    assert [r["structured_payload"]["claim_kind"] for r in records] == ["analysis", "reported_claim"]
