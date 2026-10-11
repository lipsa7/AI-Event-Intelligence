"""v6.20: canonical duplicate-story resolver, cross-run event memory, structural non-obviousness."""

import os
from contextlib import closing
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.underlying_event_resolver import UnderlyingEventResolver  # noqa: E402
from services.event_memory_service import EventMemoryService  # noqa: E402
from services import non_obviousness  # noqa: E402


def resolver():
    return UnderlyingEventResolver(actor_key=lambda e: e.get("actor", ""),
                                   family=lambda e: e.get("family", ""), topic="artificial intelligence")


def ev(eid, title, actor="acme", family="acquisition", source="SRC-1", date="2026-10-01"):
    return {"event_id": eid, "title": title, "actor": actor, "family": family,
            "source_id": source, "event_date": date}


class ResolverTests(unittest.TestCase):
    def test_reports_of_one_story_become_one_event(self):
        a = ev("EVENT-1", "Acme acquires Widgetco robotics startup for warehouse automation", source="SRC-1")
        b = ev("EVENT-2", "Acme buys Widgetco robotics startup in warehouse automation push", source="SRC-2")
        mapping, meta = resolver().resolve([a, b])
        self.assertEqual(mapping["EVENT-1"], mapping["EVENT-2"])
        group = meta[mapping["EVENT-1"]]
        self.assertEqual(group["member_count"], 2)
        self.assertEqual(group["source_ids"], ["SRC-1", "SRC-2"])

    def test_complete_linkage_prevents_chaining(self):
        # A~B and B~C match, A and C share nothing: they must not end up together.
        a = ev("EVENT-A", "alpha bravo charlie delta", source="S1", date="2026-10-01")
        b = ev("EVENT-B", "alpha bravo charlie echo foxtrot golf", source="S2", date="2026-10-02")
        c = ev("EVENT-C", "echo foxtrot golf hotel", source="S3", date="2026-10-03")
        mapping, _ = resolver().resolve([a, b, c])
        self.assertNotEqual(mapping["EVENT-A"], mapping["EVENT-C"])
        self.assertFalse(mapping["EVENT-A"] == mapping["EVENT-B"] == mapping["EVENT-C"])

    def test_same_actor_and_family_alone_is_not_one_event(self):
        a = ev("EVENT-1", "Acme acquires Widgetco robotics startup", date="2026-10-01")
        b = ev("EVENT-2", "Acme purchases Lumen payments platform", source="SRC-2", date="2026-10-07")
        mapping, _ = resolver().resolve([a, b])
        self.assertNotEqual(mapping["EVENT-1"], mapping["EVENT-2"])

    def test_id_is_stable_when_another_report_arrives(self):
        a = ev("EVENT-1", "Acme acquires Widgetco robotics startup warehouse", source="SRC-1")
        b = ev("EVENT-2", "Acme acquires Widgetco robotics startup warehouse deal", source="SRC-2")
        c = ev("EVENT-9", "Acme acquires Widgetco robotics startup warehouse", source="SRC-3")
        first, _ = resolver().resolve([a, b])
        second, _ = resolver().resolve([a, b, c])
        self.assertEqual(first["EVENT-1"], second["EVENT-1"])
        self.assertEqual(second["EVENT-9"], second["EVENT-1"])


    def test_same_actor_family_date_different_targets_are_not_merged(self):
        a = ev("EVENT-1", "Acme acquires Widgetco robotics startup", date="2026-10-01", source="SRC-1")
        b = ev("EVENT-2", "Acme acquires Lumen payments platform", date="2026-10-01", source="SRC-2")
        mapping, _ = resolver().resolve([a, b])
        self.assertNotEqual(mapping["EVENT-1"], mapping["EVENT-2"])

    def test_id_survives_older_report_crossing_week_boundary(self):
        a = ev("EVENT-1", "Acme acquires Widgetco robotics startup warehouse", source="SRC-1", date="2026-10-05")
        b = ev("EVENT-2", "Acme buys Widgetco robotics startup in warehouse automation push", source="SRC-2", date="2026-10-06")
        older = ev("EVENT-3", "Widgetco deal gives Acme warehouse robotics capability", source="SRC-3", date="2026-09-29")
        first, _ = resolver().resolve([a, b])
        second, _ = resolver().resolve([a, b, older])
        self.assertEqual(first["EVENT-1"], second["EVENT-1"])
        self.assertEqual(second["EVENT-3"], second["EVENT-1"])

    def test_id_does_not_depend_on_event_row_ids(self):
        a = ev("EVENT-1", "Acme acquires Widgetco robotics startup warehouse")
        renamed = dict(a, event_id="EVENT-77")
        self.assertEqual(resolver().resolve([a])[0]["EVENT-1"], resolver().resolve([renamed])[0]["EVENT-77"])

    def test_id_collision_gets_a_suffix(self):
        taken = {}
        features = {"E1": {"actor": "", "family": "", "date": "", "tokens": set()}}
        base = UnderlyingEventResolver._stable_id(["E1"], features, taken)
        taken[base] = {}
        self.assertEqual(UnderlyingEventResolver._stable_id(["E1"], features, taken), base + "-2")

    def test_orchestrator_and_agent_share_the_resolver(self):
        source = (Path(__file__).resolve().parents[1] / "agents" / "strategic_inference_agent.py").read_text(encoding="utf-8")
        self.assertIn("UnderlyingEventResolver", source)
        orch = (Path(__file__).resolve().parents[1] / "services" / "research_orchestrator_service.py").read_text(encoding="utf-8")
        self.assertIn("UnderlyingEventResolver", orch)


class EventMemoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.memory = EventMemoryService(Path(self.folder.name) / "memory.sqlite3")

    def tearDown(self):
        self.folder.cleanup()

    def remember(self, run_id, underlying="UNDERLYINGEVENT-A", actor="Acme"):
        events = [{"event_id": "EVENT-1", "underlying_event_id": underlying, "actor": actor,
                   "title": "Acme opens chip plant in Arizona", "event_date": "2026-10-01"}]
        evidence = [
            {"evidence_id": f"EV-{run_id}-1", "event_id": "EVENT-1", "source_id": "SRC-1",
             "evidence_type": "source_excerpt", "source_excerpt": "Acme said it opened a chip plant in Arizona on Monday.",
             "published_at": "2026-10-01"},
            {"evidence_id": f"EV-{run_id}-2", "event_id": "EVENT-1", "source_id": "SRC-1",
             "evidence_type": "model_inference", "text": "This is a model-written inference that must not be stored."},
        ]
        return self.memory.remember(run_id=run_id, topic="chips", events=events, evidence=evidence)

    def test_earlier_run_is_found_by_shared_actor(self):
        self.assertEqual(self.remember("RUN-1"), 1)
        records = self.memory.earlier_related(
            development={"actors": ["Acme"], "title": "Acme expands in Texas"}, current_run_id="RUN-2")
        self.assertTrue(records)
        self.assertEqual(records[0]["gap_type"], "earlier")
        self.assertEqual(records[0]["evidence_role"], "earlier_coverage")
        self.assertIn("Arizona", records[0]["text"])

    def test_memory_ids_never_collide_with_this_runs_ids(self):
        self.remember("RUN-1")
        records = self.memory.earlier_related(development={"actors": ["Acme"]}, current_run_id="RUN-2")
        self.assertTrue(all(r["evidence_id"].startswith("MEM-") for r in records))
        self.assertEqual(records[0]["original_evidence_id"], "EV-RUN-1-1")

    def test_orchestrator_skips_excerpts_already_in_this_run(self):
        from services.research_orchestrator_service import ResearchOrchestratorService as Orchestrator
        self.remember("RUN-1")
        obj = object.__new__(Orchestrator)
        obj.event_memory_service = self.memory
        state = {"run_id": "RUN-2", "events": [], "evidence": [{"evidence_id": "EV-RUN-1-1"}]}
        self.assertEqual(obj._earlier_coverage([{"actors": ["Acme"]}], state), [])
        state["evidence"] = []
        found = obj._earlier_coverage([{"actors": ["Acme"]}, {"actors": ["Acme"]}], state)
        self.assertEqual(len(found), 1)  # no duplicates across developments
        self.assertEqual(state["event_memory"]["earlier_records"], 1)

    def test_only_source_excerpts_are_stored(self):
        self.remember("RUN-1")
        records = self.memory.earlier_related(development={"actors": ["Acme"]}, current_run_id="RUN-2")
        self.assertTrue(all("model-written" not in r["text"] for r in records))

    def test_current_run_and_current_events_are_excluded(self):
        self.remember("RUN-1")
        self.assertEqual(self.memory.earlier_related(development={"actors": ["Acme"]}, current_run_id="RUN-1"), [])
        self.assertEqual(self.memory.earlier_related(development={"actors": ["Acme"]}, current_run_id="RUN-2",
                                                     exclude_underlying=["UNDERLYINGEVENT-A"]), [])

    def test_same_event_in_two_runs_is_one_row(self):
        self.remember("RUN-1")
        self.remember("RUN-2")
        import sqlite3
        with closing(sqlite3.connect(str(self.memory.path))) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)

    def test_unrelated_events_are_not_returned(self):
        self.remember("RUN-1")
        self.assertEqual(self.memory.earlier_related(
            development={"actors": ["Zenith"], "title": "Zenith bank cuts mortgage rates"}, current_run_id="RUN-2"), [])


