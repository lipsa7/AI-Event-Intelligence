"""12 Nov 2026: an outlet citing another outlet is the same source as that outlet (AP attribution
rule; Admiralty 'confirmed' needs independent sources)."""
from services import claim_support_gate as gate
from services import source_origin as so


def test_cited_outlet_is_the_origin_and_actor_attribution_is_not():
    assert so.cited_origin("The FTC is probing OpenAI, according to The New York Times.") == "nyt"
    assert so.cited_origin("SiliconANGLE, citing a report in the Wall Street Journal, said ...") == "wsj"
    assert so.cited_origin("Reuters reported that the order was signed.") == "reuters"
    assert so.cited_origin("According to Google's filing, revenue rose.") == ""
    assert so.cited_origin("citing the IMF's forecast") == ""


def test_outlet_citing_nyt_and_nyt_itself_count_as_one_source():
    evidence = [{"evidence_id": "EVID-A", "source_id": "S1", "text": "FTC probe, the New York Times reported."},
                {"evidence_id": "EVID-B", "source_id": "S2", "text": "The FTC opened a probe."},
                {"evidence_id": "EVID-C", "source_id": "S3", "text": "Regulators confirmed the probe."}]
    sources = so.origin_sources(evidence, {"EVID-B": "www.nytimes.com", "EVID-C": "ftc.gov"})
    assert sources["EVID-A"] == sources["EVID-B"] == "ORIGIN:nyt" and sources["EVID-C"] == "S3"
    fact = {"claim_id": "R-P1", "epistemic_type": "fact", "grounding_status": "supported",
            "claim_text": "The FTC is probing OpenAI.", "supporting_evidence_ids": ["EVID-A", "EVID-B"]}
    concl = {"claim_id": "R-INSIGHT-01", "epistemic_type": "hidden_insight",
             "grounding_status": "inference_supported", "claim_text": "X.", "supporting_evidence_ids": ["EVID-A"]}
    verdict = gate.evaluate({"review_status": "complete", "claims": [fact, concl]}, sources)
    assert verdict["passed"] and verdict["confidence"].startswith("moderate")
    assert verdict["single_source_claims"][0]["source_id"] == "ORIGIN:nyt"
    fact["supporting_evidence_ids"] = ["EVID-A", "EVID-C"]
    assert gate.evaluate({"review_status": "complete", "claims": [fact, concl]}, sources)["confidence"].startswith("high")
