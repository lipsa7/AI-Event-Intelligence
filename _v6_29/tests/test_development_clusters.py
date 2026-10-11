"""
Development clustering: admitted events are grouped by what they say, not by
fixed keyword buckets. Embeddings propose groups (average linkage, or HDBSCAN
as an option); a shared-development check, with a verbatim quote from each
member, decides which groups stand. Everything else stays ungrouped.

A fake Ollama transport maps words to concept dimensions and a stub model
answers the shared-development check from those concepts. These test the
plumbing, not the quality of a real embedding model or the real model's
judgment. Calibrate EVENT_INTELLIGENCE_CLUSTER_MIN_SIMILARITY from
`development_clustering.nearest_neighbour_similarity` in a real run.

Run:  python -m tests.test_development_clusters
"""

from __future__ import annotations

import hashlib
import json
import random
import re

from agents.extraction_agent_v6 import ExtractionAgentV6
from services.research_orchestrator_service import ResearchOrchestratorService as R
from services.vector_service import VectorService

CONCEPTS = {
    1: {"underwriting", "loan", "loans", "lending", "credit"},
    2: {"drug", "discovery", "molecule", "pharma", "clinical"},
    3: {"chip", "chips", "gpu", "gpus", "semiconductor", "accelerator"},
    4: {"payroll", "hr"},
}


def fake_vector(text: str) -> list[float]:
    vector = [0.0] * 24
    vector[0] = 1.0  # everything here is AI news: shared background similarity
    for raw in text.casefold().replace(":", " ").split():
        word = raw.strip(".,;()'\"$")
        hit = False
        for dim, words in CONCEPTS.items():
            if word in words:
                vector[dim] += 2.0
                hit = True
        if not hit and word:
            vector[8 + int(hashlib.md5(word.encode()).hexdigest(), 16) % 16] += 0.05
    return vector


def transport(path, payload):
    texts = payload.get("input") or [payload.get("prompt")]
    vectors = [fake_vector(text.split(": ", 1)[-1]) for text in texts]
    return {"embeddings": vectors} if path == "/api/embed" else {"embedding": vectors[0]}


class SharedDevelopmentJudge:
    """Stub model for the SHARED-DEVELOPMENT CHECK: events share a development
    when they share a concept word; the quote is that word plus the word
    before it, copied from the event text."""

    model_name = "stub-judge"

    def __init__(self, mode="judge"):
        self.mode = mode
        self.calls = 0

    def ask(self, prompt, system_prompt=None, **kwargs):
        assert prompt.startswith("SHARED-DEVELOPMENT CHECK"), prompt[:40]
        self.calls += 1
        if self.mode == "fail":
            raise TimeoutError("stub judge timeout")
        groups = []
        for block in re.split(r"^Group (\d+)$", prompt, flags=re.M)[1:]:
            groups.append(block)
        rows = []
        for number, body in zip(groups[0::2], groups[1::2]):
            members = re.findall(r'^\[(E\d+\.\d+)\] actor ".*?"(?: \(.*?\))?: (.*)$', body, flags=re.M)
            best, best_word = None, ""
            for concept in CONCEPTS.values():
                # A correct verifier names the key word the members share and
                # quotes it from each member (v6.16.1 subject evidence).
                for word in sorted(concept):
                    hits = []
                    for label, text in members:
                        match = re.search(r"(\S+ %s)\b" % re.escape(word), text, flags=re.I)
                        if match:
                            hits.append({"event": label, "quote": match.group(1), "subject_quote": match.group(1)})
                    if len(hits) >= 2 and (best is None or len(hits) > len(best)):
                        best, best_word = hits, word
            if self.mode == "invent_quotes" and best:
                best = [dict(item, quote="an invented phrase") for item in best]
            if best:
                rows.append({"group": int(number), "verdict": "shared",
                             "development": "same change: " + best[0]["quote"],
                             "subject": "AI " + best_word, "members": best})
            else:
                rows.append({"group": int(number), "verdict": "not_shared", "development": "", "members": []})
        return json.dumps({"groups": rows})


