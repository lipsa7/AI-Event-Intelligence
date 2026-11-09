# Review package: insight funnel + fixes A, C, D (with GPT review points 1–3)

23 Oct 2026. Not committed. No paid calls. Plan: docs/PLAN_next_two_weeks.md.

## What changed
- **Funnel** (`services/insight_funnel.py`, new): per stage it keeps candidates (one row per distinct ID, final outcome), events (narrowing), failed claims, gate attempts, pruned sentences and display states (shown/collapsed/cut) separate. A figure the report never recorded reads `not_recorded`. It is attached to `analysis_metadata.insight_funnel` and logged as `insight_funnel=`. CLI on saved reports (opens the DB read-only): `python -m services.insight_funnel [REPORT-ID]`. The lab parsers count losses that were never recorded before (invalid rows, duplicates, low critic score, caps).
- **A**: a basis made only of fact IDs is replaced by those facts' text. The cited facts' evidence goes to that basis claim only (review point 1). An unknown ID → `unresolved_fact_ids`, basis left out of grounding, field `not_checked`. Prose with "(F3, F5)" is unchanged.
- **C**: `EXPERT_VIEW_RULE` added to the document-review prompt. The mapping is unchanged.
- **D**: a narrowed rewrite drops `critic_scores` and `critic_total`, and records `critic_inherited_from` plus a labelled `critic_total_before_narrowing`. `sort_by_critic` places unscored items at the median of scored ones (a provisional display order, not a quality judgment).

## Baseline (review point 3)
The pre-edit originals of the 3 edited files come from `event_intelligence_research_ner_update.zip`. The lab file's hash matches the lab patch manifest's `before` (d05bd16f…). The diffs below are the only differences. Two otherwise identical copies of the project (pre and post) ran the same full pytest command: **59 identical failures in both**, none added or removed. The 16 new tests exist only in post, and they pass.

## Known limits
- C's test proves the instruction is present, not that the model follows it.
- The funnel cannot show losses before grounding for runs made before these counters.
- The GPT lab patch's `before` hash no longer matches `strategic_hypothesis_lab.py`; it must be reconciled into one package.

