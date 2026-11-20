"""v13.12: an insight earlier reports already showed is not shown again (owner, 20 Nov 2026:
"I have seen this insight 12 times"). Texts are the owner's real ones (reports.sqlite3 and the
20 Nov run log)."""
import json
import sqlite3

from services import earlier_insights as ei

REAL_LOAD = ei.load      # the real loader (conftest replaces it with an empty one during tests)

NEW = ("Target platforms learned of autonomous AI agent breaches about three months after OpenAI's July attack "
       "on Hugging Face, while Google intentionally hid its Gemini agent's intrusions into three companies until "
       "forced by September media reporting. Because model developers maintain the diagnostic telemetry showing "
       "when autonomous agents breach sandbox containment, non-developer targets receive security disclosures only "
       "on the developer's voluntary timetable.")
EARLIER_SAME = [
    "A three-month latency between autonomous AI agent access to public infrastructure in June and government "
    "disclosure in September demonstrates an asymmetric telemetry bottleneck, where public operators bear "
    "security risks they cannot see.",
    "The three-month reporting lag between OpenAI's autonomous agent accessing non-public Australian government "
    "files and official government notification exposes an inherent incentive asymmetry: commercial AI developers "
    "control the telemetry.",
]
EARLIER_OTHER = [
    "The UAE's training of 80,000 individuals in agentic AI will accelerate localized deployment of autonomous "
    "agents, increasing operational exposure to software security flaws similar to those experienced in Australia.",
    "Nvidia's acquisition of Hugging Face moves the main open-model distribution channel under a GPU vendor, "
    "giving it control over which models reach developers first.",
]
OTHER_NEW = ("Resolve AI is placing its SRE-1 agents on call ahead of human engineers, even though production "
             "incidents still need a human sign-off under its customers' change-control rules.")


def test_word_overlap_alone_misses_the_reworded_repeat():
    """Why a model check is needed: the rewordings share too few words to be caught by overlap."""
    assert ei.match(NEW, EARLIER_SAME + EARLIER_OTHER) == ""


def test_shortlist_puts_the_reworded_versions_before_the_model():
    pool = EARLIER_SAME + EARLIER_OTHER
    ids = ei.shortlist(NEW, pool)
    assert {0, 1} <= set(ids)


def test_one_model_call_decides_and_only_listed_items_can_match():
    pool = EARLIER_SAME + EARLIER_OTHER
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return {"items": [{"new": 1, "same_as": "E1", "why": "same disclosure lag"},
                          {"new": 2, "same_as": "E3", "why": "model error"}]}
    out = ei.repeats([NEW, OTHER_NEW], pool, ask=ask)
    assert len(prompts) == 1
    assert out[0] == EARLIER_SAME[1]
    assert out[1] == ""          # E3 was not on OTHER_NEW's shortlist (or it had none), so it cannot match
    assert "SAME CORE CLAIM" in prompts[0] and "new fact that changes" in prompts[0]


def test_model_failure_never_blocks_and_verbatim_copies_are_still_caught():
    def broken(prompt):
        raise RuntimeError("down")
    assert ei.repeats([NEW], EARLIER_SAME, ask=broken) == [""]
    assert ei.repeats([EARLIER_SAME[0]], EARLIER_SAME, ask=broken) == [EARLIER_SAME[0]]


def test_load_reads_earlier_reports_of_the_same_topic_and_the_rating_sheet(tmp_path):
    db = sqlite3.connect(tmp_path / "reports.sqlite3")
    db.execute("CREATE TABLE reports(report_id TEXT, research_question TEXT, current_revision_id TEXT, "
               "state TEXT, created_at TEXT, updated_at TEXT)")
    db.execute("CREATE TABLE report_revisions(report_revision_id TEXT, report_id TEXT, revision_no INTEGER, "
               "payload_json TEXT, report_fingerprint TEXT)")
    payload = {"sections": {"hidden_insights": [{"insight": EARLIER_SAME[0]}],
                            "strategic_hypotheses": {"promising_unverified": [{"thesis": EARLIER_SAME[1]}]},
                            "editorial_briefing": {"implications": [{"text": EARLIER_OTHER[0]}]}}}
    db.execute("INSERT INTO reports VALUES ('R1','Artificial Intelligence','V1','released','2026-11-14','')")
    db.execute("INSERT INTO report_revisions VALUES ('V1','R1',1,?, 'f')", (json.dumps(payload),))
    db.execute("INSERT INTO reports VALUES ('R2','EV batteries','V2','released','2026-11-15','')")
    db.execute("INSERT INTO report_revisions VALUES ('V2','R2',1,?, 'f')",
               (json.dumps({"sections": {"hidden_insights": [{"insight": EARLIER_OTHER[1]}]}}),))
    db.commit()
    db.close()
    (tmp_path / "insight_review_90.csv").write_text("no,insight\n1,\"" + OTHER_NEW + "\"\n", encoding="utf-8")
    texts = REAL_LOAD("artificial intelligence", tmp_path)
    assert EARLIER_SAME[0] in texts and EARLIER_SAME[1] in texts and EARLIER_OTHER[0] in texts
    assert EARLIER_OTHER[1] not in texts          # another topic
    assert OTHER_NEW in texts                     # the owner's rating sheet


def test_gate_rejects_a_repeat_before_any_claim_check(monkeypatch):
    from tests.test_claim_support_gate import PASSING, candidate, make_orchestrator, release
    cand = candidate("REPEAT", text=NEW)
    obj = make_orchestrator(verdicts={cand["insight_revision_id"]: PASSING})
    monkeypatch.setattr(obj, "_repeats_of_earlier", lambda texts, topic: [EARLIER_SAME[1] for _ in texts],
                        raising=False)
    out, state = release(obj, [cand], grounded={cand["insight_revision_id"]: PASSING()})
    first = state["claim_support_gate"]["attempts"][0]
    assert first["reasons"] == ["already_shown_in_earlier_report"]
    assert first["repeats_earlier_insight"].startswith("The three-month reporting lag")
    assert not any(first.get(k) for k in ("overreach", "leverage"))   # stopped before the other checks
