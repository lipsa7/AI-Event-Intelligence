# Review package: the 26 Oct build (all remaining work, built offline)

Not committed. **No Gemini calls**: every run used a scratch copy of the project with no API key and a
call cap of 0. The full code diff is at the end. Builds on the installed B/E completion (v1).

## Why this build

The fresh run RUN-3E2FE (25 Oct) cost about ₹1000 and showed the code did not meet the objective:
- no main implications (both were "widely expected");
- no expert views;
- weak advice;
- no use of past runs.

Its own data, analysed offline, showed where good material was lost. Everything below was built and
tested against that saved data.

## Changes

| # | Change | Files | Why (evidence) |
|---|---|---|---|
| 1 | **Typed mechanism steps** | `strategic_hypothesis_lab.py`, `hypothesis_tier.py` | "A -> B -> C" was graded as one claim, so one unsourced step failed a good chain (EV3). Now the first step is a fact (must be backed), and each later step is a link (must follow). See "Typed steps" below for how many unbacked links are allowed. |
| 2 | **Leverage split merged** (GPT lab-dependency patch, by hand) | `strategic_hypothesis_lab.py`, GPT's `tests/test_lab_dependency_split.py` | Control must be supported; dependency may be inference_supported. The old ZIP's input hash no longer matched. |
| 3 | **Memory pass** | new `memory_pass.py`; orchestrator `_retrieve_memory_pass`; lab facts M1..; dashboard "Uses coverage from earlier runs" | Past events were never available to the lab as facts. See "Memory-pass safeguards" below. |
| 4 | **Memory never corroborates** | orchestrator `_convergence_maps` | Earlier/memory records now add no event and no publisher to convergence. Confirmed bug (GPT review). |
| 5 | **Dates reach the prompt** | `fact_rows`, `_facts_text`, `narrow_prompt` | Earlier facts render in a dated EARLIER COVERAGE block. The 40-fact cap never cuts them. Confirmed bug (GPT review). |
| 6 | **Duplicate facts merged** | `lab.dedupe_facts` | The real prompt had F6=F8, F10=F12, F14=F16. Each merged fact keeps every source's evidence ids; IDs are not renumbered. |
| 7 | **Report leads with an inference** | new `inference_summary.py`, dashboard `render_lead_inference`, `analysis_metadata.inference_inventory` | Your rule: "never blank" means some inference. See "Lead inference" below. |
| 8 | **Supported thesis qualifies for the tier** | `hypothesis_tier.py` | EVOLVED_H1 (thesis supported, mechanism partly backed) was hidden. |
| 9 | **Expert commentary dates** | `hypothesis_evidence_service.page_text_date`, `_process_source(date_from_text=…)` | CFR's and Brookings' analyses of the AI Accord were found and then dropped for "missing publication date". See "Commentary dates" below. |
| 10 | **Per-run cost cap** | orchestrator `_apply_call_cap`, `_call_cap_exhausted` | `EVENT_INTELLIGENCE_MAX_GEMINI_CALLS=N` caps every Gemini attempt and grounded search. See "Cost cap" below. |

**Typed steps (1)**
- Exactly one unbacked link makes the status **`analysis`**: the facts are sourced, and that link is shown as the stated assumption (an ICD 203 key assumption).
- Two or more unbacked links, an unbacked starting fact, or any contradiction still fail.
- The prompts now say: start the chain from a cited fact, and put general knowledge in a later step.
- Analysis items are not narrowed, are counted as passed in the funnel, and are shown first in the tier.

**Memory-pass safeguards (3)**
- **Local and read-only.** A missing store is skipped, never created.
- **Two lanes:** same actor (plus a shared mechanism, or 2 subject words), or same mechanism (plus 3 subject words, excluding cue and filler words). Mechanism cues are whole-word ("bill" is not "billion").
- **Dates and cut-off:** occurrence, publication and availability dates are kept separately. A record must be published and stored before the cut-off: the original checkpoint time for a replay, otherwise now.
- **Exclusions:** this run's own sources, its events, and its selected sources.
- **No re-reports:** text close to one current fact (≥ 0.35 Jaccard) is treated as the same news and excluded.
- **Verified excerpts:** a database excerpt must appear verbatim in the stored article text.
- **Deduplication** by occurrence and by passage.
- **Limits:** up to 3 records per development, 8 in total.
- **Recorded:** the selection (ids, content hashes, cut-off) is saved in `analysis_metadata.memory_pass`.
- **Labelled** as earlier coverage, never as a precedent. Off with `EVENT_INTELLIGENCE_MEMORY_PASS=0`.

**Lead inference (7)**
- Fall-through order: hidden insight > implication > verified hypothesis > analysis > hypothesis > analogy > expected implication > move. Each keeps its own label.
- A report with none is flagged (`report_without_inference`), and the dashboard says so.

**Commentary dates (9)**
- Applies to commentary pages only.
- When the page has no date tag, the first full date printed near the top of the article is used, subject to the usual window and "not in the future" checks.
- Recorded as `publication_date_basis: date_printed_in_page_text`.

**Cost cap (10)**
- At 85% of the cap, optional stages skip themselves (through the time-budget hook), so the rest goes to the briefing and lab.
- At 100% it stops hard.
- When unset, nothing changes.

## Objectives: status after this build

| Objective | Before | After this build (offline evidence) |
|---|---|---|
| Precedent ("happened before…") | ❌ | ⚠️ The memory pass now gives dated earlier facts; in my offline lab test, 2 of 4 hypotheses used them. Web precedent search is unchanged. |
| Leverage → move | ❌ | ⚠️ The control/dependency split removes the false "fact" grading; untested live. |
| Hidden insight / analysis | ❌ | ⚠️ The typed steps add an "analysis" tier, honestly labelled; the strict hidden-insight gate is unchanged. |
| Never blank (with inference) | ⚠️ | ✅ in code: a lead inference is always shown, and an empty report is flagged. |
| Expert views | ❌ | ⚠️ The pages that were dropped would now be accepted; untested live. |
| Uses past events | ❌ | ✅ local memory pass, with the safeguards above. |
| Cost control | ❌ | ✅ hard cap with a reserve for analysis. |

## Evidence

- **Tests:** new tests in `test_build_26oct_memory_cost_inference.py`, `test_typed_mechanism_analysis.py` and GPT's `test_lab_dependency_split.py`, all passing. Three existing tests were updated for intended changes:
  - `test_claim_support_gate.py`: the report now opens with "Main inference this run".
  - `test_v6_34_1_field_checks.py`: the mechanism label is "partially_supported" when the starting step is backed and the links are not; the item is still rejected.
  - `test_v6_36_4_leverage_test.py`: CONTROL/DEPENDENCY instead of LEVERAGE, from GPT's diff.
- **Full suite vs. this morning's baseline:** 59 failures before, 59 after. No new failures and none removed. The run used a scratch copy of the final code, compared with the failure list saved before today's work.
- **Zero-call smoke run** (scratch copy, replay of RUN-3E2FE, no key, cap 0): the whole pipeline ran without errors and released REPORT-571024FA…, with 0 Gemini calls.
  - `gemini_call_cap=0` was applied, and optional stages skipped cleanly.
  - The memory pass ran even with no model available. Its cut-off was the checkpoint time (2026-10-25T11:37:55Z), and it recorded 3 selections with their publication and storage dates.
  - The report was correctly flagged `report_without_inference`: without a model it has no inference, and it says so instead of looking complete.
  - First smoke run: it showed the memory pass skipped whenever the lab hit its budget. Fixed by moving it ahead of the budget check.
- **Memory retrieval on RUN-3E2FE's developments** (local, 1.1 s): it returned exactly three items: India rejecting the pact (4 Oct), and Trump on 29 Sep promoting self-regulation and saying the steps might be codified. No leak of today's news and no off-topic funding stories.
- **Claude standing in for the model** (`lab_gen_prompt.txt` / `lab_gen_answer.json` in the session scratchpad): the app's real generation prompt, with memory facts, parsed cleanly. Two of four hypotheses cited earlier coverage, e.g. "the voluntary accord works as a draft of future law, since Trump said on 29 Sep the steps might be codified". This shows what the prompts allow; it does not show what Gemini will produce.

## Known limits

- **Not measured live:** quality depends on Gemini's grading of individual links. Only one capped live run can show the real rate of "analysis" items.
- **Main pipeline unchanged:** claim-gate candidates (INSIGHT-…) still use the strict gate; typed steps apply to the hypothesis lab.
- **Precedent queries:** web precedent queries are unchanged (they still produced off-target searches on 25 Oct).
- **Retrieval thresholds:** set by hand on one run's data.
- **Temp-folder clean-up:** my baseline copies in Temp were partly deleted by the system on 23 and 26 Oct. Failure lists were saved before that happened, and the comparison uses those lists.

## Suggested single live run (only after review)

```
EVENT_INTELLIGENCE_MAX_GEMINI_CALLS=250   # hard cap; analysis keeps the last 15%
EVENT_INTELLIGENCE_DEEP_MAX_SOURCES=12    # fewer sources (default 20)
EVENT_INTELLIGENCE_DEEP_EXPANSION_ROUNDS=1
EVENT_INTELLIGENCE_DEEP_REFILL_ROUNDS=1
```

Don't keep the dashboard open during the run: its log handle blocked log rotation on 25 Oct.

