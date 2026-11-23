"""v13.24: primary records, plain-language check, novelty against the consensus, pre-mortem. No network, no model."""
import json
from datetime import datetime, timedelta, timezone

from services import primary_data, variant_view

TODAY = datetime(2026, 11, 22, tzinfo=timezone.utc)


def fake_fetch(url, headers=None):
    if "federalregister" in url:
        assert "conditions%5Bterm%5D=AI+agent+safety" in url
        return {"results": [{"title": "Safety Standards for AI Agents", "type": "Proposed Rule",
                             "publication_date": "2026-11-10", "comments_close_on": "2027-01-09",
                             "html_url": "https://www.federalregister.gov/d/2026-1", "agencies": [{"name": "NIST"}]}]}
    if "efts.sec.gov" in url:
        assert headers and headers.get("User-Agent")
        return {"hits": {"hits": [{"_source": {"display_names": ["OpenAI Global LLC (CIK 0001)"], "form": "8-K",
                                                "file_date": "2026-11-18", "adsh": "0001-26-000001", "ciks": ["0000000001"]}}]}}
    if "courtlistener" in url:
        assert headers["Authorization"] == "Token T"
        return {"results": [{"caseName": "California v. OpenAI", "docketNumber": "3:26-cv-0001", "court": "N.D. Cal.",
                             "dateFiled": "2026-11-16", "docket_absolute_url": "/docket/1/california-v-openai/"}]}
    raise AssertionError(url)


def test_primary_sources_become_citable_records(monkeypatch):
    monkeypatch.setenv("COURTLISTENER_API_TOKEN", "T")
    rows, receipt = primary_data.collect({"federal_register": "AI agent safety", "sec": "OpenAI",
                                          "court": "California v. OpenAI"}, fetch=fake_fetch)
    texts = [r["text"] for r in rows]
    assert texts[0] == ('Federal Register proposed rule "Safety Standards for AI Agents" by NIST, published 2026-11-10; '
                        'comments close 2027-01-09.')
    assert texts[1] == 'SEC EDGAR: OpenAI Global LLC (CIK 0001) filed form 8-K on 2026-11-18 that mentions "OpenAI".'
    assert texts[2] == "Court docket: California v. OpenAI, No. 3:26-cv-0001, N.D. Cal., filed 2026-11-16."
    assert receipt["court"] == {"query": "California v. OpenAI", "found": 1}
    recs = primary_data.records(rows, "story")
    assert all(r["evidence_id"].startswith("EVID-PRIMARY-") and r["gap_type"] == "numbers"
               and r["analytical_context_only"] for r in recs)
    assert recs[0]["source_url"] == "https://www.federalregister.gov/d/2026-1"


def test_primary_data_fails_quietly_and_needs_a_token_for_courts(monkeypatch):
    monkeypatch.delenv("COURTLISTENER_API_TOKEN", raising=False)

    def broken(url, headers=None):
        raise ConnectionError("blocked")
    rows, receipt = primary_data.collect({"federal_register": "x", "sec": "y", "court": "z"}, fetch=broken)
    assert rows == [] and receipt["federal_register"]["error"] == "ConnectionError"
    assert receipt["court"] == {"query": "z", "found": 0}
    monkeypatch.setenv("EVENT_INTELLIGENCE_PRIMARY_DATA", "0")
    assert primary_data.collect({"sec": "y"}, fetch=broken) == ([], {"status": "off"})


def test_plain_language_check():
    ok = {"insight": "California's suit asks $2,500 per violation. OpenAI will settle before the 9 Jan comment deadline."}
    assert variant_view.plain_language_problems(ok) == []
    jargon = {"insight": "A telemetry blind spot creates a structural asymmetry for OpenAI."}
    assert variant_view.plain_language_problems(jargon) == ["jargon:structural asymmetry,blind spot,telemetry blind spot"]
    long = {"insight": " ".join(["word"] * 40) + ". Second."}
    assert variant_view.plain_language_problems(long) == ["thesis_40_words"]


def test_novelty_against_consensus():
    view = {"claims": [{"claim": "OpenAI will contest the suit for years.", "source": "EVID-1"}]}
    restates = variant_view.novelty("OpenAI will fight it in court.", view,
                                    lambda p: json.dumps({"new": False, "overlap_quote": "contest the suit"}))
    assert restates == {"new": False, "checked": True, "overlap": "contest the suit", "what_is_new": ""}
    assert "CONSENSUS[1]: OpenAI will contest the suit for years." in variant_view.novelty_prompt("x", view)
    assert variant_view.novelty("x", {}, lambda p: {"new": False})["new"] is True        # no consensus: not judged
    assert variant_view.novelty("x", view, lambda p: "junk") == {"new": True, "checked": False, "error": "bad_answer"}


def test_premortem_kills_only_when_failure_is_already_in_evidence():
    cand = {"insight": "OpenAI settles by 9 Jan.", "forecast_contract": {"question": "Will OpenAI settle?", "deadline": "2027-01-09"}}
    lines = ["[EVID-7] OpenAI said on 18 Nov it will fight the case to trial."]
    killed = variant_view.premortem(cand, lines, lambda p: {"reasons": [
        {"reason": "OpenAI chose trial", "already_in_evidence": True, "evidence_id": "EVID-7"},
        {"reason": "court delay", "already_in_evidence": False}]})
    assert killed["killed"] and killed["evidenced"] == [{"reason": "OpenAI chose trial", "evidence_id": "EVID-7"}]
    alive = variant_view.premortem(cand, lines, lambda p: {"reasons": [{"reason": "court delay", "already_in_evidence": False}]})
    assert not alive["killed"]
    assert "[EVID-7] OpenAI said" in variant_view.premortem_prompt(cand, lines)


