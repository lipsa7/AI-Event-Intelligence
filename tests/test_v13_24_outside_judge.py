"""v13.24: judgments of Gemini's output come from another model family; evidence-first premise check."""
import json

import pytest

from services import outside_judge, variant_view


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}

    def json(self):
        return self._body


class Tags:
    def __init__(self, names):
        self.names = names

    def __call__(self, url, timeout):
        assert url.endswith("/api/tags")
        return Resp(200, {"models": [{"name": n} for n in self.names]})


def test_no_ollama_falls_back_and_says_self_judged():
    outside_judge.reset()
    fallback = lambda p: {"better": "A"}
    assert not outside_judge.available(get=Tags([]))
    ask, who = outside_judge.judge_or(fallback)
    assert ask is fallback and who == "self-judged:gemini"


def test_picks_an_installed_non_google_model_and_calls_ollama(monkeypatch):
    outside_judge.reset()
    assert outside_judge.model(get=Tags(["gemma2:9b", "llama3.1:8b", "qwen2.5:7b"])) == "llama3.1:8b"
    assert outside_judge.available()
    ask, who = outside_judge.judge_or(lambda p: "gemini")
    assert ask is outside_judge.ask and who == "outside:llama3.1:8b"
    sent = []

    def post(url, headers, data, timeout):
        sent.append((url, json.loads(data), timeout))
        return Resp(200, {"choices": [{"message": {"content": '{"new": false}'}}]})
    assert outside_judge.ask("judge this", post=post) == '{"new": false}'
    url, body, timeout = sent[0]
    assert url == "http://localhost:11434/v1/chat/completions" and timeout == 180.0
    assert body["model"] == "llama3.1:8b" and body["messages"][1]["content"] == "judge this" and body["stream"] is False
    with pytest.raises(RuntimeError, match="HTTP 500"):
        outside_judge.ask("x", post=lambda *a, **k: Resp(500))
    monkeypatch.setenv("OUTSIDE_JUDGE_MODEL", "mistral:7b")
    assert outside_judge.model() == "mistral:7b"
    monkeypatch.setenv("OUTSIDE_JUDGE", "0")
    assert outside_judge.judge_or(lambda p: 1)[1] == "self-judged:gemini"
    outside_judge.reset()


def test_only_google_models_installed_is_not_an_outside_judge():
    outside_judge.reset()
    assert outside_judge.model(get=Tags(["gemma3:4b"])) == "" and not outside_judge.available()
    outside_judge.reset()


def test_evidence_first_premise_check():
    good = {"premises": [{"evidence_ids": ["EVID-1"], "notes": "quote_check=cited"},
                         {"evidence_ids": ["EVID-2"], "notes": "quote_check=elsewhere; citation_added_from_quote=EVID-3"}]}
    assert variant_view.evidence_problems(good) == []
    bad = {"premises": [{"evidence_ids": [], "notes": ""},
                        {"evidence_ids": ["EVID-2"], "notes": "quote_check=not_found"},
                        {"evidence_ids": ["EVID-4"], "notes": "quote_check=ambiguous"},
                        {"evidence_ids": ["EVID-5"]}]}
    assert variant_view.evidence_problems(bad) == ["premise_1_uncited", "premise_2_quote_not_found",
                                                   "premise_3_quote_ambiguous", "premise_4_quote_missing"]
    assert variant_view.evidence_problems({}) == ["no_premises"]


def test_select_uses_the_outside_judge_and_keeps_unsourced_for_the_claim_gate(monkeypatch):
    """v13.25b: a fact not yet matched to a source is noted, not dropped - the claim gate's challenge
    search attaches evidence and grounds it again; dropping it here would throw the insight away unsourced."""
    from datetime import datetime, timedelta, timezone
    from services.research_orchestrator_service import ResearchOrchestratorService as S
    monkeypatch.setenv("EVENT_INTELLIGENCE_VARIANT_VIEW", "1")
    near = (datetime.now(timezone.utc) + timedelta(days=40)).isoformat()
    base = {"leverage_analysis": {"holder": "OpenAI"}, "forecast_contract": {"question": "q", "deadline": near}}
    unsourced = dict(base, insight="OpenAI faces 3 suits.", premises=[{"evidence_ids": ["EVID-1"], "notes": "quote_check=not_found"}])
    sourced = dict(base, insight="OpenAI faces 4 suits.", premises=[{"evidence_ids": ["EVID-1"], "notes": "quote_check=cited"}])
    outside_calls, gemini_calls = [], []
    monkeypatch.setattr(outside_judge, "available", lambda: True)
    monkeypatch.setattr(outside_judge, "ask", lambda p: outside_calls.append(p) or ('{"new": true}' if p.startswith("Does") else
                                                                                      ('{"reasons": []}' if p.startswith("Pre") else '{"better": "A"}')))
    service = S.__new__(S)
    service._ask_briefing_model = lambda p: gemini_calls.append(p) or {}
    entry = {}
    kept = service._variant_select([unsourced, sourced], {"claims": [{"claim": "c", "source": "E"}]}, entry, [])
    assert entry["variant_dropped"] == [] and len(kept) == 1          # both survived; tournament kept one
    assert entry["judged_by"].startswith("outside:") and gemini_calls == []
    notes = {c["insight"]: c["metadata"]["evidence_check"] for c in (unsourced, sourced)}
    assert notes == {"OpenAI faces 3 suits.": ["premise_1_quote_not_found"], "OpenAI faces 4 suits.": ["ok"]}