## Full code diff (against the start of this build)
```diff
--- a/services/research_orchestrator_service.py
+++ b/services/research_orchestrator_service.py
@@ -852,6 +852,8 @@
             raise ResearchConfigurationError(
                 "topic cannot be empty."
             )
+
+        self._apply_call_cap()
 
         profile = (
             self._resolve_profile(
@@ -3506,6 +3508,15 @@
             facts += moves.case_facts(brief.select_cases(historical))
         except Exception:
             historical = list(_as_mapping(state.get("historical_context")).get("records") or [])
+        # Memory pass (services/memory_pass.py): earlier runs' events as dated facts M1, M2 ...
+        # Retrieved once per run in _attach_spec_sections (free, local), before any budget check.
+        from services import memory_pass
+        memory = _as_mapping(state.get("memory_pass"))
+        memory_records = [dict(r) for r in memory.get("records") or [] if isinstance(r, Mapping)]
+        base["memory_pass"] = _json_safe(memory.get("meta") or {"status": "not_retrieved"})
+        facts += memory_pass.as_facts(memory_records)
+        facts = lab.dedupe_facts(facts)
+        earlier_facts = {r["fact_id"]: r for r in lab.fact_rows(facts, limit=250) if r.get("earlier")}
         if len(lab.fact_rows(facts)) < 3:
             return dict(base, status="insufficient_facts")
 
@@ -3551,7 +3562,7 @@
         if not evolved:
             return dict(base, status="evolution_failed", raw_count=len(raw), models=models)
 
-        records = _unique_evidence_records(list(evidence) + context_records + historical)
+        records = _unique_evidence_records(list(evidence) + context_records + historical + memory_records)
         known_ids = {_clean(r.get("evidence_id")) for r in records if isinstance(r, Mapping)}
         research_receipts: list[dict[str, Any]] = []
 
@@ -3647,6 +3658,13 @@
                 list(cited_ids) + [eid for ids in basis_ids.values() for eid in ids])
             final = lab.finalize(item_for_final, grounded_rows, facts)
             final["grounded_claims"] = _json_safe(grounded_rows)
+            # Shown with the hypothesis: which earlier-run facts it relies on, with their dates.
+            cited = set(item.get("fact_ids") or []) | {x for ids in _as_mapping(item.get("basis_fact_ids")).values()
+                                                       for x in ids}
+            final["earlier_facts_used"] = [
+                {"fact_id": fid, "text": row["text"][:300], "reported": row.get("date", ""),
+                 "occurred": row.get("occurred_on", "")}
+                for fid, row in earlier_facts.items() if fid in cited]
             # v6.34.1 (GPT review): each supporting quote carries its source link, publisher and rating.
             final["supporting_evidence"] = [
                 self._quote_with_source(by_eid[eid])
@@ -3797,6 +3815,20 @@
 
     FOCUS_RESEARCH_MIN_SECONDS = 240.0
     SOURCE_BACKED_MAX_SHOWN = 2  # v6.30.2: backed insights that sources/experts already state
+
+    def _retrieve_memory_pass(self, developments: Sequence[Mapping[str, Any]],
+                              evidence: Sequence[Mapping[str, Any]], state: dict[str, Any]) -> None:
+        """Memory pass, once per run: local, read-only, no model call, so it runs even when the
+        call cap or time budget skips the analysis stages. Never fatal."""
+        from services import memory_pass
+        try:
+            result = memory_pass.retrieve(developments, dict(state, evidence=list(evidence or [])))
+        except Exception as error:
+            result = {"records": [], "meta": {"version": memory_pass.VERSION, "status": "failed:" + type(error).__name__}}
+        state["memory_pass"] = _json_safe(result)
+        meta = result["meta"]
+        logger.info("memory_pass=%s", json.dumps({k: v for k, v in meta.items() if k != "selected"}
+                                                 | {"selected": len(meta.get("selected") or [])}, default=str))
 
     def _choose_focus(self, developments: Sequence[Mapping[str, Any]], facts: Sequence[Mapping[str, Any]],
                       state: Mapping[str, Any]) -> dict[str, Any]:
@@ -3990,6 +4022,8 @@
                 ])
             if state.get("gap_research_summary") is not None:
                 report.setdefault("analysis_metadata", {})["precedent_research"] = state.get("gap_research_summary")
+            if isinstance(state, dict) and "memory_pass" not in state:
+                self._retrieve_memory_pass(developments, evidence, state)
             if deep and isinstance(state, dict):
                 try:
                     self._focus_research_before_briefing(developments, state)
@@ -4048,6 +4082,9 @@
             "past_cases": research.get("past_cases"),
             "case_found_after_briefing": list(state.get("case_found_after_briefing") or []),
         })
+        # Memory pass: what was selected (ids, hashes, cut-off) so comparisons can hold it fixed.
+        report.setdefault("analysis_metadata", {})["memory_pass"] = _json_safe(
+            _as_mapping(state.get("memory_pass")).get("meta") or {"status": "not_retrieved"})
         report["forecast_followups"] = _json_safe(self._forecast_followups(
             report=report, topic=topic, events=events, evidence=evidence, state=state, briefing=briefing))
         # v6.35: which arm of the experiment produced this report (baseline or profile).
@@ -16817,7 +16854,37 @@
     def _insight_key(insight: Mapping[str, Any]) -> str:
         return _clean(insight.get("insight_revision_id") or insight.get("insight_id"))
 
+    CALL_CAP_ENV = "EVENT_INTELLIGENCE_MAX_GEMINI_CALLS"
+    CALL_CAP_RESERVE = 0.15   # share of the cap kept for the briefing and hypothesis lab
+
+    def _apply_call_cap(self) -> None:
+        """Cost cap (26 Oct 2026): EVENT_INTELLIGENCE_MAX_GEMINI_CALLS caps every Gemini attempt and
+        grounded search in this run (services/gemini_llm_service.consume_call). Unset = no cap."""
+        raw = _clean(os.getenv(self.CALL_CAP_ENV))
+        self._call_cap = None
+        if not raw:
+            return
+        try:
+            cap = max(0, int(raw))
+        except ValueError:
+            logger.warning("%s=%r is not a number; no call cap applied", self.CALL_CAP_ENV, raw)
+            return
+        from services.gemini_llm_service import set_call_budget
+        set_call_budget(cap)
+        self._call_cap = cap
+        logger.info("gemini_call_cap=%s", cap)
+
+    def _call_cap_exhausted(self) -> bool:
+        """Past (1 - reserve) of the cap, optional stages stop so the rest goes to the report."""
+        cap = getattr(self, "_call_cap", None)
+        if cap is None:
+            return False
+        from services.gemini_llm_service import calls_used
+        return calls_used() >= cap * (1 - self.CALL_CAP_RESERVE)
+
     def _remaining_discretionary_seconds(self) -> float:
+        if self._call_cap_exhausted():
+            return 0.0
         remaining = getattr(self.execution_budget_service, "remaining_discretionary_seconds", None)
         if not callable(remaining):
             return float("inf")
@@ -17165,6 +17232,12 @@
                 continue
             evidence_id = _clean(record.get("evidence_id"))
             if not evidence_id:
+                continue
+            if (_clean(record.get("gap_type")) == "earlier"
+                    or _clean(record.get("evidence_role")) == "earlier_coverage"):
+                # Coverage from earlier runs (event memory, memory pass) shows a pattern over
+                # time; it never independently confirms today's event, so it adds no event
+                # and no publisher to convergence (GPT review of the memory-pass design).
                 continue
             event_id = _clean(record.get("event_id")).upper()
             if event_id:
@@ -21878,6 +21951,12 @@
             from services import insight_funnel
             funnel = insight_funnel.build(canonical_sections)
             canonical_sections["analysis_metadata"]["insight_funnel"] = _json_safe(funnel)
+            # "Never blank" means some inference reaches the reader: count it, flag a report with none.
+            from services import inference_summary
+            inventory = inference_summary.inventory(canonical_sections)
+            canonical_sections["analysis_metadata"]["inference_inventory"] = _json_safe(inventory)
+            if not inventory["inference_count"]:
+                logger.warning("report_without_inference run_id=%s", _clean(report.get("run_id")))
             logger.info("insight_funnel=%s", json.dumps(insight_funnel.summary(funnel), ensure_ascii=False, default=str))
         except Exception as error:
             logger.warning("insight_funnel failed: %s: %s", type(error).__name__, error)
--- a/frontend/dashboard.py
+++ b/frontend/dashboard.py
@@ -3940,7 +3940,9 @@
     thesis = clean_text(item.get("thesis") or item.get("insight") or item.get("text"))
     if not thesis:
         return []
-    lines = [("**Verified** · " if verified else "**Not yet backed** · ") + _markdown_safe(thesis)]
+    label = ("**Verified** · " if verified else "**Analysis** · " if item.get("status") == "analysis"
+             else "**Not yet backed** · ")
+    lines = [label + _markdown_safe(thesis)]
     checks = item.get("field_checks") if isinstance(item.get("field_checks"), Mapping) else {}
 
     def tag(field: str) -> str:
@@ -4000,6 +4002,7 @@
             quotes.append("\u201c" + _markdown_safe(q)[:200] + "\u201d (source not linked)")
     if quotes:
         lines.append("- **Backed by:** " + " · ".join(quotes[:3]))
+    lines += earlier_coverage_lines(item)
     if not verified:
         reasons = []
         grounding = item.get("grounding") if isinstance(item.get("grounding"), Mapping) else {}
@@ -4120,6 +4123,20 @@
                 st.success(f"Saved {saved} ratings.")
 
 
+def earlier_coverage_lines(item: Mapping[str, Any]) -> list[str]:
+    """Memory pass: earlier-run facts a hypothesis relies on, always shown with their dates."""
+    used = [e for e in as_list(item.get("earlier_facts_used")) if isinstance(e, Mapping)]
+    if not used:
+        return []
+    lines = ["- **Uses coverage from earlier runs** (a pattern over time, not confirmation of this run's news):"]
+    for e in used[:4]:
+        when = "; ".join(x for x in (f"occurred {clean_text(e.get('occurred'))}" if clean_text(e.get("occurred")) else "",
+                                     f"reported {clean_text(e.get('reported'))}" if clean_text(e.get("reported")) else "")
+                         if x) or "date not recorded"
+        lines.append(f"  - {_markdown_safe(e.get('fact_id'))} ({when}): {_markdown_safe(e.get('text'))[:220]}")
+    return lines
+
+
 def investigation_lines(item: Mapping[str, Any]) -> list[str]:
     """Plan fix E: one hypothesis to investigate - what the sources establish, kept apart from
     what is unresolved or assumed, the reasoning step, and what would disprove it."""
@@ -4128,6 +4145,10 @@
     if not thesis:
         return []
     lines = ["**" + _markdown_safe(thesis) + "**"]
+    if item.get("status") == "analysis":
+        stated = "; ".join(_markdown_safe(a) for a in as_list(item.get("key_assumptions")) if clean_text(a))
+        lines.append("*Analysis: the facts are sourced and every reasoning step follows from them except one, "
+                     "stated here as the assumption:* " + stated)
     if clean_text(item.get("_rating")):
         lines.append(f"*You rated this {clean_text(item.get('_rating'))}.*")
     lines.append(f"- **What the sources establish** ({tier.coverage(item)}):")
@@ -4147,6 +4168,7 @@
     if unresolved:
         lines.append("- **Not established by the sources:**")
         lines += ["  - " + _markdown_safe(point) for point in unresolved[:4]]
+    lines += earlier_coverage_lines(item)
     for key, label in (("falsifier", "Would be wrong if"), ("watch_signal", "Watch for")):
         if clean_text(item.get(key)):
             lines.append(f"- **{label}:** {_markdown_safe(item.get(key))}")
@@ -4181,6 +4203,27 @@
                 st.markdown("\n".join(extra))
 
 
+def render_lead_inference(report: Mapping[str, Any], sections: Mapping[str, Any]) -> None:
+    """The report always opens with its strongest inference, under its own label (never relabelled)."""
+    from services import inference_summary
+    ratings: dict[str, str] = {}
+    try:
+        from services import insight_ratings
+        report_id = clean_text(report.get("report_id"))
+        ratings = {r["item_key"]: r["rating"] for r in insight_ratings.load() if r.get("report_id") == report_id}
+    except Exception:
+        ratings = {}
+    lead = inference_summary.lead(sections, ratings)
+    st.subheader("Main inference this run")
+    if not lead:
+        st.warning("This report contains no inference beyond the news itself.")
+        return
+    st.caption(lead["label"])
+    st.markdown("**" + _markdown_safe(lead["text"]) + "**")
+    if lead["kind"] == "hypothesis":
+        st.caption("Details are under \"Hypotheses to investigate\" below.")
+
+
 def _investigation_items(report: Mapping[str, Any], sections: Mapping[str, Any]) -> list[dict[str, Any]]:
     from services import hypothesis_tier as tier
     ratings: dict[str, str] = {}
@@ -4206,6 +4249,7 @@
     separate "main finding".
     """
     render_profile_note(sections)
+    render_lead_inference(report, sections)
     render_insight_ranking(sections)
     investigate = _investigation_items(report, sections)
     render_strategic_hypotheses(sections, exclude_ids=[clean_text(i.get("id")) for i in investigate])
--- a/services/hypothesis_tier.py
+++ b/services/hypothesis_tier.py
@@ -6,8 +6,8 @@
 reasoning step and what would disprove it.
 
 An item qualifies when:
-- its thesis "follows from" or is "partly backed by" the sources (inference_supported or
-  partially_supported);
+- its thesis is stated by, follows from, or is partly backed by the sources (supported,
+  inference_supported or partially_supported);
 - nothing in it is contradicted by a source, so a contradiction is never shown as a premise;
 - at least one source quote supports part of it.
 
@@ -18,11 +18,14 @@
 
 from __future__ import annotations
 
+import re
 from typing import Any, Mapping, Sequence
 
-VERSION = "hypothesis-tier-v1"
+VERSION = "hypothesis-tier-v1.1-supported-thesis"
 MAX_SHOWN = 3
-QUALIFYING_THESIS = frozenset({"inference_supported", "partially_supported"})
+# A thesis the sources fully state also qualifies when its item failed verification elsewhere
+# (RUN-3E2FE: EVOLVED_H1, thesis "supported", mechanism partly backed, was hidden).
+QUALIFYING_THESIS = frozenset({"supported", "inference_supported", "partially_supported"})
 PASSED = frozenset({"supported", "inference_supported"})
 RATING_ORDER = {"useful": 0, "": 1, "meh": 2, "poor": 3}
 FIELD_LABELS = {"LEVERAGE": "Leverage", "CONTROL": "Control point", "DEPENDENCY": "Dependency",
@@ -73,14 +76,20 @@
             and not contradicted(item) and bool(premises(item)))
 
 
+def _is_link(claim: Mapping[str, Any]) -> bool:
+    """A reasoning link of a typed mechanism (…-MECH-L2): an assumption when unbacked, not a fact."""
+    return bool(re.search(r"-MECH-L\d+$", _clean(claim.get("claim_id"))))
+
+
 def open_points(item: Mapping[str, Any]) -> list[str]:
-    """Checked parts the sources do not establish, in plain words."""
+    """Checked facts and fields the sources do not establish, in plain words."""
     out = []
     for claim in _claims(item):
-        suffix = _clean(claim.get("claim_id")).rsplit("-", 1)[-1]
-        if suffix in {"THESIS", "MECH"} or _clean(claim.get("grounding_status")) in PASSED:
+        claim_id = _clean(claim.get("claim_id"))
+        suffix = claim_id.rsplit("-", 1)[-1]
+        if suffix in {"THESIS", "MECH"} or _is_link(claim) or _clean(claim.get("grounding_status")) in PASSED:
             continue
-        label = FIELD_LABELS.get(suffix, suffix.title())
+        label = "Starting fact of the reasoning" if claim_id.endswith("-MECH-S1") else FIELD_LABELS.get(suffix, suffix.title())
         out.append(f"{label}: {_clean(claim.get('claim_text'))}")
     for field, ids in _map(item.get("unresolved_fact_ids")).items():
         out.append(f"{FIELD_LABELS.get('MOVEBASIS' if field == 'actor_move_basis' else 'COUNTERBASIS')}: "
@@ -90,9 +99,11 @@
 
 def assumptions(item: Mapping[str, Any]) -> list[str]:
     """What the reasoning adds that no source states (the thesis and mechanism checks)."""
-    out = []
+    out = [_clean(x) for x in _list(item.get("key_assumptions")) if _clean(x)]   # analysis: the one stated link
     for claim in _claims(item):
-        if _clean(claim.get("claim_id")).rsplit("-", 1)[-1] in {"THESIS", "MECH"}:
+        if _is_link(claim) and _clean(claim.get("grounding_status")) not in PASSED:
+            out.append(_clean(claim.get("claim_text")))
+        elif _clean(claim.get("claim_id")).rsplit("-", 1)[-1] in {"THESIS", "MECH"}:
             out += [_clean(x) for x in _list(claim.get("unsupported_parts")) if _clean(x)
                     and not _clean(x).startswith("The claim requires multiple supporting evidence records")]
     return list(dict.fromkeys(out))
@@ -104,9 +115,10 @@
     ratings = ratings or {}
     lab = _map(sections.get("strategic_hypotheses"))
     pool = [h for h in _list(lab.get("promising_unverified")) if isinstance(h, Mapping) and qualifies(h)]
+    # Your rating first; then analysis (sourced facts, one stated assumption) before other near misses.
     ranked = sorted(enumerate(pool), key=lambda pair: (
         RATING_ORDER.get(_clean(ratings.get(f"lab:{_clean(pair[1].get('id'))}")), 1),
-        -len(premises(pair[1])), pair[0]))
+        pair[1].get("status") != "analysis", -len(premises(pair[1])), pair[0]))
     return [dict(item, _rating=_clean(ratings.get(f"lab:{_clean(item.get('id'))}"))) for _, item in ranked[:MAX_SHOWN]]
 
 
--- a/services/insight_funnel.py
+++ b/services/insight_funnel.py
@@ -69,6 +69,10 @@
                                + (f"; rewritten from {_clean(h.get('critic_inherited_from'))}, unscored"
                                   if h.get("critic_inherited_from") else "")))
     for h in unverified:
+        if h.get("status") == "analysis":   # sourced facts; one reasoning link shown as its assumption
+            candidates.append(_row(h.get("id"), h.get("thesis"), "passed",
+                                   "analysis; assumption: " + "; ".join(_clean(a) for a in _list(h.get("key_assumptions")))))
+            continue
         grounding = _map(h.get("grounding"))
         failed = {k: v for k, v in _map(h.get("field_checks")).items()
                   if v not in {"supported", "inference_supported", "not_checked"}}
--- a/services/strategic_hypothesis_lab.py
+++ b/services/strategic_hypothesis_lab.py
@@ -163,6 +163,10 @@
             "text": text[:900],
             "citations": citations,
             "source_ids": _unique(explicit_source_ids + [c.get("source_id") for c in citations]),
+            # Memory pass: coverage from earlier runs keeps its dates into the prompt.
+            "earlier": _clean(fact.get("gap_type")) == "earlier",
+            "date": _clean(fact.get("date") or fact.get("published_at"))[:10],
+            "occurred_on": _clean(fact.get("occurred_on"))[:10],
         })
         if len(out) >= limit:
             break
@@ -259,9 +263,53 @@
     return premises
 
 
+def dedupe_facts(facts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
+    """The same sentence from several sources is one fact (RUN-3E2FE: F6=F8, F10=F12, F14=F16),
+    keeping every source's evidence id with it. IDs are not renumbered, so citations stay valid."""
+    out: list[dict[str, Any]] = []
+    by_text: dict[str, dict[str, Any]] = {}
+    for fact in facts or []:
+        if not isinstance(fact, Mapping):
+            continue
+        key = re.sub(r"\W+", " ", _clean(fact.get("text")).casefold()).strip()
+        if key and key in by_text:
+            kept = by_text[key]
+            extra = _walk_values(fact, {"evidence_id", "evidence_ids"})
+            kept["evidence_ids"] = _unique(list(kept.get("evidence_ids") or []) + extra)
+            continue
+        row = dict(fact)
+        row["evidence_ids"] = _unique(list(row.get("evidence_ids") or [])
+                                      + _walk_values(fact, {"evidence_id", "evidence_ids"}))
+        out.append(row)
+        if key:
+            by_text[key] = row
+    return out
+
+
+PROMPT_FACT_LIMIT = 40
+EARLIER_RULE = (
+    "EARLIER COVERAGE (from previous runs; each is dated). Use these only for 'this happened before' "
+    "reasoning - a pattern, a repeat by the same actor, or a comparable case - and name the date in "
+    "the claim. They are never evidence that this run's events happened, and an earlier announcement "
+    "is not an outcome unless the fact states what followed:")
+
+
+def earlier_label(row: Mapping[str, Any]) -> str:
+    when = [f"occurred {row['occurred_on']}"] if row.get("occurred_on") else []
+    when += [f"reported {row['date']}"] if row.get("date") else []
+    return f"[{row['fact_id']}] ({'; '.join(when) or 'date not recorded'}) {row['text']}"
+
+
 def _facts_text(facts: Sequence[Mapping[str, Any]]) -> str:
-    rows = fact_rows(facts)
-    return "\n".join(f"[{r['fact_id']}] {r['text']}" for r in rows)
+    """Current facts (capped at PROMPT_FACT_LIMIT), then earlier coverage in its own dated
+    block, which the cap never cuts (memory pass)."""
+    rows = fact_rows(facts, limit=250)
+    current = [r for r in rows if not r.get("earlier")][:PROMPT_FACT_LIMIT]
+    earlier = [r for r in rows if r.get("earlier")]
+    text = "\n".join(f"[{r['fact_id']}] {r['text']}" for r in current)
+    if earlier:
+        text += "\n\n" + EARLIER_RULE + "\n" + "\n".join(earlier_label(r) for r in earlier)
+    return text
 
 
 # v1.4 (resource-dependence theory, Pfeffer & Salancik): leverage needs dependence, few alternatives
@@ -328,6 +376,12 @@
     return all(_clean(lev.get(k)) for k in LEVERAGE_KEYS)
 
 
+# Typed mechanism steps (26 Oct 2026): the first step is checked as a fact and each later step only
+# has to follow from the facts; exactly one unbacked later step is shown as the stated assumption.
+MECHANISM_RULE = ("Start the chain with an observation that a cited fact states (step A); put any general "
+                  "knowledge or judgment in a later step, never in the first. Each step is checked on its own.")
+
+
 def generation_prompt(topic: str, facts: Sequence[Mapping[str, Any]], lenses: Sequence[str], *, batch: int,
                       profile: str = "") -> str:
     lens_text = "\n".join(f"- {lens}" for lens in lenses)
@@ -357,7 +411,7 @@
 - novel_proposition: the part a knowledgeable reader probably would not infer from one source
 - why_not_obvious
 - anomaly_or_trigger: what observation needs explaining
-- mechanism: an explicit A -> B -> C causal/strategic chain
+- mechanism: an explicit A -> B -> C causal/strategic chain. {MECHANISM_RULE}
 - fact_ids: 2-6 fact IDs that anchor the thesis (one is allowed only for a genuine internal contradiction)
 - leverage: {{holder,counterparty,dependency,control_point,outside_option,holder_dependence}}; use empty strings if not a leverage thesis.
   {LEVERAGE_TEST}
@@ -499,6 +553,7 @@
 
 A 9/10 candidate should reveal an overlooked constraint/control point/incentive or explain a surprising pattern, have a defensible
 mechanism, tell us who gains/loses or what changes next, and be falsifiable. A generic recommendation is not a candidate.
+Mechanism format: {MECHANISM_RULE}
 
 FACTS:
 {_facts_text(facts)}
@@ -655,14 +710,34 @@
     claims = [
         {"claim_id": base + "-THESIS", "claim_text": _clean(item.get("thesis")),
          "epistemic_type": "hidden_insight", "evidence_ids": ids, "multi_evidence_required": len(ids) >= 2},
-        {"claim_id": base + "-MECH", "claim_text": _clean(item.get("mechanism")),
-         "epistemic_type": "mechanism", "evidence_ids": ids, "multi_evidence_required": len(ids) >= 2},
     ]
+    steps = mechanism_steps(item.get("mechanism"))
+    if len(steps) >= 2:
+        # Typed steps (26 Oct 2026): the starting observation is a fact and must be stated by a
+        # source; each link only has to follow from the sourced facts. Graded as one claim, one
+        # unsourced background step failed the whole chain (EV3, RUN-3BF777).
+        claims.append({"claim_id": base + "-MECH-S1", "claim_text": steps[0], "epistemic_type": "fact",
+                       "evidence_ids": ids, "multi_evidence_required": False})
+        for n in range(1, len(steps)):
+            claims.append({"claim_id": f"{base}-MECH-L{n + 1}",
+                           "claim_text": f"Given that {steps[n - 1]}, {steps[n]}",
+                           "epistemic_type": "mechanism", "evidence_ids": ids, "multi_evidence_required": False})
+    else:
+        claims.append({"claim_id": base + "-MECH", "claim_text": _clean(item.get("mechanism")),
+                       "epistemic_type": "mechanism", "evidence_ids": ids, "multi_evidence_required": len(ids) >= 2})
     lev = item.get("leverage") if isinstance(item.get("leverage"), Mapping) else {}
     sentence = leverage_sentence(lev)
-    if sentence:   # who controls what, and who depends on it: a stated position, so held to the fact bar
-        claims.append({"claim_id": base + "-LEVERAGE", "claim_text": sentence, "epistemic_type": "fact",
-                       "evidence_ids": ids, "multi_evidence_required": False})
+    # GPT lab-dependency patch, merged 26 Oct 2026: control is a factual premise; dependency is a
+    # separate inference. A sourced control point or an absence of contradiction never establishes it.
+    holder, counterparty = _clean(lev.get("holder")), _clean(lev.get("counterparty"))
+    control, dependency = _clean(lev.get("control_point")), _clean(lev.get("dependency"))
+    if holder and control:
+        claims.append({"claim_id": base + "-CONTROL", "claim_text": f"{holder} controls {control}.",
+                       "epistemic_type": "fact", "evidence_ids": ids, "multi_evidence_required": False})
+    if holder and counterparty and control and dependency:
+        claims.append({"claim_id": base + "-DEPENDENCY",
+                       "claim_text": f"{counterparty} depends on {holder}'s {control} for {dependency}.",
+                       "epistemic_type": "connection", "evidence_ids": ids, "multi_evidence_required": False})
     from services.leverage_validation import alternatives_known
     if sentence and alternatives_known(lev.get("outside_option")):
         claims.append({"claim_id": base + "-ALTERNATIVES", "evidence_ids": ids, "multi_evidence_required": False,
@@ -728,6 +803,43 @@
     return blockers
 
 
+def mechanism_steps(mechanism: Any) -> list[str]:
+    """'A -> B -> C' as its steps (also accepts → and =>)."""
+    return [s for s in (_clean(x) for x in re.split(r"\s*(?:->|→|=>)\s*", _clean(mechanism))) if s]
+
+
+PASSING = frozenset({"supported", "inference_supported"})
+ASSUMPTION_BRIDGED = "assumption_bridged"   # every step backed except one stated link
+
+
+def mechanism_grade(grounded_claims: Sequence[Mapping[str, Any]]) -> tuple[str, list[str]]:
+    """Mechanism status from its typed steps, and the one unbacked link if there is exactly one.
+
+    contradicted         any step contradicted by a source
+    supported/inference  the starting fact is backed and every link follows
+    assumption_bridged   the starting fact is backed and exactly ONE link is unbacked: shown as
+                         analysis with that link as its stated assumption (ICD 203 key assumption)
+    partially_supported / unsupported otherwise. Legacy single -MECH claims grade as before."""
+    rows = {_clean(c.get("claim_id")): c for c in grounded_claims or [] if isinstance(c, Mapping)}
+    legacy = next((c for k, c in rows.items() if k.endswith("-MECH")), None)
+    if legacy is not None:
+        return _clean(legacy.get("grounding_status")) or "unsupported", []
+    start = next((c for k, c in rows.items() if k.endswith("-MECH-S1")), None)
+    links = [c for k, c in sorted(rows.items()) if re.search(r"-MECH-L\d+$", k)]
+    if start is None:
+        return "unsupported", []
+    statuses = [_clean(start.get("grounding_status"))] + [_clean(c.get("grounding_status")) for c in links]
+    if "contradicted" in statuses:
+        return "contradicted", []
+    unbacked = [c for c in links if _clean(c.get("grounding_status")) not in PASSING]
+    start_backed = _clean(start.get("grounding_status")) in PASSING
+    if start_backed and not unbacked:
+        return ("supported" if all(s == "supported" for s in statuses) else "inference_supported"), []
+    if start_backed and len(unbacked) == 1 and links:
+        return ASSUMPTION_BRIDGED, [_clean(unbacked[0].get("claim_text"))]
+    return ("partially_supported" if any(s in PASSING for s in statuses) else "unsupported"), []
+
+
 def finalize(item: Mapping[str, Any], grounded_claims: Sequence[Mapping[str, Any]], facts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
     statuses = {_clean(c.get("claim_id")): _clean(c.get("grounding_status")) for c in grounded_claims or []}
     support_ids = _unique([
@@ -735,13 +847,16 @@
         for eid in (claim.get("supporting_evidence_ids") or [])
     ])
     thesis_status = next((v for k, v in statuses.items() if k.endswith("-THESIS")), "unsupported")
-    mechanism_status = next((v for k, v in statuses.items() if k.endswith("-MECH")), "unsupported")
+    mechanism_status, key_assumptions = mechanism_grade(grounded_claims)
     blockers = promotion_blockers(item, thesis_status, mechanism_status)
     verified = (
-        thesis_status in {"supported", "inference_supported"}
-        and mechanism_status in {"supported", "inference_supported"}
+        thesis_status in PASSING
+        and mechanism_status in PASSING
         and not blockers
     )
+    # Analysis: the thesis follows from the sources and the chain holds except one stated link.
+    analysis = (not verified and thesis_status in PASSING and mechanism_status == ASSUMPTION_BRIDGED
+                and not blockers)
     field_checks = {}
     lev = item.get("leverage") if isinstance(item.get("leverage"), Mapping) else {}
     for suffix, field in (("-LEVERAGE", "leverage"), ("-ALTERNATIVES", "leverage_alternatives"),
@@ -755,6 +870,23 @@
             field_checks[field] = status or "not_checked"
     for field in (item.get("unresolved_fact_ids") or {}):   # cited an unknown fact ID: not checkable
         field_checks[field] = "not_checked"
+    # GPT lab-dependency patch (merged): fresh split checks supersede a legacy combined check.
+    # Missing components fail closed; inference_supported is valid for dependency, never for control.
+    split = any(k.endswith(("-CONTROL", "-DEPENDENCY")) for k in statuses)
+    if leverage_sentence(lev):
+        control = next((v for k, v in statuses.items() if k.endswith("-CONTROL")), "not_checked")
+        dependency = next((v for k, v in statuses.items() if k.endswith("-DEPENDENCY")), "not_checked")
+        field_checks["leverage_control"] = control
+        field_checks["leverage_dependency"] = dependency
+        if split:
+            if "contradicted" in (control, dependency):
+                field_checks["leverage"] = "contradicted"
+            elif control == "supported" and dependency in PASSING:
+                field_checks["leverage"] = dependency
+            else:
+                field_checks["leverage"] = "unsupported"
+        elif not all(_clean(lev.get(k)) for k in ("holder", "counterparty", "control_point", "dependency")):
+            field_checks["leverage"] = "not_checked"
     from services.leverage_validation import result as leverage_result
     # Unknown alternatives must never become evidence of limited alternatives.
     if leverage_sentence(lev):
@@ -765,8 +897,9 @@
     result.update({
         "field_checks": field_checks,
         "leverage_test": leverage_test,
-        "status": "verified" if verified else "promising_unverified",
+        "status": "verified" if verified else "analysis" if analysis else "promising_unverified",
         "grounding": {"thesis": thesis_status, "mechanism": mechanism_status},
+        "key_assumptions": key_assumptions if analysis else [],
         "supporting_evidence_ids": support_ids,
         "evidence_ids": _unique(item.get("resolved_evidence_ids") or evidence_ids_for_fact_ids(facts, item.get("fact_ids") or [])),
         "promotion_blockers": blockers,
@@ -792,7 +925,7 @@
 
 
 def needs_narrowing(final: Mapping[str, Any]) -> bool:
-    if final.get("status") == "verified" or final.get("narrowed_from"):
+    if final.get("status") in {"verified", "analysis"} or final.get("narrowed_from"):
         return False
     grounding = final.get("grounding") if isinstance(final.get("grounding"), Mapping) else {}
     if "contradicted" in {_clean(grounding.get("thesis")), _clean(grounding.get("mechanism"))}:
@@ -827,7 +960,8 @@
         "3. Remove legal-loophole or naming arguments unless a fact quotes the statute.\n"
         "4. Cite only the fact IDs listed; add no new facts, numbers or actors.\n"
         "5. A move or effect that does not follow from these facts is dropped, not softened into 'may'.\n"
-        "6. If nothing strategic survives, return {\"drop\": true}.\n\n"
+        "6. If nothing strategic survives, return {\"drop\": true}.\n"
+        "7. Mechanism format: " + MECHANISM_RULE + "\n\n"
         f"ORIGINAL THESIS: {_clean(final.get('thesis'))}\n"
         f"ORIGINAL MECHANISM: {_clean(final.get('mechanism'))}\n"
         f"LEVERAGE: holder={_clean(lev.get('holder'))}; counterparty={_clean(lev.get('counterparty'))}; "
@@ -838,7 +972,8 @@
         + MOVE_BASIS_RULE + "\n"
         + (f"WORDS THAT BLOCKED IT: {', '.join(terms)}\n" if terms else "")
         + ("WHAT FAILED:\n" + "\n".join(problems) + "\n" if problems else "")
-        + "FACTS:\n" + "\n".join(f"[{r['fact_id']}] {r['text']}" for r in rows) +
+        + "FACTS:\n" + "\n".join(earlier_label(r) if r.get("earlier") else f"[{r['fact_id']}] {r['text']}"
+                                 for r in rows) +
         '\n\nRewrite EVERY field below for the narrower claim (an empty string if it no longer follows); '
         'do not carry over wording from the original.\n'
         'Return JSON: {"thesis":"...","mechanism":"A -> B -> C","novel_proposition":"...","why_not_obvious":"...",'
--- a/services/hypothesis_evidence_service.py
+++ b/services/hypothesis_evidence_service.py
@@ -52,6 +52,30 @@
 
 def claim_fingerprint(candidate: Mapping[str, Any]) -> str:
     return digest({k: candidate.get(k) for k in ("insight", "insight_type", "premises", "mechanism")})
+
+
+_MONTHS = ("january february march april may june july august september october november december").split()
+_TEXT_DATE = re.compile(
+    r"\b(?:(?P<m1>[A-Za-z]{3,9})\.?\s+(?P<d1>\d{1,2}),?\s+(?P<y1>20\d\d)"   # October 22, 2026
+    r"|(?P<d2>\d{1,2})\s+(?P<m2>[A-Za-z]{3,9})\.?,?\s+(?P<y2>20\d\d)"        # 22 October 2026
+    r"|(?P<y3>20\d\d)-(?P<m3>\d{2})-(?P<d3>\d{2}))\b")                      # 2026-10-22
+
+
+def page_text_date(body: Any, head_chars: int = 1500) -> str:
+    """The first full calendar date printed near the top of an article (ISO), or ""."""
+    for match in _TEXT_DATE.finditer(str(body or "")[:head_chars]):
+        g = match.groupdict()
+        try:
+            if g["y3"]:
+                year, month, day = int(g["y3"]), int(g["m3"]), int(g["d3"])
+            else:
+                name = (g["m1"] or g["m2"]).casefold()
+                month = next(n for n, m in enumerate(_MONTHS, 1) if m.startswith(name[:3]) and len(name) >= 3)
+                year, day = int(g["y1"] or g["y2"]), int(g["d1"] or g["d2"])
+            return datetime(year, month, day, tzinfo=timezone.utc).isoformat()
+        except (StopIteration, ValueError):
+            continue
+    return ""
 
 
 def parse_date(value: Any) -> datetime | None:
@@ -388,7 +412,8 @@
                         candidate: Mapping[str, Any], now: datetime, timeout: float,
                         allow_undated_in_force: bool = False,
                         enforce_window: bool = True,
-                        allow_undated_historical: bool = False) -> dict[str, Any]:
+                        allow_undated_historical: bool = False,
+                        date_from_text: bool = False) -> dict[str, Any]:
         meta = source.get("metadata") or {}
         result = context.get("search_result") or {}
         url = clean(source.get("canonical_url") or source.get("url") or result.get("url"))
@@ -405,6 +430,14 @@
             raise ValueError("Document exceeds research context budget; review incomplete")
         published = clean(source.get("published_at") or meta.get("published_at") or result.get("published_at"))
         published_date = parse_date(published)
+        date_basis = ""
+        if published_date is None and date_from_text:
+            # Expert commentary pages (think tanks, law firms) often carry no date tag: RUN-3E2FE
+            # dropped CFR's and Brookings' analyses of the AI Accord for that alone. Use the first
+            # full date printed near the top of the article; the window checks below still apply.
+            published = page_text_date(body)
+            published_date = parse_date(published)
+            date_basis = "date_printed_in_page_text" if published_date else ""
         # v6.19: an official policy page often has no publication-date tag. For
         # policy gap research only, such a page may be reviewed, but each record
         # must then quote the page's own in-force / effective statement with a
@@ -528,7 +561,7 @@
                 "published_at": published,
                 "publication_date_basis": ("in_force_statement" if undated_policy
                                            else "historical_case_date_quote" if undated_historical
-                                           else "fetched_source_or_search_metadata"),
+                                           else date_basis or "fetched_source_or_search_metadata"),
                 "in_force_quote": in_force_quote or None,
                 "claim_status": "reported_claim", "temporal_status": "future" if kind == "forecast" else "unknown",
                 "admission_status": "admitted", "source_independence_status": "unknown",
@@ -729,7 +762,8 @@
                                               timeout=min(timeout_seconds, local_deadline - time.monotonic()),
                                               allow_undated_in_force=(gap == "policy"),
                                               enforce_window=(gap != "precedent"),
-                                              allow_undated_historical=(gap == "precedent"))
+                                              allow_undated_historical=(gap == "precedent"),
+                                              date_from_text=(gap == "commentary"))
             except Exception as error:
                 review_log.append({"gap": gap, "url": url[:120], "outcome": "not_reviewed",
                                    "reason": f"{type(error).__name__}: {clean(error)[:140]}"})
--- /dev/null
+++ b/services/memory_pass.py
@@ -0,0 +1,350 @@
+"""Memory pass: earlier events as dated facts for the hypothesis lab (26 Oct 2026).
+
+docs/DESIGN_memory_pass.md, revised for GPT's review. Retrieval is local and read-only (no model
+call, nothing written, a missing store is skipped, never created). Safeguards:
+
+1. Never corroboration: records carry gap_type "earlier" / evidence_role "earlier_coverage";
+   _convergence_maps ignores them, so they add no event and no publisher to convergence.
+2. Dates reach the prompt: strategic_hypothesis_lab renders earlier facts in their own dated
+   block that the 40-fact cap never cuts.
+3. Three dates, kept apart: occurred_on (the event), published_on (the source), available_since
+   (when this app first stored it). A record is used only if it was published AND stored before
+   the run's cut-off. For a replay the cut-off is the original checkpoint time, so a replay never
+   sees anything collected later.
+4. Earlier coverage is not a precedent: records are labelled "earlier_coverage"; an outcome
+   analogy still needs a dated case with an observed outcome (editorial_briefing.select_cases).
+5. Stored excerpts are checked: a database event is used only if its quoted snippet appears
+   verbatim in the stored article text; memory excerpts are the sources' own words already.
+   Model conclusions are never retrieved - only source excerpts.
+6. One retrieval rule, two lanes: "same_actor" (a shared named actor and a shared mechanism or
+   subject words) and "mechanism" (a shared mechanism, any actor, plus shared subject words). A
+   match on actor alone, or on one generic word, is not enough.
+7. Deduplicated by occurrence: one row per memory underlying event; a database event whose
+   snippet repeats a memory excerpt (or another row) is dropped.
+8. Frozen for comparisons: the selection (ids, content hashes, cut-off, version) is returned and
+   stored with the report.
+9. Off with EVENT_INTELLIGENCE_MEMORY_PASS=0.
+"""
+
+from __future__ import annotations
+
+import hashlib
+import json
+import os
+import re
+import sqlite3
+from contextlib import closing
+from datetime import datetime, timezone
+from pathlib import Path
+from typing import Any, Iterable, Mapping, Sequence
+
+VERSION = "memory-pass-v1"
+ENV = "EVENT_INTELLIGENCE_MEMORY_PASS"
+MAX_PER_DEVELOPMENT = 3
+MAX_TOTAL = 8
+MIN_SHARED_WORDS = 2
+ROOT = Path(__file__).resolve().parents[1]
+
+# Mechanisms, matched on words in titles/excerpts and on the database's event types. Deliberately
+# about what happened (a fine, a breach, a pledge), not the topic, so "rename" never matches
+# "rename" unless the mechanism does (the off-target precedent problem).
+MECHANISMS: dict[str, tuple[str, ...]] = {
+    "breach_or_incident": ("breach", "breached", "hack", "hacked", "unauthori", "leak", "leaked", "incident",
+                           "intrusion", "compromised"),
+    "disclosure_or_reporting": ("disclos", "notif", "reporting rule", "alerted"),
+    "fine_or_penalty": ("fine", "fined", "fines", "penalt", "sanction", "sanctions", "turnover"),
+    "regulation_or_law": ("regulat", "law", "laws", "bill", "bills", "statute", "rule", "rules",
+                          "mandate", "compliance"),
+    "voluntary_pledge": ("pledge", "pledges", "accord", "voluntary", "commitment", "commitments",
+                         "code of conduct", "self-regulat", "pact"),
+    "government_partnership": ("for countries", "government partnership", "sovereign", "public sector",
+                               "ministry"),
+    "funding_round": ("raise", "raised", "raises", "funding", "valuation", "series", "venture", "investor",
+                      "investors"),
+    "acquisition": ("acqui", "merger", "takeover", "buyout"),
+    "partnership": ("partner", "partners", "partnership", "alliance", "collaborat", "joint"),
+    "research_grant": ("grant", "grants", "scientist", "scientists", "academic", "mathematicians", "researchers"),
+    "ban_or_restriction": ("ban", "bans", "banned", "restrict", "restricted", "restrictions", "prohibit",
+                           "export control", "blacklist"),
+    "lawsuit": ("lawsuit", "sued", "court", "litigation", "settlement"),
+    "safety_testing": ("safety", "audit", "audits", "red team", "evaluation", "security institute"),
+}
+EVENT_TYPE_MECHANISM = {
+    "investment_or_funding": "funding_round", "fundraising": "funding_round",
+    "acquisition_or_merger": "acquisition", "partnership_or_agreement": "partnership",
+    "regulation_or_policy_action": "regulation_or_law", "legal_action": "lawsuit",
+}
+
+
+def enabled() -> bool:
+    return os.getenv(ENV, "1") != "0"
+
+
+def _clean(value: Any) -> str:
+    return " ".join(str(value or "").split())
+
+
+def _words(text: str) -> set[str]:
+    from services.event_memory_service import _words as words
+    return words(text)
+
+
+def _norm_actor(actor: str) -> str:
+    try:
+        from services.event_memory_service import _norm_actor as norm
+        return norm(actor)
+    except Exception:
+        return _clean(actor).casefold()
+
+
+# Word stems match as prefixes ("regulat" -> regulation); every other cue matches whole words only.
+STEMS = frozenset({"disclos", "notif", "penalt", "regulat", "self-regulat", "acqui", "collaborat",
+                   "prohibit", "unauthori"})
+_CUE_PATTERNS = {name: re.compile("|".join(r"\b" + re.escape(cue.strip()) + ("" if cue.strip() in STEMS else r"\b")
+                                           for cue in cues))
+                 for name, cues in MECHANISMS.items()}
+SAME_OCCURRENCE_OVERLAP = 0.35   # this much shared wording with one current fact = the same news
+MIN_SUBJECT_WORDS_MECHANISM_LANE = 3
+CUE_WORDS = frozenset(w for cues in MECHANISMS.values() for cue in cues for w in re.findall(r"[a-z]{4,}", cue))
+GENERIC_WORDS = frozenset("""million billion company companies year years month months today week said says
+announced percent capital round total including according first new bringing co-led rise later led
+will would could help make made model models launched launch raising community based provide provides
+plans plan also more most other within""".split())
+
+
+def mechanisms(text: str, event_type: str = "") -> set[str]:
+    """Cues match at word starts only ("bill" never matches "billion")."""
+    low = _clean(text).casefold()
+    found = {name for name, pattern in _CUE_PATTERNS.items() if pattern.search(low)}
+    if EVENT_TYPE_MECHANISM.get(_clean(event_type)):
+        found.add(EVENT_TYPE_MECHANISM[_clean(event_type)])
+    return found
+
+
+def _when(value: Any) -> datetime | None:
+    text = _clean(value)
+    if not text:
+        return None
+    try:
+        parsed = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1))
+    except ValueError:
+        try:
+            parsed = datetime.fromisoformat(text[:10])
+        except ValueError:
+            return None
+    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
+
+
+def _sha(text: str) -> str:
+    return hashlib.sha256(_clean(text).encode("utf-8")).hexdigest()
+
+
+def _connect(path: Path):
+    """Read-only; a missing store is skipped, never created."""
+    if not path.is_file():
+        return None
+    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
+
+
+def memory_path() -> Path:
+    configured = _clean(os.getenv("EVENT_INTELLIGENCE_EVENT_MEMORY"))
+    return Path(configured) if configured else ROOT / "data" / "event_memory.sqlite3"
+
+
+def database_path() -> Path:
+    url = _clean(os.getenv("EVENT_INTELLIGENCE_DATABASE_URL") or os.getenv("DATABASE_URL"))
+    if url.startswith("sqlite:///"):
+        path = Path(url[len("sqlite:///"):])
+        return path if path.is_absolute() else ROOT / path
+    return ROOT / "data" / "event_intelligence.db"
+
+
+def cutoff(state: Mapping[str, Any], now: datetime | None = None) -> datetime:
+    """The replay's original checkpoint time, else now. Nothing stored later is used."""
+    replay = state.get("replayed_from") if isinstance(state.get("replayed_from"), Mapping) else {}
+    return _when(replay.get("saved_at")) or _when(state.get("memory_as_of")) or now or datetime.now(timezone.utc)
+
+
+def _target(development: Mapping[str, Any]) -> dict[str, Any]:
+    fact_texts = [_clean(f.get("text")) for f in development.get("facts") or [] if isinstance(f, Mapping)]
+    text = f"{_clean(development.get('title'))} {' '.join(fact_texts)}"
+    return {"title": _clean(development.get("title")), "words": _words(text),
+            "fact_words": [w for w in (_words(t) for t in fact_texts) if w],
+            "actors": {_norm_actor(a) for a in development.get("actors") or [] if _clean(a)} - {""},
+            "mechanisms": mechanisms(text)}
+
+
+def _match(target: Mapping[str, Any], *, words: set[str], actors: set[str], mechs: set[str]) -> dict[str, Any] | None:
+    shared_words = target["words"] & words
+    for fact in target.get("fact_words") or []:
+        if len(fact & words) / len(fact | words) >= SAME_OCCURRENCE_OVERLAP:
+            return None   # the same news as one current fact (another outlet), not earlier coverage
+    shared_actors = target["actors"] & actors
+    shared_mechs = target["mechanisms"] & mechs
+    # Subject words, not mechanism words: "raised" and "million" make every funding story alike.
+    subject = {w for w in shared_words if w not in CUE_WORDS and w not in GENERIC_WORDS}
+    if shared_actors and (shared_mechs or len(subject) >= MIN_SHARED_WORDS):
+        lane = "same_actor"
+    elif shared_mechs and len(subject) >= MIN_SUBJECT_WORDS_MECHANISM_LANE:
+        lane = "mechanism"
+    else:
+        return None
+    score = 3 * bool(shared_actors) + 2 * len(shared_mechs) + min(len(shared_words), 6) / 6
+    return {"lane": lane, "score": round(score, 3), "shared_actors": sorted(shared_actors),
+            "shared_mechanisms": sorted(shared_mechs), "shared_words": sorted(shared_words)[:8]}
+
+
+def _memory_candidates(cut: datetime, exclude_runs: set[str], exclude_underlying: set[str]) -> list[dict[str, Any]]:
+    db = _connect(memory_path())
+    if db is None:
+        return []
+    out = []
+    with closing(db):
+        rows = db.execute("SELECT underlying_event_id, title, actors, event_date, first_run_id, first_seen, words "
+                          "FROM events").fetchall()
+        for underlying, title, actors_json, event_date, first_run, first_seen, words_json in rows:
+            if underlying.upper() in exclude_underlying or first_run in exclude_runs:
+                continue
+            available = _when(first_seen)
+            if available is None or available >= cut:
+                continue
+            excerpts = db.execute("SELECT evidence_id, source_id, text, published_at FROM excerpts "
+                                  "WHERE underlying_event_id=? ORDER BY length(text) DESC", (underlying,)).fetchall()
+            for evidence_id, source_id, text, published in excerpts:
+                published_on = _when(published) or _when(event_date)
+                if published_on is None or published_on >= cut or len(_clean(text)) < 40:
+                    continue
+                actors = {_norm_actor(a) for a in json.loads(actors_json or "[]")} - {""}
+                out.append({"store": "event_memory", "occurrence": underlying.upper(),
+                            "evidence_id": "MEM-" + _clean(evidence_id), "original_evidence_id": _clean(evidence_id),
+                            "source_id": _clean(source_id), "text": _clean(text), "title": _clean(title),
+                            "occurred_on": _clean(event_date)[:10], "published_on": published_on.date().isoformat(),
+                            "available_since": available.date().isoformat(), "actors": actors,
+                            "words": {w for w in json.loads(words_json or "[]") if not w.startswith("anchor:")},
+                            "mechanisms": mechanisms(f"{title} {text}")})
+                break   # one excerpt per occurrence: the longest usable one
+    return out
+
+
+def _database_candidates(cut: datetime, exclude_sources: set[str]) -> list[dict[str, Any]]:
+    db = _connect(database_path())
+    if db is None:
+        return []
+    out = []
+    with closing(db):
+        rows = db.execute(
+            "SELECT e.event_id, e.source_id, e.title, e.event_type, e.event_start_at, e.entity_mentions, e.snippets, "
+            "e.created_at, a.content, a.published_at, a.url FROM events e JOIN articles a ON a.source_id = e.source_id "
+            "WHERE a.content IS NOT NULL AND a.published_at IS NOT NULL").fetchall()
+    for event_id, source_id, title, event_type, start, mentions, snippets, created, content, published, url in rows:
+        if _clean(source_id).upper() in exclude_sources:
+            continue
+        available, published_on = _when(created), _when(published)
+        if available is None or published_on is None or available >= cut or published_on >= cut:
+            continue
+        body = _clean(content)
+        try:
+            quotes = [_clean(s) for s in json.loads(snippets or "[]") if _clean(s)]
+        except (TypeError, ValueError):
+            quotes = []
+        quote = next((q for q in quotes if len(q) >= 40 and q in body), "")
+        if not quote:
+            continue   # safeguard 5: the excerpt must appear verbatim in the stored article
+        try:
+            actors = {_norm_actor(m.get("mention")) for m in json.loads(mentions or "[]")
+                      if isinstance(m, Mapping) and m.get("role") == "actor"} - {""}
+        except (TypeError, ValueError):
+            actors = set()
+        out.append({"store": "event_intelligence_db", "occurrence": _clean(event_id).upper(),
+                    "evidence_id": "DBEV-" + _sha(f"{source_id}|{quote}")[:24].upper(), "original_evidence_id": "",
+                    "source_id": _clean(source_id), "text": quote, "title": _clean(title), "source_url": _clean(url),
+                    "occurred_on": _clean(start)[:10], "published_on": published_on.date().isoformat(),
+                    "available_since": available.date().isoformat(), "actors": actors,
+                    "words": _words(f"{title} {quote}"), "mechanisms": mechanisms(f"{title} {quote}", event_type)})
+    return out
+
+
+def _record(candidate: Mapping[str, Any], match: Mapping[str, Any], development_title: str) -> dict[str, Any]:
+    return {
+        "evidence_id": candidate["evidence_id"], "original_evidence_id": candidate["original_evidence_id"],
+        "source_id": candidate["source_id"], "event_id": "", "evidence_type": "source_excerpt",
+        "evidence_role": "earlier_coverage", "gap_type": "earlier", "earlier_kind": "earlier_coverage",
+        "text": candidate["text"], "source_excerpt": candidate["text"], "source_url": candidate.get("source_url", ""),
+        "published_at": candidate["published_on"], "occurred_on": candidate["occurred_on"],
+        "available_since": candidate["available_since"], "earlier_event_title": candidate["title"],
+        "gap_for": development_title, "memory_store": candidate["store"], "memory_match": dict(match),
+        "analytical_context_only": True, "direct_event_evidence": False,
+        "prohibited_uses": ["observed_event_count", "acting_actor_count", "sample_base_rate",
+                            "current_event_corroboration"],
+        "content_sha256": _sha(candidate["text"]),
+    }
+
+
+def retrieve(developments: Sequence[Mapping[str, Any]], state: Mapping[str, Any], *,
+             now: datetime | None = None) -> dict[str, Any]:
+    """Selected earlier-coverage records and the selection metadata (never raises)."""
+    cut = cutoff(state, now)
+    meta: dict[str, Any] = {"version": VERSION, "enabled": enabled(), "cutoff": cut.isoformat(),
+                            "stores": {}, "selected": [], "errors": []}
+    if not meta["enabled"]:
+        return {"records": [], "meta": meta}
+    run_id = _clean(state.get("run_id"))
+    replay = state.get("replayed_from") if isinstance(state.get("replayed_from"), Mapping) else {}
+    exclude_runs = {x for x in (run_id, _clean(replay.get("run_id"))) if x}
+    exclude_underlying = {_clean(e.get("underlying_event_id")).upper()
+                          for e in state.get("events") or [] if isinstance(e, Mapping)} - {""}
+    # Every source this run selected, not only those that produced admitted evidence (RUN-3E2FE:
+    # a Simons Foundation article selected today came back as "earlier" coverage).
+    exclude_sources = ({_clean(e.get("source_id")).upper()
+                        for e in list(state.get("events") or []) + list(state.get("evidence") or [])
+                        if isinstance(e, Mapping)}
+                       | {_clean(x).upper() for x in list(state.get("source_ids") or [])
+                          + list(state.get("selected_source_ids") or [])}) - {""}
+    candidates: list[dict[str, Any]] = []
+    for name, loader in (("event_memory", lambda: _memory_candidates(cut, exclude_runs, exclude_underlying)),
+                         ("event_intelligence_db", lambda: _database_candidates(cut, exclude_sources))):
+        try:
+            rows = loader()
+            meta["stores"][name] = len(rows)
+            candidates += rows
+        except Exception as error:   # a broken store never stops the run
+            meta["errors"].append(f"{name}:{type(error).__name__}")
+    targets = [_target(d) for d in developments or [] if isinstance(d, Mapping) and _clean(d.get("title"))]
+    records: list[dict[str, Any]] = []
+    used_occurrences: set[str] = set()
+    used_texts: set[str] = set()
+    for target in targets:
+        scored = []
+        for candidate in candidates:
+            match = _match(target, words=candidate["words"], actors=candidate["actors"],
+                           mechs=candidate["mechanisms"])
+            if match:
+                scored.append((match["score"], candidate["published_on"], candidate, match))
+        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
+        taken = 0
+        for _, _, candidate, match in scored:
+            key = _sha(candidate["text"])
+            if candidate["occurrence"] in used_occurrences or key in used_texts:
+                continue   # safeguard 7: one record per occurrence and per passage
+            used_occurrences.add(candidate["occurrence"])
+            used_texts.add(key)
+            records.append(_record(candidate, match, target["title"]))
+            taken += 1
+            if taken >= MAX_PER_DEVELOPMENT or len(records) >= MAX_TOTAL:
+                break
+        if len(records) >= MAX_TOTAL:
+            break
+    meta["selected"] = [{"evidence_id": r["evidence_id"], "store": r["memory_store"], "lane": r["memory_match"]["lane"],
+                         "published_on": r["published_at"], "available_since": r["available_since"],
+                         "content_sha256": r["content_sha256"], "for": r["gap_for"][:120]} for r in records]
+    meta["selection_sha256"] = _sha(json.dumps(meta["selected"], sort_keys=True))
+    return {"records": records, "meta": meta}
+
+
+def as_facts(records: Iterable[Mapping[str, Any]], start: int = 1) -> list[dict[str, Any]]:
+    """Earlier-coverage records as lab facts M1, M2, ... (rendered in their own dated block)."""
+    return [{"fact_id": f"M{n}", "development": f"Earlier coverage: {_clean(r.get('earlier_event_title'))[:120]}",
+             "text": _clean(r.get("text"))[:600], "date": _clean(r.get("published_at"))[:10],
+             "occurred_on": _clean(r.get("occurred_on"))[:10], "evidence_id": _clean(r.get("evidence_id")),
+             "source_id": _clean(r.get("source_id")), "gap_type": "earlier"}
+            for n, r in enumerate(records, start=start)]
--- /dev/null
+++ b/services/inference_summary.py
@@ -0,0 +1,96 @@
+"""The report always leads with an inference (26 Oct 2026).
+
+The user's rule "the report is never blank" means the reader always gets some inference, not
+only restated news. RUN-3E2FE released a report whose implications were all "widely expected",
+so its only non-obvious reasoning sat further down in the hypothesis sections.
+
+lead(): the strongest inference in a report, with its label, so the dashboard can show it first.
+Fall-through order, strongest claim first; each keeps its own label and is never relabelled:
+
+    hidden_insight        passed every check
+    implication           forward implication beyond the headlines
+    verified_hypothesis   lab hypothesis whose thesis and mechanism the sources back
+    analysis              lab hypothesis: sourced facts, every link backed but one stated assumption
+    hypothesis            "Hypotheses to investigate" (services/hypothesis_tier.py)
+    analogy               a dated past case with an observed outcome
+    expected_implication  backed, but headline readers already expect it
+    move                  a next step for an actor, with its basis
+
+inventory(): how many of each a report has, so a report without any inference is flagged
+(inference_count == 0) instead of being counted as complete.
+"""
+
+from __future__ import annotations
+
+from typing import Any, Mapping
+
+VERSION = "inference-summary-v1"
+LABELS = {
+    "hidden_insight": "Hidden insight - every claim checked against its sources",
+    "implication": "Forward implication - backed by the sources, beyond the headlines",
+    "verified_hypothesis": "Hypothesis - its thesis and reasoning are backed by the sources",
+    "analysis": "Analysis - the facts are sourced; one reasoning step is a stated assumption",
+    "hypothesis": "Hypothesis to investigate - rests on sourced facts plus unstated reasoning",
+    "analogy": "Historical analogy - a dated past case with an observed outcome",
+    "expected_implication": "Expected implication - backed, but already implied by the headlines",
+    "move": "Possible next step for an actor - with the facts it rests on",
+}
+
+
+def _clean(value: Any) -> str:
+    return " ".join(str(value or "").split())
+
+
+def _map(value: Any) -> Mapping[str, Any]:
+    return value if isinstance(value, Mapping) else {}
+
+
+def _items(value: Any) -> list[Mapping[str, Any]]:
+    return [v for v in value if isinstance(v, Mapping)] if isinstance(value, (list, tuple)) else []
+
+
+def _candidates(sections: Mapping[str, Any], ratings: Mapping[str, str] | None = None):
+    from services import hypothesis_tier
+    brief = _map(sections.get("editorial_briefing"))
+    lab = _map(sections.get("strategic_hypotheses"))
+    for item in _items(sections.get("hidden_insights")):
+        yield "hidden_insight", _clean(item.get("text")), item
+    for item in _items(brief.get("implications")):
+        yield "implication", _clean(item.get("text")), item
+    # A rewritten (narrowed) hypothesis often only restates its facts: originals first.
+    verified = sorted(_items(lab.get("verified")), key=lambda h: bool(h.get("critic_inherited_from")))
+    for item in verified:
+        yield "verified_hypothesis", _clean(item.get("thesis")), item
+    for item in _items(lab.get("promising_unverified")):
+        if item.get("status") == "analysis":
+            yield "analysis", _clean(item.get("thesis")), item
+    for item in hypothesis_tier.select(sections, ratings):
+        if item.get("status") != "analysis":
+            yield "hypothesis", _clean(item.get("thesis")), item
+    for item in _items(brief.get("analogies")) or _items(sections.get("historical_analogies")):
+        yield "analogy", _clean(item.get("text") or _map(item.get("case")).get("text")), item
+    for item in _items(brief.get("expected_implications")):
+        yield "expected_implication", _clean(item.get("text")), item
+    for actor in _items(_map(sections.get("actor_moves")).get("actors")):
+        for move in _items(actor.get("moves")):
+            text = _clean(move.get("move"))
+            if text:
+                yield "move", f"{_clean(actor.get('actor'))}: {text}", move
+
+
+def lead(sections: Mapping[str, Any], ratings: Mapping[str, str] | None = None) -> dict[str, Any] | None:
+    """The strongest inference to show first, or None when the report has none."""
+    for kind, text, item in _candidates(sections, ratings):
+        if text:
+            return {"kind": kind, "label": LABELS[kind], "text": text, "item": dict(item)}
+    return None
+
+
+def inventory(sections: Mapping[str, Any]) -> dict[str, Any]:
+    counts = {kind: 0 for kind in LABELS}
+    for kind, text, _ in _candidates(sections):
+        if text:
+            counts[kind] += 1
+    non_obvious = sum(v for k, v in counts.items() if k not in {"expected_implication", "move"})
+    return {"version": VERSION, "counts": counts, "inference_count": sum(counts.values()),
+            "non_obvious_count": non_obvious, "lead_kind": (lead(sections) or {}).get("kind", "none")}
--- /dev/null
+++ b/tests/test_build_26oct_memory_cost_inference.py
@@ -0,0 +1,178 @@
+"""26 Oct 2026 build: memory pass, convergence exclusion, dated earlier facts, inference lead,
+supported-thesis tier fix, commentary page dates and the per-run call cap. No model calls."""
+from __future__ import annotations
+
+import json
+import sqlite3
+from datetime import datetime, timezone
+
+import pytest
+
+import services.research_orchestrator_service as ros
+from services import hypothesis_tier, inference_summary, memory_pass
+from services import strategic_hypothesis_lab as lab
+from services.hypothesis_evidence_service import page_text_date
+
+NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
+ACCORD = {"title": "Signing a voluntary safety agreement for AI technology", "actors": ["Donald Trump", "OpenAI"],
+          "facts": [{"text": "Executives from Anthropic, OpenAI and Nvidia signed the voluntary White House Accord, "
+                             "which sets no penalties."}]}
+
+
+def memory_store(path, rows):
+    db = sqlite3.connect(path)
+    db.execute("CREATE TABLE events(underlying_event_id, title, actors, event_date, topic, first_run_id, last_run_id,"
+               " first_seen, last_seen, source_ids, words)")
+    db.execute("CREATE TABLE excerpts(evidence_id, underlying_event_id, source_id, text, published_at, run_id)")
+    for uid, title, actors, day, run, seen, text in rows:
+        words = sorted(memory_pass._words(f"{title} {text}"))
+        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
+                   (uid, title, json.dumps(actors), day, "ai", run, run, seen, seen, "[]", json.dumps(words)))
+        db.execute("INSERT INTO excerpts VALUES (?,?,?,?,?,?)", (f"EVID-{uid}", uid, f"S-{uid}", text, day, run))
+    db.commit()
+    db.close()
+
+
+@pytest.fixture
+def stores(tmp_path, monkeypatch):
+    mem = tmp_path / "memory.sqlite3"
+    memory_store(mem, [
+        ("U-PLEDGE", "Trump gathers AI leaders and backs self-regulation pledge", ["Donald Trump"], "2026-09-29",
+         "RUN-OLD", "2026-10-09T12:00:00+00:00",
+         "President Trump gathered AI leaders and said a voluntary self-regulation pledge is the answer to AI risk."),
+        ("U-LATE", "Trump accord criticised by senators", ["Donald Trump"], "2026-10-20", "RUN-OLD",
+         "2026-10-26T09:00:00+00:00",   # stored AFTER the cut-off below: must never be used
+         "Senators criticised the voluntary accord signed by Donald Trump for lacking penalties or audits."),
+        ("U-CURRENT", "Executives sign the White House Accord", ["Donald Trump"], "2026-10-22", "RUN-NOW",
+         "2026-10-25T10:00:00+00:00",   # this run's own occurrence
+         "Executives from Anthropic, OpenAI and Nvidia signed the voluntary White House Accord, which sets no penalties."),
+        ("U-OTHER", "Bakery opens new branch", ["Local Bakery"], "2026-09-01", "RUN-OLD",
+         "2026-10-09T12:00:00+00:00", "A local bakery opened a new branch selling bread and cakes in the town centre."),
+    ])
+    monkeypatch.setenv("EVENT_INTELLIGENCE_EVENT_MEMORY", str(mem))
+    monkeypatch.setenv("EVENT_INTELLIGENCE_DATABASE_URL", "sqlite:///" + str(tmp_path / "missing.db"))
+    monkeypatch.delenv(memory_pass.ENV, raising=False)
+    return tmp_path
+
+
+def test_memory_pass_retrieves_dated_earlier_coverage_only(stores):
+    out = memory_pass.retrieve([ACCORD], {"run_id": "RUN-NOW"}, now=NOW)
+    ids = [r["evidence_id"] for r in out["records"]]
+    assert ids == ["MEM-EVID-U-PLEDGE"]                       # not the later, current or unrelated rows
+    record = out["records"][0]
+    assert record["gap_type"] == "earlier" and record["evidence_role"] == "earlier_coverage"
+    assert record["published_at"] == "2026-09-29" and record["available_since"] == "2026-10-09"
+    assert "current_event_corroboration" in record["prohibited_uses"]
+    assert record["memory_match"]["lane"] == "same_actor"
+    # eligible candidates per store after the cut-off and this-run filters; a missing store is skipped
+    assert out["meta"]["stores"] == {"event_memory": 2, "event_intelligence_db": 0}
+    assert out["meta"]["selected"][0]["content_sha256"] == record["content_sha256"]
+
+
+def test_replay_cutoff_is_the_original_checkpoint_time(stores):
+    state = {"run_id": "RUN-REPLAY", "replayed_from": {"run_id": "RUN-NOW", "saved_at": "2026-09-30T00:00:00+00:00"}}
+    out = memory_pass.retrieve([ACCORD], state, now=NOW)
+    assert out["records"] == [] and out["meta"]["cutoff"].startswith("2026-09-30")   # stored 9 Oct: too late
+
+
+def test_memory_pass_can_be_switched_off(stores, monkeypatch):
+    monkeypatch.setenv(memory_pass.ENV, "0")
+    assert memory_pass.retrieve([ACCORD], {"run_id": "RUN-NOW"}, now=NOW)["records"] == []
+
+
+def test_mechanism_cues_match_whole_words():
+    assert memory_pass.mechanisms("raised $1 billion in a funding round") == {"funding_round"}
+    assert "regulation_or_law" in memory_pass.mechanisms("a new bill would regulate AI")
+    assert "regulation_or_law" not in memory_pass.mechanisms("act as a partner")
+
+
+def test_database_snippet_must_appear_in_the_stored_article(tmp_path, monkeypatch):
+    db_path = tmp_path / "ei.db"
+    db = sqlite3.connect(db_path)
+    db.execute("CREATE TABLE events(event_id, source_id, title, event_type, event_start_at, entity_mentions, snippets,"
+               " created_at)")
+    db.execute("CREATE TABLE articles(source_id, content, published_at, url)")
+    quote = "President Trump said voluntary self-regulation pledges by AI leaders are the answer to AI risk."
+    for eid, sid, body in (("E-OK", "S-OK", f"Intro. {quote} More."), ("E-BAD", "S-BAD", "Different text only.")):
+        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
+                   (eid, sid, "Trump backs AI pledge", "regulation_or_policy_action", None,
+                    json.dumps([{"mention": "Donald Trump", "role": "actor"}]), json.dumps([quote]),
+                    "2026-10-01 10:00:00"))
+        db.execute("INSERT INTO articles VALUES (?,?,?,?)", (sid, body, "2026-09-30T08:00:00Z", "https://x/" + sid))
+    db.commit()
+    db.close()
+    monkeypatch.setenv("EVENT_INTELLIGENCE_EVENT_MEMORY", str(tmp_path / "none.sqlite3"))
+    monkeypatch.setenv("EVENT_INTELLIGENCE_DATABASE_URL", "sqlite:///" + str(db_path))
+    out = memory_pass.retrieve([ACCORD], {"run_id": "RUN-NOW"}, now=NOW)
+    assert [r["memory_store"] for r in out["records"]] == ["event_intelligence_db"]
+    assert out["records"][0]["source_url"] == "https://x/S-OK"
+
+
+def test_earlier_records_never_count_toward_convergence():
+    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
+    obj._source_reference = lambda sid: {"url": "", "publisher": ""}
+    evidence = [{"evidence_id": f"MEM-{i}", "source_id": f"S{i}", "analytical_context_only": True,
+                 "gap_type": "earlier", "source_url": f"https://pub{i}.example/a"} for i in range(3)]
+    evidence.append({"evidence_id": "CTX", "source_id": "S9", "analytical_context_only": True, "gap_type": "policy",
+                     "source_url": "https://rules.example/a"})
+    events, publishers = obj._convergence_maps(evidence, [])
+    assert set(events) == {"CTX"} and set(publishers) == {"CTX"}
+
+
+def test_earlier_facts_render_in_a_dated_block_the_cap_never_cuts():
+    facts = [{"fact_id": f"F{n}", "text": f"Current fact {n}.", "evidence_id": f"E{n}"} for n in range(1, 46)]
+    facts += memory_pass.as_facts([{"text": "Trump backed a voluntary pledge.", "published_at": "2026-09-29",
+                                    "occurred_on": "2026-09-29", "evidence_id": "MEM-1", "source_id": "S",
+                                    "earlier_event_title": "pledge"}])
+    text = lab._facts_text(facts)
+    assert "[F40]" in text and "[F41]" not in text                       # current facts still capped at 40
+    assert "EARLIER COVERAGE" in text and "[M1] (occurred 2026-09-29; reported 2026-09-29)" in text
+    assert "M1" in {r["fact_id"] for r in lab.fact_rows(facts, limit=200)}   # still citable
+
+
+def test_tier_admits_a_fully_supported_thesis():
+    item = {"id": "H1", "thesis": "t", "grounding": {"thesis": "supported", "mechanism": "partially_supported"},
+            "supporting_evidence": [{"evidence_id": "E1", "text": "a quote"}]}
+    assert hypothesis_tier.qualifies(item)
+
+
+def test_report_leads_with_the_strongest_inference_and_counts_it():
+    sections = {"editorial_briefing": {"implications": [],
+                                       "expected_implications": [{"text": "VC money keeps flowing."}]},
+                "strategic_hypotheses": {"verified": [
+                    {"id": "H1-N", "thesis": "Narrowed restatement.", "critic_inherited_from": "H1"},
+                    {"id": "H3", "thesis": "Labs make academic science depend on their platforms."}]}}
+    lead = inference_summary.lead(sections)
+    assert lead["kind"] == "verified_hypothesis" and lead["text"].startswith("Labs make")   # originals first
+    inv = inference_summary.inventory(sections)
+    assert inv["counts"]["verified_hypothesis"] == 2 and inv["counts"]["expected_implication"] == 1
+    assert inv["non_obvious_count"] == 2 and inv["lead_kind"] == "verified_hypothesis"
+    empty = inference_summary.inventory({"event_landscape": [{"title": "news"}]})
+    assert empty["inference_count"] == 0 and inference_summary.lead({}) is None
+
+
+def test_commentary_page_date_is_read_from_the_article_text():
+    assert page_text_date("Analysis | Brookings\nOctober 22, 2026\nTrump's pact is not enough").startswith("2026-10-22")
+    assert page_text_date("By A. Writer, 9 Sep 2026. The accord...").startswith("2026-09-09")
+    assert page_text_date("No date here at all.") == ""
+
+
+def test_call_cap_stops_optional_stages_before_the_reserve(monkeypatch):
+    from services import gemini_llm_service as gemini
+    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
+    obj.execution_budget_service = None
+    monkeypatch.setenv(obj.CALL_CAP_ENV, "20")
+    try:
+        obj._apply_call_cap()
+        assert obj._remaining_discretionary_seconds() == float("inf")
+        for _ in range(17):
+            gemini.consume_call()
+        assert obj._remaining_discretionary_seconds() == 0.0          # 17 >= 85% of 20: optional stages stop
+        gemini.consume_call(), gemini.consume_call(), gemini.consume_call()
+        with pytest.raises(gemini.GeminiCallBudgetExceeded):
+            gemini.consume_call()                                     # hard stop at the cap
+    finally:
+        gemini.set_call_budget(None)
+    monkeypatch.delenv(obj.CALL_CAP_ENV)
+    obj._apply_call_cap()
+    assert obj._call_cap is None
--- /dev/null
+++ b/tests/test_typed_mechanism_analysis.py
@@ -0,0 +1,71 @@
+"""Typed mechanism steps and the "analysis" status (26 Oct 2026). No model calls.
+
+A chain "A -> B -> C" used to be graded as one claim, so one unsourced background step failed a
+good hypothesis (EV3, RUN-3BF777). Now the starting fact must be stated by a source, each link
+only has to follow, and exactly one unbacked link makes the item "analysis" with that link shown
+as its stated assumption. Contradictions and two or more unbacked links still fail.
+"""
+from services import hypothesis_tier, inference_summary, insight_funnel
+from services import strategic_hypothesis_lab as lab
+
+ITEM = {"id": "EV3", "thesis": "The vendor, not the victim, controls when a breach is discovered.",
+        "mechanism": "An OpenAI agent accessed a government site -> Breaches are usually found by victims "
+                     "-> The victim learned months later from the vendor",
+        "fact_ids": ["F7"], "supporting_evidence": [{"evidence_id": "E1", "text": "It took three months to alert."}]}
+
+
+def grounded(start="supported", l2="unsupported", l3="inference_supported", thesis="inference_supported"):
+    claims = lab.grounding_claims(ITEM, ["E1", "E2"])
+    status = {"THESIS": thesis, "S1": start, "L2": l2, "L3": l3}
+    return [dict(c, grounding_status=status.get(c["claim_id"].rsplit("-", 1)[-1], "supported")) for c in claims]
+
+
+def test_mechanism_is_split_into_a_starting_fact_and_links():
+    claims = {c["claim_id"]: c for c in lab.grounding_claims(ITEM, ["E1", "E2"])}
+    assert claims["LAB-EV3-MECH-S1"]["epistemic_type"] == "fact"
+    assert claims["LAB-EV3-MECH-L2"]["epistemic_type"] == "mechanism"
+    assert claims["LAB-EV3-MECH-L2"]["claim_text"].startswith("Given that An OpenAI agent accessed")
+    assert "LAB-EV3-MECH" not in claims
+    single = lab.grounding_claims(dict(ITEM, mechanism="no arrows here"), ["E1"])
+    assert any(c["claim_id"] == "LAB-EV3-MECH" for c in single)          # unchanged when not a chain
+
+
+def test_one_unbacked_link_makes_analysis_with_that_link_as_assumption():
+    final = lab.finalize(ITEM, grounded(), [])
+    assert final["status"] == "analysis" and final["grounding"]["mechanism"] == lab.ASSUMPTION_BRIDGED
+    assert final["key_assumptions"] == ["Given that An OpenAI agent accessed a government site, "
+                                        "Breaches are usually found by victims"]
+    assert not lab.needs_narrowing(final)
+
+
+def test_every_link_backed_is_verified():
+    final = lab.finalize(ITEM, grounded(l2="inference_supported"), [])
+    assert final["status"] == "verified" and final["key_assumptions"] == []
+
+
+def test_still_rejected_when_it_should_be():
+    assert lab.finalize(ITEM, grounded(l3="unsupported"), [])["status"] == "promising_unverified"   # two links
+    assert lab.finalize(ITEM, grounded(start="partially_supported"), [])["status"] == "promising_unverified"
+    assert lab.finalize(ITEM, grounded(l3="contradicted"), [])["grounding"]["mechanism"] == "contradicted"
+    assert lab.finalize(ITEM, grounded(thesis="partially_supported"), [])["status"] == "promising_unverified"
+
+
+def test_legacy_single_mechanism_claim_grades_as_before():
+    rows = [{"claim_id": "LAB-X-THESIS", "grounding_status": "supported"},
+            {"claim_id": "LAB-X-MECH", "grounding_status": "inference_supported"}]
+    assert lab.finalize({"id": "X"}, rows, [])["status"] == "verified"
+
+
+def test_analysis_is_shown_labelled_first_and_counted_as_passed():
+    final = dict(lab.finalize(ITEM, grounded(), []), grounded_claims=grounded())
+    other = dict(final, id="EV9", status="promising_unverified", key_assumptions=[])
+    sections = {"strategic_hypotheses": {"verified": [], "promising_unverified": [other, final]}}
+    assert [h["id"] for h in hypothesis_tier.select(sections)] == ["EV3", "EV9"]
+    assert "Breaches are usually found by victims" in hypothesis_tier.assumptions(final)[0]
+    assert not any("Breaches" in p for p in hypothesis_tier.open_points(final))   # a link is not a fact
+    lead = inference_summary.lead(sections)
+    assert lead["kind"] == "analysis" and "stated assumption" in lead["label"]
+    inv = inference_summary.inventory(sections)
+    assert inv["counts"]["analysis"] == 1 and inv["counts"]["hypothesis"] == 1     # not counted twice
+    rows = insight_funnel.build(sections)["stages"]["hypothesis_lab"]["candidates"]
+    assert ("EV3", "passed") in [(r["id"], r["outcome"]) for r in rows]
--- /dev/null
+++ b/tests/test_lab_dependency_split.py
@@ -0,0 +1,56 @@
+import pytest
+from services import strategic_hypothesis_lab as lab
+
+
+def item():
+    return {'id':'H1','thesis':'A reporting delay is possible.','mechanism':'Access to logs constrains discovery.',
+            'leverage':{'holder':'Vendor A','counterparty':'Agency B','control_point':'internal agent logs',
+                        'dependency':'discovering an incident','outside_option':'Independent audit takes six months.',
+                        'holder_dependence':'A small government contract.'}}
+
+
+def grounded(control='supported',dependency='inference_supported'):
+    states={'THESIS':'inference_supported','MECH':'inference_supported','CONTROL':control,
+            'DEPENDENCY':dependency,'ALTERNATIVES':'inference_supported','ASYMMETRY':'inference_supported'}
+    return [{'claim_id':'LAB-H1-'+k,'grounding_status':v} for k,v in states.items()]
+
+
+def test_control_and_dependency_are_separate_atomic_checks():
+    claims={c['claim_id']:c for c in lab.grounding_claims(item(),['E1','E2'])}
+    assert claims['LAB-H1-CONTROL']['epistemic_type']=='fact'
+    assert 'depends' not in claims['LAB-H1-CONTROL']['claim_text']
+    assert claims['LAB-H1-DEPENDENCY']['epistemic_type']=='connection'
+    assert claims['LAB-H1-DEPENDENCY']['evidence_ids']==['E1','E2']
+    assert 'LAB-H1-LEVERAGE' not in claims
+
+
+def test_supported_dependency_inference_can_pass_without_verbatim_conclusion():
+    result=lab.finalize(item(),grounded(),[])
+    assert result['leverage_test']=='passed'
+    assert result['field_checks']['leverage']=='inference_supported'
+
+
+@pytest.mark.parametrize('control,dependency',[
+    ('supported','unsupported'),('supported','partially_supported'),
+    ('supported','contradicted'),('inference_supported','supported'),
+    ('unsupported','inference_supported'),('contradicted','supported')])
+def test_unsupported_components_never_pass(control,dependency):
+    assert lab.finalize(item(),grounded(control,dependency),[])['leverage_test']!='passed'
+
+
+def test_missing_dependency_cannot_borrow_legacy_success():
+    claims=[c for c in grounded() if not c['claim_id'].endswith('-DEPENDENCY')]
+    claims.append({'claim_id':'LAB-H1-LEVERAGE','grounding_status':'supported'})
+    assert lab.finalize(item(),claims,[])['leverage_test']!='passed'
+
+
+def test_incomplete_fields_do_not_generate_an_invented_dependency():
+    value=item();value['leverage']['control_point']=''
+    claims=lab.grounding_claims(value,['E1'])
+    assert not any(c['claim_id'].endswith(('-CONTROL','-DEPENDENCY')) for c in claims)
+    assert lab.finalize(value,grounded(),[])['leverage_test']!='passed'
+
+
+def test_split_does_not_override_contradicted_core_thesis():
+    claims=grounded();claims[0]['grounding_status']='contradicted'
+    assert lab.finalize(item(),claims,[])['status']=='promising_unverified'
```
