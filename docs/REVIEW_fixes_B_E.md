# Review package: fixes B and E (after the funnel and A, C, D)

23 Oct 2026. Not committed. No paid calls. Earlier package: docs/REVIEW_funnel_and_fixes_ACD.md.

## B: focus research before the briefing
- `_focus_research_before_briefing` runs in `_attach_spec_sections` before `_editorial_briefing`, but only if the time left covers focus research, the briefing and actor moves (240 + 90 + 120 s). Otherwise it records `{"attempted": false, "reason": "time_budget"}` and `_actor_moves` researches late, as before.
- There is one search per run. `_actor_moves` reuses `state["focus_research_early"]` whenever it was attempted, even when that attempt failed. `_choose_focus` is the old selection logic moved into a helper, with unchanged behaviour.
- **Briefing coverage (review point):** the focus organisation's current records are excluded from the briefing's context facts and commentary, so the briefing receives exactly the inputs it had before plus the past cases. The test compares the actual selected fact IDs.
- On the late path, a case found after the briefing is recorded in `case_found_after_briefing`, together with whether `select_cases` would have accepted it. The briefing is never regenerated.
- `analysis_metadata.focus_research` records attempted_before_briefing, the skip reason, the position, the status, the past-case count and any late cases.

## E: Hypotheses to investigate
- `services/hypothesis_tier.py` (new) decides what is shown; the dashboard only renders it.
- **Qualifies** when the thesis is inference_supported or partially_supported, no claim is contradicted (by status or by contradicted_evidence_ids), and at least one source quote supports part of the item.
- **Shows:** what the sources establish (quotes deduplicated by evidence ID and by text, with coverage as "N quotes from M publishers"); the reasoning step and its status; the assumptions (unsupported parts of the thesis and mechanism); the points not established (failed field claims and unresolved fact IDs); "would be wrong if"; and "watch for".
- **Order:** your rating of the same saved item (`lab:<id>`) first, then the number of distinct quotes, then pipeline order. Critic scores are not used.
- **Labels:** heading "Hypotheses to investigate" with the caption "Not insights…". The hidden-insight slot and the abstention status are unchanged. Items shown in this section are excluded from the collapsed list, and the funnel marks them `shown`.
- **On the saved report (RUN-3BF777):** EV2, EV1 and EV3 are shown; EV4 is excluded because its thesis is unsupported.

## Tests
26 new tests across 4 files, all passing. Two otherwise identical project copies (pre-edit originals vs. current, differing only in the 10 touched files) ran the same full pytest command: **59 identical failures in both**, none added or removed.

## Known limits
- Only lab items enter the tier; claim-gate near misses (`promising_hypotheses`) do not yet.
- The funnel's display selection ignores ratings, so with ratings present the three shown items can differ.
- On a saved pre-A report, an unresolved "F7" basis appears as an open point; new runs resolve it.