def orchestrator(vector_service="fake", judge="judge"):
    instance = object.__new__(R)
    instance.vector_service = VectorService(transport=transport) if vector_service == "fake" else vector_service
    instance.judge = SharedDevelopmentJudge(judge) if judge else None
    instance.extraction_agent = (
        ExtractionAgentV6(llm_service=instance.judge, entity_tagger=None) if judge else None
    )
    return instance


def event(event_id, actor, summary, *, underlying=None, source="SOURCE-1"):
    """A v6-shaped event whose AI link was verified in context."""
    record = {
        "event_id": event_id, "source_id": source, "title": summary, "summary": summary,
        "description": "", "event_type": "deployment", "temporal_status": "current",
        "entity_mentions": [{"mention": actor, "entity_type": "organization"}],
        "actor": {"name": actor, "entity_type": "organization"},
        "snippets": [summary], "quantitative_claims": [],
        "cited_sentences": [{"id": "S1", "paragraph_id": "P1", "text": summary}],
        "roles": {
            "actor": {"mention": actor, "sentence_id": "S1"}, "action_sentence_ids": ["S1"],
            "object": None, "ai_link_sentence_ids": ["S1"], "time": None,
            "ai_link_verification": [{"sentence_id": "S1", "verdict": "supported",
                                      "linking_text": " ".join(summary.split()[:3])}],
        },
        "extraction_metadata": {"event_taxonomy": "observed_event"},
    }
    if underlying:
        record["underlying_event_id"] = underlying
    return record


# Same development (AI underwriting at banks) worded as a deployment, a
# funding round and a partnership -> different keyword buckets.
UNDERWRITING = [
    event("EVENT-A1", "HDFC Bank", "HDFC Bank deployed generative AI agents for loan underwriting.", source="SOURCE-1"),
    event("EVENT-A2", "Axis Bank", "Axis Bank raised funding to expand its AI underwriting of credit.", source="SOURCE-2"),
    event("EVENT-A3", "Kotak Bank", "Kotak Bank signed an agreement with OpenAI for AI loan underwriting.", source="SOURCE-3"),
]
# Two funding rounds about different things -> same bucket, different developments.
DRUGS = [
    event("EVENT-B1", "Insilico Medicine", "Insilico Medicine raised funding for generative AI drug discovery.", source="SOURCE-4"),
    event("EVENT-B2", "Recursion", "Recursion launched an AI model for molecule discovery in clinical pharma.", source="SOURCE-5"),
]
CHIPS = [event("EVENT-C1", "NVIDIA", "NVIDIA released new GPUs for AI training clusters.", source="SOURCE-6")]


def evidence_for(events):
    return [{"evidence_id": f"EVID-{e['event_id'][6:]}", "event_id": e["event_id"], "text": e["summary"]} for e in events]


def admit_and_cluster(instance, events, topic="artificial intelligence"):
    return instance._prepare_strategic_event_context(topic=topic, events=events, evidence=evidence_for(events))


def clusters_of(selected):
    groups: dict[str, set[str]] = {}
    for item in selected:
        groups.setdefault(item["strategic_cluster_id"], set()).add(item["event_id"])
    return sorted(groups.values(), key=lambda g: sorted(g))


# ----------------------------------------------------------------------

def test_events_group_by_development_not_by_keyword_bucket():
    selected, evidence, diagnostics = admit_and_cluster(orchestrator(), UNDERWRITING + DRUGS + CHIPS)
    assert len(selected) == 6, diagnostics["exclusion_reasons"]
    assert clusters_of(selected) == [{"EVENT-A1", "EVENT-A2", "EVENT-A3"}, {"EVENT-B1", "EVENT-B2"}, {"EVENT-C1"}]
    # The old buckets put unrelated funding rounds together and split the
    # underwriting development; they are kept only as `strategic_theme`.
    themes = {item["event_id"]: item["strategic_theme"] for item in selected}
    assert len({themes["EVENT-A1"], themes["EVENT-A2"], themes["EVENT-A3"]}) > 1
    assert diagnostics["development_clustering"]["method"] == "embedding_average_linkage:nomic-embed-text"
    assert diagnostics["development_clustering"]["verified_cluster_count"] == 2
    assert diagnostics["development_clustering"]["shared_development_check"] == "answered"
    assert diagnostics["theme_distribution"] and diagnostics["cluster_distribution"]
    # Evidence rows carry the development cluster too.
    by_event = {item["event_id"]: item for item in selected}
    for row in evidence:
        assert row["strategic_cluster_id"] == by_event[row["event_id"]]["strategic_cluster_id"]