# ---- non-obviousness ------------------------------------------------------

EVENTS = [
    {"event_id": "EVENT-1", "underlying_event_id": "U-A", "title": "Acme signs chip supply deal with Foundry"},
    {"event_id": "EVENT-2", "underlying_event_id": "U-A", "title": "Acme and Foundry agree supply contract"},
    {"event_id": "EVENT-3", "underlying_event_id": "U-B", "title": "Government adds export limits on chips"},
]
EVIDENCE = [
    {"evidence_id": "EV-1", "event_id": "EVENT-1", "source_id": "S1", "text": "a"},
    {"evidence_id": "EV-2", "event_id": "EVENT-2", "source_id": "S2", "text": "b"},
    {"evidence_id": "EV-3", "event_id": "EVENT-3", "source_id": "S3", "text": "c"},
    {"evidence_id": "EV-BG", "event_id": "", "source_id": "S4", "text": "background"},
]


def grounding(support):
    return {"review_status": "complete", "claims": [
        {"claim_id": "C1", "epistemic_type": "hidden_insight", "grounding_status": "inference_supported",
         "claim_text": "Acme's supply deal gives it room the export limits take from rivals.",
         "cited_evidence_ids": list(support), "supporting_evidence_ids": list(support)}]}


class FakeGround:
    def __init__(self, obvious="unsupported", alone=None):
        self.obvious, self.alone, self.calls = obvious, alone or {}, []

    def __call__(self, claims, records):
        ids = [r["evidence_id"] for r in records]
        self.calls.append(ids)
        if ids == [non_obviousness.OBVIOUS_EVIDENCE_ID]:
            return {"claims": [{"grounding_status": self.obvious} for _ in claims]}
        dev = "U-A" if "EV-1" in ids or "EV-2" in ids else "U-B"
        return {"claims": [{"grounding_status": self.alone.get(dev, "unsupported")}]}


def _plain_echo(prompt):
    """v6.26: the plain-language rewrite step; this fake returns the claim unchanged."""
    import json as _json
    claim = prompt.split("Claim: ", 1)[1].split("\n\n", 1)[0]
    return _json.dumps({"plain": claim})