def test_select_repairs_revises_and_downgrades_instead_of_dropping(monkeypatch):
    """v13.25c: a rule miss is repaired once, a restatement revised once, a pre-mortem risk downgraded;
    only a candidate whose repair fails is held back (shown as a draft)."""
    from services import outside_judge
    from services.research_orchestrator_service import ResearchOrchestratorService as S
    monkeypatch.setenv("EVENT_INTELLIGENCE_VARIANT_VIEW", "1")
    near = (datetime.now(timezone.utc) + timedelta(days=40)).isoformat()
    far = (datetime.now(timezone.utc) + timedelta(days=400)).isoformat()   # v13.31: horizon up to 365 days

    def cand(text, deadline=near):
        return {"insight": text, "leverage_analysis": {"holder": "OpenAI"},
                "premises": [{"text": "p", "evidence_ids": ["EVID-1"], "notes": "quote_check=cited"}],
                "forecast_contract": {"question": "Will OpenAI settle?", "deadline": deadline}}
    jargon = cand("A structural asymmetry: OpenAI faces $2,500 fines.")
    late = cand("OpenAI faces $2,500 fines and settles.", deadline=far)
    restating = cand("OpenAI will contest the $2,500-per-violation suit.")
    risky = cand("OpenAI settles the $2,500-per-violation suit by January.")
    hopeless = cand("A paradigm shift for OpenAI on 3 fronts.")
    repairs = []

    def gemini(prompt):                              # the generator repairs; it never judges
        assert prompt.startswith("Revise this insight"), "Gemini was asked to judge"
        repairs.append(prompt)
        if "paradigm" in prompt:
            return {"insight": "Still a paradigm shift for OpenAI on 3 fronts."}
        if "THE CONSENSUS IT REPEATS" in prompt:
            return {"insight": "The 9 Jan comment deadline forces OpenAI to answer 2 regulators first."}
        if "structural asymmetry" in prompt:
            return {"insight": "OpenAI faces $2,500 fines per violation in California."}
        return {"insight": "OpenAI must answer the complaint by 15 Jan.",
                "forecast_contract": {"question": "Will OpenAI answer by 15 Jan?", "deadline": near,
                                      "resolution_criteria": "docket"}}

    def judge(prompt):
        if prompt.startswith("Does the CANDIDATE"):
            return json.dumps({"new": "contest" not in prompt.split("CANDIDATE: ")[1], "overlap_quote": "contest"})
        if prompt.startswith("Pre-mortem"):
            killed = "settles the" in prompt
            return json.dumps({"reasons": [{"reason": "r", "already_in_evidence": killed, "evidence_id": "EVID-7" if killed else ""}]})
        return json.dumps({"better": "A"})
    monkeypatch.setattr(outside_judge, "available", lambda get=None: True)
    monkeypatch.setattr(outside_judge, "ask", judge)
    service = S.__new__(S)
    service._ask_briefing_model = gemini
    service.VARIANT_KEEP = 10
    entry = {}
    kept = service._variant_select([jargon, late, restating, risky, hopeless],
                                   {"claims": [{"claim": "OpenAI will contest.", "source": "E"}]}, entry,
                                   [{"evidence_id": "EVID-7", "text": "OpenAI plans to settle."}])
    texts = [k["insight"] for k in kept]
    assert "OpenAI faces $2,500 fines per violation in California." in texts          # jargon repaired
    assert "OpenAI must answer the complaint by 15 Jan." in texts                       # late call repaired
    assert "The 9 Jan comment deadline forces OpenAI to answer 2 regulators first." in texts   # revised
    assert texts[-1] == risky["insight"]                                                # downgraded, last
    assert [d["insight"] for d in entry["variant_dropped"]] == ["Still a paradigm shift for OpenAI on 3 fronts."]
    assert entry["variant_downgraded"] == 1 and entry["variant_repaired"] == 4


def test_without_an_outside_model_nothing_is_judged_by_gemini(monkeypatch):
    from services.research_orchestrator_service import ResearchOrchestratorService as S
    monkeypatch.setenv("EVENT_INTELLIGENCE_VARIANT_VIEW", "1")
    near = (datetime.now(timezone.utc) + timedelta(days=40)).isoformat()
    item = {"insight": "OpenAI answers by 15 Jan.", "leverage_analysis": {"holder": "OpenAI"},
            "premises": [{"evidence_ids": ["E"], "notes": "quote_check=cited"}],
            "forecast_contract": {"question": "q", "deadline": near}}
    service = S.__new__(S)
    service._ask_briefing_model = lambda p: (_ for _ in ()).throw(AssertionError("Gemini must not judge"))
    entry = {}
    kept = service._variant_select([item], {"claims": [{"claim": "c", "source": "E"}]}, entry, [])
    assert kept[0]["insight"] == item["insight"] and entry["judged_by"] == "not_judged:no_outside_model"
