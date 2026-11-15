"""
Test doubles for the v6 extractor's two model calls: event frames and
AI-link verification.

The verifier double answers from a table of verdicts keyed by candidate
sentence text. It stands in for the model's judgment; these tests check
what the code does with a verdict (prompt context, quote check, fail-closed,
what reaches the gate), not the judgment itself. tests/eval_ai_link_verifier.py
measures the real model's judgment.
"""

from __future__ import annotations

import json
import re

VERIFICATION_HEADER = "AI-LINK VERIFICATION"

# Verdicts a correct verifier would give for the sentences used across the tests.
DEFAULT_VERDICTS = {
    "The company raised $40 million in Series B funding for its AI underwriting engine.":
        ("supported", "funding for its AI underwriting engine"),
    "Acme Inc. announced a partnership with Beta Corp to deploy AI agents across its claims unit.":
        ("supported", "to deploy AI agents"),
    "The rollout covers generative AI agents in 40 branches.":
        ("supported", "The rollout covers"),
    "Sarvam launched a new Indic LLM for banks.":
        ("supported", "launched a new Indic LLM"),
    "The deal includes an AI concierge for guests.":
        ("supported", "The deal includes"),
    "Separately, OpenAI released its new generative AI model.":
        ("not_supported", ""),
    "Separately, OpenAI released a new model.":
        ("not_supported", ""),
    "The resort has two swimming pools.":
        ("not_supported", ""),
    # Actor background: Hotel Group's AI is about payroll, not the acquisition.
    "Hotel Group uses generative AI for internal payroll.":
        ("not_supported", ""),
}


def actor_action_reply(prompt: str) -> str:
    """Answer the v6.10.1 ACTOR-ACTION VERIFICATION prompt: "supported", quoting
    the actor's name with the next word and the first three words of the
    action sentence (the stub stands in for the model's judgment)."""
    actor = json.loads(re.search(r'^ACTOR: (".*?"); sentence: (S\d+)$', prompt, re.M).group(1))
    actor_id = re.search(r'; sentence: (S\d+)$', prompt, re.M).group(1)
    action_id = json.loads(re.search(r"^ACTIONS: (.*)$", prompt, re.M).group(1))[0]
    text = lambda sid: re.search(rf"^\[{sid}\] (.*)$", prompt, re.M).group(1)
    actor_text = text(actor_id)
    words = actor_text[actor_text.find(actor):].split()
    return json.dumps({
        "verdict": "supported",
        "actor_quote": " ".join(words[:len(actor.split()) + 1]),
        "action_quote": " ".join(text(action_id).split()[:3]),
    })


def is_actor_action_prompt(prompt: str) -> bool:
    return prompt.startswith("ACTOR-ACTION VERIFICATION")


def is_verification_prompt(prompt: str) -> bool:
    return prompt.startswith(VERIFICATION_HEADER)


def parse_claims(prompt: str) -> list[dict]:
    """Claims as the verifier sees them: number, actor, action, context lines, candidate."""
    claims = []
    pattern = re.compile(
        r'^Claim (\d+)\nEVENT: actor "(.*?)"; action: "(.*?)"\n(.*?)^CANDIDATE: (S\d+)$',
        flags=re.MULTILINE | re.DOTALL,
    )
    for match in pattern.finditer(prompt):
        number, actor, action, body, candidate_id = match.groups()
        context = [
            line for line in body.splitlines()
            if line.startswith("[S") or line.startswith("[…") or line == "¶"
        ]
        text = re.search(rf"^\[{candidate_id}\] (.*)$", body, flags=re.MULTILINE).group(1)
        claims.append({
            "claim": int(number),
            "actor": actor,
            "action": action,
            "context": context,
            "candidate_id": candidate_id,
            "candidate_text": text,
        })
    return claims


def verification_reply(prompt: str, verdicts: dict | None = None) -> str:
    table = dict(DEFAULT_VERDICTS)
    table.update(verdicts or {})
    rows = []
    for claim in parse_claims(prompt):
        verdict, linking = table.get(claim["candidate_text"], ("unclear", ""))
        rows.append({"claim": claim["claim"], "verdict": verdict, "linking_text": linking})
    return json.dumps({"verdicts": rows})


class FramesAndVerifier:
    """Returns canned frames for extraction prompts and table verdicts for verification.

    frames: list of frames; by default only those whose action sentences are in
    the prompt window are returned. verdicts: overrides for DEFAULT_VERDICTS.
    fail_verification: raise on verification calls. drop_claims: claim numbers
    the verifier leaves out of its reply. Records every prompt.
    """

    model_name = "stub-model"

    def __init__(self, frames, *, verdicts=None, fail_on=None, filter_by_window=True,
                 fail_verification=False, raw_verification_reply=None, drop_claims=()):
        self.frames = frames
        self.verdicts = verdicts or {}
        self.fail_on = fail_on or set()
        self.filter_by_window = filter_by_window
        self.fail_verification = fail_verification
        self.raw_verification_reply = raw_verification_reply
        self.drop_claims = set(drop_claims)
        self.extraction_prompts: list[str] = []
        self.verification_prompts: list[str] = []

    def ask(self, prompt, system_prompt=None, temperature=0.0, response_format=None):
        if is_actor_action_prompt(prompt):
            return actor_action_reply(prompt)
        if is_verification_prompt(prompt):
            self.verification_prompts.append(prompt)
            if self.fail_verification:
                raise TimeoutError("stub verifier timeout")
            if self.raw_verification_reply is not None:
                return self.raw_verification_reply
            reply = json.loads(verification_reply(prompt, self.verdicts))
            reply["verdicts"] = [row for row in reply["verdicts"] if row["claim"] not in self.drop_claims]
            return json.dumps(reply)
        self.extraction_prompts.append(prompt)
        present = set(re.findall(r"^\[(S\d+)\]", prompt, flags=re.MULTILINE))
        if present & self.fail_on:
            raise TimeoutError("stub timeout")
        frames = [
            f for f in self.frames
            if not self.filter_by_window or set(f.get("action_sentence_ids", [])) <= present
        ]
        return json.dumps({"events": frames})
