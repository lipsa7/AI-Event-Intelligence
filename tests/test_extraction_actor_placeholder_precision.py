from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENT_PATH = ROOT / "agents" / "extraction_agent.py"

spec = importlib.util.spec_from_file_location("actor_precision_agent", AGENT_PATH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
ExtractionAgent = module.ExtractionAgent


def mentions(text: str):
    return ExtractionAgent._deep_anchor_entity_mentions_from_text(text)


def names(text: str):
    return [
        str(item.get("mention") or "")
        for item in mentions(text)
        if isinstance(item, dict)
    ]


def org_names(text: str):
    return [
        str(item.get("mention") or "")
        for item in mentions(text)
        if isinstance(item, dict)
        and str(item.get("entity_type") or "").casefold() == "organization"
    ]


def test_v532_identity():
    assert "v5.3.2-deep-actor-recall-placeholder-precision" in module.AGENT_VERSION


def test_company_placeholder_is_not_recovered_as_named_actor():
    text = (
        "In March, the Company announced that it further expanded its "
        "drug discovery collaboration with Tenacia Biotechnology."
    )
    assert "Company" not in org_names(text)


def test_the_company_placeholder_is_not_recovered_as_named_actor():
    text = "The Company signed a global R&D collaboration agreement with Lilly."
    assert "The Company" not in org_names(text)
    assert "Company" not in org_names(text)


def test_real_insilico_actor_is_recovered():
    text = (
        "In June, Insilico Medicine announced a US$2.5 billion AI drug "
        "discovery collaboration with SKBiopharmaceuticals."
    )
    assert "Insilico Medicine" in org_names(text)


def test_real_actor_wins_in_mixed_company_and_insilico_anchor():
    text = (
        "In January, the Company announced a US$66 million global co-development "
        "collaboration with Hygtia Therapeutics. In the same month, Insilico Medicine "
        "reached an R&D collaboration with Qilu Pharmaceutical totaling nearly $120 million."
    )
    result = org_names(text)
    assert "Company" not in result
    assert "Insilico Medicine" in result


def test_hkex_appositive_still_recovers_insilico():
    text = (
        "26, 2026 /PRNewswire/ -- Insilico Medicine (HKEX: 3696), "
        "a clinical-stage generative artificial intelligence (AI)-driven "
        "drug discovery company, recently released its interim results report for 2026."
    )
    assert "Insilico Medicine" in org_names(text)


def test_unknown_acronym_mentions_are_not_removed_by_this_patch():
    text = "AI investment in the US increased while IBD incidence was discussed."
    result = names(text)
    # This patch is intentionally NOT the unknown-acronym/agency redesign.
    assert "US" in result
    assert "IBD" in result


def test_definite_article_generic_firm_is_placeholder():
    text = "The Firm announced a new financing agreement."
    assert "The Firm" not in org_names(text)
    assert "Firm" not in org_names(text)