def test_cluster_labels_come_from_distinctive_terms():
    selected, _, _ = admit_and_cluster(orchestrator(), UNDERWRITING + DRUGS + CHIPS)
    underwriting = next(item for item in selected if item["event_id"] == "EVENT-A1")
    assert "underwriting" in underwriting["strategic_cluster_terms"]
    assert underwriting["strategic_cluster_id"].startswith("dev_")
    assert "HDFC Bank" in underwriting["strategic_cluster_label"]


def test_duplicate_reports_of_one_event_stay_together():
    events = [
        event("EVENT-D1", "HDFC Bank", "HDFC Bank deployed generative AI agents for loan underwriting.", underlying="UEV-1"),
        event("EVENT-D2", "HDFC Bank", "HDFC Bank deployed generative AI agents, its chief said at a chip summit on GPUs.",
              underlying="UEV-1", source="SOURCE-9"),
    ] + CHIPS
    selected, _, _ = admit_and_cluster(orchestrator(), events)
    ids = {item["event_id"]: item["strategic_cluster_id"] for item in selected}
    assert ids["EVENT-D1"] == ids["EVENT-D2"]


def test_cluster_ids_carry_over_when_new_events_join():
    instance = orchestrator()
    first, _, _ = admit_and_cluster(instance, UNDERWRITING[:2] + DRUGS)
    underwriting_id = next(item["strategic_cluster_id"] for item in first if item["event_id"] == "EVENT-A1")
    # Expansion found more events: the underwriting cluster keeps its ID.
    second, _, _ = admit_and_cluster(instance, UNDERWRITING + DRUGS + CHIPS)
    joined = {item["event_id"]: item["strategic_cluster_id"] for item in second}
    assert joined["EVENT-A1"] == joined["EVENT-A3"] == underwriting_id


def test_actor_background_does_not_steer_the_cluster():
    # The actor's background sentence is about chips; the action is lending.
    v6 = event("EVENT-V6", "Zeta Labs", "The company launched an AI underwriting engine for loans.")
    v6["cited_sentences"].insert(0, {"id": "S0", "paragraph_id": "P1",
                                     "text": "Zeta Labs designs AI chips and GPU accelerators."})
    v6["roles"]["actor"]["sentence_id"] = "S0"
    v6["description"] = "Zeta Labs designs AI chips and GPU accelerators."
    assert "chips" not in R._development_text(v6)
    selected, _, diagnostics = admit_and_cluster(orchestrator(), UNDERWRITING + CHIPS + [v6])
    ids = {item["event_id"]: item["strategic_cluster_id"] for item in selected}
    assert set(ids) == {"EVENT-A1", "EVENT-A2", "EVENT-A3", "EVENT-C1", "EVENT-V6"}, diagnostics["exclusion_reasons"]
    assert ids["EVENT-V6"] == ids["EVENT-A1"] != ids["EVENT-C1"]


def test_without_embeddings_keyword_buckets_are_kept_and_reported():
    selected, _, diagnostics = admit_and_cluster(orchestrator(vector_service=None), UNDERWRITING + DRUGS)
    clustering = diagnostics["development_clustering"]
    assert clustering["method"] == "keyword_buckets"
    assert clustering["fallback_reason"] == "vector_service_not_configured"
    assert all(item["strategic_cluster_id"] == item["strategic_theme"] for item in selected)


def test_average_linkage_is_order_independent():
    vectors = [fake_vector(item["summary"]) for item in UNDERWRITING + DRUGS + CHIPS]
    expected = R._average_linkage_clusters(vectors, 0.7)
    order = list(range(len(vectors)))
    for seed in range(5):
        random.Random(seed).shuffle(order)
        shuffled = R._average_linkage_clusters([vectors[i] for i in order], 0.7)
        mapped = sorted(sorted(order[i] for i in members) for members in shuffled)
        assert mapped == sorted(sorted(members) for members in expected)


