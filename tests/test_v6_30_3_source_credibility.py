"""v6.30.3: sources must be credible ("a random fb comment doesn't count") without blocking
genuine credible sources (user, 2026-10-10). Method from the research: Klue scores each
signal by source credibility before generating; the IC Quality of Information Check grades
sources. Only social media, user pages and video/podcast pages are dropped; unknown sites
are kept and labelled "unrated"; only credible sites count as an expert or organisation view.
The URLs below are real pages found by gap research in the user's runs.
"""

from __future__ import annotations

import pytest

from services import editorial_briefing as brief
from services import source_credibility as cred
from services.grounded_search import is_article_source

GENUINE = {
    "http://casar.house.gov/media/press-releases/news-casar-sanders": "official",
    "https://www.oaic.gov.au/privacy/notifiable-data-breaches": "official",
    "https://digital-strategy.ec.europa.eu/en/faqs/guidelines-obligations": "official",
    "https://www.csis.org/analysis/understanding-us-federal-governments-ai-spending": "research",
    "https://artificialintelligenceact.eu/article/51/": "research",
    "https://fedscoop.com/gsa-issued-an-ai-acquisition-policy": "news",
    "https://www.latimes.com/business/la-fi-mo-fda-corn-sugar-20120531-story.html": "news",
    "https://www.dlapiperdataprotection.com/?c=AU&t=breach-notification": "professional",
    "https://www.reuters.com/technology/x": "news",
    "https://hai.stanford.edu/news/x": "research",
    "https://www.cs.ox.ac.uk/news/x": "research",
}


@pytest.mark.parametrize("url,level", GENUINE.items())
def test_genuine_credible_sources_are_rated_and_never_blocked(url, level):
    assert cred.tier(url) == level and cred.is_credible(url) and not cred.is_blocked(url)


def test_social_and_user_pages_are_blocked_unknown_sites_are_only_unrated():
    for url in ("https://www.facebook.com/groups/x/posts/1", "https://m.facebook.com/story", "https://x.com/a/status/1",
                "https://www.reddit.com/r/ai/comments/1", "https://www.youtube.com/watch?v=5sJxQYCyxXI",
                "https://www.quora.com/x", "https://www.linkedin.com/posts/x"):
        assert cred.tier(url) == "blocked", url
    for url in ("https://en.wikipedia.org/wiki/Ban_Artificial_Superintelligence_Act",
                "https://www.jwmconceptsllc.com/insights/ai-government-contracting.html",
                "https://auth0.com/blog/the-worst-data-breaches-of-2021-q1-and-q2/"):
        assert cred.tier(url) == "unrated" and not cred.is_blocked(url), url
    assert cred.tier("https://www.prnewswire.com/news-releases/x") == "press_release"
    assert cred.tier("https://openai.com/index/x") == "primary"
    assert cred.is_media_page("https://techcrunch.com/video/amazon-and-others/")
    assert cred.is_media_page("https://techcrunch.com/podcast/amazon-drops/")
    assert not cred.is_media_page("https://techcrunch.com/2026/10/01/story/")


def test_redirect_is_rated_by_its_site_and_grounded_results_skip_social():
    redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQ"
    assert cred.page_tier(redirect, "reuters.com") == "news"
    assert cred.page_tier(redirect, "facebook.com") == "blocked"
    assert not is_article_source({"url": redirect, "title": "quora.com"})
    assert is_article_source({"url": redirect, "title": "csis.org"})


def test_user_can_add_trusted_and_blocked_sources(tmp_path, monkeypatch):
    (tmp_path / "credible_sources.txt").write_text("jwmconceptsllc.com professional\nmysubstack.substack.com\n",
                                                  encoding="utf-8")
    (tmp_path / "blocked_sources.txt").write_text("contentfarm.example  # spam\n", encoding="utf-8")
    monkeypatch.setenv("EVENT_INTELLIGENCE_DATA_DIR", str(tmp_path))
    assert cred.tier("https://www.jwmconceptsllc.com/insights/x") == "professional"
    assert cred.tier("https://mysubstack.substack.com/p/x") == "news"
    assert cred.tier("https://contentfarm.example/a") == "blocked"


def test_only_credible_commentary_counts_as_expert_backing():
    credible = {"evidence_id": "E1", "source_id": "S1", "gap_type": "commentary", "gap_for": "d",
                "source_url": "https://www.csis.org/analysis/x", "text": "Analysts argue vendors will shorten notice."}
    unrated = {"evidence_id": "E2", "source_id": "S2", "gap_type": "commentary_unrated", "gap_for": "d",
               "source_url": "https://someblog.example/x", "text": "A blogger argues vendors will shorten notice."}
    cites = brief.expert_citations([unrated, credible], "vendors will shorten notice")
    assert [c["evidence_id"] for c in cites] == ["E1"] and cites[0]["credibility"] == "research institution"
    shown = brief.commentary_items([unrated, credible])
    assert [i["evidence_id"] for i in shown] == ["E1", "E2"]                 # credible first; unrated labelled
    assert [i["credibility"] for i in shown] == ["research institution", "unrated source"]
    # commentary is never a fact; an unrated site's fact ranks after credible ones
    facts = brief.select_context_facts([
        {"evidence_id": "F-U", "gap_type": "policy", "source_url": "https://someblog.example/p",
         "text": "An unrated page says the rule requires reporting within 72 hours."},
        {"evidence_id": "F-G", "gap_type": "policy", "source_url": "https://www.oaic.gov.au/p",
         "text": "The regulator says the rule requires reporting within 72 hours of awareness."},
        {"evidence_id": "F-X", "gap_type": "policy", "source_url": "https://www.facebook.com/p",
         "text": "A social post claims the rule requires reporting within 24 hours of awareness."}])
    assert [f["evidence_id"] for f in facts] == ["F-G", "F-U"]


def test_gap_research_drops_blocked_pages_before_reading_them():
    import inspect
    from services.hypothesis_evidence_service import HypothesisEvidenceService
    source = inspect.getsource(HypothesisEvidenceService)
    assert source.index('if page_tier == "blocked" or credibility.is_media_page(url):') < source.index(
        "result = self._process_source(source=source, context=discovery_context")
    assert 'record["gap_type"] = "commentary_unrated"' in source
