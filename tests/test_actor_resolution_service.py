from __future__ import annotations

from pathlib import Path
import importlib.util

import spacy
from spacy.tokens import Doc, Span


ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = ROOT / "services" / "actor_resolution_service.py"

spec = importlib.util.spec_from_file_location("actor_resolution_service", SERVICE_PATH)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = module
spec.loader.exec_module(module)

ActorResolutionService = module.ActorResolutionService


class StaticNLP:
    _actor_resolution_test_double = True
    pipe_names = ["parser", "ner"]
    meta = {"version": "test", "lang": "en"}

    def __init__(self):
        self.vocab = spacy.blank("en").vocab
        self._docs = {}

    def add(
        self,
        text: str,
        *,
        words,
        heads,
        deps,
        ents=(),
        lemmas=None,
    ):
        doc = Doc(
            self.vocab,
            words=words,
            heads=heads,
            deps=deps,
            lemmas=lemmas or [word.casefold() for word in words],
        )
        doc.ents = [
            Span(doc, start, end, label=label)
            for start, end, label in ents
        ]
        self._docs[text] = doc

    def __call__(self, text: str):
        if text in self._docs:
            return self._docs[text]
        return Doc(self.vocab, words=text.split())


def service_with_docs():
    nlp = StaticNLP()

    nlp.add(
        "The US imposed restrictions.",
        words=["The", "US", "imposed", "restrictions", "."],
        heads=[1, 2, 2, 2, 2],
        deps=["det", "nsubj", "ROOT", "dobj", "punct"],
        ents=[(1, 2, "GPE")],
        lemmas=["the", "US", "impose", "restriction", "."],
    )

    nlp.add(
        "AI investment in the US rose.",
        words=["AI", "investment", "in", "the", "US", "rose", "."],
        heads=[1, 5, 1, 4, 2, 5, 5],
        deps=["compound", "nsubj", "prep", "det", "pobj", "ROOT", "punct"],
        ents=[(4, 5, "GPE")],
        lemmas=["AI", "investment", "in", "the", "US", "rise", "."],
    )

    nlp.add(
        "The UAE announced a national AI strategy.",
        words=["The", "UAE", "announced", "a", "national", "AI", "strategy", "."],
        heads=[1, 2, 2, 6, 6, 6, 2, 2],
        deps=["det", "nsubj", "ROOT", "det", "amod", "compound", "dobj", "punct"],
        ents=[(1, 2, "GPE")],
        lemmas=["the", "UAE", "announce", "a", "national", "AI", "strategy", "."],
    )

    nlp.add(
        "In June, Insilico Medicine announced a partnership.",
        words=["In", "June", ",", "Insilico", "Medicine", "announced", "a", "partnership", "."],
        heads=[1, 5, 1, 4, 5, 5, 7, 5, 5],
        deps=["prep", "pobj", "punct", "compound", "nsubj", "ROOT", "det", "dobj", "punct"],
        ents=[(1, 2, "DATE"), (3, 5, "ORG")],
        lemmas=["in", "June", ",", "Insilico", "Medicine", "announce", "a", "partnership", "."],
    )

    nlp.add(
        "The Company announced a partnership.",
        words=["The", "Company", "announced", "a", "partnership", "."],
        heads=[1, 2, 2, 4, 2, 2],
        deps=["det", "nsubj", "ROOT", "det", "dobj", "punct"],
        ents=[(1, 2, "ORG")],
        lemmas=["the", "company", "announce", "a", "partnership", "."],
    )

    nlp.add(
        "R&D expanded.",
        words=["R&D", "expanded", "."],
        heads=[1, 1, 1],
        deps=["nsubj", "ROOT", "punct"],
        ents=[],
        lemmas=["R&D", "expand", "."],
    )

    nlp.add(
        "CMS announced a collaboration.",
        words=["CMS", "announced", "a", "collaboration", "."],
        heads=[1, 1, 3, 1, 1],
        deps=["nsubj", "ROOT", "det", "dobj", "punct"],
        ents=[],
        lemmas=["CMS", "announce", "a", "collaboration", "."],
    )

    nlp.add(
        "China Medical System",
        words=["China", "Medical", "System"],
        heads=[2, 2, 2],
        deps=["compound", "compound", "ROOT"],
        ents=[(0, 3, "ORG")],
        lemmas=["China", "Medical", "System"],
    )

    nlp.add(
        "Restrictions were imposed by the US.",
        words=["Restrictions", "were", "imposed", "by", "the", "US", "."],
        heads=[2, 2, 2, 2, 5, 3, 2],
        deps=["nsubjpass", "auxpass", "ROOT", "agent", "det", "pobj", "punct"],
        ents=[(5, 6, "GPE")],
        lemmas=["restriction", "be", "impose", "by", "the", "US", "."],
    )

    nlp.add(
        "According to Reuters, Microsoft announced a partnership.",
        words=["According", "to", "Reuters", ",", "Microsoft", "announced", "a", "partnership", "."],
        heads=[5, 0, 1, 2, 5, 5, 7, 5, 5],
        deps=["advcl", "prep", "pobj", "punct", "nsubj", "ROOT", "det", "dobj", "punct"],
        ents=[(2, 3, "ORG"), (4, 5, "ORG")],
        lemmas=["accord", "to", "Reuters", ",", "Microsoft", "announce", "a", "partnership", "."],
    )

    return ActorResolutionService(model_name="static-test", nlp=nlp)