def ask_ok(prompt):
    if "ONE plain sentence" in prompt:
        return _plain_echo(prompt)
    return {"takeaways": ["Acme secured chip supply.", "Export limits on chips were added."]}


class NonObviousnessTests(unittest.TestCase):
    def run_check(self, support, ground, ask=ask_ok, budget=lambda: True):
        return non_obviousness.check(grounding=grounding(support), evidence=EVIDENCE, events=EVENTS,
                                     ground=ground, ask=ask, budget_ok=budget)

    def test_two_reports_of_one_story_are_one_development(self):
        ground = FakeGround()
        result = self.run_check(["EV-1", "EV-2"], ground)
        self.assertFalse(result["passed"])
        self.assertEqual(result["reasons"], ["conclusion_single_development"])
        self.assertEqual(ground.calls, [])  # no model calls spent

    def test_obvious_from_headlines_fails(self):
        result = self.run_check(["EV-1", "EV-3"], FakeGround(obvious="supported"))
        self.assertFalse(result["passed"])
        self.assertEqual(result["reasons"], ["conclusion_obvious_from_headlines"])

    def test_one_development_alone_suffices_fails(self):
        result = self.run_check(["EV-1", "EV-3", "EV-BG"], FakeGround(alone={"U-A": "inference_supported"}))
        self.assertFalse(result["passed"])
        self.assertEqual(result["reasons"], ["conclusion_follows_from_one_development"])

    def test_cross_development_non_obvious_conclusion_passes(self):
        ground = FakeGround()
        result = self.run_check(["EV-1", "EV-3", "EV-BG"], ground)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["passing_claim_ids"], ["C1"])
        # background record is included in every single-development re-check
        self.assertTrue(all("EV-BG" in ids for ids in ground.calls[2:]))
        # v6.26: plain-meaning check + obvious check + two single-development checks
        self.assertEqual(len(ground.calls), 4)

    def test_headlines_come_one_per_development(self):
        prompts = []
        self.run_check(["EV-1", "EV-3"], FakeGround(), ask=lambda p: prompts.append(p) or ask_ok(p))
        headline_prompt = next(p for p in prompts if "news headlines" in p)  # v6.26: after the plain rewrite
        self.assertIn("Acme signs chip supply deal", headline_prompt)
        self.assertNotIn("agree supply contract", headline_prompt)
        self.assertIn("export limits", headline_prompt)

    def test_model_failure_fails_closed(self):
        def broken(prompt):
            raise TimeoutError("slow")
        result = self.run_check(["EV-1", "EV-3"], FakeGround(), ask=broken)
        self.assertFalse(result["passed"])
        self.assertTrue(result["reasons"][0].startswith("non_obviousness_untested"))

    def test_grounding_error_fails_closed(self):
        class Broken(FakeGround):
            def __call__(self, claims, records):
                if records[0]["evidence_id"] != non_obviousness.OBVIOUS_EVIDENCE_ID:
                    raise RuntimeError("down")
                return super().__call__(claims, records)
        result = self.run_check(["EV-1", "EV-3"], Broken())
        self.assertFalse(result["passed"])
        # v6.26: the first grounding call is now the plain-meaning check; still fails closed
        self.assertEqual(result["reasons"], ["non_obviousness_untested:plain_check_RuntimeError"])

    def test_no_time_fails_closed_without_model_calls(self):
        ground = FakeGround()
        result = self.run_check(["EV-1", "EV-3"], ground, budget=lambda: False)
        self.assertEqual(result["reasons"], ["non_obviousness_untested:time_budget"])
        self.assertEqual(ground.calls, [])

    def test_stated_conclusion_is_not_tested_here(self):
        data = grounding(["EV-1", "EV-3"])
        data["claims"][0]["grounding_status"] = "supported"
        result = non_obviousness.check(grounding=data, evidence=EVIDENCE, events=EVENTS,
                                       ground=FakeGround(), ask=ask_ok)
        self.assertEqual(result["reasons"], ["no_inferred_conclusion"])

    def test_takeaways_parse_from_text(self):
        self.assertEqual(non_obviousness.parse_takeaways('x {"takeaways": ["A.", "B."]} y'), ["A.", "B."])


