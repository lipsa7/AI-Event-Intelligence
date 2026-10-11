"""
Live check of the AI-link verifier's judgment (needs the real model).

The unit tests use a verdict table, so they only prove what the code does
with a verdict. This script asks the configured model (EXTRACTION_V6_PROVIDER,
default Gemini via EXTRACTION_V6_GEMINI_CLASS) about labeled cases and
reports where its judgment is wrong. Set EXTRACTION_V6_VERIFIER_MODEL to
evaluate a different verifier model. Positive = the cited sentence ties AI to
THIS event; negative = the AI belongs to someone/something else.

It also checks the shared-development check used for clustering
(SHARED_CASES): does the model confirm a group only when its events show the
same change?

Run:  python -m tests.eval_ai_link_verifier
Exit code 0 only when every case is right; 1 if any case is wrong or errors;
2 when no model is reachable (NOT RUN - that is not a pass).
"""

from __future__ import annotations

from collections import Counter

from agents.extraction_agent_v6 import ExtractionAgentV6, segment_source

# (name, text, actor, action sentence, candidate sentence, expected link)
CASES = [
    ("reviewer: pronoun refers to another company",
     "Hotel Group acquired a resort. Separately, OpenAI released its new generative AI model.",
     "Hotel Group", "S1", "S2", False),
    ("reviewer: link across a paragraph break",
     "Hotel Group acquired a resort.\n\nThe deal includes an AI concierge for guests.",
     "Hotel Group", "S1", "S2", True),
    ("rollout refers to the deal",
     "Infosys signed a deal with a European bank. The rollout covers generative AI agents in 40 branches.",
     "Infosys", "S1", "S2", True),
    ("industry trend, not this event",
     "Hotel Group acquired a resort. Analysts say AI is reshaping the hospitality industry.",
     "Hotel Group", "S1", "S2", False),
    ("possessive points at actor, AI belongs to rival",
     "Walmart opened 12 new stores in Texas. Its rival Amazon uses AI to set prices.",
     "Walmart", "S1", "S2", False),
    ("acquired asset will use AI",
     "Tata Motors bought a battery plant in Gujarat. The plant will use AI-driven quality inspection.",
     "Tata Motors", "S1", "S2", True),
    ("incidental AI mention about the same actor",
     "HDFC Bank raised its lending rates. The bank's chief executive spoke at an AI conference last month.",
     "HDFC Bank", "S1", "S2", False),
    ("funding use of proceeds",
     "Zeta Labs raised $40 million in Series B funding. The money will expand its AI underwriting engine.",
     "Zeta Labs", "S1", "S2", True),
    ("acquired company's product is ML-based",
     "Siemens agreed to acquire Altair for $10 billion. Altair makes simulation software built on machine learning.",
     "Siemens", "S1", "S2", True),
    ("same actor, unrelated AI product",
     "Reliance signed a crude supply deal with a Saudi producer. Reliance's telecom arm Jio launched an AI chatbot in May.",
     "Reliance", "S1", "S2", False),
    ("regulator fine about AI training, new paragraph",
     "The European Commission fined Meta 200 million euros.\n\nThe fine concerns how Meta trained its AI models on user data.",
     "The European Commission", "S1", "S2", True),
    ("AI sentence two sentences away, about a competitor",
     "Lufthansa ordered 20 Airbus A350 jets. The order was placed on Monday. Rival Emirates has deployed AI for crew scheduling.",
     "Lufthansa", "S1", "S3", False),
    ("reviewer: actor's background AI, not this acquisition",
     "Hotel Group uses generative AI for internal payroll. The company acquired a beach resort.",
     "Hotel Group", "S2", "S1", False),
    ("reviewer: link after eight intervening details",
     "Hotel Group acquired a resort. " + " ".join(f"The resort has feature number {n}." for n in range(1, 9))
     + " The deal includes an AI concierge for guests.",
     "Hotel Group", "S1", "S10", True),
    ("unusual wording, no AI keyword",
     "Hotel Group acquired a resort. The deal adds software that learns each guest's preferences.",
     "Hotel Group", "S1", "S2", True),
]