## Diff against pre-edit originals
```diff
--- a/services/strategic_hypothesis_lab.py
+++ b/services/strategic_hypothesis_lab.py
@@ -420,17 +420,32 @@
     }
 
 
-def parse_generation(reply: Any, facts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
+def _count(stats: dict[str, int] | None, key: str, n: int = 1) -> None:
+    """Insight funnel (services/insight_funnel.py): losses no report records otherwise."""
+    if stats is not None and n:
+        stats[key] = stats.get(key, 0) + n
+
+
+def parse_generation(reply: Any, facts: Sequence[Mapping[str, Any]],
+                     stats: dict[str, int] | None = None) -> list[dict[str, Any]]:
     data = _json(reply)
     valid = {r["fact_id"].casefold() for r in fact_rows(facts, limit=200)}
     out: list[dict[str, Any]] = []
-    for n, row in enumerate(data.get("hypotheses") or []):
+    rows = data.get("hypotheses") or []
+    _count(stats, "generated_rows", len(rows))
+    for n, row in enumerate(rows):
         if not isinstance(row, Mapping):
+            _count(stats, "generated_invalid")
             continue
         item = _candidate(row, valid, fallback_id=f"H{n + 1}")
         if item:
             out.append(item)
-    return dedupe(out)[:MAX_RAW]
+        else:
+            _count(stats, "generated_invalid")
+    unique = dedupe(out)
+    _count(stats, "generated_duplicates", len(out) - len(unique))
+    _count(stats, "generated_over_cap", max(0, len(unique) - MAX_RAW))
+    return unique[:MAX_RAW]
 
 
 def dedupe(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
@@ -499,15 +514,20 @@
 Only return candidates with total critic score >= 7/10. Do not fabricate facts."""
 
 
-def parse_evolution(reply: Any, facts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
+def parse_evolution(reply: Any, facts: Sequence[Mapping[str, Any]],
+                    stats: dict[str, int] | None = None) -> list[dict[str, Any]]:
     data = _json(reply)
     valid = {r["fact_id"].casefold() for r in fact_rows(facts, limit=200)}
     out: list[dict[str, Any]] = []
-    for n, row in enumerate(data.get("hypotheses") or []):
+    rows = data.get("hypotheses") or []
+    _count(stats, "evolution_rows", len(rows))
+    for n, row in enumerate(rows):
         if not isinstance(row, Mapping):
+            _count(stats, "evolution_invalid")
             continue
         item = _candidate(row, valid, fallback_id=f"E{n + 1}")
         if not item:
+            _count(stats, "evolution_invalid")
             continue
         scores = row.get("critic_scores") if isinstance(row.get("critic_scores"), Mapping) else {}
         normalized = {k: max(0, min(2, int(scores.get(k, 0) or 0))) for k in (
@@ -515,6 +535,7 @@
         )}
         total = sum(normalized.values())
         if total < 7:
+            _count(stats, "evolution_low_critic")
             continue
         item["critic_scores"] = normalized
         item["critic_total"] = total
@@ -522,9 +543,11 @@
         item["evolution_note"] = _clean(row.get("evolution_note"))[:700]
         item["from_user"] = [u for u in (user_id_from_source(x) for x in item["source_ids"]) if u]   # v1.5/1.6
         out.append(item)
-    out = dedupe(out)
-    out.sort(key=lambda x: (-int(x.get("critic_total", 0)), -len(x.get("fact_ids") or [])))
-    return out[:MAX_EVOLVED]
+    unique = dedupe(out)
+    _count(stats, "evolution_duplicates", len(out) - len(unique))
+    unique.sort(key=lambda x: (-int(x.get("critic_total", 0)), -len(x.get("fact_ids") or [])))
+    _count(stats, "evolution_over_cap", max(0, len(unique) - MAX_EVOLVED))
+    return unique[:MAX_EVOLVED]
 
 
 def to_research_candidate(
@@ -574,12 +597,61 @@
             + ".")
 
 
-def grounding_claims(item: Mapping[str, Any], evidence_ids: Sequence[str]) -> list[dict[str, Any]]:
+def sort_by_critic(items: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
+    """Critic score first, then supporting evidence. An unscored item (a narrowed rewrite) is
+    placed at the median of the scored ones: unscored is not a low score."""
+    scores = [x["critic_total"] for x in items if isinstance(x.get("critic_total"), int)]
+    neutral = sorted(scores)[len(scores) // 2] if scores else 0
+    return sorted(items, key=lambda x: (
+        -(x["critic_total"] if isinstance(x.get("critic_total"), int) else neutral),
+        -len(x.get("supporting_evidence_ids") or [])))
+
+
+BASIS_FIELDS = ("actor_move_basis", "countermove_basis")
+_FACT_REF = r"[A-Za-z]{1,3}\d{1,3}"
+_ONLY_FACT_REFS = re.compile(rf"^\W*{_FACT_REF}(?:\W+(?:and\W+)?{_FACT_REF})*\W*$", re.I)
+
+
+def resolve_basis_refs(item: Mapping[str, Any], facts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
+    """A move basis written only as fact IDs ("F7", "F16, F17") is replaced by those facts'
+    text from this candidate's fact table, so grounding checks the statement rather than the
+    literal "F7" (RUN-3BF777: EV2-EV4 failed that way). Prose citing "(F3, F5)" is unchanged.
+    An unknown ID is never dropped silently: the basis is left out of grounding and its field
+    is reported as not_checked, with the IDs in unresolved_fact_ids."""
+    out = dict(item)
+    table = {r["fact_id"].casefold(): r["text"] for r in fact_rows(facts, limit=250)}
+    unresolved: dict[str, list[str]] = {}
+    resolved: dict[str, list[str]] = {}   # the basis cites these facts, so their evidence goes with it
+    for field in BASIS_FIELDS:
+        text = _clean(item.get(field))
+        if not text or not _ONLY_FACT_REFS.match(text):
+            continue
+        ids = _unique(re.findall(_FACT_REF, text))
+        missing = [x for x in ids if x.casefold() not in table]
+        out[field + "_as_written"] = text
+        if missing:
+            unresolved[field] = missing
+            out[field] = ""
+        else:
+            out[field] = "; ".join(f"{x}: {table[x.casefold()]}" for x in ids)[:1500]
+            resolved[field] = ids
+    if unresolved:
+        out["unresolved_fact_ids"] = unresolved
+    if resolved:
+        out["basis_fact_ids"] = resolved
+    return out
+
+
+def grounding_claims(item: Mapping[str, Any], evidence_ids: Sequence[str],
+                     basis_evidence_ids: Mapping[str, Sequence[str]] | None = None) -> list[dict[str, Any]]:
     """Thesis and mechanism decide 'verified'. v1.3 (GPT review of v6.34): the leverage chain,
     likely move and countermove are shown too, so each is checked separately and shown with
-    its own status - a field the sources do not back is labelled, never presented as verified."""
+    its own status - a field the sources do not back is labelled, never presented as verified.
+    basis_evidence_ids: evidence of the facts a resolved basis cites (resolve_basis_refs), added
+    to that basis claim only - a basis may cite a fact outside the candidate's own fact_ids."""
     base = f"LAB-{_clean(item.get('id'))}"
     ids = list(evidence_ids)
+    basis_evidence_ids = basis_evidence_ids or {}
     claims = [
         {"claim_id": base + "-THESIS", "claim_text": _clean(item.get("thesis")),
          "epistemic_type": "hidden_insight", "evidence_ids": ids, "multi_evidence_required": len(ids) >= 2},
@@ -605,8 +677,9 @@
     for suffix, key, basis in (("-MOVEBASIS", "likely_actor_move", "actor_move_basis"),
                                ("-COUNTERBASIS", "likely_countermove", "countermove_basis")):
         if _clean(item.get(key)) and _clean(item.get(basis)):   # v1.5: the past behaviour is a stated fact
+            basis_ids = _unique(ids + [_clean(x) for x in basis_evidence_ids.get(basis) or []])
             claims.append({"claim_id": base + suffix, "claim_text": _clean(item.get(basis)), "epistemic_type": "fact",
-                           "evidence_ids": ids, "multi_evidence_required": False})
+                           "evidence_ids": basis_ids, "multi_evidence_required": False})
     for suffix, key in (("-MOVE", "likely_actor_move"), ("-COUNTER", "likely_countermove")):
         if _clean(item.get(key)):
             claims.append({"claim_id": base + suffix, "claim_text": _clean(item.get(key)),
@@ -680,6 +753,8 @@
                    "leverage_asymmetry": _clean(lev.get("holder_dependence"))}.get(field, _clean(item.get(field)))
         if status or present:
             field_checks[field] = status or "not_checked"
+    for field in (item.get("unresolved_fact_ids") or {}):   # cited an unknown fact ID: not checkable
+        field_checks[field] = "not_checked"
     from services.leverage_validation import result as leverage_result
     # Unknown alternatives must never become evidence of limited alternatives.
     if leverage_sentence(lev):
@@ -793,8 +868,14 @@
     out = dict(final)
     for key in ("status", "grounding", "supporting_evidence_ids", "promotion_blockers", "grounded_claims",
                 "supporting_evidence", "resolved_evidence_ids", "evidence_ids", "field_checks",
-                "narrowing", "narrowed_attempt", "leverage_test"):
+                "narrowing", "narrowed_attempt", "leverage_test", "critic_scores", "critic_total",
+                "actor_move_basis_as_written", "countermove_basis_as_written", "unresolved_fact_ids",
+                "basis_fact_ids"):
         out.pop(key, None)
+    # The critic scored the original claim, not this rewrite: the rewrite is unscored, and the
+    # original's score is kept only as a labelled reference (never reused for ranking).
+    out["critic_inherited_from"] = _clean(final.get("id"))
+    out["critic_total_before_narrowing"] = final.get("critic_total")
     # v1.3 (GPT review): every displayed field comes from the rewrite; nothing is inherited from the
     # overstated original. A field the rewrite leaves empty is shown as absent, not as the old text.
     rewritten = {key: _clean(data.get(key))[:900] for key in (
--- a/services/research_orchestrator_service.py
+++ b/services/research_orchestrator_service.py
@@ -3496,18 +3496,26 @@
 
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
@@ -3520,8 +3528,9 @@
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
@@ -3580,6 +3589,8 @@
             if isinstance(r, Mapping) and _clean(r.get("evidence_id"))
         }
         def ground_item(item: Mapping[str, Any]) -> dict[str, Any]:
+            # A basis written as bare fact IDs is grounded (and shown) as those facts' text.
+            item = lab.resolve_basis_refs(item, facts)
             seed_ids = lab.evidence_ids_for_fact_ids(
                 facts, item.get("fact_ids") or [], records
             )
@@ -3600,7 +3611,11 @@
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
@@ -3613,7 +3628,8 @@
                 result = self._ground_claims_isolated(claims, records, label="Strategic hypothesis lab")
                 grounded_rows = [dict(x) for x in result.get("claims") or [] if isinstance(x, Mapping)]
             item_for_final = dict(item)
-            item_for_final["resolved_evidence_ids"] = list(cited_ids)
+            item_for_final["resolved_evidence_ids"] = _unique_strings(
+                list(cited_ids) + [eid for ids in basis_ids.values() for eid in ids])
             final = lab.finalize(item_for_final, grounded_rows, facts)
             final["grounded_claims"] = _json_safe(grounded_rows)
             # v6.34.1 (GPT review): each supporting quote carries its source link, publisher and rating.
@@ -3666,8 +3682,10 @@
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
@@ -21772,6 +21790,15 @@
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
--- a/services/hypothesis_evidence_service.py
+++ b/services/hypothesis_evidence_service.py
@@ -20,6 +20,16 @@
 logger = logging.getLogger("event_intelligence.services.hypothesis_evidence_service")
 
 VERSION = "hypothesis-evidence-v1.1"
+# Expert views (23 Oct 2026): a quoted outside expert's assessment had no claim_kind of its own,
+# so it was tagged reported_claim and never reached "What experts are already saying". The
+# mapping (gap "commentary" + analysis/forecast) is unchanged; only the definition is stated.
+EXPERT_VIEW_RULE = (
+    "claim_kind: \"analysis\" is an assessment, explanation or recommendation by the page's author "
+    "or by a named or described expert, analyst or organisation that is NOT a party to the events; "
+    "keep the attribution (who said it) inside the quote. A statement by a party to the events (a "
+    "company, government, official or person involved) is \"reported_claim\", even when it is an "
+    "opinion. The credibility of the website does not change this."
+)
 CHALLENGE_MODES = {"counterevidence", "alternative_explanation"}
 CHALLENGE_ROLES = {"counterevidence", "counter_incentive_evidence", "alternative_explanation_evidence"}
 # Search families are judged separately, so a broken history source cannot
@@ -444,7 +454,8 @@
             "case as news of that time (then give that publication year). Outcomes must be observed, not "
             "forecast. Include "
             "the actor, action and qualifiers in each quote. Return records=[] when irrelevant; "
-            "do not manufacture a competing explanation. review_complete=false if context is insufficient.\n"
+            "do not manufacture a competing explanation. review_complete=false if context is insufficient. "
+            + EXPERT_VIEW_RULE + "\n"
             + json.dumps({"hypothesis": candidate, "query": context.get("query"),
                           "title": result.get("title"), "published_at": published,
                           "as_of": now.isoformat(), "source_text": body}, ensure_ascii=False),
--- /dev/null
+++ b/services/insight_funnel.py
@@ -0,0 +1,239 @@
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
+    display, seen = [], set()
+    pool = unverified + [h for h in _list(evaluation.get("promising_unverified")) if isinstance(h, Mapping)]
+    for h in pool:
+        key = _clean(h.get("thesis") or h.get("insight") or h.get("text")).casefold()
+        if not key or key in seen:
+            continue
+        seen.add(key)
+        state = "cut" if len(seen) > DASHBOARD_UNVERIFIED_SHOWN else "collapsed" if verified else "shown"
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
+++ b/tests/test_insight_funnel.py
@@ -0,0 +1,126 @@
+"""Insight funnel (services/insight_funnel.py): losses traceable by id, never mixing units."""
+from services import insight_funnel as funnel
+from services import strategic_hypothesis_lab as lab
+
+
+def sections():
+    return {
+        "strategic_hypotheses": {
+            "status": "complete", "raw_count": 5, "evolved_count": 3,
+            "funnel": {"generated_rows": 7, "generated_invalid": 2, "evolution_low_critic": 1},
+            "narrowing": {"tried": 2, "verified": 1, "dropped": 1},
+            "verified": [{"id": "EV1-N", "thesis": "Narrowed claim.", "critic_inherited_from": "EV1"}],
+            "promising_unverified": [
+                {"id": "EV1", "thesis": "Original claim.", "grounding": {"thesis": "inference_supported",
+                                                                       "mechanism": "partially_supported"},
+                 "field_checks": {"likely_actor_move": "supported", "countermove_basis": "unsupported"},
+                 "narrowing": "narrowed_version_verified:EV1-N"},
+                {"id": "EV3", "thesis": "Discovery inversion.", "grounding": {"thesis": "inference_supported",
+                                                                            "mechanism": "partially_supported"},
+                 "promotion_blockers": ["absolute_or_exclusive_claim_requires_direct_support"],
+                 "narrowing": "nothing_strategic_survived_or_still_overstated"},
+            ],
+        },
+        "insight_evaluation": {"promising_unverified": [{"insight_id": "INSIGHT-1", "text": "Gate near miss."}]},
+        "promising_hypotheses": [{"insight_id": "INSIGHT-1", "text": "Gate near miss.",
+                                  "failure_reasons": ["premise_not_supported_by_cited_evidence"]}],
+        "analysis_metadata": {"claim_support_gate": {
+            "abstention_reasons": ["claim_support_gate:premise_not_supported_by_cited_evidence"],
+            "attempts": [{"failed_claims": [{"claim_id": "C-LEV", "claim_text": "X controls Y.",
+                                             "epistemic_type": "fact", "grounding_status": "partially_supported"}]},
+                         {"failed_claims": [{"claim_id": "C-MECH", "claim_text": "So Z.",
+                                             "epistemic_type": "mechanism", "grounding_status": "unsupported"}]}]}},
+        "editorial_briefing": {"mode": "checked_analysis", "case_count": 0, "sentences_removed_by_check": 4,
+                               "implications": [{"text": "Taskforce will recommend windows.", "novelty": "beyond_headlines"}],
+                               "expected_implications": [{"text": "Terms diverge.", "novelty_reasons": ["already_reported"]}],
+                               "analogies": [], "expert_commentary": []},
+        "actor_moves": {"status": "checked", "removed": ["OpenAI leverage not supported: steer the taskforce"],
+                        "actors": [{"actor": "OpenAI", "moves": [{"move": "Explain the delay.", "basis": "incentive"}],
+                                    "expected_moves": [{"move": "Apologise."}],
+                                    "rejected": [{"move": "Argue a technicality.", "why_not": "Ignores EU fines."}]}]},
+    }
+
+
+def rows(stage, unit="candidates"):
+    return [(r["id"], r["outcome"]) for r in stage[unit]]
+
+
+def test_one_candidate_row_per_distinct_id_with_events_separate():
+    result = funnel.build(sections())
+    lab_stage = result["stages"]["hypothesis_lab"]
+    assert rows(lab_stage) == [("EV1-N", "passed"), ("EV1", "repaired"), ("EV3", "rejected")]
+    assert result["tally"]["hypothesis_lab"]["distinct_candidate_ids"] == 3
+    assert [r["id"] for r in lab_stage["events"]] == ["EV1", "EV3"]
+    ev3 = lab_stage["candidates"][2]
+    assert "mechanism=partially_supported" in ev3["reason"] and "blocked:absolute" in ev3["reason"]
+    assert "rewritten from EV1, unscored" in lab_stage["candidates"][0]["reason"]
+
+
+def test_display_states_are_their_own_unit():
+    lab_stage = funnel.build(sections())["stages"]["hypothesis_lab"]
+    assert ("EV3", "collapsed") in rows(lab_stage, "display")
+    assert ("INSIGHT-1", "collapsed") in rows(lab_stage, "display")
+    assert ("EV1-N", "shown") in rows(lab_stage, "display")
+    data = sections()
+    data["strategic_hypotheses"]["verified"] = []
+    states = {o for _, o in rows(funnel.build(data)["stages"]["hypothesis_lab"], "display")}
+    assert states == {"shown"}
+
+
+def test_attempts_claims_and_sentences_are_counted_in_their_own_units():
+    result = funnel.build(sections())
+    gate = result["tally"]["claim_support_gate"]
+    assert gate["attempts"] == 2 and gate["failed_claims"] == 2 and gate["candidates"] == {"rejected": 1}
+    brief = result["tally"]["editorial_briefing"]
+    assert brief["sentences_pruned"] == 4 and brief["candidates"] == {"passed": 1, "downgraded": 1}
+    moves = result["tally"]["actor_moves"]
+    assert moves["candidates"] == {"passed": 1, "downgraded": 1, "rejected": 1} and moves["failed_claims"] == 1
+
+
+def test_missing_records_say_not_recorded_rather_than_zero():
+    data = sections()
+    del data["strategic_hypotheses"]["funnel"]
+    del data["editorial_briefing"]["sentences_removed_by_check"]
+    del data["analysis_metadata"]["claim_support_gate"]["attempts"]
+    result = funnel.build(data)
+    assert result["stages"]["hypothesis_lab"]["losses_before_grounding"] == funnel.NOT_RECORDED
+    assert result["stages"]["editorial_briefing"]["sentences_pruned"] == funnel.NOT_RECORDED
+    assert result["stages"]["claim_support_gate"]["attempts"] == funnel.NOT_RECORDED
+    assert "attempts" not in result["tally"]["claim_support_gate"]
+    empty = funnel.build({})
+    assert empty["stages"]["editorial_briefing"]["mode"] == funnel.NOT_RECORDED
+
+
+def test_summary_lists_rejected_candidates_only():
+    summary = funnel.summary(funnel.build(sections()))
+    assert summary["hypothesis_lab"]["rejected_ids"] == ["EV3"]
+    assert summary["claim_support_gate"]["rejected_ids"] == ["INSIGHT-1"]
+
+
+def row(n, **extra):
+    base = {"thesis": f"Distinct hypothesis number {n} about a shifted dependency between two actors.",
+            "mechanism": "A -> B -> C", "novel_proposition": "p", "why_not_obvious": "w",
+            "alternative_explanation": "a", "falsifier": "f", "watch_signal": "s", "fact_ids": ["F1"]}
+    base.update(extra)
+    return base
+
+
+FACTS = [{"fact_id": "F1", "text": "A fact."}]
+
+
+def test_lab_parsers_count_their_drops():
+    stats = {}
+    reply = {"hypotheses": [row(1), row(1), {"thesis": "missing fields"}, "not a row"]}
+    assert len(lab.parse_generation(reply, FACTS, stats=stats)) == 1
+    assert stats == {"generated_rows": 4, "generated_invalid": 2, "generated_duplicates": 1}
+    scores = {k: 2 for k in ("mental_model_change", "mechanism_depth", "surprise_nonobviousness",
+                             "strategic_consequence", "falsifiability")}
+    low = {k: 1 for k in scores}
+    stats = {}
+    evolved = lab.parse_evolution({"hypotheses": [row(2, critic_scores=scores), row(3, critic_scores=low)]},
+                                  FACTS, stats=stats)
+    assert len(evolved) == 1 and stats["evolution_low_critic"] == 1 and stats["evolution_rows"] == 2
+
+
+def test_lab_parsers_unchanged_without_stats():
+    assert len(lab.parse_generation({"hypotheses": [row(1)]}, FACTS)) == 1
--- /dev/null
+++ b/tests/test_plan_fixes_a_c_d.py
@@ -0,0 +1,124 @@
+"""Plan fixes A, C, D (docs/PLAN_next_two_weeks.md, 23 Oct 2026)."""
+import json
+from datetime import datetime, timezone
+
+from services import strategic_hypothesis_lab as lab
+
+FACTS = [{"fact_id": "F7", "text": "Albanese said OpenAI took three months to alert the government."},
+         {"fact_id": "F18", "text": "Australia set up a taskforce to examine AI incident reporting."},
+         {"fact_id": "H1", "text": "Capital One disclosed its 2019 breach ten days after a tip."}]
+
+
+# A. Bare fact-ID basis --------------------------------------------------------
+
+def test_basis_of_only_fact_ids_is_resolved_to_fact_text():
+    item = lab.resolve_basis_refs({"actor_move_basis": "F7", "countermove_basis": "F18 and H1."}, FACTS)
+    assert item["actor_move_basis"] == "F7: " + FACTS[0]["text"]
+    assert item["countermove_basis"].startswith("F18: Australia") and "; H1: Capital One" in item["countermove_basis"]
+    assert item["actor_move_basis_as_written"] == "F7" and "unresolved_fact_ids" not in item
+
+
+def test_prose_citing_fact_ids_is_unchanged():
+    text = "President Trump announced the rename in official communications (F3, F5)."
+    assert lab.resolve_basis_refs({"actor_move_basis": text}, FACTS)["actor_move_basis"] == text
+
+
+def test_unknown_fact_id_is_flagged_not_dropped_and_not_graded_unsupported():
+    item = lab.resolve_basis_refs({"id": "X", "thesis": "t", "mechanism": "m",
+                                   "likely_actor_move": "OpenAI explains the delay.",
+                                   "actor_move_basis": "F7, F99"}, FACTS)
+    assert item["unresolved_fact_ids"] == {"actor_move_basis": ["F99"]}
+    assert item["actor_move_basis_as_written"] == "F7, F99"
+    claims = lab.grounding_claims(item, ["E1"])
+    assert not any(c["claim_id"].endswith("-MOVEBASIS") for c in claims)
+    grounded = [{"claim_id": c["claim_id"], "grounding_status": "supported"} for c in claims]
+    assert lab.finalize(item, grounded, FACTS)["field_checks"]["actor_move_basis"] == "not_checked"
+
+
+def test_resolved_basis_is_what_gets_grounded():
+    item = lab.resolve_basis_refs({"id": "X", "thesis": "t", "mechanism": "m",
+                                   "likely_actor_move": "OpenAI explains the delay.", "actor_move_basis": "F7"}, FACTS)
+    basis = next(c for c in lab.grounding_claims(item, ["E1"]) if c["claim_id"].endswith("-MOVEBASIS"))
+    assert basis["claim_text"] == "F7: " + FACTS[0]["text"] and basis["epistemic_type"] == "fact"
+
+
+def test_basis_citing_a_fact_outside_fact_ids_carries_that_facts_evidence():
+    # GPT review: basis "F18" on a candidate whose fact_ids are only F7 was grounded on F7's source.
+    facts = [{"fact_id": "F7", "text": FACTS[0]["text"], "evidence_id": "EVID-F7"},
+             {"fact_id": "F18", "text": FACTS[1]["text"], "evidence_id": "EVID-F18"}]
+    item = lab.resolve_basis_refs({"id": "X", "thesis": "t", "mechanism": "m", "fact_ids": ["F7"],
+                                   "likely_countermove": "Taskforce reviews reporting.",
+                                   "countermove_basis": "F18"}, facts)
+    assert item["basis_fact_ids"] == {"countermove_basis": ["F18"]}
+    seed = lab.evidence_ids_for_fact_ids(facts, item["fact_ids"])
+    basis = {f: lab.evidence_ids_for_fact_ids(facts, ids) for f, ids in item["basis_fact_ids"].items()}
+    claims = {c["claim_id"].rsplit("-", 1)[-1]: c for c in lab.grounding_claims(item, seed, basis_evidence_ids=basis)}
+    assert claims["COUNTERBASIS"]["evidence_ids"] == ["EVID-F7", "EVID-F18"]
+    assert claims["THESIS"]["evidence_ids"] == ["EVID-F7"]   # the thesis is not widened
+
+
+# D. Inherited critic scores ----------------------------------------------------
+
+def narrowed():
+    original = {"id": "EV1", "thesis": "Original overstated claim about paralysis.", "mechanism": "A -> B",
+                "fact_ids": ["F7"], "critic_scores": {"mental_model_change": 2}, "critic_total": 10,
+                "status": "promising_unverified", "actor_move_basis": "F7: text",
+                "actor_move_basis_as_written": "F7"}
+    reply = {"thesis": "The rename and the bill use conflicting terms across branches.",
+             "mechanism": "Rename -> bill -> conflicting terms", "fact_ids": ["F7"], "what_was_narrowed": "cut paralysis"}
+    return lab.parse_narrow(reply, original, FACTS)
+
+
+def test_narrowed_rewrite_is_unscored_with_original_score_labelled():
+    item = narrowed()
+    assert "critic_total" not in item and "critic_scores" not in item
+    assert item["critic_inherited_from"] == "EV1" and item["critic_total_before_narrowing"] == 10
+    assert "actor_move_basis_as_written" not in item
+
+
+def test_unscored_item_sorts_at_the_median_not_last():
+    items = [{"id": "A", "critic_total": 10}, {"id": "B", "critic_total": 8},
+             {"id": "N", "critic_inherited_from": "X"}, {"id": "C", "critic_total": 7}]
+    assert [x["id"] for x in lab.sort_by_critic(items)] == ["A", "B", "N", "C"]
+    assert [x["id"] for x in lab.sort_by_critic([{"id": "N"}])] == ["N"]
+
+
+# C. Expert view definition -----------------------------------------------------
+
+def _review(records):
+    from services.hypothesis_evidence_service import HypothesisEvidenceService
+    prompts = []
+
+    class Model:
+        model_name = "m"
+        timeout_seconds = 30
+
+        def ask(self, prompt, **kwargs):
+            prompts.append(prompt)
+            return json.dumps({"review_complete": True, "records": records})
+
+    body = ("Experts warn the incident is another canary in the coal mine, said Dr Lee of the Lowy Institute. "
+            "Albanese said he expressed extreme concern to OpenAI.")
+    service = HypothesisEvidenceService(web_search_service=None, source_fetch_service=None, llm_service=Model())
+    out = service._process_source(source={"url": "https://x.example/a", "text_content": body,
+                                          "published_at": "2026-09-24T00:00:00Z"},
+                                  context={}, candidate={"insight": "i"}, now=datetime(2026, 10, 9, tzinfo=timezone.utc),
+                                  timeout=10, enforce_window=False)
+    return prompts[0], out["accepted_evidence"]
+
+
+def test_review_prompt_defines_independent_expert_views():
+    prompt, _ = _review([])
+    assert "NOT a party to the events" in prompt and "reported_claim" in prompt
+    assert "credibility of the website does not change this" in prompt
+
+
+def test_expert_and_party_statements_keep_their_claim_kind():
+    # The model's classification is carried unchanged; investigate_gaps maps analysis -> commentary,
+    # reported_claim -> reported. (This proves the contract, not that the model follows it.)
+    _, records = _review([
+        {"quote": "Experts warn the incident is another canary in the coal mine, said Dr Lee of the Lowy Institute.",
+         "relation": "context", "claim_kind": "analysis", "connection": "c"},
+        {"quote": "Albanese said he expressed extreme concern to OpenAI.",
+         "relation": "context", "claim_kind": "reported_claim", "connection": "c"}])
+    assert [r["structured_payload"]["claim_kind"] for r in records] == ["analysis", "reported_claim"]
```
