"""v13.18: claim support checks are sent in batches; verdicts match one-call-per-claim checks."""
import json
import re

import pytest

from services.grounding_service import GroundingService

EVIDENCE = [
    {"evidence_id": "EVID-1", "text": "Acme raised $10 million in October 2026.", "source_id": "S1"},
    {"evidence_id": "EVID-2", "text": "Acme hired 40 engineers in November 2026.", "source_id": "S2"},
    {"evidence_id": "EVID-3", "text": "Beta Corp cut prices by 20% in November 2026.", "source_id": "S3"},
]
CLAIMS = [
    {"claim_id": "C1", "claim_text": "Acme raised $10 million.", "evidence_ids": ["EVID-1"], "epistemic_type": "fact"},
    {"claim_id": "C2", "claim_text": "Acme hired 40 engineers.", "evidence_ids": ["EVID-2"], "epistemic_type": "fact"},
    {"claim_id": "C3", "claim_text": "Beta Corp raised prices.", "evidence_ids": ["EVID-3"], "epistemic_type": "fact"},
]


def verdict(claim_text, ids):
    if "raised prices" in claim_text:
        return {"status": "contradicted", "supporting_evidence_ids": [], "contradicted_evidence_ids": ids,
                "attribution_valid": True, "temporal_valid": True, "numeric_valid": True,
                "unsupported_parts": ["prices were cut"], "rationale": "cut, not raised"}
    return {"status": "supported", "supporting_evidence_ids": ids, "contradicted_evidence_ids": [],
            "attribution_valid": True, "temporal_valid": True, "numeric_valid": True,
            "unsupported_parts": [], "rationale": "stated"}


def item_parts(prompt):
    claims = re.findall(r"Claim:\n(.*?)\n", prompt)
    ids = [re.findall(r'"evidence_id": "(EVID-\d)"', chunk) for chunk in prompt.split("===== ITEM")[1:]] \
        or [re.findall(r'"evidence_id": "(EVID-\d)"', prompt)]
    return claims, ids


class Fake:
    def __init__(self, batch_mode="ok"):
        self.calls = []
        self.batch_mode = batch_mode

    def __call__(self, prompt):
        self.calls.append(prompt)
        claims, ids = item_parts(prompt)
        if "===== ITEM" in prompt:
            if self.batch_mode == "fail":
                raise RuntimeError("model error")
            results = [dict(verdict(c, i), item=n + 1) for n, (c, i) in enumerate(zip(claims, ids))]
            if self.batch_mode == "bad_second":
                results[1]["supporting_evidence_ids"] = ["EVID-999"]   # illegal id: must be re-checked alone
            return json.dumps({"results": results})
        return json.dumps(verdict(claims[0], ids[0]))


def service(fake, monkeypatch, batch="6"):
    monkeypatch.setenv("EVENT_INTELLIGENCE_GROUNDING_BATCH", batch)
    monkeypatch.setenv("EVENT_INTELLIGENCE_GROUNDING_CACHE", "0")
    return GroundingService(support_classifier=fake, model_name="fake")


def statuses(result):
    return [(c["claim_id"], c["grounding_status"], c["supporting_evidence_ids"]) for c in result["claims"]]


def test_three_claims_one_call_same_verdicts(monkeypatch):
    single = Fake()
    expected = service(single, monkeypatch, batch="1").ground_claims(claims=CLAIMS, evidence_records=EVIDENCE)
    assert len(single.calls) == 3
    batched = Fake()
    got = service(batched, monkeypatch).ground_claims(claims=CLAIMS, evidence_records=EVIDENCE)
    assert len(batched.calls) == 1
    assert statuses(got) == statuses(expected)
    assert got["status"] == expected["status"] == "contradicted"


def test_each_item_gets_only_its_own_evidence(monkeypatch):
    fake = Fake()
    service(fake, monkeypatch).ground_claims(claims=CLAIMS, evidence_records=EVIDENCE)
    items = fake.calls[0].split("===== ITEM")[1:]
    assert [re.findall(r"EVID-\d", item) for item in items] == [["EVID-1"], ["EVID-2"], ["EVID-3"]]


