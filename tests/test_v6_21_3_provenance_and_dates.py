"""v6.21.3: the two defects GPT reproduced in v6.21.1 / v6.21.2.

1. Provenance: an implication's rule fact checked against all quoted facts
   was proven by EVID-RULE while the reader saw only EVID-DELAY as its source.
   The backing fact is now added to the implication's citations; if the
   checker names no backing record outside the implication's own citations,
   the implication is not shown.
2. A 2017 article saying "In 2012, USDA changed ..." could be accepted as a
   2017 case when the model returned year=2017. The page year now dates a case
   only when the passage around the quote names no other year.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from services import editorial_briefing as brief

FACTS = [
    {"fact_id": "F1", "development": "d", "text": "OpenAI took three months to notify Australia of the breach.",
     "date": "2026-09-24", "evidence_id": "EVID-DELAY", "source_id": "SOURCE-1"},
    {"fact_id": "F2", "development": "d", "text": "Article 55 of the EU AI Act requires serious-incident reporting.",
     "date": "2026-09-20", "evidence_id": "EVID-RULE", "source_id": "SOURCE-2"},
]
DRAFT = {"headline": "", "paragraphs": [], "analogies": [], "implications": [
    {"basis": "incentive", "text": "Faster incident reporting appears strategically attractive for OpenAI.",
     "fact_ids": ["F1"], "actor": "OpenAI", "policy_or_regulation": "Article 55 requires incident reporting",
     "company_relevance": "it took three months to notify a government", "economic_or_strategic_effect": "fines",
     "attractive_move": "report within days", "conditions": "the Commission does not enforce Article 55"}]}


def _run(backing_for_rule):
    draft = brief.parse_draft(DRAFT, FACTS, [])
    claims = brief.grounding_claims(draft, FACTS, [])
    statuses = {c["claim_id"]: ("supported" if c["_bar"] == ["supported"] else "inference_supported") for c in claims}
    supporting = {c["claim_id"]: ["EVID-DELAY"] for c in claims}
    rule = next(c for c in claims if c["claim_text"].startswith(brief.POLICY_EXISTS_PREFIX))
    supporting[rule["claim_id"]] = backing_for_rule
    return brief.sanitize(draft, claims, statuses, FACTS, [], supporting=supporting)["implications"]


def test_rescued_fact_is_cited_to_the_reader():
    kept = _run(["EVID-RULE"])
    assert kept and kept[0]["fact_ids"] == ["F1", "F2"]
    assert {c["evidence_id"] for c in kept[0]["citations"]} == {"EVID-DELAY", "EVID-RULE"}


def test_fact_with_unknown_provenance_is_not_shown():
    assert _run([]) == []


def test_fact_backed_by_the_implications_own_citation_needs_no_extra_citation():
    kept = _run(["EVID-DELAY"])
    assert kept and kept[0]["fact_ids"] == ["F1"]


# 2 ---------------------------------------------------------------------

def _review(body, published, year, quote):
    from services.hypothesis_evidence_service import HypothesisEvidenceService

    class Model:
        model_name = "m"
        timeout_seconds = 30

        def ask(self, prompt, **kwargs):
            return json.dumps({"review_complete": True, "records": [{
                "quote": quote, "relation": "supports", "claim_kind": "reported_claim", "connection": "c",
                "historical_case": {"year": year, "date_quote": quote, "outcome_quote": "",
                                    "similarity": "wording change", "difference": "agency"}}]})

    service = HypothesisEvidenceService(web_search_service=None, source_fetch_service=None, llm_service=Model())
    out = service._process_source(source={"url": "https://x.example/a", "text_content": body, "published_at": published},
                                  context={}, candidate={"insight": "i"}, now=datetime(2026, 10, 9, tzinfo=timezone.utc),
                                  timeout=10, enforce_window=False)
    return [r["historical_case"] for r in out["accepted_evidence"] if r.get("historical_case")]


def test_retrospective_article_is_not_dated_by_its_page():
    body = "In 2012 USDA changed its internal wording. The guidance was later withdrawn after criticism."
    assert _review(body, "2017-08-07T00:00:00Z", 2017, "USDA changed its internal wording.") == []


def test_contemporaneous_report_is_still_dated_by_its_page():
    body = "Staff at USDA have been told to avoid the term climate change. The guidance was later withdrawn."
    cases = _review(body, "2017-08-07T00:00:00Z", 2017, "Staff at USDA have been told to avoid the term climate change.")
    assert cases and cases[0]["date_basis"] == "page_publication_date"


def test_an_explicit_year_in_the_quote_still_counts():
    body = "In 2012 USDA changed its internal wording. The guidance was later withdrawn after criticism."
    cases = _review(body, "2017-08-07T00:00:00Z", 2012, "In 2012 USDA changed its internal wording.")
    assert cases and cases[0]["year"] == 2012 and cases[0]["date_basis"] == "quoted_passage"