class ReleaseIntegrationTests(unittest.TestCase):
    def test_failed_non_obviousness_releases_a_verified_connection(self):
        from tests.test_claim_support_gate import make_orchestrator, release, candidate, PASSING
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING},
                                nonobvious={"passed": False, "reasons": ["conclusion_obvious_from_headlines"]})
        (released, _, _), state = release(obj, [candidate("ONE")])
        self.assertEqual([r["finding_tier"] for r in released], ["strategic_connection"])
        attempt = state["claim_support_gate"]["attempts"][0]
        self.assertEqual(attempt["reasons"], ["not_a_hidden_insight:conclusion_obvious_from_headlines"])
        self.assertEqual(state["claim_support_gate"]["released_as"], "verified_connection")
        self.assertEqual(len(obj.nonobvious_calls), 1)

    def _release_with_events(self, obj, item, events):
        from tests.test_claim_support_gate import EVIDENCE, DEEP_PROFILE
        state = {"warnings": []}
        grounded = {item["insight_revision_id"]: obj._ground_insights([item], EVIDENCE)[item["insight_revision_id"]]}
        out = obj._claim_supported_release(
            topic="artificial intelligence", candidates=[item], grounded=grounded, events=events,
            evidence=list(EVIDENCE), graph_context={}, constraints={}, blueprint={}, profile=DEEP_PROFILE, state=state)
        return out, state

    def test_single_development_failure_is_not_mislabeled_as_connection(self):
        # Realistic case: two articles (EVENT-A, EVENT-B) about ONE story. The model's
        # scope count still says 2 events, so only the underlying-event check stops it.
        from tests.test_claim_support_gate import make_orchestrator, candidate, PASSING
        one = candidate("ONEDEV")
        one["metadata"]["scope_validation"]["distinct_event_count"] = 2
        events = [{"event_id": "EVENT-A", "underlying_event_id": "UNDERLYINGEVENT-SAME"},
                  {"event_id": "EVENT-B", "underlying_event_id": "UNDERLYINGEVENT-SAME"}]
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONEDEV": PASSING},
                                nonobvious={"passed": False, "reasons": ["conclusion_single_development"]})
        (released, _, _), state = self._release_with_events(obj, one, events)
        self.assertEqual(released, [])
        self.assertEqual(state["claim_support_gate"]["fallback_connection_rejected"],
                         "connection_rests_on_one_development")

    def test_restatement_of_one_story_is_not_shown_as_a_connection(self):
        # Even when the downgrade reason is only "obvious", a connection needs two
        # different underlying developments in its supporting evidence.
        from tests.test_claim_support_gate import make_orchestrator, candidate, PASSING
        item = candidate("ONESTORY")
        events = [{"event_id": "EVENT-A", "underlying_event_id": "UNDERLYINGEVENT-SAME"},
                  {"event_id": "EVENT-B", "underlying_event_id": "UNDERLYINGEVENT-SAME"}]
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONESTORY": PASSING},
                                nonobvious={"passed": False, "reasons": ["conclusion_obvious_from_headlines"]})
        (released, _, _), state = self._release_with_events(obj, item, events)
        self.assertEqual(released, [])
        self.assertEqual(state["claim_support_gate"]["fallback_connection_rejected"],
                         "connection_needs_two_underlying_developments")

    def test_two_developments_obvious_finding_is_still_a_connection(self):
        from tests.test_claim_support_gate import make_orchestrator, candidate, PASSING
        item = candidate("TWODEV")
        events = [{"event_id": "EVENT-A", "underlying_event_id": "UNDERLYINGEVENT-1"},
                  {"event_id": "EVENT-B", "underlying_event_id": "UNDERLYINGEVENT-2"}]
        obj = make_orchestrator(verdicts={"INSIGHTREV-TWODEV": PASSING},
                                nonobvious={"passed": False, "reasons": ["conclusion_obvious_from_headlines"]})
        (released, _, _), _ = self._release_with_events(obj, item, events)
        self.assertEqual([r["finding_tier"] for r in released], ["strategic_connection"])

    def test_passing_non_obviousness_releases_a_hidden_insight(self):
        from tests.test_claim_support_gate import make_orchestrator, release, candidate, PASSING
        obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": PASSING})
        (released, _, _), _ = release(obj, [candidate("ONE")])
        self.assertEqual([r["finding_tier"] for r in released], ["hidden_insight"])

    def test_orchestrator_check_fails_closed_when_grounding_breaks(self):
        from services.research_orchestrator_service import ResearchOrchestratorService as Orchestrator

        class Grounding:
            def ground_claims(self, **kwargs):
                raise RuntimeError("down")

        class LLM:
            def ask(self, prompt, **kwargs):
                return ask_ok(prompt)

        obj = object.__new__(Orchestrator)
        obj.grounding_service = Grounding()
        obj.extraction_agent = type("A", (), {"llm": LLM()})()
        obj.execution_budget_service = None
        result = obj._non_obviousness(grounding(["EV-1", "EV-3"]), EVENTS, EVIDENCE)
        self.assertFalse(result["passed"])
        self.assertTrue(result["reasons"][0].startswith("non_obviousness_untested"))

    def test_can_be_disabled_by_setting(self):
        from services.research_orchestrator_service import ResearchOrchestratorService as Orchestrator
        obj = object.__new__(Orchestrator)
        os.environ["EVENT_INTELLIGENCE_NONOBVIOUS_CHECK"] = "0"
        try:
            self.assertTrue(obj._non_obviousness({}, [], [])["passed"])
        finally:
            del os.environ["EVENT_INTELLIGENCE_NONOBVIOUS_CHECK"]