def test_failed_batch_falls_back_to_single_calls(monkeypatch):
    fake = Fake(batch_mode="fail")
    got = service(fake, monkeypatch).ground_claims(claims=CLAIMS, evidence_records=EVIDENCE)
    assert len(fake.calls) == 4          # 1 failed batch + 3 single checks
    assert [s for _, s, _ in statuses(got)] == ["supported", "supported", "contradicted"]


def test_invalid_batched_verdict_is_rechecked_alone(monkeypatch):
    fake = Fake(batch_mode="bad_second")
    got = service(fake, monkeypatch).ground_claims(claims=CLAIMS, evidence_records=EVIDENCE)
    assert len(fake.calls) == 2          # batch + one re-check for C2
    assert statuses(got)[1] == ("C2", "supported", ["EVID-2"])


def test_supplied_classifier_without_setting_keeps_one_call_per_claim(monkeypatch):
    monkeypatch.delenv("EVENT_INTELLIGENCE_GROUNDING_BATCH", raising=False)
    fake = Fake()
    GroundingService(support_classifier=fake, model_name="fake").ground_claims(claims=CLAIMS, evidence_records=EVIDENCE)
    assert len(fake.calls) == 3


def test_single_claim_unchanged(monkeypatch):
    fake = Fake()
    got = service(fake, monkeypatch).ground_claims(claims=CLAIMS[:1], evidence_records=EVIDENCE)
    assert len(fake.calls) == 1 and "===== ITEM" not in fake.calls[0]
    assert statuses(got) == [("C1", "supported", ["EVID-1"])]


def test_duplicate_claim_ids_still_rejected(monkeypatch):
    from services.grounding_service import GroundingValidationError
    with pytest.raises(GroundingValidationError):
        service(Fake(), monkeypatch).ground_claims(claims=[CLAIMS[0], CLAIMS[0]], evidence_records=EVIDENCE)


def test_batching_with_verdict_cache_on(monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_GROUNDING_BATCH", "6")
    monkeypatch.setenv("EVENT_INTELLIGENCE_GROUNDING_CACHE", "1")
    fake = Fake()
    svc = GroundingService(support_classifier=fake, model_name="fake")
    first = svc.ground_claims(claims=CLAIMS, evidence_records=EVIDENCE)
    again = svc.ground_claims(claims=CLAIMS, evidence_records=EVIDENCE)
    assert len(fake.calls) == 1                     # second pass answered from the cache
    assert statuses(first) == statuses(again)
    assert svc.verdict_cache_stats == {"hits": 3, "misses": 3}


def test_one_at_a_time_callers_use_batched_verdicts(monkeypatch):
    """The briefing and actor moves check claims one by one (one bad check must not void the rest)."""
    fake = Fake()
    svc = service(fake, monkeypatch)
    got = []
    with svc.batched_checks(CLAIMS, EVIDENCE):
        for claim in CLAIMS:
            got.extend(svc.ground_claims(claims=[claim], evidence_records=EVIDENCE)["claims"])
    assert len(fake.calls) == 1
    assert [c["grounding_status"] for c in got] == ["supported", "supported", "contradicted"]
    # after the block, a new check makes its own call again
    svc.ground_claims(claims=[CLAIMS[0]], evidence_records=EVIDENCE)
    assert len(fake.calls) == 2


def test_batch_refused_by_call_cap_falls_back_to_one_at_a_time(monkeypatch):
    class Budget(Exception):
        pass
    Budget.__name__ = "GeminiCallBudgetExceeded"
    fake = Fake()
    def classify(prompt):
        if "===== ITEM" in prompt:
            raise Budget("cap")
        return fake(prompt)
    svc = service(classify, monkeypatch)
    with svc.batched_checks(CLAIMS, EVIDENCE):
        got = [svc.ground_claims(claims=[c], evidence_records=EVIDENCE)["claims"][0]["grounding_status"] for c in CLAIMS]
    assert got == ["supported", "supported", "contradicted"]