def primary(result):
    return result["primary_actor"]


def find_mention(result, surface):
    for item in result["mentions"]:
        if item["surface_form"] == surface:
            return item
    raise AssertionError(f"mention not found: {surface!r}")


def test_us_subject_is_actor_without_manual_country_dictionary():
    svc = service_with_docs()
    result = svc.resolve_event(event_text="The US imposed restrictions.")
    actor = primary(result)
    assert actor is not None
    assert actor["surface_form"] == "US"
    assert actor["role_status"] == "action_subject"
    assert actor["identity_status"] == "source_supported"
    assert actor["entity_type"] == "geopolitical_entity"


def test_us_in_prepositional_location_is_not_actor():
    svc = service_with_docs()
    result = svc.resolve_event(event_text="AI investment in the US rose.")
    assert primary(result) is None
    us = find_mention(result, "US")
    assert us["role_status"] == "location"
    assert result["admission_actor_ready"] is False


def test_uae_subject_is_actor_without_uae_lookup_table():
    svc = service_with_docs()
    result = svc.resolve_event(event_text="The UAE announced a national AI strategy.")
    actor = primary(result)
    assert actor is not None
    assert actor["surface_form"] == "UAE"
    assert actor["role_status"] == "action_subject"
    assert actor["identity_status"] == "source_supported"


def test_june_is_temporal_and_insilico_is_actor():
    svc = service_with_docs()
    result = svc.resolve_event(
        event_text="In June, Insilico Medicine announced a partnership."
    )
    actor = primary(result)
    assert actor is not None
    assert actor["surface_form"] == "Insilico Medicine"
    june = find_mention(result, "June")
    assert june["role_status"] == "temporal"
    assert june["actor_ready"] is False


def test_the_company_keeps_role_but_fails_identity_axis():
    svc = service_with_docs()
    result = svc.resolve_event(
        event_text="The Company announced a partnership."
    )
    assert primary(result) is None
    company = find_mention(result, "Company")
    assert company["role_status"] == "action_subject"
    assert company["identity_status"] == "unresolved"
    assert company["placeholder"] is True
    assert "actor_identity_unresolved" in result["actor_gate_failure_reasons"]


def test_rd_can_have_grammatical_agency_without_resolved_identity():
    svc = service_with_docs()
    result = svc.resolve_event(event_text="R&D expanded.")
    assert primary(result) is None
    rd = find_mention(result, "R&D")
    assert rd["role_status"] == "action_subject"
    assert rd["identity_status"] == "unresolved"
    assert result["admission_actor_ready"] is False


def test_source_local_alias_resolves_without_global_acronym_dictionary():
    svc = service_with_docs()
    result = svc.resolve_event(
        event_text="CMS announced a collaboration.",
        source_text="China Medical System (CMS) announced an expansion.",
    )
    actor = primary(result)
    assert actor is not None
    assert actor["surface_form"] == "CMS"
    assert actor["canonical_name"] == "China Medical System"
    assert actor["identity_status"] == "source_local_alias"
    assert actor["entity_type"] == "organization"


def test_passive_agent_is_action_subject_role():
    svc = service_with_docs()
    result = svc.resolve_event(
        event_text="Restrictions were imposed by the US."
    )
    actor = primary(result)
    assert actor is not None
    assert actor["surface_form"] == "US"
    assert actor["role_status"] == "action_subject"
    assert "passive_agent" in actor["resolution_basis"]


def test_publisher_metadata_never_creates_candidate():
    svc = service_with_docs()
    result = svc.resolve_event(
        event_text="The US imposed restrictions.",
        source_publisher="Reuters",
    )
    surfaces = {item["surface_form"] for item in result["mentions"]}
    assert "Reuters" not in surfaces
    assert result["publisher_is_candidate_source"] is False


def test_publisher_mention_is_not_promoted_when_it_is_not_action_subject():
    svc = service_with_docs()
    result = svc.resolve_event(
        event_text="According to Reuters, Microsoft announced a partnership.",
        source_publisher="Reuters",
    )
    actor = primary(result)
    assert actor is not None
    assert actor["surface_form"] == "Microsoft"
    reuters = find_mention(result, "Reuters")
    assert reuters["publisher_match"] is True
    assert reuters["publisher_exclusion_applied"] is True
    assert reuters["role_status"] == "non_actor"


def test_v1_never_uses_llm():
    svc = service_with_docs()
    result = svc.resolve_event(event_text="The US imposed restrictions.")
    assert result["llm_used"] is False
    assert result["model"]["llm_used"] is False
