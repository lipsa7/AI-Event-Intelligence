"""12 Nov 2026 (cost): the same claim against the same records is checked once per run."""
from services.grounding_service import GroundingService

EVIDENCE = [{"evidence_id": "EVID-1", "text": "The accord has no penalties.", "source_id": "S1"},
            {"evidence_id": "EVID-2", "text": "The FTC opened a probe.", "source_id": "S2"}]


def _service(calls):
    def classifier(prompt):
        calls.append(prompt)
        return {"status": "supported", "supporting_evidence_ids": ["EVID-1"], "contradicted_evidence_ids": [],
                "attribution_valid": True, "temporal_valid": True, "numeric_valid": True,
                "unsupported_parts": [], "rationale": "stated"}
    return GroundingService(support_classifier=classifier)


def test_repeated_claim_is_not_rechecked_but_a_changed_one_is(monkeypatch):
    calls = []
    service = _service(calls)
    claim = {"claim_id": "A-P1", "claim_text": "The accord has no penalties.", "evidence_ids": ["EVID-1"],
             "epistemic_type": "fact"}
    first = service.ground_claims(claims=[claim], evidence_records=EVIDENCE)
    again = service.ground_claims(claims=[dict(claim, claim_id="B-P1")], evidence_records=EVIDENCE)
    assert len(calls) == 1 and again["claims"][0]["claim_id"] == "B-P1"
    assert again["claims"][0]["grounding_status"] == first["claims"][0]["grounding_status"]
    service.ground_claims(claims=[dict(claim, claim_text="The accord has penalties.")], evidence_records=EVIDENCE)
    changed_record = [dict(EVIDENCE[0], text="The accord sets fines."), EVIDENCE[1]]
    service.ground_claims(claims=[claim], evidence_records=changed_record)
    assert len(calls) == 3 and service.verdict_cache_stats == {"hits": 1, "misses": 3}
    monkeypatch.setenv("EVENT_INTELLIGENCE_GROUNDING_CACHE", "0")
    service.ground_claims(claims=[claim], evidence_records=EVIDENCE)
    assert len(calls) == 4
