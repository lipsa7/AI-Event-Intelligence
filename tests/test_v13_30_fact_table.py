"""v13.30 (research report of 22 Nov, Method 1): the fact table - figures extracted and compared by code."""
from datetime import datetime, timedelta, timezone

from services import fact_table, variant_view

RECORDS = [
    {"evidence_id": "E1", "event_id": "V1", "source_published_at": "2026-11-17T10:00:00Z",
     "text": "California sued OpenAI on Nov 17, 2026 over 80 incidents, including the Hugging Face attack in July."},
    {"evidence_id": "E2", "event_id": "V1", "text": "Bonta sent a subpoena on September 30, 2026."},
    {"evidence_id": "E3", "event_id": "V2", "text": "Revenue rose to $13 billion from $3.7 billion, a margin of 40% against 25%."},
]


def test_extract_finds_dates_amounts_percentages_and_counts():
    rows = fact_table.extract(RECORDS)
    kinds = {r["kind"] for r in rows}
    assert {"date", "money", "percent", "count", "published"} <= kinds
    assert any(r["kind"] == "count" and r["value"] == 80 for r in rows)


def test_compute_gaps_ratios_and_points_with_their_evidence():
    computed = fact_table.compute(fact_table.extract(RECORDS))
    texts = " | ".join(c["text"] for c in computed)
    assert "September 30, 2026 to Nov 17, 2026: 48 days" in texts
    assert "about 4 months" in texts                       # July (month only) to November
    assert any(c["kind"] == "money_ratio" and c["ratio"] == 3.5 for c in computed)
    assert any(c["kind"] == "percentage_points" and c["points"] == 15.0 for c in computed)
    assert all(c["calc_id"].startswith("CALC-") and c["evidence_ids"] for c in computed)


def test_amounts_are_never_compared_across_records():
    records = [{"evidence_id": "A", "text": "Hyperscalers spent $121.4 billion on AI infrastructure."},
               {"evidence_id": "B", "text": "The market will reach $1.33 trillion."}]
    assert not [c for c in fact_table.compute(fact_table.extract(records)) if c["kind"] == "money_ratio"]


def test_prompt_block_and_lens_instruction_carry_the_table():
    rows = fact_table.extract(RECORDS)
    computed = fact_table.compute(rows)
    block = fact_table.prompt_block(rows, computed)
    assert "COMPUTED BY CODE" in block and "CALC-1" in block
    text = variant_view.lens_instruction({"claims": [], "fact_table_block": block}, "constraints")
    assert "FACT TABLE" in text and "48 days" in text


def test_a_computed_figure_counts_as_sourced_for_the_number_check():
    computed = fact_table.compute(fact_table.extract(RECORDS))
    near = (datetime.now(timezone.utc) + timedelta(days=40)).isoformat()
    cand = {"insight": "OpenAI had 48 days between the subpoena and the suit to settle.",
            "leverage_analysis": {"holder": "OpenAI"}, "forecast_contract": {"question": "q", "deadline": near},
            "variant_perception": {"consensus_quote": "", "overlooked_evidence_id": "E2", "rival_hypothesis": "r",
                                   "load_bearing_assumption": "a",
                                   "mechanism_chain": [{"step": "OpenAI got a subpoena", "evidence_ids": ["E2"]},
                                                       {"step": "OpenAI was sued", "evidence_ids": ["E1"]}]}}
    without = variant_view.perception_problems(cand, {"claims": []}, RECORDS)
    with_table = variant_view.perception_problems(cand, {"claims": [], "computed_facts": computed}, RECORDS)
    assert any(p.startswith("number_not_in_chain") for p in without)
    assert not any(p.startswith("number_not_in_chain") for p in with_table)