def test_threshold_controls_grouping():
    vectors = [fake_vector(item["summary"]) for item in UNDERWRITING + DRUGS]
    assert len(R._average_linkage_clusters(vectors, 1.01)) == 5   # nothing similar enough
    assert len(R._average_linkage_clusters(vectors, -1.0)) == 1   # everything merges
    assert len(R._average_linkage_clusters(vectors, 0.7)) == 2


def test_expansion_queries_target_the_development():
    selected, evidence, _ = admit_and_cluster(orchestrator(), UNDERWRITING + DRUGS + CHIPS)
    profiles = R._strategic_cluster_expansion_profiles(
        topic="artificial intelligence", events=selected, evidence=evidence)
    underwriting = next(p for p in profiles if "underwriting" in p["cluster_terms"])
    assert len(underwriting["underlying_event_ids"]) == 3
    queries = R._cluster_expansion_queries(topic="artificial intelligence", profile=underwriting, round_number=1)
    anchored = [R._anchor_discovery_query("artificial intelligence", q) for q in queries]
    assert all(q.startswith("(artificial intelligence) AND (") for q in anchored)
    assert any("underwriting" in q for q in anchored)


# ----------------------------------------------------------------------
# Shared-development check: similarity proposes, the check decides
# ----------------------------------------------------------------------

def test_group_without_a_shared_development_is_ungrouped():
    # Similar enough to be proposed (threshold very low), but no shared change.
    import os
    os.environ["EVENT_INTELLIGENCE_CLUSTER_MIN_SIMILARITY"] = "0.05"
    try:
        selected, evidence, diagnostics = admit_and_cluster(orchestrator(), [UNDERWRITING[0], DRUGS[0], CHIPS[0]])
    finally:
        os.environ.pop("EVENT_INTELLIGENCE_CLUSTER_MIN_SIMILARITY", None)
    clustering = diagnostics["development_clustering"]
    assert clustering["proposed_multi_event_group_count"] == 1
    assert clustering["verified_cluster_count"] == 0
    assert len(clusters_of(selected)) == 3
    assert {item["strategic_cluster_check"] for item in selected} == {"not_shared"}
    profiles = R._strategic_cluster_expansion_profiles(
        topic="artificial intelligence", events=selected, evidence=evidence)
    assert not any(p["seed_basis"] == "verified_shared_development" for p in profiles)


def test_partly_shared_group_keeps_only_confirmed_members():
    import os
    os.environ["EVENT_INTELLIGENCE_CLUSTER_MIN_SIMILARITY"] = "0.1"
    try:
        selected, _, _ = admit_and_cluster(orchestrator(), UNDERWRITING[:2] + CHIPS)
    finally:
        os.environ.pop("EVENT_INTELLIGENCE_CLUSTER_MIN_SIMILARITY", None)
    status = {item["event_id"]: (item["strategic_cluster_status"], item["strategic_cluster_check"]) for item in selected}
    assert status["EVENT-A1"] == status["EVENT-A2"] == ("verified", "shared")
    assert status["EVENT-C1"] == ("single", "not_confirmed_member")


def test_unanswered_check_leaves_groups_ungrouped_and_says_so():
    instance = orchestrator(judge="fail")
    selected, evidence, diagnostics = admit_and_cluster(instance, UNDERWRITING + DRUGS)
    assert diagnostics["development_clustering"]["shared_development_check"] == "partly_unanswered"
    assert len(clusters_of(selected)) == 5
    assert {item["strategic_cluster_check"] for item in selected} == {"unanswered"}
    quality = R._strategic_corpus_sufficiency_assessment(
        topic="artificial intelligence", events=selected, evidence=evidence)
    assert quality["verified_cluster_count"] == 0 and quality["coherent_multi_event_cluster_count"] == 0