## Diff against pre-edit originals (also includes the A/C/D/funnel hunks in the same files)
```diff
--- a/services/research_orchestrator_service.py
+++ b/services/research_orchestrator_service.py
@@ -2897,7 +2897,12 @@
         when the run has any source statement.
         """
         from services import editorial_briefing as brief
-        context_records = [r for r in state.get("gap_context_records") or [] if isinstance(r, Mapping)]
+        # Plan fix B: research on the advised organisation now runs before the briefing, but only
+        # its past cases are meant for the briefing. Its current records stay out of the briefing's
+        # context facts and commentary, which keep the general coverage they had before.
+        focus_title = _clean(_as_mapping(state.get("focus_research_early")).get("development_title"))
+        context_records = [r for r in state.get("gap_context_records") or [] if isinstance(r, Mapping)
+                           and not (focus_title and _clean(r.get("gap_for")) == focus_title)]
         facts = brief.select_facts(developments)
         facts += brief.select_context_facts(context_records, start=len(facts) + 1)
         historical = list(_as_mapping(state.get("historical_context")).get("records") or [])
@@ -3342,17 +3347,27 @@
         from services import editorial_briefing as brief
         context_records = [r for r in state.get("gap_context_records") or [] if isinstance(r, Mapping)]
         facts = brief.select_facts(developments)
-        # v6.30: whose side the advice takes (the research: specific advice comes from writing for ONE
-        # known organisation - Klue, Feedly). Named by the user, else the organisation most involved.
-        profile = self._run_profile()
-        # v6.35: with a profile, the profiled organisation is the one advised (unless --org says otherwise).
-        focus_name = _clean(os.getenv("EVENT_INTELLIGENCE_FOCUS_ORG") or state.get("focus_organisation")
-                            or (profile or {}).get("name"))
-        focus = {"name": focus_name, "chosen_by": "user" if focus_name else "auto"}
-        if not focus_name:
-            focus["name"] = moves.pick_focus(developments, facts)
+        early = _as_mapping(state.get("focus_research_early"))
+        if early.get("attempted"):
+            # Researched before the briefing (_focus_research_before_briefing): reuse it, never search twice.
+            focus = dict(_as_mapping(early.get("focus")))
+            focus["research"] = dict(_as_mapping(early.get("research")))
+        else:
+            focus = self._choose_focus(developments, facts, state)
+        if focus["name"] and not early.get("attempted"):
+            known_cases = {_clean(r.get("evidence_id"))
+                           for r in _as_mapping(state.get("historical_context")).get("records") or []}
+            focus["research"] = dict(self._focus_research(focus["name"], facts, state), position="after_briefing")
+            # A case found now is too late for the briefing's analogies: kept on record, with
+            # whether it would have been selected, instead of being lost silently.
+            historical_now = list(_as_mapping(state.get("historical_context")).get("records") or [])
+            selected = {_clean(c.get("evidence_id")) for c in brief.select_cases(historical_now)}
+            late = [{"evidence_id": _clean(r.get("evidence_id")), "year": _as_mapping(r.get("historical_case")).get("year"),
+                     "selectable_as_analogy": _clean(r.get("evidence_id")) in selected}
+                    for r in historical_now if _clean(r.get("evidence_id")) not in known_cases]
+            if late and isinstance(state, dict):
+                state["case_found_after_briefing"] = late
         if focus["name"]:
-            focus["research"] = self._focus_research(focus["name"], facts, state)
             context_records = [r for r in state.get("gap_context_records") or [] if isinstance(r, Mapping)]
             # Facts found about the organisation being advised come first in the context.
             mine = moves.focus_development(focus["name"], [])["title"]
@@ -3496,18 +3511,26 @@
 
         raw: list[dict[str, Any]] = []
         models: list[str] = []
+        # Insight funnel: losses the lab output does not otherwise record (services/insight_funnel.py).
+        funnel: dict[str, int] = {}
+        base["funnel"] = funnel
         for batch_no, lenses in enumerate(lab.LENS_GROUPS, start=1):
             if self._remaining_discretionary_seconds() < self.HYPOTHESIS_LAB_MIN_SECONDS / 2:
+                funnel["generation_batches_skipped_time"] = len(lab.LENS_GROUPS) - batch_no + 1
                 break
             try:
                 reply, model = self._ask_strong_model(
                     lab.generation_prompt(topic, facts, lenses, batch=batch_no, profile=self._profile_block_for())
                 )
                 models.append(model)
-                raw.extend(lab.parse_generation(reply, facts))
+                raw.extend(lab.parse_generation(reply, facts, stats=funnel))
             except Exception as error:
+                funnel["generation_batches_failed"] = funnel.get("generation_batches_failed", 0) + 1
                 logger.warning("Strategic hypothesis generation batch %d failed: %s", batch_no, type(error).__name__)
-        raw = lab.dedupe(raw)[:lab.MAX_RAW]
+        unique_raw = lab.dedupe(raw)
+        funnel["generated_duplicates"] = funnel.get("generated_duplicates", 0) + len(raw) - len(unique_raw)
+        funnel["generated_over_cap"] = funnel.get("generated_over_cap", 0) + max(0, len(unique_raw) - lab.MAX_RAW)
+        raw = unique_raw[:lab.MAX_RAW]
         # v6.36.5: the user's own hypotheses (replay_run.py --hypotheses) join the evolution stage
         # under the same rules and checks, as scientists do in Co-Scientist. A broken file stops the run.
         user_hypotheses = lab.active_user_hypotheses()
@@ -3520,8 +3543,9 @@
                                                                        profile=self._profile_block_for(),
                                                                        user_hypotheses=user_hypotheses))
             models.append(model)
-            evolved = lab.parse_evolution(reply, facts)
+            evolved = lab.parse_evolution(reply, facts, stats=funnel)
         except Exception as error:
+            funnel["evolution_failed"] = 1
             logger.warning("Strategic hypothesis evolution failed: %s", type(error).__name__)
             evolved = []
         if not evolved:
@@ -3580,6 +3604,8 @@
             if isinstance(r, Mapping) and _clean(r.get("evidence_id"))
         }
         def ground_item(item: Mapping[str, Any]) -> dict[str, Any]:
+            # A basis written as bare fact IDs is grounded (and shown) as those facts' text.
+            item = lab.resolve_basis_refs(item, facts)
             seed_ids = lab.evidence_ids_for_fact_ids(
                 facts, item.get("fact_ids") or [], records
             )
@@ -3600,7 +3626,11 @@
                         if eid and eid not in extra_ids:
                             extra_ids.append(eid)
             cited_ids = _unique_strings(seed_ids + extra_ids)
-            claims = lab.grounding_claims(item, cited_ids)
+            # A resolved basis may cite facts outside the candidate's fact_ids: their evidence
+            # goes to that basis claim (and is listed with the item), not to the thesis.
+            basis_ids = {field: lab.evidence_ids_for_fact_ids(facts, ids, records)
+                         for field, ids in _as_mapping(item.get("basis_fact_ids")).items()}
+            claims = lab.grounding_claims(item, cited_ids, basis_evidence_ids=basis_ids)
             if not cited_ids or getattr(self, "grounding_service", None) is None:
                 grounded_rows = [{
                     "claim_id": c["claim_id"],
@@ -3613,7 +3643,8 @@
                 result = self._ground_claims_isolated(claims, records, label="Strategic hypothesis lab")
                 grounded_rows = [dict(x) for x in result.get("claims") or [] if isinstance(x, Mapping)]
             item_for_final = dict(item)
-            item_for_final["resolved_evidence_ids"] = list(cited_ids)
+            item_for_final["resolved_evidence_ids"] = _unique_strings(
+                list(cited_ids) + [eid for ids in basis_ids.values() for eid in ids])
             final = lab.finalize(item_for_final, grounded_rows, facts)
             final["grounded_claims"] = _json_safe(grounded_rows)
             # v6.34.1 (GPT review): each supporting quote carries its source link, publisher and rating.
@@ -3666,8 +3697,10 @@
                 original["narrowed_attempt"] = {k: final.get(k) for k in (
                     "id", "thesis", "mechanism", "grounding", "promotion_blockers", "supporting_evidence_ids")}
 
-        verified.sort(key=lambda x: (-int(x.get("critic_total", 0)), -len(x.get("supporting_evidence_ids") or [])))
-        promising.sort(key=lambda x: (-int(x.get("critic_total", 0)), -len(x.get("supporting_evidence_ids") or [])))
+        verified = lab.sort_by_critic(verified)
+        promising = lab.sort_by_critic(promising)
+        funnel["verified_over_cap"] = max(0, len(verified) - lab.MAX_VERIFIED)
+        funnel["promising_over_cap"] = max(0, len(promising) - lab.MAX_PROMISING)
         output = dict(
             base,
             status="complete" if verified else "hypotheses_unverified",
@@ -3765,6 +3798,42 @@
     FOCUS_RESEARCH_MIN_SECONDS = 240.0
     SOURCE_BACKED_MAX_SHOWN = 2  # v6.30.2: backed insights that sources/experts already state
 
+    def _choose_focus(self, developments: Sequence[Mapping[str, Any]], facts: Sequence[Mapping[str, Any]],
+                      state: Mapping[str, Any]) -> dict[str, Any]:
+        """v6.30: whose side the advice takes (the research: specific advice comes from writing for ONE
+        known organisation - Klue, Feedly). Named by the user, else the organisation most involved.
+        v6.35: with a profile, the profiled organisation is the one advised (unless --org says otherwise)."""
+        from services import actor_moves as moves
+        profile = self._run_profile()
+        focus_name = _clean(os.getenv("EVENT_INTELLIGENCE_FOCUS_ORG") or state.get("focus_organisation")
+                            or (profile or {}).get("name"))
+        focus = {"name": focus_name, "chosen_by": "user" if focus_name else "auto"}
+        if not focus_name:
+            focus["name"] = moves.pick_focus(developments, facts)
+        return focus
+
+    def _focus_research_before_briefing(self, developments: Sequence[Mapping[str, Any]], state: dict[str, Any]) -> None:
+        """Plan fix B (23 Oct 2026): research the advised organisation BEFORE the briefing, so a
+        past case it finds (RUN-3BF777: Capital One 2019, found at 12:23 after the briefing chose
+        its analogies at 12:21) can reach the analogies. Runs only when the time left also covers
+        the briefing and actor moves; otherwise _actor_moves researches late, as before. Either
+        way there is one search per run, and state["focus_research_early"] records what happened."""
+        from services import editorial_briefing as brief
+        if not isinstance(state, dict) or state.get("focus_research_early"):
+            return
+        facts = brief.select_facts(developments)
+        focus = self._choose_focus(developments, facts, state)
+        if not focus["name"]:
+            return
+        needed = self.FOCUS_RESEARCH_MIN_SECONDS + self.BRIEFING_MIN_SECONDS + self.ACTOR_MOVES_MIN_SECONDS
+        if self._remaining_discretionary_seconds() < needed:
+            state["focus_research_early"] = {"attempted": False, "reason": "time_budget"}
+            return
+        from services import actor_moves as moves
+        research = dict(self._focus_research(focus["name"], facts, state), position="before_briefing")
+        state["focus_research_early"] = {"attempted": True, "focus": focus, "research": research,
+                                         "development_title": moves.focus_development(focus["name"], [])["title"]}
+
     def _focus_research(self, org: str, facts: Sequence[Mapping[str, Any]], state: Mapping[str, Any]) -> dict:
         """v6.30: targeted research on the organisation being advised (its rules, dependencies,
         size, comparable cases, analyst views), through the same gap research and checks.
@@ -3912,6 +3981,11 @@
                 ])
             if state.get("gap_research_summary") is not None:
                 report.setdefault("analysis_metadata", {})["precedent_research"] = state.get("gap_research_summary")
+            if deep and isinstance(state, dict):
+                try:
+                    self._focus_research_before_briefing(developments, state)
+                except Exception as error:   # never fatal: _actor_moves then researches late, as before
+                    state["focus_research_early"] = {"attempted": False, "reason": "failed:" + type(error).__name__}
             briefing = self._editorial_briefing(
                 topic=topic, developments=developments, evidence=evidence, state=state)
         except Exception as error:
@@ -3948,6 +4022,17 @@
                 "version": "strategic-hypothesis-lab-v1.1.1-evidence-bridge", "status": "failed",
                 "error_type": type(error).__name__, "verified": [], "promising_unverified": []}
         report["historical_analogies"] = _json_safe(briefing.get("analogies") or [])
+        # Plan fix B: where the advised organisation was researched, and any case found too late.
+        early = _as_mapping(state.get("focus_research_early"))
+        research = _as_mapping(_as_mapping(_as_mapping(report.get("actor_moves")).get("focus_organisation")).get("research"))
+        report.setdefault("analysis_metadata", {})["focus_research"] = _json_safe({
+            "attempted_before_briefing": bool(early.get("attempted")),
+            "early_skipped_reason": _clean(early.get("reason")),
+            "position": _clean(research.get("position")) or "not_run",
+            "status": _clean(research.get("status")),
+            "past_cases": research.get("past_cases"),
+            "case_found_after_briefing": list(state.get("case_found_after_briefing") or []),
+        })
         report["forecast_followups"] = _json_safe(self._forecast_followups(
             report=report, topic=topic, events=events, evidence=evidence, state=state, briefing=briefing))
         # v6.35: which arm of the experiment produced this report (baseline or profile).
@@ -21772,6 +21857,15 @@
                 "",
             ),
         }
+        # Insight funnel: where each candidate went, from what the sections above record.
+        # Log and metadata only; never fatal and never changes what is released.
+        try:
+            from services import insight_funnel
+            funnel = insight_funnel.build(canonical_sections)
+            canonical_sections["analysis_metadata"]["insight_funnel"] = _json_safe(funnel)
+            logger.info("insight_funnel=%s", json.dumps(insight_funnel.summary(funnel), ensure_ascii=False, default=str))
+        except Exception as error:
+            logger.warning("insight_funnel failed: %s: %s", type(error).__name__, error)
 
         persisted_findings = (
             report.get("strategic_findings", [])
--- a/frontend/dashboard.py
+++ b/frontend/dashboard.py
@@ -4022,15 +4022,18 @@
     return lines
 
 
-def render_strategic_hypotheses(sections: Mapping[str, Any]) -> None:
-    """v6.34: the hypothesis lab's output was saved but never shown."""
+def render_strategic_hypotheses(sections: Mapping[str, Any], exclude_ids: Iterable[str] = ()) -> None:
+    """v6.34: the hypothesis lab's output was saved but never shown.
+    exclude_ids: hypotheses already shown under "Hypotheses to investigate" (plan fix E)."""
     lab = sections.get("strategic_hypotheses") if isinstance(sections, Mapping) else None
     lab = lab if isinstance(lab, Mapping) else {}
     evaluation = sections.get("insight_evaluation") if isinstance(sections.get("insight_evaluation"), Mapping) else {}
     verified = [h for h in as_list(lab.get("verified")) if isinstance(h, Mapping)]
     # The lab's own hypotheses first (they carry the leverage chain), then claim-gate near misses.
-    unverified, seen = [], set()
+    unverified, seen, excluded = [], set(), {clean_text(x) for x in exclude_ids}
     for h in as_list(lab.get("promising_unverified")) + as_list(evaluation.get("promising_unverified")):
+        if isinstance(h, Mapping) and clean_text(h.get("id")) and clean_text(h.get("id")) in excluded:
+            continue
         key = clean_text(isinstance(h, Mapping) and (h.get("thesis") or h.get("insight") or h.get("text"))).casefold()
         if isinstance(h, Mapping) and key and key not in seen:
             seen.add(key)
@@ -4117,6 +4120,63 @@
                 st.success(f"Saved {saved} ratings.")
 
 
+def investigation_lines(item: Mapping[str, Any]) -> list[str]:
+    """Plan fix E: one hypothesis to investigate - what the sources establish, kept apart from
+    what is unresolved or assumed, the reasoning step, and what would disprove it."""
+    from services import hypothesis_tier as tier
+    thesis = clean_text(item.get("thesis"))
+    if not thesis:
+        return []
+    lines = ["**" + _markdown_safe(thesis) + "**"]
+    if clean_text(item.get("_rating")):
+        lines.append(f"*You rated this {clean_text(item.get('_rating'))}.*")
+    lines.append(f"- **What the sources establish** ({tier.coverage(item)}):")
+    for quote in tier.premises(item)[:4]:
+        url, who = clean_text(quote.get("url")), _markdown_safe(quote.get("publisher")) or "source"
+        src = f"[{who}]({url})" if url.startswith(("http://", "https://")) else who
+        lines.append("  - “" + _markdown_safe(quote.get("text"))[:220] + "” (" + src + ")")
+    grounding = item.get("grounding") if isinstance(item.get("grounding"), Mapping) else {}
+    if clean_text(item.get("mechanism")):
+        status = clean_text(grounding.get("mechanism"))
+        lines.append("- **Reasoning step:** " + _markdown_safe(item.get("mechanism"))
+                     + (f" *({GROUNDING_NOTES.get(status, status)})*" if status else ""))
+    assumed = tier.assumptions(item)
+    if assumed:
+        lines.append("- **Assumed, not stated by any source:** " + "; ".join(_markdown_safe(a) for a in assumed[:3]))
+    unresolved = tier.open_points(item)
+    if unresolved:
+        lines.append("- **Not established by the sources:**")
+        lines += ["  - " + _markdown_safe(point) for point in unresolved[:4]]
+    for key, label in (("falsifier", "Would be wrong if"), ("watch_signal", "Watch for")):
+        if clean_text(item.get(key)):
+            lines.append(f"- **{label}:** {_markdown_safe(item.get(key))}")
+    return lines
+
+
+def render_hypotheses_to_investigate(items: Iterable[Mapping[str, Any]]) -> None:
+    """Plan fix E: near misses shown expanded, labelled as hypotheses, never as insights."""
+    items = [item for item in items if isinstance(item, Mapping)]
+    if not items:
+        return
+    st.subheader("Hypotheses to investigate")
+    st.caption("Not insights. Each rests on facts the sources state plus reasoning they do not state; "
+               "nothing here is contradicted by a source. Check the open points before acting on one.")
+    for item in items:
+        st.markdown("\n".join(investigation_lines(item)))
+
+
+def _investigation_items(report: Mapping[str, Any], sections: Mapping[str, Any]) -> list[dict[str, Any]]:
+    from services import hypothesis_tier as tier
+    ratings: dict[str, str] = {}
+    try:   # your own ratings of these same saved items order them first
+        from services import insight_ratings
+        report_id = clean_text(report.get("report_id"))
+        ratings = {r["item_key"]: r["rating"] for r in insight_ratings.load() if r.get("report_id") == report_id}
+    except Exception:
+        ratings = {}
+    return tier.select(sections, ratings)
+
+
 def render_main_finding(
     report: Mapping[str, Any],
     sections: Mapping[str, Any],
@@ -4131,7 +4191,8 @@
     """
     render_profile_note(sections)
     render_insight_ranking(sections)
-    render_strategic_hypotheses(sections)
+    investigate = _investigation_items(report, sections)
+    render_strategic_hypotheses(sections, exclude_ids=[clean_text(i.get("id")) for i in investigate])
     render_rating_form(report, sections)
     render_editorial_briefing(sections)
     hidden = [item for item in as_list(sections.get("hidden_insights")) if isinstance(item, Mapping)]
@@ -4154,6 +4215,7 @@
     else:
         st.markdown("**None this run.**")
         st.caption("No candidate passed every evidence check for a hidden insight. The briefing above is complete.")
+    render_hypotheses_to_investigate(investigate)
     checked_connections = [clean_text(item.get("text") or item.get("connection")) for item in connections]
     checked_connections += [
         clean_text(item.get("text")) for item in findings
--- /dev/null
+++ b/services/insight_funnel.py
@@ -0,0 +1,248 @@
+"""Where candidate insights go in a run (insight funnel v2, 23 Oct 2026).
+
+Built from what a report already records, so it works on saved reports as well as new
+runs and needs no model call. Different things are counted in different units, never mixed:
+
+    candidates  one row per distinct candidate id, with its final outcome:
+                passed | rejected | downgraded | repaired (rewritten; see its events)
+    events      what happened along the way (a narrowing attempt and its result)
+    claims      individual checked claims that failed (one candidate has many)
+    attempts    how many gate attempts ran
+    sentences   sentences pruned from kept text (the briefing records only a count)
+    display     what the dashboard does with a kept candidate: shown | collapsed | cut
+
+A figure a report never recorded is "not_recorded", not 0: an old report without the lab's
+counters has not been shown to lose nothing. The funnel makes losses traceable; it does not
+say whether a lost item was useful - that is what data/insight_ratings.jsonl is for.
+
+Read-only CLI on saved reports (opens reports.sqlite3 read-only):
+    python -m services.insight_funnel            # latest report
+    python -m services.insight_funnel REPORT-ID  # one report
+"""
+
+from __future__ import annotations
+
+import json
+import sqlite3
+import sys
+from collections import Counter
+from pathlib import Path
+from typing import Any, Mapping
+
+VERSION = "insight-funnel-v2-units"
+CANDIDATE_OUTCOMES = ("passed", "rejected", "downgraded", "repaired")
+NOT_RECORDED = "not_recorded"
+# What the dashboard shows of the lab (frontend/dashboard.py render_strategic_hypotheses).
+DASHBOARD_UNVERIFIED_SHOWN = 10
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
+def _list(value: Any) -> list[Any]:
+    return list(value) if isinstance(value, (list, tuple)) else []
+
+
+def _row(item_id: Any, text: Any, outcome: str, reason: Any = "") -> dict[str, str]:
+    return {"id": _clean(item_id), "text": _clean(text)[:160], "outcome": outcome, "reason": _clean(reason)[:240]}
+
+
+def _stage(candidates=(), events=(), claims=(), display=(), attempts: Any = NOT_RECORDED,
+           sentences_pruned: Any = NOT_RECORDED, **extra) -> dict[str, Any]:
+    return {"candidates": list(candidates), "events": list(events), "claims": list(claims),
+            "display": list(display), "attempts": attempts, "sentences_pruned": sentences_pruned, **extra}
+
+
+def _lab(sections: Mapping[str, Any]) -> dict[str, Any]:
+    lab = _map(sections.get("strategic_hypotheses"))
+    evaluation = _map(sections.get("insight_evaluation"))
+    verified = [h for h in _list(lab.get("verified")) if isinstance(h, Mapping)]
+    unverified = [h for h in _list(lab.get("promising_unverified")) if isinstance(h, Mapping)]
+    candidates, events = [], []
+    for h in verified:
+        candidates.append(_row(h.get("id"), h.get("thesis"), "passed", "verified"
+                               + (f"; rewritten from {_clean(h.get('critic_inherited_from'))}, unscored"
+                                  if h.get("critic_inherited_from") else "")))
+    for h in unverified:
+        grounding = _map(h.get("grounding"))
+        failed = {k: v for k, v in _map(h.get("field_checks")).items()
+                  if v not in {"supported", "inference_supported", "not_checked"}}
+        reason = "; ".join([f"thesis={_clean(grounding.get('thesis'))}",
+                            f"mechanism={_clean(grounding.get('mechanism'))}"]
+                           + [f"{k}={v}" for k, v in sorted(failed.items())]
+                           + [f"blocked:{b}" for b in _list(h.get("promotion_blockers"))])
+        narrowing = _clean(h.get("narrowing"))
+        repaired = narrowing.startswith("narrowed_version_verified")
+        candidates.append(_row(h.get("id"), h.get("thesis"), "repaired" if repaired else "rejected", reason))
+        if narrowing:
+            events.append(_row(h.get("id"), h.get("thesis"), "narrowing", narrowing))
+    # Dashboard: unverified hypotheses (lab and claim-gate near misses, deduplicated by text)
+    # are collapsed when any is verified, and cut after DASHBOARD_UNVERIFIED_SHOWN.
+    # Plan fix E: qualifying near misses are shown expanded as "Hypotheses to investigate"
+    # (selection here ignores your ratings, which can change which three are shown).
+    from services import hypothesis_tier
+    investigated = {_clean(h.get("id")) for h in hypothesis_tier.select(sections)}
+    display, seen = [], set()
+    for h in unverified:
+        if _clean(h.get("id")) in investigated:
+            seen.add(_clean(h.get("thesis")).casefold())
+            display.append(_row(h.get("id"), h.get("thesis"), "shown", "hypotheses_to_investigate"))
+    pool = unverified + [h for h in _list(evaluation.get("promising_unverified")) if isinstance(h, Mapping)]
+    for h in pool:
+        key = _clean(h.get("thesis") or h.get("insight") or h.get("text")).casefold()
+        if not key or key in seen:
+            continue
+        seen.add(key)
+        state = ("cut" if len(seen) - len(investigated) > DASHBOARD_UNVERIFIED_SHOWN
+                 else "collapsed" if verified else "shown")
+        display.append(_row(h.get("id") or h.get("insight_id"), key, state))
+    display += [_row(h.get("id"), h.get("thesis"), "shown") for h in verified]
+    counts = {k: v for k, v in _map(lab.get("funnel")).items() if isinstance(v, int)}
+    return _stage(candidates, events, display=display, status=_clean(lab.get("status")),
+                  generated_kept=lab.get("raw_count", NOT_RECORDED),
+                  evolved_kept=lab.get("evolved_count", NOT_RECORDED),
+                  losses_before_grounding=counts or NOT_RECORDED,   # v6.36.8+ runs only
+                  narrowing=dict(_map(lab.get("narrowing"))) or NOT_RECORDED)
+
+
+def _claim_gate(sections: Mapping[str, Any]) -> dict[str, Any]:
+    meta = _map(_map(sections.get("analysis_metadata")).get("claim_support_gate"))
+    candidates, claims = [], []
+    for item in _list(sections.get("hidden_insights")):
+        if isinstance(item, Mapping):
+            candidates.append(_row(item.get("insight_id"), item.get("text"), "passed", item.get("finding_tier")))
+    for item in _list(sections.get("strategic_findings")):
+        if isinstance(item, Mapping) and _clean(item.get("finding_tier")) != "hidden_insight":
+            candidates.append(_row(item.get("insight_id"), item.get("text"), "downgraded",
+                                   "finding_tier:" + _clean(item.get("finding_tier"))))
+    for item in _list(sections.get("promising_hypotheses")):
+        if isinstance(item, Mapping):
+            candidates.append(_row(item.get("insight_id"), item.get("text"), "rejected",
+                                   "; ".join(_clean(r) for r in _list(item.get("failure_reasons")))))
+    attempts = _list(meta.get("attempts"))
+    for attempt in attempts:
+        for claim in _list(_map(attempt).get("failed_claims")):
+            claim = _map(claim)
+            claims.append(_row(claim.get("claim_id"), claim.get("claim_text"), "rejected",
+                               f"{_clean(claim.get('epistemic_type'))}:{_clean(claim.get('grounding_status'))}"))
+    return _stage(candidates, claims=claims, attempts=len(attempts) if "attempts" in meta else NOT_RECORDED,
+                  abstention_reasons=[_clean(r) for r in _list(meta.get("abstention_reasons"))])
+
+
+def _briefing(sections: Mapping[str, Any]) -> dict[str, Any]:
+    brief = _map(sections.get("editorial_briefing"))
+    candidates = []
+    for n, item in enumerate(_list(brief.get("implications"))):
+        if isinstance(item, Mapping):
+            candidates.append(_row(f"IMP{n + 1}", item.get("text"), "passed", item.get("novelty")))
+    for n, item in enumerate(_list(brief.get("expected_implications"))):
+        if isinstance(item, Mapping):
+            candidates.append(_row(f"EXP{n + 1}", item.get("text"), "downgraded",
+                                   "widely_expected:" + ",".join(_clean(r) for r in _list(item.get("novelty_reasons")))))
+    for n, item in enumerate(_list(brief.get("analogies"))):
+        if isinstance(item, Mapping):
+            candidates.append(_row(item.get("case_id") or f"AN{n + 1}", item.get("text"), "passed", "analogy"))
+    pruned = brief.get("sentences_removed_by_check")
+    return _stage(candidates, sentences_pruned=int(pruned) if isinstance(pruned, int) else NOT_RECORDED,
+                  mode=_clean(brief.get("mode")) or NOT_RECORDED,
+                  cases=brief.get("case_count", NOT_RECORDED),
+                  expert_commentary=len(_list(brief.get("expert_commentary"))) if "expert_commentary" in brief
+                  else NOT_RECORDED)
+
+
+def _actor_moves(sections: Mapping[str, Any]) -> dict[str, Any]:
+    moves = _map(sections.get("actor_moves"))
+    candidates, claims = [], []
+    for actor in _list(moves.get("actors")):
+        actor = _map(actor)
+        name = _clean(actor.get("actor"))
+        for n, move in enumerate(_list(actor.get("moves"))):
+            if isinstance(move, Mapping):
+                candidates.append(_row(f"{name}:M{n + 1}", move.get("move"), "passed", move.get("basis")))
+        for n, move in enumerate(_list(actor.get("expected_moves"))):
+            if isinstance(move, Mapping):
+                candidates.append(_row(f"{name}:E{n + 1}", move.get("move"), "downgraded", "widely_expected"))
+        for n, move in enumerate(_list(actor.get("rejected"))):
+            if isinstance(move, Mapping):   # the model's own best-response step set it aside
+                candidates.append(_row(f"{name}:R{n + 1}", move.get("move"), "rejected",
+                                       "set_aside_by_choice:" + _clean(move.get("why_not"))))
+    # "removed" lists unsupported parts of an actor's analysis (crux, leverage, choice, a move):
+    # claims, not whole candidates.
+    for n, text in enumerate(_list(moves.get("removed"))):
+        claims.append(_row(f"AM-X{n + 1}", text, "rejected", "not_supported_by_sources"))
+    return _stage(candidates, claims=claims, status=_clean(moves.get("status")) or NOT_RECORDED)
+
+
+def _tally(stage: Mapping[str, Any]) -> dict[str, Any]:
+    out: dict[str, Any] = {"candidates": dict(Counter(r["outcome"] for r in stage["candidates"]))}
+    out["distinct_candidate_ids"] = len({r["id"] for r in stage["candidates"] if r["id"]})
+    if stage["events"]:
+        out["events"] = len(stage["events"])
+    if stage["claims"]:
+        out["failed_claims"] = len(stage["claims"])
+    if stage["display"]:
+        out["display"] = dict(Counter(r["outcome"] for r in stage["display"]))
+    for key in ("attempts", "sentences_pruned"):
+        if stage[key] != NOT_RECORDED:
+            out[key] = stage[key]
+    return out
+
+
+def build(sections: Mapping[str, Any]) -> dict[str, Any]:
+    """The funnel for one report's sections (the saved payload's "sections")."""
+    stages = {
+        "hypothesis_lab": _lab(sections),
+        "claim_support_gate": _claim_gate(sections),
+        "editorial_briefing": _briefing(sections),
+        "actor_moves": _actor_moves(sections),
+    }
+    return {"version": VERSION, "tally": {name: _tally(stage) for name, stage in stages.items()},
+            "stages": stages}
+
+
+def summary(funnel: Mapping[str, Any]) -> dict[str, Any]:
+    """Compact form for the run log: the per-stage tally plus rejected candidate ids."""
+    out: dict[str, Any] = {"version": funnel.get("version")}
+    for name, stage in _map(funnel.get("stages")).items():
+        out[name] = dict(_map(_map(funnel.get("tally")).get(name)))
+        out[name]["rejected_ids"] = [r["id"] for r in stage["candidates"] if r["outcome"] == "rejected" and r["id"]][:12]
+    return out
+
+
+def _load_sections(db_path: Path, report_id: str = "") -> tuple[str, Mapping[str, Any]]:
+    uri = db_path.resolve().as_uri() + "?mode=ro"
+    with sqlite3.connect(uri, uri=True) as db:
+        query = ("SELECT r.report_id, v.payload_json FROM reports r JOIN report_revisions v "
+                 "ON v.report_revision_id = r.current_revision_id ")
+        row = (db.execute(query + "WHERE r.report_id = ?", (report_id,)).fetchone() if report_id
+               else db.execute(query + "ORDER BY r.created_at DESC LIMIT 1").fetchone())
+    if not row:
+        raise SystemExit(f"No report found: {report_id or 'latest'}")
+    return row[0], _map(json.loads(row[1]).get("sections"))
+
+
+def main(argv: list[str]) -> int:
+    db_path = Path(__file__).resolve().parents[1] / "data" / "reports.sqlite3"
+    report_id, sections = _load_sections(db_path, argv[0] if argv else "")
+    funnel = build(sections)
+    print(report_id)
+    for name, stage in funnel["stages"].items():
+        print(f"\n== {name}  {json.dumps(funnel['tally'][name])}")
+        extra = {k: v for k, v in stage.items()
+                 if k not in {"candidates", "events", "claims", "display", "attempts", "sentences_pruned"}}
+        if extra:
+            print("   " + json.dumps(extra, default=str)[:400])
+        for unit in ("candidates", "events", "claims", "display"):
+            for r in stage[unit]:
+                print(f"  [{unit[:-1]}] {r['outcome']:<10} {r['id']:<24} {r['text'][:80]}"
+                      + (f"\n               reason: {r['reason']}" if r["reason"] else ""))
+    return 0
+
+
+if __name__ == "__main__":
+    raise SystemExit(main(sys.argv[1:]))
--- /dev/null
+++ b/services/hypothesis_tier.py
@@ -0,0 +1,117 @@
+"""Hypotheses to investigate (plan fix E, 23 Oct 2026).
+
+When nothing passes every check, the lab's near misses were hidden in a collapsed list while
+the report said "None this run". This tier shows up to MAX_SHOWN of them, expanded, but never
+as insights: each separates what the sources establish from what is unresolved, states the
+reasoning step and what would disprove it.
+
+An item qualifies when:
+- its thesis "follows from" or is "partly backed by" the sources (inference_supported or
+  partially_supported);
+- nothing in it is contradicted by a source, so a contradiction is never shown as a premise;
+- at least one source quote supports part of it.
+
+Order: your own rating of the same saved item first (useful, then unrated, then meh/poor),
+then how many distinct source quotes support it. Critic scores are not used: they saturate
+(most items score 10), and a rewritten item is unscored.
+"""
+
+from __future__ import annotations
+
+from typing import Any, Mapping, Sequence
+
+VERSION = "hypothesis-tier-v1"
+MAX_SHOWN = 3
+QUALIFYING_THESIS = frozenset({"inference_supported", "partially_supported"})
+PASSED = frozenset({"supported", "inference_supported"})
+RATING_ORDER = {"useful": 0, "": 1, "meh": 2, "poor": 3}
+FIELD_LABELS = {"LEVERAGE": "Leverage", "CONTROL": "Control point", "DEPENDENCY": "Dependency",
+                "ALTERNATIVES": "Alternatives", "ASYMMETRY": "Who needs whom more",
+                "MOVEBASIS": "Past behaviour behind the move", "COUNTERBASIS": "Past behaviour behind the countermove",
+                "MOVE": "Likely move", "COUNTER": "Countermove"}
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
+def _list(value: Any) -> list[Any]:
+    return list(value) if isinstance(value, (list, tuple)) else []
+
+
+def _claims(item: Mapping[str, Any]) -> list[Mapping[str, Any]]:
+    return [c for c in _list(item.get("grounded_claims")) if isinstance(c, Mapping)]
+
+
+def contradicted(item: Mapping[str, Any]) -> bool:
+    grounding = _map(item.get("grounding"))
+    return ("contradicted" in {_clean(grounding.get("thesis")), _clean(grounding.get("mechanism"))}
+            or any(_clean(c.get("grounding_status")) == "contradicted" or _list(c.get("contradicted_evidence_ids"))
+                   for c in _claims(item)))
+
+
+def premises(item: Mapping[str, Any]) -> list[Mapping[str, Any]]:
+    """Source quotes that support part of the item: one per evidence record, and the same
+    passage stored under two evidence ids is counted once."""
+    seen, out = set(), []
+    for quote in _list(item.get("supporting_evidence")):
+        if isinstance(quote, Mapping) and _clean(quote.get("text")):
+            keys = {_clean(quote.get("text")).casefold()} | ({_clean(quote.get("evidence_id"))}
+                                                            if _clean(quote.get("evidence_id")) else set())
+            if not keys & seen:
+                seen |= keys
+                out.append(quote)
+    return out
+
+
+def qualifies(item: Mapping[str, Any]) -> bool:
+    return (_clean(_map(item.get("grounding")).get("thesis")) in QUALIFYING_THESIS
+            and not contradicted(item) and bool(premises(item)))
+
+
+def open_points(item: Mapping[str, Any]) -> list[str]:
+    """Checked parts the sources do not establish, in plain words."""
+    out = []
+    for claim in _claims(item):
+        suffix = _clean(claim.get("claim_id")).rsplit("-", 1)[-1]
+        if suffix in {"THESIS", "MECH"} or _clean(claim.get("grounding_status")) in PASSED:
+            continue
+        label = FIELD_LABELS.get(suffix, suffix.title())
+        out.append(f"{label}: {_clean(claim.get('claim_text'))[:220]}")
+    for field, ids in _map(item.get("unresolved_fact_ids")).items():
+        out.append(f"{FIELD_LABELS.get('MOVEBASIS' if field == 'actor_move_basis' else 'COUNTERBASIS')}: "
+                   f"cites unknown fact {', '.join(ids)} (not checked)")
+    return out
+
+
+def assumptions(item: Mapping[str, Any]) -> list[str]:
+    """What the reasoning adds that no source states (the thesis and mechanism checks)."""
+    out = []
+    for claim in _claims(item):
+        if _clean(claim.get("claim_id")).rsplit("-", 1)[-1] in {"THESIS", "MECH"}:
+            out += [_clean(x)[:220] for x in _list(claim.get("unsupported_parts")) if _clean(x)
+                    and not _clean(x).startswith("The claim requires multiple supporting evidence records")]
+    return list(dict.fromkeys(out))
+
+
+def select(sections: Mapping[str, Any], ratings: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
+    """The hypotheses to show, best first. ratings: {item_key: useful|meh|poor} for this report
+    (item_key "lab:<id>", as services/insight_ratings.py stores lab items)."""
+    ratings = ratings or {}
+    lab = _map(sections.get("strategic_hypotheses"))
+    pool = [h for h in _list(lab.get("promising_unverified")) if isinstance(h, Mapping) and qualifies(h)]
+    ranked = sorted(enumerate(pool), key=lambda pair: (
+        RATING_ORDER.get(_clean(ratings.get(f"lab:{_clean(pair[1].get('id'))}")), 1),
+        -len(premises(pair[1])), pair[0]))
+    return [dict(item, _rating=_clean(ratings.get(f"lab:{_clean(item.get('id'))}"))) for _, item in ranked[:MAX_SHOWN]]
+
+
+def coverage(item: Mapping[str, Any]) -> str:
+    quotes = premises(item)
+    publishers = {_clean(q.get("publisher")).casefold() for q in quotes if _clean(q.get("publisher"))}
+    return (f"{len(quotes)} source quote{'s' if len(quotes) != 1 else ''} from "
+            f"{len(publishers) or 'unknown'} publisher{'s' if len(publishers) != 1 else ''}")
--- /dev/null
+++ b/tests/test_plan_fix_b_focus_before_briefing.py
@@ -0,0 +1,112 @@
+"""Plan fix B (docs/PLAN_next_two_weeks.md): research on the advised organisation runs before
+the briefing, so a past case it finds can reach the analogies (RUN-3BF777 lost Capital One 2019:
+found at 12:23, briefing chose analogies at 12:21). One search per run; late fallback kept."""
+from __future__ import annotations
+
+import json
+
+import services.research_orchestrator_service as ros
+from services import editorial_briefing as brief
+
+DEVELOPMENTS = [{"title": "OpenAI agent breach", "actors": ["OpenAI", "Australia"],
+                 "facts": [{"text": "OpenAI notified Australia three months after its agent accessed a health site.",
+                            "evidence_id": "E1", "source_id": "S1", "date": "2026-09-24"}]}]
+GENERAL = {"evidence_id": "EVID-GENERAL", "source_id": "S8", "gap_type": "policy", "analytical_context_only": True,
+           "gap_for": "OpenAI agent breach",
+           "text": "Under the Security of Critical Infrastructure Act entities must notify ACSC within 72 hours."}
+CASE = {"evidence_id": "EVID-CAPONE", "source_id": "S7", "gap_type": "precedent",
+        "text": "In 2019 Capital One disclosed a breach of 100 million records.",
+        "source_excerpt": "In 2019 Capital One disclosed a breach of 100 million records.",
+        "historical_case": {"year": 2019, "date_quote": "In 2019", "outcome_quote": "It paid an $80 million penalty.",
+                            "similarity": "cloud provider breach disclosed late", "difference": "human attacker"}}
+
+
+def orchestrator(monkeypatch, seconds=float("inf")):
+    monkeypatch.setenv("EVENT_INTELLIGENCE_FOCUS_ORG", "OpenAI")
+    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
+    obj._remaining_discretionary_seconds = lambda: seconds
+    obj._run_profile = lambda: None
+    calls = []
+
+    class Service:
+        def investigate_gaps(self, *, development, blueprint, search):
+            calls.append(development["title"])
+            return {"status": "complete", "queries": [], "records": [
+                {"evidence_id": "EVID-ORG", "source_id": "S9", "gap_type": "policy", "analytical_context_only": True,
+                 "gap_for": development["title"], "text": "OpenAI must report serious incidents under the EU AI Act."},
+                dict(CASE, precedent_for=development["title"])]}
+
+    obj._hypothesis_evidence_service = lambda: Service()
+    obj._grounded_search = lambda state: None
+    obj._ask_strong_model = lambda prompt: (json.dumps({"actors": []}), "pro")
+    return obj, calls
+
+
+def capture_briefing(monkeypatch, obj, state):
+    seen = {}
+
+    def build_prompt(topic, facts, cases, focus=""):
+        seen["facts"], seen["cases"] = [f["evidence_id"] for f in facts], [c["evidence_id"] for c in cases]
+        return "prompt"
+
+    def unavailable(prompt):
+        raise RuntimeError("no model in tests")
+
+    monkeypatch.setattr(brief, "build_prompt", build_prompt)
+    obj._ask_briefing_model = unavailable
+    obj._briefing_focus = lambda: ""
+    out = obj._editorial_briefing(topic="ai", developments=DEVELOPMENTS, evidence=[], state=state)
+    return out, seen
+
+
+def test_case_found_by_focus_research_reaches_the_briefing(monkeypatch):
+    obj, calls = orchestrator(monkeypatch)
+    state = {"gap_context_records": [GENERAL]}
+    obj._focus_research_before_briefing(DEVELOPMENTS, state)
+    assert state["focus_research_early"]["attempted"] is True
+    assert state["focus_research_early"]["research"]["position"] == "before_briefing"
+    out, seen = capture_briefing(monkeypatch, obj, state)
+    assert out["case_count"] == 1 and seen["cases"] == ["EVID-CAPONE"]
+
+
+def test_briefing_context_facts_are_unchanged_by_focus_records(monkeypatch):
+    # GPT review: keeping the order does not protect general coverage (the selector sorts by
+    # credibility before its cap), so compare the actual selected fact IDs.
+    obj, _ = orchestrator(monkeypatch)
+    _, without = capture_briefing(monkeypatch, obj, {"gap_context_records": [GENERAL]})
+    state = {"gap_context_records": [GENERAL]}
+    obj._focus_research_before_briefing(DEVELOPMENTS, state)
+    assert any(r["evidence_id"] == "EVID-ORG" for r in state["gap_context_records"])
+    _, with_focus = capture_briefing(monkeypatch, obj, state)
+    assert with_focus["facts"] == without["facts"] and "EVID-ORG" not in with_focus["facts"]
+
+
+def test_actor_moves_reuses_the_early_research_and_never_searches_twice(monkeypatch):
+    obj, calls = orchestrator(monkeypatch)
+    state = {"gap_context_records": [GENERAL]}
+    obj._focus_research_before_briefing(DEVELOPMENTS, state)
+    obj._focus_research_before_briefing(DEVELOPMENTS, state)   # a second call is a no-op
+    out = obj._actor_moves(topic="ai", developments=DEVELOPMENTS, evidence=[], state=state)
+    assert len(calls) == 1
+    assert out["focus_organisation"]["research"]["position"] == "before_briefing"
+    assert "case_found_after_briefing" not in state
+
+
+def test_low_time_budget_keeps_late_research_and_records_the_late_case(monkeypatch):
+    obj, calls = orchestrator(monkeypatch, seconds=ros.ResearchOrchestratorService.FOCUS_RESEARCH_MIN_SECONDS
+                              + ros.ResearchOrchestratorService.ACTOR_MOVES_MIN_SECONDS + 1)
+    state = {"gap_context_records": [GENERAL]}
+    obj._focus_research_before_briefing(DEVELOPMENTS, state)
+    assert state["focus_research_early"] == {"attempted": False, "reason": "time_budget"} and not calls
+    out = obj._actor_moves(topic="ai", developments=DEVELOPMENTS, evidence=[], state=state)
+    assert len(calls) == 1 and out["focus_organisation"]["research"]["position"] == "after_briefing"
+    assert state["case_found_after_briefing"] == [
+        {"evidence_id": "EVID-CAPONE", "year": 2019, "selectable_as_analogy": True}]
+
+
+def test_without_an_organisation_nothing_is_researched(monkeypatch):
+    obj, calls = orchestrator(monkeypatch)
+    monkeypatch.setenv("EVENT_INTELLIGENCE_FOCUS_ORG", "")
+    state = {}
+    obj._focus_research_before_briefing([{"title": "x", "actors": ["tech CEOs"], "facts": []}], state)
+    assert not calls and "focus_research_early" not in state
--- /dev/null
+++ b/tests/test_plan_fix_e_hypotheses_to_investigate.py
@@ -0,0 +1,117 @@
+"""Plan fix E: near misses shown as "Hypotheses to investigate" - never as insights."""
+from __future__ import annotations
+
+import contextlib
+import importlib
+import sys
+import types
+
+from services import hypothesis_tier as tier
+from services import insight_funnel
+
+QUOTE = {"evidence_id": "EVID-DELAY", "publisher": "slashdot.org", "url": "https://slashdot.org/x",
+         "text": "Albanese complained it took three months for OpenAI to alert Australia's government."}
+
+
+def hypothesis(hid, thesis_status="inference_supported", quotes=(QUOTE,), **extra):
+    item = {"id": hid, "thesis": f"Hypothesis {hid}: the provider, not the victim, controls breach discovery.",
+            "mechanism": "Agent accesses site -> provider logs show it -> victim learns months later",
+            "grounding": {"thesis": thesis_status, "mechanism": "partially_supported"},
+            "falsifier": "Australia detected the access itself.", "watch_signal": "Taskforce findings.",
+            "supporting_evidence": list(quotes),
+            "grounded_claims": [
+                {"claim_id": f"LAB-{hid}-THESIS", "claim_text": "t", "grounding_status": thesis_status,
+                 "unsupported_parts": []},
+                {"claim_id": f"LAB-{hid}-MECH", "claim_text": "m", "grounding_status": "partially_supported",
+                 "unsupported_parts": ["Traditional breaches are identified by victims",
+                                       "The claim requires multiple supporting evidence records, but fewer"]},
+                {"claim_id": f"LAB-{hid}-LEVERAGE", "claim_text": "OpenAI controls the agent logs.",
+                 "grounding_status": "unsupported"},
+                {"claim_id": f"LAB-{hid}-MOVE", "claim_text": "Leaders complain to CEOs.",
+                 "grounding_status": "supported"}]}
+    item.update(extra)
+    return item
+
+
+def sections(*items, verified=()):
+    return {"strategic_hypotheses": {"verified": list(verified), "promising_unverified": list(items)}}
+
+
+def test_qualifying_conditions():
+    assert tier.qualifies(hypothesis("A"))
+    assert tier.qualifies(hypothesis("B", thesis_status="partially_supported"))
+    assert not tier.qualifies(hypothesis("C", thesis_status="unsupported"))      # e.g. EV4
+    assert not tier.qualifies(hypothesis("D", quotes=()))                        # no sourced premise
+    contradicted = hypothesis("E")
+    contradicted["grounded_claims"][2]["grounding_status"] = "contradicted"
+    assert not tier.qualifies(contradicted)                                      # never shown as a premise
+    contradicted_ids = hypothesis("F")
+    contradicted_ids["grounded_claims"][3]["contradicted_evidence_ids"] = ["EVID-X"]
+    assert not tier.qualifies(contradicted_ids)
+
+
+def test_premises_open_points_and_assumptions_are_kept_apart():
+    item = hypothesis("A")
+    assert [q["evidence_id"] for q in tier.premises(item)] == ["EVID-DELAY"]
+    assert tier.open_points(item) == ["Leverage: OpenAI controls the agent logs."]    # supported MOVE not listed
+    assert tier.assumptions(item) == ["Traditional breaches are identified by victims"]
+    unknown = hypothesis("U", unresolved_fact_ids={"actor_move_basis": ["F99"]})
+    assert "cites unknown fact F99 (not checked)" in tier.open_points(unknown)[-1]
+    assert tier.coverage(item) == "1 source quote from 1 publisher"
+    twice = hypothesis("T", quotes=(QUOTE, dict(QUOTE, evidence_id="EVID-COPY")))   # same passage, two ids
+    assert len(tier.premises(twice)) == 1
+
+
+def test_order_uses_your_ratings_then_quote_count_never_critic_score():
+    two = dict(QUOTE, evidence_id="EVID-2", publisher="indianexpress.com",
+               text="Australia set up a taskforce to examine how AI-related breaches are identified and reported.")
+    items = [hypothesis("A", critic_total=10), hypothesis("B", quotes=(QUOTE, two), critic_total=7),
+             hypothesis("C", critic_total=10), hypothesis("D", critic_total=10)]
+    assert [h["id"] for h in tier.select(sections(*items))] == ["B", "A", "C"]
+    rated = tier.select(sections(*items), {"lab:D": "useful", "lab:B": "poor"})
+    assert [h["id"] for h in rated] == ["D", "A", "C"] and rated[0]["_rating"] == "useful"
+
+
+def test_funnel_reports_selected_items_as_shown():
+    data = sections(hypothesis("A"), hypothesis("C", thesis_status="unsupported"), verified=[{"id": "V", "thesis": "v"}])
+    display = {r["id"]: r["outcome"] for r in insight_funnel.build(data)["stages"]["hypothesis_lab"]["display"]}
+    assert display == {"A": "shown", "C": "collapsed", "V": "shown"}
+
+
+def _dashboard():
+    calls = []
+
+    class Ctx(contextlib.nullcontext):
+        def __call__(self, fn=None, *a, **k):
+            return fn
+
+    class St(types.ModuleType):
+        def __getattr__(self, name):
+            return lambda *a, **k: calls.append((name, a[0] if a else k)) or Ctx()
+
+    saved = {name: sys.modules.get(name) for name in ("streamlit", "frontend.dashboard")}
+    sys.modules["streamlit"] = St("streamlit")
+    sys.modules.pop("frontend.dashboard", None)
+    return importlib.import_module("frontend.dashboard"), calls, saved
+
+
+def test_dashboard_section_is_labelled_and_not_called_an_insight():
+    dashboard, calls, saved = _dashboard()
+    try:
+        dashboard.render_hypotheses_to_investigate(tier.select(sections(hypothesis("A"))))
+        text = "\n".join(str(arg) for _, arg in calls)
+        assert "Hypotheses to investigate" in text and "Not insights." in text
+        assert "**What the sources establish** (1 source quote from 1 publisher):" in text
+        assert "[slashdot.org](https://slashdot.org/x)" in text
+        assert "**Assumed, not stated by any source:** Traditional breaches are identified by victims" in text
+        assert "**Not established by the sources:**" in text and "**Would be wrong if:**" in text
+        calls.clear()
+        dashboard.render_hypotheses_to_investigate([])
+        assert not calls
+        calls.clear()   # an item shown above is not repeated in the collapsed list
+        dashboard.render_strategic_hypotheses(sections(hypothesis("A"), hypothesis("B")), exclude_ids=["A"])
+        text = "\n".join(str(arg) for _, arg in calls)
+        assert "Hypothesis B:" in text and "Hypothesis A:" not in text
+    finally:
+        for name, mod in saved.items():
+            sys.modules.pop(name, None) if mod is None else sys.modules.__setitem__(name, mod)
```