# (name, [(actor, event text), ...], expected: shared development?)
SHARED_CASES = [
    ("three banks adopt AI for loan underwriting", [
        ("HDFC Bank", "HDFC Bank deployed generative AI agents to underwrite small-business loans."),
        ("Axis Bank", "Axis Bank moved its retail credit underwriting to a machine-learning model."),
        ("Kotak Bank", "Kotak Bank signed a deal with OpenAI to automate loan underwriting."),
    ], True),
    ("AI funding rounds in unrelated fields", [
        ("Insilico Medicine", "Insilico Medicine raised $110 million for AI drug discovery."),
        ("Groq", "Groq raised $640 million to build AI inference chips."),
    ], False),
    ("same company, different developments", [
        ("Microsoft", "Microsoft signed a power purchase deal for its AI data centres in Texas."),
        ("Microsoft", "Microsoft added an AI tutor to its Teams education product."),
    ], False),
    ("export rules on AI chips from two governments", [
        ("US Commerce Department", "The US Commerce Department restricted exports of advanced AI chips to China."),
        ("Dutch government", "The Dutch government widened licence requirements for exports of chip-making tools used for AI processors."),
    ], True),
]


def build(text, actor, action_id, candidate_id):
    sentences, _, _ = segment_source(text)
    by_id = {sentence["id"]: sentence for sentence in sentences}
    action = by_id[action_id]
    # Actor is named in the action sentence, except "The company ..." cases.
    actor_id = action_id if actor in action["text"] else next(
        sentence["id"] for sentence in sentences if actor in sentence["text"])
    event = {
        "summary": action["text"],
        "description": "",
        "snippets": [action["text"]],
        "cited_sentences": [{key: action[key] for key in ("id", "paragraph_id", "start", "end", "text")}],
        "roles": {
            "actor": {"mention": actor, "sentence_id": actor_id},
            "action_sentence_ids": [action_id],
            "ai_link_sentence_ids": [],
        },
        "extraction_metadata": {},
        "_ai_link_candidates": [dict(by_id[candidate_id], origin="eval")],
    }
    return event, sentences


def main() -> int:
    agent = ExtractionAgentV6(entity_tagger=None, fallback_to_v5=False)
    if not agent._llm_chain:
        print(f"NOT RUN (this is not a pass): no model provider ({agent.provider_error})")
        return 2
    agent._verification_errors = []
    outcome = Counter()
    for name, text, actor, action_id, candidate_id, expected in CASES:
        event, sentences = build(text, actor, action_id, candidate_id)
        diagnostics: Counter = Counter()
        unanswered = agent._verify_ai_links([event], sentences, diagnostics)
        linked = bool(event["roles"]["ai_link_sentence_ids"])
        record = (event["roles"].get("ai_link_verification") or [{}])[0]
        rejected = (event["extraction_metadata"].get("ai_link_rejected") or [{}])[0]
        detail = record.get("linking_text") or rejected.get("reason", "")
        if unanswered:
            outcome["error"] += 1
            status = "ERROR"
        elif linked == expected:
            outcome["correct"] += 1
            status = "ok"
        else:
            outcome["false_link" if linked else "missed_link"] += 1
            status = "WRONG"
        print(f"{status:5} expected={'link' if expected else 'none':4} got={'link' if linked else 'none':4} "
              f"| {name} | {detail}")
    total = len(CASES)
    print(f"\nverifier={agent._verifier_model_name()} correct={outcome['correct']}/{total} "
          f"false_links={outcome['false_link']} missed_links={outcome['missed_link']} errors={outcome['error']}")
    if agent._verification_errors:
        print("errors:", agent._verification_errors[:3])

    print("\nShared-development check (clustering):")
    groups = [
        [{"id": f"E{n}", "actor": actor, "event_type": "", "text": text} for n, (actor, text) in enumerate(members)]
        for _, members, _ in SHARED_CASES
    ]
    answers = agent.verify_shared_development(groups)
    shared_correct = 0
    for (name, _, expected), answer in zip(SHARED_CASES, answers):
        if answer is None:
            print(f"ERROR expected={'shared' if expected else 'separate'} got=no valid answer | {name}")
            continue
        got = bool(answer.get("verified"))
        shared_correct += int(got == expected)
        print(f"{'ok' if got == expected else 'WRONG':5} expected={'shared' if expected else 'separate'} "
              f"got={'shared' if got else 'separate'} | {name} | {answer.get('development', '')}")
    print(f"shared-development correct={shared_correct}/{len(SHARED_CASES)}")
    return 0 if outcome["correct"] == total and shared_correct == len(SHARED_CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