def test_shared_verdict_with_invented_quotes_is_not_accepted():
    selected, _, diagnostics = admit_and_cluster(orchestrator(judge="invent_quotes"), UNDERWRITING + DRUGS)
    assert diagnostics["development_clustering"]["verified_cluster_count"] == 0
    assert len(clusters_of(selected)) == 5


def test_no_shared_development_checker_means_no_groups():
    selected, _, diagnostics = admit_and_cluster(orchestrator(judge=None), UNDERWRITING + DRUGS)
    assert diagnostics["development_clustering"]["shared_development_check"].startswith("unavailable")
    assert len(clusters_of(selected)) == 5


def test_checked_groups_are_cached_between_admission_passes():
    instance = orchestrator()
    admit_and_cluster(instance, UNDERWRITING + DRUGS)
    calls = instance.judge.calls
    admit_and_cluster(instance, UNDERWRITING + DRUGS)
    assert instance.judge.calls == calls


def test_only_verified_groups_seed_follow_up_searches_and_count_as_coherent():
    selected, evidence, _ = admit_and_cluster(orchestrator(), UNDERWRITING + DRUGS + CHIPS)
    profiles = R._strategic_cluster_expansion_profiles(
        topic="artificial intelligence", events=selected, evidence=evidence)
    seeds = {p["cluster_id"] for p in profiles if p["seed_basis"] == "verified_shared_development"}
    assert len(seeds) == 2
    # Keyword-bucket fallback: buckets group events, but never seed or count.
    fallback, fallback_evidence, _ = admit_and_cluster(orchestrator(vector_service=None), UNDERWRITING + DRUGS + CHIPS)
    fallback_profiles = R._strategic_cluster_expansion_profiles(
        topic="artificial intelligence", events=fallback, evidence=fallback_evidence)
    assert not any(p["seed_basis"] == "verified_shared_development" for p in fallback_profiles)
    assert R._strategic_corpus_sufficiency_assessment(
        topic="artificial intelligence", events=fallback, evidence=fallback_evidence)["verified_cluster_count"] == 0


def test_hdbscan_option_keeps_noise_ungrouped():
    import os
    try:
        import sklearn.cluster  # noqa: F401
    except ImportError:
        print("SKIP hdbscan test: scikit-learn not installed")
        return
    noise = [
        event("EVENT-N1", "Hotel Group", "Hotel Group opened an AI concierge desk for guests.", source="SOURCE-7"),
        event("EVENT-N2", "Port Authority", "Port Authority approved an AI crane scheduling pilot.", source="SOURCE-8"),
    ]
    os.environ["EVENT_INTELLIGENCE_CLUSTER_METHOD"] = "hdbscan"
    try:
        selected, _, diagnostics = admit_and_cluster(orchestrator(), UNDERWRITING + DRUGS + noise)
    finally:
        os.environ.pop("EVENT_INTELLIGENCE_CLUSTER_METHOD", None)
    assert diagnostics["development_clustering"]["method"].startswith("embedding_hdbscan"), diagnostics["development_clustering"]
    ids = {item["event_id"]: item["strategic_cluster_id"] for item in selected}
    assert ids["EVENT-A1"] == ids["EVENT-A2"] == ids["EVENT-A3"]
    # Noise events are not merged into a miscellaneous group.
    assert ids["EVENT-N1"] != ids["EVENT-N2"]
    assert {item["strategic_cluster_status"] for item in selected if item["event_id"].startswith("EVENT-N")} == {"single"}


# ----------------------------------------------------------------------
# Review findings (v6.7 pre-release review)
# ----------------------------------------------------------------------

