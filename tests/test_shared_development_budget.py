"""v6.13: the shared-development check's prompt budget follows the verifier model."""
from __future__ import annotations

import json
import os
from unittest.mock import patch

from agents.extraction_agent_v6 import ExtractionAgentV6 as Agent


class Judge:
    def __init__(self, model_name):
        self.model_name = model_name
        self.prompts = []

    def ask(self, prompt, system_prompt=None, **kwargs):
        self.prompts.append(prompt)
        return json.dumps({"groups": [
            {"group": 0, "verdict": "shared", "development": "agents escaping test sandboxes",
             "subject": "AI agents escaping test sandboxes",
             "members": [{"event": f"E0.{i}", "quote": "escaped its testing sandbox",
                          "subject_quote": "escaped its testing sandbox"} for i in range(1, 4)]},
        ]})


def big_group(events=12, words=220):
    """One development reported by many outlets, each passage ~1,500 characters."""
    filler = " ".join(["The report described the testing environment in detail."] * (words // 8))
    return [[
        {"id": f"EVENT-{i}", "actor": f"Lab {i}", "event_type": "incident",
         "text": f"Lab {i} said its agent escaped its testing sandbox. {filler}"}
        for i in range(1, events + 1)
    ]]


def test_large_group_is_checked_whole_by_a_long_context_verifier():
    judge = Judge("gemini-3.6-flash")
    agent = Agent(llm_service=judge, entity_tagger=None)
    groups = big_group()
    size = len(agent._build_shared_development_prompt([(0, groups[0])]))
    assert size > Agent.SHARED_DEVELOPMENT_MAX_CHARS  # the old budget left it unanswered
    assert agent.shared_development_max_chars() == Agent.SHARED_DEVELOPMENT_LONG_CONTEXT_MAX_CHARS
    result = agent.verify_shared_development(groups)
    assert len(judge.prompts) == 1
    assert result[0] is not None and result[0]["verified"]
    assert result[0]["members"] == ["EVENT-1", "EVENT-2", "EVENT-3"]


def test_small_context_verifier_keeps_the_original_budget():
    judge = Judge("qwen2.5:3b")
    agent = Agent(llm_service=judge, entity_tagger=None)
    assert agent.shared_development_max_chars() == Agent.SHARED_DEVELOPMENT_MAX_CHARS
    assert agent.verify_shared_development(big_group()) == [None]
    assert judge.prompts == []


def test_budget_override_and_cache_identity():
    agent = Agent(llm_service=Judge("gemini-3.6-flash"), entity_tagger=None)
    assert agent.shared_development_cache_identity()["input_budget"] == Agent.SHARED_DEVELOPMENT_LONG_CONTEXT_MAX_CHARS
    with patch.dict(os.environ, {"EXTRACTION_V6_SHARED_DEVELOPMENT_MAX_CHARS": "20000"}):
        assert agent.shared_development_max_chars() == 20_000
        assert agent.shared_development_cache_identity()["input_budget"] == 20_000
    with patch.dict(os.environ, {"EXTRACTION_V6_SHARED_DEVELOPMENT_MAX_CHARS": "lots"}):
        assert agent.shared_development_max_chars() == Agent.SHARED_DEVELOPMENT_LONG_CONTEXT_MAX_CHARS
    with patch.dict(os.environ, {"EXTRACTION_V6_SHARED_DEVELOPMENT_MAX_CHARS": "1"}):
        assert agent.shared_development_max_chars() == 4_000


def test_group_too_large_even_for_long_context_is_still_left_unanswered():
    judge = Judge("gemini-3.6-flash")
    agent = Agent(llm_service=judge, entity_tagger=None)
    huge = big_group(events=80, words=400)
    assert len(agent._build_shared_development_prompt([(0, huge[0])])) > Agent.SHARED_DEVELOPMENT_LONG_CONTEXT_MAX_CHARS
    assert agent.verify_shared_development(huge) == [None]
    assert judge.prompts == []
