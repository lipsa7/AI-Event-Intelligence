from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENT_PATH = ROOT / "agents" / "extraction_agent.py"

spec = importlib.util.spec_from_file_location("actor_boundary_agent", AGENT_PATH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
ExtractionAgent = module.ExtractionAgent


def entity_mentions(text: str):
    return ExtractionAgent._deep_anchor_entity_mentions_from_text(text)


def organizations(text: str):
    return [
        str(item.get("mention") or "")
        for item in entity_mentions(text)
        if isinstance(item, dict)
        and str(item.get("entity_type") or "").casefold() == "organization"
    ]


def test_v533_identity():
    assert module.AGENT_VERSION == (
        "extraction-agent-v5.3.3-deep-actor-recall-boundary-precision"
    )


def test_real_insilico_subject_survives():
    text = (
        "In June, Insilico Medicine announced a US$2.5 billion AI drug "
        "discovery collaboration with SKBiopharmaceuticals."
    )
    assert "Insilico Medicine" in organizations(text)


def test_passive_insilico_survives():
    text = (
        "The total contract value of transactions announced by "
        "Insilico Medicine in 2026 was approximately US$7.3 billion."
    )
    assert "Insilico Medicine" in organizations(text)


def test_hkex_appositive_survives():
    text = (
        "26, 2026 /PRNewswire/ -- Insilico Medicine (HKEX: 3696), "
        "a clinical-stage generative artificial intelligence AI-driven "
        "drug discovery company, recently released its interim results report."
    )
    assert "Insilico Medicine" in organizations(text)


def test_company_placeholder_stays_rejected():
    text = (
        "In March, the Company announced that it further expanded its "
        "drug discovery collaboration with Tenacia Biotechnology."
    )
    assert "Company" not in organizations(text)
    assert "The Company" not in organizations(text)


def test_real_actor_in_mixed_company_anchor_survives():
    text = (
        "In January, the Company announced a US$66 million global co-development "
        "collaboration with Hygtia Therapeutics. In the same month, "
        "Insilico Medicine reached an R&D collaboration with Qilu Pharmaceutical "
        "totaling nearly $120 million."
    )
    result = organizations(text)
    assert "Company" not in result
    assert "Insilico Medicine" in result


def test_no_prose_actor_from_tenacia_anchor():
    text = (
        "In March, the Company announced that it further expanded its drug discovery "
        "collaboration with Tenacia Biotechnology, entering into a CNS drug R&D "
        "collaboration with a total deal value of US$94.75 million. In the Asia-Pacific "
        "region, where generative AI drug discovery innovations continue to bring "
        "changes, the commercial footprint continues to expand, presenting new "
        "scenarios for pioneering AI application."
    )
    result = organizations(text)
    assert "scenarios for pioneering AI application. In March" not in result
    assert not any("pioneering AI application" in name for name in result)


def test_no_date_phrase_actor_from_cms_anchor():
    text = (
        "Furthermore, in February and June, the Company reached consecutively an "
        "early-stage collaboration worth tens of millions of RMB with CMS (China "
        "Medical System), followed by a deepened collaboration agreement totaling "
        "RMB 1.2 billion in June, to utilize generative AI for innovative solutions."
    )
    result = organizations(text)
    assert "in February and June" not in result
    assert not any(name.casefold().startswith("in february") for name in result)


def test_no_prose_actor_from_lilly_anchor():
    text = (
        "In March, the Company signed a global out-licensing and R&D collaboration "
        "agreement with Lilly valued up to US$2.75 billion. This marks Lilly's role "
        "transformation from a platform user to an R&D partner, reflecting continued "
        "strategic investment on AI-driven drug discovery."
    )
    result = organizations(text)
    assert not any("AI-driven drug discovery. In March" in name for name in result)


def test_no_prose_actor_from_takeda_anchor():
    text = (
        "In July, the Company announced a strategic collaboration with Takeda "
        "Pharmaceutical totaling approximately US$600 million, leveraging generative "
        "AI to empower multi-domain drug discovery and deeply participating in the "
        "strategic transformation of MNCs toward an AI-native R&D model."
    )
    result = organizations(text)
    assert "toward an AI-native R&D model. In July" not in result
    assert not any("AI-native R&D model" in name for name in result)


def test_unknown_acronym_precision_is_still_deferred():
    text = "AI investment in the US increased while IBD incidence was discussed."
    mentions = [
        str(item.get("mention") or "")
        for item in entity_mentions(text)
        if isinstance(item, dict)
    ]
    assert "US" in mentions
    assert "IBD" in mentions