def test_hdbscan_can_return_one_development_and_still_separates_two():
    import os
    try:
        import sklearn.cluster  # noqa: F401
    except ImportError:
        print("SKIP hdbscan test: scikit-learn not installed")
        return
    more = [event(f"EVENT-A{n}", f"Bank {n}", f"Bank {n} deployed generative AI for loan underwriting.",
                  source=f"SOURCE-{n}") for n in range(4, 6)]
    os.environ["EVENT_INTELLIGENCE_CLUSTER_METHOD"] = "hdbscan"
    try:
        one, one_ev, _ = admit_and_cluster(orchestrator(), UNDERWRITING + more)
        two, _, _ = admit_and_cluster(orchestrator(), UNDERWRITING + DRUGS)
    finally:
        os.environ.pop("EVENT_INTELLIGENCE_CLUSTER_METHOD", None)
    # Reviewer: with default settings HDBSCAN returned only noise for a
    # single-development corpus. Now every event is grouped and verified
    # (HDBSCAN may split one development into denser sub-groups).
    assert all(len(group) >= 2 for group in clusters_of(one))
    assert {item["strategic_cluster_status"] for item in one} == {"verified"}
    assert R._strategic_corpus_sufficiency_assessment(
        topic="artificial intelligence", events=one, evidence=one_ev)["verified_cluster_count"] >= 1
    assert clusters_of(two) == [{"EVENT-A1", "EVENT-A2", "EVENT-A3"}, {"EVENT-B1", "EVENT-B2"}]


def test_quotes_must_be_whole_specific_words_from_the_event_itself():
    from agents.extraction_agent_v6 import ExtractionAgentV6 as Agent
    text = "Hotel Group opened an AI concierge desk for guests."
    assert Agent._valid_quote("AI concierge", text)
    assert not Agent._valid_quote("p opened", text)          # word fragment
    assert not Agent._valid_quote("an AI", text)             # no content word
    assert not Agent._valid_quote("deployment: hotel", text)  # pipeline label, not source text
    assert R._valid_evidence_quote("AI concierge", text) and not R._valid_evidence_quote("p opened", text)

    class Fragments:
        model_name = "stub"

        def ask(self, prompt, system_prompt=None, **kwargs):
            return json.dumps({"groups": [{"group": 0, "verdict": "shared", "development": "x",
                                           "members": [{"event": "E0.1", "quote": "deployment: hotel"},
                                                       {"event": "E0.2", "quote": "t approved"}]}]})

    agent = Agent(llm_service=Fragments(), entity_tagger=None)
    groups = [[{"id": "EVENT-1", "actor": "Hotel Group", "event_type": "deployment", "text": text},
               {"id": "EVENT-2", "actor": "Port Authority", "event_type": "deployment",
                "text": "Port Authority approved an AI crane scheduling pilot."}]]
    assert agent.verify_shared_development(groups) == [None]


def test_keyword_fallback_clears_fields_from_an_earlier_pass():
    instance = orchestrator()
    first, first_evidence, _ = admit_and_cluster(instance, UNDERWRITING + DRUGS)
    instance.vector_service = None  # embeddings fail in a later round
    again, evidence, _ = instance._prepare_strategic_event_context(
        topic="artificial intelligence", events=first, evidence=first_evidence)
    for item in again:
        assert item["strategic_cluster_status"] == "keyword_bucket"
        assert item["strategic_cluster_check"] == "" and item["strategic_cluster_development"] == ""
        assert item["strategic_cluster_terms"] == [] and item["strategic_cluster_label"] == ""
    assert not any("strategic_cluster_label" in row for row in evidence)


def test_malformed_checker_answers_are_not_trusted():
    for bad in ({"verified": True, "members": []},
                {"verified": True, "members": ["EVENT-A1"], "development": "x"},
                {"verified": "false", "members": ["EVENT-A1", "EVENT-A2"], "development": "x"},
                {"verified": True, "members": ["EVENT-A1", "EVENT-ZZ"], "development": "x"}):
        instance = orchestrator()
        instance.extraction_agent = type("Checker", (), {
            "verify_shared_development": staticmethod(lambda groups, bad=bad: [dict(bad) for _ in groups])})()
        selected, _, diagnostics = admit_and_cluster(instance, UNDERWRITING[:2])
        assert diagnostics["development_clustering"]["verified_cluster_count"] == 0, bad
        assert {item["strategic_cluster_status"] for item in selected} == {"single"}, bad


