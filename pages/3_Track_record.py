"""Track record (v13.20, 22 Nov 2026): every figure recomputed from the app's own saved data.

Why this page exists: news sites and editorials rarely score their calls; forecasting platforms earn
trust by showing every resolved forecast, misses included. The app's claim over the news is
"original, backed and scored" - this page is where that claim can be checked (services/track_record.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import track_record  # noqa: E402

st.set_page_config(page_title="Track record", layout="wide")
st.title("Track record")
st.caption("Recomputed from this folder's saved reports, forecasts and your ratings. Misses are counted too.")

record = track_record.compute("data")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Runs saved", record["runs_saved"])
c2.metric("Runs with an original insight", record["runs_with_original_insight"])
c3.metric("Original insights (unique)", f"{record['original_insights']} ({record['unique_original_insights']})")
c4.metric("Abstained", f"{record['abstention_share']}%" if record["abstention_share"] is not None else "-")

st.subheader("Forecasts")
f1, f2, f3 = st.columns(3)
f1.metric("Made", record["forecasts_made"])
f2.metric("Resolved", record["forecasts_resolved"])
f3.metric("Brier score", record["brier"] if record["brier"] is not None else "-")
st.caption("Brier: 0 is perfect, 0.25 is always saying 50%, about 0.12 is the general public / best LLM on "
           "ForecastBench. About 100 resolved forecasts are needed before differences under 0.01 mean "
           "anything (MacKay, Significance 2025)." + ("" if record["brier_credible"] else " Not credible yet."))

st.subheader("Reader ratings (original insights)")
for key, label in (("new_to_me", "New to me"), ("would_act", "Would act on it")):
    row = record["ratings"][key]
    if row["n"]:
        st.write(f"**{label}:** {row['share']}% of {row['n']} rated (95% interval ±{row['plus_minus']} points)"
                 + ("" if row["credible"] else f" — not credible below {track_record.RATING_CREDIBLE_N} ratings"))
    else:
        st.write(f"**{label}:** no ratings yet. Answer the two questions under each insight on the main page.")

st.subheader("Summary")
for line in track_record.summary_lines(record):
    st.write("- " + line)
st.caption("Topics covered: " + (", ".join(record["topics"]) or "-")
           + ". Repeats are found by word overlap, so reworded repeats can be missed. Reports saved before "
             "v6.30.2 have no 'already in the coverage' label, so their insights count as original here.")

if record["recent_original_insights"]:
    st.subheader("Latest original insights")
    for item in record["recent_original_insights"]:
        st.markdown(f"- {item['date']} — {item['text'][:400]}")
