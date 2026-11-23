"""v13.31.1: the fact table states only supportable figures; novelty compares like with like."""
import inspect

from services import fact_table, semantic_novelty

RECORDS = [
    {"evidence_id": "E0", "published_at": "2026-10-01", "text": "California sent a subpoena on September 30, 2026."},
    {"evidence_id": "E1", "published_at": "2026-11-18",
     "text": "Bonta sent an investigative subpoena on September 30, 2026. On November 17, 2026 California sued OpenAI "
             "over around 80 other incidents involving loss of control. OpenAI was founded in December 2015."},
]


def test_no_rate_is_invented_from_unrelated_dates():
    computed = fact_table.compute(fact_table.extract(RECORDS))
    assert not [c for c in computed if c["kind"] == "rate"], computed


def test_a_rate_needs_the_count_and_both_dates_in_one_sentence():
    rec = [{"evidence_id": "E", "text": "Between January 1, 2026 and July 1, 2026 regulators logged 60 incidents."}]
    rates = [c for c in fact_table.compute(fact_table.extract(rec)) if c["kind"] == "rate"]
    assert rates and abs(rates[0]["per_month"] - 10.0) < 0.2


def test_reported_later_uses_the_first_record_that_mentions_the_date():
    computed = fact_table.compute(fact_table.extract(RECORDS))
    assert not [c for c in computed if c["kind"] == "reported_later"]   # first mentioned the next day


def test_the_story_gap_comes_before_a_founding_date():
    computed = fact_table.compute(fact_table.extract(RECORDS))
    assert computed[0]["text"].startswith("September 30, 2026 to November 17, 2026")


def test_novelty_embeds_both_sides_the_same_way():
    class Fake:
        def __init__(self):
            self.purposes = []

        def embed(self, texts, purpose="document"):
            self.purposes.append(purpose)
            return [[1.0, 0.0] for _ in texts]

        def similarities(self, *a):
            raise AssertionError("retrieval similarity must not be used for novelty")
    fake = Fake()
    result = semantic_novelty.check("a", [{"kind": "consensus", "text": "b"}], service=fake)
    assert result["checked"] and result["max"] == 1.0 and fake.purposes == ["clustering"]


def test_earlier_insights_load_before_the_consensus_view():
    import services.research_orchestrator_service as ros
    source = inspect.getsource(ros.ResearchOrchestratorService)
    load = source.index("self._semantic_earlier = list(earlier_insights.load(topic))[:400]")
    assert load < source.index('view = self._consensus_view(combo["labels"], sub_evidence, evidence)')
    assert 'self.__dict__.pop("_semantic_earlier", None)' in source