def test_events_identified_only_by_id_are_clustered():
    events = []
    for item in UNDERWRITING + DRUGS:
        copy = dict(item)
        copy["id"] = copy.pop("event_id")
        events.append(copy)
    instance = orchestrator()
    selected, _, diagnostics = instance._prepare_strategic_event_context(
        topic="artificial intelligence", events=events,
        evidence=[{"evidence_id": f"EVID-{n}", "event_id": item["id"], "text": item["summary"]}
                  for n, item in enumerate(events)])
    assert selected
    assert diagnostics["development_clustering"]["verified_cluster_count"] == 2
    assert "" not in instance._development_cluster_memory["artificial intelligence"]


def test_failed_check_on_a_grown_group_keeps_the_verified_subgroup():
    instance = orchestrator()
    first, _, _ = admit_and_cluster(instance, UNDERWRITING[:2] + DRUGS)
    first_id = next(item["strategic_cluster_id"] for item in first if item["event_id"] == "EVENT-A1")
    instance.judge.mode = "fail"     # the next check times out
    second, _, diagnostics = admit_and_cluster(instance, UNDERWRITING + DRUGS)
    status = {item["event_id"]: (item["strategic_cluster_id"], item["strategic_cluster_check"]) for item in second}
    assert status["EVENT-A1"] == status["EVENT-A2"] == (first_id, "shared_previously_verified")
    assert status["EVENT-A3"][1] == "unanswered" and status["EVENT-A3"][0] != first_id


# ----------------------------------------------------------------------
# Automatic method selection (default EVENT_INTELLIGENCE_CLUSTER_METHOD=auto)
# ----------------------------------------------------------------------

def test_auto_runs_both_methods_and_records_the_choice():
    try:
        import sklearn.cluster  # noqa: F401
    except ImportError:
        print("SKIP auto test: scikit-learn not installed")
        return
    _, _, diagnostics = admit_and_cluster(orchestrator(), UNDERWRITING + DRUGS + CHIPS)
    clustering = diagnostics["development_clustering"]
    assert clustering["method_requested"] == "auto"
    selection = clustering["method_selection"]
    assert set(selection["candidates"]) == {"average_linkage", "hdbscan"}
    # Same confirmed result from both: the tie goes to average linkage.
    assert selection["chosen"] == "average_linkage"


def test_auto_picks_the_method_whose_groups_the_check_confirms():
    import os
    try:
        import sklearn.cluster  # noqa: F401
    except ImportError:
        print("SKIP auto test: scikit-learn not installed")
        return
    # A threshold so strict that average linkage proposes no groups at all.
    os.environ["EVENT_INTELLIGENCE_CLUSTER_MIN_SIMILARITY"] = "0.9999"
    try:
        selected, _, diagnostics = admit_and_cluster(orchestrator(), UNDERWRITING + DRUGS)
    finally:
        os.environ.pop("EVENT_INTELLIGENCE_CLUSTER_MIN_SIMILARITY", None)
    selection = diagnostics["development_clustering"]["method_selection"]
    assert selection["candidates"]["average_linkage"]["events_in_verified_clusters"] == 0
    assert selection["chosen"] == "hdbscan"
    assert clusters_of(selected) == [{"EVENT-A1", "EVENT-A2", "EVENT-A3"}, {"EVENT-B1", "EVENT-B2"}]


def test_auto_without_scikit_learn_uses_average_linkage_and_says_why():
    original = R._hdbscan_unit_clusters

    def missing(*args, **kwargs):
        raise ImportError("No module named 'sklearn'")

    R._hdbscan_unit_clusters = staticmethod(missing)
    try:
        selected, _, diagnostics = admit_and_cluster(orchestrator(), UNDERWRITING + DRUGS)
    finally:
        R._hdbscan_unit_clusters = original
    clustering = diagnostics["development_clustering"]
    assert clustering["method_selection"]["chosen"] == "average_linkage"
    assert "hdbscan not used" in clustering["method_note"] and "sklearn" in clustering["method_note"]
    assert len(clusters_of(selected)) == 2


if __name__ == "__main__":
    failures = 0
    for name, function in list(globals().items()):
        if name.startswith("test_") and callable(function):
            try:
                function()
                print(f"PASS {name}")
            except Exception as error:
                failures += 1
                print(f"FAIL {name}: {type(error).__name__}: {error}")
    raise SystemExit(1 if failures else 0)
