"""26 Oct 2026 build: memory pass, convergence exclusion, dated earlier facts, inference lead,
supported-thesis tier fix, commentary page dates and the per-run call cap. No model calls."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

import services.research_orchestrator_service as ros
from services import hypothesis_tier, inference_summary, memory_pass
from services import strategic_hypothesis_lab as lab
from services.hypothesis_evidence_service import page_text_date

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
ACCORD = {"title": "Signing a voluntary safety agreement for AI technology", "actors": ["Donald Trump", "OpenAI"],
          "facts": [{"text": "Executives from Anthropic, OpenAI and Nvidia signed the voluntary White House Accord, "
                             "which sets no penalties."}]}


def memory_store(path, rows):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE events(underlying_event_id, title, actors, event_date, topic, first_run_id, last_run_id,"
               " first_seen, last_seen, source_ids, words)")
    db.execute("CREATE TABLE excerpts(evidence_id, underlying_event_id, source_id, text, published_at, run_id)")
    for uid, title, actors, day, run, seen, text in rows:
        words = sorted(memory_pass._words(f"{title} {text}"))
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (uid, title, json.dumps(actors), day, "ai", run, run, seen, seen, "[]", json.dumps(words)))
        db.execute("INSERT INTO excerpts VALUES (?,?,?,?,?,?)", (f"EVID-{uid}", uid, f"S-{uid}", text, day, run))
    db.commit()
    db.close()


@pytest.fixture
def stores(tmp_path, monkeypatch):
    mem = tmp_path / "memory.sqlite3"
    memory_store(mem, [
        ("U-PLEDGE", "Trump gathers AI leaders and backs self-regulation pledge", ["Donald Trump"], "2026-09-29",
         "RUN-OLD", "2026-10-09T12:00:00+00:00",
         "President Trump gathered AI leaders and said a voluntary self-regulation pledge is the answer to AI risk."),
        ("U-LATE", "Trump accord criticised by senators", ["Donald Trump"], "2026-10-20", "RUN-OLD",
         "2026-10-26T09:00:00+00:00",   # stored AFTER the cut-off below: must never be used
         "Senators criticised the voluntary accord signed by Donald Trump for lacking penalties or audits."),
        ("U-CURRENT", "Executives sign the White House Accord", ["Donald Trump"], "2026-10-22", "RUN-NOW",
         "2026-10-25T10:00:00+00:00",   # this run's own occurrence
         "Executives from Anthropic, OpenAI and Nvidia signed the voluntary White House Accord, which sets no penalties."),
        ("U-OTHER", "Bakery opens new branch", ["Local Bakery"], "2026-09-01", "RUN-OLD",
         "2026-10-09T12:00:00+00:00", "A local bakery opened a new branch selling bread and cakes in the town centre."),
    ])
    monkeypatch.setenv("EVENT_INTELLIGENCE_EVENT_MEMORY", str(mem))
    monkeypatch.setenv("EVENT_INTELLIGENCE_DATABASE_URL", "sqlite:///" + str(tmp_path / "missing.db"))
    monkeypatch.delenv(memory_pass.ENV, raising=False)
    return tmp_path


def test_memory_pass_retrieves_dated_earlier_coverage_only(stores):
    out = memory_pass.retrieve([ACCORD], {"run_id": "RUN-NOW"}, now=NOW)
    ids = [r["evidence_id"] for r in out["records"]]
    assert ids == ["MEM-EVID-U-PLEDGE"]                       # not the later, current or unrelated rows
    record = out["records"][0]
    assert record["gap_type"] == "earlier" and record["evidence_role"] == "earlier_coverage"
    assert record["published_at"] == "2026-09-29" and record["available_since"] == "2026-10-09"
    assert "current_event_corroboration" in record["prohibited_uses"]
    assert record["memory_match"]["lane"] == "same_actor"
    # eligible candidates per store after the cut-off and this-run filters; a missing store is skipped
    assert out["meta"]["stores"] == {"event_memory": 2, "event_intelligence_db": 0}
    assert out["meta"]["selected"][0]["content_sha256"] == record["content_sha256"]


def test_replay_cutoff_is_the_original_checkpoint_time(stores):
    state = {"run_id": "RUN-REPLAY", "replayed_from": {"run_id": "RUN-NOW", "saved_at": "2026-09-30T00:00:00+00:00"}}
    out = memory_pass.retrieve([ACCORD], state, now=NOW)
    assert out["records"] == [] and out["meta"]["cutoff"].startswith("2026-09-30")   # stored 9 Oct: too late


def test_memory_pass_can_be_switched_off(stores, monkeypatch):
    monkeypatch.setenv(memory_pass.ENV, "0")
    assert memory_pass.retrieve([ACCORD], {"run_id": "RUN-NOW"}, now=NOW)["records"] == []


def test_mechanism_cues_match_whole_words():
    assert memory_pass.mechanisms("raised $1 billion in a funding round") == {"funding_round"}
    assert "regulation_or_law" in memory_pass.mechanisms("a new bill would regulate AI")
    assert "regulation_or_law" not in memory_pass.mechanisms("act as a partner")


def test_database_snippet_must_appear_in_the_stored_article(tmp_path, monkeypatch):
    db_path = tmp_path / "ei.db"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE events(event_id, source_id, title, event_type, event_start_at, entity_mentions, snippets,"
               " created_at)")
    db.execute("CREATE TABLE articles(source_id, content, published_at, url)")
    quote = "President Trump said voluntary self-regulation pledges by AI leaders are the answer to AI risk."
    for eid, sid, body in (("E-OK", "S-OK", f"Intro. {quote} More."), ("E-BAD", "S-BAD", "Different text only.")):
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                   (eid, sid, "Trump backs AI pledge", "regulation_or_policy_action", None,
                    json.dumps([{"mention": "Donald Trump", "role": "actor"}]), json.dumps([quote]),
                    "2026-10-01 10:00:00"))
        db.execute("INSERT INTO articles VALUES (?,?,?,?)", (sid, body, "2026-09-30T08:00:00Z", "https://x/" + sid))
    db.commit()
    db.close()
    monkeypatch.setenv("EVENT_INTELLIGENCE_EVENT_MEMORY", str(tmp_path / "none.sqlite3"))
    monkeypatch.setenv("EVENT_INTELLIGENCE_DATABASE_URL", "sqlite:///" + str(db_path))
    out = memory_pass.retrieve([ACCORD], {"run_id": "RUN-NOW"}, now=NOW)
    assert [r["memory_store"] for r in out["records"]] == ["event_intelligence_db"]
    assert out["records"][0]["source_url"] == "https://x/S-OK"


def test_earlier_records_never_count_toward_convergence():
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    obj._source_reference = lambda sid: {"url": "", "publisher": ""}
    evidence = [{"evidence_id": f"MEM-{i}", "source_id": f"S{i}", "analytical_context_only": True,
                 "gap_type": "earlier", "source_url": f"https://pub{i}.example/a"} for i in range(3)]
    evidence.append({"evidence_id": "CTX", "source_id": "S9", "analytical_context_only": True, "gap_type": "policy",
                     "source_url": "https://rules.example/a"})
    events, publishers = obj._convergence_maps(evidence, [])
    assert set(events) == {"CTX"} and set(publishers) == {"CTX"}


def test_earlier_facts_render_in_a_dated_block_the_cap_never_cuts():
    facts = [{"fact_id": f"F{n}", "text": f"Current fact {n}.", "evidence_id": f"E{n}"} for n in range(1, 46)]
    facts += memory_pass.as_facts([{"text": "Trump backed a voluntary pledge.", "published_at": "2026-09-29",
                                    "occurred_on": "2026-09-29", "evidence_id": "MEM-1", "source_id": "S",
                                    "earlier_event_title": "pledge"}])
    text = lab._facts_text(facts)
    assert "[F40]" in text and "[F41]" not in text                       # current facts still capped at 40
    assert "EARLIER COVERAGE" in text and "[M1] (occurred 2026-09-29; reported 2026-09-29)" in text
    assert "M1" in {r["fact_id"] for r in lab.fact_rows(facts, limit=200)}   # still citable


def test_tier_admits_a_fully_supported_thesis():
    item = {"id": "H1", "thesis": "t", "grounding": {"thesis": "supported", "mechanism": "partially_supported"},
            "supporting_evidence": [{"evidence_id": "E1", "text": "a quote"}]}
    assert hypothesis_tier.qualifies(item)


def test_report_leads_with_the_strongest_inference_and_counts_it():
    sections = {"editorial_briefing": {"implications": [],
                                       "expected_implications": [{"text": "VC money keeps flowing."}]},
                "strategic_hypotheses": {"verified": [
                    {"id": "H1-N", "thesis": "Narrowed restatement.", "critic_inherited_from": "H1"},
                    {"id": "H3", "thesis": "Labs make academic science depend on their platforms."}]}}
    lead = inference_summary.lead(sections)
    assert lead["kind"] == "verified_hypothesis" and lead["text"].startswith("Labs make")   # originals first
    inv = inference_summary.inventory(sections)
    assert inv["counts"]["verified_hypothesis"] == 2 and inv["counts"]["expected_implication"] == 1
    assert inv["non_obvious_count"] == 2 and inv["lead_kind"] == "verified_hypothesis"
    empty = inference_summary.inventory({"event_landscape": [{"title": "news"}]})
    assert empty["inference_count"] == 0 and inference_summary.lead({}) is None


def test_commentary_page_date_is_read_from_the_article_text():
    assert page_text_date("Analysis | Brookings\nOctober 22, 2026\nTrump's pact is not enough").startswith("2026-10-22")
    assert page_text_date("By A. Writer, 9 Sep 2026. The accord...").startswith("2026-09-09")
    assert page_text_date("No date here at all.") == ""


def test_call_cap_stops_optional_stages_before_the_reserve(monkeypatch):
    from services import gemini_llm_service as gemini
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    obj.execution_budget_service = None
    monkeypatch.setenv(obj.CALL_CAP_ENV, "20")
    try:
        obj._apply_call_cap()
        assert obj._remaining_discretionary_seconds() == float("inf")
        for _ in range(17):
            gemini.consume_call()
        assert obj._remaining_discretionary_seconds() == 0.0          # 17 >= 85% of 20: optional stages stop
        gemini.consume_call(), gemini.consume_call(), gemini.consume_call()
        with pytest.raises(gemini.GeminiCallBudgetExceeded):
            gemini.consume_call()                                     # hard stop at the cap
    finally:
        gemini.set_call_budget(None)
    monkeypatch.delenv(obj.CALL_CAP_ENV)
    obj._apply_call_cap()
    assert obj._call_cap is None
