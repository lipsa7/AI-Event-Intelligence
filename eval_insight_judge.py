"""Check the insight judge against YOUR verdicts (v6.31).

    python eval_insight_judge.py            # about 14 short Gemini calls

Reads tests/data/insight_gold.json (pairs where you said which insight is better), asks the
same pairwise judge the app uses, in both orders, and prints pairwise accuracy, the order-flip
rate (position bias) and every disagreement. The judge is never asked to grade itself.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from services import insight_evaluator as judge


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=os.getenv("GEMINI_BRIEFING_MODEL", "gemini-3.6-flash"))
    args = parser.parse_args()
    from services.gemini_llm_service import GeminiLLMService
    llm = GeminiLLMService(model_name=args.model, default_json=True)

    def ask(prompt: str):
        return llm.ask(prompt, system_prompt="Return one JSON object only.", temperature=0.0,
                       response_format="json", metadata={"task": "insight_judge_eval"})

    gold = json.loads((Path(__file__).resolve().parent / "tests" / "data" / "insight_gold.json").read_text(encoding="utf-8"))
    # v6.35: pairs from your dashboard ratings count as your verdicts too.
    from services import insight_ratings
    rated = insight_ratings.gold_pairs(insight_ratings.load())
    known = {(p["a"], p["b"]) for p in gold.get("human_pairs") or []}
    gold["human_pairs"] = list(gold.get("human_pairs") or []) + [p for p in rated if (p["a"], p["b"]) not in known]
    result = judge.agreement(gold, ask)
    for name, title in (("agreement_with_you", "AGREEMENT WITH YOUR VERDICTS (only pairs you decided)"),
                        ("reference_sanity", "REFERENCE SANITY CHECK (not your verdicts; never counted above)")):
        block = result.get(name)
        print("\n" + title)
        if not block:
            print("  no pairs yet")
            continue
        print(json.dumps({k: v for k, v in block.items() if k != "rows"}, indent=1))
        for row in block["rows"]:
            mark = "ok " if row["judge"] == row["human"].upper() else "XX "
            print(" ", mark, row["id"], "expected:", row["human"], "judge:", row["judge"])
    if result["human_pairs"] < 10:
        print(f"\nOnly {result['human_pairs']} pairs carry your own verdicts; add more to human_pairs for a reliable figure.")


if __name__ == "__main__":
    main()