class ObjectiveIntegrityRegressionTests(unittest.TestCase):
    def test_stable_id_survives_materially_different_same_event_wording(self):
        a = ev("EVENT-1", "Acme acquires Widgetco robotics startup", source="S1", date="2026-10-05")
        b = ev("EVENT-2", "Acme completes acquisition of robotics startup Widgetco", source="S2", date="2026-10-06")
        first, _ = resolver().resolve([a])
        second, _ = resolver().resolve([a, b])
        self.assertEqual(first["EVENT-1"], second["EVENT-1"])
        self.assertEqual(second["EVENT-2"], second["EVENT-1"])

    def test_memory_reconciles_an_old_alias_without_merging_a_different_target(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = EventMemoryService(Path(folder) / "memory.sqlite3")
            def store(run_id, uid, target):
                event = {"event_id": f"EVENT-{run_id}", "underlying_event_id": uid,
                         "actor": "Acme", "title": f"Acme acquires {target} robotics platform",
                         "event_date": "2026-10-01"}
                evidence = [{"evidence_id": f"EVID-{run_id}", "event_id": event["event_id"],
                             "source_id": f"SRC-{run_id}", "evidence_type": "source_excerpt",
                             "source_excerpt": f"Acme acquired {target} robotics platform for warehouse automation.",
                             "published_at": "2026-10-01"}]
                memory.remember(run_id=run_id, topic="robotics", events=[event], evidence=evidence,
                                actor_of=lambda e: e.get("actor", ""))
            store("1", "UNDERLYINGEVENT-OLD", "Widgetco")
            # Simulate a resolver migration that proposes a different ID for the same occurrence.
            store("2", "UNDERLYINGEVENT-NEW", "Widgetco")
            # A different target must still be distinct.
            store("3", "UNDERLYINGEVENT-LUMEN", "Lumen")
            import sqlite3
            with closing(sqlite3.connect(str(memory.path))) as db:
                rows = db.execute("SELECT underlying_event_id,title FROM events ORDER BY underlying_event_id").fetchall()
            self.assertEqual(len(rows), 2)
            self.assertIn("UNDERLYINGEVENT-OLD", {row[0] for row in rows})
            self.assertIn("UNDERLYINGEVENT-LUMEN", {row[0] for row in rows})

class RepeatedOccurrenceRegressionTests(unittest.TestCase):
    def test_same_actor_target_months_apart_are_separate_developments(self):
        a = ev("EVENT-OLD", "Acme partners with Widgetco on warehouse robotics", actor="Acme",
               family="partnership", source="S1", date="2026-01-10")
        b = ev("EVENT-NEW", "Acme partners with Widgetco on warehouse robotics", actor="Acme",
               family="partnership", source="S2", date="2026-10-10")
        mapping, _ = resolver().resolve([a, b])
        self.assertNotEqual(mapping["EVENT-OLD"], mapping["EVENT-NEW"])

    def test_memory_disambiguates_repeated_same_target_occurrences_across_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = EventMemoryService(Path(folder) / "memory.sqlite3")
            def store(run_id, day):
                event = {"event_id": f"EVENT-{run_id}", "underlying_event_id": "UNDERLYINGEVENT-BASE",
                         "actor": "Acme", "title": "Acme partners with Widgetco on warehouse robotics",
                         "event_date": day}
                evidence = [{"evidence_id": f"EVID-{run_id}", "event_id": event["event_id"],
                             "source_id": f"SRC-{run_id}", "evidence_type": "source_excerpt",
                             "source_excerpt": "Acme partnered with Widgetco on warehouse robotics deployment.",
                             "published_at": day}]
                memory.remember(run_id=run_id, topic="robotics", events=[event], evidence=evidence,
                                actor_of=lambda e: e.get("actor", ""))
                return dict(memory.last_aliases)
            self.assertEqual(store("JAN", "2026-01-10"), {})
            aliases = store("OCT", "2026-10-10")
            self.assertIn("UNDERLYINGEVENT-BASE", aliases)
            self.assertNotEqual(aliases["UNDERLYINGEVENT-BASE"], "UNDERLYINGEVENT-BASE")
            aliases2 = store("OCT2", "2026-10-12")
            self.assertEqual(aliases2["UNDERLYINGEVENT-BASE"], aliases["UNDERLYINGEVENT-BASE"])
            import sqlite3
            with closing(sqlite3.connect(str(memory.path))) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()



class WindowsFileLockTests(unittest.TestCase):
    """v6.20.8: every memory connection is closed (an open handle locks the file on Windows)."""

    def test_event_memory_closes_every_connection(self):
        import sqlite3
        opened = []
        real = sqlite3.connect

        def tracking(*args, **kwargs):
            connection = real(*args, **kwargs)
            opened.append(connection)
            return connection

        with tempfile.TemporaryDirectory() as folder:
            sqlite3.connect = tracking
            try:
                memory = EventMemoryService(Path(folder) / "memory.sqlite3")
                event = {"event_id": "EVENT-1", "underlying_event_id": "U-1", "actor": "Acme",
                         "title": "Acme opens chip plant in Arizona", "event_date": "2026-10-01"}
                evidence = [{"evidence_id": "EV-1", "event_id": "EVENT-1", "source_id": "S1",
                             "evidence_type": "source_excerpt",
                             "source_excerpt": "Acme said it opened a chip plant in Arizona on Monday."}]
                memory.remember(run_id="RUN-1", topic="chips", events=[event], evidence=evidence)
                memory.earlier_related(development={"actors": ["Acme"]}, current_run_id="RUN-2")
            finally:
                sqlite3.connect = real
            self.assertTrue(opened)
            for connection in opened:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")  # closed connections refuse queries
