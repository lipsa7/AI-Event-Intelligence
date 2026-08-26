from __future__ import annotations

import email.utils
import hashlib
import importlib
import json
import os
import re
import inspect
import logging
import time
import uuid

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence
from urllib.parse import urlparse


logger = logging.getLogger(__name__)

SERVICE_VERSION = "research-orchestrator-service-v3.26.3-tiered-strategic-findings-provider-circuit-breaker"
PATCH_BUILD_ID = "ORCH-V3263-20260826-1325-PROVIDER-CIRCUIT-BREAKER"


# Deep-mode portfolio reliability controls.
# The initial pass intentionally stays small; targeted evidence discovery can
# still add corroboration later for the single surviving insight candidate.
DEEP_MIN_USABLE_INITIAL_SOURCES = 4
DEEP_TARGET_USABLE_INITIAL_SOURCES = 6
DEEP_MAX_FETCHED_TEXT_CHARACTERS = 35_000
DEEP_SUBSTITUTION_QUERY_COUNT = 4

# V3.12: a promising Deep candidate should not be rejected for an evidence gap
# until the system has spent a small, explicit search budget on that gap.
DEEP_MAX_STRATEGIC_EVIDENCE_RESCUE_ROUNDS = 2
DEEP_STRATEGIC_RESCUE_MAX_SOURCES_PER_ROUND = 3
DEEP_STRATEGIC_RESCUE_MIN_INVESTIGATION_SCORE = 60.0

# V3.25: Deep mode must first assemble a minimally researchable strategic
# corpus. A poor first discovery pass is a retrieval problem, not evidence that
# an information-rich topic has no events. Corpus refill is deliberately
# upstream of cluster expansion and StrategicInference.
DEEP_CORPUS_REFILL_MAX_ROUNDS = 2
DEEP_CORPUS_REFILL_MAX_QUERIES_PER_ROUND = 5
DEEP_CORPUS_REFILL_MAX_SOURCES_PER_ROUND = 4
DEEP_CORPUS_MIN_STRATEGIC_EVENTS = 5
DEEP_CORPUS_MIN_UNDERLYING_EVENTS = 4
DEEP_CORPUS_MIN_SOURCE_COUNT = 3
DEEP_CORPUS_MIN_COHERENT_CLUSTER_EVENTS = 2

# V3.24+: Deep cluster expansion happens BEFORE StrategicInference. It is not
# candidate rescue. The goal is to research promising event clusters until a
# bounded material-information saturation point, so inference starts from a
# richer event sequence rather than the first two headlines found.
DEEP_CLUSTER_EXPANSION_MAX_CLUSTERS = 2
DEEP_CLUSTER_EXPANSION_MAX_ROUNDS_PER_CLUSTER = 2
DEEP_CLUSTER_EXPANSION_MAX_QUERIES_PER_ROUND = 3
DEEP_CLUSTER_EXPANSION_MAX_SOURCES_PER_ROUND = 3
DEEP_CLUSTER_EXPANSION_MIN_RESEARCH_SCORE = 55.0

STRATEGIC_EVIDENCE_RESCUE_REASONS = {
    "fewer_than_two_supporting_evidence_records",
    "fewer_than_two_derivation_observations",
    "fewer_than_two_evidence_backed_premises",
    "support_not_cross_source",
    "cross_entity_pattern_requires_multiple_actors",
    "cross_entity_pattern_requires_two_distinct_acting_institutions",
    "cross_entity_pattern_requires_multiple_events",
    "cross_entity_pattern_requires_multiple_source_groups",
    "sector_pattern_requires_three_distinct_actors",
    "sector_pattern_requires_three_distinct_acting_institutions",
    "sector_pattern_requires_three_distinct_events",
    "sector_pattern_requires_multiple_source_groups",
    "sector_pattern_requires_non_focal_corroboration",
    "comparative_claim_requires_comparative_evidence",
    "cross_entity_pattern_requires_two_distinct_acting_institutions",
    "sector_pattern_requires_three_distinct_acting_institutions",
}


@dataclass(frozen=True)
class ResearchExecutionProfile:
    name: str
    max_runtime_seconds: int
    max_discovered_sources: int
    max_selected_insights: int
    max_refinement_rounds: int
    semantic_fact_extraction: bool
    semantic_fail_closed: bool
    use_external_evidence_discovery: bool


FAST_PROFILE = ResearchExecutionProfile(
    name="fast",
    max_runtime_seconds=360,
    max_discovered_sources=3,
    max_selected_insights=2,
    max_refinement_rounds=0,
    semantic_fact_extraction=False,
    semantic_fail_closed=False,
    use_external_evidence_discovery=False,
)

DEEP_PROFILE = ResearchExecutionProfile(
    name="deep",
    max_runtime_seconds=1200,
    max_discovered_sources=8,
    max_selected_insights=1,
    # MVP: one refinement round is enough to reach and inspect the real
    # strategic-inference path quickly. Restore to 2 after the insight layer
    # is producing useful outputs reliably.
    max_refinement_rounds=1,
    # Deterministic event/source evidence remains authoritative. The optional
    # semantic evidence enrichment repeatedly consumed the full local-model
    # timeout and then failed closed, so skip it for the Deep MVP.
    semantic_fact_extraction=False,
    semantic_fail_closed=False,
    # Keep targeted external evidence discovery: this is still Deep research.
    use_external_evidence_discovery=True,
)

EXECUTION_PROFILES = {
    "fast": FAST_PROFILE,
    "deep": DEEP_PROFILE,
}


class ResearchOrchestratorError(RuntimeError):
    pass


class ResearchPipelineError(ResearchOrchestratorError):
    pass


class ResearchConfigurationError(ResearchOrchestratorError):
    pass


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _normalize_source_text_preserve_boundaries(value: Any) -> str:
    """Normalize source prose without destroying block/sentence boundaries.

    `_clean()` is correct for labels and metadata but must not be applied to an
    entire fetched article because it flattens newlines and can glue unrelated
    teaser/headline blocks into one pseudo-sentence.
    """
    if value is None:
        return ""
    raw = str(value).replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    last = ""
    for line in raw.split("\n"):
        normalized = " ".join(line.split()).strip()
        if not normalized:
            continue
        if normalized == last:
            continue
        lines.append(normalized)
        last = normalized
    return "\n".join(lines).strip()


def _enum(value: Any) -> str:
    return (
        _clean(value)
        .casefold()
        .replace("-", "_")
        .replace(" ", "_")
    )


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(
        value,
        (
            str,
            int,
            float,
            bool,
        ),
    ):
        return value

    if is_dataclass(value):
        return _json_safe(
            asdict(value)
        )

    if isinstance(
        value,
        Mapping,
    ):
        return {
            str(key): _json_safe(item)
            for key, item
            in value.items()
        }

    if isinstance(
        value,
        (
            list,
            tuple,
            set,
        ),
    ):
        return [
            _json_safe(item)
            for item in value
        ]

    if hasattr(
        value,
        "to_dict",
    ):
        try:
            return _json_safe(
                value.to_dict()
            )
        except Exception:
            pass

    if hasattr(
        value,
        "__dict__",
    ):
        try:
            return {
                str(key): _json_safe(item)
                for key, item
                in vars(value).items()
                if not str(key).startswith("_")
            }
        except Exception:
            pass

    return str(value)


def _as_mapping(
    value: Any,
) -> dict[str, Any]:
    normalized = _json_safe(
        value
    )

    if isinstance(
        normalized,
        Mapping,
    ):
        return dict(
            normalized
        )

    return {}


def _as_list(
    value: Any,
) -> list[Any]:
    if value is None:
        return []

    if isinstance(
        value,
        list,
    ):
        return value

    if isinstance(
        value,
        tuple,
    ):
        return list(
            value
        )

    return [
        value
    ]


def _unique_strings(
    values: Iterable[Any],
) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()

    for value in values:
        text = _clean(
            value
        )

        if (
            text
            and text not in seen
        ):
            seen.add(
                text
            )

            output.append(
                text
            )

    return output


def _new_run_id() -> str:
    return (
        "RUN-"
        + uuid.uuid4()
        .hex[:24]
        .upper()
    )


class ResearchOrchestratorService:

    def __init__(
        self,
        *,
        article_service: Any = None,
        event_service: Any = None,
        entity_service: Any = None,
        entity_resolution_service: Any = None,
        relationship_service: Any = None,
        graph_service: Any = None,
        vector_service: Any = None,
        web_search_service: Any = None,
        source_fetch_service: Any = None,
        source_provenance_service: Any = None,
        reasoning_context_service: Any = None,
        report_service: Any = None,
        insight_quality_service: Any = None,
        insight_registry_service: Any = None,
        insight_trace_service: Any = None,
        policy_registry: Any = None,
        blueprint_compiler: Any = None,
        policy_enforcement_service: Any = None,
        execution_budget_service: Any = None,
        telemetry_service: Any = None,
        audit_receipt_service: Any = None,
        extraction_agent: Any = None,
        evidence_builder_agent: Any = None,
        constraint_agent: Any = None,
        strategic_inference_agent: Any = None,
        grounding_service: Any = None,
        red_team_agent: Any = None,
        evidence_discovery_agent: Any = None,
    ) -> None:

        self.article_service = (
            article_service
            or self._construct(
                "services.article_service",
                "ArticleService",
            )
        )

        self.event_service = (
            event_service
            or self._construct(
                "services.event_service",
                "EventService",
            )
        )

        self.entity_service = (
            entity_service
            or self._construct(
                "services.entity_service",
                "EntityService",
            )
        )

        self.entity_resolution_service = (
            entity_resolution_service
            or self._construct_optional(
                "services.entity_resolution_service",
                "EntityResolutionService",
            )
        )

        self.relationship_service = (
            relationship_service
            or self._construct(
                "services.relationship_service",
                "RelationshipService",
            )
        )

        self.graph_service = (
            graph_service
            or self._construct(
                "services.graph_service",
                "GraphService",
            )
        )

        self.vector_service = (
            vector_service
            or self._construct_optional(
                "services.vector_service",
                "VectorService",
            )
        )

        self.web_search_service = (
            web_search_service
            if web_search_service is not None
            else self._construct_web_search_service_optional()
        )

        self.source_fetch_service = (
            source_fetch_service
            or self._construct_optional(
                "services.source_fetch_service",
                "SourceFetchService",
            )
        )

        self.source_provenance_service = (
            source_provenance_service
            or self._construct_optional(
                "services.source_provenance_service",
                "SourceProvenanceService",
            )
        )

        self.reasoning_context_service = (
            reasoning_context_service
            or self._construct_optional(
                "services.reasoning_context_service",
                "ReasoningContextService",
            )
        )

        self.report_service = (
            report_service
            or self._construct_optional(
                "services.report_service",
                "ReportService",
            )
        )

        self.insight_quality_service = (
            insight_quality_service
            or self._construct(
                "services.insight_quality_service",
                "InsightQualityService",
            )
        )

        self.insight_registry_service = (
            insight_registry_service
            or self._construct_optional(
                "services.insight_registry_service",
                "InsightRegistryService",
            )
        )

        self.insight_trace_service = (
            insight_trace_service
            or self._construct_optional(
                "services.insight_trace_service",
                "InsightTraceService",
            )
        )

        self.policy_registry = (
            policy_registry
            or self._construct_optional(
                "services.policy_registry",
                "PolicyRegistryService",
            )
        )

        self.blueprint_compiler = (
            blueprint_compiler
            or self._construct_optional(
                "services.blueprint_compiler",
                "BlueprintCompiler",
            )
        )

        self.policy_enforcement_service = (
            policy_enforcement_service
            or self._construct_optional(
                "services.policy_enforcement_service",
                "PolicyEnforcementService",
            )
        )

        self.execution_budget_service = (
            execution_budget_service
            or self._construct_optional(
                "services.execution_budget_service",
                "ExecutionBudgetService",
            )
        )

        self.telemetry_service = (
            telemetry_service
            or self._construct_optional(
                "services.telemetry_service",
                "TelemetryService",
            )
        )

        self.audit_receipt_service = (
            audit_receipt_service
            or self._construct_optional(
                "services.audit_receipt_service",
                "AuditReceiptService",
            )
        )

        self.extraction_agent = (
            extraction_agent
            or self._construct(
                "agents.extraction_agent",
                "ExtractionAgent",
            )
        )

        self.evidence_builder_agent = (
            evidence_builder_agent
            or self._construct(
                "agents.evidence_builder_agent",
                "EvidenceBuilderAgent",
            )
        )

        self.constraint_agent = (
            constraint_agent
            or self._construct(
                "agents.constraint_agent",
                "ConstraintAgent",
            )
        )

        self.strategic_inference_agent = (
            strategic_inference_agent
            or self._construct(
                "agents.strategic_inference_agent",
                "StrategicInferenceAgent",
            )
        )

        self.grounding_service = (
            grounding_service
            or self._construct_grounding_service()
        )

        self.red_team_agent = (
            red_team_agent
            or self._construct(
                "agents.red_team_agent",
                "RedTeamAgent",
            )
        )

        self.evidence_discovery_agent = (
            evidence_discovery_agent
            or self._construct_optional(
                "agents.evidence_discovery_agent",
                "EvidenceDiscoveryAgent",
            )
        )

    def run(
        self,
        topic: str,
        *,
        research_depth: str = "fast",
        source_ids: Optional[
            Sequence[str]
        ] = None,
        max_sources: Optional[int] = None,
    ) -> dict[str, Any]:

        started_at = (
            time.perf_counter()
        )

        run_id = (
            _new_run_id()
        )

        topic = (
            _clean(topic)
        )

        if not topic:
            raise ResearchConfigurationError(
                "topic cannot be empty."
            )

        profile = (
            self._resolve_profile(
                research_depth
            )
        )

        source_limit = (
            int(max_sources)
            if max_sources is not None
            else profile.max_discovered_sources
        )

        if source_limit <= 0:
            raise ResearchConfigurationError(
                "max_sources must be > 0."
            )

        requested_depth = _enum(research_depth)
        state: dict[str, Any] = {
            "run_id": run_id,
            "topic": topic,
            "requested_research_depth": requested_depth,
            "research_depth": profile.name,
            "service_version": SERVICE_VERSION,
            "patch_build_id": PATCH_BUILD_ID,
            "stage_status": {},
            "warnings": [],
            "errors": [],
            "depth_integrity": {
                "requested": requested_depth,
                "resolved_profile": profile.name,
                "extraction": None,
                "strategic": None,
                "ok": False,
            },
        }

        if requested_depth != profile.name:
            raise ResearchConfigurationError(
                "Depth routing mismatch before execution: "
                f"requested={requested_depth!r} resolved={profile.name!r}."
            )

        logger.info(
            (
                "Research run started. "
                "run_id=%s topic=%s requested_depth=%s resolved_depth=%s"
            ),
            run_id,
            topic,
            requested_depth,
            profile.name,
        )

        try:
            blueprint = (
                self._compile_blueprint(
                    topic=topic,
                    profile=profile,
                    run_id=run_id,
                )
            )
            blueprint = self._bind_blueprint_to_profile(
                blueprint,
                profile=profile,
            )

            state[
                "blueprint"
            ] = blueprint
            state["depth_integrity"]["blueprint_reasoning_depth"] = (
                _enum(
                    _as_mapping(blueprint.get("reasoning_policy")).get(
                        "desired_depth"
                    )
                )
            )

            self._stage_ok(
                state,
                "control_plane",
            )

            if source_ids:
                selected_source_ids = (
                    _unique_strings(
                        source_ids
                    )
                )
            else:
                selected_source_ids = (
                    self._discover_sources(
                        topic=topic,
                        max_sources=(
                            source_limit
                        ),
                        state=state,
                    )
                )

            if not selected_source_ids:
                raise ResearchPipelineError(
                    (
                        "No persisted SOURCE-* "
                        "records are available "
                        "for research."
                    )
                )

            state[
                "source_ids"
            ] = selected_source_ids

            self._stage_ok(
                state,
                "source_discovery",
                count=len(
                    selected_source_ids
                ),
            )

            event_records = (
                self._extract_events(
                    source_ids=(
                        selected_source_ids
                    ),
                    state=state,
                )
            )

            if profile.name == "fast":
                selected_source_ids, event_records = self._fast_recover_minimum_events(
                    topic=topic,
                    selected_source_ids=selected_source_ids,
                    event_records=event_records,
                    state=state,
                )
                state["source_ids"] = list(selected_source_ids)

            elif profile.name == "deep" and not event_records:
                selected_source_ids, event_records = self._deep_bootstrap_zero_events(
                    topic=topic,
                    selected_source_ids=selected_source_ids,
                    state=state,
                )
                state["source_ids"] = list(selected_source_ids)

            if not event_records:
                if profile.name == "deep":
                    bootstrap = _as_mapping(state.get("deep_zero_event_bootstrap"))
                    raise ResearchPipelineError(
                        (
                            "No structured EVENT-* records were extracted from the "
                            "initial Deep sources or from the bounded zero-event "
                            "bootstrap search. "
                            f"bootstrap_status={_clean(bootstrap.get('status')) or 'unknown'}."
                        )
                    )

                raise ResearchPipelineError(
                    (
                        "No structured EVENT-* "
                        "records were extracted "
                        "from the sources."
                    )
                )

            state[
                "events"
            ] = event_records

            state[
                "event_ids"
            ] = [
                event[
                    "event_id"
                ]
                for event
                in event_records
            ]

            self._stage_ok(
                state,
                "event_extraction",
                count=len(
                    event_records
                ),
            )

            self._index_events(
                event_records,
                state,
            )

            source_payloads = (
                self._source_payloads(
                    selected_source_ids
                )
            )

            evidence_payload = (
                self.evidence_builder_agent
                .build_evidence(
                    topic=topic,
                    events=event_records,
                    sources=source_payloads,
                    semantic_fact_extraction=(
                        profile
                        .semantic_fact_extraction
                    ),
                    semantic_fail_closed=(
                        profile
                        .semantic_fail_closed
                    ),
                )
            )

            evidence_payload = (
                _as_mapping(
                    evidence_payload
                )
            )

            evidence_records = list(
                evidence_payload.get(
                    "evidence_records",
                    [],
                )
                or []
            )

            if not evidence_records:
                raise ResearchPipelineError(
                    (
                        "EvidenceBuilderAgent "
                        "admitted no EVID-* records."
                    )
                )

            self._validate_evidence_currency(
                evidence_records
            )

            event_records, underlying_event_diagnostics = self._annotate_underlying_events(
                events=event_records,
                topic=topic,
            )
            state["events"] = list(event_records)
            state["underlying_event_diagnostics"] = _json_safe(underlying_event_diagnostics)

            state[
                "evidence"
            ] = evidence_records

            state[
                "evidence_manifest"
            ] = (
                self._evidence_manifest(
                    evidence_records
                )
            )

            # V3.17: preserve the complete admitted extraction/evidence set for
            # diagnostics, then form the smaller event set that is actually
            # allowed to shape relationships, graph context and strategic
            # reasoning. Fast mode keeps its existing bounded summarization path.
            admitted_event_records = [dict(item) for item in event_records]
            admitted_evidence_records = [dict(item) for item in evidence_records]
            state["admitted_events_before_strategic_filter"] = _json_safe(
                admitted_event_records
            )
            state["admitted_evidence_before_strategic_filter"] = _json_safe(
                admitted_evidence_records
            )
            if profile.name != "fast":
                (
                    event_records,
                    evidence_records,
                    strategic_event_admission,
                ) = self._prepare_strategic_event_context(
                    topic=topic,
                    events=event_records,
                    evidence=evidence_records,
                )
                state["strategic_event_admission"] = _json_safe(
                    strategic_event_admission
                )
                state["events"] = list(event_records)
                state["event_ids"] = [
                    _clean(item.get("event_id"))
                    for item in event_records
                    if isinstance(item, Mapping) and _clean(item.get("event_id"))
                ]
                state["evidence"] = list(evidence_records)
                state["evidence_manifest"] = self._evidence_manifest(
                    evidence_records
                )

                # V3.25: a weak initial corpus is a retrieval problem, not a
                # reason to immediately infer/abstain. Deep mode first refills the
                # strategic corpus, then V3.24 expands the best clusters, and only
                # after those two research stages does StrategicInference run.
                if profile.name == "deep":
                    state["strategic_initial_event_admission"] = _json_safe(
                        strategic_event_admission
                    )
                    corpus_changed = False
                    corpus_refill = self._refill_strategic_corpus(
                        topic=topic,
                        selected_source_ids=selected_source_ids,
                        event_records=event_records,
                        evidence_records=evidence_records,
                        state=state,
                    )
                    selected_source_ids = list(
                        corpus_refill.get("selected_source_ids") or selected_source_ids
                    )
                    event_records = list(
                        corpus_refill.get("event_records") or event_records
                    )
                    evidence_records = list(
                        corpus_refill.get("evidence_records") or evidence_records
                    )
                    corpus_changed = bool(
                        int(corpus_refill.get("new_material_event_count") or 0) > 0
                    )

                    cluster_expansion = self._expand_promising_strategic_clusters(
                        topic=topic,
                        selected_source_ids=selected_source_ids,
                        event_records=event_records,
                        evidence_records=evidence_records,
                        state=state,
                    )
                    selected_source_ids = list(
                        cluster_expansion.get("selected_source_ids") or selected_source_ids
                    )
                    event_records = list(
                        cluster_expansion.get("event_records") or event_records
                    )
                    evidence_records = list(
                        cluster_expansion.get("evidence_records") or evidence_records
                    )
                    corpus_changed = bool(
                        corpus_changed
                        or int(cluster_expansion.get("new_material_event_count") or 0) > 0
                    )

                    # Rebuild exactly once from the final pre-inference corpus so
                    # relationships, graph projection and StrategicInference see
                    # the same event/EVID universe after refill + expansion.
                    if corpus_changed:
                        source_payloads = self._source_payloads(selected_source_ids)
                        evidence_payload = _as_mapping(
                            self.evidence_builder_agent.build_evidence(
                                topic=topic,
                                events=event_records,
                                sources=source_payloads,
                                semantic_fact_extraction=False,
                                semantic_fail_closed=False,
                            )
                        )
                        rebuilt_evidence = [
                            dict(item)
                            for item in (evidence_payload.get("evidence_records") or [])
                            if isinstance(item, Mapping)
                            and _clean(item.get("evidence_id") or item.get("id"))
                        ]
                        self._validate_evidence_currency(rebuilt_evidence)
                        event_records, evidence_records, final_expansion_admission = (
                            self._prepare_strategic_event_context(
                                topic=topic,
                                events=event_records,
                                evidence=rebuilt_evidence,
                            )
                        )
                        state["strategic_post_expansion_event_admission"] = _json_safe(
                            final_expansion_admission
                        )
                        state["strategic_event_admission"] = _json_safe(
                            final_expansion_admission
                        )

                    final_corpus_quality = self._strategic_corpus_sufficiency_assessment(
                        topic=topic,
                        events=event_records,
                        evidence=evidence_records,
                    )
                    state["strategic_corpus_quality_before_inference"] = _json_safe(
                        final_corpus_quality
                    )
                    logger.info(
                        "strategic_corpus_quality_before_inference=%s",
                        json.dumps(
                            _json_safe(final_corpus_quality),
                            ensure_ascii=False,
                            default=str,
                        ),
                    )
                    state["source_ids"] = list(selected_source_ids)
                    state["events"] = list(event_records)
                    state["event_ids"] = [
                        _clean(item.get("event_id"))
                        for item in event_records
                        if isinstance(item, Mapping) and _clean(item.get("event_id"))
                    ]
                    state["evidence"] = list(evidence_records)
                    state["evidence_manifest"] = self._evidence_manifest(evidence_records)

            self._stage_ok(
                state,
                "evidence_construction",
                count=len(
                    evidence_records
                ),
                admitted_event_count=len(admitted_event_records),
                admitted_evidence_count=len(admitted_evidence_records),
                strategic_considered_event_count=len(event_records),
                strategic_considered_evidence_count=len(evidence_records),
            )

            if (
                profile.name
                == "fast"
            ):
                relationship_diagnostics = {
                    "status": (
                        "fast_skipped_rich_"
                        "relationship_projection"
                    ),
                    "relationship_count": 0,
                }

                state[
                    "relationship_diagnostics"
                ] = (
                    relationship_diagnostics
                )

                self._stage_ok(
                    state,
                    "relationships",
                    mode="fast_skipped",
                )

            else:
                relationship_diagnostics = (
                    self._build_relationships(
                        events=event_records,
                        evidence_payload=(
                            evidence_payload
                        ),
                        state=state,
                    )
                )

                state[
                    "relationship_diagnostics"
                ] = (
                    relationship_diagnostics
                )

                self._stage_ok(
                    state,
                    "relationships",
                    mode="deep_full",
                )

            if (
                profile.name
                == "fast"
            ):
                graph_context = {}

                state[
                    "graph_context"
                ] = graph_context

                self._stage_ok(
                    state,
                    "graph_projection",
                    mode=(
                        "fast_skipped_non_"
                        "authoritative_graph"
                    ),
                )

            else:
                graph_context = (
                    self._rebuild_graph(
                        state=state
                    )
                )

                state[
                    "graph_context"
                ] = graph_context

                self._stage_ok(
                    state,
                    "graph_projection",
                    mode="deep_full",
                )

            # MVP constraint policy:
            # ConstraintAgent is enrichment, not authoritative evidence. On the
            # current local route it repeatedly consumed the full 90-second
            # timeout and then failed closed. Skip that pre-inference call so the
            # run reaches StrategicInferenceAgent quickly. Deep still keeps the
            # graph, evidence discovery, refinement, grounding and release gates.
            constraints = {}

            state[
                "constraints"
            ] = constraints

            self._stage_ok(
                state,
                "constraints",
                mode=(
                    "deep_mvp_skipped_slow_constraint_agent"
                    if profile.name == "deep"
                    else "fast_skipped_agentic_constraint_analysis"
                ),
            )

            if (
                profile.name
                == "deep"
            ):
                state[
                    "warnings"
                ].append(
                    {
                        "type": (
                            "deep_mvp_constraint_analysis_skipped"
                        ),
                        "message": (
                            "ConstraintAgent was skipped for the Deep MVP because "
                            "the local semantic consolidation path repeatedly timed "
                            "out. Strategic inference continues from admitted "
                            "events, evidence and graph context."
                        ),
                    }
                )

            if profile.name != "fast" and not evidence_records:
                return self._complete_abstention(
                    state=state,
                    topic=topic,
                    run_id=run_id,
                    depth=profile.name,
                    events=event_records,
                    evidence=evidence_records,
                    constraints=constraints,
                    graph_context=graph_context,
                    reasons=["no_material_topic_relevant_events_after_admission"],
                    diagnostics=_as_mapping(state.get("strategic_event_admission")),
                    started_at=started_at,
                    stage="strategic_event_admission",
                )

            if (
                profile.name
                == "fast"
            ):
                # First form a bounded hypothesis from the initial evidence.
                provisional_candidates = (
                    self
                    ._fast_evidence_summary_candidates(
                        topic=topic,
                        evidence=evidence_records,
                        events=event_records,
                        maximum=(
                            profile
                            .max_selected_insights
                        ),
                    )
                )

                # Then spend a small bounded search budget trying to falsify or
                # corroborate the strongest thesis. This is the key Fast v2.1
                # change: hidden insights are evidence-expanded before release.
                (
                    selected_source_ids,
                    event_records,
                    evidence_records,
                ) = self._fast_confirm_trend_evidence(
                    topic=topic,
                    provisional_candidates=provisional_candidates,
                    selected_source_ids=selected_source_ids,
                    event_records=event_records,
                    evidence_records=evidence_records,
                    state=state,
                    max_confirmation_sources=3,
                )

                event_records, underlying_event_diagnostics = self._annotate_underlying_events(
                    events=event_records,
                    topic=topic,
                )
                state["underlying_event_diagnostics"] = _json_safe(underlying_event_diagnostics)
                state["source_ids"] = list(selected_source_ids)
                state["events"] = list(event_records)
                state["event_ids"] = [
                    _clean(event.get("event_id"))
                    for event in event_records
                    if isinstance(event, Mapping)
                    and _clean(event.get("event_id"))
                ]
                state["evidence"] = list(evidence_records)
                state["evidence_manifest"] = self._evidence_manifest(
                    evidence_records
                )

                candidates = (
                    self
                    ._fast_evidence_summary_candidates(
                        topic=topic,
                        evidence=evidence_records,
                        events=event_records,
                        maximum=(
                            profile
                            .max_selected_insights
                        ),
                        reason=True,
                    )
                )

                fast_reasoning_metadata = _json_safe(
                    getattr(self, "_last_fast_reasoning_metadata", {})
                )
                inference_result = {
                    "mode": "fast_evidence_conditioned_synthesis",
                    "llm_called": bool(fast_reasoning_metadata.get("llm_called")),
                    "fallback_used": bool(fast_reasoning_metadata.get("fallback_used")),
                    "degraded": bool(fast_reasoning_metadata.get("degraded")),
                    "reasoning_metadata": fast_reasoning_metadata,
                    "confirmation": _json_safe(
                        state.get("fast_trend_confirmation", {})
                    ),
                }

                state[
                    "fast_inference_fallback_used"
                ] = False

                state[
                    "fast_synthesis_mode"
                ] = "bounded_evidence_conditioned_llm"

                state[
                    "fast_strategic_inference_skipped"
                ] = True
                state[
                    "fast_bounded_reasoning_called"
                ] = bool(fast_reasoning_metadata.get("llm_called"))

            else:
                expected_strategic_depth = (
                    "deep"
                    if profile.name == "deep"
                    else "standard"
                )
                state.setdefault("depth_integrity", {})["strategic_expected"] = (
                    expected_strategic_depth
                )
                logger.info(
                    "Depth routing | stage=strategic_inference requested=%s "
                    "profile=%s expected_reasoning_depth=%s",
                    state.get("requested_research_depth"),
                    profile.name,
                    expected_strategic_depth,
                )

                inference_result = (
                    self
                    .strategic_inference_agent
                    .analyze(
                        research_question=(
                            topic
                        ),
                        current_events=(
                            event_records
                        ),
                        evidence=evidence_records,
                        historical_events="",
                        graph_context=(
                            graph_context
                        ),
                        constraints=constraints,
                        blueprint=blueprint,
                        budget_service=(
                            self
                            .execution_budget_service
                        ),
                        execution_stage=(
                            "initial"
                        ),
                    )
                )

                inference_map = _as_mapping(inference_result)
                inference_metadata = _as_mapping(
                    inference_map.get("metadata")
                )
                actual_strategic_depth = _enum(
                    inference_metadata.get("desired_depth")
                )
                state.setdefault("depth_integrity", {})["strategic"] = (
                    actual_strategic_depth
                )

                if (
                    profile.name == "deep"
                    and actual_strategic_depth not in {"deep", "very_deep"}
                ):
                    raise ResearchPipelineError(
                        "Depth integrity failure inside StrategicInferenceAgent: "
                        f"Deep run resolved desired_depth={actual_strategic_depth!r}."
                    )

                state["strategic_connections"] = _json_safe(
                    inference_map.get("connections", []) or []
                )

                candidates = (
                    self._extract_insights(
                        inference_result
                    )
                )

                initial_rejected_diagnostics = [
                    dict(item)
                    for item in (inference_metadata.get("rejected_candidate_diagnostics") or [])
                    if isinstance(item, Mapping)
                ]
                initial_inference_diagnostics = {
                    "candidate_count": len(candidates),
                    "finding_tier_counts": _json_safe(
                        inference_metadata.get("finding_tier_counts") or {}
                    ),
                    "highest_finding_tier": _clean(
                        inference_metadata.get("highest_finding_tier")
                    ),
                    "tiered_candidate_diagnostics": _json_safe(
                        list(inference_metadata.get("tiered_candidate_diagnostics") or [])[-6:]
                    ),
                    "event_count": len(event_records),
                    "evidence_count": len(evidence_records),
                    "source_count": len(selected_source_ids),
                    "support_cluster_count": int(
                        inference_metadata.get("support_cluster_count") or 0
                    ),
                    "candidate_rejection_reasons": list(
                        inference_metadata.get("candidate_rejection_reasons") or []
                    )[:20],
                    "final_candidate_rejection_reasons": list(
                        inference_metadata.get("final_candidate_rejection_reasons") or []
                    )[:20],
                    "last_evidence_linkage_diagnostics": _json_safe(
                        _as_mapping(
                            inference_metadata.get("last_evidence_linkage_diagnostics")
                        )
                    ),
                    "rejected_candidate_diagnostics": _json_safe(
                        initial_rejected_diagnostics[-6:]
                    ),
                    "strategic_models_attempted": list(
                        inference_metadata.get("strategic_models_attempted")
                        or inference_metadata.get("strategic_models_used")
                        or []
                    )[:20],
                    "strategic_model_used": _clean(
                        inference_metadata.get("strategic_model_used")
                    ) or None,
                    "desired_depth": inference_metadata.get("desired_depth"),
                    "strategic_event_admission": _json_safe(
                        state.get("strategic_event_admission", {})
                    ),
                    "strategic_cluster_expansion": _json_safe(
                        state.get("strategic_cluster_expansion", {})
                    ),
                }
                state["strategic_initial_inference_diagnostics"] = _json_safe(
                    initial_inference_diagnostics
                )
                logger.info(
                    "strategic_initial_inference_outcome=%s",
                    json.dumps(
                        _json_safe(initial_inference_diagnostics),
                        ensure_ascii=False,
                        default=str,
                    ),
                )

                # V3.12: if Deep strategic synthesis produced a promising but
                # evidence-incomplete candidate, do not immediately say "no".
                # Search specifically for the blocking evidence gap, process the
                # new sources through the same Extraction/EvidenceBuilder path,
                # then re-run the SAME bounded strategic task. Formatting/generic
                # failures are intentionally not rescued with web search.
                if (
                    not candidates
                    or not self._has_hidden_insight_candidate(candidates)
                ):
                    rescue = self._attempt_targeted_strategic_evidence_rescue(
                        topic=topic,
                        inference_result=inference_result,
                        selected_source_ids=selected_source_ids,
                        event_records=event_records,
                        evidence_records=evidence_records,
                        graph_context=graph_context,
                        constraints=constraints,
                        blueprint=blueprint,
                        state=state,
                    )
                    if rescue.get("attempted"):
                        inference_result = rescue.get("inference_result") or inference_result
                        candidates = list(rescue.get("candidates") or [])
                        selected_source_ids = list(
                            rescue.get("selected_source_ids") or selected_source_ids
                        )
                        event_records = list(
                            rescue.get("event_records") or event_records
                        )
                        evidence_records = list(
                            rescue.get("evidence_records") or evidence_records
                        )
                        state["source_ids"] = list(selected_source_ids)
                        state["events"] = list(event_records)
                        state["event_ids"] = [
                            _clean(item.get("event_id"))
                            for item in event_records
                            if isinstance(item, Mapping) and _clean(item.get("event_id"))
                        ]
                        state["evidence"] = list(evidence_records)
                        state["evidence_manifest"] = self._evidence_manifest(evidence_records)

                        rescued_map = _as_mapping(inference_result)
                        rescued_metadata = _as_mapping(rescued_map.get("metadata"))
                        rescued_depth = _enum(rescued_metadata.get("desired_depth"))
                        if rescued_depth:
                            state.setdefault("depth_integrity", {})["strategic"] = rescued_depth

            state[
                "initial_inference"
            ] = _json_safe(
                inference_result
            )

            if not candidates:
                inference_map = _as_mapping(inference_result)
                inference_metadata = _as_mapping(inference_map.get("metadata"))

                rejection_history = list(
                    inference_metadata.get("quality_rejection_history")
                    or inference_metadata.get("candidate_rejection_reasons")
                    or []
                )
                final_rejection_reasons = list(
                    inference_metadata.get("final_candidate_rejection_reasons") or []
                )
                rejected_candidate_diagnostics = list(
                    inference_metadata.get("rejected_candidate_diagnostics") or []
                )
                if not final_rejection_reasons and rejected_candidate_diagnostics:
                    last_reason = _clean(
                        _as_mapping(rejected_candidate_diagnostics[-1]).get("reason")
                    )
                    if last_reason:
                        final_rejection_reasons = [last_reason]
                if not final_rejection_reasons:
                    final_rejection_reasons = list(rejection_history[-1:])
                # Backward-compatible name now reflects the FINAL attempt, not
                # an aggregate of mutually exclusive failures across repair rounds.
                rejection_reasons = list(final_rejection_reasons)
                run_status = _enum(inference_metadata.get("run_status"))
                error_type = _clean(inference_metadata.get("error_type"))
                error_message = _clean(inference_metadata.get("error_message"))
                models_attempted = list(
                    inference_metadata.get("strategic_models_attempted")
                    or inference_metadata.get("strategic_models_used")
                    or []
                )
                model_failures = list(
                    inference_metadata.get("strategic_model_failures") or []
                )
                quality_rejected_models = list(
                    inference_metadata.get("strategic_quality_rejected_models") or []
                )
                support_cluster_count = int(
                    inference_metadata.get("support_cluster_count") or 0
                )
                last_linkage_diagnostics = _as_mapping(
                    inference_metadata.get("last_evidence_linkage_diagnostics")
                )
                route_unavailable_models = list(
                    inference_metadata.get("route_unavailable_models") or []
                )
                model_used = _clean(
                    inference_metadata.get("strategic_model_used")
                )

                # Do not collapse transport/provider failures into the same
                # message as deterministic publishability rejection. The
                # distinction is essential for debugging and operator UX.
                provider_failure = bool(
                    run_status in {"failed", "failed_closed", "provider_unavailable"}
                    or error_type
                    or error_message
                    or "strategic_provider_unavailable_all_models" in rejection_reasons
                )

                if provider_failure:
                    failure_kind = "strategic_model_execution_failure"
                elif rejection_reasons:
                    failure_kind = "strategic_candidate_rejected"
                else:
                    failure_kind = "strategic_no_candidate_returned"

                diagnostic = {
                    "failure_kind": failure_kind,
                    "run_status": run_status or None,
                    "error_type": error_type or None,
                    "error_message": error_message or None,
                    "desired_depth": inference_metadata.get("desired_depth"),
                    "quality_repair_used": inference_metadata.get("quality_repair_used"),
                    "strategic_model_used": model_used or None,
                    "models_attempted": models_attempted[:20],
                    "model_failures": model_failures[-12:],
                    "quality_rejected_models": quality_rejected_models[:20],
                    "route_unavailable_models": route_unavailable_models[:20],
                    "fallback_used": inference_metadata.get("strategic_fallback_used"),
                    "candidate_rejection_reasons": rejection_reasons[:20],
                    "final_candidate_rejection_reasons": final_rejection_reasons[:20],
                    "quality_rejection_history": rejection_history[:30],
                    "support_cluster_count": support_cluster_count,
                    "last_evidence_linkage_diagnostics": _json_safe(last_linkage_diagnostics),
                    "rejected_candidate_diagnostics": _json_safe(rejected_candidate_diagnostics[-6:]),
                    "accepted_evidence_count": len(evidence_records),
                    "event_count": len(event_records),
                    "source_count": len(selected_source_ids),
                    "strategic_evidence_rescue": _json_safe(
                        state.get("strategic_evidence_rescue", {})
                    ),
                    "strategic_initial_inference_diagnostics": _json_safe(
                        state.get("strategic_initial_inference_diagnostics", {})
                    ),
                }
                state["strategic_abstention_diagnostics"] = _json_safe(diagnostic)

                log_fn = logger.error if provider_failure else logger.info
                log_fn(
                    "Strategic inference stopped. diagnostics=%s",
                    json.dumps(
                        _json_safe(diagnostic),
                        ensure_ascii=False,
                        default=str,
                    ),
                )

                if provider_failure:
                    exact_message = (
                        error_message
                        or (
                            _clean(model_failures[-1].get("message"))
                            if model_failures and isinstance(model_failures[-1], Mapping)
                            else ""
                        )
                        or "all configured strategic model routes failed"
                    )
                    raise ResearchPipelineError(
                        "Strategic model execution failed. "
                        f"error_type={error_type or 'unknown'} "
                        f"message={exact_message} "
                        f"models_attempted={models_attempted!r} "
                        "model_failures="
                        + json.dumps(
                            _json_safe(model_failures[-12:]),
                            ensure_ascii=False,
                            default=str,
                        )
                    )

                if rejection_reasons:
                    return self._complete_abstention(
                        state=state,
                        topic=topic,
                        run_id=run_id,
                        depth=profile.name,
                        events=event_records,
                        evidence=evidence_records,
                        constraints=constraints,
                        graph_context=graph_context,
                        reasons=final_rejection_reasons,
                        rejection_history=rejection_history,
                        diagnostics=diagnostic,
                        started_at=started_at,
                        stage="strategic_inference",
                    )

                if run_status in {"ok", "completed", "abstained", ""}:
                    return self._complete_abstention(
                        state=state,
                        topic=topic,
                        run_id=run_id,
                        depth=profile.name,
                        events=event_records,
                        evidence=evidence_records,
                        constraints=constraints,
                        graph_context=graph_context,
                        reasons=["no_publishable_candidate_returned"],
                        rejection_history=rejection_history,
                        diagnostics=diagnostic,
                        started_at=started_at,
                        stage="strategic_inference",
                    )
                raise ResearchPipelineError(
                    "Strategic model returned no candidate insight and no explicit "
                    "provider or publishability error. diagnostics="
                    + json.dumps(
                        _json_safe(diagnostic),
                        ensure_ascii=False,
                        default=str,
                    )
                )

            if profile.name == "deep":
                state.setdefault("depth_integrity", {})["ok"] = bool(
                    state.get("depth_integrity", {}).get("extraction") == "deep"
                    and state.get("depth_integrity", {}).get("strategic")
                    in {"deep", "very_deep"}
                )
                if not state["depth_integrity"]["ok"]:
                    raise ResearchPipelineError(
                        "Deep run failed depth-integrity checks before quality selection."
                    )
            else:
                state.setdefault("depth_integrity", {})["ok"] = True

            self._stage_ok(
                state,
                "strategic_inference",
                count=len(
                    candidates
                ),
                depth_integrity=_json_safe(state.get("depth_integrity", {})),
            )

            assessed = (
                self._assess_insights(
                    insights=candidates,
                    evidence=evidence_records,
                    constraints=constraints,
                    blueprint=blueprint,
                )
            )

            selected = (
                self._select_insights(
                    assessed,
                    max_selected=(
                        profile
                        .max_selected_insights
                    ),
                )
            )

            if not selected:
                return self._complete_abstention(
                    state=state,
                    topic=topic,
                    run_id=run_id,
                    depth=profile.name,
                    events=event_records,
                    evidence=evidence_records,
                    constraints=constraints,
                    graph_context=graph_context,
                    reasons=["no_candidate_passed_initial_quality_selection"],
                    started_at=started_at,
                    stage="insight_quality_initial",
                )

            state[
                "selected_insights_initial"
            ] = selected

            self._stage_ok(
                state,
                "insight_quality_initial",
                count=len(
                    selected
                ),
            )

            refined = (
                self._refine_if_needed(
                    topic=topic,
                    selected=selected,
                    events=event_records,
                    evidence=evidence_records,
                    graph_context=(
                        graph_context
                    ),
                    constraints=constraints,
                    blueprint=blueprint,
                    profile=profile,
                    state=state,
                )
            )

            if (
                profile.name
                == "fast"
            ):
                grounded = (
                    self._ground_insights_fast(
                        refined,
                        evidence_records,
                    )
                )

            else:
                grounded = (
                    self._ground_insights(
                        refined,
                        evidence_records,
                    )
                )

            state[
                "grounding"
            ] = grounded

            self._stage_ok(
                state,
                "grounding",
                count=len(
                    grounded
                ),
            )

            final_assessed = (
                self
                ._final_quality_assessment(
                    selected=refined,
                    grounding=grounded,
                    evidence=evidence_records,
                    constraints=constraints,
                    blueprint=blueprint,
                )
            )

            final_insights = (
                self._select_insights(
                    final_assessed,
                    max_selected=(
                        profile
                        .max_selected_insights
                    ),
                    final=True,
                )
            )

            if (
                not final_insights
                and profile.name
                == "fast"
            ):
                grounded_fallback = []

                for insight in refined:
                    insight_key = _clean(
                        insight.get(
                            "insight_revision_id"
                        )
                        or insight.get(
                            "insight_id"
                        )
                    )

                    grounding_result = (
                        _as_mapping(
                            grounded.get(
                                insight_key,
                                {},
                            )
                        )
                    )

                    grounding_status = (
                        _enum(
                            grounding_result.get(
                                "review_status"
                            )
                            or grounding_result.get(
                                "status"
                            )
                        )
                    )

                    if (
                        grounding_status
                        in {
                            "complete",
                            "ok",
                            "supported",
                            "pass",
                            "passed",
                        }
                        and self
                        ._insight_evidence_ids(
                            insight
                        )
                    ):
                        grounded_fallback.append(
                            dict(
                                insight
                            )
                        )

                final_insights = (
                    grounded_fallback[
                        :profile
                        .max_selected_insights
                    ]
                )

                if final_insights:
                    state[
                        "warnings"
                    ].append(
                        {
                            "type": (
                                "fast_final_"
                                "quality_fallback"
                            ),
                            "message": (
                                "Fast mode retained "
                                "directly evidence-"
                                "linked, grounded "
                                "insights after final "
                                "quality filtering "
                                "removed all candidates."
                            ),
                        }
                    )

            if not final_insights:
                return self._complete_abstention(
                    state=state,
                    topic=topic,
                    run_id=run_id,
                    depth=profile.name,
                    events=event_records,
                    evidence=evidence_records,
                    constraints=constraints,
                    graph_context=graph_context,
                    reasons=["no_insight_remained_after_final_quality_and_grounding"],
                    started_at=started_at,
                    stage="final_quality_and_grounding",
                )

            # Final deterministic defense: never release known generic analyst
            # filler even if an upstream quality service scores it as usable.
            release_rejections = []
            release_safe_insights = []
            for candidate in final_insights:
                reason = self._final_release_rejection_reason(candidate)
                if reason:
                    release_rejections.append({
                        "insight_id": _clean(candidate.get("insight_id")),
                        "reason": reason,
                    })
                    logger.warning(
                        "Final insight rejected by deterministic release gate. "
                        "insight_id=%s reason=%s",
                        _clean(candidate.get("insight_id")),
                        reason,
                    )
                    continue
                release_safe_insights.append(candidate)

            state["final_release_rejections"] = release_rejections
            final_insights = release_safe_insights

            if not final_insights:
                return self._complete_abstention(
                    state=state,
                    topic=topic,
                    run_id=run_id,
                    depth=profile.name,
                    events=event_records,
                    evidence=evidence_records,
                    constraints=constraints,
                    graph_context=graph_context,
                    reasons=[
                        _clean(item.get("reason"))
                        for item in release_rejections
                        if isinstance(item, Mapping) and _clean(item.get("reason"))
                    ] or ["no_insight_survived_final_release_gate"],
                    started_at=started_at,
                    stage="final_release_gate",
                )

            state[
                "final_insights"
            ] = final_insights

            report = (
                self._build_report(
                    topic=topic,
                    run_id=run_id,
                    depth=profile.name,
                    events=event_records,
                    evidence=evidence_records,
                    constraints=constraints,
                    graph_context=(
                        graph_context
                    ),
                    insights=final_insights,
                    grounding=grounded,
                    strategic_connections=(
                        state.get("strategic_connections", [])
                    ),
                )
            )

            report[
                "blueprint_id"
            ] = _clean(
                blueprint.get(
                    "blueprint_id"
                )
            )
            report.setdefault("analysis_metadata", {})[
                "strategic_event_admission"
            ] = _json_safe(state.get("strategic_event_admission", {}))
            report["analysis_metadata"]["strategic_evidence_rescue"] = _json_safe(
                state.get("strategic_evidence_rescue", {})
            )
            report["analysis_metadata"]["strategic_initial_inference_diagnostics"] = _json_safe(
                state.get("strategic_initial_inference_diagnostics", {})
            )
            report["analysis_metadata"]["run_observability"] = self._run_observability_summary(
                state=state,
                events=event_records,
                evidence=evidence_records,
                report=report,
            )

            if (
                profile.name
                == "fast"
            ):
                report_grounding = (
                    self._ground_report_fast(
                        report,
                        evidence_records,
                    )
                )
            else:
                report_grounding = (
                    self._ground_report(
                        report,
                        evidence_records,
                    )
                )

            state[
                "report_grounding"
            ] = report_grounding

            if (
                profile.name
                == "fast"
            ):
                red_team_result = (
                    self
                    ._fast_red_team_review(
                        report=report,
                        grounding=(
                            report_grounding
                        ),
                    )
                )
            else:
                red_team_result = (
                    self._red_team(
                        topic=topic,
                        evidence=evidence_records,
                        constraints=constraints,
                        report=report,
                        grounding=(
                            report_grounding
                        ),
                    )
                )

            state[
                "red_team"
            ] = red_team_result

            self._stage_ok(
                state,
                "red_team",
            )

            release_decision = (
                self._release_decision(
                    blueprint=blueprint,
                    report=report,
                    grounding=(
                        report_grounding
                    ),
                    red_team=(
                        red_team_result
                    ),
                    state=state,
                )
            )

            if (
                profile.name == "fast"
                and not self
                ._release_is_authorized(
                    release_decision
                )
            ):
                state[
                    "policy_release_decision"
                ] = _json_safe(
                    release_decision
                )

                release_decision = (
                    self
                    ._fast_deferred_release_decision(
                        report=report,
                        evidence=evidence_records,
                        report_grounding=(
                            report_grounding
                        ),
                        red_team=(
                            red_team_result
                        ),
                        policy_decision=(
                            state[
                                "policy_release_decision"
                            ]
                        ),
                    )
                )

                state[
                    "warnings"
                ].append(
                    {
                        "type": (
                            "fast_governance_deferred"
                        ),
                        "message": (
                            "Fast report release used the temporary "
                            "evidence/grounding/red-team bridge; the "
                            "original policy decision is retained in "
                            "policy_release_decision for later governance "
                            "integration repair."
                        ),
                    }
                )

            state[
                "release_decision"
            ] = release_decision

            self._persist_insight_lifecycle(
                topic=topic,
                run_id=run_id,
                insights=final_insights,
                grounding=grounded,
                state=state,
            )

            report_persistence = (
                self._persist_report(
                    topic=topic,
                    report=report,
                    release_decision=(
                        release_decision
                    ),
                )
            )

            state[
                "report_persistence"
            ] = report_persistence

            elapsed = (
                time.perf_counter()
                - started_at
            )

            state[
                "elapsed_seconds"
            ] = round(
                elapsed,
                3,
            )

            state[
                "status"
            ] = "completed"

            state[
                "report"
            ] = report

            state[
                "report_released"
            ] = bool(
                report_persistence.get(
                    "saved",
                    False,
                )
                and _enum(
                    report_persistence.get(
                        "state"
                    )
                )
                == "released"
            )
            final_observability = self._run_observability_summary(
                state=state,
                events=event_records,
                evidence=evidence_records,
                report=report,
            )
            report.setdefault("analysis_metadata", {})["run_observability"] = _json_safe(
                final_observability
            )
            logger.info(
                "strategic_final_outcome=%s",
                json.dumps(_json_safe(final_observability), ensure_ascii=False, default=str),
            )
            logger.info(
                "report_persistence_outcome=%s",
                json.dumps(
                    _json_safe(final_observability.get("report_persistence", {})),
                    ensure_ascii=False,
                    default=str,
                ),
            )

            self._emit_telemetry(
                state
            )

            logger.info(
                (
                    "Research run completed. "
                    "run_id=%s elapsed=%.3fs "
                    "released=%s"
                ),
                run_id,
                elapsed,
                state[
                    "report_released"
                ],
            )

            return state

        except Exception as error:
            elapsed = (
                time.perf_counter()
                - started_at
            )

            state[
                "status"
            ] = "failed"

            state[
                "elapsed_seconds"
            ] = round(
                elapsed,
                3,
            )

            state[
                "errors"
            ].append(
                {
                    "type": (
                        type(error).__name__
                    ),
                    "message": (
                        str(error)
                    ),
                }
            )

            self._emit_telemetry(
                state
            )

            logger.exception(
                (
                    "Research run failed. "
                    "run_id=%s"
                ),
                run_id,
            )

            raise

    def _complete_abstention(
        self,
        *,
        state: dict[str, Any],
        topic: str,
        run_id: str,
        depth: str,
        events: Sequence[Mapping[str, Any]],
        evidence: Sequence[Mapping[str, Any]],
        constraints: Any,
        graph_context: Mapping[str, Any],
        reasons: Sequence[str],
        rejection_history: Optional[Sequence[str]] = None,
        diagnostics: Optional[Mapping[str, Any]] = None,
        started_at: float,
        stage: str = "strategic_inference",
    ) -> dict[str, Any]:
        """Complete a research run with an explicit intelligence abstention.

        A quality-gated absence of a publishable insight is a valid research
        outcome, not an execution failure. Provider/runtime failures still raise
        ResearchPipelineError before this helper is called.
        """
        final_reasons = _unique_strings([_clean(item) for item in reasons if _clean(item)])
        history = _unique_strings([
            _clean(item) for item in (rejection_history or final_reasons) if _clean(item)
        ])

        # Build an evidence-only abstention report. Do not reuse the normal
        # publishable-insight report builder here: its material-event ranking may
        # intentionally omit actor-ambiguous events, while an abstention must still
        # show every event the research run examined.
        evidence_by_event: dict[str, list[Mapping[str, Any]]] = {}
        for record in evidence:
            if not isinstance(record, Mapping):
                continue
            event_id = _clean(record.get("event_id")).upper()
            if event_id:
                evidence_by_event.setdefault(event_id, []).append(record)

        event_landscape: list[dict[str, Any]] = []
        represented_event_ids: set[str] = set()
        for event in events:
            if not isinstance(event, Mapping):
                continue
            event_id = _clean(event.get("event_id") or event.get("id")).upper()
            if not event_id or event_id in represented_event_ids:
                continue
            linked = evidence_by_event.get(event_id, [])
            evidence_ids = _unique_strings([
                _clean(item.get("evidence_id") or item.get("id")).upper()
                for item in linked
                if _clean(item.get("evidence_id") or item.get("id"))
            ])
            source_ids = _unique_strings([
                _clean(item.get("source_id")).upper()
                for item in linked
                if _clean(item.get("source_id"))
            ])
            fallback_claim = next((
                _clean(item.get("claim") or item.get("text") or item.get("excerpt"))
                for item in linked
                if _clean(item.get("claim") or item.get("text") or item.get("excerpt"))
            ), "")
            summary = _clean(
                event.get("summary")
                or event.get("title")
                or event.get("event")
                or event.get("description")
                or fallback_claim
            )[:1200]
            actor = _clean(
                event.get("primary_actor")
                or event.get("acting_entity")
                or event.get("actor")
                or event.get("organization")
                or event.get("company")
                or event.get("institution")
            )
            event_landscape.append({
                "event_id": event_id,
                "event": summary or "Evidence-backed event; actor/detail attribution incomplete.",
                "summary": summary or "Evidence-backed event; actor/detail attribution incomplete.",
                "actor": actor or "unattributed",
                "evidence_ids": evidence_ids,
                "source_ids": source_ids,
                "representation": "abstention_event_summary",
            })
            represented_event_ids.add(event_id)

        evidence_trail = []
        for record in evidence:
            if not isinstance(record, Mapping):
                continue
            evidence_id = _clean(record.get("evidence_id") or record.get("id")).upper()
            if not evidence_id:
                continue
            evidence_trail.append({
                "evidence_id": evidence_id,
                "event_id": _clean(record.get("event_id")).upper(),
                "source_id": _clean(record.get("source_id")).upper(),
                "claim": _clean(
                    record.get("claim") or record.get("text") or record.get("excerpt")
                )[:1400],
                "evidence_type": _clean(record.get("evidence_type") or record.get("type")),
            })

        considered_event_ids = {
            _clean(item.get("event_id") or item.get("id")).upper()
            for item in events
            if isinstance(item, Mapping) and _clean(item.get("event_id") or item.get("id"))
        }
        unrepresented = sorted(considered_event_ids - represented_event_ids)
        if unrepresented:
            raise ResearchPipelineError(
                "Abstention Event Landscape integrity failed. unrepresented_event_ids="
                + repr(unrepresented[:20])
            )

        report = {
            "research_question": topic,
            "run_id": run_id,
            "research_depth": depth,
            "executive_synthesis": [],
            "event_landscape": event_landscape,
            "event_landscape_metadata": {
                "raw_considered_event_count": len(considered_event_ids),
                "underlying_event_count": len(considered_event_ids),
                "all_considered_events_represented": not unrepresented,
                "representation_policy": "every_considered_event_as_concise_abstention_summary",
            },
            "connections": [],
            "what_is_happening_underneath": [],
            "hidden_insights": [],
            "second_order_effects": [],
            "counterevidence_and_alternative_explanations": [],
            "what_to_watch": [],
            "evidence_trail": evidence_trail,
            "critical_unknowns": [],
            "grounding_by_insight": {},
            "analysis_metadata": {
                "analysis_mode": _enum(depth) or depth,
                "synthesis_mode": "deep_strategic_inference" if _enum(depth) == "deep" else "bounded_evidence_conditioned_llm",
                "fallback_used": False,
                "degraded": False,
                "insight_count": 0,
                "evidence_record_count": len(evidence),
                "event_count": len(events),
                "citation_integrity": {
                    "ok": not unrepresented,
                    "considered_event_count": len(considered_event_ids),
                    "represented_event_count": len(represented_event_ids),
                    "unrepresented_considered_event_ids": unrepresented,
                    "honest_abstention": True,
                },
            },
            "epistemic_note": (
                "Facts, connections, mechanisms, inferences and uncertainties are "
                "not treated as equivalent claims."
            ),
        }
        report["underlying_dynamics"] = report["what_is_happening_underneath"]
        report["counterevidence_and_alternatives"] = report[
            "counterevidence_and_alternative_explanations"
        ]
        report["uncertainty"] = report["critical_unknowns"]
        corpus_quality = _as_mapping(state.get("strategic_corpus_quality_before_inference"))
        refill = _as_mapping(state.get("strategic_corpus_refill"))
        rescue_state = _as_mapping(state.get("strategic_evidence_rescue"))
        discovery_circuit_open = self._fresh_discovery_circuit_is_open(state)
        if (
            _enum(depth) == "deep"
            and discovery_circuit_open
            and not bool(corpus_quality.get("sufficient", True))
        ):
            abstention_classification = (
                "retrieval_provider_unavailable_before_sufficient_corpus"
            )
        elif _enum(depth) == "deep" and not bool(refill.get("attempted")) and not bool(corpus_quality.get("sufficient", True)):
            abstention_classification = "premature_no_refill_attempted"
        elif _enum(depth) == "deep" and bool(refill.get("attempted")) and not bool(corpus_quality.get("sufficient", True)):
            abstention_classification = "insufficient_corpus_after_mandatory_refill"
        elif bool(rescue_state.get("attempted")):
            abstention_classification = "exhausted_bounded_research"
        else:
            abstention_classification = "no_publishable_finding_after_sufficient_research"

        report["outcome"] = "abstained"
        report["abstention_classification"] = abstention_classification
        report["abstention_summary"] = (
            "No publishable strategic insight was established after bounded "
            "quality review. The evidence and event landscape are retained, but "
            "the system did not release a thesis that exceeded its evidence."
        )
        report["abstention"] = {
            "status": "no_publishable_strategic_finding",
            "classification": abstention_classification,
            "stage": stage,
            "final_rejection_reasons": list(final_reasons),
            "quality_rejection_history": list(history),
            "diagnostics": _json_safe(dict(diagnostics or {})),
        }
        report.setdefault("analysis_metadata", {})["outcome"] = "abstained"
        report["analysis_metadata"]["abstention_classification"] = abstention_classification
        report["analysis_metadata"]["publishable_insight_count"] = 0
        report["analysis_metadata"]["strategic_finding_count"] = 0
        report["analysis_metadata"]["abstention_is_pipeline_failure"] = (
            abstention_classification
            in {
                "premature_no_refill_attempted",
                "retrieval_provider_unavailable_before_sufficient_corpus",
            }
        )
        report["analysis_metadata"]["strategic_event_admission"] = _json_safe(
            state.get("strategic_event_admission", {})
        )
        report["analysis_metadata"]["strategic_evidence_rescue"] = _json_safe(
            state.get("strategic_evidence_rescue", {})
        )
        report["analysis_metadata"]["strategic_initial_inference_diagnostics"] = _json_safe(
            state.get("strategic_initial_inference_diagnostics", {})
        )
        report["analysis_metadata"]["strategic_abstention_diagnostics"] = _json_safe(
            state.get("strategic_abstention_diagnostics", {})
        )
        report["analysis_metadata"]["run_observability"] = self._run_observability_summary(
            state=state,
            events=events,
            evidence=evidence,
            report=report,
        )

        # Release the research report even when the Hidden Insight outcome is
        # abstention.  Report release and insight publication are separate
        # product concepts: the evidence/event audit record must remain
        # retrievable, while analysis_metadata/outcome make it explicit that
        # zero strategic insights were authorized.  Use the existing
        # ReportService "released" state for compatibility with stores/UIs that
        # expose only released report revisions; do not depend on an unsupported
        # ReportService-level "abstained" state.
        decision_id = (
            "RELEASE-"
            + uuid.uuid4().hex[:24].upper()
        )
        release_decision = {
            "release_decision_id": decision_id,
            "decision_id": decision_id,
            "release_allowed": True,
            "authorizes_release": True,
            "allowed": True,
            "decision": "allow",
            "authority": "abstention_report_release_policy",
            "reasons": [
                (
                    "Research completed with an honest abstention; the evidence "
                    "and event audit record are released for review even though "
                    "zero Hidden Insights were authorized for publication."
                )
            ],
            "report_outcome": "abstained",
            "publishable_insight_count": 0,
            "final_rejection_reasons": list(final_reasons),
        }
        report.setdefault("analysis_metadata", {})["report_release_semantics"] = (
            "released_research_report_with_abstained_hidden_insight_outcome"
        )

        report_persistence = self._persist_report(
            topic=topic,
            report=report,
            release_decision=release_decision,
        )

        state["status"] = "completed"
        state["research_outcome"] = "abstained"
        state["final_insights"] = []
        state["report"] = report
        state["release_decision"] = release_decision
        state["report_persistence"] = report_persistence
        state["report_released"] = bool(
            report_persistence.get("saved", False)
            and _enum(report_persistence.get("state")) == "released"
        )

        if not state["report_released"]:
            state.setdefault("warnings", []).append({
                "type": "abstention_report_persistence_failed",
                "message": (
                    "Research completed with abstention, but the abstention "
                    "report was not persisted as a released report revision."
                ),
                "persistence": _json_safe(report_persistence),
            })
        final_observability = self._run_observability_summary(
            state=state,
            events=events,
            evidence=evidence,
            report=report,
        )
        report.setdefault("analysis_metadata", {})["run_observability"] = _json_safe(
            final_observability
        )
        logger.info(
            "strategic_final_outcome=%s",
            json.dumps(_json_safe(final_observability), ensure_ascii=False, default=str),
        )
        logger.info(
            "report_persistence_outcome=%s",
            json.dumps(
                _json_safe(final_observability.get("report_persistence", {})),
                ensure_ascii=False,
                default=str,
            ),
        )
        state.setdefault("stage_status", {})[stage] = {
            "status": "ok",
            "outcome": "abstained",
            "count": 0,
        }
        if _enum(depth) == "deep":
            depth_state = state.setdefault("depth_integrity", {})
            depth_state["ok"] = bool(
                depth_state.get("extraction") == "deep"
                and depth_state.get("strategic") in {"deep", "very_deep"}
            )

        elapsed = time.perf_counter() - started_at
        state["elapsed_seconds"] = round(elapsed, 3)
        self._emit_telemetry(state)
        logger.info(
            "Research run completed with abstention. run_id=%s elapsed=%.3fs reasons=%s",
            run_id,
            elapsed,
            list(final_reasons),
        )
        return state

    @staticmethod
    def _source_history_path() -> Path:
        configured = _clean(os.getenv("EVENT_INTELLIGENCE_STATE_DIR"))
        base = Path(configured) if configured else Path(".event_intelligence_state")
        return base / "source_history.json"

    @staticmethod
    def _history_topic_key(topic: str) -> str:
        return hashlib.sha256(_clean(topic).casefold().encode("utf-8")).hexdigest()[:20]

    @classmethod
    def _load_source_history(cls, topic: str) -> dict[str, Any]:
        path = cls._source_history_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            return {"run_count": 0, "entries": []}
        node = payload.get(cls._history_topic_key(topic), {}) if isinstance(payload, Mapping) else {}
        if not isinstance(node, Mapping):
            return {"run_count": 0, "entries": []}
        entries = [dict(item) for item in (node.get("entries", []) or []) if isinstance(item, Mapping)]
        return {"run_count": int(node.get("run_count", 0) or 0), "entries": entries[-80:]}

    @classmethod
    def _save_source_history(cls, topic: str, selected: Sequence[Mapping[str, Any]]) -> None:
        if not selected:
            return
        path = cls._source_history_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}
        key = cls._history_topic_key(topic)
        prior = payload.get(key, {}) if isinstance(payload.get(key), Mapping) else {}
        entries = [dict(item) for item in (prior.get("entries", []) or []) if isinstance(item, Mapping)]
        now = datetime.now(timezone.utc).isoformat()
        for item in selected:
            entries.append({
                "url": _clean(item.get("url")),
                "title": _clean(item.get("title")),
                "source_id": _clean(item.get("source_id")),
                "content_fingerprint": _clean(item.get("content_fingerprint")),
                "published_at": _clean(item.get("published_at")),
                "seen_at": now,
            })
        payload[key] = {
            "topic": _clean(topic),
            "run_count": int(prior.get("run_count", 0) or 0) + 1,
            "entries": entries[-80:],
        }
        temp = path.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)
        except Exception:
            try:
                if temp.exists():
                    temp.unlink()
            except Exception:
                pass

    def _cached_source_ids_for_topic(
        self,
        *,
        topic: str,
        max_sources: int,
        state: dict[str, Any],
    ) -> list[str]:
        """
        Return the most recently persisted, still-readable SOURCE-* records for
        the exact same topic.

        This is a resilience path for temporary discovery-provider failure
        (for example NewsAPI quota/rate exhaustion). It does NOT claim that the
        sources were freshly discovered in the current run.
        """

        history = self._load_source_history(topic)
        entries = [
            dict(item)
            for item in (history.get("entries", []) or [])
            if isinstance(item, Mapping)
        ]

        selected: list[str] = []
        seen: set[str] = set()
        rejected: list[dict[str, str]] = []

        # Newest history entries first. History may contain the same SOURCE-*
        # more than once across runs, so deduplicate before validating.
        for item in reversed(entries):
            source_id = _clean(item.get("source_id"))
            if (
                not source_id
                or not source_id.upper().startswith("SOURCE-")
                or source_id in seen
            ):
                continue

            seen.add(source_id)

            try:
                payload = self._get_source_extraction_payload(source_id)
            except Exception as error:
                rejected.append(
                    {
                        "source_id": source_id,
                        "error": type(error).__name__,
                        "message": str(error),
                    }
                )
                continue

            article_text = _clean(
                payload.get("article_text")
                or payload.get("content")
                or payload.get("body")
                or payload.get("text")
            )
            if not article_text:
                rejected.append(
                    {
                        "source_id": source_id,
                        "error": "EmptyCachedSource",
                        "message": "Persisted source has no extractable article text.",
                    }
                )
                continue

            selected.append(source_id)
            if len(selected) >= max(1, int(max_sources)):
                break

        state["source_discovery_cached_candidate_count"] = len(entries)
        state["source_discovery_cached_selected_count"] = len(selected)
        state["source_discovery_cached_rejections"] = rejected

        return selected

    @staticmethod
    def _history_title_identity(value: Any) -> str:
        text = _clean(value).casefold()
        text = re.sub(r"[^a-z0-9]+", " ", text) if 're' in globals() else text
        return " ".join(text.split())[:240]

    @staticmethod
    def _fresh_discovery_terminal_error(error: Any) -> bool:
        """Return True when retrying fresh discovery in this run is pointless.

        Quota/rate-limit/auth exhaustion is treated as a run-scoped circuit-open
        condition. Ordinary query-specific failures remain retryable because a
        different query or source may still succeed.
        """
        error_text = str(error or "").casefold()
        return any(
            token in error_text
            for token in (
                "quota",
                "rate limit",
                "rate_limit",
                "429",
                "resource_exhausted",
                "apikeyexhausted",
                "ratelimited",
                "api key authentication failed",
                "apikeyinvalid",
                "apikeydisabled",
                "unauthorized",
                "401",
            )
        )

    @staticmethod
    def _fresh_discovery_circuit_is_open(state: Mapping[str, Any]) -> bool:
        circuit = _as_mapping(state.get("fresh_discovery_circuit_breaker"))
        return bool(
            state.get("fresh_discovery_unavailable_for_run")
            or circuit.get("open")
        )

    @staticmethod
    def _mark_fresh_discovery_circuit_open(
        state: dict[str, Any],
        *,
        phase: str,
        error: Any = None,
        reason: str = "provider_unavailable",
    ) -> None:
        """Open a run-scoped breaker so later retrieval lanes do not retry it."""
        previous = _as_mapping(state.get("fresh_discovery_circuit_breaker"))
        was_open = bool(
            state.get("fresh_discovery_unavailable_for_run")
            or previous.get("open")
        )
        phases = _unique_strings(
            list(previous.get("phases") or []) + [_clean(phase)]
        )
        error_payload = _as_mapping(previous.get("provider_error"))
        if error is not None:
            error_payload = {
                "error": type(error).__name__,
                "message": str(error),
            }

        state["fresh_discovery_unavailable_for_run"] = True
        state["fresh_discovery_circuit_breaker"] = {
            "open": True,
            "reason": _clean(reason) or "provider_unavailable",
            "opened_phase": (
                _clean(previous.get("opened_phase"))
                or _clean(phase)
            ),
            "phases": phases,
            "provider_error": _json_safe(error_payload),
            "policy": (
                "do_not_retry_fresh_web_discovery_after_terminal_provider_"
                "failure_within_same_research_run"
            ),
        }
        if error_payload:
            state["source_discovery_provider_error"] = _json_safe(error_payload)

        if not was_open:
            state.setdefault("warnings", []).append(
                {
                    "type": "fresh_discovery_circuit_opened",
                    "message": (
                        "Fresh discovery became unavailable for this research run. "
                        "Later refill, cluster-expansion and rescue stages will not "
                        "retry the failed provider; they may reuse persisted sources "
                        "and continue from the evidence already collected."
                    ),
                    "phase": _clean(phase),
                    "reason": _clean(reason),
                    "provider_error": _json_safe(error_payload),
                }
            )

    @classmethod
    def _propagate_fresh_discovery_circuit(
        cls,
        *,
        parent_state: dict[str, Any],
        discovery_state: Mapping[str, Any],
        phase: str,
    ) -> bool:
        """Propagate a temporary discovery state's breaker to the parent run."""
        child_circuit = _as_mapping(
            discovery_state.get("fresh_discovery_circuit_breaker")
        )
        unavailable = bool(
            discovery_state.get("fresh_discovery_unavailable_for_run")
            or child_circuit.get("open")
            or discovery_state.get("source_discovery_provider_unavailable")
        )
        if not unavailable:
            return False

        provider_error = _as_mapping(
            discovery_state.get("source_discovery_provider_error")
            or child_circuit.get("provider_error")
        )
        cls._mark_fresh_discovery_circuit_open(
            parent_state,
            phase=phase,
            error=None,
            reason=(
                _clean(child_circuit.get("reason"))
                or "provider_unavailable"
            ),
        )
        if provider_error:
            parent_state["source_discovery_provider_error"] = _json_safe(
                provider_error
            )
            parent_circuit = _as_mapping(
                parent_state.get("fresh_discovery_circuit_breaker")
            )
            parent_circuit["provider_error"] = _json_safe(provider_error)
            parent_state["fresh_discovery_circuit_breaker"] = parent_circuit
        parent_state["source_discovery_provider_unavailable"] = True
        return True

    def _discover_sources_with_run_circuit_breaker(
        self,
        *,
        topic: str,
        max_sources: int,
        parent_state: dict[str, Any],
        discovery_state: dict[str, Any],
        discovery_queries_override: Optional[Sequence[str]] = None,
        discovery_phase: str,
    ) -> list[str]:
        """Run one discovery stage while honoring the parent run circuit breaker."""
        if self._fresh_discovery_circuit_is_open(parent_state):
            discovery_state["fresh_discovery_unavailable_for_run"] = True
            discovery_state["fresh_discovery_circuit_breaker"] = _json_safe(
                parent_state.get("fresh_discovery_circuit_breaker") or {}
            )
            discovery_state["source_discovery_provider_unavailable"] = True
            if parent_state.get("source_discovery_provider_error"):
                discovery_state["source_discovery_provider_error"] = _json_safe(
                    parent_state.get("source_discovery_provider_error")
                )

        discovered = self._discover_sources(
            topic=topic,
            max_sources=max_sources,
            state=discovery_state,
            discovery_queries_override=discovery_queries_override,
            discovery_phase=discovery_phase,
        )
        self._propagate_fresh_discovery_circuit(
            parent_state=parent_state,
            discovery_state=discovery_state,
            phase=discovery_phase,
        )
        return discovered

    def _discover_sources(
        self,
        *,
        topic: str,
        max_sources: int,
        state: dict[str, Any],
        discovery_queries_override: Optional[Sequence[str]] = None,
        discovery_phase: str = "initial",
    ) -> list[str]:

        # V3.26.3: once a terminal provider failure (quota/rate-limit/auth)
        # opens the run-scoped breaker, later retrieval lanes must not call the
        # same provider again. They may still reuse persisted sources.
        if self._fresh_discovery_circuit_is_open(state):
            cached_source_ids = self._cached_source_ids_for_topic(
                topic=topic,
                max_sources=max_sources,
                state=state,
            )
            state["source_discovery_phase"] = discovery_phase
            state["source_discovery_mode"] = (
                "cached_persisted_sources_circuit_breaker"
            )
            state["source_discovery_degraded"] = True
            state["source_discovery_fresh"] = False
            state["source_discovery_provider_unavailable"] = True
            state["source_discovery_queries"] = list(
                _unique_strings(discovery_queries_override)
            )
            state["source_discovery_selected"] = [
                {
                    "source_id": source_id,
                    "selection_mode": "cached_persisted_source_circuit_breaker",
                }
                for source_id in cached_source_ids
            ]
            state["source_discovery_new_source_count"] = 0
            state["source_discovery_repeat_source_count"] = len(cached_source_ids)
            logger.info(
                "Fresh discovery skipped because run circuit is open. "
                "phase=%s cached_source_count=%s topic=%s",
                discovery_phase,
                len(cached_source_ids),
                topic,
            )
            return cached_source_ids

        if (
            self.web_search_service is None
            or self.source_fetch_service is None
        ):
            missing_components = []
            if self.web_search_service is None:
                missing_components.append("WebSearchService")
            if self.source_fetch_service is None:
                missing_components.append("SourceFetchService")
            self._mark_fresh_discovery_circuit_open(
                state,
                phase=discovery_phase,
                reason=(
                    "fresh_discovery_components_unavailable:"
                    + ",".join(missing_components)
                ),
            )
            cached_source_ids = self._cached_source_ids_for_topic(
                topic=topic,
                max_sources=max_sources,
                state=state,
            )

            if cached_source_ids:
                state["source_discovery_mode"] = "cached_persisted_sources"
                state["source_discovery_degraded"] = True
                state["source_discovery_fresh"] = False
                state["source_discovery_provider_unavailable"] = (
                    self.web_search_service is None
                )
                state["source_fetch_service_unavailable"] = (
                    self.source_fetch_service is None
                )
                state["source_discovery_selected"] = [
                    {
                        "source_id": source_id,
                        "selection_mode": "cached_persisted_source",
                    }
                    for source_id in cached_source_ids
                ]
                state["source_discovery_new_source_count"] = 0
                state["source_discovery_repeat_source_count"] = len(
                    cached_source_ids
                )
                state.setdefault("warnings", []).append(
                    {
                        "type": "source_discovery_cached_fallback",
                        "message": (
                            "Fresh web discovery is not configured or the "
                            "source-fetch layer is unavailable. The run "
                            "continued with previously persisted sources for "
                            "the same topic; these are not fresh discoveries "
                            "from the current run."
                        ),
                    }
                )

                logger.warning(
                    "Fresh discovery components unavailable; using %s "
                    "persisted SOURCE-* records for topic=%s.",
                    len(cached_source_ids),
                    topic,
                )

                return cached_source_ids

            missing = []
            if self.web_search_service is None:
                missing.append("WebSearchService")
            if self.source_fetch_service is None:
                missing.append("SourceFetchService")

            raise ResearchPipelineError(
                "Fresh topic discovery is unavailable because "
                + ", ".join(missing)
                + " is not configured, and no cached persisted sources "
                "exist for this exact topic. Configure a web-search provider "
                "or supply source_ids explicitly."
            )

        research_depth = _enum(
            state.get(
                "research_depth",
                "fast",
            )
        )

        history = self._load_source_history(topic)
        recent_entries = list(history.get("entries", []) or [])
        recent_urls = {
            self._discovery_url_identity(item)
            for item in recent_entries
            if self._discovery_url_identity(item)
        }
        recent_titles = {
            self._history_title_identity(item.get("title"))
            for item in recent_entries
            if self._history_title_identity(item.get("title"))
        }
        recent_content_fingerprints = {
            _clean(item.get("content_fingerprint"))
            for item in recent_entries
            if _clean(item.get("content_fingerprint"))
        }
        state["source_history_prior_run_count"] = int(history.get("run_count", 0) or 0)
        state["source_history_recent_record_count"] = len(recent_entries)

        discovery_queries = (
            _unique_strings(discovery_queries_override)
            if discovery_queries_override
            else self._build_initial_discovery_queries(
                topic=topic,
                research_depth=research_depth,
                rotation=int(history.get("run_count", 0) or 0),
            )
        )
        state["source_discovery_phase"] = discovery_phase

        state[
            "source_discovery_queries"
        ] = list(
            discovery_queries
        )

        if (
            research_depth
            == "fast"
        ):
            per_query_limit = min(
                max(
                    int(
                        max_sources
                    ),
                    4,
                ),
                8,
            )
        else:
            per_query_limit = min(
                max(
                    int(
                        max_sources
                    ),
                    6,
                ),
                10,
            )

        raw_candidates: list[
            dict[str, Any]
        ] = []

        search_failures: list[
            dict[str, str]
        ] = []

        provider_unavailable = False

        for (
            query_index,
            query,
        ) in enumerate(
            discovery_queries
        ):
            try:
                search_result = (
                    self._call_method(
                        self.web_search_service,
                        (
                            "search",
                            "search_web",
                            "discover",
                        ),
                        {
                            "query": query,
                            "topic": topic,
                            "research_question": (
                                topic
                            ),
                            "limit": (
                                per_query_limit
                            ),
                            "max_results": (
                                per_query_limit
                            ),
                            "timeout_seconds": (
                                8.0
                                if research_depth
                                == "fast"
                                else 10.0
                            ),
                        },
                    )
                )

                items = (
                    self._extract_search_items(
                        search_result
                    )
                )

                for item in items:
                    candidate = dict(
                        item
                    )

                    candidate[
                        "_discovery_query"
                    ] = query

                    candidate[
                        "_discovery_query_index"
                    ] = query_index

                    raw_candidates.append(
                        candidate
                    )

            except Exception as error:
                search_failures.append(
                    {
                        "query": query,
                        "error": (
                            type(error).__name__
                        ),
                        "message": (
                            str(error)
                        ),
                    }
                )

                logger.warning(
                    (
                        "Discovery search query "
                        "failed. query=%s "
                        "error=%s message=%s"
                    ),
                    query,
                    type(error).__name__,
                    str(error),
                )

                if self._fresh_discovery_terminal_error(error):
                    provider_unavailable = True
                    state["source_discovery_provider_unavailable"] = True
                    state["source_discovery_provider_error"] = {
                        "error": type(error).__name__,
                        "message": str(error),
                    }
                    self._mark_fresh_discovery_circuit_open(
                        state,
                        phase=discovery_phase,
                        error=error,
                        reason="terminal_discovery_provider_failure",
                    )
                    break

        if not raw_candidates and not provider_unavailable:
            fallback_query = (
                topic
            )

            try:
                search_result = (
                    self._call_method(
                        self.web_search_service,
                        (
                            "search",
                            "search_web",
                            "discover",
                        ),
                        {
                            "query": (
                                fallback_query
                            ),
                            "topic": topic,
                            "research_question": (
                                topic
                            ),
                            "limit": (
                                per_query_limit
                            ),
                            "max_results": (
                                per_query_limit
                            ),
                            "timeout_seconds": (
                                8.0
                                if research_depth
                                == "fast"
                                else 10.0
                            ),
                        },
                    )
                )

                for item in (
                    self._extract_search_items(
                        search_result
                    )
                ):
                    candidate = dict(
                        item
                    )

                    candidate[
                        "_discovery_query"
                    ] = fallback_query

                    candidate[
                        "_discovery_query_index"
                    ] = len(
                        discovery_queries
                    )

                    candidate[
                        "_raw_topic_fallback"
                    ] = True

                    raw_candidates.append(
                        candidate
                    )

                state[
                    "warnings"
                ].append(
                    {
                        "type": (
                            "source_discovery_"
                            "fallback"
                        ),
                        "message": (
                            "Event-oriented discovery "
                            "returned no candidates; "
                            "raw-topic search fallback "
                            "was used."
                        ),
                    }
                )

            except Exception as error:
                search_failures.append(
                    {
                        "query": (
                            fallback_query
                        ),
                        "error": (
                            type(error).__name__
                        ),
                        "message": (
                            str(error)
                        ),
                    }
                )
                if self._fresh_discovery_terminal_error(error):
                    provider_unavailable = True
                    state["source_discovery_provider_unavailable"] = True
                    state["source_discovery_provider_error"] = {
                        "error": type(error).__name__,
                        "message": str(error),
                    }
                    self._mark_fresh_discovery_circuit_open(
                        state,
                        phase=discovery_phase,
                        error=error,
                        reason="terminal_discovery_provider_failure",
                    )

        state[
            "source_discovery_search_failures"
        ] = (
            search_failures
        )
        state.setdefault(
            "source_discovery_provider_unavailable",
            provider_unavailable,
        )

        # If fresh discovery produced nothing, do not fail the entire research
        # pipeline merely because the external provider is temporarily
        # unavailable or quota-limited. Reuse only persisted SOURCE-* records
        # previously captured for this exact topic, and mark the execution as a
        # degraded cached-source run so downstream/UI code cannot mistake it for
        # fresh discovery.
        if not raw_candidates:
            cached_source_ids = self._cached_source_ids_for_topic(
                topic=topic,
                max_sources=max_sources,
                state=state,
            )

            if cached_source_ids:
                state["source_discovery_mode"] = "cached_persisted_sources"
                state["source_discovery_degraded"] = True
                state["source_discovery_fresh"] = False
                state["source_discovery_selected"] = [
                    {
                        "source_id": source_id,
                        "selection_mode": "cached_persisted_source",
                    }
                    for source_id in cached_source_ids
                ]
                state["source_discovery_new_source_count"] = 0
                state["source_discovery_repeat_source_count"] = len(
                    cached_source_ids
                )
                state.setdefault("warnings", []).append(
                    {
                        "type": "source_discovery_cached_fallback",
                        "message": (
                            "Fresh NewsAPI/web discovery returned no usable candidates or was unavailable. "
                            "The run continued with previously persisted sources "
                            "for the same topic. These sources are not fresh "
                            "discoveries from the current run."
                        ),
                    }
                )

                logger.warning(
                    "Fresh NewsAPI/web discovery unavailable; using %s persisted "
                    "SOURCE-* records for topic=%s.",
                    len(cached_source_ids),
                    topic,
                )

                return cached_source_ids

            state["source_discovery_mode"] = "fresh_discovery_unavailable"
            state["source_discovery_degraded"] = True
            state["source_discovery_fresh"] = False

        ranked_candidates = (
            self
            ._rank_initial_discovery_candidates(
                raw_candidates,
                research_depth=research_depth,
                recent_urls=recent_urls,
                recent_titles=recent_titles,
            )
        )

        if (
            research_depth
            == "fast"
        ):
            candidate_attempt_limit = min(
                len(
                    ranked_candidates
                ),
                max(
                    max_sources + 8,
                    max_sources * 2,
                ),
            )
        else:
            candidate_attempt_limit = min(
                len(
                    ranked_candidates
                ),
                max(
                    max_sources + 12,
                    max_sources * 2,
                ),
            )

        fetch_candidates = (
            self
            ._diversify_discovery_candidates(
                ranked_candidates,
                maximum_candidates=(
                    candidate_attempt_limit
                ),
                per_domain_limit=(
                    2
                    if research_depth
                    == "fast"
                    else 3
                ),
            )
        )

        state[
            "source_discovery_candidate_count"
        ] = len(
            raw_candidates
        )

        state[
            "source_discovery_ranked_candidates"
        ] = [
            {
                "url": (
                    item.get(
                        "canonical_url"
                    )
                    or item.get(
                        "url"
                    )
                    or item.get(
                        "link"
                    )
                    or ""
                ),
                "title": (
                    item.get(
                        "title",
                        "",
                    )
                ),
                "domain": (
                    item.get(
                        "domain",
                        "",
                    )
                ),
                "published_at": (
                    item.get(
                        "published_at",
                        "",
                    )
                ),
                "discovery_score": (
                    item.get(
                        "_discovery_score",
                        0.0,
                    )
                ),
                "evergreen_deprioritized": (
                    item.get(
                        "_evergreen_deprioritized",
                        False,
                    )
                ),
                "queries": (
                    item.get(
                        "_discovery_queries",
                        [],
                    )
                ),
                "previously_seen_url": bool(item.get("_previously_seen_url")),
                "previously_seen_title": bool(item.get("_previously_seen_title")),
            }
            for item
            in fetch_candidates
        ]

        source_ids: list[str] = []

        discovery_failures: list[
            dict[str, str]
        ] = []

        selected_sources: list[
            dict[str, Any]
        ] = []

        # Content-level freshness is enforced after fetch, not merely recorded.
        # This catches the same story resurfacing under a different URL/title.
        selected_content_fingerprints: set[str] = set()

        pdf_extractor_available = (
            callable(
                getattr(
                    self.source_fetch_service,
                    "document_text_extractor",
                    None,
                )
            )
        )

        for item in (
            fetch_candidates
        ):
            if (
                len(source_ids)
                >= max_sources
            ):
                break

            url = _clean(
                item.get(
                    "url"
                )
                or item.get(
                    "canonical_url"
                )
                or item.get(
                    "link"
                )
            )

            if not url:
                continue

            url_without_query = (
                url.split(
                    "?",
                    1,
                )[0]
                .split(
                    "#",
                    1,
                )[0]
                .casefold()
            )

            if (
                url_without_query
                .endswith(
                    ".pdf"
                )
                and (
                    research_depth
                    == "fast"
                    or not
                    pdf_extractor_available
                )
            ):
                discovery_failures.append(
                    {
                        "url": url,
                        "error": (
                            "SourceSkipped"
                        ),
                        "message": (
                            "PDF candidate skipped "
                            "for current execution "
                            "profile/runtime."
                        ),
                    }
                )

                continue

            normalized_published_at = (
                self
                ._normalize_discovery_published_at(
                    item.get(
                        "published_at"
                    )
                    or item.get(
                        "published"
                    )
                    or item.get(
                        "date"
                    )
                )
            )

            try:
                fetched = (
                    self._call_method(
                        self.source_fetch_service,
                        (
                            "fetch",
                            "fetch_url",
                            "fetch_source",
                        ),
                        {
                            "url": url,
                            "source_url": url,
                        },
                    )
                )

                fetched_mapping = (
                    _as_mapping(
                        fetched
                    )
                )

                fetched_fingerprint = _clean(
                    fetched_mapping.get("text_sha256")
                    or fetched_mapping.get("raw_sha256")
                )
                if (
                    research_depth == "fast"
                    and fetched_fingerprint
                    and (
                        fetched_fingerprint in recent_content_fingerprints
                        or fetched_fingerprint in selected_content_fingerprints
                    )
                ):
                    discovery_failures.append(
                        {
                            "url": url,
                            "error": "SourceSkippedRepeatContent",
                            "message": (
                                "Fast source skipped after fetch because its "
                                "content fingerprint was already seen in a "
                                "recent run or earlier in this run."
                            ),
                        }
                    )
                    continue

                fetched_metadata = fetched_mapping.get("metadata") or {}
                if not isinstance(fetched_metadata, Mapping):
                    fetched_metadata = {}
                if (
                    research_depth == "fast"
                    and _enum(fetched_metadata.get("content_page_type"))
                    == "listing_or_roundup"
                ):
                    raise ResearchPipelineError(
                        "Fast source skipped: fetched page is a listing/roundup teaser surface, not a single article."
                    )

                # Deep should reason over article-sized, event-bearing pages, not
                # massive reports/roundups that explode into dozens of local-LLM
                # extraction chunks. Reject them here so discovery can substitute
                # another source instead of burning the entire research budget.
                deep_fetched_text = _normalize_source_text_preserve_boundaries(
                    fetched_mapping.get("text_content")
                    or fetched_mapping.get("raw_text")
                )
                if research_depth == "deep":
                    page_type = _enum(fetched_metadata.get("content_page_type"))
                    if page_type == "listing_or_roundup":
                        raise ResearchPipelineError(
                            "Deep source skipped: listing/roundup teaser surface is too noisy for event extraction."
                        )
                    if (
                        deep_fetched_text
                        and len(deep_fetched_text) > DEEP_MAX_FETCHED_TEXT_CHARACTERS
                    ):
                        raise ResearchPipelineError(
                            "Deep source skipped: fetched text is oversized for bounded local extraction "
                            f"({len(deep_fetched_text)} characters > "
                            f"{DEEP_MAX_FETCHED_TEXT_CHARACTERS})."
                        )

                legacy_persist = any(
                    callable(
                        getattr(
                            self.article_service,
                            name,
                            None,
                        )
                    )
                    for name in (
                        "upsert_fetched_source",
                        "save_fetched_source",
                    )
                )

                if legacy_persist:
                    write_result = (
                        self._call_method(
                            self.article_service,
                            (
                                "upsert_fetched_source",
                                "save_fetched_source",
                            ),
                            {
                                "fetched_source": (
                                    fetched_mapping
                                ),
                                "source": (
                                    fetched_mapping
                                ),
                            },
                        )
                    )

                else:
                    fetch_metadata = (
                        fetched_mapping.get(
                            "metadata"
                        )
                        or {}
                    )

                    if not isinstance(
                        fetch_metadata,
                        Mapping,
                    ):
                        fetch_metadata = {}

                    article_title = _clean(
                        fetch_metadata.get(
                            "title"
                        )
                        or item.get(
                            "title"
                        )
                        or fetched_mapping.get(
                            "canonical_url"
                        )
                        or url
                    )

                    article_content = (
                        deep_fetched_text
                        if research_depth == "deep"
                        else _normalize_source_text_preserve_boundaries(
                            fetched_mapping.get("text_content")
                            or fetched_mapping.get("raw_text")
                        )
                    )

                    if not article_content:
                        raise ResearchPipelineError(
                            (
                                "Fetched source "
                                "contains no "
                                "extractable text "
                                "content."
                            )
                        )

                    raw_metadata = {
                        **dict(
                            fetch_metadata
                        ),
                        "source_fetch_id": (
                            fetched_mapping.get(
                                "source_fetch_id"
                            )
                        ),
                        "requested_url": (
                            fetched_mapping.get(
                                "requested_url"
                            )
                        ),
                        "final_url": (
                            fetched_mapping.get(
                                "final_url"
                            )
                        ),
                        "canonical_url": (
                            fetched_mapping.get(
                                "canonical_url"
                            )
                        ),
                        "content_type": (
                            fetched_mapping.get(
                                "content_type"
                            )
                        ),
                        "raw_sha256": (
                            fetched_mapping.get(
                                "raw_sha256"
                            )
                        ),
                        "text_sha256": (
                            fetched_mapping.get(
                                "text_sha256"
                            )
                        ),
                        "search_result_id": (
                            item.get(
                                "result_id"
                            )
                        ),
                        "search_provider": (
                            item.get(
                                "provider"
                            )
                        ),
                        "search_rank": (
                            item.get(
                                "rank"
                            )
                        ),
                        "discovery_query": (
                            item.get(
                                "_discovery_query"
                            )
                        ),
                        "discovery_queries": (
                            item.get(
                                "_discovery_queries",
                                [],
                            )
                        ),
                        "discovery_score": (
                            item.get(
                                "_discovery_score"
                            )
                        ),
                        "discovery_published_at_raw": (
                            item.get(
                                "published_at"
                            )
                            or item.get(
                                "published"
                            )
                            or item.get(
                                "date"
                            )
                            or ""
                        ),
                        "discovery_published_at_normalized": (
                            normalized_published_at
                        ),
                        "evergreen_deprioritized": (
                            item.get(
                                "_evergreen_deprioritized",
                                False,
                            )
                        ),
                    }

                    write_result = (
                        self._call_method(
                            self.article_service,
                            (
                                "upsert_article",
                            ),
                            {
                                "url": (
                                    fetched_mapping.get(
                                        "canonical_url"
                                    )
                                    or fetched_mapping.get(
                                        "final_url"
                                    )
                                    or url
                                ),
                                "title": (
                                    article_title
                                ),
                                "content": (
                                    article_content
                                ),
                                "source_name": (
                                    item.get(
                                        "domain"
                                    )
                                    or item.get(
                                        "provider"
                                    )
                                    or ""
                                ),
                                "published_at": (
                                    self
                                    ._normalize_discovery_published_at(
                                        fetch_metadata.get(
                                            "published_at"
                                        )
                                    )
                                    or
                                    normalized_published_at
                                ),
                                "raw_metadata": (
                                    raw_metadata
                                ),
                            },
                        )
                    )

                result_mapping = (
                    _as_mapping(
                        write_result
                    )
                )

                source_id = _clean(
                    result_mapping.get(
                        "source_id"
                    )
                )

                if (
                    source_id
                    and source_id
                    not in source_ids
                ):
                    source_ids.append(
                        source_id
                    )
                    if fetched_fingerprint:
                        selected_content_fingerprints.add(
                            fetched_fingerprint
                        )

                    selected_sources.append(
                        {
                            "source_id": (
                                source_id
                            ),
                            "url": url,
                            "title": (
                                item.get(
                                    "title",
                                    "",
                                )
                            ),
                            "domain": (
                                item.get(
                                    "domain",
                                    "",
                                )
                            ),
                            "published_at": (
                                normalized_published_at
                                or ""
                            ),
                            "discovery_score": (
                                item.get(
                                    "_discovery_score",
                                    0.0,
                                )
                            ),
                            "queries": (
                                item.get(
                                    "_discovery_queries",
                                    [],
                                )
                            ),
                            "content_fingerprint": _clean(
                                fetched_mapping.get("text_sha256")
                                or fetched_mapping.get("raw_sha256")
                            ),
                            "previously_seen_url": (
                                self._discovery_url_identity(item) in recent_urls
                            ),
                            "previously_seen_content": (
                                _clean(
                                    fetched_mapping.get("text_sha256")
                                    or fetched_mapping.get("raw_sha256")
                                ) in recent_content_fingerprints
                                if _clean(
                                    fetched_mapping.get("text_sha256")
                                    or fetched_mapping.get("raw_sha256")
                                ) else False
                            ),
                        }
                    )

            except Exception as error:
                discovery_failures.append(
                    {
                        "url": url,
                        "error": (
                            type(error).__name__
                        ),
                        "message": (
                            str(error)
                        ),
                    }
                )

                logger.warning(
                    (
                        "Source discovery/"
                        "persistence failed. "
                        "url=%s error=%s "
                        "message=%s"
                    ),
                    url,
                    type(error).__name__,
                    str(error),
                )

        state[
            "source_discovery_failures"
        ] = discovery_failures

        state[
            "source_discovery_selected"
        ] = selected_sources
        state["source_discovery_new_source_count"] = sum(
            1 for item in selected_sources
            if not item.get("previously_seen_url") and not item.get("previously_seen_content")
        )
        state["source_discovery_repeat_source_count"] = sum(
            1 for item in selected_sources
            if item.get("previously_seen_url") or item.get("previously_seen_content")
        )
        self._save_source_history(topic, selected_sources)

        # Deep substitution round: blocked/oversized/timeout sources must not
        # starve strategic reasoning. After the initial pass, search again with
        # failed domains explicitly excluded and fill only the remaining slots.
        if (
            research_depth == "deep"
            and discovery_phase == "initial"
            and len(source_ids) < min(
                int(max_sources),
                DEEP_TARGET_USABLE_INITIAL_SOURCES,
            )
        ):
            failed_domains: list[str] = []
            for failure in discovery_failures:
                failed_url = _clean(failure.get("url"))
                if not failed_url:
                    continue
                try:
                    domain = (urlparse(failed_url).hostname or "").casefold()
                except Exception:
                    domain = ""
                if domain.startswith("www."):
                    domain = domain[4:]
                if domain and domain not in failed_domains:
                    failed_domains.append(domain)

            # Keep queries below provider query-length limits. Excluding the
            # first several failed domains is enough to rotate away from the
            # repeatedly blocked authority-heavy result set.
            exclusion = " ".join(
                f"-site:{domain}"
                for domain in failed_domains[:10]
            )
            now = datetime.now(timezone.utc)
            year = now.year
            month_year = now.strftime("%B %Y")
            replacement_queries = _unique_strings([
                f"{topic} {month_year} announcement launch deployment acquisition partnership {exclusion}",
                f"{topic} {year} enterprise implementation contract customer production {exclusion}",
                f"{topic} {year} regulation policy enforcement government decision {exclusion}",
                f"{topic} {year} investment infrastructure capacity adoption benchmark {exclusion}",
            ])[:DEEP_SUBSTITUTION_QUERY_COUNT]

            remaining = max(0, int(max_sources) - len(source_ids))
            if remaining > 0 and replacement_queries:
                logger.info(
                    "Deep source substitution starting. usable=%s target=%s "
                    "remaining=%s excluded_domains=%s",
                    len(source_ids),
                    min(int(max_sources), DEEP_TARGET_USABLE_INITIAL_SOURCES),
                    remaining,
                    failed_domains[:10],
                )

                # Preserve initial diagnostics because the recursive discovery
                # call owns the same state mapping.
                initial_failures = list(discovery_failures)
                initial_selected = list(selected_sources)
                initial_queries = list(discovery_queries)

                replacement_ids = self._discover_sources(
                    topic=topic,
                    max_sources=remaining,
                    state=state,
                    discovery_queries_override=replacement_queries,
                    discovery_phase="deep_substitution",
                )

                replacement_failures = list(
                    state.get("source_discovery_failures", []) or []
                )
                replacement_selected = list(
                    state.get("source_discovery_selected", []) or []
                )
                for replacement_id in replacement_ids:
                    if replacement_id and replacement_id not in source_ids:
                        source_ids.append(replacement_id)
                        if len(source_ids) >= int(max_sources):
                            break

                selected_by_id: dict[str, dict[str, Any]] = {}
                for selected in initial_selected + replacement_selected:
                    sid = _clean(selected.get("source_id"))
                    key = sid or _clean(selected.get("url"))
                    if key and key not in selected_by_id:
                        selected_by_id[key] = selected

                selected_sources = list(selected_by_id.values())
                discovery_failures = initial_failures + replacement_failures
                state["source_discovery_queries"] = _unique_strings(
                    initial_queries + replacement_queries
                )
                state["source_discovery_failures"] = discovery_failures
                state["source_discovery_selected"] = selected_sources
                state["source_discovery_substitution_used"] = True
                state["source_discovery_substitution_excluded_domains"] = failed_domains[:10]
                state["source_discovery_new_source_count"] = sum(
                    1 for item in selected_sources
                    if not item.get("previously_seen_url")
                    and not item.get("previously_seen_content")
                )
                state["source_discovery_repeat_source_count"] = sum(
                    1 for item in selected_sources
                    if item.get("previously_seen_url")
                    or item.get("previously_seen_content")
                )

                logger.info(
                    "Deep source substitution completed. usable=%s minimum=%s",
                    len(source_ids),
                    DEEP_MIN_USABLE_INITIAL_SOURCES,
                )
        else:
            state.setdefault("source_discovery_substitution_used", False)

        return source_ids

    @staticmethod
    def _fast_recovery_query_rounds(
        topic: str,
    ) -> list[list[str]]:
        """
        Two deliberately different Fast recovery rounds.

        Round 1 favors discrete official/company/regulatory developments.
        Round 2 broadens toward buyer behavior, quantitative adoption,
        infrastructure/capacity, and named enforcement/contract evidence.

        Recovery stops as soon as >=3 observed events from >=3 source records
        survive extraction. The second round is never paid for when round 1
        already satisfies the minimum.
        """
        topic = _clean(topic)
        now = datetime.now(timezone.utc)
        month_year = now.strftime("%B %Y")
        year = now.year

        round_one = _unique_strings([
            f"{topic} {month_year} official announcement launch release press release",
            f"{topic} {year} acquisition investment partnership contract customer deployment recent",
            f"{topic} {year} regulation enforcement government decision policy recent",
            f"{topic} {month_year} research breakthrough infrastructure investment deployment",
        ])

        round_two = _unique_strings([
            f"{topic} {month_year} enterprise customer production deployment contract implementation",
            f"{topic} {year} regulator enforcement transparency compliance effective date decision",
            f"{topic} {year} funding acquisition infrastructure capacity data center chips investment",
            f"{topic} {year} adoption survey benchmark report percentage enterprise deployment",
        ])

        return [
            list(round_one),
            list(round_two),
        ]

    @classmethod
    def _fast_recovery_queries(
        cls,
        topic: str,
    ) -> list[str]:
        # Backward-compatible helper used by diagnostics/tests.
        rounds = cls._fast_recovery_query_rounds(topic)
        return list(rounds[0]) if rounds else []

    @staticmethod
    def _fast_event_is_observed(event: Mapping[str, Any]) -> bool:
        temporal = _enum(event.get("temporal_status"))
        metadata = event.get("extraction_metadata") or {}
        if not isinstance(metadata, Mapping):
            metadata = {}
        taxonomy = _enum(metadata.get("event_taxonomy") or metadata.get("content_boundary_adjudication"))
        if temporal in {"future", "uncertain"}:
            return False
        if taxonomy and taxonomy not in {"observed_event"}:
            return False
        return bool(_clean(event.get("event_id")) and _clean(event.get("source_id")))

    def _fast_recover_minimum_events(
        self,
        *,
        topic: str,
        selected_source_ids: Sequence[str],
        event_records: Sequence[Mapping[str, Any]],
        state: dict[str, Any],
        minimum_events: int = 3,
        minimum_sources: int = 3,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """
        Recover from an initially thin/dirty Fast evidence set.

        v2.7 performed only one recovery batch. If that batch contained
        marketing/background/repeat material, Fast stopped with one real event.

        v2.8 performs at most two bounded rounds, with different query families,
        re-checking clean observed-event sufficiency after each round.
        """
        merged_by_id: dict[str, dict[str, Any]] = {}
        for event in event_records:
            if not isinstance(event, Mapping):
                continue
            key = _clean(event.get("event_id")) or uuid.uuid4().hex
            merged_by_id[key] = dict(event)

        all_source_ids = list(_unique_strings(selected_source_ids))

        def snapshot() -> tuple[list[dict[str, Any]], set[str]]:
            merged = list(merged_by_id.values())
            observed_local = [
                dict(e)
                for e in merged
                if self._fast_event_is_observed(e)
            ]
            source_local = {
                _clean(e.get("source_id"))
                for e in observed_local
                if _clean(e.get("source_id"))
            }
            return observed_local, source_local

        observed, observed_sources = snapshot()

        if (
            len(observed) >= minimum_events
            and len(observed_sources) >= minimum_sources
        ):
            state["fast_evidence_recovery"] = {
                "attempted": False,
                "reason": "initial_evidence_sufficient",
                "observed_event_count": len(observed),
                "observed_source_count": len(observed_sources),
                "minimum_satisfied": True,
            }
            return all_source_ids, list(merged_by_id.values())

        initial_event_count = len(observed)
        initial_source_count = len(observed_sources)
        round_diagnostics: list[dict[str, Any]] = []

        for round_index, queries in enumerate(
            self._fast_recovery_query_rounds(topic),
            start=1,
        ):
            recovery_state: dict[str, Any] = {
                "research_depth": "fast",
                "warnings": [],
                "errors": [],
                "stage_status": {},
                "topic": topic,
                "run_id": _clean(state.get("run_id")),
            }

            try:
                discovered = self._discover_sources_with_run_circuit_breaker(
                    topic=topic,
                    max_sources=4,
                    parent_state=state,
                    discovery_state=recovery_state,
                    discovery_queries_override=queries,
                    discovery_phase=(
                        f"minimum_evidence_recovery_round_{round_index}"
                    ),
                )
            except Exception as error:
                round_diagnostics.append(
                    {
                        "round": round_index,
                        "error": type(error).__name__,
                        "message": str(error),
                        "queries": list(queries),
                    }
                )
                continue

            existing = set(all_source_ids)
            new_ids = [
                sid
                for sid in _unique_strings(discovered)
                if sid not in existing
            ][:4]

            if not new_ids:
                circuit_open = self._fresh_discovery_circuit_is_open(state)
                round_diagnostics.append(
                    {
                        "round": round_index,
                        "new_source_count": 0,
                        "queries": list(queries),
                        "fresh_discovery_circuit_open": circuit_open,
                        "status": (
                            "recovery_stopped_provider_circuit_open"
                            if circuit_open
                            else "no_new_sources"
                        ),
                        "warnings": recovery_state.get("warnings", []),
                        "search_failures": recovery_state.get(
                            "source_discovery_search_failures",
                            [],
                        ),
                        "fetch_failures": recovery_state.get(
                            "source_discovery_failures",
                            [],
                        ),
                    }
                )
                if circuit_open:
                    break
                continue

            all_source_ids = list(
                _unique_strings(
                    list(all_source_ids)
                    + list(new_ids)
                )
            )

            new_events = self._extract_events(
                source_ids=new_ids,
                state=recovery_state,
            )

            for event in new_events:
                if not isinstance(event, Mapping):
                    continue
                key = _clean(event.get("event_id")) or uuid.uuid4().hex
                merged_by_id[key] = dict(event)

            observed, observed_sources = snapshot()

            round_diagnostics.append(
                {
                    "round": round_index,
                    "queries": list(queries),
                    "new_source_count": len(new_ids),
                    "new_source_ids": list(new_ids),
                    "new_event_count": len(new_events),
                    "observed_event_count_after": len(observed),
                    "observed_source_count_after": len(observed_sources),
                    "minimum_satisfied": (
                        len(observed) >= minimum_events
                        and len(observed_sources) >= minimum_sources
                    ),
                    "warnings": recovery_state.get("warnings", []),
                    "search_failures": recovery_state.get(
                        "source_discovery_search_failures",
                        [],
                    ),
                    "fetch_failures": recovery_state.get(
                        "source_discovery_failures",
                        [],
                    ),
                }
            )

            if (
                len(observed) >= minimum_events
                and len(observed_sources) >= minimum_sources
            ):
                break

        observed, observed_sources = snapshot()

        state["fast_evidence_recovery"] = {
            "attempted": True,
            "initial_observed_event_count": initial_event_count,
            "initial_observed_source_count": initial_source_count,
            "rounds_attempted": len(round_diagnostics),
            "rounds": round_diagnostics,
            "observed_event_count_after": len(observed),
            "observed_source_count_after": len(observed_sources),
            "minimum_satisfied": (
                len(observed) >= minimum_events
                and len(observed_sources) >= minimum_sources
            ),
        }

        return all_source_ids, list(merged_by_id.values())

    def _deep_bootstrap_zero_events(
        self,
        *,
        topic: str,
        selected_source_ids: Sequence[str],
        state: dict[str, Any],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Recover a Deep run whose initial extraction produced zero events.

        This is deliberately narrower than the normal V3.25 strategic-corpus refill.
        The regular refill runs only after at least one EVENT/EVID universe exists,
        while this bootstrap exists solely to prevent Deep mode from failing before
        it can reach that stage.

        Policy:
        - trigger only when initial Deep extraction produced zero events;
        - reuse the existing strategic-corpus refill query family so bootstrap search
          stays topic-agnostic and development-oriented;
        - perform one bounded fresh discovery batch;
        - extract only newly discovered sources;
        - never manufacture events or weaken event/evidence admission;
        - if recovery still yields zero events, return empty and let ``run()`` fail
          closed with an explicit bootstrap diagnostic.
        """
        current_source_ids = list(_unique_strings(selected_source_ids))

        empty_assessment = self._strategic_corpus_sufficiency_assessment(
            topic=topic,
            events=[],
            evidence=[],
        )
        queries = self._strategic_corpus_refill_queries(
            topic=topic,
            assessment=empty_assessment,
            events=[],
            round_number=1,
        )

        diagnostics: dict[str, Any] = {
            "attempted": True,
            "trigger": "initial_deep_extraction_zero_events",
            "status": "started",
            "queries": list(queries),
            "initial_source_ids": list(current_source_ids),
            "initial_source_count": len(current_source_ids),
            "max_queries": DEEP_CORPUS_REFILL_MAX_QUERIES_PER_ROUND,
            "max_sources": DEEP_CORPUS_REFILL_MAX_SOURCES_PER_ROUND,
            "new_source_ids": [],
            "recovered_event_ids": [],
            "recovered_event_count": 0,
            "search_failures": [],
            "fetch_failures": [],
            "extraction_failures": [],
            "invariant": (
                "bootstrap_may_expand_retrieval_but_must_not_lower_event_or_evidence_standards"
            ),
        }

        if not queries:
            diagnostics["status"] = "no_bootstrap_queries_generated"
            state["deep_zero_event_bootstrap"] = _json_safe(diagnostics)
            return current_source_ids, []

        bootstrap_state: dict[str, Any] = {
            "run_id": state.get("run_id"),
            "topic": topic,
            "requested_research_depth": "deep",
            "research_depth": "deep",
            "warnings": [],
            "errors": [],
            "stage_status": {},
            "depth_integrity": {
                "requested": "deep",
                "resolved_profile": "deep",
                "extraction": None,
            },
        }

        try:
            discovered = self._discover_sources_with_run_circuit_breaker(
                topic=topic,
                max_sources=DEEP_CORPUS_REFILL_MAX_SOURCES_PER_ROUND,
                parent_state=state,
                discovery_state=bootstrap_state,
                discovery_queries_override=queries,
                discovery_phase="deep_zero_event_bootstrap",
            )
        except Exception as error:
            diagnostics["status"] = "bootstrap_search_failed"
            diagnostics["error"] = {
                "type": type(error).__name__,
                "message": str(error),
            }
            diagnostics["search_failures"] = list(
                bootstrap_state.get("source_discovery_search_failures") or []
            )
            diagnostics["fetch_failures"] = list(
                bootstrap_state.get("source_discovery_failures") or []
            )
            state["deep_zero_event_bootstrap"] = _json_safe(diagnostics)
            state.setdefault("warnings", []).append(
                {
                    "type": "deep_zero_event_bootstrap_search_failed",
                    "message": str(error),
                }
            )
            return current_source_ids, []

        existing = set(current_source_ids)
        new_source_ids = [
            source_id
            for source_id in _unique_strings(discovered)
            if source_id not in existing
        ][:DEEP_CORPUS_REFILL_MAX_SOURCES_PER_ROUND]

        diagnostics["discovered_source_ids"] = list(_unique_strings(discovered))
        diagnostics["new_source_ids"] = list(new_source_ids)
        diagnostics["search_failures"] = list(
            bootstrap_state.get("source_discovery_search_failures") or []
        )
        diagnostics["fetch_failures"] = list(
            bootstrap_state.get("source_discovery_failures") or []
        )

        if not new_source_ids:
            diagnostics["status"] = "bootstrap_found_no_new_sources"
            state["deep_zero_event_bootstrap"] = _json_safe(diagnostics)
            return current_source_ids, []

        merged_source_ids = list(
            _unique_strings(list(current_source_ids) + list(new_source_ids))
        )

        try:
            recovered_events = self._extract_events(
                source_ids=new_source_ids,
                state=bootstrap_state,
            )
        except Exception as error:
            diagnostics["status"] = "bootstrap_extraction_failed"
            diagnostics["error"] = {
                "type": type(error).__name__,
                "message": str(error),
            }
            diagnostics["extraction_failures"] = list(
                bootstrap_state.get("extraction_failures") or []
            )
            state["deep_zero_event_bootstrap"] = _json_safe(diagnostics)
            state.setdefault("warnings", []).append(
                {
                    "type": "deep_zero_event_bootstrap_extraction_failed",
                    "message": str(error),
                }
            )
            return merged_source_ids, []

        recovered_events = [
            dict(item)
            for item in recovered_events
            if isinstance(item, Mapping) and _clean(item.get("event_id"))
        ]
        diagnostics["extraction_failures"] = list(
            bootstrap_state.get("extraction_failures") or []
        )
        diagnostics["recovered_event_ids"] = [
            _clean(item.get("event_id"))
            for item in recovered_events
            if _clean(item.get("event_id"))
        ]
        diagnostics["recovered_event_count"] = len(recovered_events)
        diagnostics["status"] = (
            "recovered_events"
            if recovered_events
            else "bootstrap_exhausted_zero_events"
        )

        if bootstrap_state.get("warnings"):
            diagnostics["warnings"] = list(bootstrap_state.get("warnings") or [])

        state["deep_zero_event_bootstrap"] = _json_safe(diagnostics)

        logger.info(
            "deep_zero_event_bootstrap=%s",
            json.dumps(
                _json_safe(diagnostics),
                ensure_ascii=False,
                default=str,
            ),
        )

        return merged_source_ids, recovered_events

    @staticmethod
    def _normalize_discovery_published_at(
        value: Any,
    ) -> Optional[str]:

        text = _clean(
            value
        )

        if not text:
            return None

        candidate = (
            text[:-1]
            + "+00:00"
            if text.endswith(
                "Z"
            )
            else text
        )

        try:
            parsed = (
                datetime
                .fromisoformat(
                    candidate
                )
            )

            if (
                parsed.tzinfo
                is None
            ):
                parsed = (
                    parsed.replace(
                        tzinfo=(
                            timezone.utc
                        )
                    )
                )
            else:
                parsed = (
                    parsed.astimezone(
                        timezone.utc
                    )
                )

            return (
                parsed.isoformat()
            )

        except ValueError:
            pass

        try:
            parsed = (
                email.utils
                .parsedate_to_datetime(
                    text
                )
            )

            if parsed is not None:
                if (
                    parsed.tzinfo
                    is None
                ):
                    parsed = (
                        parsed.replace(
                            tzinfo=(
                                timezone.utc
                            )
                        )
                    )
                else:
                    parsed = (
                        parsed.astimezone(
                            timezone.utc
                        )
                    )

                return (
                    parsed.isoformat()
                )

        except Exception:
            pass

        normalized = (
            " ".join(
                text.split()
            )
        )

        patterns = (
            "%a, %m/%d/%Y - %H:%M",
            "%m/%d/%Y - %H:%M",
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M",
            "%b %d, %Y",
            "%B %d, %Y",
            "%d %b %Y",
            "%d %B %Y",
            "%Y-%m-%d",
        )

        for pattern in patterns:
            try:
                parsed = (
                    datetime.strptime(
                        normalized,
                        pattern,
                    )
                    .replace(
                        tzinfo=(
                            timezone.utc
                        )
                    )
                )

                return (
                    parsed.isoformat()
                )

            except ValueError:
                continue

        return None

    @staticmethod
    def _build_initial_discovery_queries(
        *,
        topic: str,
        research_depth: str,
        rotation: int = 0,
    ) -> list[str]:
        topic = _clean(topic)
        now = datetime.now(timezone.utc)
        month_year = now.strftime("%B %Y")
        current_year = now.year

        focus_sets = (
            (
                f"{topic} {month_year} latest significant developments announcements",
                f"{topic} {current_year} enterprise adoption customer deployment contracts procurement",
                f"{topic} {current_year} regulation enforcement government decisions",
                f"{topic} {current_year} launches partnerships acquisitions funding new",
            ),
            (
                f"{topic} {month_year} new developments since last month",
                f"{topic} {current_year} buyer requirements RFP contracts implementation",
                f"{topic} {current_year} infrastructure investment capacity supply chain deployment",
                f"{topic} {current_year} incidents failures restrictions enforcement",
            ),
            (
                f"{topic} {month_year} latest market changes",
                f"{topic} {current_year} enterprise production rollout customer case study",
                f"{topic} {current_year} policy procurement compliance requirements",
                f"{topic} {current_year} company strategy new business unit product launch",
            ),
        )
        queries = list(focus_sets[int(rotation or 0) % len(focus_sets)])
        if _enum(research_depth) == "deep":
            queries.extend([
                f"{topic} {current_year} market impact investment supply chain recent developments",
                f"{topic} {current_year} research breakthroughs deployments risks recent",
            ])
        return _unique_strings(queries)

    @staticmethod
    def _discovery_url_identity(
        item: Mapping[str, Any],
    ) -> str:

        url = _clean(
            item.get(
                "canonical_url"
            )
            or item.get(
                "url"
            )
            or item.get(
                "link"
            )
        )

        if not url:
            return ""

        return (
            url.split(
                "#",
                1,
            )[0]
            .rstrip("/")
            .casefold()
        )

    @staticmethod
    def _discovery_domain(
        item: Mapping[str, Any],
    ) -> str:

        domain = _clean(
            item.get(
                "domain"
            )
        ).casefold()

        if domain:
            return (
                domain[4:]
                if domain.startswith(
                    "www."
                )
                else domain
            )

        url = _clean(
            item.get(
                "canonical_url"
            )
            or item.get(
                "url"
            )
            or item.get(
                "link"
            )
        ).casefold()

        if "://" in url:
            url = (
                url.split(
                    "://",
                    1,
                )[1]
            )

        domain = (
            url.split(
                "/",
                1,
            )[0]
        )

        if domain.startswith(
            "www."
        ):
            domain = (
                domain[4:]
            )

        return domain

    @classmethod
    def _score_initial_discovery_candidate(
        cls,
        item: Mapping[str, Any],
    ) -> tuple[float, bool]:

        title = _clean(
            item.get(
                "title"
            )
        ).casefold()

        snippet = _clean(
            item.get(
                "snippet"
            )
            or item.get(
                "description"
            )
            or item.get(
                "content"
            )
        ).casefold()

        url = _clean(
            item.get(
                "canonical_url"
            )
            or item.get(
                "url"
            )
            or item.get(
                "link"
            )
        ).casefold()

        domain = (
            cls._discovery_domain(
                item
            )
        )

        published_at = _clean(
            item.get(
                "published_at"
            )
            or item.get(
                "published"
            )
            or item.get(
                "date"
            )
        )

        text = (
            " ".join(
                (
                    title,
                    snippet,
                    url,
                )
            )
        )

        score = 0.0

        if published_at:
            score += 8.0
            normalized_published = cls._normalize_discovery_published_at(published_at)
            if normalized_published:
                try:
                    published_dt = datetime.fromisoformat(normalized_published)
                    age_days = (datetime.now(timezone.utc) - published_dt).total_seconds() / 86400.0
                    if age_days < -3:
                        score -= 20.0
                    elif age_days <= 7:
                        score += 30.0
                    elif age_days <= 30:
                        score += 20.0
                    elif age_days <= 90:
                        score += 10.0
                    elif age_days <= 180:
                        score += 2.0
                    elif age_days > 365:
                        score -= 20.0
                    else:
                        score -= 6.0
                except Exception:
                    pass
        else:
            score -= 4.0

        event_cues = {
            "announces": 7.0,
            "announced": 7.0,
            "launches": 7.0,
            "launched": 7.0,
            "release": 4.0,
            "released": 6.0,
            "acquires": 8.0,
            "acquisition": 8.0,
            "merger": 8.0,
            "invests": 7.0,
            "investment": 5.0,
            "funding": 7.0,
            "partnership": 7.0,
            "partners with": 7.0,
            "agreement": 5.0,
            "deal": 5.0,
            "regulation": 6.0,
            "regulator": 6.0,
            "policy": 4.0,
            "government": 4.0,
            "approves": 7.0,
            "approval": 6.0,
            "bans": 7.0,
            "ban": 5.0,
            "lawsuit": 6.0,
            "court": 4.0,
            "earnings": 5.0,
            "results": 3.0,
            "breakthrough": 6.0,
            "deployment": 5.0,
            "expands": 5.0,
            "expansion": 5.0,
        }

        for (
            cue,
            weight,
        ) in (
            event_cues.items()
        ):
            if cue in text:
                score += weight

        if any(
            marker in url
            for marker in (
                "/news/",
                "/newsroom/",
                "/press/",
                "/press-release",
                "/releases/",
                "/media/",
                "/announcement",
            )
        ):
            score += 10.0

        if (
            domain.endswith(
                ".gov"
            )
            or ".gov."
            in domain
            or domain.endswith(
                ".europa.eu"
            )
            or "commission.europa.eu"
            in domain
        ):
            score += 10.0

        evergreen_domains = (
            "wikipedia.org",
            "britannica.com",
            "coursera.org",
            "geeksforgeeks.org",
            "tutorialspoint.com",
            "w3schools.com",
            "investopedia.com",
        )

        evergreen_deprioritized = any(
            (
                domain
                == blocked
                or domain.endswith(
                    "."
                    + blocked
                )
            )
            for blocked
            in evergreen_domains
        )

        evergreen_title_patterns = (
            "what is ",
            "what are ",
            "explained",
            "introduction to ",
            "guide to ",
            "beginner",
            "tutorial",
            "definition of ",
            "meaning of ",
            "history of ",
            "overview of ",
            "learn ",
        )

        if any(
            pattern in title
            for pattern
            in evergreen_title_patterns
        ):
            score -= 25.0
            evergreen_deprioritized = (
                True
            )

        if evergreen_deprioritized:
            score -= 70.0

        if url:
            without_scheme = (
                url.split(
                    "://",
                    1,
                )[-1]
            )

            path = (
                without_scheme.split(
                    "/",
                    1,
                )[1]
                if "/"
                in without_scheme
                else ""
            )

            if not path.strip(
                "/"
            ):
                score -= 18.0

        try:
            rank = int(
                item.get(
                    "rank",
                    0,
                )
                or 0
            )

        except (
            TypeError,
            ValueError,
        ):
            rank = 0

        if rank > 0:
            score += max(
                0.0,
                10.0
                - float(rank),
            )

        return (
            round(
                score,
                3,
            ),
            evergreen_deprioritized,
        )

    @classmethod
    def _rank_initial_discovery_candidates(
        cls,
        items: Sequence[Mapping[str, Any]],
        *,
        research_depth: str,
        recent_urls: Optional[set[str]] = None,
        recent_titles: Optional[set[str]] = None,
    ) -> list[dict[str, Any]]:

        by_url: dict[
            str,
            dict[str, Any],
        ] = {}

        for raw_item in items:
            item = dict(
                raw_item
            )

            identity = (
                cls
                ._discovery_url_identity(
                    item
                )
            )

            if not identity:
                continue

            (
                score,
                evergreen,
            ) = (
                cls
                ._score_initial_discovery_candidate(
                    item
                )
            )

            identity_seen = bool(recent_urls and identity in recent_urls)
            title_identity = cls._history_title_identity(item.get("title"))
            title_seen = bool(recent_titles and title_identity and title_identity in recent_titles)
            if identity_seen:
                score -= 55.0
            elif title_seen:
                score -= 28.0

            item["_previously_seen_url"] = identity_seen
            item["_previously_seen_title"] = title_seen
            item[
                "_discovery_score"
            ] = round(score, 3)

            item[
                "_evergreen_deprioritized"
            ] = evergreen

            query = _clean(
                item.get(
                    "_discovery_query"
                )
            )

            item[
                "_discovery_queries"
            ] = (
                [
                    query
                ]
                if query
                else []
            )

            existing = (
                by_url.get(
                    identity
                )
            )

            if (
                existing
                is None
            ):
                by_url[
                    identity
                ] = item
                continue

            merged_queries = (
                _unique_strings(
                    list(
                        existing.get(
                            "_discovery_queries",
                            [],
                        )
                        or []
                    )
                    + list(
                        item.get(
                            "_discovery_queries",
                            [],
                        )
                        or []
                    )
                )
            )

            if (
                float(
                    item.get(
                        "_discovery_score",
                        0.0,
                    )
                )
                >
                float(
                    existing.get(
                        "_discovery_score",
                        0.0,
                    )
                )
            ):
                item[
                    "_discovery_queries"
                ] = merged_queries

                by_url[
                    identity
                ] = item

            else:
                existing[
                    "_discovery_queries"
                ] = merged_queries

        ranked = list(
            by_url.values()
        )

        ranked.sort(
            key=lambda item: (
                -float(
                    item.get(
                        "_discovery_score",
                        0.0,
                    )
                ),
                int(
                    item.get(
                        "_discovery_query_index",
                        999,
                    )
                    or 999
                ),
                int(
                    item.get(
                        "rank",
                        999,
                    )
                    or 999
                ),
                cls
                ._discovery_url_identity(
                    item
                ),
            )
        )

        return ranked

    @classmethod
    def _diversify_discovery_candidates(
        cls,
        items: Sequence[
            Mapping[str, Any]
        ],
        *,
        maximum_candidates: int,
        per_domain_limit: int,
    ) -> list[dict[str, Any]]:

        maximum_candidates = max(
            0,
            int(
                maximum_candidates
            ),
        )

        per_domain_limit = max(
            1,
            int(
                per_domain_limit
            ),
        )

        if (
            maximum_candidates
            == 0
        ):
            return []

        selected: list[
            dict[str, Any]
        ] = []

        overflow: list[
            dict[str, Any]
        ] = []

        domain_counts: dict[
            str,
            int,
        ] = {}

        for raw_item in items:
            item = dict(
                raw_item
            )

            domain = (
                cls._discovery_domain(
                    item
                )
                or "<unknown>"
            )

            current_count = (
                domain_counts.get(
                    domain,
                    0,
                )
            )

            if (
                current_count
                < per_domain_limit
            ):
                selected.append(
                    item
                )

                domain_counts[
                    domain
                ] = (
                    current_count
                    + 1
                )

                if (
                    len(selected)
                    >= maximum_candidates
                ):
                    return selected

            else:
                overflow.append(
                    item
                )

        for item in overflow:
            if (
                len(selected)
                >= maximum_candidates
            ):
                break

            selected.append(
                item
            )

        return selected

    @staticmethod
    def _extract_search_items(
        value: Any,
    ) -> list[dict[str, Any]]:

        if isinstance(
            value,
            list,
        ):
            return [
                _as_mapping(
                    item
                )
                for item
                in value
                if isinstance(
                    item,
                    Mapping,
                )
            ]

        if not isinstance(
            value,
            Mapping,
        ):
            return []

        for key in (
            "results",
            "search_results",
            "items",
            "records",
        ):
            candidate = (
                value.get(
                    key
                )
            )

            if isinstance(
                candidate,
                list,
            ):
                return [
                    _as_mapping(
                        item
                    )
                    for item
                    in candidate
                    if isinstance(
                        item,
                        Mapping,
                    )
                ]

        return []

    def _get_source_extraction_payload(
        self,
        source_id: str,
    ) -> dict[str, Any]:

        direct = getattr(
            self.article_service,
            "get_extraction_payload_by_source_id",
            None,
        )

        if callable(
            direct
        ):
            payload = (
                direct(
                    source_id
                )
            )

            mapping = (
                _as_mapping(
                    payload
                )
            )

            if not mapping:
                raise ResearchPipelineError(
                    (
                        "ArticleService returned "
                        "an empty extraction "
                        "payload for "
                        f"source_id={source_id!r}."
                    )
                )

            return mapping

        get_article = getattr(
            self.article_service,
            "get_by_source_id",
            None,
        )

        to_payload = getattr(
            self.article_service,
            "to_extraction_payload",
            None,
        )

        if (
            not callable(
                get_article
            )
            or not callable(
                to_payload
            )
        ):
            raise ResearchPipelineError(
                (
                    "ArticleService must expose "
                    "either "
                    "get_extraction_payload_by_"
                    "source_id(source_id), or "
                    "get_by_source_id(source_id) "
                    "plus "
                    "to_extraction_payload(article)."
                )
            )

        article = (
            get_article(
                source_id
            )
        )

        if article is None:
            raise ResearchPipelineError(
                (
                    "Persisted source not found "
                    f"for source_id={source_id!r}."
                )
            )

        payload = (
            to_payload(
                article
            )
        )

        mapping = (
            _as_mapping(
                payload
            )
        )

        if not mapping:
            raise ResearchPipelineError(
                (
                    "ArticleService returned "
                    "an empty extraction "
                    "payload for "
                    f"source_id={source_id!r}."
                )
            )

        return mapping

    def _source_payloads(
        self,
        source_ids: Sequence[str],
    ) -> list[dict[str, Any]]:

        return [
            self
            ._get_source_extraction_payload(
                source_id
            )
            for source_id
            in source_ids
        ]

    @staticmethod
    def _event_primary_institution(event: Mapping[str, Any]) -> str:
        if not isinstance(event, Mapping):
            return ""
        person_types = {
            "person", "individual", "executive", "founder", "ceo", "employee",
            "spokesperson", "organizer", "organiser", "researcher", "analyst",
        }
        institution_types = {
            "company", "organization", "organisation", "government",
            "government_body", "government_agency", "agency", "regulator",
            "institution", "municipality", "township", "city", "county",
            "state_government", "local_government", "public_body", "authority",
            "commission", "council", "university", "research_institution",
            "nonprofit", "non_profit", "ngo", "industry_body", "coalition",
        }
        for key in (
            "primary_actor", "acting_entity", "actor", "organization",
            "organisation", "company", "institution", "regulator",
        ):
            raw = event.get(key)
            name = _clean(
                raw.get("name") or raw.get("mention") or raw.get("entity_name")
                if isinstance(raw, Mapping) else raw
            )
            if name:
                return name[:240]
        candidates: list[tuple[int, str]] = []
        for raw in event.get("entity_mentions", []) or []:
            if not isinstance(raw, Mapping):
                continue
            name = _clean(raw.get("mention") or raw.get("name") or raw.get("entity_name"))
            entity_type = _enum(raw.get("entity_type") or raw.get("type") or "unknown")
            if not name or entity_type in person_types:
                continue
            score = 10 if entity_type in institution_types else 0
            if re.search(
                r"\b(?:inc|llc|ltd|corp|corporation|company|group|network|association|"
                r"coalition|university|institute|agency|department|ministry|township|"
                r"city|county|council|authority|commission|government)\b",
                name,
                re.I,
            ):
                score += 5
            if score:
                candidates.append((score, name[:240]))
        if not candidates:
            # Conservative text-only institutional fallback. Used only when
            # Extraction supplied no structured organization mention. Ordinary
            # capitalized person names are never promoted here.
            blob = " ".join(
                _clean(event.get(key))
                for key in ("title", "summary", "event", "description")
                if _clean(event.get(key))
            )
            regulator_aliases = (
                (r"\bFederal Communications Commission\b|\bFCC\b", "Federal Communications Commission"),
                (r"\bFederal Trade Commission\b|\bFTC\b", "Federal Trade Commission"),
                (r"\bSecurities and Exchange Commission\b|\bSEC\b", "Securities and Exchange Commission"),
                (r"\bCommodity Futures Trading Commission\b|\bCFTC\b", "Commodity Futures Trading Commission"),
            )
            for pattern, canonical in regulator_aliases:
                if re.search(pattern, blob, re.I):
                    return canonical
            suffix_match = re.search(
                r"\b([A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,6}\s+"
                r"(?:Commission|Agency|Department|Ministry|Authority|Council|University|"
                r"Institute|Institution|Group|Systems|Technologies|Corporation|Corp|Inc|PLC|Ltd))\b",
                blob,
            )
            if suffix_match:
                return _clean(suffix_match.group(1))[:240]
            return ""
        candidates.sort(key=lambda item: (-item[0], item[1].casefold()))
        return candidates[0][1]

    @staticmethod
    def _canonical_actor_key(value: Any) -> str:
        text = _clean(value).casefold()
        text = re.sub(r"[^a-z0-9 ]+", " ", text)
        for suffix in (" inc", " llc", " ltd", " corp", " corporation", " group", " plc"):
            if text.endswith(suffix) and len(text) > len(suffix) + 2:
                text = text[: -len(suffix)]
                break
        return " ".join(text.split())

    @staticmethod
    def _coarse_event_family(event: Mapping[str, Any]) -> str:
        blob = " ".join(
            _clean(event.get(k)) for k in ("event_type", "type", "title", "summary")
            if _clean(event.get(k))
        ).casefold()
        raw_type = _enum(event.get("event_type") or event.get("type"))
        explicit_family = {
            "regulation": "regulation",
            "policy_or_regulation": "regulation",
            "litigation": "litigation",
            "legal_action": "litigation",
            "financing": "financing",
            "m_and_a": "m_and_a",
            "merger_acquisition": "m_and_a",
            "partnership": "partnership",
            "commercial_contract": "partnership",
            "infrastructure": "infrastructure",
            "capacity_or_infrastructure": "infrastructure",
            "product_launch": "product_launch",
            "personnel": "personnel",
            "operations": "operations",
            "operating_metric": "operations",
        }.get(raw_type)
        if explicit_family:
            return explicit_family
        families = (
            ("litigation", r"\b(?:lawsuit|litigation|sued|sues|court|legal challenge|injunction)\b"),
            ("regulation", r"\b(?:regulat|ordinance|zoning|ban\b|banned|moratorium|permit|approval|policy|law\b|legislation|preemption)"),
            ("financing", r"\b(?:funding|financ|debt|equity|credit facility|bond|notes|raise[sd]?|investment round|capital raise)\b"),
            ("m_and_a", r"\b(?:acquir|acquisition|merg|takeover|buyout)"),
            ("partnership", r"\b(?:partner|partnership|agreement|contract|collaborat|alliance)"),
            ("infrastructure", r"\b(?:data center|datacenter|grid|power|electricity|transmission|substation|construction|buildout|capacity|water|cooling|site|siting)\b"),
            ("product_launch", r"\b(?:launch|release|introduc|product(?:s)?\b|server|platform|integration|deploy|rollout)"),
            ("personnel", r"\b(?:appoint|hire[sd]?|hiring|chief executive|ceo|personnel|workforce|layoff)\b"),
            ("operations", r"\b(?:production|manufactur|shipment|supply|facility|operations?|output)\b"),
        )
        for family, pattern in families:
            if re.search(pattern, blob, re.I):
                return family
        return _enum(event.get("event_type") or event.get("type")) or "other"

    @staticmethod
    def _strategic_event_text(
        event: Mapping[str, Any],
        evidence_items: Sequence[Mapping[str, Any]],
    ) -> str:
        """Bounded factual text used only for deterministic admission/clustering."""
        parts: list[str] = []
        for key in (
            "title", "summary", "event", "description", "event_type",
            "event_time_text",
        ):
            value = _clean(event.get(key))
            if value:
                parts.append(value)
        for item in evidence_items[:4]:
            if not isinstance(item, Mapping):
                continue
            value = _clean(
                item.get("claim")
                or item.get("text")
                or item.get("source_excerpt")
                or item.get("statement")
                or item.get("excerpt")
            )
            if value:
                parts.append(value)
        return " ".join(parts)[:6000]

    @staticmethod
    def _is_broad_ai_topic(topic: str) -> bool:
        value = _clean(topic).casefold()
        return bool(re.search(
            r"\b(?:artificial\s+intelligence|generative\s+ai|genai|agentic\s+ai|ai)\b",
            value,
            re.I,
        ))

    @classmethod
    def _strategic_cluster_for_event(
        cls,
        *,
        topic: str,
        event: Mapping[str, Any],
        text: str,
    ) -> str:
        """Assign one coarse strategic subdomain before candidate generation.

        For very broad AI queries, this prevents enterprise adoption, robotics,
        chips/infrastructure, policy and trust/governance from entering one
        undifferentiated candidate pool. For other topics the fallback remains
        deterministic and event-family based.
        """
        blob = _clean(text).casefold()
        family = cls._coarse_event_family(event)

        if cls._is_broad_ai_topic(topic):
            policy = bool(re.search(
                r"\b(?:regulat|policy|law\b|legislation|ban\b|banned|covered\s+list|"
                r"export\s+control|sanction|permit|approval|fcc\b|ftc\b|sec\b|"
                r"eu\s+ai\s+act|ai\s+act|government\s+rule|ordinance|zoning)\b",
                blob,
                re.I,
            ))
            robotics = bool(re.search(
                r"\b(?:robot|robotics|humanoid|quadruped|drone|autonomous\s+machine|"
                r"physical\s+ai|biped|robotic)\b",
                blob,
                re.I,
            ))
            infrastructure = bool(re.search(
                r"\b(?:gpu|accelerator|semiconductor|chip(?:s)?\b|advanced\s+packaging|"
                r"data\s*cent(?:er|re)|compute\s+capacity|nvlink|spectrum-x|optical\s+network|"
                r"inference\s+accelerator|training\s+cluster|power\s+capacity)\b",
                blob,
                re.I,
            ))
            provider_relationship = bool(re.search(
                r"\b(?:anthropic|openai|gemini|claude|gpt(?:-[0-9a-z.]+)?|foundation\s+model|"
                r"model\s+provider|model\s+routing|model\s+integration|mcp\b|"
                r"model\s+context\s+protocol|copilot|enterprise\s+adoption|pilot\s+use\s+case|"
                r"engineering\s+productivity|developer\s+workflow|ai\s+agent|agentic)\b",
                blob,
                re.I,
            ))
            trust_governance = bool(re.search(
                r"\b(?:trust|responsible\s+ai|governance|audit(?:ability)?|safety|"
                r"data\s+residency|jurisdiction|sovereignty|bias|compliance|institutional\s+trust|"
                r"risk\s+management)\b",
                blob,
                re.I,
            ))
            capital = bool(re.search(
                r"\b(?:funding|fundraise|investment\s+round|venture\s+capital|valuation|"
                r"acquisition|merger|equity\s+investment|debt\s+financing)\b",
                blob,
                re.I,
            ))

            # Regulation wins when the action itself is regulatory, even if the
            # regulated object is a robot or chip. This makes a cross-domain link
            # explicit rather than silently mixing policy with technology events.
            if policy and family in {"regulation", "litigation"}:
                return "ai_policy_regulation"
            if robotics:
                return "ai_physical_robotics"
            if infrastructure:
                return "ai_infrastructure_compute"
            if provider_relationship:
                return "ai_enterprise_model_relationships"
            if trust_governance:
                return "ai_trust_governance"
            if capital:
                return "ai_capital_transactions"
            if policy:
                return "ai_policy_regulation"
            return "ai_general"

        family_key = _enum(family) or "other"
        return f"topic_{family_key}"

    @classmethod
    def _topic_linkage_type(
        cls,
        *,
        topic: str,
        event: Mapping[str, Any],
        text: str,
    ) -> str:
        """Classify how the event is linked to the research topic.

        Only direct events and evidenced material enablers, constraints or
        consequences may enter strategic reasoning. Contextual/incidental items
        remain available in diagnostics but cannot shape synthesis.
        """
        blob = _clean(text).casefold()
        family = cls._coarse_event_family(event)
        material_action = bool(re.search(
            r"\b(?:announce|announced|launch|launched|deploy|deployed|adopt|adopted|"
            r"partner|partnered|sign|signed|contract|agreement|invest|invested|build|"
            r"built|approve|approved|ban|banned|restrict|restricted|restricting|regulate|regulated|"
            r"reported|reached|selected|awarded|introduced|unveiled|added|adds|cut|increased|"
            r"decreased|halted|cancelled|canceled|delayed)\b",
            blob,
            re.I,
        ))
        if cls._is_broad_ai_topic(topic):
            explicit_ai = bool(re.search(
                r"\b(?:artificial\s+intelligence|generative\s+ai|genai|agentic\s+ai|"
                r"physical\s+ai|large\s+language\s+models?|llms?\b|foundation\s+models?|"
                r"machine\s+learning|anthropic|openai|claude|chatgpt|copilot|"
                r"model\s+context\s+protocol|mcp\b|ai\s+agents?|ai\s+models?)\b",
                blob,
                re.I,
            ))
            robotics = bool(re.search(
                r"\b(?:robots?|robotics|humanoid|quadruped|robotic|physical\s+ai|"
                r"autonomous\s+machines?)\b",
                blob,
                re.I,
            ))
            ai_infrastructure = bool(re.search(
                r"\b(?:ai\s+(?:accelerator|chip|compute|data\s*cent(?:er|re)|cluster)|"
                r"gpu(?:s)?\b.{0,80}\b(?:ai|inference|training)|"
                r"(?:inference|training)\s+(?:accelerator|cluster|capacity)|"
                r"advanced\s+packaging.{0,100}\b(?:ai|accelerator|gpu)|"
                r"(?:ai|accelerator|gpu).{0,100}\badvanced\s+packaging)\b",
                blob,
                re.I,
            ))
            ai_constraint = bool(re.search(
                r"\b(?:ai|robot|humanoid|gpu|accelerator|model)\b.{0,100}\b"
                r"(?:ban|restrict|covered\s+list|approval|authorization|authorisation|"
                r"export\s+control|permit|regulat|sanction|compliance)\b|"
                r"\b(?:ban|restrict|covered\s+list|approval|authorization|authorisation|"
                r"export\s+control|permit|regulat|sanction|compliance)\b.{0,100}\b"
                r"(?:ai|robot|humanoid|gpu|accelerator|model)\b",
                blob,
                re.I,
            ))
            ai_consequence = bool(re.search(
                r"\b(?:due\s+to|because\s+of|driven\s+by|resulting\s+from|as\s+a\s+result\s+of)\b"
                r".{0,120}\b(?:ai|artificial\s+intelligence|gpu|model|robot)\b|"
                r"\b(?:ai|artificial\s+intelligence|gpu|model|robot)\b.{0,120}\b"
                r"(?:drove|driven|caused|increased|reduced|cut|boosted|delayed)\b",
                blob,
                re.I,
            ))
            if ai_constraint and material_action:
                return "material_constraint"
            if ai_consequence and material_action:
                return "material_consequence"
            if explicit_ai and material_action:
                return "direct"
            if robotics and material_action:
                return "direct"
            if ai_infrastructure and material_action:
                return "material_enabler"
            if explicit_ai or robotics or ai_infrastructure:
                return "contextual"
            return "none"

        topic_value = _clean(topic).casefold()
        if topic_value and topic_value in blob and material_action:
            return "direct"
        topic_tokens = {
            token for token in re.findall(r"[a-z0-9][a-z0-9_-]{3,}", topic_value)
            if token not in {"about", "latest", "news", "market", "industry", "development", "developments"}
        }
        hits = sum(1 for token in topic_tokens if re.search(rf"\b{re.escape(token)}\b", blob))
        if hits >= 2 and material_action:
            return "direct"
        if hits and family in {"regulation", "litigation"} and material_action:
            return "material_constraint"
        causal = bool(re.search(
            r"\b(?:due\s+to|because\s+of|driven\s+by|depends?\s+on|requires?|constrains?|"
            r"enables?|supplies?|customer|provider|approval|permit|capacity)\b",
            blob,
            re.I,
        ))
        if hits and causal and material_action:
            return "material_enabler"
        return "contextual" if hits else "none"

    @classmethod
    def _strategic_relation_signature(
        cls,
        *,
        topic: str,
        event: Mapping[str, Any],
        text: str,
        linkage_type: str,
    ) -> dict[str, Any]:
        """Deterministic mechanism/dependency signature for relation-aware clustering."""
        del topic
        blob = _clean(text).casefold()
        mechanism_patterns = {
            "foundation_model_procurement": r"\b(?:foundation\s+model|anthropic|openai|claude|gpt)\b.{0,100}\b(?:use|adopt|procure|partner|agreement|integration|deploy)",
            "developer_productivity": r"\b(?:developer|engineering|engineer)\b.{0,100}\b(?:productivity|talent|workflow|coding|efficien)",
            "platform_coopetition": r"\b(?:coopetition|compete|competitive\s+overlap|rival)\b.{0,120}\b(?:partner|provider|platform|model)|\bpartner\b.{0,120}\b(?:compete|rival)",
            "enterprise_integration": r"\b(?:workflow|integration|integrate|mcp\b|model\s+context\s+protocol|existing\s+environment|enterprise\s+system)",
            "baseline_validation": r"\b(?:baseline|parallel\s+run|shadow\s+run|pilot)\b.{0,120}\b(?:accuracy|bias|validation|outperform|parity|threshold)",
            "regulatory_authorization": r"\b(?:covered\s+list|authorization|authorisation|approval|permit|regulat|fcc\b|export\s+control|ban|restrict)",
            "robotics_commercialization": r"\b(?:robot|humanoid|quadruped|physical\s+ai)\b.{0,120}\b(?:sales|commercial|launch|deploy|market|customer|production)",
            "compute_capacity": r"\b(?:gpu|accelerator|data\s*cent(?:er|re)|compute|training\s+cluster|inference\s+cluster|capacity)\b",
            "advanced_packaging_capacity": r"\badvanced\s+packaging\b.{0,120}\b(?:capacity|facility|manufactur|accelerator|gpu|ai)",
            "power_capacity": r"\b(?:grid|power|electricity|transmission|substation|energy)\b.{0,120}\b(?:capacity|data\s*cent(?:er|re)|compute|ai)",
            "capital_formation": r"\b(?:funding|fundraise|equity|debt|credit\s+facility|investment\s+round|capital\s+raise|financing)\b",
            "customer_commitment": r"\b(?:customer|offtake|reserved\s+capacity|commitment|contract|purchase\s+agreement|anchor\s+tenant)\b",
            "switching_portability": r"\b(?:switching|portability|multi[- ]model|model\s+routing|interoperability|abstraction\s+layer)\b",
            "data_residency_governance": r"\b(?:data\s+residency|jurisdiction|sovereignty|governance|auditability|compliance)\b",
        }
        dependency_patterns = {
            "model_provider": r"\b(?:anthropic|openai|claude|gpt|foundation\s+model|model\s+provider)\b",
            "cloud_provider": r"\b(?:azure|aws|google\s+cloud|cloud\s+provider|cloud\s+platform)\b",
            "regulatory_approval": r"\b(?:authorization|authorisation|approval|permit|covered\s+list|fcc\b|regulat|ban|restrict)\b",
            "compute_supply": r"\b(?:gpu|accelerator|compute\s+capacity|training\s+cluster|inference\s+capacity)\b",
            "advanced_packaging_supply": r"\badvanced\s+packaging\b",
            "power_grid": r"\b(?:grid|power|electricity|transmission|substation)\b",
            "enterprise_workflow": r"\b(?:workflow|enterprise\s+system|existing\s+environment|mcp\b|integration)\b",
            "validation_process": r"\b(?:baseline|parallel\s+run|shadow\s+run|pilot|validation|parity|threshold)\b",
            "customer_demand": r"\b(?:customer|contract|offtake|reserved\s+capacity|commitment|demand)\b",
            "capital_access": r"\b(?:funding|debt|equity|credit\s+facility|financing|capital)\b",
            "model_portability": r"\b(?:switching|portability|multi[- ]model|model\s+routing|interoperability|abstraction)\b",
        }
        mechanism_tags = sorted(tag for tag, pattern in mechanism_patterns.items() if re.search(pattern, blob, re.I))
        dependency_tags = sorted(tag for tag, pattern in dependency_patterns.items() if re.search(pattern, blob, re.I))
        geography_patterns = (
            ("united_states", r"\b(?:united\s+states|u\.s\.|us\b|american)\b"),
            ("china", r"\b(?:china|chinese|shanghai|beijing|shenzhen)\b"),
            ("european_union", r"\b(?:european\s+union|eu\b)\b"),
            ("united_kingdom", r"\b(?:united\s+kingdom|u\.k\.|uk\b|britain|london)\b"),
            ("canada", r"\b(?:canada|canadian)\b"),
            ("india", r"\b(?:india|indian)\b"),
            ("africa", r"\b(?:africa|african)\b"),
        )
        geography = [tag for tag, pattern in geography_patterns if re.search(pattern, blob, re.I)]
        time_text = _clean(
            event.get("event_time") or event.get("event_time_text") or event.get("date")
            or event.get("published_at") or event.get("publication_date")
        )
        time_window = ""
        month_match = re.search(r"\b(20\d{2})[-/](0?[1-9]|1[0-2])\b", time_text)
        if month_match:
            time_window = f"{month_match.group(1)}-{int(month_match.group(2)):02d}"
        else:
            year_match = re.search(r"\b(20\d{2})\b", time_text or blob)
            if year_match:
                time_window = year_match.group(1)
        payload = {
            "topic_linkage_type": linkage_type,
            "mechanism_tags": mechanism_tags,
            "dependency_tags": dependency_tags,
            "geography": geography,
            "time_window": time_window,
        }
        payload["relation_signature_id"] = "RELSIG-" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:24].upper()
        return payload

    @classmethod
    def _strategic_event_assessment(
        cls,
        *,
        topic: str,
        event: Mapping[str, Any],
        evidence_items: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Classify whether an extracted EVENT belongs in strategic reasoning.

        This is intentionally deterministic and conservative. It does not create
        facts or infer hidden meaning. It asks only whether the record is a
        topic-relevant real-world change/action/outcome rather than boilerplate,
        stable background, opinion or unrelated feed noise.
        """
        text = cls._strategic_event_text(event, evidence_items)
        lowered = text.casefold()
        actor = cls._event_primary_institution(event)
        family = cls._coarse_event_family(event)
        temporal = _enum(event.get("temporal_status"))
        quantities = list(event.get("quantitative_claims", []) or [])

        boilerplate_patterns = (
            r"\bthis\s+press\s+release\s+should\s+be\s+read\s+in\s+conjunction\b",
            r"\bforward[- ]looking\s+statements?\b",
            r"\bsafe\s+harbor\s+statement\b",
            r"\bwe\s+intend\s+to\s+use\s+our\s+website\b",
            r"\bregulation\s+fd\b",
            r"\bfor\s+more\s+information\s+(?:please\s+)?visit\b",
            r"\bplease\s+refer\s+to\s+(?:our|the)\s+(?:sec\s+)?filings\b",
            r"\badditional\s+financial\s+and\s+operating\s+metrics\b",
            r"\babout\s+(?:the\s+)?company\b",
            r"\bcontacts?:\s*(?:investor|media|press)\b",
        )
        background_patterns = (
            r"\bis\s+a\s+(?:technology|software|global|leading)\s+company\s+(?:focused|that)\b",
            r"\bhas\s+demonstrated\b.{0,120}\bfor\s+years\b",
            r"\bhas\s+long\s+been\b",
            r"\bis\s+known\s+for\b",
            r"\bprovides?\s+a\s+range\s+of\b",
            r"\bour\s+mission\s+is\b",
        )
        if any(re.search(pattern, lowered, re.I) for pattern in boilerplate_patterns):
            return {
                "admit": False,
                "event_class": "boilerplate",
                "cluster_id": "excluded_boilerplate",
                "relevance_score": 0.0,
                "materiality_score": 0.0,
                "actor": actor,
                "reasons": ["boilerplate_or_disclosure_language"],
                "display_text": text[:600],
            }

        material_action = bool(re.search(
            r"\b(?:announc(?:e|ed|es|ing)|launch(?:ed|es|ing)?|release(?:d|s|ing)?|"
            r"deploy(?:ed|s|ing)?|adopt(?:ed|s|ion|ing)?|partner(?:ed|s|ship|ing)?|"
            r"sign(?:ed|s|ing)?|contract(?:ed|s|ing)?|agreement|invest(?:ed|s|ment|ing)?|"
            r"fund(?:ed|s|ing)?|rais(?:e|ed|es|ing)|acquir(?:e|ed|es|ing)|merg(?:e|ed|es|ing)|"
            r"build(?:s|ing|out)?|open(?:ed|s|ing)?|expand(?:ed|s|ing)?|approve(?:d|s|al)?|"
            r"ban(?:ned|s)?|restrict(?:ed|s|ion)?|regulat(?:ed|es|ion)|sue(?:d|s)?|"
            r"rule(?:d|s|ing)?|add(?:ed|s)\s+.{0,60}\bcovered\s+list\b|"
            r"cut(?:s|ting)?|increase(?:d|s|ing)?|decrease(?:d|s|ing)?|grew|growth|"
            r"reported|reached|won|lost|selected|awarded|introduced|unveiled|"
            r"halt(?:ed|s|ing)?|cancel(?:led|ed|s|ing)?|delay(?:ed|s|ing)?)\b",
            lowered,
            re.I,
        ))
        background_hit = any(
            re.search(pattern, lowered, re.I) for pattern in background_patterns
        )

        relevance = 0.0
        topic_value = _clean(topic).casefold()
        if cls._is_broad_ai_topic(topic):
            explicit_ai = bool(re.search(
                r"\b(?:artificial\s+intelligence|generative\s+ai|genai|agentic\s+ai|"
                r"physical\s+ai|large\s+language\s+model|llm(?:s)?\b|foundation\s+model|"
                r"machine\s+learning|anthropic|openai|claude|chatgpt|copilot|"
                r"model\s+context\s+protocol|mcp\b|ai\s+agent|ai\s+model)\b",
                lowered,
                re.I,
            ))
            if explicit_ai:
                relevance += 3.0
            if re.search(r"\b(?:robot|robotics|humanoid|quadruped|robotic|autonomous\s+machine)\b", lowered, re.I):
                relevance += 2.0
            if re.search(r"\b(?:gpu|accelerator|semiconductor|advanced\s+packaging|nvlink|data\s*cent(?:er|re)|compute\s+capacity)\b", lowered, re.I):
                relevance += 1.0
        else:
            if topic_value and topic_value in lowered:
                relevance += 3.0
            topic_stop = {
                "about", "latest", "news", "new", "development", "developments",
                "market", "industry", "company", "companies", "the", "and", "for",
            }
            topic_tokens = {
                token for token in re.findall(r"[a-z0-9][a-z0-9_-]{3,}", topic_value)
                if token not in topic_stop
            }
            token_hits = sum(1 for token in topic_tokens if re.search(rf"\b{re.escape(token)}\b", lowered))
            relevance += min(3.0, token_hits * 0.9)

        materiality = 0.0
        if material_action:
            materiality += 2.0
        if family not in {"", "other", "unknown"}:
            materiality += 0.8
        if actor:
            materiality += 0.8
        if quantities:
            materiality += 0.5
        if temporal in {"current", "future", "past"}:
            materiality += 0.2
        if background_hit and not material_action:
            materiality -= 2.5

        topic_linkage_type = cls._topic_linkage_type(
            topic=topic,
            event=event,
            text=text,
        )
        cluster_id = cls._strategic_cluster_for_event(
            topic=topic,
            event=event,
            text=text,
        )
        relation_signature = cls._strategic_relation_signature(
            topic=topic,
            event=event,
            text=text,
            linkage_type=topic_linkage_type,
        )

        if family == "regulation":
            event_class = "policy_or_regulation"
        elif family == "litigation":
            event_class = "legal_action"
        elif family == "financing":
            event_class = "financing"
        elif family == "m_and_a":
            event_class = "commercial_contract"
        elif family == "partnership":
            event_class = "commercial_contract"
        elif family == "infrastructure":
            event_class = "capacity_or_infrastructure"
        elif family == "product_launch":
            event_class = "product_launch"
        elif family == "operations" and quantities:
            event_class = "operating_metric"
        elif re.search(r"\b(?:earnings|revenue|financial\s+results?|quarter|half[- ]year)\b", lowered, re.I):
            event_class = "earnings_result"
        elif re.search(r"\b(?:share\s+price|stock|bond\s+yield|market\s+price|index)\b", lowered, re.I):
            event_class = "market_signal"
        elif material_action:
            event_class = "material_action"
        else:
            event_class = "background_fact"

        reasons: list[str] = []
        eligible_linkages = {"direct", "material_enabler", "material_constraint", "material_consequence"}
        if relevance < 2.0:
            reasons.append("topic_relevance_below_threshold")
        if materiality < 2.0:
            reasons.append("materiality_below_threshold")
        if topic_linkage_type not in eligible_linkages:
            reasons.append(f"topic_linkage_not_strategic:{topic_linkage_type or 'none'}")
        if background_hit and not material_action:
            reasons.append("stable_background_not_development")
        if not actor:
            reasons.append("specific_acting_institution_missing")
        admit = bool(
            relevance >= 2.0
            and materiality >= 2.0
            and bool(actor)
            and topic_linkage_type in eligible_linkages
            and event_class not in {"background_fact", "boilerplate", "opinion", "unrelated"}
        )
        return {
            "admit": admit,
            "event_class": event_class if admit else (
                "unrelated" if relevance < 2.0 or topic_linkage_type in {"none", "incidental"} else "background_fact"
            ),
            "cluster_id": cluster_id if admit else "excluded",
            "topic_linkage_type": topic_linkage_type,
            "relation_signature": relation_signature,
            "relevance_score": round(relevance, 3),
            "materiality_score": round(materiality, 3),
            "actor": actor,
            "reasons": reasons,
            "display_text": text[:600],
        }

    @classmethod
    def _prepare_strategic_event_context(
        cls,
        *,
        topic: str,
        events: Sequence[Mapping[str, Any]],
        evidence: Sequence[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        """Filter and cluster events before they are supplied to reasoning.

        All accepted Extraction/EvidenceBuilder records remain available in run
        diagnostics/persistence, but only material, topic-relevant EVENT records
        enter relationships, graph projection and StrategicInference. This makes
        the auditability invariant precise: every event *considered by reasoning*
        is represented in the released report, while admitted-but-irrelevant feed
        noise never shapes the model context.
        """
        evidence_by_event: dict[str, list[Mapping[str, Any]]] = {}
        for record in evidence:
            if not isinstance(record, Mapping):
                continue
            event_id = _clean(record.get("event_id")).upper()
            if event_id:
                evidence_by_event.setdefault(event_id, []).append(record)

        selected_events: list[dict[str, Any]] = []
        selected_event_ids: set[str] = set()
        excluded: list[dict[str, Any]] = []
        cluster_distribution: dict[str, int] = {}
        class_distribution: dict[str, int] = {}
        exclusion_reasons: dict[str, int] = {}
        linkage_distribution: dict[str, int] = {}
        mechanism_tag_distribution: dict[str, int] = {}
        dependency_tag_distribution: dict[str, int] = {}

        for raw_event in events:
            if not isinstance(raw_event, Mapping):
                continue
            event = dict(raw_event)
            event_id = _clean(event.get("event_id") or event.get("id")).upper()
            if not event_id:
                continue
            assessment = cls._strategic_event_assessment(
                topic=topic,
                event=event,
                evidence_items=evidence_by_event.get(event_id, []),
            )
            if assessment.get("admit"):
                event["strategic_admission_status"] = "considered"
                event["strategic_event_class"] = assessment.get("event_class")
                event["strategic_cluster_id"] = assessment.get("cluster_id")
                event["strategic_topic_relevance_score"] = assessment.get("relevance_score")
                event["strategic_materiality_score"] = assessment.get("materiality_score")
                event["strategic_admission_reasons"] = list(assessment.get("reasons") or [])
                event["strategic_topic_linkage_type"] = assessment.get("topic_linkage_type")
                relation_signature = assessment.get("relation_signature") if isinstance(assessment.get("relation_signature"), Mapping) else {}
                event["strategic_relation_signature"] = dict(relation_signature)
                event["strategic_mechanism_tags"] = list(relation_signature.get("mechanism_tags") or [])
                event["strategic_dependency_tags"] = list(relation_signature.get("dependency_tags") or [])
                event["strategic_geography"] = list(relation_signature.get("geography") or [])
                event["strategic_time_window"] = _clean(relation_signature.get("time_window"))
                selected_events.append(event)
                selected_event_ids.add(event_id)
                cluster_key = _clean(assessment.get("cluster_id")) or "unclustered"
                class_key = _clean(assessment.get("event_class")) or "other"
                cluster_distribution[cluster_key] = cluster_distribution.get(cluster_key, 0) + 1
                class_distribution[class_key] = class_distribution.get(class_key, 0) + 1
                linkage_key = _clean(assessment.get("topic_linkage_type")) or "unknown"
                linkage_distribution[linkage_key] = linkage_distribution.get(linkage_key, 0) + 1
                for tag in event.get("strategic_mechanism_tags", []) or []:
                    mechanism_tag_distribution[tag] = mechanism_tag_distribution.get(tag, 0) + 1
                for tag in event.get("strategic_dependency_tags", []) or []:
                    dependency_tag_distribution[tag] = dependency_tag_distribution.get(tag, 0) + 1
            else:
                reasons = list(assessment.get("reasons") or ["not_strategically_eligible"])
                for reason in reasons:
                    exclusion_reasons[reason] = exclusion_reasons.get(reason, 0) + 1
                excluded.append({
                    "event_id": event_id,
                    "event_class": assessment.get("event_class"),
                    "relevance_score": assessment.get("relevance_score"),
                    "materiality_score": assessment.get("materiality_score"),
                    "topic_linkage_type": assessment.get("topic_linkage_type"),
                    "relation_signature": assessment.get("relation_signature"),
                    "reasons": reasons,
                    "text": assessment.get("display_text"),
                })

        selected_evidence: list[dict[str, Any]] = []
        for record in evidence:
            if not isinstance(record, Mapping):
                continue
            event_id = _clean(record.get("event_id")).upper()
            if event_id not in selected_event_ids:
                continue
            item = dict(record)
            # Mirror cluster metadata onto EVID rows for trace/debug surfaces.
            event = next((e for e in selected_events if _clean(e.get("event_id")).upper() == event_id), None)
            if isinstance(event, Mapping):
                item["strategic_cluster_id"] = event.get("strategic_cluster_id")
                item["strategic_event_class"] = event.get("strategic_event_class")
                item["strategic_topic_linkage_type"] = event.get("strategic_topic_linkage_type")
                item["strategic_relation_signature"] = event.get("strategic_relation_signature")
                item["strategic_mechanism_tags"] = list(event.get("strategic_mechanism_tags") or [])
                item["strategic_dependency_tags"] = list(event.get("strategic_dependency_tags") or [])
                item["strategic_geography"] = list(event.get("strategic_geography") or [])
                item["strategic_time_window"] = event.get("strategic_time_window")
            selected_evidence.append(item)

        diagnostics = {
            "policy_version": "strategic-event-admission-v2-relation-aware",
            "admitted_event_count": len([
                item for item in events if isinstance(item, Mapping) and _clean(item.get("event_id") or item.get("id"))
            ]),
            "considered_event_count": len(selected_events),
            "excluded_event_count": len(excluded),
            "admitted_evidence_count": len([
                item for item in evidence if isinstance(item, Mapping) and _clean(item.get("evidence_id") or item.get("id"))
            ]),
            "considered_evidence_count": len(selected_evidence),
            "cluster_distribution": cluster_distribution,
            "event_class_distribution": class_distribution,
            "topic_linkage_distribution": linkage_distribution,
            "mechanism_tag_distribution": mechanism_tag_distribution,
            "dependency_tag_distribution": dependency_tag_distribution,
            "exclusion_reasons": exclusion_reasons,
            "considered_event_ids": sorted(selected_event_ids),
            "considered_evidence_ids": sorted(
                _clean(item.get("evidence_id") or item.get("id")).upper()
                for item in selected_evidence
                if _clean(item.get("evidence_id") or item.get("id"))
            ),
            "excluded_events": excluded[:60],
            "invariant": "only_material_topic_linked_relation_annotated_events_are_supplied_to_reasoning",
        }
        logger.info(
            "strategic_event_admission=%s",
            json.dumps(_json_safe(diagnostics), ensure_ascii=False, default=str),
        )
        return selected_events, selected_evidence, diagnostics

    @classmethod
    def _annotate_underlying_events(
        cls,
        *,
        events: Sequence[Mapping[str, Any]],
        topic: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Annotate duplicate EVENT-* rows with one underlying-event identifier.

        It is conservative and topic-aware: sharing the query term itself never
        counts as evidence that two records describe the same real-world action.
        """
        records = [dict(item) for item in events if isinstance(item, Mapping)]
        by_id = {
            _clean(item.get("event_id")).upper(): item
            for item in records if _clean(item.get("event_id"))
        }
        ids = list(by_id)
        parent = {eid: eid for eid in ids}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        topic_stop = {
            t.casefold() for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", _clean(topic))
        } | {
            "ai", "artificial", "intelligence", "technology", "tech", "company",
            "companies", "announced", "announcement", "launch", "new", "latest",
            "news", "development", "developments",
        }
        generic = {
            "about", "after", "again", "against", "also", "among", "because",
            "being", "between", "could", "current", "during", "from", "have",
            "into", "more", "most", "other", "over", "said", "says", "than",
            "that", "their", "there", "these", "this", "those", "through",
            "under", "using", "which", "while", "with", "would", "report",
            "reports", "source", "sources", "event", "events", "market", "industry",
        }

        def tokens(event: Mapping[str, Any]) -> set[str]:
            blob = " ".join(
                _clean(event.get(k)) for k in ("title", "summary", "event_type", "event_time_text")
                if _clean(event.get(k))
            ).casefold()
            return {
                t for t in re.findall(r"[a-z0-9][a-z0-9_-]{3,}", blob)
                if t not in generic and t not in topic_stop and not t.isdigit()
            }

        def date_key(event: Mapping[str, Any]) -> str:
            raw = _clean(
                event.get("event_date") or event.get("date") or event.get("published_at")
                or event.get("event_time") or event.get("event_time_text")
            )
            match = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", raw)
            if not match:
                return ""
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"

        features: dict[str, dict[str, Any]] = {}
        for eid, event in by_id.items():
            actor_display = cls._event_primary_institution(event)
            features[eid] = {
                "actor": cls._canonical_actor_key(actor_display),
                "actor_display": actor_display,
                "family": cls._coarse_event_family(event),
                "source": _clean(event.get("source_id")).upper(),
                "date": date_key(event),
                "tokens": tokens(event),
            }

        for i, left_id in enumerate(ids):
            left = features[left_id]
            for right_id in ids[i + 1:]:
                right = features[right_id]
                shared = left["tokens"] & right["tokens"]
                all_terms = left["tokens"] | right["tokens"]
                jaccard = len(shared) / len(all_terms) if all_terms else 0.0
                same_family = bool(left["family"] and left["family"] == right["family"] and left["family"] not in {"other", "unknown"})
                same_actor = bool(left["actor"] and left["actor"] == right["actor"])
                same_source = bool(left["source"] and left["source"] == right["source"])
                same_date = bool(left["date"] and left["date"] == right["date"])
                if (
                    (same_source and same_family and (len(shared) >= 2 or jaccard >= 0.35))
                    or (same_actor and same_family and same_date and (len(shared) >= 1 or jaccard >= 0.22))
                    or (same_actor and same_family and (len(shared) >= 3 or jaccard >= 0.45))
                ):
                    union(left_id, right_id)

        members_by_root: dict[str, list[str]] = {}
        for eid in ids:
            members_by_root.setdefault(find(eid), []).append(eid)

        group_metadata: dict[str, Any] = {}
        event_to_group: dict[str, str] = {}
        for members in members_by_root.values():
            members = sorted(set(members))
            signature = json.dumps({
                "members": members,
                "actors": sorted({features[e]["actor"] for e in members if features[e]["actor"]}),
                "families": sorted({features[e]["family"] for e in members if features[e]["family"]}),
                "dates": sorted({features[e]["date"] for e in members if features[e]["date"]}),
            }, sort_keys=True, ensure_ascii=False)
            group_id = "UNDERLYINGEVENT-" + hashlib.sha256(signature.encode("utf-8")).hexdigest()[:24].upper()
            for eid in members:
                event_to_group[eid] = group_id
            group_metadata[group_id] = {
                "underlying_event_id": group_id,
                "member_event_ids": members,
                "member_count": len(members),
                "primary_actor_names": _unique_strings(
                    features[e]["actor_display"] for e in members if features[e]["actor_display"]
                ),
                "event_families": _unique_strings(
                    features[e]["family"] for e in members if features[e]["family"]
                ),
            }

        annotated: list[dict[str, Any]] = []
        for item in records:
            eid = _clean(item.get("event_id")).upper()
            group_id = event_to_group.get(eid, eid)
            item["underlying_event_id"] = group_id
            meta = group_metadata.get(group_id, {})
            item["underlying_event_member_count"] = int(meta.get("member_count") or 1)
            annotated.append(item)

        return annotated, {
            "raw_event_count": len(records),
            "underlying_event_count": len(group_metadata),
            "duplicate_event_row_count": max(0, len(records) - len(group_metadata)),
            "groups": list(group_metadata.values()),
            "event_to_underlying_event": event_to_group,
        }

    def _extract_events(
        self,
        *,
        source_ids: Sequence[str],
        state: dict[str, Any],
    ) -> list[dict[str, Any]]:

        output: list[
            dict[str, Any]
        ] = []

        extraction_runs: list[
            dict[str, Any]
        ] = []

        extraction_failures: list[
            dict[str, Any]
        ] = []

        research_depth = (
            _enum(
                state.get(
                    "research_depth",
                    "fast",
                )
            )
            or "fast"
        )

        # Depth integrity rule:
        # A user-selected Deep run must remain Deep during event extraction.
        # Earlier MVP code silently routed Deep -> Fast here, which produced
        # shallow/noisy events while the UI still labelled the run Deep.
        extraction_depth = research_depth

        if extraction_depth not in {"fast", "deep"}:
            raise ResearchPipelineError(
                f"Invalid extraction depth resolved from run state: {extraction_depth!r}."
            )

        state.setdefault("depth_integrity", {})["extraction"] = extraction_depth
        logger.info(
            "Depth routing | stage=event_extraction requested=%s effective=%s",
            state.get("requested_research_depth"),
            extraction_depth,
        )

        for source_id in source_ids:
            normalized_events: list[
                dict[str, Any]
            ] = []

            try:
                payload = (
                    self
                    ._get_source_extraction_payload(
                        source_id
                    )
                )

                extract_callable = (
                    getattr(
                        self.extraction_agent,
                        "extract",
                        None,
                    )
                )

                if not callable(
                    extract_callable
                ):
                    raise ResearchPipelineError(
                        (
                            "ExtractionAgent does "
                            "not expose callable "
                            "extract()."
                        )
                    )

                supports_depth = False

                try:
                    signature = (
                        inspect.signature(
                            extract_callable
                        )
                    )

                    supports_depth = (
                        "research_depth"
                        in signature.parameters
                        or any(
                            parameter.kind
                            ==
                            inspect.Parameter
                            .VAR_KEYWORD
                            for parameter
                            in signature
                            .parameters
                            .values()
                        )
                    )

                except (
                    TypeError,
                    ValueError,
                ):
                    supports_depth = (
                        False
                    )

                if supports_depth:
                    extraction = (
                        extract_callable(
                            payload,
                            research_depth=(
                                extraction_depth
                            ),
                        )
                    )
                else:
                    extraction = (
                        extract_callable(
                            payload
                        )
                    )

                extraction_mapping = (
                    _as_mapping(
                        extraction
                    )
                )

                events = list(
                    extraction_mapping.get(
                        "events",
                        [],
                    )
                    or []
                )

                normalized_events = [
                    _as_mapping(
                        event
                    )
                    for event
                    in events
                ]

                self.event_service.replace_events_for_source(
                    source_id=(
                        source_id
                    ),
                    events=(
                        normalized_events
                    ),
                )

                persisted = (
                    self.event_service
                    .list_by_source_id(
                        source_id
                    )
                )

                for event in persisted:
                    if hasattr(
                        self.event_service,
                        "to_dict",
                    ):
                        record = (
                            self.event_service
                            .to_dict(
                                event
                            )
                        )
                    else:
                        record = (
                            _as_mapping(
                                event
                            )
                        )

                    output.append(
                        record
                    )

            except Exception as error:
                failure = {
                    "source_id": (
                        source_id
                    ),
                    "error": (
                        type(error).__name__
                    ),
                    "message": (
                        str(error)
                    ),
                }

                extraction_failures.append(
                    failure
                )

                state.setdefault(
                    "warnings",
                    [],
                ).append(
                    {
                        "type": (
                            "source_extraction_"
                            "failure"
                        ),
                        **failure,
                    }
                )

                logger.warning(
                    (
                        "Event extraction failed "
                        "for source. "
                        "source_id=%s "
                        "error=%s message=%s"
                    ),
                    source_id,
                    type(error).__name__,
                    str(error),
                )

            metadata = {}

            if hasattr(
                self.extraction_agent,
                "get_last_run_metadata",
            ):
                try:
                    metadata = (
                        self.extraction_agent
                        .get_last_run_metadata()
                    )
                except Exception:
                    metadata = {}

            # Validate the extractor's own diagnostic when available.
            validation_diagnostics = (
                metadata.get("validation_diagnostics", {})
                if isinstance(metadata, Mapping)
                else {}
            )
            if isinstance(validation_diagnostics, Mapping):
                opposite_depth = "fast" if extraction_depth == "deep" else "deep"
                expected_key = f"research_depth_{extraction_depth}"
                opposite_key = f"research_depth_{opposite_depth}"
                expected_seen = int(validation_diagnostics.get(expected_key, 0) or 0)
                opposite_seen = int(validation_diagnostics.get(opposite_key, 0) or 0)
                if opposite_seen > 0 or (
                    validation_diagnostics
                    and expected_seen <= 0
                    and any(str(k).startswith("research_depth_") for k in validation_diagnostics)
                ):
                    raise ResearchPipelineError(
                        "Depth integrity failure inside ExtractionAgent: "
                        f"orchestrator={extraction_depth!r} "
                        f"diagnostics={dict(validation_diagnostics)!r}."
                    )

            extraction_runs.append(
                {
                    "source_id": (
                        source_id
                    ),
                    "event_count": len(
                        normalized_events
                    ),
                    "research_depth": (
                        research_depth
                    ),
                    "effective_extraction_depth": (
                        extraction_depth
                    ),
                    "metadata": (
                        metadata
                    ),
                }
            )

        state[
            "extraction_runs"
        ] = extraction_runs

        state[
            "extraction_failures"
        ] = extraction_failures

        mismatched_runs = [
            run
            for run in extraction_runs
            if _enum(run.get("effective_extraction_depth")) != research_depth
        ]
        if mismatched_runs:
            raise ResearchPipelineError(
                "Depth integrity failure: one or more extraction runs used a "
                f"different depth than {research_depth!r}."
            )

        output.sort(
            key=lambda item: (
                item.get(
                    "source_id",
                    "",
                ),
                item.get(
                    "event_id",
                    "",
                ),
            )
        )

        return output

    def _index_events(
        self,
        events: Sequence[
            Mapping[str, Any]
        ],
        state: dict[str, Any],
    ) -> None:

        if (
            self.vector_service
            is None
        ):
            state[
                "stage_status"
            ][
                "vector_index"
            ] = {
                "status": (
                    "not_configured"
                )
            }

            return

        indexed = 0
        failures = []

        for event in events:
            try:
                self._call_method(
                    self.vector_service,
                    (
                        "upsert_event",
                        "add_event",
                    ),
                    {
                        "event_id": (
                            event.get(
                                "event_id"
                            )
                        ),
                        "id": (
                            event.get(
                                "event_id"
                            )
                        ),
                        "title": (
                            event.get(
                                "title",
                                "",
                            )
                        ),
                        "summary": (
                            event.get(
                                "summary",
                                "",
                            )
                        ),
                        "metadata": {
                            "source_id": (
                                event.get(
                                    "source_id"
                                )
                            )
                        },
                    },
                )

                indexed += 1

            except Exception as error:
                failures.append(
                    {
                        "event_id": (
                            event.get(
                                "event_id"
                            )
                        ),
                        "error": (
                            type(error).__name__
                        ),
                    }
                )

        state[
            "stage_status"
        ][
            "vector_index"
        ] = {
            "status": (
                "ok"
                if not failures
                else "partial"
            ),
            "indexed": (
                indexed
            ),
            "failures": (
                failures
            ),
        }

    def _build_relationships(
        self,
        *,
        events: Sequence[
            Mapping[str, Any]
        ],
        evidence_payload: Mapping[
            str,
            Any,
        ],
        state: dict[str, Any],
    ) -> dict[str, Any]:

        if (
            self.entity_resolution_service
            is None
        ):
            return {
                "status": (
                    "entity_resolution_"
                    "not_configured"
                ),
                "relationship_count": 0,
            }

        relationship_count = 0
        unresolved_mentions = 0
        unresolved_relationships = 0

        for event in events:
            event_id = _clean(
                event.get(
                    "event_id"
                )
            )

            source_id = _clean(
                event.get(
                    "source_id"
                )
            )

            resolved_mentions: dict[
                str,
                str,
            ] = {}

            for mention in (
                event.get(
                    "entity_mentions",
                    [],
                )
                or []
            ):
                if not isinstance(
                    mention,
                    Mapping,
                ):
                    continue

                mention_text = _clean(
                    mention.get(
                        "mention"
                    )
                )

                if not mention_text:
                    continue

                resolution = (
                    self
                    ._resolve_entity_mention(
                        mention=mention,
                        event=event,
                    )
                )

                entity_id = _clean(
                    resolution.get(
                        "entity_id"
                    )
                )

                if entity_id:
                    resolved_mentions[
                        mention_text
                        .casefold()
                    ] = entity_id
                else:
                    unresolved_mentions += 1

            resolved_relationships = []

            for relationship in (
                event.get(
                    "relationship_mentions",
                    [],
                )
                or []
            ):
                if not isinstance(
                    relationship,
                    Mapping,
                ):
                    continue

                source_mention = _clean(
                    relationship.get(
                        "source_mention"
                    )
                )

                target_mention = _clean(
                    relationship.get(
                        "target_mention"
                    )
                )

                source_entity_id = (
                    resolved_mentions.get(
                        source_mention
                        .casefold()
                    )
                )

                target_entity_id = (
                    resolved_mentions.get(
                        target_mention
                        .casefold()
                    )
                )

                if not (
                    source_entity_id
                    and target_entity_id
                ):
                    unresolved_relationships += 1
                    continue

                evidence_ids = (
                    self
                    .evidence_builder_agent
                    .relationship_evidence_ids(
                        evidence_payload,
                        event_id=(
                            event_id
                        ),
                        source_mention=(
                            source_mention
                        ),
                        relation_type=(
                            relationship.get(
                                "relation_type",
                                "",
                            )
                        ),
                        target_mention=(
                            target_mention
                        ),
                    )
                )

                if not evidence_ids:
                    unresolved_relationships += 1
                    continue

                resolved_relationships.append(
                    {
                        "source_entity_id": (
                            source_entity_id
                        ),
                        "relation_type": (
                            relationship.get(
                                "relation_type"
                            )
                        ),
                        "target_entity_id": (
                            target_entity_id
                        ),
                        "evidence_ids": (
                            evidence_ids
                        ),
                        "source_mention": (
                            source_mention
                        ),
                        "target_mention": (
                            target_mention
                        ),
                        "relation_mention": (
                            relationship.get(
                                "relation_mention",
                                "",
                            )
                        ),
                        "assertion_text": (
                            relationship.get(
                                "assertion_text",
                                "",
                            )
                        ),
                        "claim_status": (
                            event.get(
                                "claim_status",
                                "unknown",
                            )
                        ),
                        "temporal_status": (
                            event.get(
                                "temporal_status",
                                "unknown",
                            )
                        ),
                        "relation_time_text": (
                            relationship.get(
                                "relation_time_text",
                                "",
                            )
                        ),
                        "valid_start_at": (
                            relationship.get(
                                "valid_start_at"
                            )
                        ),
                        "valid_end_at": (
                            relationship.get(
                                "valid_end_at"
                            )
                        ),
                        "temporal_precision": (
                            relationship.get(
                                "temporal_precision",
                                "unresolved",
                            )
                        ),
                    }
                )

            if resolved_relationships:
                result = (
                    self.relationship_service
                    .replace_event_relationships(
                        event_id=(
                            event_id
                        ),
                        source_id=(
                            source_id
                        ),
                        relationships=(
                            resolved_relationships
                        ),
                    )
                )

                result_map = (
                    _as_mapping(
                        result
                    )
                )

                relationship_count += int(
                    result_map.get(
                        "input_relationship_count",
                        len(
                            resolved_relationships
                        ),
                    )
                )

        return {
            "status": "ok",
            "relationship_count": (
                relationship_count
            ),
            "unresolved_entity_mentions": (
                unresolved_mentions
            ),
            "unresolved_relationship_mentions": (
                unresolved_relationships
            ),
        }

    def _resolve_entity_mention(
        self,
        *,
        mention: Mapping[
            str,
            Any,
        ],
        event: Mapping[
            str,
            Any,
        ],
    ) -> dict[str, Any]:

        registry = (
            self.entity_service
            .registry_records()
        )

        result = (
            self._call_method(
                self.entity_resolution_service,
                (
                    "resolve_mention",
                    "resolve_entity",
                    "resolve",
                ),
                {
                    "mention": mention,
                    "mention_text": (
                        mention.get(
                            "mention"
                        )
                    ),
                    "entity_type": (
                        mention.get(
                            "entity_type"
                        )
                    ),
                    "context": {
                        "event_id": (
                            event.get(
                                "event_id"
                            )
                        ),
                        "source_id": (
                            event.get(
                                "source_id"
                            )
                        ),
                        "source_span": (
                            mention.get(
                                "source_span"
                            )
                        ),
                        "context": (
                            mention.get(
                                "context"
                            )
                        ),
                    },
                    "registry": (
                        registry
                    ),
                    "candidates": (
                        self.entity_service
                        .candidates_for_mention(
                            mention
                        )
                    ),
                },
            )
        )

        result_map = (
            _as_mapping(
                result
            )
        )

        entity_id = _clean(
            result_map.get(
                "entity_id"
            )
            or result_map.get(
                "canonical_entity_id"
            )
        )

        if entity_id:
            return {
                **result_map,
                "entity_id": (
                    entity_id
                ),
            }

        create_new = bool(
            result_map.get(
                "create_new",
                False,
            )
        )

        identity_key = _clean(
            result_map.get(
                "identity_key"
            )
        )

        if (
            create_new
            and identity_key
        ):
            write_result = (
                self.entity_service
                .upsert_entity(
                    name=(
                        result_map.get(
                            "canonical_name"
                        )
                        or mention.get(
                            "mention"
                        )
                    ),
                    entity_type=(
                        result_map.get(
                            "entity_type"
                        )
                        or mention.get(
                            "entity_type"
                        )
                        or "unknown"
                    ),
                    identity_key=(
                        identity_key
                    ),
                    identity_namespace=(
                        result_map.get(
                            "identity_namespace",
                            "entity_resolution",
                        )
                    ),
                    aliases=(
                        result_map.get(
                            "aliases",
                            [],
                        )
                    ),
                    attributes=(
                        result_map.get(
                            "attributes",
                            {},
                        )
                    ),
                )
            )

            write_map = (
                _as_mapping(
                    write_result
                )
            )

            return {
                **result_map,
                "entity_id": (
                    write_map.get(
                        "entity_id",
                        "",
                    )
                ),
            }

        return result_map

    def _rebuild_graph(
        self,
        *,
        state: dict[str, Any],
    ) -> dict[str, Any]:

        if hasattr(
            self.graph_service,
            "rebuild_from_services",
        ):
            result = (
                self.graph_service
                .rebuild_from_services(
                    entity_service=(
                        self.entity_service
                    ),
                    relationship_service=(
                        self.relationship_service
                    ),
                    save=True,
                )
            )

            state[
                "graph_rebuild"
            ] = _json_safe(
                result
            )

        else:
            if hasattr(
                self.graph_service,
                "build_graph",
            ):
                self.graph_service.build_graph()

            if hasattr(
                self.graph_service,
                "save_graph",
            ):
                self.graph_service.save_graph()

        if hasattr(
            self.graph_service,
            "build_reasoning_context",
        ):
            return (
                self.graph_service
                .build_reasoning_context()
            )

        if hasattr(
            self.graph_service,
            "export_graph_text",
        ):
            return {
                "text": (
                    self.graph_service
                    .export_graph_text()
                ),
                "epistemic_authority": (
                    False
                ),
            }

        return {}

    def _fast_evidence_summary_candidates(
        self,
        *,
        topic: str,
        evidence: Sequence[Mapping[str, Any]],
        events: Sequence[Mapping[str, Any]] = (),
        maximum: int = 2,
        reason: bool = False,
    ) -> list[dict[str, Any]]:
        """Select Fast evidence deterministically; reason over it only once.

        v3.1 keeps the old relation-template library removed. The deterministic
        layer decides only WHAT evidence is eligible and worth showing to the
        model. It does not decide WHAT the conclusion is.
        """
        import re as _re

        maximum = max(1, int(maximum))
        topic_text = _clean(topic)
        self._last_fast_reasoning_metadata = {
            "llm_called": False,
            "fallback_used": False,
            "degraded": False,
            "reasoning_status": "not_started",
        }

        event_by_id = {
            _clean(event.get("event_id")): event
            for event in events
            if isinstance(event, Mapping) and _clean(event.get("event_id"))
        }

        topic_tokens = {
            token
            for token in _re.findall(r"[a-z0-9]+", topic_text.casefold())
            if len(token) >= 3
            and token not in {
                "the", "and", "for", "with", "from", "into", "what",
                "how", "why", "are", "was", "were", "this", "that",
            }
        }
        topic_aliases = set(topic_tokens)
        if "artificial" in topic_tokens or "intelligence" in topic_tokens:
            topic_aliases.update({"ai", "genai", "agentic", "llm", "agents"})

        noise_patterns = (
            r"\bLearn more\b", r"\bLoading(?:…|\.\.\.)?\b", r"\bShare\b",
            r"\bSkip to main content\b", r"\bMy Account\b", r"\bSubscribe\b",
            r"\bSign up\b", r"\bLogin\b", r"\bBack to top\b",
            r"\bRelated articles?\b",
        )

        def _remove_page_noise(value: Any) -> str:
            cleaned = _clean(value)
            for pattern in noise_patterns:
                cleaned = _re.sub(pattern, " ", cleaned, flags=_re.IGNORECASE)
            return _re.sub(r"\s+", " ", cleaned).strip(" …-|:;,.")

        def _best_event_sentence(value: Any) -> str:
            cleaned = _remove_page_noise(value)
            if not cleaned:
                return ""
            clauses = _re.split(r"(?<=[.!?])\s+|\s+[|•]\s+", cleaned)
            viable = [_clean(c) for c in clauses if len(_clean(c)) >= 25]
            if not viable:
                viable = [cleaned]
            value = max(viable, key=lambda c: min(len(c), 300))
            if len(value) > 320:
                clipped = value[:319]
                if " " in clipped:
                    clipped = clipped.rsplit(" ", 1)[0]
                value = clipped.rstrip(" …,;:-") + "…"
            return value

        def _record_is_prospective(
            item: Mapping[str, Any],
            event: Mapping[str, Any],
            text_value: str,
        ) -> bool:
            temporal_values = [
                _enum(item.get("temporal_status")),
                _enum(event.get("temporal_status")),
            ]
            payload = item.get("structured_payload")
            if isinstance(payload, Mapping):
                temporal_values.append(_enum(payload.get("temporal_status")))
            if any(v in {
                "future", "future_commitment", "prospective", "forecast",
                "prediction", "scenario",
            } for v in temporal_values if v):
                return True
            current_year = datetime.now(timezone.utc).year
            years = [int(x) for x in _re.findall(r"\b(20\d{2})\b", _clean(text_value))]
            return bool(years and min(years) > current_year)

        def _significant_entity_name(value: Any) -> str:
            name = _clean(value)
            low = name.casefold().strip(" .,:;()[]{}")
            if not name:
                return ""
            excluded = {
                "ai", "artificial intelligence", "responsible ai", "genai",
                "nyse", "nasdaq", "lse", "euronext", "otc", "tsx", "asx",
                "inc", "inc.", "ltd", "ltd.", "llc", "plc", "corp", "corporation",
                "company", "group", "holdings", "press release", "pr newswire",
                "business wire", "reuters", "globe newswire",
            }
            if low in excluded:
                return ""
            if _re.fullmatch(r"(?:nyse|nasdaq|lse|tsx|asx)\s*[:\-]?\s*[a-z.]{1,6}", low):
                return ""
            if _re.fullmatch(r"[a-z]{1,3}", low) and low not in {"ibm", "aws", "amd", "sap"}:
                return ""
            return name

        def _dominant_theme(record: Mapping[str, Any]) -> str:
            text_value = _clean(
                f"{record.get('event_title', '')} {record.get('text', '')}"
            ).casefold()
            themes = {
                "enterprise deployment": (
                    "deploy", "deployment", "implementation", "integrat", "rollout",
                    "production", "workflow", "enterprise", "business outcomes",
                ),
                "capital and consolidation": (
                    "acquir", "funding", "raised", "valuation", "invest", "capital",
                    "merger", "m&a", "financing",
                ),
                "governance and safety": (
                    "governance", "audit", "assurance", "risk", "due diligence",
                    "monitor", "oversight", "safety", "transparency",
                ),
                "regulation and policy": (
                    "regulation", "regulatory", "rule", "law", "policy", "mandate",
                    "commission", "government", "compliance",
                ),
                "commercial adoption": (
                    "customer", "adoption", "contract", "procurement", "budget",
                    "revenue", "spend", "license", "buyer",
                ),
                "model and infrastructure": (
                    "model", "training", "compute", "gpu", "datacenter", "data center",
                    "chip", "inference", "multilingual",
                ),
            }
            scored = []
            for theme, stems in themes.items():
                score = sum(1 for stem in stems if stem in text_value)
                scored.append((score, theme))
            scored.sort(reverse=True)
            return scored[0][1] if scored and scored[0][0] > 0 else "other"

        def _materiality_score(record: Mapping[str, Any]) -> float:
            value = _clean(f"{record.get('event_title', '')} {record.get('text', '')}").casefold()
            words = set(_re.findall(r"[a-z0-9]+", value))
            score = float(len(words & topic_aliases) * 1.25)
            action_terms = (
                "announce", "launch", "acquir", "partner", "invest", "fund", "raise",
                "deploy", "release", "regulat", "require", "adopt", "contract",
                "revenue", "valuation", "percent", "%",
            )
            score += sum(1.2 for term in action_terms if term in value)
            if _re.search(r"\b20\d{2}\b", value):
                score += 0.7
            return score

        usable: list[dict[str, Any]] = []
        for item in evidence:
            if not isinstance(item, Mapping):
                continue
            evidence_id = _clean(item.get("evidence_id"))
            if not evidence_id.startswith("EVID-"):
                continue
            event_id = _clean(item.get("event_id"))
            event = event_by_id.get(event_id, {}) if event_id else {}
            text_value = _clean(
                item.get("claim")
                or item.get("text")
                or item.get("excerpt")
                or item.get("content")
                or item.get("value")
            )
            event_title = _best_event_sentence(
                event.get("title") or item.get("event_title") or text_value
            )
            if not text_value or not event_title:
                continue
            if _record_is_prospective(item, event, f"{event_title} {text_value}"):
                continue

            extraction_metadata = event.get("extraction_metadata", {})
            taxonomy = _enum(extraction_metadata.get("event_taxonomy")) if isinstance(extraction_metadata, Mapping) else ""
            if taxonomy in {
                "background", "marketing", "document_structure", "roundup_or_listing",
                "scenario", "opinion", "future_commitment",
            }:
                continue

            raw_entities = []
            for entity in (event.get("entity_mentions", []) or []):
                if isinstance(entity, Mapping):
                    name = _significant_entity_name(entity.get("mention") or entity.get("name"))
                    if name:
                        raw_entities.append(name)
            entity_names = _unique_strings(raw_entities)

            normalized = {
                "evidence_id": evidence_id,
                "source_id": _clean(item.get("source_id") or event.get("source_id")),
                "event_id": event_id,
                "text": text_value,
                "event_title": event_title,
                "claim_status": _enum(item.get("claim_status")) or "unknown",
                "temporal_status": _enum(item.get("temporal_status") or event.get("temporal_status")) or "unknown",
                "entity_names": entity_names,
                "quantitative_claims": [
                    dict(q) for q in (event.get("quantitative_claims", []) or [])
                    if isinstance(q, Mapping)
                ],
                "relationship_mentions": [
                    dict(r) for r in (event.get("relationship_mentions", []) or [])
                    if isinstance(r, Mapping)
                ],
                "event_taxonomy": taxonomy or "observed_event",
            }
            normalized["theme"] = _dominant_theme(normalized)
            normalized["label"] = event_title
            normalized["_score"] = _materiality_score(normalized)
            normalized["_score"] += min(len(entity_names), 3) * 0.7
            normalized["_score"] += min(len(normalized["quantitative_claims"]), 2) * 1.0
            normalized["_score"] += min(len(normalized["relationship_mentions"]), 2) * 1.4
            usable.append(normalized)

        if not usable:
            self._last_fast_reasoning_metadata.update({
                "reasoning_status": "abstained_no_usable_evidence",
                "degraded": True,
            })
            return []

        # v3.1: choose evidence for CONNECTABILITY, not only individual
        # materiality. Fast previously took the highest-scoring record from each
        # source and then truncated that list. That can hand the model four
        # individually important but mutually unrelated events, which makes a
        # correct model abstain even when a connectable pair exists elsewhere in
        # the admitted evidence set.
        #
        # This deterministic layer does NOT infer the conclusion. It only chooses
        # a small cross-source cluster with concrete connection signals for the
        # model to inspect.
        generic_connection_tokens = {
            "artificial", "intelligence", "generative", "genai", "agentic",
            "model", "models", "system", "systems", "technology", "technologies",
            "company", "companies", "business", "businesses", "enterprise",
            "market", "markets", "industry", "industries", "platform", "platforms",
            "service", "services", "solution", "solutions", "software", "data",
            "digital", "global", "current", "new", "using", "used", "use",
            "will", "would", "could", "also", "more", "into", "from", "with",
            "their", "this", "that", "these", "those", "about", "across",
        }

        def _distinctive_tokens(record: Mapping[str, Any]) -> set[str]:
            value = _clean(
                f"{record.get('event_title', '')} {record.get('text', '')}"
            ).casefold()
            tokens = {
                token
                for token in _re.findall(r"[a-z0-9][a-z0-9._+-]{3,}", value)
                if token not in generic_connection_tokens
                and token not in topic_aliases
                and not token.isdigit()
                and not _re.fullmatch(r"20\d{2}", token)
            }
            return tokens

        def _relationship_entities(record: Mapping[str, Any]) -> set[str]:
            values: set[str] = set()
            for rel in record.get("relationship_mentions", []) or []:
                if not isinstance(rel, Mapping):
                    continue
                for key in (
                    "source", "subject", "source_mention",
                    "target", "object", "target_mention",
                ):
                    name = _significant_entity_name(rel.get(key))
                    if name:
                        values.add(name.casefold())
            return values

        compatible_theme_pairs = {
            frozenset({"enterprise deployment", "commercial adoption"}),
            frozenset({"enterprise deployment", "capital and consolidation"}),
            frozenset({"enterprise deployment", "governance and safety"}),
            frozenset({"regulation and policy", "governance and safety"}),
            frozenset({"regulation and policy", "commercial adoption"}),
            frozenset({"model and infrastructure", "commercial adoption"}),
            frozenset({"model and infrastructure", "capital and consolidation"}),
        }

        def _pair_connectability(
            left: Mapping[str, Any],
            right: Mapping[str, Any],
        ) -> tuple[float, list[str]]:
            if _clean(left.get("source_id")) == _clean(right.get("source_id")):
                return (-1.0, [])

            score = 0.0
            signals: list[str] = []

            left_entities = {
                _clean(name).casefold()
                for name in left.get("entity_names", []) or []
                if _significant_entity_name(name)
            }
            right_entities = {
                _clean(name).casefold()
                for name in right.get("entity_names", []) or []
                if _significant_entity_name(name)
            }
            shared_entities = sorted(left_entities & right_entities)
            if shared_entities:
                score += 6.0 + min(2.0, float(len(shared_entities) - 1))
                signals.append(
                    "shared_entity:" + ",".join(shared_entities[:3])
                )

            left_rel_entities = _relationship_entities(left)
            right_rel_entities = _relationship_entities(right)
            relation_bridge = sorted(
                (left_rel_entities & (right_entities | right_rel_entities))
                | (right_rel_entities & (left_entities | left_rel_entities))
            )
            if relation_bridge:
                score += 5.0
                signals.append(
                    "relationship_bridge:" + ",".join(relation_bridge[:3])
                )

            left_tokens = _distinctive_tokens(left)
            right_tokens = _distinctive_tokens(right)
            shared_tokens = sorted(left_tokens & right_tokens)
            if shared_tokens:
                token_score = min(3.0, 0.75 * len(shared_tokens))
                score += token_score
                signals.append(
                    "shared_terms:" + ",".join(shared_tokens[:5])
                )

            left_theme = _clean(left.get("theme"))
            right_theme = _clean(right.get("theme"))
            if left_theme and left_theme != "other" and left_theme == right_theme:
                score += 1.6
                signals.append("same_theme:" + left_theme)
            elif frozenset({left_theme, right_theme}) in compatible_theme_pairs:
                score += 0.9
                signals.append(
                    "compatible_themes:" + left_theme + "+" + right_theme
                )

            left_quant = bool(left.get("quantitative_claims"))
            right_quant = bool(right.get("quantitative_claims"))
            if left_quant and right_quant:
                score += 1.0
                signals.append("cross_source_quantitative")

            left_relationship = bool(left.get("relationship_mentions"))
            right_relationship = bool(right.get("relationship_mentions"))
            if left_relationship or right_relationship:
                score += 0.8
                signals.append("structured_relationship_present")

            score += min(
                1.0,
                0.05 * (
                    float(left.get("_score", 0.0))
                    + float(right.get("_score", 0.0))
                ),
            )
            return (score, signals)

        ranked_usable = sorted(
            usable,
            key=lambda r: float(r.get("_score", 0.0)),
            reverse=True,
        )
        best_pair: tuple[dict[str, Any], dict[str, Any]] | None = None
        best_pair_score = -1.0
        best_pair_signals: list[str] = []

        # Pair search is deterministic and bounded by the already-small Fast
        # evidence set. Only cross-source pairs are considered.
        for left_index, left in enumerate(ranked_usable):
            for right in ranked_usable[left_index + 1:]:
                pair_score, pair_signals = _pair_connectability(left, right)
                if pair_score > best_pair_score:
                    best_pair = (left, right)
                    best_pair_score = pair_score
                    best_pair_signals = pair_signals

        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()

        # A concrete lexical/entity/relationship signal must exist before the pair
        # gets priority. Theme similarity by itself is intentionally insufficient.
        pair_has_concrete_signal = any(
            signal.startswith((
                "shared_entity:",
                "relationship_bridge:",
                "shared_terms:",
            ))
            for signal in best_pair_signals
        )

        if best_pair is not None and pair_has_concrete_signal:
            for record in best_pair:
                selected.append(record)
                selected_ids.add(record["evidence_id"])

            # Add records that connect to the seed pair. Prefer a new source, then
            # higher pair-connectability, then materiality.
            while len(selected) < 8:
                candidates: list[tuple[float, int, float, dict[str, Any]]] = []
                selected_sources = {
                    _clean(record.get("source_id")) for record in selected
                }
                for candidate in ranked_usable:
                    if candidate["evidence_id"] in selected_ids:
                        continue
                    connectivity = max(
                        _pair_connectability(candidate, existing)[0]
                        for existing in selected
                    )
                    new_source = int(
                        _clean(candidate.get("source_id")) not in selected_sources
                    )
                    candidates.append((
                        connectivity,
                        new_source,
                        float(candidate.get("_score", 0.0)),
                        candidate,
                    ))
                if not candidates:
                    break
                candidates.sort(
                    key=lambda item: (item[0], item[1], item[2]),
                    reverse=True,
                )
                chosen = candidates[0][3]
                selected.append(chosen)
                selected_ids.add(chosen["evidence_id"])
        else:
            # Safe fallback: source-diverse materiality ranking. The model may
            # abstain; no deterministic conclusion is manufactured.
            by_source: dict[str, list[dict[str, Any]]] = {}
            for item in ranked_usable:
                by_source.setdefault(
                    item["source_id"] or "__unknown__", []
                ).append(item)
            for values in by_source.values():
                values.sort(
                    key=lambda r: float(r.get("_score", 0.0)),
                    reverse=True,
                )
            for head in sorted(
                (values[0] for values in by_source.values() if values),
                key=lambda r: float(r.get("_score", 0.0)),
                reverse=True,
            ):
                if len(selected) >= 6:
                    break
                selected.append(head)
                selected_ids.add(head["evidence_id"])
            for item in ranked_usable:
                if len(selected) >= 8:
                    break
                if item["evidence_id"] in selected_ids:
                    continue
                selected.append(item)
                selected_ids.add(item["evidence_id"])

        self._last_fast_reasoning_metadata.update({
            "connection_cluster_pair_score": round(max(best_pair_score, 0.0), 3),
            "connection_cluster_signals": list(best_pair_signals),
            "connection_cluster_seeded": bool(
                best_pair is not None and pair_has_concrete_signal
            ),
        })

        significant_entities = _unique_strings(
            name for record in selected for name in record.get("entity_names", [])
        )
        themes = _unique_strings(record.get("theme") for record in selected if record.get("theme"))
        relationship_tokens: list[str] = []
        for record in selected:
            for rel in record.get("relationship_mentions", []) or []:
                subject = _clean(rel.get("source") or rel.get("subject") or rel.get("source_mention"))
                predicate = _clean(rel.get("relation") or rel.get("relationship_type"))
                obj = _clean(rel.get("target") or rel.get("object") or rel.get("target_mention"))
                if predicate:
                    relationship_tokens.append(_clean(f"{subject} {predicate} {obj}"))

        verification_query = _clean(
            " ".join(
                [topic_text, str(datetime.now(timezone.utc).year)]
                + significant_entities[:4]
                + themes[:3]
                + relationship_tokens[:2]
                + ["current evidence contract adoption revenue investment regulation deployment"]
            )
        )

        if not reason:
            return [{
                "verification_query": verification_query,
                "evidence_ids": [r["evidence_id"] for r in selected],
                "support_evidence_ids": [r["evidence_id"] for r in selected],
                "fast_synthesis_mode": "evidence_expansion_seed_only",
                "publish_hidden_insight": False,
            }]

        def _quantity_display(q: Mapping[str, Any]) -> str:
            for key in ("text", "claim", "raw_text", "value_text", "normalized_text"):
                val = _clean(q.get(key))
                if val:
                    return val
            value = _clean(q.get("value"))
            unit = _clean(q.get("unit") or q.get("canonical_unit"))
            return _clean(f"{value} {unit}")

        def _relationship_display(r: Mapping[str, Any]) -> str:
            subject = _clean(r.get("source") or r.get("subject") or r.get("source_mention"))
            predicate = _clean(r.get("relation") or r.get("relationship_type"))
            obj = _clean(r.get("target") or r.get("object") or r.get("target_mention"))
            return _clean(f"{subject} {predicate} {obj}")

        # Final Fast reasoning is intentionally smaller than the evidence-expansion
        # seed. v3.1 orders `selected` around the strongest connectable cross-source
        # cluster first, so this truncation keeps the records most likely to support
        # an actual relationship rather than merely the individually strongest events.
        primary_limit = max(
            3,
            min(
                5,
                int(os.getenv("FAST_SYNTHESIS_PRIMARY_EVIDENCE", "4")),
            ),
        )
        selected = selected[: min(primary_limit, len(selected))]

        def _build_fast_packet(
            records: Sequence[Mapping[str, Any]],
            *,
            compact: bool = False,
        ) -> list[dict[str, Any]]:
            packet: list[dict[str, Any]] = []
            event_chars = 240 if compact else 320
            evidence_chars = 320 if compact else 480
            entity_limit = 4 if compact else 6
            quantity_limit = 3 if compact else 4
            relationship_limit = 3 if compact else 4
            for record in records:
                packet.append({
                    "evidence_id": record["evidence_id"],
                    "source_id": record["source_id"],
                    "event_id": record["event_id"],
                    "event": record["label"][:event_chars],
                    "evidence_text": record["text"][:evidence_chars],
                    "theme_hint": record["theme"],
                    "entities": record["entity_names"][:entity_limit],
                    "quantities": [
                        _quantity_display(q)
                        for q in record["quantitative_claims"][:quantity_limit]
                        if _quantity_display(q)
                    ],
                    "relationships": [
                        _relationship_display(r)
                        for r in record["relationship_mentions"][:relationship_limit]
                        if _relationship_display(r)
                    ],
                    "temporal_status": record["temporal_status"],
                })
            return packet

        evidence_packet = _build_fast_packet(selected)

        def _positive_env_float(
            name: str,
            default: float,
            low: float,
            high: float,
        ) -> float:
            try:
                value = float(os.getenv(name, str(default)))
            except (TypeError, ValueError):
                value = default
            return max(low, min(high, value))

        def _get_fast_synthesis_llm() -> Any:
            """Return a dedicated Fast-only local inference client.

            LLMService.ask() does not accept timeout_seconds/timeout per call.
            Timeout is constructor-only, so Fast synthesis must not borrow the
            StrategicInferenceAgent client and pretend a per-call timeout applies.
            """
            model_name = _clean(
                os.getenv("OLLAMA_FAST_SYNTHESIS_MODEL")
                or os.getenv("OLLAMA_EXTRACTION_MODEL")
                or "qwen2.5:1.5b"
            )
            timeout_seconds = _positive_env_float(
                "OLLAMA_FAST_SYNTHESIS_TIMEOUT_SECONDS",
                45.0,
                10.0,
                45.0,
            )

            cached = getattr(self, "_fast_synthesis_llm_v30", None)
            if (
                callable(getattr(cached, "ask", None))
                and _clean(getattr(cached, "model_name", "")) == model_name
                and abs(float(getattr(cached, "timeout_seconds", 0.0)) - timeout_seconds) < 0.001
            ):
                return cached

            from services.llm_service import LLMService

            llm = LLMService(
                model_name=model_name,
                timeout_seconds=timeout_seconds,
                max_retries=0,
            )
            self._fast_synthesis_llm_v30 = llm
            return llm

        primary_llm = _get_fast_synthesis_llm()
        primary_model = _clean(getattr(primary_llm, "model_name", "")) or "qwen2.5:1.5b"
        primary_timeout = float(getattr(primary_llm, "timeout_seconds", 45.0))
        ask = getattr(primary_llm, "ask", None)
        if not callable(ask):
            self._last_fast_reasoning_metadata.update({
                "reasoning_status": "abstained_no_llm",
                "degraded": True,
            })
            return self._fast_abstention_candidate(
                topic=topic_text,
                selected=selected,
                reason="The bounded Fast reasoning model was unavailable.",
                verification_query=verification_query,
            )

        system_prompt = '''You are the Fast strategic-reasoning step in an evidence-grounded event-intelligence pipeline.
Use ONLY the supplied validated evidence. Do not use outside knowledge.

RULES
1. Connect events only when specific supplied actors, actions, relationships or numbers make the connection real. Broad AI similarity is not enough.
2. Ignore exchange/ticker/publisher/dateline boilerplate as linking entities.
3. Do not claim a shift, acceleration, convergence, decline, replacement, or value moving upstream/downstream unless supplied comparative or temporal evidence establishes that direction.
4. Funding for model training is not evidence for deployment economics merely because both involve AI capital.
5. Distinct source IDs are not proof of source independence.
6. A second-order effect must follow from the stated mechanism and be phrased as a prediction.
7. If the claim could have been written without seeing these named events/numbers, abstain instead of writing analyst filler.
8. evidence_specific_anchors MUST contain 2-4 short exact phrases copied from the supplied evidence, and the anchors must collectively come from at least two different evidence records/sources. Prefer actor names, transaction/deployment names, concrete actions, named programs, or supplied numbers. Do not use generic anchors such as "AI", "artificial intelligence", "enterprise", "market", or theme labels.

Return ONLY valid JSON in this shape:
{
  "analysis_status": "ok|abstained",
  "candidate": {
    "connection": "",
    "mechanism": "",
    "hidden_insight": "",
    "second_order_effect": "",
    "alternative_explanation": "",
    "critical_unknown": "",
    "watch_signal": "",
    "supporting_evidence_ids": [],
    "evidence_specific_anchors": [],
    "confidence": "low|medium|high",
    "publish_hidden_insight": false,
    "verification_query": ""
  },
  "abstention_reason": ""
}
Return at most ONE candidate. A useful connection may be returned with an empty hidden_insight and publish_hidden_insight=false.'''

        def _extract_json_object(raw: str) -> dict[str, Any]:
            decoder = json.JSONDecoder()
            cleaned = str(raw or "").strip()
            for index, character in enumerate(cleaned):
                if character != "{":
                    continue
                try:
                    parsed_value, _ = decoder.raw_decode(cleaned[index:])
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed_value, Mapping):
                    return dict(parsed_value)
            raise ValueError("Fast synthesis returned no valid JSON object.")

        def _call_fast_llm(
            llm_client: Any,
            *,
            packet: Sequence[Mapping[str, Any]],
            records: Sequence[Mapping[str, Any]],
            compact: bool,
            model_name: str,
        ) -> tuple[dict[str, Any], str]:
            ask_fn = getattr(llm_client, "ask", None)
            if not callable(ask_fn):
                raise ValueError(
                    "Fast synthesis LLM does not expose callable ask()."
                )

            user_prompt = json.dumps({
                "research_question": topic_text,
                "evidence": list(packet),
                "allowed_evidence_ids": [
                    r["evidence_id"] for r in records
                ],
            }, ensure_ascii=False)

            call_kwargs: dict[str, Any] = {}
            try:
                ask_signature = inspect.signature(ask_fn)
                params = ask_signature.parameters
                var_kw = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in params.values()
                )

                def _supports(name: str) -> bool:
                    return var_kw or name in params
            except (TypeError, ValueError):
                def _supports(name: str) -> bool:
                    return False

            if _supports("system_prompt"):
                call_kwargs["system_prompt"] = system_prompt
            if _supports("model_name"):
                call_kwargs["model_name"] = model_name
            if _supports("temperature"):
                call_kwargs["temperature"] = 0.15
            if _supports("seed"):
                call_kwargs["seed"] = 17
            if _supports("num_ctx"):
                call_kwargs["num_ctx"] = 2048
            if _supports("num_predict"):
                call_kwargs["num_predict"] = 360
            if _supports("response_format"):
                call_kwargs["response_format"] = "json"
            if _supports("metadata"):
                call_kwargs["metadata"] = {
                    "task": "fast_evidence_conditioned_synthesis",
                    "prompt_version": "fast-synthesis-v3.1",
                    "compact_retry": compact,
                    "evidence_count": len(records),
                }

            if "system_prompt" in call_kwargs:
                raw = ask_fn(user_prompt, **call_kwargs)
            else:
                raw = ask_fn(
                    f"SYSTEM\n{system_prompt}\nUSER\n{user_prompt}",
                    **call_kwargs,
                )
            return _extract_json_object(str(raw or "")), str(raw or "")

        started = time.perf_counter()
        parsed: dict[str, Any]
        raw_response = ""
        retry_used = False
        try:
            parsed, raw_response = _call_fast_llm(
                primary_llm,
                packet=evidence_packet,
                records=selected,
                compact=True,
                model_name=primary_model,
            )
            logger.info(
                "Fast dedicated synthesis completed. model=%s timeout_seconds=%.1f "
                "evidence_count=%s latency_ms=%.2f analysis_status=%s",
                primary_model,
                primary_timeout,
                len(selected),
                (time.perf_counter() - started) * 1000,
                _enum(parsed.get("analysis_status")) or "unknown",
            )
        except Exception as exc:
            logger.warning(
                "Fast dedicated synthesis failed; abstaining. "
                "model=%s timeout_seconds=%.1f evidence_count=%s "
                "error=%s message=%s",
                primary_model,
                primary_timeout,
                len(selected),
                type(exc).__name__,
                str(exc),
            )
            self._last_fast_reasoning_metadata.update({
                "llm_called": True,
                "reasoning_status": "abstained_model_failure",
                "latency_ms": round(
                    (time.perf_counter() - started) * 1000,
                    2,
                ),
                "fallback_used": False,
                "degraded": True,
                "error": type(exc).__name__,
                "primary_model": primary_model,
                "primary_timeout_seconds": primary_timeout,
                "primary_evidence_count": len(selected),
                "retry_used": False,
            })
            return self._fast_abstention_candidate(
                topic=topic_text,
                selected=selected,
                reason=(
                    "The dedicated Fast reasoning call failed, so no analytical "
                    "claim was substituted from a template."
                ),
                verification_query=verification_query,
            )

        # Normalize the one-candidate Fast contract back to the list shape
        # consumed by the existing grounding/post-validation code below.
        if isinstance(parsed.get("candidate"), Mapping):
            parsed["candidates"] = [dict(parsed["candidate"])]
        elif not isinstance(parsed.get("candidates"), list):
            parsed["candidates"] = []

        self._last_fast_reasoning_metadata.update({
            "llm_called": True,
            "reasoning_status": (
                _enum(parsed.get("analysis_status")) or "unknown"
            ),
            "latency_ms": round(
                (time.perf_counter() - started) * 1000,
                2,
            ),
            "fallback_used": False,
            "degraded": False,
            "raw_response_characters": len(raw_response),
            "retry_used": retry_used,
            "primary_model": primary_model,
            "selected_evidence_count": len(selected),
            "prompt_version": "fast-synthesis-v3.1",
        })

        if _enum(parsed.get("analysis_status")) == "abstained":
            return self._fast_abstention_candidate(
                topic=topic_text,
                selected=selected,
                reason=_clean(parsed.get("abstention_reason")) or "The supplied evidence did not support a sufficiently specific cross-event inference.",
                verification_query=verification_query,
            )

        allowed_ids = {r["evidence_id"] for r in selected}
        record_by_id = {r["evidence_id"]: r for r in selected}
        raw_candidates = parsed.get("candidates", [])
        if not isinstance(raw_candidates, list):
            raw_candidates = []

        directional_pattern = _re.compile(
            r"\b(?:shift(?:ing|ed)?|moving?\s+from|move\s+from|replac(?:e|ing|ed)|"
            r"declin(?:e|ing|ed)|accelerat(?:e|ing|ed)|converg(?:e|ing|ed)|"
            r"value\s+(?:pool\s+)?(?:is\s+)?moving|downstream|upstream)\b",
            _re.I,
        )
        comparative_pattern = _re.compile(
            r"\b(?:rose|risen|grew|growth|increased|jumped|fell|declined|decreased|"
            r"from\s+\d|to\s+\d|versus|vs\.?|compared with|year[- ]over[- ]year|"
            r"quarter[- ]over[- ]quarter|\d+(?:\.\d+)?\s*%.*\d+(?:\.\d+)?\s*%)\b",
            _re.I,
        )

        def _support_has_directional_evidence(records: list[dict[str, Any]]) -> bool:
            combined = " ".join(
                _clean(f"{r.get('event_title', '')} {r.get('text', '')}")
                for r in records
            )
            if comparative_pattern.search(combined):
                return True
            quantity_total = sum(len(r.get("quantitative_claims", []) or []) for r in records)
            return quantity_total >= 2 and len({r.get("source_id") for r in records if r.get("source_id")}) >= 2

        output: list[dict[str, Any]] = []
        for item in raw_candidates[:maximum]:
            if not isinstance(item, Mapping):
                continue
            raw_support_ids = item.get("supporting_evidence_ids", []) or []
            if isinstance(raw_support_ids, str):
                raw_support_ids = [raw_support_ids]
            support_ids = _unique_strings(raw_support_ids)
            support_ids = [e for e in support_ids if e in allowed_ids]
            if len(support_ids) < 2:
                continue
            support_records = [record_by_id[e] for e in support_ids]
            distinct_sources = len({r.get("source_id") for r in support_records if r.get("source_id")})
            if distinct_sources < 2:
                continue

            connection = _clean(item.get("connection"))
            mechanism = _clean(item.get("mechanism"))
            hidden = _clean(item.get("hidden_insight"))
            second = _clean(item.get("second_order_effect"))
            alternative = _clean(item.get("alternative_explanation"))
            unknown = _clean(item.get("critical_unknown"))
            watch = _clean(item.get("watch_signal"))
            if not connection or not mechanism:
                continue

            core_candidate_text = _clean(f"{connection} {mechanism}")
            hidden_candidate_text = _clean(hidden)
            all_candidate_text = _clean(f"{core_candidate_text} {hidden_candidate_text}")
            support_entities = _unique_strings(
                name for r in support_records for name in r.get("entity_names", [])
            )
            mentioned_entities = [
                name for name in support_entities
                if name.casefold() in core_candidate_text.casefold()
            ]
            quantity_strings = _unique_strings(
                _quantity_display(q)
                for r in support_records
                for q in r.get("quantitative_claims", []) or []
                if _quantity_display(q)
            )
            mentioned_quantities = [
                q for q in quantity_strings
                if q and q.casefold() in core_candidate_text.casefold()
            ]
            relationship_strings = _unique_strings(
                _relationship_display(rel)
                for r in support_records
                for rel in r.get("relationship_mentions", []) or []
                if _relationship_display(rel)
            )
            relationship_anchor = any(
                rel and any(part.casefold() in core_candidate_text.casefold() for part in rel.split() if len(part) > 3)
                for rel in relationship_strings
            )

            # v3.1: structured extraction is valuable but imperfect. In recent
            # Fast runs many legitimate event records had zero entity/relationship
            # mentions even though their source text contained specific named actions.
            # The model contract already returns evidence_specific_anchors; validate
            # those anchors against the supplied support text and allow them to prove
            # specificity when the structured extractor missed the same detail.
            raw_model_anchors = item.get("evidence_specific_anchors", []) or []
            if isinstance(raw_model_anchors, str):
                raw_model_anchors = [raw_model_anchors]

            generic_anchor_values = {
                "ai", "artificial intelligence", "generative ai", "genai",
                "agentic ai", "enterprise", "enterprise ai", "market",
                "industry", "technology", "platform", "business", "company",
                "deployment", "adoption", "regulation", "governance",
            }
            verified_model_anchors: list[str] = []
            anchor_source_ids: set[str] = set()
            for raw_anchor in raw_model_anchors:
                anchor = _clean(raw_anchor).strip(" .,:;|-–—")
                anchor_low = anchor.casefold()
                if (
                    len(anchor) < 4
                    or anchor_low in generic_anchor_values
                    or len(anchor_low.split()) > 12
                ):
                    continue
                matched_sources: set[str] = set()
                for support_record in support_records:
                    support_haystack = _clean(
                        f"{support_record.get('event_title', '')} "
                        f"{support_record.get('text', '')}"
                    ).casefold()
                    if anchor_low in support_haystack:
                        source_id = _clean(support_record.get("source_id"))
                        if source_id:
                            matched_sources.add(source_id)
                if matched_sources:
                    verified_model_anchors.append(anchor)
                    anchor_source_ids.update(matched_sources)

            verified_model_anchors = _unique_strings(verified_model_anchors)
            verified_anchor_specificity = bool(
                len(verified_model_anchors) >= 2
                and len(anchor_source_ids) >= 2
            )

            evidence_specific = (
                len(set(x.casefold() for x in mentioned_entities)) >= 2
                or (len(mentioned_entities) >= 1 and bool(mentioned_quantities))
                or relationship_anchor
                or verified_anchor_specificity
            )
            core_directional_claim = bool(directional_pattern.search(core_candidate_text))
            hidden_directional_claim = bool(directional_pattern.search(hidden_candidate_text))
            directional_claim = core_directional_claim or hidden_directional_claim
            directional_supported = _support_has_directional_evidence(support_records)

            # A generic connection/mechanism is rejected completely. A useful
            # evidence-specific connection may survive even when only the hidden
            # implication overreaches directionally.
            if not evidence_specific:
                continue
            if core_directional_claim and not directional_supported:
                continue

            model_publish_value = item.get("publish_hidden_insight")
            if isinstance(model_publish_value, str):
                model_publish_value = model_publish_value.strip().casefold() in {
                    "true", "1", "yes", "y"
                }
            model_publish = bool(model_publish_value) and bool(hidden)
            publish_hidden = bool(
                model_publish
                and evidence_specific
                and (not hidden_directional_claim or directional_supported)
                and distinct_sources >= 2
            )
            if hidden_directional_claim and not directional_supported:
                hidden = ""
                second = ""
                publish_hidden = False

            insight_id = "INSIGHT-FAST-" + f"{len(output) + 1:02d}-" + uuid.uuid4().hex[:8].upper()
            revision_id = f"{insight_id}-R1"
            premises = [
                {
                    "premise_id": f"{revision_id}-PREMISE-{idx}",
                    "text": r["text"],
                    "evidence_ids": [r["evidence_id"]],
                    "status": "unresolved",
                }
                for idx, r in enumerate(support_records, start=1)
            ]
            insight_text = hidden if publish_hidden else ""
            output.append({
                "insight_id": insight_id,
                "insight_revision_id": revision_id,
                "insight_type": "descriptive",
                "insight": insight_text,
                "text": insight_text,
                "connection": connection,
                "evidence_ids": support_ids,
                "support_evidence_ids": support_ids,
                "supporting_evidence_ids": support_ids,
                "event_ids": _unique_strings(r.get("event_id") for r in support_records),
                "premises": premises,
                "mechanism": {
                    "description": mechanism,
                    "status": "unresolved",
                    "evidence_ids": support_ids,
                    "analogical_evidence_ids": [],
                },
                "why_it_matters": "",
                "second_order_effect": second if publish_hidden else "",
                "counterevidence_ids": [],
                "counter_incentive_evidence_ids": [],
                "alternative_explanations": [alternative] if alternative else [],
                "critical_unknowns": [unknown] if unknown else [],
                "uncertainty": unknown,
                "watch_signals": [watch] if watch else [],
                "quality_status": "unassessed",
                "epistemic_status": "unresolved",
                "research_question": topic_text,
                "fast_synthesis_mode": "bounded_evidence_conditioned_llm_v31",
                "analysis_mode": "fast",
                "synthesis_mode": "bounded_evidence_conditioned_llm",
                "fallback_used": False,
                "degraded": False,
                "trend_status": "model_supported_candidate" if publish_hidden else "connection_only",
                "independent_source_count": 0,
                "distinct_source_count": distinct_sources,
                "source_independence_status": "unknown_not_inferred",
                "trend_evidence": [r["label"] for r in support_records],
                "repeated_entities": mentioned_entities,
                "structured_entity_count": len(set(x.casefold() for x in support_entities)),
                "structured_quantity_count": sum(len(r.get("quantitative_claims", []) or []) for r in support_records),
                "structured_relationship_count": sum(len(r.get("relationship_mentions", []) or []) for r in support_records),
                "evidence_specificity_passed": evidence_specific,
                "directional_claim_detected": directional_claim,
                "directional_support_passed": directional_supported,
                "publish_hidden_insight": publish_hidden,
                "verification_query": _clean(item.get("verification_query")) or verification_query,
                "evidence_specific_anchors": verified_model_anchors,
                "verified_anchor_count": len(verified_model_anchors),
                "verified_anchor_source_count": len(anchor_source_ids),
                "connection_cluster_pair_score": self._last_fast_reasoning_metadata.get(
                    "connection_cluster_pair_score", 0.0
                ),
                "connection_cluster_signals": self._last_fast_reasoning_metadata.get(
                    "connection_cluster_signals", []
                ),
                "confidence": _enum(item.get("confidence")) or "low",
            })

        if not output:
            self._last_fast_reasoning_metadata.update({
                "reasoning_status": "abstained_post_validation",
                "degraded": False,
            })
            return self._fast_abstention_candidate(
                topic=topic_text,
                selected=selected,
                reason="The model output did not pass Fast evidence-specificity and grounding checks.",
                verification_query=verification_query,
            )

        self._last_fast_reasoning_metadata.update({
            "reasoning_status": "ok",
            "candidate_count": len(output),
            "publishable_hidden_insight_count": sum(1 for x in output if x.get("publish_hidden_insight")),
            "selected_evidence_count": len(selected),
            "selected_source_count": len({x.get("source_id") for x in selected if x.get("source_id")}),
        })
        return output

    def _fast_abstention_candidate(
        self,
        *,
        topic: str,
        selected: Sequence[Mapping[str, Any]],
        reason: str,
        verification_query: str = "",
    ) -> list[dict[str, Any]]:
        """Return one non-publishable Fast record without canned insight prose."""
        selected = [dict(x) for x in selected if isinstance(x, Mapping)]
        if not selected:
            return []
        support = selected[: min(3, len(selected))]
        evidence_ids = _unique_strings(x.get("evidence_id") for x in support)
        event_ids = _unique_strings(x.get("event_id") for x in support)
        insight_id = "INSIGHT-FAST-ABSTAIN-" + uuid.uuid4().hex[:8].upper()
        revision_id = f"{insight_id}-R1"
        return [{
            "insight_id": insight_id,
            "insight_revision_id": revision_id,
            "insight_type": "descriptive",
            "insight": _clean(f"Fast abstained from strategic inference: {reason}"),
            "text": _clean(f"Fast abstained from strategic inference: {reason}"),
            "connection": "",
            "evidence_ids": evidence_ids,
            "support_evidence_ids": evidence_ids,
            "supporting_evidence_ids": evidence_ids,
            "event_ids": event_ids,
            "premises": [
                {
                    "premise_id": f"{revision_id}-PREMISE-{idx}",
                    "text": _clean(x.get("text")),
                    "evidence_ids": [_clean(x.get("evidence_id"))],
                    "status": "unresolved",
                }
                for idx, x in enumerate(support, start=1)
            ],
            "mechanism": {
                "description": "",
                "status": "unresolved",
                "evidence_ids": evidence_ids,
                "analogical_evidence_ids": [],
            },
            "why_it_matters": "",
            "second_order_effect": "",
            "counterevidence_ids": [],
            "counter_incentive_evidence_ids": [],
            "alternative_explanations": [],
            "critical_unknowns": [_clean(reason)],
            "uncertainty": _clean(reason),
            "watch_signals": [],
            "quality_status": "unassessed",
            "epistemic_status": "unresolved",
            "research_question": _clean(topic),
            "fast_synthesis_mode": "bounded_evidence_conditioned_llm_v26_abstained",
            "analysis_mode": "fast",
            "synthesis_mode": "abstained",
            "fallback_used": False,
            "degraded": False,
            "trend_status": "abstained",
            "independent_source_count": 0,
            "distinct_source_count": len({_clean(x.get("source_id")) for x in support if _clean(x.get("source_id"))}),
            "source_independence_status": "unknown_not_inferred",
            "publish_hidden_insight": False,
            "verification_query": _clean(verification_query),
            "abstention_reason": _clean(reason),
        }]

    def _fast_confirm_trend_evidence(
        self,
        *,
        topic: str,
        provisional_candidates: Sequence[Mapping[str, Any]],
        selected_source_ids: Sequence[str],
        event_records: Sequence[Mapping[str, Any]],
        evidence_records: Sequence[Mapping[str, Any]],
        state: dict[str, Any],
        max_confirmation_sources: int = 3,
    ) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
        """Run one bounded evidence-confirmation search for the strongest Fast thesis.

        This is deliberately not an LLM reasoning step. It exists to answer the
        product question that matters most for Fast mode: does the initial
        cross-event hypothesis survive contact with additional independent
        evidence?

        The search is bounded to one thesis and at most three additional
        persisted sources. If confirmation fails, the original evidence remains
        unchanged and the candidate is explicitly treated as a hypothesis rather
        than silently promoted to a trend.
        """
        if (
            not provisional_candidates
            or self.web_search_service is None
            or self.source_fetch_service is None
        ):
            state["fast_trend_confirmation"] = {
                "attempted": False,
                "reason": "no_candidate_or_search_dependency",
            }
            return (
                list(selected_source_ids),
                [dict(item) for item in event_records],
                [dict(item) for item in evidence_records],
            )

        candidate = next(
            (
                item
                for item in provisional_candidates
                if isinstance(item, Mapping)
                and _clean(item.get("verification_query"))
            ),
            None,
        )
        if candidate is None:
            state["fast_trend_confirmation"] = {
                "attempted": False,
                "reason": "candidate_has_no_verification_query",
            }
            return (
                list(selected_source_ids),
                [dict(item) for item in event_records],
                [dict(item) for item in evidence_records],
            )

        verification_query = _clean(candidate.get("verification_query"))
        current_year = datetime.now(timezone.utc).year
        verification_query = _clean(
            f"{verification_query} {current_year} current observed procurement contract deployment"
        )
        confirmation_state: dict[str, Any] = {
            "research_depth": "fast",
            "warnings": [],
            "errors": [],
            "stage_status": {},
            "topic": verification_query,
            "run_id": _clean(state.get("run_id")),
        }

        try:
            discovered = self._discover_sources_with_run_circuit_breaker(
                topic=verification_query,
                max_sources=max(1, min(int(max_confirmation_sources), 3)),
                parent_state=state,
                discovery_state=confirmation_state,
                discovery_phase="fast_trend_confirmation",
            )
        except Exception as error:
            logger.warning(
                "Fast trend confirmation search failed. query=%s error=%s message=%s",
                verification_query,
                type(error).__name__,
                str(error),
            )
            state["fast_trend_confirmation"] = {
                "attempted": True,
                "query": verification_query,
                "confirmed_new_source_count": 0,
                "error": type(error).__name__,
                "message": str(error),
                "warnings": confirmation_state.get("warnings", []),
            }
            return (
                list(selected_source_ids),
                [dict(item) for item in event_records],
                [dict(item) for item in evidence_records],
            )

        original_ids = set(_unique_strings(selected_source_ids))
        new_source_ids = [
            source_id
            for source_id in _unique_strings(discovered)
            if source_id not in original_ids
        ]

        if not new_source_ids:
            state["fast_trend_confirmation"] = {
                "attempted": True,
                "query": verification_query,
                "confirmed_new_source_count": 0,
                "warnings": confirmation_state.get("warnings", []),
                "search_failures": confirmation_state.get(
                    "source_discovery_search_failures", []
                ),
                "fetch_failures": confirmation_state.get(
                    "source_discovery_failures", []
                ),
            }
            return (
                list(selected_source_ids),
                [dict(item) for item in event_records],
                [dict(item) for item in evidence_records],
            )

        # Keep only a small number of confirmation sources even if the discovery
        # service returns more due to persistence de-duplication behavior.
        new_source_ids = new_source_ids[: max(1, min(int(max_confirmation_sources), 3))]

        try:
            new_events = self._extract_events(
                source_ids=new_source_ids,
                state=confirmation_state,
            )

            if not new_events:
                raise ResearchPipelineError(
                    "Fast trend confirmation sources produced no structured events."
                )

            new_source_payloads = self._source_payloads(new_source_ids)
            new_evidence_payload = self.evidence_builder_agent.build_evidence(
                topic=topic,
                events=new_events,
                sources=new_source_payloads,
                semantic_fact_extraction=False,
                semantic_fail_closed=False,
            )
            new_evidence_payload = _as_mapping(new_evidence_payload)
            new_evidence = list(
                new_evidence_payload.get("evidence_records", []) or []
            )

            if new_evidence:
                self._validate_evidence_currency(new_evidence)

        except Exception as error:
            logger.warning(
                "Fast trend confirmation evidence build failed. query=%s error=%s message=%s",
                verification_query,
                type(error).__name__,
                str(error),
            )
            state["fast_trend_confirmation"] = {
                "attempted": True,
                "query": verification_query,
                "confirmed_new_source_count": len(new_source_ids),
                "admitted_new_evidence_count": 0,
                "error": type(error).__name__,
                "message": str(error),
            }
            return (
                list(selected_source_ids),
                [dict(item) for item in event_records],
                [dict(item) for item in evidence_records],
            )

        merged_sources = _unique_strings(
            list(selected_source_ids) + list(new_source_ids)
        )

        merged_events_by_id: dict[str, dict[str, Any]] = {}
        for item in list(event_records) + list(new_events):
            if not isinstance(item, Mapping):
                continue
            key = _clean(item.get("event_id")) or uuid.uuid4().hex
            merged_events_by_id[key] = dict(item)

        merged_evidence_by_id: dict[str, dict[str, Any]] = {}
        for item in list(evidence_records) + list(new_evidence):
            if not isinstance(item, Mapping):
                continue
            key = _clean(item.get("evidence_id"))
            if not key:
                continue
            merged_evidence_by_id[key] = dict(item)

        state["fast_trend_confirmation"] = {
            "attempted": True,
            "query": verification_query,
            "confirmed_new_source_count": len(new_source_ids),
            "confirmed_new_source_ids": list(new_source_ids),
            "new_event_count": len(new_events),
            "admitted_new_evidence_count": len(new_evidence),
            "warnings": confirmation_state.get("warnings", []),
            "search_failures": confirmation_state.get(
                "source_discovery_search_failures", []
            ),
            "fetch_failures": confirmation_state.get(
                "source_discovery_failures", []
            ),
        }

        return (
            merged_sources,
            list(merged_events_by_id.values()),
            list(merged_evidence_by_id.values()),
        )

    @staticmethod
    def _fast_red_team_review(
        *,
        report: Mapping[
            str,
            Any,
        ],
        grounding: Mapping[
            str,
            Any,
        ],
    ) -> dict[str, Any]:

        issues: list[
            dict[str, Any]
        ] = []

        grounding_status = _enum(
            grounding.get(
                "review_status"
            )
            or grounding.get(
                "status"
            )
        )

        if (
            grounding_status
            not in {
                "complete",
                "ok",
                "pass",
                "passed",
            }
        ):
            issues.append(
                {
                    "severity": (
                        "critical"
                    ),
                    "type": (
                        "grounding_incomplete"
                    ),
                    "message": (
                        "Fast report-level "
                        "grounding is not complete."
                    ),
                }
            )

        for item in (
            report.get(
                "executive_synthesis",
                [],
            )
            or []
        ):
            if (
                isinstance(
                    item,
                    Mapping,
                )
                and not (
                    item.get(
                        "evidence_ids"
                    )
                    or []
                )
            ):
                issues.append(
                    {
                        "severity": (
                            "high"
                        ),
                        "type": (
                            "missing_evidence_link"
                        ),
                        "message": (
                            "An executive-synthesis "
                            "item has no evidence ID."
                        ),
                    }
                )

        assessment_seed = (
            _clean(
                report.get(
                    "run_id"
                )
            )
            + "|"
            + str(
                len(
                    issues
                )
            )
        )

        assessment_id = (
            "REDTEAM-"
            + uuid.uuid5(
                uuid.NAMESPACE_URL,
                assessment_seed,
            )
            .hex[:24]
            .upper()
        )

        return {
            "red_team_assessment_id": (
                assessment_id
            ),
            "review_status": (
                "complete"
            ),
            "status": (
                "complete"
            ),
            "red_team_complete": (
                True
            ),
            "review_mode": (
                "fast_deterministic_"
                "release_check"
            ),
            "issues": issues,
            "critical_issue_count": sum(
                1
                for issue in issues
                if _enum(
                    issue.get(
                        "severity"
                    )
                )
                == "critical"
            ),
            "high_issue_count": sum(
                1
                for issue in issues
                if _enum(
                    issue.get(
                        "severity"
                    )
                )
                == "high"
            ),
            "metadata": {
                "search_performed": False,
                "private_chain_of_thought_stored": False,
            },
        }

    def _identify_constraints(
        self,
        *,
        topic: str,
        events: Sequence[
            Mapping[str, Any]
        ],
        evidence: Sequence[
            Mapping[str, Any]
        ],
        graph_context: Mapping[
            str,
            Any,
        ],
    ) -> Any:

        return (
            self._call_method(
                self.constraint_agent,
                (
                    "analyze",
                    "identify_constraints",
                    "extract_constraints",
                ),
                {
                    "topic": topic,
                    "research_question": (
                        topic
                    ),
                    "current_events": (
                        events
                    ),
                    "events": events,
                    "evidence": evidence,
                    "graph_context": (
                        graph_context
                    ),
                },
            )
        )

    @staticmethod
    def _finding_tier(insight: Mapping[str, Any]) -> str:
        metadata = insight.get("metadata") if isinstance(insight.get("metadata"), Mapping) else {}
        tier = _enum(insight.get("finding_tier") or metadata.get("finding_tier"))
        if tier in {"hidden_insight", "underlying_dynamic", "strategic_connection", "early_signal"}:
            return tier
        return "hidden_insight" if bool(insight.get("publish_hidden_insight", True)) else "strategic_connection"

    @classmethod
    def _finding_tier_rank(cls, insight: Mapping[str, Any]) -> int:
        return {
            "hidden_insight": 4,
            "underlying_dynamic": 3,
            "strategic_connection": 2,
            "early_signal": 1,
        }.get(cls._finding_tier(insight), 0)

    @classmethod
    def _has_hidden_insight_candidate(cls, insights: Sequence[Mapping[str, Any]]) -> bool:
        return any(
            isinstance(item, Mapping) and cls._finding_tier(item) == "hidden_insight"
            for item in insights
        )

    def _assess_insights(
        self,
        *,
        insights: Sequence[
            Mapping[str, Any]
        ],
        evidence: Sequence[
            Mapping[str, Any]
        ],
        constraints: Any,
        blueprint: Mapping[
            str,
            Any,
        ],
    ) -> list[dict[str, Any]]:

        output = []

        normalized_constraints = (
            self._constraint_list(
                constraints
            )
        )

        for insight in insights:
            assessment = (
                self.insight_quality_service
                .assess(
                    insight=insight,
                    evidence=evidence,
                    grounding=None,
                    constraints=(
                        normalized_constraints
                    ),
                    blueprint=blueprint,
                )
            )

            assessment_map = (
                _as_mapping(
                    assessment
                )
            )

            output.append(
                {
                    "insight": (
                        dict(
                            insight
                        )
                    ),
                    "quality": (
                        assessment_map
                    ),
                }
            )

        return output

    @staticmethod
    def _quality_score(
        assessment: Mapping[
            str,
            Any,
        ],
    ) -> float:

        for key in (
            "quality_score",
            "aggregate_score",
            "overall_score",
            "score",
        ):
            value = (
                assessment.get(
                    key
                )
            )

            if value is not None:
                try:
                    return (
                        float(
                            value
                        )
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    pass

        dimensions = (
            assessment.get(
                "dimensions"
            )
        )

        if isinstance(
            dimensions,
            Mapping,
        ):
            numeric = []

            for value in (
                dimensions.values()
            ):
                if isinstance(
                    value,
                    Mapping,
                ):
                    value = (
                        value.get(
                            "score"
                        )
                    )

                try:
                    numeric.append(
                        float(
                            value
                        )
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    continue

            if numeric:
                return (
                    sum(
                        numeric
                    )
                    / len(
                        numeric
                    )
                )

        status = _enum(
            assessment.get(
                "quality_status"
            )
        )

        return {
            "strong": 4.0,
            "usable_with_limits": 3.0,
            "needs_refinement": 2.0,
            "insufficient_evidence": 1.0,
        }.get(
            status,
            0.0,
        )

    def _select_insights(
        self,
        assessed: Sequence[
            Mapping[str, Any]
        ],
        *,
        max_selected: int,
        final: bool = False,
    ) -> list[dict[str, Any]]:

        ranked = sorted(
            assessed,
            key=lambda item: (
                self._finding_tier_rank(
                    item.get("insight", {})
                    if isinstance(item.get("insight"), Mapping)
                    else {}
                ),
                self._quality_score(
                    item.get(
                        "quality",
                        {},
                    )
                ),
            ),
            reverse=True,
        )

        output = []

        for item in ranked:
            quality = (
                item.get(
                    "quality",
                    {},
                )
            )

            status = _enum(
                quality.get(
                    "quality_status"
                )
            )

            if (
                final
                and status
                in {
                    "insufficient_evidence"
                }
            ):
                continue

            record = dict(
                item.get(
                    "insight",
                    {},
                )
            )

            record[
                "quality_assessment"
            ] = quality

            output.append(
                record
            )

            if (
                len(output)
                >= max_selected
            ):
                break

        return output

    @staticmethod
    def _strategic_candidate_investigation_score(
        *,
        reason: str,
        snapshot: Mapping[str, Any],
    ) -> float:
        """Score whether a rejected candidate is worth spending web-search budget on.

        This is intentionally deterministic and conservative. Search is for
        evidence gaps in already-promising hypotheses, not for rescuing generic
        explanations or structurally malformed output.
        """
        reason = _enum(reason)
        if reason not in STRATEGIC_EVIDENCE_RESCUE_REASONS:
            return 0.0

        insight = _clean(snapshot.get("insight"))
        support_ids = _unique_strings(snapshot.get("supporting_evidence_ids") or [])
        premises = [
            item
            for item in (snapshot.get("premises") or [])
            if isinstance(item, Mapping)
        ]
        mechanism_raw = snapshot.get("mechanism")
        mechanism = (
            _clean(mechanism_raw.get("description") or mechanism_raw.get("mechanism"))
            if isinstance(mechanism_raw, Mapping)
            else _clean(mechanism_raw)
        )
        quality = _as_mapping(snapshot.get("insight_quality_check"))
        scope = _as_mapping(snapshot.get("scope_validation"))

        score = 20.0
        if len(insight) >= 120:
            score += 10.0
        if len(support_ids) >= 2:
            score += 20.0
        elif len(support_ids) == 1:
            score += 8.0
        if len(premises) >= 2:
            score += 15.0
        elif premises:
            score += 6.0
        if len(mechanism) >= 80:
            score += 15.0
        elif len(mechanism) >= 40:
            score += 7.0
        if reason in {
            "support_not_cross_source",
            "fewer_than_two_derivation_observations",
            "sector_pattern_requires_three_distinct_actors",
            "sector_pattern_requires_three_distinct_acting_institutions",
            "cross_entity_pattern_requires_multiple_actors",
            "cross_entity_pattern_requires_two_distinct_acting_institutions",
            "sector_pattern_requires_non_focal_corroboration",
            "comparative_claim_requires_comparative_evidence",
            "deep_candidate_requires_coherent_support_cluster",
            "deep_candidate_missing_cluster_coherence_reason",
        }:
            score += 15.0

        # Do not spend search budget on something the model itself admits is an
        # explainer or something that does not actually depend on the events.
        if bool(quality.get("is_merely_explanatory", False)):
            score -= 70.0
        if bool(scope.get("could_be_written_without_events", False)):
            score -= 70.0
        if _enum(scope.get("event_dependency")) not in {"", "necessary"}:
            score -= 35.0

        return max(0.0, min(100.0, score))

    @staticmethod
    def _strategic_query_terms(value: Any, *, maximum_words: int = 12) -> str:
        text = _clean(value)
        if not text:
            return ""
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9+._/-]*", text)
        stop = {
            "the", "a", "an", "and", "or", "of", "to", "for", "in", "on",
            "with", "by", "from", "that", "this", "these", "those", "may",
            "might", "could", "would", "should", "is", "are", "was", "were",
            "be", "been", "being", "as", "at", "into", "than", "then",
        }
        output: list[str] = []
        seen: set[str] = set()
        for word in words:
            key = word.casefold()
            if key in stop or key in seen or len(word) < 3:
                continue
            seen.add(key)
            output.append(word)
            if len(output) >= maximum_words:
                break
        return " ".join(output)

    @classmethod
    def _strategic_candidate_context_for_rescue(
        cls,
        *,
        snapshot: Mapping[str, Any],
        events: Sequence[Mapping[str, Any]],
        evidence: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Resolve the event/cluster/relation envelope for a rejected candidate."""
        event_by_id = {
            _clean(item.get("event_id")).upper(): item
            for item in events
            if isinstance(item, Mapping) and _clean(item.get("event_id"))
        }
        evidence_by_id = {
            _clean(item.get("evidence_id") or item.get("id")).upper(): item
            for item in evidence
            if isinstance(item, Mapping)
            and _clean(item.get("evidence_id") or item.get("id"))
        }
        event_ids = _unique_strings(
            _clean(value).upper() for value in (snapshot.get("event_ids") or []) if _clean(value)
        )
        if not event_ids:
            for evidence_id in snapshot.get("supporting_evidence_ids") or []:
                item = evidence_by_id.get(_clean(evidence_id).upper())
                if not item:
                    continue
                event_id = _clean(item.get("event_id")).upper()
                if event_id and event_id not in event_ids:
                    event_ids.append(event_id)

        selected = [event_by_id[eid] for eid in event_ids if eid in event_by_id]
        clusters = _unique_strings(
            _clean(item.get("strategic_cluster_id")) for item in selected
            if _clean(item.get("strategic_cluster_id"))
        )
        mechanisms = _unique_strings(
            tag
            for item in selected
            for tag in (item.get("strategic_mechanism_tags") or [])
            if _clean(tag)
        )
        dependencies = _unique_strings(
            tag
            for item in selected
            for tag in (item.get("strategic_dependency_tags") or [])
            if _clean(tag)
        )
        actors = _unique_strings(
            cls._event_primary_institution(item) for item in selected
            if cls._event_primary_institution(item)
        )
        linkage = _as_mapping(snapshot.get("evidence_linkage_diagnostics"))
        return {
            "event_ids": event_ids,
            "resolved_event_count": len(selected),
            "strategic_cluster_ids": clusters,
            "mechanism_tags": mechanisms,
            "dependency_tags": dependencies,
            "acting_institutions": actors,
            "support_cluster_resolved": bool(linkage.get("support_cluster_resolved")),
            "support_cluster_id": _clean(linkage.get("raw_support_cluster_id")),
            "cluster_coherence_reason": _clean(linkage.get("cluster_coherence_reason")),
            "cross_cluster_bridge_reason": _clean(linkage.get("cluster_cross_cluster_bridge_reason")),
        }

    @classmethod
    def _strategic_candidate_structural_rescue_eligibility(
        cls,
        *,
        snapshot: Mapping[str, Any],
        events: Sequence[Mapping[str, Any]],
        evidence: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Allow search only for a structurally eligible but evidence-incomplete thesis.

        The rescue loop must never search for a bridge that the initial evidence did
        not establish. In particular, topic overlap and same-actor-only cross-cluster
        pairings are not rescueable.
        """
        context = cls._strategic_candidate_context_for_rescue(
            snapshot=snapshot, events=events, evidence=evidence
        )
        quality = _as_mapping(snapshot.get("insight_quality_check"))
        scope = _as_mapping(snapshot.get("scope_validation"))
        mechanism_raw = snapshot.get("mechanism")
        mechanism = (
            _clean(mechanism_raw.get("description") or mechanism_raw.get("mechanism"))
            if isinstance(mechanism_raw, Mapping)
            else _clean(mechanism_raw)
        )
        reasons: list[str] = []
        if bool(quality.get("is_merely_explanatory", False)):
            reasons.append("candidate_is_merely_explanatory")
        if bool(scope.get("could_be_written_without_events", False)):
            reasons.append("candidate_not_event_dependent")
        if _enum(scope.get("event_dependency")) not in {"", "necessary"}:
            reasons.append("candidate_event_dependency_not_necessary")
        if len(mechanism) < 30:
            reasons.append("candidate_mechanism_too_thin_for_gap_search")
        if context["resolved_event_count"] <= 0:
            reasons.append("candidate_events_not_resolved")

        clusters = list(context.get("strategic_cluster_ids") or [])
        if len(clusters) > 1:
            bridge = _clean(context.get("cross_cluster_bridge_reason"))
            if not bridge:
                reasons.append("cross_cluster_candidate_has_no_explicit_bridge")
            elif bridge == "cross_cluster_shared_acting_institution":
                reasons.append("same_actor_only_is_not_a_structural_bridge")

        eligible = not reasons
        return {
            "eligible": eligible,
            "reasons": reasons,
            "candidate_context": context,
            "rule": (
                "search_only_structurally_eligible_evidence_incomplete_candidates;"
                "never_search_to_manufacture_a_bridge"
            ),
        }

    @staticmethod
    def _strategic_candidate_alternative_texts(snapshot: Mapping[str, Any]) -> list[str]:
        alternatives: list[str] = []
        for item in snapshot.get("alternative_explanations") or []:
            if isinstance(item, Mapping):
                text = _clean(item.get("text") or item.get("alternative") or item.get("explanation"))
            else:
                text = _clean(item)
            if text and text not in alternatives:
                alternatives.append(text[:900])
            if len(alternatives) >= 4:
                break
        if not alternatives:
            alternatives = [
                "The observed relationship is actor-specific or contextual rather than a general strategic mechanism.",
                "A different constraint, incentive, or timing factor explains the observed actions better than the leading hypothesis.",
            ]
        return alternatives

    def _strategic_collection_plan(
        self,
        *,
        topic: str,
        reason: str,
        snapshot: Mapping[str, Any],
        events: Sequence[Mapping[str, Any]],
        evidence: Sequence[Mapping[str, Any]],
        round_number: int,
    ) -> dict[str, Any]:
        """Create an auditable, non-CoT hypothesis-testing collection plan."""
        reason = _enum(reason)
        eligibility = self._strategic_candidate_structural_rescue_eligibility(
            snapshot=snapshot, events=events, evidence=evidence
        )
        context = _as_mapping(eligibility.get("candidate_context"))
        mechanism_raw = snapshot.get("mechanism")
        mechanism = (
            _clean(mechanism_raw.get("description") or mechanism_raw.get("mechanism"))
            if isinstance(mechanism_raw, Mapping)
            else _clean(mechanism_raw)
        )
        main_hypothesis = _clean(snapshot.get("insight"))[:1800]
        alternatives = self._strategic_candidate_alternative_texts(snapshot)
        critical_unknowns = [
            _clean(item)[:800] for item in (snapshot.get("critical_unknowns") or []) if _clean(item)
        ][:4]
        derivation = _as_mapping(snapshot.get("derivation_summary"))
        falsifiers = [
            _clean(item)[:800] for item in (derivation.get("falsifiers") or []) if _clean(item)
        ][:4]

        gap_catalog: dict[str, dict[str, Any]] = {
            "fewer_than_two_supporting_evidence_records": {
                "needed_fact": "A distinct underlying event that materially supports, weakens, or falsifies the proposed mechanism rather than merely repeating the same claim.",
                "why_it_matters": "Distinguishes a one-event explanation from an independently testable strategic pattern.",
                "event_classes": ["material_action", "commercial_contract", "product_launch", "policy_or_regulation", "capacity_or_infrastructure", "operating_metric"],
                "source_types": ["company filing", "official announcement", "customer statement", "regulator record", "contract or project record"],
            },
            "fewer_than_two_derivation_observations": {
                "needed_fact": "A second distinct evidence-backed factual observation, tied to a distinct underlying event or materially different observed outcome, that advances, weakens, or falsifies the proposed mechanism instead of restating the existing observation.",
                "why_it_matters": "Multiple EVID records can still collapse to one factual observation. A Hidden Insight needs at least two distinct observed facts that create a real derivation path rather than citation-count inflation.",
                "event_classes": ["material_action", "commercial_contract", "product_launch", "policy_or_regulation", "legal_action", "capacity_or_infrastructure", "customer_adoption", "operating_metric", "market_signal"],
                "source_types": ["company filing", "official announcement", "customer or supplier statement", "regulator record", "court or project record", "independent operating record"],
            },
            "fewer_than_two_evidence_backed_premises": {
                "needed_fact": "A material event that independently establishes the currently unsupported factual premise or shows that premise is wrong.",
                "why_it_matters": "Prevents the mechanism from depending on a premise that exists only in model synthesis.",
                "event_classes": ["material_action", "commercial_contract", "policy_or_regulation", "legal_action", "capacity_or_infrastructure", "operating_metric"],
                "source_types": ["primary company source", "regulator record", "court or planning record", "customer or supplier statement"],
            },
            "support_not_cross_source": {
                "needed_fact": "Independent-source evidence of the same underlying mechanism, or credible evidence that contradicts it.",
                "why_it_matters": "Separates source repetition from independent corroboration.",
                "event_classes": ["material_action", "commercial_contract", "policy_or_regulation", "capacity_or_infrastructure", "operating_metric"],
                "source_types": ["independent primary source", "regulator record", "customer statement", "company filing"],
            },
            "comparative_claim_requires_comparative_evidence": {
                "needed_fact": "An observed outcome or counterexample that directly discriminates the leading explanation from a competing constraint or mechanism.",
                "why_it_matters": "A comparative claim requires evidence about alternatives, not just more examples of the preferred narrative.",
                "event_classes": ["operating_metric", "commercial_contract", "policy_or_regulation", "legal_action", "capacity_or_infrastructure", "material_action"],
                "source_types": ["official outcome record", "customer contract", "regulator record", "company filing", "independent operator example"],
            },
        }
        actor_gap_reasons = {
            "cross_entity_pattern_requires_multiple_actors",
            "cross_entity_pattern_requires_two_distinct_acting_institutions",
            "sector_pattern_requires_three_distinct_actors",
            "sector_pattern_requires_three_distinct_acting_institutions",
            "sector_pattern_requires_non_focal_corroboration",
        }
        event_gap_reasons = {
            "cross_entity_pattern_requires_multiple_events",
            "sector_pattern_requires_three_distinct_events",
        }
        source_gap_reasons = {
            "cross_entity_pattern_requires_multiple_source_groups",
            "sector_pattern_requires_multiple_source_groups",
        }
        if reason in actor_gap_reasons:
            gap = {
                "needed_fact": "A comparable material action by another independent acting institution showing the same mechanism, or a counterexample showing the mechanism is actor-specific.",
                "why_it_matters": "Tests whether the candidate generalizes beyond the focal actor.",
                "event_classes": ["material_action", "commercial_contract", "product_launch", "policy_or_regulation", "capacity_or_infrastructure", "operating_metric"],
                "source_types": ["primary company source", "regulator record", "customer statement", "independent operator record"],
            }
        elif reason in event_gap_reasons:
            gap = {
                "needed_fact": "Another distinct underlying event connected by the same evidenced mechanism or a material counterexample.",
                "why_it_matters": "Tests whether the apparent mechanism recurs across separate real-world actions.",
                "event_classes": ["material_action", "commercial_contract", "policy_or_regulation", "capacity_or_infrastructure", "operating_metric"],
                "source_types": ["official announcement", "company filing", "regulator or project record", "customer statement"],
            }
        elif reason in source_gap_reasons:
            gap = {
                "needed_fact": "Independent-source confirmation or contradiction tied to a distinct underlying event or authoritative primary record.",
                "why_it_matters": "Prevents multiple articles about one action from masquerading as independent evidence.",
                "event_classes": ["material_action", "commercial_contract", "policy_or_regulation", "capacity_or_infrastructure", "operating_metric"],
                "source_types": ["primary source", "regulator record", "customer or supplier statement", "official project record"],
            }
        else:
            gap = dict(gap_catalog.get(reason) or gap_catalog["fewer_than_two_supporting_evidence_records"])

        if critical_unknowns:
            gap["critical_unknown"] = critical_unknowns[0]
        gap["discrimination_question"] = (
            "What observable event would make the leading mechanism more credible, less credible, or false relative to the competing explanations?"
        )
        gap["target_mechanism_tags"] = list(context.get("mechanism_tags") or [])[:8]
        gap["target_dependency_tags"] = list(context.get("dependency_tags") or [])[:8]
        gap["priority"] = "high"

        # Deterministic priority is a budget-allocation score, not a probability.
        priority_score = 70.0
        if reason in actor_gap_reasons or reason == "comparative_claim_requires_comparative_evidence":
            priority_score += 10.0
        if critical_unknowns or falsifiers:
            priority_score += 5.0
        if len(context.get("mechanism_tags") or []) + len(context.get("dependency_tags") or []) >= 2:
            priority_score += 5.0
        priority_score = min(95.0, priority_score)

        return {
            "candidate_id": _clean(snapshot.get("insight_id")) or "REJECTED-CANDIDATE",
            "round": round_number,
            "structural_eligibility": eligibility,
            "main_hypothesis": main_hypothesis,
            "alternatives": alternatives,
            "mechanism": mechanism[:1400],
            "candidate_context": context,
            "falsifiers": falsifiers,
            "gaps": [gap],
            "collection_priority_score": priority_score,
            "collection_priority_formula": (
                "decision_relevance * discrimination_value * expected_source_quality "
                "* probability_of_finding_evidence - duplication_risk - scope_expansion_risk"
            ),
            "stop_rule": (
                "Maximum two rounds; maximum three queries per round; stop when no new "
                "independent underlying event or gap-relevant structure is admitted; abstain "
                "if the bridge remains merely plausible."
            ),
            "search_rule": "search_for_evidence_that_can_support_weaken_or_falsify_not_confirmation_only",
        }

    def _strategic_evidence_rescue_queries(
        self,
        *,
        topic: str,
        reason: str,
        snapshot: Mapping[str, Any],
        round_number: int,
        collection_plan: Optional[Mapping[str, Any]] = None,
    ) -> list[str]:
        plan = _as_mapping(collection_plan)
        gap = _as_mapping((plan.get("gaps") or [{}])[0])
        context = _as_mapping(plan.get("candidate_context"))
        topic_terms = self._strategic_query_terms(topic, maximum_words=5)
        mechanism_terms = self._strategic_query_terms(
            plan.get("mechanism") or _as_mapping(snapshot.get("mechanism")).get("description"),
            maximum_words=7,
        )
        actors = [
            self._strategic_query_terms(value, maximum_words=4)
            for value in (context.get("acting_institutions") or [])[:2]
            if _clean(value)
        ]
        actor_terms = " ".join(value for value in actors if value)
        dependency_terms = " ".join(
            _clean(value).replace("_", " ")
            for value in (gap.get("target_dependency_tags") or [])[:3]
            if _clean(value)
        )
        event_classes = set(_enum(value) for value in (gap.get("event_classes") or []) if _enum(value))
        relation_tags = {
            _enum(value) for value in (
                list(context.get("mechanism_tags") or [])
                + list(context.get("dependency_tags") or [])
            ) if _enum(value)
        }

        # Query vocabulary follows the candidate's actual mechanism/dependency
        # signature first. The broad event-class allowlist is only a fallback;
        # otherwise an enterprise-model candidate could accidentally receive
        # infrastructure search terms merely because infrastructure is one of
        # several acceptable evidence classes.
        if relation_tags & {
            "foundation_model_procurement", "model_provider", "model_portability",
            "switching_portability", "developer_productivity", "enterprise_integration",
            "cloud_provider",
        }:
            outcome_terms = ["model procurement deployment switching portability customer contract production usage"]
        elif relation_tags & {
            "compute_capacity", "compute_supply", "power_capacity", "power_grid",
            "advanced_packaging_capacity", "advanced_packaging_supply", "customer_commitment",
        }:
            outcome_terms = ["capacity customer commitment GPU delivery site power grid permit construction operational"]
        elif relation_tags & {"capital_formation", "capital_access"}:
            outcome_terms = ["financing use of proceeds collateral covenants drawdown asset customer contract deployment"]
        elif relation_tags & {"regulatory_authorization", "regulatory_approval", "data_residency_governance"}:
            outcome_terms = ["regulator order approval denial filing compliance permit commercial response"]
        elif relation_tags & {"robotics_commercialization"}:
            outcome_terms = ["robot customer deployment production sales authorization commercial launch"]
        elif "policy_or_regulation" in event_classes or "legal_action" in event_classes:
            outcome_terms = ["regulator order approval denial filing lawsuit compliance response"]
        elif "commercial_contract" in event_classes:
            outcome_terms = ["contract customer supplier procurement agreement deployment outcome"]
        elif "operating_metric" in event_classes:
            outcome_terms = ["production usage adoption measured outcome"]
        elif "product_launch" in event_classes:
            outcome_terms = ["customer deployment production launch usage"]
        elif "capacity_or_infrastructure" in event_classes:
            outcome_terms = ["capacity project permit power grid construction operational"]
        else:
            outcome_terms = ["deployment contract outcome official record"]

        falsifier_terms = self._strategic_query_terms(
            " ".join(plan.get("falsifiers") or plan.get("alternatives") or []),
            maximum_words=7,
        )
        queries = [
            f"{actor_terms} {mechanism_terms} {dependency_terms} {outcome_terms[0]}",
            f"{topic_terms} {mechanism_terms} counterexample alternative {falsifier_terms}",
            f"{actor_terms or topic_terms} {dependency_terms or mechanism_terms} official filing regulator customer contract outcome",
        ]
        if round_number > 1:
            queries = [f"{query} independent source" for query in queries]

        normalized: list[str] = []
        seen: set[str] = set()
        for query in queries:
            query = " ".join(_clean(query).split())[:420]
            key = query.casefold()
            if not query or key in seen:
                continue
            seen.add(key)
            normalized.append(query)
            if len(normalized) >= 3:
                break
        return normalized

    @classmethod
    def _rescue_material_information_assessment(
        cls,
        *,
        plan: Mapping[str, Any],
        existing_events: Sequence[Mapping[str, Any]],
        new_events: Sequence[Mapping[str, Any]],
        topic: str,
    ) -> dict[str, Any]:
        """Admit rescue events only when they close a defined structural gap."""
        combined, underlying_diag = cls._annotate_underlying_events(
            events=list(existing_events) + list(new_events), topic=topic
        )
        existing_ids = {
            _clean(item.get("event_id")).upper()
            for item in existing_events if isinstance(item, Mapping) and _clean(item.get("event_id"))
        }
        existing_underlying = {
            _clean(item.get("underlying_event_id")).upper()
            for item in combined
            if _clean(item.get("event_id")).upper() in existing_ids
            and _clean(item.get("underlying_event_id"))
        }
        gap = _as_mapping((plan.get("gaps") or [{}])[0])
        context = _as_mapping(plan.get("candidate_context"))
        target_mechanisms = set(_unique_strings(gap.get("target_mechanism_tags") or context.get("mechanism_tags") or []))
        target_dependencies = set(_unique_strings(gap.get("target_dependency_tags") or context.get("dependency_tags") or []))
        target_classes = set(_enum(value) for value in (gap.get("event_classes") or []) if _enum(value))
        existing_actors = {
            cls._canonical_actor_key(value)
            for value in (context.get("acting_institutions") or []) if cls._canonical_actor_key(value)
        }

        admitted_event_ids: list[str] = []
        rows: list[dict[str, Any]] = []
        for item in combined:
            event_id = _clean(item.get("event_id")).upper()
            if not event_id or event_id in existing_ids:
                continue
            underlying_id = _clean(item.get("underlying_event_id")).upper()
            new_underlying = bool(underlying_id and underlying_id not in existing_underlying)
            mechanisms = set(_unique_strings(item.get("strategic_mechanism_tags") or []))
            dependencies = set(_unique_strings(item.get("strategic_dependency_tags") or []))
            event_class = _enum(item.get("strategic_event_class"))
            actor = cls._event_primary_institution(item)
            actor_key = cls._canonical_actor_key(actor)
            shared_mechanisms = sorted(mechanisms & target_mechanisms)
            shared_dependencies = sorted(dependencies & target_dependencies)
            class_match = bool(event_class and event_class in target_classes)
            new_actor = bool(actor_key and actor_key not in existing_actors)
            text = cls._strategic_event_text(item, []).casefold()
            contradiction_signal = bool(re.search(
                r"\b(?:denied|rejected|cancel(?:led|ed)|halted|failed|declined|reversed|"
                r"no evidence|not material|unconstrained|approved despite|proceeded despite)\b",
                text,
                re.I,
            ))
            # Event class is only a routing hint. It cannot by itself close a
            # hypothesis gap, otherwise any new AI contract or launch could be
            # admitted as "more evidence". Require relation-tag overlap, a
            # contradiction signal, or distinctive overlap with the defined gap.
            gap_blob = _clean(
                str(plan.get("mechanism") or "") + " " +
                str(gap.get("needed_fact") or "") + " " +
                str(gap.get("critical_unknown") or "")
            ).casefold()
            generic_gap_terms = {
                "event", "events", "material", "mechanism", "evidence", "independent",
                "underlying", "support", "supports", "supporting", "candidate", "strategic",
                "observed", "action", "actions", "outcome", "outcomes", "company", "companies",
                "another", "distinct", "factual", "proposed", "leading", "explanation",
            }
            gap_terms = {
                token for token in re.findall(r"[a-z0-9][a-z0-9_-]{3,}", gap_blob)
                if token not in generic_gap_terms
            }
            text_terms = set(re.findall(r"[a-z0-9][a-z0-9_-]{3,}", text))
            gap_keyword_overlap = sorted(gap_terms & text_terms)
            distinctive_gap_match = len(gap_keyword_overlap) >= 2
            relation_match = bool(
                shared_mechanisms
                or shared_dependencies
                or contradiction_signal
                or distinctive_gap_match
            )
            gap_relevant = bool(
                relation_match
                and (
                    shared_mechanisms
                    or shared_dependencies
                    or contradiction_signal
                    or class_match
                    or new_actor
                )
            )
            admit = bool(new_underlying and gap_relevant)
            if admit:
                admitted_event_ids.append(event_id)
            rows.append({
                "event_id": event_id,
                "underlying_event_id": underlying_id,
                "new_underlying_event": new_underlying,
                "event_class": event_class,
                "actor": actor,
                "new_actor": new_actor,
                "shared_mechanism_tags": shared_mechanisms,
                "shared_dependency_tags": shared_dependencies,
                "event_class_matches_gap": class_match,
                "contradiction_signal": contradiction_signal,
                "gap_keyword_overlap": gap_keyword_overlap[:12],
                "distinctive_gap_match": distinctive_gap_match,
                "gap_relevant": gap_relevant,
                "admitted_for_rescue": admit,
                "rejection_reason": "" if admit else (
                    "duplicate_underlying_event" if not new_underlying else "does_not_close_defined_evidence_gap"
                ),
            })

        return {
            "admitted_event_ids": admitted_event_ids,
            "admitted_event_count": len(admitted_event_ids),
            "new_independent_underlying_event_count": sum(
                1 for row in rows if row.get("admitted_for_rescue") and row.get("new_underlying_event")
            ),
            "event_assessments": rows,
            "underlying_event_diagnostics": underlying_diag,
            "rule": "new_event_must_be_independent_and_close_a_defined_hypothesis_gap",
        }

    @classmethod
    def _strategic_corpus_sufficiency_assessment(
        cls,
        *,
        topic: str,
        events: Sequence[Mapping[str, Any]],
        evidence: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Decide whether Deep mode has enough *structure* to start inference.

        This is intentionally not an article-count gate. A corpus is researchable
        when it contains several distinct underlying events from multiple sources
        and at least one coherent multi-event cluster, or one unusually rich
        focused cluster. This prevents a broad topic from reaching inference with
        one random launch announcement merely because extraction technically ran.
        """
        clean_events = [dict(item) for item in events if isinstance(item, Mapping)]
        clean_evidence = [dict(item) for item in evidence if isinstance(item, Mapping)]
        event_ids = _unique_strings(
            _clean(item.get("event_id") or item.get("id")).upper()
            for item in clean_events
        )
        underlying_ids = _unique_strings(
            _clean(item.get("underlying_event_id") or item.get("event_id") or item.get("id")).upper()
            for item in clean_events
        )
        source_ids = _unique_strings(
            _clean(item.get("source_id")).upper() for item in clean_events
        )
        cluster_ids = _unique_strings(
            _clean(item.get("strategic_cluster_id"))
            for item in clean_events
            if _clean(item.get("strategic_cluster_id")) not in {"", "excluded", "unclustered"}
        )
        event_class_distribution: dict[str, int] = {}
        for item in clean_events:
            family = _enum(item.get("strategic_event_class") or cls._coarse_event_family(item)) or "other"
            event_class_distribution[family] = event_class_distribution.get(family, 0) + 1

        profiles = cls._strategic_cluster_expansion_profiles(
            topic=topic,
            events=clean_events,
            evidence=clean_evidence,
        )
        coherent_profiles = [
            item for item in profiles
            if len(item.get("underlying_event_ids") or []) >= DEEP_CORPUS_MIN_COHERENT_CLUSTER_EVENTS
        ]
        best = dict(profiles[0]) if profiles else {}
        best_underlying = len(best.get("underlying_event_ids") or [])
        best_sources = len(best.get("source_ids") or [])
        best_score = float(best.get("score") or 0.0)

        broad_corpus_ready = bool(
            len(event_ids) >= DEEP_CORPUS_MIN_STRATEGIC_EVENTS
            and len(underlying_ids) >= DEEP_CORPUS_MIN_UNDERLYING_EVENTS
            and len(source_ids) >= DEEP_CORPUS_MIN_SOURCE_COUNT
            and coherent_profiles
        )
        # A focused topic may legitimately have one deep cluster rather than two
        # broad clusters. Do not force artificial breadth when one cluster already
        # contains a real multi-event, multi-source sequence.
        focused_cluster_ready = bool(
            best_underlying >= 3
            and best_sources >= 2
            and best_score >= DEEP_CLUSTER_EXPANSION_MIN_RESEARCH_SCORE
        )
        sufficient = bool(broad_corpus_ready or focused_cluster_ready)

        reasons: list[str] = []
        if len(event_ids) < DEEP_CORPUS_MIN_STRATEGIC_EVENTS:
            reasons.append("too_few_strategic_events")
        if len(underlying_ids) < DEEP_CORPUS_MIN_UNDERLYING_EVENTS:
            reasons.append("too_few_distinct_underlying_events")
        if len(source_ids) < DEEP_CORPUS_MIN_SOURCE_COUNT:
            reasons.append("insufficient_source_diversity")
        if not coherent_profiles:
            reasons.append("no_coherent_multi_event_cluster")

        return {
            "sufficient": sufficient,
            "broad_corpus_ready": broad_corpus_ready,
            "focused_cluster_ready": focused_cluster_ready,
            "strategic_event_count": len(event_ids),
            "distinct_underlying_event_count": len(underlying_ids),
            "source_count": len(source_ids),
            "strategic_cluster_count": len(cluster_ids),
            "coherent_multi_event_cluster_count": len(coherent_profiles),
            "best_cluster_id": _clean(best.get("cluster_id")),
            "best_cluster_score": round(best_score, 2),
            "best_cluster_underlying_event_count": best_underlying,
            "best_cluster_source_count": best_sources,
            "event_class_distribution": event_class_distribution,
            "reasons": reasons,
            "thresholds": {
                "minimum_strategic_events": DEEP_CORPUS_MIN_STRATEGIC_EVENTS,
                "minimum_distinct_underlying_events": DEEP_CORPUS_MIN_UNDERLYING_EVENTS,
                "minimum_source_count": DEEP_CORPUS_MIN_SOURCE_COUNT,
                "minimum_coherent_cluster_events": DEEP_CORPUS_MIN_COHERENT_CLUSTER_EVENTS,
                "focused_cluster_min_underlying_events": 3,
                "focused_cluster_min_sources": 2,
                "focused_cluster_min_score": DEEP_CLUSTER_EXPANSION_MIN_RESEARCH_SCORE,
            },
            "cluster_profiles": profiles[:12],
            "rule": (
                "deep_inference_requires_a_minimally_researchable_event_corpus_or_one_"
                "rich_multi_source_cluster_before_strategic_inference"
            ),
        }

    @classmethod
    def _strategic_corpus_refill_queries(
        cls,
        *,
        topic: str,
        assessment: Mapping[str, Any],
        events: Sequence[Mapping[str, Any]],
        round_number: int,
    ) -> list[str]:
        """Generate broad-but-structured development searches for a weak corpus.

        This is topic-agnostic. It does not hard-code AI subdomains; it searches
        universal strategic development lanes that matter for companies, markets,
        regulation and operations, then lets strategic admission decide what is
        genuinely relevant to the user's topic.
        """
        topic = _clean(topic)
        if not topic:
            return []
        now = datetime.now(timezone.utc)
        month_year = now.strftime("%B %Y")
        year = now.year
        distribution = {
            _enum(k): int(v or 0)
            for k, v in _as_mapping(assessment.get("event_class_distribution")).items()
        }
        queries: list[str] = []

        # Always start with a high-signal current-development query rather than a
        # generic topic explainer.
        queries.append(
            f"{topic} {month_year} major new developments companies regulators customers contracts decisions"
        )

        if not (distribution.get("regulation") or distribution.get("litigation")):
            queries.append(
                f"{topic} {year} regulation enforcement approval restriction government decision litigation recent"
            )
        if not (distribution.get("financing") or distribution.get("m_and_a")):
            queries.append(
                f"{topic} {year} funding financing investment acquisition merger capital commitment recent"
            )
        if not (distribution.get("partnership") or distribution.get("operations")):
            queries.append(
                f"{topic} {year} customer contract procurement production deployment operating results recent"
            )
        if not distribution.get("infrastructure"):
            queries.append(
                f"{topic} {year} infrastructure capacity supply chain manufacturing facilities expansion constraint recent"
            )

        # Product-launch-heavy corpora need outcomes, not more launch copy.
        if distribution.get("product_launch", 0) >= max(1, sum(distribution.values()) // 3):
            queries.append(
                f"{topic} {year} customer adoption commercial deployment usage contracts outcomes after launch"
            )

        if round_number >= 2:
            queries.extend([
                f"{topic} {year} delays cancellations failures constraints shortages disputes recent developments",
                f"{topic} {year} competitors comparable companies customer commitments capacity expansion recent",
                f"{topic} {year} demand pricing revenue utilization operating performance customers recent",
            ])

        # If a high-value non-launch event already exists, one actor-specific
        # follow-up can help form a sequence. Do not chase a lone marketing launch.
        high_value_families = {
            "regulation", "litigation", "financing", "m_and_a",
            "partnership", "infrastructure", "operations",
        }
        ranked = sorted(
            [dict(item) for item in events if isinstance(item, Mapping)],
            key=lambda item: -float(item.get("strategic_materiality_score") or 0.0),
        )
        for item in ranked[:6]:
            family = _enum(item.get("strategic_event_class") or cls._coarse_event_family(item))
            actor = cls._event_primary_institution(item)
            materiality = float(item.get("strategic_materiality_score") or 0.0)
            if actor and family in high_value_families and materiality >= 3.0:
                queries.append(
                    f"{topic} {actor} {year} follow-up contracts capacity financing regulation outcomes recent"
                )
                break

        return _unique_strings(queries)[:DEEP_CORPUS_REFILL_MAX_QUERIES_PER_ROUND]

    @staticmethod
    def _strategic_corpus_refill_summary_payload(result: Mapping[str, Any]) -> dict[str, Any]:
        def compact_assessment(value: Any) -> dict[str, Any]:
            raw = _as_mapping(value)
            return {
                "sufficient": bool(raw.get("sufficient")),
                "broad_corpus_ready": bool(raw.get("broad_corpus_ready")),
                "focused_cluster_ready": bool(raw.get("focused_cluster_ready")),
                "strategic_event_count": raw.get("strategic_event_count"),
                "distinct_underlying_event_count": raw.get("distinct_underlying_event_count"),
                "source_count": raw.get("source_count"),
                "strategic_cluster_count": raw.get("strategic_cluster_count"),
                "coherent_multi_event_cluster_count": raw.get("coherent_multi_event_cluster_count"),
                "best_cluster_id": _clean(raw.get("best_cluster_id")),
                "best_cluster_score": raw.get("best_cluster_score"),
                "best_cluster_underlying_event_count": raw.get("best_cluster_underlying_event_count"),
                "best_cluster_source_count": raw.get("best_cluster_source_count"),
                "event_class_distribution": _json_safe(raw.get("event_class_distribution") or {}),
                "reasons": list(raw.get("reasons") or [])[:12],
            }

        initial = compact_assessment(result.get("initial_assessment"))
        final = compact_assessment(result.get("final_assessment"))
        compact_rounds = []
        for raw in (result.get("rounds") or [])[:DEEP_CORPUS_REFILL_MAX_ROUNDS]:
            if not isinstance(raw, Mapping):
                continue
            compact_rounds.append({
                "round": raw.get("round"),
                "status": _clean(raw.get("status")),
                "queries": list(raw.get("queries") or [])[:DEEP_CORPUS_REFILL_MAX_QUERIES_PER_ROUND],
                "new_source_ids": list(raw.get("new_source_ids") or [])[:8],
                "new_raw_event_count": raw.get("new_raw_event_count"),
                "new_strategically_eligible_event_count": raw.get("new_strategically_eligible_event_count"),
                "new_distinct_underlying_event_count": raw.get("new_distinct_underlying_event_count"),
                "admitted_event_ids": list(raw.get("admitted_event_ids") or [])[:30],
                "assessment_after": compact_assessment(raw.get("assessment_after")),
                "search_failure_count": len(raw.get("search_failures") or []),
                "extraction_failure_count": len(raw.get("extraction_failures") or []),
            })
        return {
            "attempted": bool(result.get("attempted")),
            "initial_assessment": _json_safe(initial),
            "rounds": compact_rounds,
            "new_material_event_count": int(result.get("new_material_event_count") or 0),
            "final_source_count": result.get("final_source_count"),
            "final_event_count": result.get("final_event_count"),
            "final_evidence_count": result.get("final_evidence_count"),
            "final_assessment": _json_safe(final),
            "stop_reason": _clean(result.get("stop_reason")),
            "invariant": _clean(result.get("invariant")),
        }

    def _refill_strategic_corpus(
        self,
        *,
        topic: str,
        selected_source_ids: Sequence[str],
        event_records: Sequence[Mapping[str, Any]],
        evidence_records: Sequence[Mapping[str, Any]],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """Mandatory Deep corpus refill before cluster expansion/inference.

        If initial retrieval leaves too little coherent strategic structure, Deep
        mode searches again across missing development lanes. The refill admits
        only strategically eligible *new underlying events*. It does not lower
        inference standards and it does not manufacture relationships between
        unrelated stories.
        """
        current_sources = list(_unique_strings(selected_source_ids))
        current_events = [dict(item) for item in event_records if isinstance(item, Mapping)]
        current_evidence = [dict(item) for item in evidence_records if isinstance(item, Mapping)]
        initial_assessment = self._strategic_corpus_sufficiency_assessment(
            topic=topic, events=current_events, evidence=current_evidence
        )
        result: dict[str, Any] = {
            "attempted": False,
            "selected_source_ids": current_sources,
            "event_records": current_events,
            "evidence_records": current_evidence,
            "initial_assessment": initial_assessment,
            "rounds": [],
            "new_material_event_count": 0,
        }
        state["strategic_corpus_quality_initial"] = _json_safe(initial_assessment)
        logger.info(
            "strategic_corpus_quality_initial=%s",
            json.dumps(_json_safe(initial_assessment), ensure_ascii=False, default=str),
        )
        if initial_assessment.get("sufficient"):
            result.update({
                "final_assessment": initial_assessment,
                "final_source_count": len(current_sources),
                "final_event_count": len(current_events),
                "final_evidence_count": len(current_evidence),
                "stop_reason": "initial_strategic_corpus_sufficient",
                "invariant": "do_not_refill_an_already_researchable_strategic_corpus",
            })
            state["strategic_corpus_refill"] = _json_safe(
                self._strategic_corpus_refill_summary_payload(result)
            )
            logger.info(
                "strategic_corpus_refill_summary=%s",
                json.dumps(_json_safe(state["strategic_corpus_refill"]), ensure_ascii=False, default=str),
            )
            return result

        result["attempted"] = True
        logger.info(
            "strategic_corpus_refill_plan=%s",
            json.dumps(_json_safe({
                "trigger_reasons": initial_assessment.get("reasons") or [],
                "initial_assessment": initial_assessment,
                "max_rounds": DEEP_CORPUS_REFILL_MAX_ROUNDS,
                "max_queries_per_round": DEEP_CORPUS_REFILL_MAX_QUERIES_PER_ROUND,
                "max_sources_per_round": DEEP_CORPUS_REFILL_MAX_SOURCES_PER_ROUND,
                "goal": (
                    "assemble_a_minimally_researchable_multi_event_multi_source_corpus_"
                    "before_cluster_expansion_and_strategic_inference"
                ),
            }), ensure_ascii=False, default=str),
        )

        for round_number in range(1, DEEP_CORPUS_REFILL_MAX_ROUNDS + 1):
            before = self._strategic_corpus_sufficiency_assessment(
                topic=topic, events=current_events, evidence=current_evidence
            )
            queries = self._strategic_corpus_refill_queries(
                topic=topic,
                assessment=before,
                events=current_events,
                round_number=round_number,
            )
            trace: dict[str, Any] = {
                "round": round_number,
                "assessment_before": before,
                "queries": queries,
            }
            if not queries:
                trace["status"] = "no_refill_queries_generated"
                result["rounds"].append(trace)
                logger.info(
                    "strategic_corpus_refill_round=%s",
                    json.dumps(_json_safe(trace), ensure_ascii=False, default=str),
                )
                continue

            search_state: dict[str, Any] = {
                "run_id": state.get("run_id"), "topic": topic,
                "requested_research_depth": "deep", "research_depth": "deep",
                "warnings": [], "errors": [],
                "depth_integrity": {
                    "requested": "deep", "resolved_profile": "deep",
                    "extraction": None, "strategic": None, "ok": False,
                },
            }
            discovered = self._discover_sources_with_run_circuit_breaker(
                topic=topic,
                max_sources=DEEP_CORPUS_REFILL_MAX_SOURCES_PER_ROUND,
                parent_state=state,
                discovery_state=search_state,
                discovery_queries_override=queries,
                discovery_phase=f"strategic_corpus_refill_{round_number}",
            )
            new_source_ids = [
                value for value in discovered if value and value not in current_sources
            ]
            trace["search_selected_source_ids"] = list(discovered)
            trace["new_source_ids"] = list(new_source_ids)
            trace["search_failures"] = list(
                search_state.get("source_discovery_search_failures")
                or search_state.get("source_discovery_failures")
                or []
            )
            if not new_source_ids:
                circuit_open = self._fresh_discovery_circuit_is_open(state)
                trace["fresh_discovery_circuit_open"] = circuit_open
                trace["status"] = (
                    "refill_stopped_provider_circuit_open"
                    if circuit_open
                    else "refill_no_new_sources"
                )
                trace["assessment_after"] = before
                result["rounds"].append(trace)
                logger.info(
                    "strategic_corpus_refill_round=%s",
                    json.dumps(_json_safe(trace), ensure_ascii=False, default=str),
                )
                if circuit_open:
                    break
                continue

            extraction_state = {
                "run_id": state.get("run_id"), "topic": topic,
                "requested_research_depth": "deep", "research_depth": "deep",
                "warnings": [], "errors": [],
                "depth_integrity": {
                    "requested": "deep", "resolved_profile": "deep",
                    "extraction": None, "strategic": None, "ok": False,
                },
            }
            new_raw_events = self._extract_events(
                source_ids=new_source_ids, state=extraction_state
            )
            trace["new_raw_event_count"] = len(new_raw_events)
            trace["extraction_failures"] = list(
                extraction_state.get("extraction_failures") or []
            )
            if not new_raw_events:
                trace["status"] = "refill_sources_produced_no_events"
                trace["assessment_after"] = before
                result["rounds"].append(trace)
                logger.info(
                    "strategic_corpus_refill_round=%s",
                    json.dumps(_json_safe(trace), ensure_ascii=False, default=str),
                )
                continue

            new_source_payloads = self._source_payloads(new_source_ids)
            new_evidence_payload = _as_mapping(
                self.evidence_builder_agent.build_evidence(
                    topic=topic,
                    events=new_raw_events,
                    sources=new_source_payloads,
                    semantic_fact_extraction=False,
                    semantic_fail_closed=False,
                )
            )
            new_raw_evidence = [
                dict(item) for item in (new_evidence_payload.get("evidence_records") or [])
                if isinstance(item, Mapping) and _clean(item.get("evidence_id") or item.get("id"))
            ]
            self._validate_evidence_currency(new_raw_evidence)
            trace["new_raw_evidence_count"] = len(new_raw_evidence)
            if not new_raw_evidence:
                trace["status"] = "refill_events_produced_no_evidence"
                trace["assessment_after"] = before
                result["rounds"].append(trace)
                logger.info(
                    "strategic_corpus_refill_round=%s",
                    json.dumps(_json_safe(trace), ensure_ascii=False, default=str),
                )
                continue

            old_underlying_ids = {
                _clean(item.get("underlying_event_id") or item.get("event_id") or item.get("id")).upper()
                for item in current_events
                if isinstance(item, Mapping)
                and _clean(item.get("underlying_event_id") or item.get("event_id") or item.get("id"))
            }
            combined_events = current_events + [dict(item) for item in new_raw_events]
            combined_evidence = current_evidence + new_raw_evidence
            combined_events, underlying_diag = self._annotate_underlying_events(
                events=combined_events, topic=topic
            )
            prepared_events, prepared_evidence, admission = self._prepare_strategic_event_context(
                topic=topic, events=combined_events, evidence=combined_evidence
            )
            trace["strategic_event_admission"] = _json_safe({
                "considered_event_count": admission.get("considered_event_count"),
                "excluded_event_count": admission.get("excluded_event_count"),
                "considered_evidence_count": admission.get("considered_evidence_count"),
                "cluster_distribution": admission.get("cluster_distribution"),
                "topic_linkage_distribution": admission.get("topic_linkage_distribution"),
                "exclusion_reasons": admission.get("exclusion_reasons"),
            })
            trace["underlying_event_diagnostics"] = _json_safe(underlying_diag)

            new_raw_ids = {
                _clean(item.get("event_id") or item.get("id")).upper()
                for item in new_raw_events
                if isinstance(item, Mapping) and _clean(item.get("event_id") or item.get("id"))
            }
            new_eligible_events = [
                dict(item) for item in prepared_events
                if _clean(item.get("event_id") or item.get("id")).upper() in new_raw_ids
            ]
            trace["new_strategically_eligible_event_count"] = len(new_eligible_events)

            admitted_new_events: list[dict[str, Any]] = []
            admitted_underlying_ids: set[str] = set()
            rejected_duplicate_count = 0
            for item in new_eligible_events:
                underlying_id = _clean(
                    item.get("underlying_event_id") or item.get("event_id") or item.get("id")
                ).upper()
                if not underlying_id:
                    continue
                if underlying_id in old_underlying_ids or underlying_id in admitted_underlying_ids:
                    rejected_duplicate_count += 1
                    continue
                admitted_underlying_ids.add(underlying_id)
                admitted_new_events.append(item)

            admitted_ids = {
                _clean(item.get("event_id") or item.get("id")).upper()
                for item in admitted_new_events
                if _clean(item.get("event_id") or item.get("id"))
            }
            existing_ids = {
                _clean(item.get("event_id") or item.get("id")).upper()
                for item in current_events
                if isinstance(item, Mapping) and _clean(item.get("event_id") or item.get("id"))
            }
            keep_ids = existing_ids | admitted_ids
            current_events = [
                dict(item) for item in prepared_events
                if _clean(item.get("event_id") or item.get("id")).upper() in keep_ids
            ]
            current_evidence = [
                dict(item) for item in prepared_evidence
                if _clean(item.get("event_id")).upper() in keep_ids
            ]
            admitted_source_ids = _unique_strings(
                _clean(item.get("source_id")) for item in admitted_new_events
            )
            current_sources = _unique_strings(current_sources + admitted_source_ids)
            added_count = len(admitted_ids)
            result["new_material_event_count"] += added_count
            trace["admitted_event_ids"] = sorted(admitted_ids)
            trace["new_distinct_underlying_event_count"] = len(admitted_underlying_ids)
            trace["duplicate_underlying_event_rejection_count"] = rejected_duplicate_count
            trace["new_material_event_count"] = added_count

            after = self._strategic_corpus_sufficiency_assessment(
                topic=topic, events=current_events, evidence=current_evidence
            )
            trace["assessment_after"] = after
            trace["status"] = (
                "corpus_sufficient_after_refill"
                if after.get("sufficient")
                else "new_material_events_added_corpus_still_below_target"
                if added_count
                else "no_new_distinct_strategic_events"
            )
            result["rounds"].append(trace)
            logger.info(
                "strategic_corpus_refill_round=%s",
                json.dumps(_json_safe(trace), ensure_ascii=False, default=str),
            )
            if after.get("sufficient"):
                break

        final_assessment = self._strategic_corpus_sufficiency_assessment(
            topic=topic, events=current_events, evidence=current_evidence
        )
        result.update({
            "selected_source_ids": current_sources,
            "event_records": current_events,
            "evidence_records": current_evidence,
            "final_source_count": len(current_sources),
            "final_event_count": len(current_events),
            "final_evidence_count": len(current_evidence),
            "final_assessment": final_assessment,
            "stop_reason": (
                "strategic_corpus_sufficient_after_refill"
                if final_assessment.get("sufficient")
                else "fresh_discovery_provider_unavailable_corpus_below_target"
                if self._fresh_discovery_circuit_is_open(state)
                else "bounded_corpus_refill_exhausted_below_target"
            ),
            "invariant": (
                "a_weak_initial_discovery_pass_triggers_additional_event_search_before_"
                "cluster_expansion_and_strategic_inference"
            ),
        })
        state["strategic_corpus_quality_final"] = _json_safe(final_assessment)
        state["strategic_corpus_refill"] = _json_safe(
            self._strategic_corpus_refill_summary_payload(result)
        )
        logger.info(
            "strategic_corpus_refill_summary=%s",
            json.dumps(_json_safe(state["strategic_corpus_refill"]), ensure_ascii=False, default=str),
        )
        logger.info(
            "strategic_corpus_quality_final=%s",
            json.dumps(_json_safe(final_assessment), ensure_ascii=False, default=str),
        )
        if not final_assessment.get("sufficient"):
            state.setdefault("warnings", []).append({
                "type": "deep_strategic_corpus_below_target_after_refill",
                "message": (
                    "Deep mode spent the bounded corpus-refill budget but the strategic "
                    "corpus remains below the preferred research target. Cluster expansion "
                    "and candidate-specific rescue may still add structure; inference "
                    "standards are not lowered."
                ),
                "diagnostics": _json_safe(final_assessment),
            })
        return result

    @staticmethod
    def _cluster_expansion_time_key(event: Mapping[str, Any]) -> str:
        """Return a bounded temporal key for cluster-sequence coverage diagnostics."""
        for key in (
            "strategic_time_window", "event_time", "event_time_text", "date",
            "published_at", "publication_date", "timestamp",
        ):
            value = _clean(event.get(key))
            if not value:
                continue
            match = re.search(r"\b(20\d{2})[-/](0?[1-9]|1[0-2])(?:[-/](0?[1-9]|[12]\d|3[01]))?\b", value)
            if match:
                month = int(match.group(2))
                day = int(match.group(3)) if match.group(3) else 0
                return f"{match.group(1)}-{month:02d}" + (f"-{day:02d}" if day else "")
            match = re.search(r"\b(20\d{2})\b", value)
            if match:
                return match.group(1)
        return ""

    @classmethod
    def _strategic_cluster_expansion_profiles(
        cls,
        *,
        topic: str,
        events: Sequence[Mapping[str, Any]],
        evidence: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Rank strategically coherent clusters for pre-inference deep research.

        The trigger is intentionally EVENT-based rather than EVID-count based.
        Multiple EVID rows can describe one underlying announcement, so a normal
        expansion seed requires >=2 distinct underlying events. A single event is
        eligible only when it is unusually material and belongs to a class where
        follow-up structure is inherently decision-relevant (regulation, financing,
        infrastructure/capacity, commercial commitment or operating outcome).
        """
        evidence_by_event: dict[str, list[Mapping[str, Any]]] = {}
        for item in evidence:
            if not isinstance(item, Mapping):
                continue
            event_id = _clean(item.get("event_id")).upper()
            if event_id:
                evidence_by_event.setdefault(event_id, []).append(item)

        grouped: dict[str, list[dict[str, Any]]] = {}
        for raw in events:
            if not isinstance(raw, Mapping):
                continue
            event = dict(raw)
            cluster_id = _clean(event.get("strategic_cluster_id"))
            if not cluster_id or cluster_id in {"excluded", "unclustered"}:
                continue
            grouped.setdefault(cluster_id, []).append(event)

        profiles: list[dict[str, Any]] = []
        high_value_single_classes = {
            "policy_or_regulation", "legal_action", "financing",
            "capacity_or_infrastructure", "commercial_contract", "operating_metric",
        }
        for cluster_id, cluster_events in grouped.items():
            event_ids = _unique_strings(
                _clean(item.get("event_id") or item.get("id")).upper()
                for item in cluster_events
            )
            underlying_ids = _unique_strings(
                _clean(item.get("underlying_event_id") or item.get("event_id") or item.get("id")).upper()
                for item in cluster_events
            )
            source_ids = _unique_strings(
                _clean(item.get("source_id")).upper() for item in cluster_events
            )
            actors = _unique_strings(
                cls._event_primary_institution(item) for item in cluster_events
            )
            event_classes = _unique_strings(
                _clean(item.get("strategic_event_class") or cls._coarse_event_family(item))
                for item in cluster_events
            )
            mechanism_tags = _unique_strings(
                tag
                for item in cluster_events
                for tag in (item.get("strategic_mechanism_tags") or [])
            )
            dependency_tags = _unique_strings(
                tag
                for item in cluster_events
                for tag in (item.get("strategic_dependency_tags") or [])
            )
            time_keys = _unique_strings(
                cls._cluster_expansion_time_key(item) for item in cluster_events
            )
            linkage_types = _unique_strings(
                _clean(item.get("strategic_topic_linkage_type")) for item in cluster_events
            )
            materialities = [
                float(item.get("strategic_materiality_score") or 0.0)
                for item in cluster_events
                if not isinstance(item.get("strategic_materiality_score"), bool)
            ]
            max_materiality = max(materialities or [0.0])
            avg_materiality = sum(materialities) / max(1, len(materialities))
            quantitative_event_count = 0
            for item in cluster_events:
                event_id = _clean(item.get("event_id")).upper()
                if item.get("quantitative_claims") or item.get("quantities"):
                    quantitative_event_count += 1
                    continue
                if any(
                    _enum(ev.get("evidence_type")) == "quantitative_claim"
                    for ev in evidence_by_event.get(event_id, [])
                    if isinstance(ev, Mapping)
                ):
                    quantitative_event_count += 1

            score = 0.0
            score += min(30.0, 12.0 * len(underlying_ids))
            score += min(15.0, 7.5 * len(source_ids))
            score += min(10.0, 4.0 * len(actors))
            score += min(14.0, 3.5 * (len(mechanism_tags) + len(dependency_tags)))
            score += min(8.0, 4.0 * len(event_classes))
            score += min(5.0, 2.5 * len(time_keys))
            score += min(5.0, 2.5 * quantitative_event_count)
            score += min(8.0, max(0.0, avg_materiality) * 2.0)
            if "direct" in linkage_types:
                score += 5.0

            multi_event_seed = len(underlying_ids) >= 2
            single_high_materiality_seed = bool(
                len(underlying_ids) == 1
                and max_materiality >= 3.8
                and any(value in high_value_single_classes for value in event_classes)
                and (mechanism_tags or dependency_tags)
            )
            seed_eligible = bool(multi_event_seed or single_high_materiality_seed)

            missing_dimensions: list[str] = []
            if len(source_ids) < 2:
                missing_dimensions.append("source_independence")
            if len(time_keys) < 2:
                missing_dimensions.append("temporal_progression")
            if not mechanism_tags and not dependency_tags:
                missing_dimensions.append("mechanism_or_dependency")
            if quantitative_event_count == 0:
                missing_dimensions.append("magnitude_or_capacity")
            if len(actors) < 2:
                missing_dimensions.append("counterparty_or_comparable_actor")
            if (
                "customer_commitment" not in mechanism_tags
                and "customer_demand" not in dependency_tags
                and not any(value in {"commercial_contract", "operating_metric"} for value in event_classes)
            ):
                missing_dimensions.append("customer_or_real_world_outcome")
            missing_dimensions.append("constraints_or_counter_developments")

            profiles.append({
                "cluster_id": cluster_id,
                "score": round(score, 2),
                "eligible": bool(seed_eligible and score >= DEEP_CLUSTER_EXPANSION_MIN_RESEARCH_SCORE),
                "seed_basis": (
                    "multiple_distinct_underlying_events"
                    if multi_event_seed
                    else "single_high_materiality_event"
                    if single_high_materiality_seed
                    else "insufficient_event_structure"
                ),
                "event_ids": event_ids,
                "underlying_event_ids": underlying_ids,
                "source_ids": source_ids,
                "actors": actors,
                "event_classes": event_classes,
                "mechanism_tags": mechanism_tags,
                "dependency_tags": dependency_tags,
                "time_keys": time_keys,
                "topic_linkage_types": linkage_types,
                "quantitative_event_count": quantitative_event_count,
                "max_materiality": round(max_materiality, 3),
                "average_materiality": round(avg_materiality, 3),
                "missing_dimensions": _unique_strings(missing_dimensions),
                "research_goal": (
                    "expand_the_cluster_until_bounded_search_stops_adding_distinct_"
                    "underlying_events_or_new_strategic_dimensions"
                ),
            })

        profiles.sort(key=lambda item: (-float(item.get("score") or 0.0), _clean(item.get("cluster_id"))))
        return profiles

    @staticmethod
    def _cluster_expansion_queries(
        *,
        topic: str,
        profile: Mapping[str, Any],
        round_number: int,
    ) -> list[str]:
        """Generate multi-angle cluster research queries from event structure."""
        topic = _clean(topic)
        year = datetime.now(timezone.utc).year
        actors = [_clean(value) for value in (profile.get("actors") or []) if _clean(value)]
        mechanisms = [_clean(value).replace("_", " ") for value in (profile.get("mechanism_tags") or []) if _clean(value)]
        dependencies = [_clean(value).replace("_", " ") for value in (profile.get("dependency_tags") or []) if _clean(value)]
        cluster_phrase = _clean(profile.get("cluster_id")).replace("_", " ")
        actor_phrase = " ".join(actors[:2])
        structural_phrase = " ".join(_unique_strings(mechanisms[:2] + dependencies[:2]))
        focus = " ".join(value for value in (actor_phrase, structural_phrase) if value).strip()
        if not focus:
            focus = cluster_phrase or topic

        missing = {_enum(value) for value in (profile.get("missing_dimensions") or [])}
        queries: list[str] = []
        queries.append(
            f"{topic} {focus} {year} latest developments contracts deployment milestones outcomes"
        )
        if round_number == 1:
            if "magnitude_or_capacity" in missing or "customer_or_real_world_outcome" in missing:
                queries.append(
                    f"{focus} {year} customer contracts capacity financing deployment production commitments"
                )
            else:
                queries.append(
                    f"{focus} {year} implementation customers results capacity commitments"
                )
            queries.append(
                f"{focus} {year} delays cancellations restrictions regulation constraints competing developments"
            )
        else:
            # The second round is deliberately different: test outcomes,
            # counter-signals and comparable actors rather than asking the same
            # confirmation question again.
            queries.append(
                f"{focus} {year} actual outcomes customer adoption utilization delays constraints"
            )
            queries.append(
                f"{topic} {cluster_phrase} {year} competitors comparable companies contracts capacity regulation"
            )
        return _unique_strings(queries)[:DEEP_CLUSTER_EXPANSION_MAX_QUERIES_PER_ROUND]

    @classmethod
    def _cluster_expansion_material_information_assessment(
        cls,
        *,
        target_profile: Mapping[str, Any],
        existing_events: Sequence[Mapping[str, Any]],
        new_events: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Admit only distinct events that actually deepen the target cluster."""
        target_cluster_id = _clean(target_profile.get("cluster_id"))
        target_actors = {
            cls._canonical_actor_key(value)
            for value in (target_profile.get("actors") or [])
            if cls._canonical_actor_key(value)
        }
        target_mechanisms = {_enum(value) for value in (target_profile.get("mechanism_tags") or []) if _enum(value)}
        target_dependencies = {_enum(value) for value in (target_profile.get("dependency_tags") or []) if _enum(value)}
        existing_underlying = {
            _clean(item.get("underlying_event_id") or item.get("event_id") or item.get("id")).upper()
            for item in existing_events
            if isinstance(item, Mapping)
            and _clean(item.get("underlying_event_id") or item.get("event_id") or item.get("id"))
        }
        existing_event_ids = {
            _clean(item.get("event_id") or item.get("id")).upper()
            for item in existing_events
            if isinstance(item, Mapping) and _clean(item.get("event_id") or item.get("id"))
        }
        existing_classes = {
            _enum(item.get("strategic_event_class"))
            for item in existing_events if isinstance(item, Mapping) and _enum(item.get("strategic_event_class"))
        }
        existing_time_keys = {
            cls._cluster_expansion_time_key(item)
            for item in existing_events if isinstance(item, Mapping) and cls._cluster_expansion_time_key(item)
        }

        admitted: list[str] = []
        rejected: list[dict[str, Any]] = []
        additions: dict[str, list[str]] = {}
        for raw in new_events:
            if not isinstance(raw, Mapping):
                continue
            event = dict(raw)
            event_id = _clean(event.get("event_id") or event.get("id")).upper()
            if not event_id or event_id in existing_event_ids:
                continue
            underlying_id = _clean(
                event.get("underlying_event_id") or event.get("event_id") or event.get("id")
            ).upper()
            if underlying_id in existing_underlying:
                rejected.append({"event_id": event_id, "reason": "duplicate_underlying_event"})
                continue

            event_cluster = _clean(event.get("strategic_cluster_id"))
            event_mechanisms = {_enum(value) for value in (event.get("strategic_mechanism_tags") or []) if _enum(value)}
            event_dependencies = {_enum(value) for value in (event.get("strategic_dependency_tags") or []) if _enum(value)}
            actor = cls._canonical_actor_key(cls._event_primary_institution(event))
            shared_mechanisms = sorted(target_mechanisms & event_mechanisms)
            shared_dependencies = sorted(target_dependencies & event_dependencies)
            same_cluster = bool(target_cluster_id and event_cluster == target_cluster_id)
            structurally_adjacent = bool(shared_mechanisms or shared_dependencies)
            if not same_cluster and not structurally_adjacent:
                rejected.append({
                    "event_id": event_id,
                    "reason": "not_target_cluster_or_structurally_adjacent",
                    "event_cluster_id": event_cluster,
                })
                continue

            dimensions = ["new_underlying_event"]
            if actor and actor not in target_actors:
                dimensions.append("new_actor")
            event_class = _enum(event.get("strategic_event_class"))
            if event_class and event_class not in existing_classes:
                dimensions.append("new_event_class")
            new_mechanisms = sorted(event_mechanisms - target_mechanisms)
            new_dependencies = sorted(event_dependencies - target_dependencies)
            if new_mechanisms:
                dimensions.append("new_mechanism")
            if new_dependencies:
                dimensions.append("new_dependency")
            time_key = cls._cluster_expansion_time_key(event)
            if time_key and time_key not in existing_time_keys:
                dimensions.append("new_time_point")
            if event.get("quantitative_claims") or event.get("quantities"):
                dimensions.append("new_magnitude")
            if shared_mechanisms:
                dimensions.append("shared_mechanism_confirmation")
            if shared_dependencies:
                dimensions.append("shared_dependency_confirmation")

            admitted.append(event_id)
            additions[event_id] = _unique_strings(dimensions)

        reason_counts: dict[str, int] = {}
        for item in rejected:
            reason = _clean(item.get("reason")) or "unknown"
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        return {
            "target_cluster_id": target_cluster_id,
            "admitted_event_ids": admitted,
            "admitted_event_count": len(admitted),
            "rejected_event_count": len(rejected),
            "rejection_reasons": reason_counts,
            "rejected_events": rejected[:40],
            "material_dimensions_added_by_event": additions,
            "material_information_added": bool(admitted),
        }

    @staticmethod
    def _cluster_expansion_summary_payload(result: Mapping[str, Any]) -> dict[str, Any]:
        """Compact expansion diagnostics safe for logs/report metadata."""
        def profile_summary(item: Mapping[str, Any]) -> dict[str, Any]:
            return {
                "cluster_id": _clean(item.get("cluster_id")),
                "score": item.get("score"),
                "eligible": bool(item.get("eligible")),
                "seed_basis": _clean(item.get("seed_basis")),
                "event_count": len(item.get("event_ids") or []),
                "underlying_event_count": len(item.get("underlying_event_ids") or []),
                "source_count": len(item.get("source_ids") or []),
                "actor_count": len(item.get("actors") or []),
                "actors": list(item.get("actors") or [])[:6],
                "event_classes": list(item.get("event_classes") or [])[:8],
                "mechanism_tags": list(item.get("mechanism_tags") or [])[:10],
                "dependency_tags": list(item.get("dependency_tags") or [])[:10],
                "missing_dimensions": list(item.get("missing_dimensions") or [])[:10],
            }

        compact_runs: list[dict[str, Any]] = []
        for item in (result.get("cluster_runs") or [])[:8]:
            if not isinstance(item, Mapping):
                continue
            rounds: list[dict[str, Any]] = []
            for raw in (item.get("rounds") or [])[:6]:
                if not isinstance(raw, Mapping):
                    continue
                material = _as_mapping(raw.get("material_information_assessment"))
                admission = _as_mapping(raw.get("strategic_event_admission"))
                rounds.append({
                    "round": raw.get("round"),
                    "status": _clean(raw.get("status")),
                    "queries": list(raw.get("queries") or [])[:DEEP_CLUSTER_EXPANSION_MAX_QUERIES_PER_ROUND],
                    "search_selected_source_ids": list(raw.get("search_selected_source_ids") or [])[:8],
                    "new_source_ids": list(raw.get("new_source_ids") or [])[:8],
                    "search_failure_count": len(raw.get("search_failures") or []),
                    "new_raw_event_count": raw.get("new_raw_event_count"),
                    "new_raw_evidence_count": raw.get("new_raw_evidence_count"),
                    "new_strategically_eligible_event_count": raw.get("new_strategically_eligible_event_count"),
                    "strategic_event_admission": {
                        "considered_event_count": admission.get("considered_event_count"),
                        "excluded_event_count": admission.get("excluded_event_count"),
                        "considered_evidence_count": admission.get("considered_evidence_count"),
                        "exclusion_reasons": _json_safe(admission.get("exclusion_reasons") or {}),
                    },
                    "admitted_material_event_ids": list(material.get("admitted_event_ids") or [])[:20],
                    "admitted_material_event_count": material.get("admitted_event_count"),
                    "material_rejection_reasons": _json_safe(material.get("rejection_reasons") or {}),
                    "material_dimensions_added_by_event": _json_safe(material.get("material_dimensions_added_by_event") or {}),
                    "event_count_after": raw.get("event_count_after"),
                    "evidence_count_after": raw.get("evidence_count_after"),
                })
            compact_runs.append({
                "cluster_id": _clean(item.get("cluster_id")),
                "initial_score": item.get("initial_score"),
                "final_score": item.get("final_score"),
                "seed_basis": _clean(item.get("seed_basis")),
                "initial_underlying_event_count": len(item.get("initial_underlying_event_ids") or []),
                "final_underlying_event_count": len(item.get("final_underlying_event_ids") or []),
                "initial_missing_dimensions": list(item.get("initial_missing_dimensions") or [])[:10],
                "final_missing_dimensions": list(item.get("final_missing_dimensions") or [])[:10],
                "saturated": bool(item.get("saturated")),
                "stop_reason": _clean(item.get("stop_reason")),
                "rounds": rounds,
            })
        return {
            "attempted": bool(result.get("attempted")),
            "selected_cluster_ids": list(result.get("selected_cluster_ids") or [])[:10],
            "new_material_event_count": result.get("new_material_event_count", 0),
            "initial_cluster_profiles": [
                profile_summary(item)
                for item in (result.get("initial_cluster_profiles") or [])[:10]
                if isinstance(item, Mapping)
            ],
            "cluster_runs": compact_runs,
            "final_cluster_profiles": [
                profile_summary(item)
                for item in (result.get("final_cluster_profiles") or [])[:10]
                if isinstance(item, Mapping)
            ],
            "final_source_count": result.get("final_source_count"),
            "final_event_count": result.get("final_event_count"),
            "final_evidence_count": result.get("final_evidence_count"),
            "stop_reason": _clean(result.get("stop_reason")),
            "invariant": _clean(result.get("invariant")),
        }

    def _expand_promising_strategic_clusters(
        self,
        *,
        topic: str,
        selected_source_ids: Sequence[str],
        event_records: Sequence[Mapping[str, Any]],
        evidence_records: Sequence[Mapping[str, Any]],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """Deep pre-inference cluster expansion with bounded saturation search.

        This is deliberately upstream of StrategicInference and distinct from the
        later hypothesis-rescue loop. It researches the most structurally useful
        event clusters before asking the model to infer anything.
        """
        current_sources = list(_unique_strings(selected_source_ids))
        current_events = [dict(item) for item in event_records if isinstance(item, Mapping)]
        current_evidence = [dict(item) for item in evidence_records if isinstance(item, Mapping)]
        initial_profiles = self._strategic_cluster_expansion_profiles(
            topic=topic, events=current_events, evidence=current_evidence
        )
        targets = [item for item in initial_profiles if item.get("eligible")][
            :DEEP_CLUSTER_EXPANSION_MAX_CLUSTERS
        ]
        result: dict[str, Any] = {
            "attempted": False,
            "selected_source_ids": current_sources,
            "event_records": current_events,
            "evidence_records": current_evidence,
            "initial_cluster_profiles": initial_profiles[:12],
            "selected_cluster_ids": [_clean(item.get("cluster_id")) for item in targets],
            "cluster_runs": [],
            "new_material_event_count": 0,
        }
        if not targets:
            result.update({
                "stop_reason": "no_cluster_met_pre_inference_expansion_threshold",
                "final_event_count": len(current_events),
                "final_evidence_count": len(current_evidence),
                "final_source_count": len(current_sources),
            })
            state["strategic_cluster_expansion"] = _json_safe(
                self._cluster_expansion_summary_payload(result)
            )
            logger.info(
                "strategic_cluster_expansion_summary=%s",
                json.dumps(_json_safe(state["strategic_cluster_expansion"]), ensure_ascii=False, default=str),
            )
            return result

        result["attempted"] = True
        logger.info(
            "strategic_cluster_expansion_plan=%s",
            json.dumps(
                _json_safe({
                    "selected_cluster_ids": result["selected_cluster_ids"],
                    "max_clusters": DEEP_CLUSTER_EXPANSION_MAX_CLUSTERS,
                    "max_rounds_per_cluster": DEEP_CLUSTER_EXPANSION_MAX_ROUNDS_PER_CLUSTER,
                    "max_queries_per_round": DEEP_CLUSTER_EXPANSION_MAX_QUERIES_PER_ROUND,
                    "max_sources_per_round": DEEP_CLUSTER_EXPANSION_MAX_SOURCES_PER_ROUND,
                    "selected_profiles": [
                        self._cluster_expansion_summary_payload({
                            "attempted": True,
                            "initial_cluster_profiles": [item],
                        }).get("initial_cluster_profiles", [])[0]
                        for item in targets
                    ],
                }),
                ensure_ascii=False,
                default=str,
            ),
        )
        for initial_target in targets:
            target = dict(initial_target)
            cluster_run: dict[str, Any] = {
                "cluster_id": _clean(target.get("cluster_id")),
                "initial_score": target.get("score"),
                "seed_basis": target.get("seed_basis"),
                "initial_event_ids": list(target.get("event_ids") or []),
                "initial_underlying_event_ids": list(target.get("underlying_event_ids") or []),
                "initial_missing_dimensions": list(target.get("missing_dimensions") or []),
                "rounds": [],
                "saturated": False,
            }
            for round_number in range(1, DEEP_CLUSTER_EXPANSION_MAX_ROUNDS_PER_CLUSTER + 1):
                # Refresh target profile from the evolving corpus so round 2
                # searches what is still missing rather than replaying round 1.
                refreshed_profiles = self._strategic_cluster_expansion_profiles(
                    topic=topic, events=current_events, evidence=current_evidence
                )
                refreshed = next(
                    (item for item in refreshed_profiles if _clean(item.get("cluster_id")) == cluster_run["cluster_id"]),
                    target,
                )
                target = dict(refreshed)
                queries = self._cluster_expansion_queries(
                    topic=topic, profile=target, round_number=round_number
                )
                round_trace: dict[str, Any] = {
                    "round": round_number,
                    "cluster_id": cluster_run["cluster_id"],
                    "cluster_score_before": target.get("score"),
                    "missing_dimensions_before": list(target.get("missing_dimensions") or []),
                    "queries": queries,
                }
                if not queries:
                    round_trace["status"] = "no_cluster_expansion_queries_generated"
                    cluster_run["rounds"].append(round_trace)
                    cluster_run["saturated"] = True
                    cluster_run["stop_reason"] = round_trace["status"]
                    logger.info(
                        "strategic_cluster_expansion_round=%s",
                        json.dumps(_json_safe(round_trace), ensure_ascii=False, default=str),
                    )
                    break

                search_state: dict[str, Any] = {
                    "run_id": state.get("run_id"), "topic": topic,
                    "requested_research_depth": "deep", "research_depth": "deep",
                    "warnings": [], "errors": [],
                    "depth_integrity": {
                        "requested": "deep", "resolved_profile": "deep",
                        "extraction": None, "strategic": None, "ok": False,
                    },
                }
                discovered = self._discover_sources_with_run_circuit_breaker(
                    topic=topic,
                    max_sources=DEEP_CLUSTER_EXPANSION_MAX_SOURCES_PER_ROUND,
                    parent_state=state,
                    discovery_state=search_state,
                    discovery_queries_override=queries,
                    discovery_phase=f"strategic_cluster_expansion_{cluster_run['cluster_id']}_{round_number}",
                )
                new_source_ids = [
                    source_id for source_id in discovered
                    if source_id and source_id not in current_sources
                ]
                round_trace["search_selected_source_ids"] = list(discovered)
                round_trace["new_source_ids"] = list(new_source_ids)
                round_trace["search_failures"] = list(
                    search_state.get("source_discovery_search_failures")
                    or search_state.get("source_discovery_failures")
                    or []
                )
                if not new_source_ids:
                    circuit_open = self._fresh_discovery_circuit_is_open(state)
                    round_trace["fresh_discovery_circuit_open"] = circuit_open
                    round_trace["status"] = (
                        "saturated_provider_circuit_open"
                        if circuit_open
                        else "saturated_no_new_sources"
                    )
                    cluster_run["rounds"].append(round_trace)
                    cluster_run["saturated"] = True
                    cluster_run["stop_reason"] = round_trace["status"]
                    logger.info(
                        "strategic_cluster_expansion_round=%s",
                        json.dumps(_json_safe(round_trace), ensure_ascii=False, default=str),
                    )
                    break

                extraction_state = {
                    "run_id": state.get("run_id"), "topic": topic,
                    "requested_research_depth": "deep", "research_depth": "deep",
                    "warnings": [], "errors": [],
                    "depth_integrity": {
                        "requested": "deep", "resolved_profile": "deep",
                        "extraction": None, "strategic": None, "ok": False,
                    },
                }
                new_raw_events = self._extract_events(
                    source_ids=new_source_ids, state=extraction_state
                )
                round_trace["new_raw_event_count"] = len(new_raw_events)
                round_trace["extraction_failures"] = list(extraction_state.get("extraction_failures") or [])
                current_sources = _unique_strings(current_sources + new_source_ids)
                if not new_raw_events:
                    round_trace["status"] = "saturated_no_new_events"
                    cluster_run["rounds"].append(round_trace)
                    cluster_run["saturated"] = True
                    cluster_run["stop_reason"] = round_trace["status"]
                    logger.info(
                        "strategic_cluster_expansion_round=%s",
                        json.dumps(_json_safe(round_trace), ensure_ascii=False, default=str),
                    )
                    break

                new_source_payloads = self._source_payloads(new_source_ids)
                new_evidence_payload = _as_mapping(self.evidence_builder_agent.build_evidence(
                    topic=topic,
                    events=new_raw_events,
                    sources=new_source_payloads,
                    semantic_fact_extraction=False,
                    semantic_fail_closed=False,
                ))
                new_raw_evidence = [
                    dict(item) for item in (new_evidence_payload.get("evidence_records") or [])
                    if isinstance(item, Mapping) and _clean(item.get("evidence_id") or item.get("id"))
                ]
                self._validate_evidence_currency(new_raw_evidence)
                round_trace["new_raw_evidence_count"] = len(new_raw_evidence)
                if not new_raw_evidence:
                    round_trace["status"] = "saturated_no_new_evidence_records"
                    cluster_run["rounds"].append(round_trace)
                    cluster_run["saturated"] = True
                    cluster_run["stop_reason"] = round_trace["status"]
                    logger.info(
                        "strategic_cluster_expansion_round=%s",
                        json.dumps(_json_safe(round_trace), ensure_ascii=False, default=str),
                    )
                    break

                combined_events = current_events + [dict(item) for item in new_raw_events]
                combined_evidence = current_evidence + new_raw_evidence
                combined_events, underlying_diag = self._annotate_underlying_events(
                    events=combined_events, topic=topic
                )
                prepared_events, prepared_evidence, admission = self._prepare_strategic_event_context(
                    topic=topic, events=combined_events, evidence=combined_evidence
                )
                round_trace["strategic_event_admission"] = _json_safe({
                    "considered_event_count": admission.get("considered_event_count"),
                    "excluded_event_count": admission.get("excluded_event_count"),
                    "considered_evidence_count": admission.get("considered_evidence_count"),
                    "exclusion_reasons": admission.get("exclusion_reasons"),
                })
                round_trace["underlying_event_diagnostics"] = _json_safe(underlying_diag)

                new_raw_ids = {
                    _clean(item.get("event_id") or item.get("id")).upper()
                    for item in new_raw_events
                    if isinstance(item, Mapping) and _clean(item.get("event_id") or item.get("id"))
                }
                new_eligible_events = [
                    dict(item) for item in prepared_events
                    if _clean(item.get("event_id") or item.get("id")).upper() in new_raw_ids
                ]
                round_trace["new_strategically_eligible_event_count"] = len(new_eligible_events)
                material = self._cluster_expansion_material_information_assessment(
                    target_profile=target,
                    existing_events=current_events,
                    new_events=new_eligible_events,
                )
                round_trace["material_information_assessment"] = _json_safe(material)
                admitted_ids = {
                    _clean(value).upper() for value in (material.get("admitted_event_ids") or []) if _clean(value)
                }
                if not admitted_ids:
                    round_trace["status"] = "saturated_no_new_material_cluster_information"
                    cluster_run["rounds"].append(round_trace)
                    cluster_run["saturated"] = True
                    cluster_run["stop_reason"] = round_trace["status"]
                    logger.info(
                        "strategic_cluster_expansion_round=%s",
                        json.dumps(_json_safe(round_trace), ensure_ascii=False, default=str),
                    )
                    break

                existing_ids = {
                    _clean(item.get("event_id") or item.get("id")).upper()
                    for item in current_events
                    if isinstance(item, Mapping) and _clean(item.get("event_id") or item.get("id"))
                }
                keep_ids = existing_ids | admitted_ids
                current_events = [
                    dict(item) for item in prepared_events
                    if _clean(item.get("event_id") or item.get("id")).upper() in keep_ids
                ]
                current_evidence = [
                    dict(item) for item in prepared_evidence
                    if _clean(item.get("event_id")).upper() in keep_ids
                ]
                added_count = len(admitted_ids)
                result["new_material_event_count"] += added_count
                round_trace["new_material_event_count"] = added_count
                round_trace["event_count_after"] = len(current_events)
                round_trace["evidence_count_after"] = len(current_evidence)
                round_trace["status"] = "material_information_added"
                cluster_run["rounds"].append(round_trace)
                logger.info(
                    "strategic_cluster_expansion_round=%s",
                    json.dumps(_json_safe(round_trace), ensure_ascii=False, default=str),
                )

            if not cluster_run.get("stop_reason"):
                cluster_run["stop_reason"] = (
                    "bounded_round_budget_exhausted_with_new_material_information"
                    if cluster_run["rounds"] and cluster_run["rounds"][-1].get("status") == "material_information_added"
                    else "completed"
                )
            final_profiles = self._strategic_cluster_expansion_profiles(
                topic=topic, events=current_events, evidence=current_evidence
            )
            final_profile = next(
                (item for item in final_profiles if _clean(item.get("cluster_id")) == cluster_run["cluster_id"]),
                {},
            )
            cluster_run["final_score"] = final_profile.get("score")
            cluster_run["final_event_ids"] = list(final_profile.get("event_ids") or [])
            cluster_run["final_underlying_event_ids"] = list(final_profile.get("underlying_event_ids") or [])
            cluster_run["final_missing_dimensions"] = list(final_profile.get("missing_dimensions") or [])
            result["cluster_runs"].append(cluster_run)

        result.update({
            "selected_source_ids": current_sources,
            "event_records": current_events,
            "evidence_records": current_evidence,
            "final_source_count": len(current_sources),
            "final_event_count": len(current_events),
            "final_evidence_count": len(current_evidence),
            "final_cluster_profiles": self._strategic_cluster_expansion_profiles(
                topic=topic, events=current_events, evidence=current_evidence
            )[:12],
            "stop_reason": "cluster_expansion_completed_before_strategic_inference",
            "invariant": (
                "cluster_expansion_adds_only_distinct_underlying_events_in_the_target_cluster_"
                "or_with_shared_mechanism_or_dependency"
            ),
        })
        state["strategic_cluster_expansion"] = _json_safe(
            self._cluster_expansion_summary_payload(result)
        )
        logger.info(
            "strategic_cluster_expansion_summary=%s",
            json.dumps(_json_safe(state["strategic_cluster_expansion"]), ensure_ascii=False, default=str),
        )
        return result

    def _attempt_targeted_strategic_evidence_rescue(
        self,
        *,
        topic: str,
        inference_result: Any,
        selected_source_ids: Sequence[str],
        event_records: Sequence[Mapping[str, Any]],
        evidence_records: Sequence[Mapping[str, Any]],
        graph_context: Mapping[str, Any],
        constraints: Any,
        blueprint: Mapping[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """Bounded hypothesis-driven gap closure before Deep mode abstains.

        V3.19 does not search for "more news". It searches only when a rejected
        candidate already has a valid structural basis and fails on an evidence
        completeness gate. Each round builds a main/competing-hypothesis plan,
        searches the discriminating gap, admits only new independent gap-relevant
        events, then re-runs the same bounded strategic task.
        """
        result = {
            "attempted": False,
            "inference_result": inference_result,
            "candidates": [],
            "selected_source_ids": list(selected_source_ids),
            "event_records": [dict(x) for x in event_records],
            "evidence_records": [dict(x) for x in evidence_records],
            "rounds": [],
        }

        current_inference = inference_result
        current_sources = list(selected_source_ids)
        current_events = [dict(x) for x in event_records]
        current_evidence = [dict(x) for x in evidence_records]
        rescue_rounds: list[dict[str, Any]] = []
        previous_rejection_signature: tuple[str, ...] = tuple()
        best_fallback_candidates = self._extract_insights(current_inference)

        for round_number in range(1, DEEP_MAX_STRATEGIC_EVIDENCE_RESCUE_ROUNDS + 1):
            inference_map = _as_mapping(current_inference)
            metadata = _as_mapping(inference_map.get("metadata"))
            diagnostics = [
                item
                for item in (
                    list(metadata.get("rejected_candidate_diagnostics") or [])
                    + list(metadata.get("tiered_candidate_diagnostics") or [])
                )
                if isinstance(item, Mapping)
            ]

            ranked: list[tuple[float, str, dict[str, Any], dict[str, Any]]] = []
            non_rescueable: list[dict[str, Any]] = []
            ignored_rejection_reasons: list[dict[str, Any]] = []
            for diagnostic in diagnostics:
                reason = _enum(
                    diagnostic.get("reason")
                    or diagnostic.get("hidden_insight_rejection_reason")
                )
                snapshot = _as_mapping(diagnostic.get("candidate_snapshot"))
                if not snapshot:
                    continue
                if reason not in STRATEGIC_EVIDENCE_RESCUE_REASONS:
                    ignored_rejection_reasons.append({
                        "reason": reason,
                        "candidate_id": _clean(snapshot.get("insight_id")),
                        "why_not_rescueable": "rejection_reason_not_classified_as_recoverable_evidence_gap",
                    })
                    continue
                eligibility = self._strategic_candidate_structural_rescue_eligibility(
                    snapshot=snapshot,
                    events=current_events,
                    evidence=current_evidence,
                )
                if not eligibility.get("eligible"):
                    non_rescueable.append({
                        "reason": reason,
                        "candidate_id": _clean(snapshot.get("insight_id")),
                        "structural_eligibility": eligibility,
                    })
                    continue
                score = self._strategic_candidate_investigation_score(
                    reason=reason, snapshot=snapshot
                )
                ranked.append((score, reason, dict(snapshot), dict(eligibility)))

            ranked.sort(key=lambda item: item[0], reverse=True)
            if not ranked:
                if non_rescueable or ignored_rejection_reasons:
                    rescue_rounds.append({
                        "round": round_number,
                        "status": (
                            "no_structurally_eligible_candidate_for_rescue"
                            if non_rescueable
                            else "no_recoverable_evidence_gap_for_rescue"
                        ),
                        "non_rescueable_candidates": non_rescueable[:8],
                        "ignored_rejection_reasons": ignored_rejection_reasons[:8],
                    })
                    logger.info(
                        "strategic_evidence_rescue_not_attempted=%s",
                        json.dumps(_json_safe(rescue_rounds[-1]), ensure_ascii=False, default=str),
                    )
                break

            score, reason, snapshot, eligibility = ranked[0]
            if score < DEEP_STRATEGIC_RESCUE_MIN_INVESTIGATION_SCORE:
                rescue_rounds.append({
                    "round": round_number,
                    "status": "not_investigated_low_candidate_score",
                    "reason": reason,
                    "investigation_score": score,
                    "structural_eligibility": eligibility,
                })
                break

            collection_plan = self._strategic_collection_plan(
                topic=topic,
                reason=reason,
                snapshot=snapshot,
                events=current_events,
                evidence=current_evidence,
                round_number=round_number,
            )
            if not _as_mapping(collection_plan.get("structural_eligibility")).get("eligible"):
                rescue_rounds.append({
                    "round": round_number,
                    "status": "collection_plan_blocked_by_structural_eligibility",
                    "reason": reason,
                    "collection_plan": collection_plan,
                })
                break

            queries = self._strategic_evidence_rescue_queries(
                topic=topic,
                reason=reason,
                snapshot=snapshot,
                round_number=round_number,
                collection_plan=collection_plan,
            )
            if not queries:
                rescue_rounds.append({
                    "round": round_number,
                    "status": "no_gap_driven_queries_generated",
                    "reason": reason,
                    "collection_plan": collection_plan,
                })
                break

            result["attempted"] = True
            logger.info(
                "strategic_evidence_collection_plan=%s",
                json.dumps(_json_safe({
                    "round": round_number,
                    "reason": reason,
                    "investigation_score": score,
                    "plan": collection_plan,
                    "queries": queries,
                }), ensure_ascii=False, default=str),
            )

            search_state: dict[str, Any] = {
                "run_id": state.get("run_id"),
                "topic": topic,
                "requested_research_depth": "deep",
                "research_depth": "deep",
                "warnings": [],
                "errors": [],
                "depth_integrity": {
                    "requested": "deep", "resolved_profile": "deep",
                    "extraction": None, "strategic": None, "ok": False,
                },
            }
            source_ids = self._discover_sources_with_run_circuit_breaker(
                topic=topic,
                max_sources=DEEP_STRATEGIC_RESCUE_MAX_SOURCES_PER_ROUND,
                parent_state=state,
                discovery_state=search_state,
                discovery_queries_override=queries,
                discovery_phase=f"strategic_gap_closure_{round_number}",
            )
            new_source_ids = [
                source_id for source_id in source_ids
                if source_id and source_id not in current_sources
            ]
            round_trace: dict[str, Any] = {
                "round": round_number,
                "reason": reason,
                "investigation_score": score,
                "candidate_insight_id": _clean(snapshot.get("insight_id")),
                "candidate_insight": _clean(snapshot.get("insight"))[:1200],
                "structural_eligibility": eligibility,
                "collection_plan": collection_plan,
                "queries": list(queries),
                "search_selected_source_ids": list(source_ids),
                "new_source_ids": list(new_source_ids),
                "search_failures": list(search_state.get("source_discovery_failures", []) or []),
                "search_completeness_claim": "bounded_hypothesis_testing_search_not_exhaustive",
            }
            if not new_source_ids:
                circuit_open = self._fresh_discovery_circuit_is_open(state)
                round_trace["fresh_discovery_circuit_open"] = circuit_open
                round_trace["status"] = (
                    "rescue_stopped_provider_circuit_open"
                    if circuit_open
                    else "no_new_sources"
                )
                rescue_rounds.append(round_trace)
                break

            extraction_state = {
                "run_id": state.get("run_id"), "topic": topic,
                "requested_research_depth": "deep", "research_depth": "deep",
                "warnings": [], "errors": [],
                "depth_integrity": {
                    "requested": "deep", "resolved_profile": "deep",
                    "extraction": None, "strategic": None, "ok": False,
                },
            }
            new_events = self._extract_events(source_ids=new_source_ids, state=extraction_state)
            round_trace["new_event_count"] = len(new_events)
            round_trace["extraction_failures"] = list(extraction_state.get("extraction_failures", []) or [])
            if not new_events:
                round_trace["status"] = "no_new_events"
                rescue_rounds.append(round_trace)
                current_sources = _unique_strings(current_sources + new_source_ids)
                break

            source_payloads = self._source_payloads(new_source_ids)
            evidence_payload = _as_mapping(self.evidence_builder_agent.build_evidence(
                topic=topic, events=new_events, sources=source_payloads,
                semantic_fact_extraction=False, semantic_fail_closed=False,
            ))
            new_evidence = [
                dict(item) for item in (evidence_payload.get("evidence_records") or [])
                if isinstance(item, Mapping) and _clean(item.get("evidence_id"))
            ]
            self._validate_evidence_currency(new_evidence)

            new_events, new_evidence, rescue_admission = self._prepare_strategic_event_context(
                topic=topic, events=new_events, evidence=new_evidence
            )
            round_trace["strategic_event_admission"] = _json_safe(rescue_admission)
            round_trace["strategically_eligible_event_count"] = len(new_events)
            round_trace["strategically_eligible_evidence_count"] = len(new_evidence)
            if not new_events or not new_evidence:
                round_trace["status"] = "no_new_strategically_eligible_evidence"
                rescue_rounds.append(round_trace)
                current_sources = _unique_strings(current_sources + new_source_ids)
                break

            materiality = self._rescue_material_information_assessment(
                plan=collection_plan,
                existing_events=current_events,
                new_events=new_events,
                topic=topic,
            )
            round_trace["material_information_assessment"] = _json_safe(materiality)
            admitted_new_event_ids = {
                _clean(value).upper() for value in (materiality.get("admitted_event_ids") or []) if _clean(value)
            }
            if not admitted_new_event_ids:
                round_trace["status"] = "no_new_gap_closing_independent_event"
                rescue_rounds.append(round_trace)
                current_sources = _unique_strings(current_sources + new_source_ids)
                logger.info(
                    "strategic_evidence_gap_closure_round=%s",
                    json.dumps(_json_safe(round_trace), ensure_ascii=False, default=str),
                )
                break

            admitted_new_events = [
                dict(item) for item in new_events
                if _clean(item.get("event_id")).upper() in admitted_new_event_ids
            ]
            admitted_new_evidence = [
                dict(item) for item in new_evidence
                if _clean(item.get("event_id")).upper() in admitted_new_event_ids
            ]
            if not admitted_new_evidence:
                round_trace["status"] = "gap_closing_events_have_no_evidence_records"
                rescue_rounds.append(round_trace)
                break

            existing_event_ids = {
                _clean(item.get("event_id")).upper() for item in current_events if _clean(item.get("event_id"))
            }
            for event in admitted_new_events:
                event_id = _clean(event.get("event_id")).upper()
                if event_id and event_id not in existing_event_ids:
                    current_events.append(dict(event)); existing_event_ids.add(event_id)

            existing_evidence_ids = {
                _clean(item.get("evidence_id") or item.get("id")).upper()
                for item in current_evidence if _clean(item.get("evidence_id") or item.get("id"))
            }
            materially_added = 0
            for evidence_item in admitted_new_evidence:
                evidence_id = _clean(evidence_item.get("evidence_id") or evidence_item.get("id")).upper()
                if evidence_id and evidence_id not in existing_evidence_ids:
                    current_evidence.append(dict(evidence_item))
                    existing_evidence_ids.add(evidence_id)
                    materially_added += 1

            current_sources = _unique_strings(current_sources + new_source_ids)
            current_events, rescue_underlying_diag = self._annotate_underlying_events(
                events=current_events, topic=topic
            )
            round_trace["underlying_event_diagnostics"] = _json_safe(rescue_underlying_diag)
            round_trace["new_gap_closing_evidence_count"] = materially_added
            if materially_added <= 0:
                round_trace["status"] = "no_new_gap_closing_evidence_records"
                rescue_rounds.append(round_trace)
                break

            refinement_context = {
                "round": round_number,
                "targeted_evidence_rescue": True,
                "hypothesis_testing_gap_closure": True,
                "trigger_reason": reason,
                "candidate_insight_id": _clean(snapshot.get("insight_id")),
                "collection_plan": _json_safe(collection_plan),
                "instruction": (
                    "Re-evaluate the same structurally eligible hypothesis using only newly admitted "
                    "gap-closing evidence. Compare the main hypothesis against the listed alternatives. "
                    "Preserve the thesis only if the new evidence changes the evidence balance or resolves "
                    "the blocking gap; otherwise narrow it or abstain. Evidence that merely repeats the "
                    "leading narrative does not increase confidence."
                ),
            }
            current_inference = self.strategic_inference_agent.analyze(
                research_question=topic,
                current_events=current_events,
                evidence=current_evidence,
                historical_events="",
                graph_context=graph_context,
                constraints=constraints,
                blueprint=blueprint,
                refinement_context=refinement_context,
                budget_service=self.execution_budget_service,
                execution_stage="refinement",
            )
            candidates = self._extract_insights(current_inference)
            post_metadata = _as_mapping(_as_mapping(current_inference).get("metadata"))
            post_reasons = tuple(sorted(_unique_strings(
                post_metadata.get("final_candidate_rejection_reasons")
                or post_metadata.get("candidate_rejection_reasons")
                or []
            )))
            round_trace["candidate_count"] = len(candidates)
            round_trace["finding_tiers_after_rerun"] = [
                self._finding_tier(item) for item in candidates if isinstance(item, Mapping)
            ]
            round_trace["post_search_rejection_reasons"] = list(post_reasons)
            hidden_recovered = self._has_hidden_insight_candidate(candidates)
            round_trace["hypothesis_matrix_changed"] = bool(
                candidates or (post_reasons and post_reasons != previous_rejection_signature)
            )
            if hidden_recovered:
                round_trace["status"] = "hidden_insight_recovered"
            elif candidates:
                round_trace["status"] = "lower_tier_finding_retained_after_rescue"
                best_fallback_candidates = list(candidates)
            else:
                round_trace["status"] = "still_rejected"
            rescue_rounds.append(round_trace)
            logger.info(
                "strategic_evidence_gap_closure_round=%s",
                json.dumps(_json_safe(round_trace), ensure_ascii=False, default=str),
            )

            if hidden_recovered:
                result["candidates"] = candidates
                break
            if previous_rejection_signature and post_reasons == previous_rejection_signature:
                # Stop rather than repeatedly searching until a preferred story appears.
                break
            previous_rejection_signature = post_reasons

        if not result.get("candidates") and best_fallback_candidates:
            result["candidates"] = list(best_fallback_candidates)

        result.update({
            "inference_result": current_inference,
            "selected_source_ids": current_sources,
            "event_records": current_events,
            "evidence_records": current_evidence,
            "rounds": rescue_rounds,
        })
        state["strategic_evidence_rescue"] = _json_safe({
            "attempted": result["attempted"],
            "mode": "hypothesis_testing_gap_closure",
            "max_rounds": DEEP_MAX_STRATEGIC_EVIDENCE_RESCUE_ROUNDS,
            "max_queries_per_round": 3,
            "max_new_sources_per_round": DEEP_STRATEGIC_RESCUE_MAX_SOURCES_PER_ROUND,
            "rounds": rescue_rounds,
            "final_candidate_count": len(result.get("candidates") or []),
            "final_source_count": len(current_sources),
            "final_event_count": len(current_events),
            "final_evidence_count": len(current_evidence),
            "invariant": "bad_structural_pair_is_never_rescued_by_search",
        })
        logger.info(
            "strategic_evidence_rescue_summary=%s",
            json.dumps(
                _json_safe(state["strategic_evidence_rescue"]),
                ensure_ascii=False,
                default=str,
            ),
        )
        return result

    def _refine_if_needed(
        self,
        *,
        topic: str,
        selected: Sequence[
            Mapping[str, Any]
        ],
        events: Sequence[
            Mapping[str, Any]
        ],
        evidence: Sequence[
            Mapping[str, Any]
        ],
        graph_context: Mapping[
            str,
            Any,
        ],
        constraints: Any,
        blueprint: Mapping[
            str,
            Any,
        ],
        profile: ResearchExecutionProfile,
        state: dict[str, Any],
    ) -> list[dict[str, Any]]:

        current = [
            dict(
                insight
            )
            for insight
            in selected
        ]

        refinement_runs = []

        for round_index in range(
            profile
            .max_refinement_rounds
        ):
            weak = [
                insight
                for insight
                in current
                if _enum(
                    insight.get(
                        "quality_assessment",
                        {},
                    ).get(
                        "quality_status"
                    )
                )
                in {
                    "needs_refinement",
                    "insufficient_evidence",
                }
            ]

            if not weak:
                break

            refinement_context = {
                "round": (
                    round_index
                    + 1
                ),
                "target_insights": (
                    weak
                ),
                "instruction": (
                    "Address documented "
                    "quality defects without "
                    "inventing evidence."
                ),
            }

            result = (
                self.strategic_inference_agent
                .analyze(
                    research_question=topic,
                    current_events=events,
                    evidence=evidence,
                    historical_events="",
                    graph_context=graph_context,
                    constraints=constraints,
                    blueprint=blueprint,
                    refinement_context=(
                        refinement_context
                    ),
                    budget_service=(
                        self
                        .execution_budget_service
                    ),
                    execution_stage=(
                        "refinement"
                    ),
                )
            )

            candidates = (
                self._extract_insights(
                    result
                )
            )

            if not candidates:
                break

            assessed = (
                self._assess_insights(
                    insights=candidates,
                    evidence=evidence,
                    constraints=constraints,
                    blueprint=blueprint,
                )
            )

            current = (
                self._select_insights(
                    assessed,
                    max_selected=(
                        profile
                        .max_selected_insights
                    ),
                )
            )

            refinement_runs.append(
                {
                    "round": (
                        round_index
                        + 1
                    ),
                    "candidate_count": (
                        len(
                            candidates
                        )
                    ),
                    "selected_count": (
                        len(
                            current
                        )
                    ),
                }
            )

        state[
            "refinement_runs"
        ] = refinement_runs

        return current

    def _ground_insights_fast(
        self,
        insights: Sequence[
            Mapping[str, Any]
        ],
        evidence: Sequence[
            Mapping[str, Any]
        ],
    ) -> dict[str, Any]:

        evidence_by_id = {
            _clean(
                item.get(
                    "evidence_id"
                )
            ): item
            for item in evidence
            if _clean(
                item.get(
                    "evidence_id"
                )
            )
        }

        all_ids = list(
            evidence_by_id
        )

        output: dict[
            str,
            Any,
        ] = {}

        for (
            index,
            insight,
        ) in enumerate(
            insights,
            start=1,
        ):
            insight_id = _clean(
                insight.get(
                    "insight_revision_id"
                )
                or insight.get(
                    "insight_id"
                )
                or (
                    f"INSIGHT-{index}"
                )
            )

            claims = (
                self._claims_from_insight(
                    insight
                )
            )

            grounded_claims: list[
                dict[str, Any]
            ] = []

            status_counts = {
                "contradicted": 0,
                "inference_supported": 0,
                "partially_supported": 0,
                "supported": 0,
                "unsupported": 0,
            }

            examined_ids: list[str] = []

            for claim in claims:
                claim_text = _clean(
                    claim.get(
                        "claim_text"
                    )
                )

                cited_ids = (
                    _unique_strings(
                        claim.get(
                            "evidence_ids",
                            [],
                        )
                        or []
                    )
                )

                valid_ids = [
                    evidence_id
                    for evidence_id
                    in cited_ids
                    if evidence_id
                    in evidence_by_id
                ]

                invalid_ids = [
                    evidence_id
                    for evidence_id
                    in cited_ids
                    if evidence_id
                    not in evidence_by_id
                ]

                supported_ids: list[
                    str
                ] = []

                normalized_claim = (
                    claim_text
                    .casefold()
                )

                for evidence_id in (
                    valid_ids
                ):
                    item = (
                        evidence_by_id[
                            evidence_id
                        ]
                    )

                    evidence_text = _clean(
                        item.get(
                            "text"
                        )
                        or item.get(
                            "source_excerpt"
                        )
                    ).casefold()

                    if (
                        normalized_claim
                        and evidence_text
                        and (
                            normalized_claim
                            == evidence_text
                            or normalized_claim
                            in evidence_text
                            or evidence_text
                            in normalized_claim
                        )
                    ):
                        supported_ids.append(
                            evidence_id
                        )

                examined_ids.extend(
                    valid_ids
                )

                epistemic_type = _enum(
                    claim.get(
                        "epistemic_type",
                        "fact",
                    )
                )

                is_inference_claim = (
                    epistemic_type
                    in {
                        "hidden_insight",
                        "mechanism",
                        "second_order_effect",
                        "inference",
                    }
                )

                # Fast grounding treats exact/contained text as direct factual
                # support. A synthesized inference is not expected to be a
                # verbatim evidence sentence: it is inference-supported only
                # when it cites at least two valid admitted evidence records
                # and none of its citations are invalid.
                if (
                    is_inference_claim
                    and len(valid_ids) >= 2
                    and not invalid_ids
                ):
                    status = (
                        "inference_supported"
                    )
                    supported_ids = list(
                        valid_ids
                    )
                elif (
                    supported_ids
                    and not invalid_ids
                ):
                    status = (
                        "supported"
                    )
                elif supported_ids:
                    status = (
                        "partially_supported"
                    )
                else:
                    status = (
                        "unsupported"
                    )

                status_counts[
                    status
                ] += 1

                grounded_claims.append(
                    {
                        "claim_id": (
                            claim.get(
                                "claim_id"
                            )
                        ),
                        "claim_text": (
                            claim_text
                        ),
                        "epistemic_type": (
                            claim.get(
                                "epistemic_type",
                                "fact",
                            )
                        ),
                        "grounding_status": (
                            status
                        ),
                        "cited_evidence_ids": (
                            cited_ids
                        ),
                        "valid_evidence_ids": (
                            valid_ids
                        ),
                        "invalid_evidence_ids": (
                            invalid_ids
                        ),
                        "supporting_evidence_ids": (
                            supported_ids
                        ),
                        "contradicted_evidence_ids": [],
                        "citation_valid": (
                            bool(
                                valid_ids
                            )
                            and not
                            invalid_ids
                        ),
                        "attribution_valid": (
                            bool(
                                supported_ids
                            )
                        ),
                        "temporal_valid": (
                            True
                        ),
                        "numeric_valid": (
                            True
                        ),
                        "multi_evidence_required": (
                            bool(
                                claim.get(
                                    "multi_evidence_required",
                                    False,
                                )
                            )
                        ),
                        "multi_evidence_satisfied": (
                            len(
                                supported_ids
                            )
                            >= 2
                            if claim.get(
                                "multi_evidence_required",
                                False,
                            )
                            else bool(
                                supported_ids
                            )
                        ),
                        "unsupported_parts": (
                            []
                            if supported_ids
                            else [
                                (
                                    "Claim text is not "
                                    "directly anchored "
                                    "to the cited "
                                    "admitted EVID-* "
                                    "records."
                                )
                            ]
                        ),
                        "rationale": (
                            (
                                "Fast deterministic grounding validates "
                                "the cited admitted EVID-* links for the "
                                "synthesized inference; it does not treat "
                                "the inference as a verbatim source fact."
                            )
                            if is_inference_claim
                            else (
                                "Fast deterministic grounding checks direct "
                                "text anchoring against cited admitted "
                                "EVID-* records."
                            )
                        ),
                        "metadata": {
                            "prompt_version": (
                                "fast-deterministic-"
                                "grounding-v1"
                            ),
                            "model": "",
                            "candidate_evidence_count": (
                                len(
                                    valid_ids
                                )
                            ),
                        },
                    }
                )

            examined_unique = (
                _unique_strings(
                    examined_ids
                )
            )

            examined_set = set(
                examined_unique
            )

            if (
                status_counts.get(
                    "contradicted",
                    0,
                ) > 0
            ):
                overall_status = (
                    "contradicted"
                )
            elif (
                status_counts.get(
                    "inference_supported",
                    0,
                ) > 0
            ):
                overall_status = (
                    "inference_supported"
                )
            elif (
                status_counts.get(
                    "supported",
                    0,
                ) > 0
                or status_counts.get(
                    "partially_supported",
                    0,
                ) > 0
            ):
                overall_status = (
                    "supported"
                )
            else:
                overall_status = (
                    "unsupported"
                )

            output[
                insight_id
            ] = {
                "review_status": (
                    "complete"
                ),
                "status": (
                    overall_status
                ),
                "support_evidence_ids": (
                    examined_unique
                ),
                "evidence_ids": (
                    examined_unique
                ),
                "critical_unknowns": (
                    _unique_strings(
                        insight.get(
                            "critical_unknowns",
                            [],
                        )
                        or []
                    )
                ),
                "claim_count": (
                    len(
                        grounded_claims
                    )
                ),
                "status_counts": (
                    status_counts
                ),
                "claims": (
                    grounded_claims
                ),
                "evidence_completeness": {
                    "all_accepted_evidence_ids": (
                        all_ids
                    ),
                    "examined_evidence_ids": (
                        examined_unique
                    ),
                    "unexamined_evidence_ids": [
                        evidence_id
                        for evidence_id
                        in all_ids
                        if evidence_id
                        not in examined_set
                    ],
                },
                "metadata": {
                    "prompt_version": (
                        "fast-deterministic-"
                        "grounding-v1"
                    ),
                    "model": "",
                    "llm_call_performed": (
                        False
                    ),
                },
            }

        return output

    @staticmethod
    def _ground_report_fast(
        report: Mapping[
            str,
            Any,
        ],
        evidence: Sequence[
            Mapping[str, Any]
        ],
    ) -> dict[str, Any]:
        """
        Deterministically validate every Fast analytical claim that is actually
        displayed.  A Fast run is also allowed to release an honest abstention
        report when there are no analytical claims, provided the displayed
        material events map back to admitted EVID-* records.

        This fixes the v2.6 contradiction where an empty Hidden Insight section
        automatically made report grounding "incomplete" even when abstention
        was the intended, safer outcome.
        """

        valid_evidence_ids = {
            _clean(item.get("evidence_id"))
            for item in evidence
            if (
                isinstance(item, Mapping)
                and _clean(item.get("evidence_id"))
            )
        }
        evidence_event_ids = {
            _clean(item.get("event_id"))
            for item in evidence
            if (
                isinstance(item, Mapping)
                and _clean(item.get("event_id"))
            )
        }

        missing_links: list[str] = []
        examined_claim_count = 0
        examined_event_count = 0
        invalid_event_links: list[str] = []
        seen_claim_keys: set[str] = set()

        def _claim_text(item: Mapping[str, Any]) -> str:
            return _clean(
                item.get("text")
                or item.get("connection")
                or item.get("description")
                or item.get("statement")
                or item.get("insight")
            )

        def _placeholder(item: Mapping[str, Any]) -> bool:
            return _enum(item.get("status")) in {
                "not_established",
                "not_established",
                "abstained",
                "insufficient_evidence",
            }

        def _check_claim(
            item: Any,
            *,
            section: str,
            require_marker: str = "",
        ) -> None:
            nonlocal examined_claim_count

            if not isinstance(item, Mapping):
                return
            if _placeholder(item):
                return

            if require_marker:
                marker_value = _enum(item.get(require_marker))
                # For Connections/Underlying, only validate synthesized claims
                # or records that explicitly carry evidence IDs.  This avoids
                # treating unrelated graph/constraint display records as Fast
                # strategic-inference claims.
                if (
                    not marker_value
                    and not (item.get("evidence_ids") or [])
                ):
                    return

            value = _claim_text(item)
            if not value:
                return

            key = f"{section}|{value.casefold()}"
            if key in seen_claim_keys:
                return
            seen_claim_keys.add(key)
            examined_claim_count += 1

            ids = _unique_strings(
                item.get("evidence_ids", [])
                or item.get("support_evidence_ids", [])
                or item.get("supporting_evidence_ids", [])
                or []
            )

            if (
                not ids
                or any(
                    evidence_id not in valid_evidence_ids
                    for evidence_id in ids
                )
            ):
                missing_links.append(
                    _clean(item.get("insight_id"))
                    or _clean(item.get("insight_revision_id"))
                    or f"{section}_{examined_claim_count}"
                )

        for item in report.get("hidden_insights", []) or []:
            _check_claim(item, section="hidden_insight")
        # executive_synthesis usually duplicates hidden_insights, but checking it
        # independently is safer if report composition changes later.
        for item in report.get("executive_synthesis", []) or []:
            _check_claim(item, section="executive_synthesis")
        for item in report.get("connections", []) or []:
            _check_claim(
                item,
                section="connection",
                require_marker="connection_type",
            )
        for item in report.get("what_is_happening_underneath", []) or []:
            _check_claim(
                item,
                section="underlying_dynamic",
                require_marker="analysis_type",
            )
        for item in report.get("second_order_effects", []) or []:
            _check_claim(item, section="second_order_effect")

        # When Fast abstains from inference, there may be zero analytical claims.
        # In that case validate the displayed material-event landscape instead of
        # forcing a fabricated hidden insight just to satisfy the release gate.
        for item in report.get("event_landscape", []) or []:
            if not isinstance(item, Mapping):
                continue
            event_id = _clean(item.get("event_id"))
            if not event_id:
                continue
            examined_event_count += 1
            if event_id not in evidence_event_ids:
                invalid_event_links.append(event_id)

        if examined_claim_count > 0:
            complete = not missing_links
            grounding_scope = "displayed_fast_analytical_claims"
            honest_abstention = False
        else:
            complete = bool(
                examined_event_count > 0
                and not invalid_event_links
            )
            grounding_scope = "material_events_only_no_publishable_inference"
            honest_abstention = complete

        combined_missing = _unique_strings(
            list(missing_links)
            + [f"event:{event_id}" for event_id in invalid_event_links]
        )

        return {
            "review_status": "complete" if complete else "incomplete",
            "status": "complete" if complete else "incomplete",
            "grounding_mode": "fast_deterministic_evidence_link_check_v2",
            "grounding_scope": grounding_scope,
            "honest_abstention": honest_abstention,
            "examined_claim_count": examined_claim_count,
            "examined_event_count": examined_event_count,
            "missing_or_invalid_evidence_links": combined_missing,
            "metadata": {
                "llm_call_performed": False,
                "hidden_insight_required_for_completion": False,
            },
        }

    def _ground_insights(
        self,
        insights: Sequence[
            Mapping[str, Any]
        ],
        evidence: Sequence[
            Mapping[str, Any]
        ],
    ) -> dict[str, Any]:

        output: dict[
            str,
            Any,
        ] = {}

        for (
            index,
            insight,
        ) in enumerate(
            insights,
            start=1,
        ):
            insight_id = _clean(
                insight.get(
                    "insight_revision_id"
                )
                or insight.get(
                    "insight_id"
                )
                or (
                    f"INSIGHT-{index}"
                )
            )

            claims = (
                self._claims_from_insight(
                    insight
                )
            )

            grounding = (
                self.grounding_service
                .ground_claims(
                    claims=claims,
                    evidence_records=evidence,
                )
            )

            output[
                insight_id
            ] = grounding

        return output

    def _ground_report(
        self,
        report: Mapping[
            str,
            Any,
        ],
        evidence: Sequence[
            Mapping[str, Any]
        ],
    ) -> dict[str, Any]:

        if hasattr(
            self.grounding_service,
            "ground_report",
        ):
            return (
                self.grounding_service
                .ground_report(
                    report=report,
                    evidence_records=evidence,
                )
            )

        valid_evidence_ids = {
            _clean(
                item.get(
                    "evidence_id"
                )
            )
            for item
            in evidence
            if _clean(
                item.get(
                    "evidence_id"
                )
            )
        }

        missing_links = []
        examined = 0

        for item in (
            report.get(
                "executive_synthesis",
                [],
            )
            or []
        ):
            if not isinstance(
                item,
                Mapping,
            ):
                continue

            examined += 1

            ids = (
                _unique_strings(
                    item.get(
                        "evidence_ids",
                        [],
                    )
                    or []
                )
            )

            if (
                not ids
                or any(
                    evidence_id
                    not in valid_evidence_ids
                    for evidence_id
                    in ids
                )
            ):
                missing_links.append(
                    _clean(
                        item.get(
                            "insight_id"
                        )
                    )
                    or (
                        f"executive_synthesis_"
                        f"{examined}"
                    )
                )

        return {
            "review_status": (
                "complete"
                if (
                    examined > 0
                    and not missing_links
                )
                else "incomplete"
            ),
            "grounding_mode": (
                "deterministic_"
                "evidence_link_check"
            ),
            "examined_claim_count": (
                examined
            ),
            "missing_or_invalid_evidence_links": (
                missing_links
            ),
        }

    @staticmethod
    def _split_material_claims(
        text: Any,
        *,
        maximum_claims: int = 24,
        maximum_chars: int = 900,
    ) -> list[str]:
        """
        Split long synthesis prose into material grounding units.

        This is intentionally deterministic and does not create or rewrite facts.
        Sentence-level units prevent a valid citation attached to a long paragraph
        from masking unsupported sentences inside that paragraph.
        """
        value = _clean(text)
        if not value:
            return []

        # Primary sentence boundaries.  Keep common decimal/abbreviation behaviour
        # conservative by requiring whitespace followed by a likely sentence start.
        units = re.split(
            r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])",
            value,
        )

        output: list[str] = []
        for raw in units:
            raw = _clean(raw)
            if not raw:
                continue

            # Very long semicolon-heavy sentences are still too coarse for
            # grounding. Split only when materially long.
            pieces = [raw]
            if len(raw) > maximum_chars and ";" in raw:
                pieces = [
                    _clean(piece)
                    for piece in re.split(r";\s+", raw)
                    if _clean(piece)
                ]

            for piece in pieces:
                if len(piece) <= maximum_chars:
                    output.append(piece)
                else:
                    # Last-resort word-boundary chunks.  No paraphrasing.
                    remaining = piece
                    while remaining:
                        if len(remaining) <= maximum_chars:
                            output.append(remaining)
                            break
                        cut = remaining.rfind(" ", 0, maximum_chars)
                        if cut < 160:
                            cut = maximum_chars
                        output.append(remaining[:cut].strip())
                        remaining = remaining[cut:].strip()

                if len(output) >= maximum_claims:
                    return output[:maximum_claims]

        return output[:maximum_claims]

    def _claims_from_insight(
        self,
        insight: Mapping[
            str,
            Any,
        ],
    ) -> list[dict[str, Any]]:

        claims: list[
            dict[str, Any]
        ] = []

        revision_id = _clean(
            insight.get(
                "insight_revision_id"
            )
            or insight.get(
                "insight_id"
            )
        )

        for premise in (
            insight.get(
                "premises",
                [],
            )
            or []
        ):
            if not isinstance(
                premise,
                Mapping,
            ):
                continue

            text = _clean(
                premise.get(
                    "text"
                )
            )

            if not text:
                continue

            claims.append(
                {
                    "claim_id": (
                        premise.get(
                            "premise_id"
                        )
                        or (
                            f"{revision_id}-"
                            f"PREMISE-"
                            f"{len(claims) + 1}"
                        )
                    ),
                    "claim_text": (
                        text
                    ),
                    "evidence_ids": (
                        premise.get(
                            "evidence_ids",
                            [],
                        )
                    ),
                    "epistemic_type": (
                        "fact"
                    ),
                }
            )

        mechanism = (
            insight.get(
                "mechanism"
            )
            or {}
        )

        if isinstance(
            mechanism,
            Mapping,
        ):
            mechanism_text = (
                _clean(
                    mechanism.get(
                        "description"
                    )
                )
            )

            if mechanism_text:
                mechanism_ids = (
                    mechanism.get(
                        "evidence_ids",
                        [],
                    )
                    or mechanism.get(
                        "analogical_evidence_ids",
                        [],
                    )
                    or self._insight_evidence_ids(insight)
                )
                for sentence_index, sentence in enumerate(
                    self._split_material_claims(mechanism_text),
                    start=1,
                ):
                    claims.append(
                        {
                            "claim_id": (
                                f"{revision_id}-MECHANISM-{sentence_index:02d}"
                            ),
                            "claim_text": sentence,
                            "evidence_ids": mechanism_ids,
                            "epistemic_type": "mechanism",
                            "multi_evidence_required": True,
                        }
                    )

        second_order = _clean(
            insight.get(
                "second_order_effect"
            )
        )

        if second_order:
            all_ids = self._insight_evidence_ids(insight)
            for sentence_index, sentence in enumerate(
                self._split_material_claims(second_order),
                start=1,
            ):
                claims.append(
                    {
                        "claim_id": (
                            f"{revision_id}-SECOND-ORDER-{sentence_index:02d}"
                        ),
                        "claim_text": sentence,
                        "evidence_ids": all_ids,
                        "epistemic_type": "second_order_effect",
                        "multi_evidence_required": True,
                    }
                )

        insight_text = _clean(
            insight.get(
                "insight"
            )
            or insight.get(
                "text"
            )
        )

        if insight_text:
            all_ids = self._insight_evidence_ids(insight)
            for sentence_index, sentence in enumerate(
                self._split_material_claims(insight_text),
                start=1,
            ):
                claims.append(
                    {
                        "claim_id": (
                            f"{revision_id}-INSIGHT-{sentence_index:02d}"
                        ),
                        "claim_text": sentence,
                        "evidence_ids": all_ids,
                        "epistemic_type": "hidden_insight",
                        "multi_evidence_required": True,
                    }
                )

        return claims

    @staticmethod
    def _insight_evidence_ids(
        insight: Mapping[
            str,
            Any,
        ],
    ) -> list[str]:

        ids: list[str] = []

        # Canonical StrategicInferenceAgent support field.
        ids.extend(
            insight.get(
                "supporting_evidence_ids",
                [],
            )
            or insight.get(
                "evidence_ids",
                [],
            )
            or []
        )

        for premise in (
            insight.get(
                "premises",
                [],
            )
            or []
        ):
            if isinstance(
                premise,
                Mapping,
            ):
                ids.extend(
                    premise.get(
                        "evidence_ids",
                        [],
                    )
                    or []
                )

        mechanism = (
            insight.get(
                "mechanism",
                {},
            )
            or {}
        )

        if isinstance(
            mechanism,
            Mapping,
        ):
            ids.extend(
                mechanism.get(
                    "analogical_evidence_ids",
                    [],
                )
                or []
            )

        feasibility = (
            insight.get(
                "feasibility",
                {},
            )
            or {}
        )

        if isinstance(
            feasibility,
            Mapping,
        ):
            ids.extend(
                feasibility.get(
                    "feasibility_evidence_ids",
                    [],
                )
                or []
            )

        ids.extend(
            insight.get(
                "counterevidence_ids",
                [],
            )
            or []
        )

        ids.extend(
            insight.get(
                "counter_incentive_evidence_ids",
                [],
            )
            or []
        )

        return (
            _unique_strings(
                ids
            )
        )

    def _final_quality_assessment(
        self,
        *,
        selected: Sequence[
            Mapping[str, Any]
        ],
        grounding: Mapping[
            str,
            Any,
        ],
        evidence: Sequence[
            Mapping[str, Any]
        ],
        constraints: Any,
        blueprint: Mapping[
            str,
            Any,
        ],
    ) -> list[dict[str, Any]]:

        output = []

        for insight in selected:
            insight_id = _clean(
                insight.get(
                    "insight_revision_id"
                )
                or insight.get(
                    "insight_id"
                )
            )

            grounding_result = (
                grounding.get(
                    insight_id,
                    {},
                )
            )

            assessment = (
                self.insight_quality_service
                .assess(
                    insight=insight,
                    evidence=evidence,
                    grounding=(
                        grounding_result
                    ),
                    constraints=(
                        self._constraint_list(
                            constraints
                        )
                    ),
                    blueprint=blueprint,
                )
            )

            output.append(
                {
                    "insight": dict(
                        insight
                    ),
                    "quality": (
                        _as_mapping(
                            assessment
                        )
                    ),
                }
            )

        return output

    @staticmethod
    def _material_event_landscape(
        *,
        events: Sequence[Mapping[str, Any]],
        evidence: Sequence[Mapping[str, Any]],
        insights: Sequence[Mapping[str, Any]],
        maximum: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Return only insight-driving events, with enough factual context for a
        human reader to understand why the event can participate in synthesis.

        Trust rule:
        a material corporate/institutional action without a named actor is not
        displayed as a usable research event.  Old cached events that pre-date
        the ExtractionAgent attribution guard are therefore filtered here too.
        """
        import re as _re

        maximum = max(1, int(maximum))

        event_by_id = {
            _clean(event.get("event_id")): event
            for event in events
            if isinstance(event, Mapping) and _clean(event.get("event_id"))
        }

        evidence_event = {
            _clean(item.get("evidence_id")): _clean(item.get("event_id"))
            for item in evidence
            if isinstance(item, Mapping) and _clean(item.get("evidence_id"))
        }

        evidence_by_event: dict[str, list[Mapping[str, Any]]] = {}
        for item in evidence:
            if not isinstance(item, Mapping):
                continue
            event_id = _clean(item.get("event_id"))
            if not event_id:
                continue
            evidence_by_event.setdefault(event_id, []).append(item)

        noise = (
            r"\bLearn more\b", r"\bLoading(?:…|\.\.\.)?\b", r"\bShare\b",
            r"\bWhy deployment matters\b",
            r"\bBuilding for where frontier AI is headed\b",
            r"\bAccelerating deployment from day one\b",
            r"\bScaling deployment across the economy\b",
            r"\bSkip to main content\b", r"\bMy Account\b",
            r"\bSubscribe\b", r"\bLogin\b",
        )

        placeholder_actor = _re.compile(
            r"^(?:the\s+)?(?:company|firm|operator|provider|issuer|business|"
            r"organization|organisation|institution|entity|party|startup|actor|it|they|today)$|"
            r"^(?:unnamed|unknown|unidentified|another|separate)\s+"
            r"(?:actor|company|firm|issuer|operator|provider|entity)$",
            _re.I,
        )

        non_actor_types = {
            "location", "place", "city", "state", "country", "region",
            "geography", "product", "technology", "metric", "money",
            "currency", "date", "time", "industry", "sector", "market",
            "topic",
        }

        material_action = _re.compile(
            r"\b(?:rais(?:e|ed|es|ing)|fund(?:ed|ing)?|financ(?:e|ed|es|ing)|"
            r"credit\s+facilit(?:y|ies)|revolving\s+credit|convertible\s+"
            r"(?:senior\s+)?notes?|bond(?:s)?|debt|equity|invest(?:s|ed|ment|ing)?|"
            r"acquir(?:e|ed|es|ing)|acquisition|merg(?:e|ed|es|ing)|partnership|"
            r"partner(?:s|ed|ing)?|contract(?:s|ed|ing)?|agreement|deal|"
            r"launch(?:es|ed|ing)?|deploy(?:s|ed|ment|ing)?|build(?:s|ing|out)?|"
            r"open(?:s|ed|ing)?|close(?:s|d|ing)?|appoint(?:s|ed|ment)?|"
            r"approve(?:s|d|al)?|ban(?:s|ned)?|regulat(?:es|ed|ion)|"
            r"sue(?:s|d)?|announce(?:s|d)?)\b",
            _re.I,
        )

        def _clean_display(value: Any, maximum_chars: int = 1100) -> str:
            cleaned = _clean(value)
            for pattern in noise:
                cleaned = _re.sub(pattern, " ", cleaned, flags=_re.I)
            cleaned = _re.sub(r"\s+", " ", cleaned).strip(" …-|:;,.")
            if not cleaned:
                return ""
            if len(cleaned) <= maximum_chars:
                return cleaned

            prefix = cleaned[:maximum_chars]
            ends = [
                match.end()
                for match in _re.finditer(r"[.!?](?:\s|$)", prefix)
            ]
            if ends and ends[-1] >= 180:
                return prefix[:ends[-1]].strip()

            clipped = prefix.rsplit(" ", 1)[0].rstrip(" ,;:-")
            return clipped + "…"

        def _best_event_text(
            values: Sequence[Any],
            *,
            actors: Sequence[str],
        ) -> str:
            candidates: list[tuple[float, str]] = []
            nav_terms = {
                "learn more", "loading", "share", "skip to main content",
                "my account", "subscribe", "login",
            }
            for raw in values:
                cleaned = _clean_display(raw, 1400)
                if not cleaned:
                    continue
                clauses = [
                    _clean(part)
                    for part in _re.split(
                        r"(?<=[.!?])\s+|\s+[|•]\s+|\s{2,}",
                        cleaned,
                    )
                    if _clean(part)
                ]
                if not clauses:
                    clauses = [cleaned]
                for clause in clauses:
                    low = clause.casefold()
                    if len(clause) < 35:
                        continue
                    nav_hits = sum(term in low for term in nav_terms)
                    if nav_hits >= 2:
                        continue
                    score = 0.0
                    if material_action.search(clause):
                        score += 5.0
                    if any(actor.casefold() in low for actor in actors if actor):
                        score += 4.0
                    if _re.search(r"(?:[$€£]|USD|EUR|GBP)\s?\d|\b\d+(?:\.\d+)?%\b", clause):
                        score += 1.5
                    if _re.search(r"\b(?:to|designed to|aims? to|will|for)\b", low):
                        score += 0.6
                    score += min(len(clause), 320) / 320.0
                    candidates.append((score, clause))
            if not candidates:
                return ""
            candidates.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
            return _clean_display(candidates[0][1], 520)

        def _actor_names(event: Mapping[str, Any]) -> list[str]:
            result: list[str] = []
            seen: set[str] = set()
            for raw in event.get("entity_mentions", []) or []:
                if not isinstance(raw, Mapping):
                    continue
                name = _clean(raw.get("mention") or raw.get("name"))
                entity_type = _enum(raw.get("entity_type") or "unknown")
                if not name or placeholder_actor.match(name):
                    continue
                if entity_type in non_actor_types:
                    continue
                key = name.casefold()
                if key in seen:
                    continue
                seen.add(key)
                result.append(name)
                if len(result) >= 5:
                    break
            return result

        def _quantitative_details(event: Mapping[str, Any]) -> list[str]:
            result: list[str] = []
            seen: set[str] = set()
            for raw in event.get("quantitative_claims", []) or []:
                if not isinstance(raw, Mapping):
                    continue
                name = _clean(raw.get("name"))
                value = _clean(raw.get("value"))
                unit = _clean(raw.get("unit"))
                period = _clean(raw.get("period"))
                context = _clean(raw.get("context"))
                if not value:
                    continue
                parts = []
                machine_names = {
                    "stated_quantity", "quantity", "amount", "value",
                    "stated_amount", "numeric_value",
                }
                if name and _enum(name) not in machine_names:
                    parts.append(name + ":")
                parts.append(value)
                if unit:
                    parts.append(unit)
                if period:
                    parts.append("(" + period + ")")
                text = " ".join(parts)
                if context and len(text) < 160:
                    text += " — " + context
                key = text.casefold()
                if key in seen:
                    continue
                seen.add(key)
                result.append(text[:260])
                if len(result) >= 4:
                    break
            return result

        # Every evidence-backed event reviewed by synthesis must be represented
        # in Event Landscape.  Insight-driving events are placed first; the rest
        # are still shown in summarized form rather than becoming a hidden corpus.
        wanted_ids: list[str] = []
        for insight in insights:
            if not isinstance(insight, Mapping):
                continue
            wanted_ids.extend(_unique_strings(insight.get("event_ids", []) or []))
            for evidence_id in _unique_strings(
                insight.get("evidence_ids", [])
                or insight.get("supporting_evidence_ids", [])
                or []
            ):
                event_id = evidence_event.get(evidence_id, "")
                if event_id:
                    wanted_ids.append(event_id)

        evidence_backed_ids = _unique_strings(
            _clean(item.get("event_id"))
            for item in evidence
            if isinstance(item, Mapping) and _clean(item.get("event_id"))
        )
        wanted_ids = _unique_strings(list(wanted_ids) + list(evidence_backed_ids))
        if not wanted_ids:
            wanted_ids = list(event_by_id)

        output: list[dict[str, Any]] = []
        seen_text: set[str] = set()
        underlying_output_index: dict[str, int] = {}

        for event_id in wanted_ids:
            event = event_by_id.get(event_id)
            if not isinstance(event, Mapping):
                continue

            underlying_event_id = _clean(event.get("underlying_event_id") or event_id).upper()
            if underlying_event_id in underlying_output_index:
                idx = underlying_output_index[underlying_event_id]
                member_ids = _unique_strings(
                    list(output[idx].get("member_event_ids", []) or []) + [event_id]
                )
                merged_evidence_ids = _unique_strings(
                    list(output[idx].get("evidence_ids", []) or [])
                    + [
                        item.get("evidence_id")
                        for item in evidence_by_event.get(event_id, [])
                        if isinstance(item, Mapping)
                    ]
                )
                output[idx]["member_event_ids"] = member_ids
                output[idx]["evidence_ids"] = merged_evidence_ids
                output[idx]["underlying_event_member_count"] = len(member_ids)
                continue

            if _enum(event.get("temporal_status")) in {
                "future", "future_commitment", "prospective", "forecast",
                "prediction", "scenario",
            }:
                continue

            metadata = event.get("extraction_metadata", {}) or {}
            taxonomy = _enum(metadata.get("event_taxonomy")) if isinstance(metadata, Mapping) else ""
            if taxonomy and taxonomy not in {"observed_event", "current", "unknown"}:
                continue

            event_evidence = evidence_by_event.get(event_id, [])
            event_evidence_ids = _unique_strings(
                item.get("evidence_id")
                for item in event_evidence
                if isinstance(item, Mapping)
            )
            evidence_texts = [
                _clean_display(
                    item.get("text")
                    or item.get("source_excerpt")
                    or item.get("claim")
                    or item.get("statement"),
                    800,
                )
                for item in event_evidence
                if isinstance(item, Mapping)
            ]
            evidence_texts = [text for text in evidence_texts if text]

            actors = _actor_names(event)
            raw_summary = _clean_display(event.get("summary"), 900)
            raw_title = _clean_display(event.get("title"), 500)
            base = _best_event_text(
                [
                    raw_summary,
                    raw_title,
                    *evidence_texts[:2],
                ],
                actors=actors,
            )
            if not base:
                base = raw_title or raw_summary or (evidence_texts[0] if evidence_texts else "")
            if not base:
                continue

            # Defense in depth for cached/pre-v5.2 extraction output.
            action_text = " ".join(
                [
                    _clean(event.get("event_type")),
                    raw_title,
                    raw_summary,
                ]
            )
            if material_action.search(action_text) and not actors:
                logger.warning(
                    "Material event omitted from landscape because actor attribution is missing. event_id=%s",
                    event_id,
                )
                continue

            details: list[str] = [base]

            if actors and not any(actor.casefold() in base.casefold() for actor in actors):
                details.append("Named actor(s): " + ", ".join(actors) + ".")

            quantities = _quantitative_details(event)
            if quantities:
                details.append("Key quantified detail(s): " + "; ".join(quantities) + ".")

            event_time_text = _clean(event.get("event_time_text"))
            if event_time_text and event_time_text.casefold() not in " ".join(details).casefold():
                details.append("Timing stated in source: " + event_time_text + ".")

            if evidence_texts:
                # Add one distinct exact evidence anchor when the event summary itself
                # is sparse.  This improves human comprehension without inventing prose.
                anchor = evidence_texts[0]
                if (
                    len(base) < 220
                    and anchor.casefold() not in base.casefold()
                    and base.casefold() not in anchor.casefold()
                ):
                    clean_anchor = _best_event_text([anchor], actors=actors)
                    if clean_anchor and clean_anchor.casefold() not in base.casefold():
                        details.append(clean_anchor)

            text_value = _clean_display(" ".join(details), 1500)
            key = text_value.casefold()
            if not key or key in seen_text:
                continue
            seen_text.add(key)

            output.append(
                {
                    "event_id": event_id,
                    "underlying_event_id": underlying_event_id,
                    "member_event_ids": [event_id],
                    "underlying_event_member_count": 1,
                    "text": text_value,
                    "title": raw_title or base,
                    "summary": raw_summary,
                    "event_type": event.get("event_type"),
                    "temporal_status": event.get("temporal_status"),
                    "event_time_text": event_time_text,
                    "source_id": event.get("source_id"),
                    "actors": actors,
                    "quantitative_claims": _json_safe(event.get("quantitative_claims", []) or []),
                    "evidence_ids": event_evidence_ids,
                }
            )
            underlying_output_index[underlying_event_id] = len(output) - 1

            if len(output) >= maximum:
                break

        # V3.16 coverage backfill -------------------------------------------------
        # The curated pass above is intentionally selective for readability, but
        # report integrity has a stronger contract: every evidence-backed event
        # examined by synthesis must be represented in Event Landscape.  Older
        # filters (future/prospective taxonomy, missing actor attribution, sparse
        # text, duplicate display text, or the soft maximum) must therefore never
        # make a considered event invisible.
        #
        # Backfill is deterministic and evidence-only.  It does not promote an
        # event into a strategic insight and it never invents an actor or claim.
        represented_event_ids: set[str] = set()
        underlying_index: dict[str, int] = {}
        for idx, item in enumerate(output):
            if not isinstance(item, Mapping):
                continue
            underlying_id = _clean(item.get("underlying_event_id")).upper()
            if underlying_id:
                underlying_index[underlying_id] = idx
            event_id = _clean(item.get("event_id")).upper()
            if event_id:
                represented_event_ids.add(event_id)
            represented_event_ids.update(
                _clean(value).upper()
                for value in (item.get("member_event_ids", []) or [])
                if _clean(value)
            )

        for event_id in evidence_backed_ids:
            event_key = _clean(event_id).upper()
            if not event_key or event_key in represented_event_ids:
                continue

            event = event_by_id.get(event_id) or event_by_id.get(event_key)
            if not isinstance(event, Mapping):
                # Evidence can theoretically reference an event omitted from the
                # supplied event payload.  Represent the evidence reference itself
                # so the integrity contract remains explicit and inspectable.
                event_evidence = evidence_by_event.get(event_id, [])
                event_evidence_ids = _unique_strings(
                    item.get("evidence_id")
                    for item in event_evidence
                    if isinstance(item, Mapping)
                )
                evidence_texts = [
                    _clean_display(
                        item.get("text")
                        or item.get("source_excerpt")
                        or item.get("claim")
                        or item.get("statement"),
                        800,
                    )
                    for item in event_evidence
                    if isinstance(item, Mapping)
                ]
                evidence_texts = [value for value in evidence_texts if value]
                text_value = evidence_texts[0] if evidence_texts else (
                    "Evidence-backed event was considered, but its structured event record was unavailable."
                )
                output.append({
                    "event_id": event_id,
                    "underlying_event_id": event_key,
                    "member_event_ids": [event_id],
                    "underlying_event_member_count": 1,
                    "text": text_value,
                    "title": "",
                    "summary": "",
                    "event_type": None,
                    "temporal_status": None,
                    "event_time_text": "",
                    "source_id": (event_evidence[0].get("source_id") if event_evidence else None),
                    "actors": [],
                    "actor_attribution_status": "unavailable",
                    "quantitative_claims": [],
                    "evidence_ids": event_evidence_ids,
                    "coverage_backfill": True,
                })
                represented_event_ids.add(event_key)
                continue

            underlying_event_id = _clean(
                event.get("underlying_event_id") or event_id
            ).upper()
            event_evidence = evidence_by_event.get(event_id, [])
            event_evidence_ids = _unique_strings(
                item.get("evidence_id")
                for item in event_evidence
                if isinstance(item, Mapping)
            )

            # If another member of the same underlying event is already visible,
            # merge this event into that summary rather than adding a duplicate row.
            if underlying_event_id in underlying_index:
                idx = underlying_index[underlying_event_id]
                output[idx]["member_event_ids"] = _unique_strings(
                    list(output[idx].get("member_event_ids", []) or []) + [event_id]
                )
                output[idx]["evidence_ids"] = _unique_strings(
                    list(output[idx].get("evidence_ids", []) or []) + event_evidence_ids
                )
                output[idx]["underlying_event_member_count"] = len(
                    output[idx]["member_event_ids"]
                )
                output[idx]["coverage_backfill"] = bool(
                    output[idx].get("coverage_backfill")
                ) or True
                represented_event_ids.add(event_key)
                continue

            actors = _actor_names(event)
            raw_summary = _clean_display(event.get("summary"), 900)
            raw_title = _clean_display(event.get("title"), 500)
            evidence_texts = [
                _clean_display(
                    item.get("text")
                    or item.get("source_excerpt")
                    or item.get("claim")
                    or item.get("statement"),
                    800,
                )
                for item in event_evidence
                if isinstance(item, Mapping)
            ]
            evidence_texts = [value for value in evidence_texts if value]
            base = _best_event_text(
                [raw_summary, raw_title, *evidence_texts[:2]],
                actors=actors,
            )
            if not base:
                base = raw_title or raw_summary or (evidence_texts[0] if evidence_texts else "")
            if not base:
                base = "Evidence-backed event considered by synthesis; no displayable source excerpt was available."

            details = [base]
            if actors and not any(actor.casefold() in base.casefold() for actor in actors):
                details.append("Named actor(s): " + ", ".join(actors) + ".")
            elif not actors:
                details.append("Actor attribution unavailable in the extracted event record.")

            quantities = _quantitative_details(event)
            if quantities:
                details.append("Key quantified detail(s): " + "; ".join(quantities) + ".")

            event_time_text = _clean(event.get("event_time_text"))
            if event_time_text and event_time_text.casefold() not in " ".join(details).casefold():
                details.append("Timing stated in source: " + event_time_text + ".")

            text_value = _clean_display(" ".join(details), 1500) or base
            output.append({
                "event_id": event_id,
                "underlying_event_id": underlying_event_id,
                "member_event_ids": [event_id],
                "underlying_event_member_count": 1,
                "text": text_value,
                "title": raw_title or base,
                "summary": raw_summary,
                "event_type": event.get("event_type"),
                "temporal_status": event.get("temporal_status"),
                "event_time_text": event_time_text,
                "source_id": event.get("source_id"),
                "actors": actors,
                "actor_attribution_status": "available" if actors else "unavailable",
                "quantitative_claims": _json_safe(event.get("quantitative_claims", []) or []),
                "evidence_ids": event_evidence_ids,
                "coverage_backfill": True,
            })
            underlying_index[underlying_event_id] = len(output) - 1
            represented_event_ids.add(event_key)

        for item in output:
            if not isinstance(item, dict):
                continue
            event_id = _clean(item.get("event_id"))
            event = event_by_id.get(event_id) or event_by_id.get(_clean(event_id).upper())
            if isinstance(event, Mapping):
                item["strategic_cluster_id"] = _clean(event.get("strategic_cluster_id"))
                item["strategic_event_class"] = _clean(event.get("strategic_event_class"))
                item["strategic_topic_relevance_score"] = event.get(
                    "strategic_topic_relevance_score"
                )
                item["strategic_materiality_score"] = event.get(
                    "strategic_materiality_score"
                )

        return output

    @staticmethod
    def _collect_report_cited_evidence_ids(report: Mapping[str, Any]) -> list[str]:
        """Collect analytical citations from every reader-visible report section."""
        sections = (
            "connections",
            "what_is_happening_underneath",
            "hidden_insights",
            "second_order_effects",
            "counterevidence_and_alternative_explanations",
            "what_to_watch",
        )
        output: list[str] = []
        for section_name in sections:
            for item in report.get(section_name, []) or []:
                if not isinstance(item, Mapping):
                    continue
                for key in (
                    "evidence_ids", "supporting_evidence_ids",
                    "counterevidence_ids", "alternative_evidence_ids",
                ):
                    output.extend(_unique_strings(item.get(key, []) or []))
        return _unique_strings(output)

    @staticmethod
    def _report_citation_integrity(
        *,
        report: Mapping[str, Any],
        evidence: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        evidence_event_by_id = {
            _clean(item.get("evidence_id")).upper(): _clean(item.get("event_id")).upper()
            for item in evidence
            if isinstance(item, Mapping) and _clean(item.get("evidence_id"))
        }
        cited_evidence_ids = [
            value.upper()
            for value in ResearchOrchestratorService._collect_report_cited_evidence_ids(report)
            if _clean(value)
        ]
        landscape_event_ids: set[str] = set()
        for item in report.get("event_landscape", []) or []:
            if not isinstance(item, Mapping):
                continue
            event_id = _clean(item.get("event_id")).upper()
            if event_id:
                landscape_event_ids.add(event_id)
            landscape_event_ids.update(
                _clean(value).upper()
                for value in (item.get("member_event_ids", []) or [])
                if _clean(value)
            )
        unknown_evidence_ids = sorted(
            evidence_id
            for evidence_id in cited_evidence_ids
            if evidence_id not in evidence_event_by_id
        )
        cited_event_ids = {
            evidence_event_by_id.get(evidence_id, "")
            for evidence_id in cited_evidence_ids
            if evidence_event_by_id.get(evidence_id, "")
        }
        orphaned = sorted(
            event_id
            for event_id in cited_event_ids
            if event_id not in landscape_event_ids
        )
        considered_event_ids = {
            _clean(item.get("event_id")).upper()
            for item in evidence
            if isinstance(item, Mapping) and _clean(item.get("event_id"))
        }
        unrepresented_considered_events = sorted(
            event_id for event_id in considered_event_ids
            if event_id not in landscape_event_ids
        )
        return {
            "landscape_event_count": len(landscape_event_ids),
            "cited_evidence_count": len(set(cited_evidence_ids)),
            "cited_event_count": len(cited_event_ids),
            "unknown_cited_evidence_ids": unknown_evidence_ids,
            "orphaned_cited_event_ids": orphaned,
            "considered_event_count": len(considered_event_ids),
            "unrepresented_considered_event_ids": unrepresented_considered_events,
            "ok": (
                not unknown_evidence_ids
                and not orphaned
                and not unrepresented_considered_events
            ),
        }

    def _build_report(
        self,
        *,
        topic: str,
        run_id: str,
        depth: str,
        events: Sequence[
            Mapping[str, Any]
        ],
        evidence: Sequence[
            Mapping[str, Any]
        ],
        constraints: Any,
        graph_context: Mapping[
            str,
            Any,
        ],
        insights: Sequence[
            Mapping[str, Any]
        ],
        grounding: Mapping[
            str,
            Any,
        ],
        strategic_connections: Optional[
            Sequence[Mapping[str, Any]]
        ] = None,
    ) -> dict[str, Any]:

        hidden_insights: list[
            dict[str, Any]
        ] = []

        strategic_findings: list[
            dict[str, Any]
        ] = []

        second_order_effects: list[
            Any
        ] = []

        alternatives: list[
            Any
        ] = []

        watch_signals: list[
            Any
        ] = []

        critical_unknowns: list[
            Any
        ] = []

        evidence_lookup = {
            _clean(item.get("evidence_id")): item
            for item in evidence
            if isinstance(item, Mapping)
            and _clean(item.get("evidence_id"))
        }

        for insight in insights:
            text = _clean(
                insight.get(
                    "insight"
                )
                or insight.get(
                    "text"
                )
            )

            evidence_ids = (
                self
                ._insight_evidence_ids(
                    insight
                )
            )

            insight_metadata = (
                insight.get("metadata")
                if isinstance(insight.get("metadata"), Mapping)
                else {}
            )
            finding_tier = self._finding_tier(insight)
            publish_hidden = finding_tier == "hidden_insight"
            finding_record = {
                "insight_id": insight.get("insight_id"),
                "insight_revision_id": insight.get("insight_revision_id"),
                "finding_tier": finding_tier,
                "insight_type": insight.get("insight_type"),
                "text": text,
                "evidence_ids": evidence_ids,
                "quality": insight.get("quality_assessment", {}),
                "scope_validation": _json_safe(
                    insight.get("scope_validation")
                    or insight_metadata.get("scope_validation", {})
                ),
                "insight_mode": _clean(insight_metadata.get("insight_mode")),
                "tier_reason": _clean(insight_metadata.get("finding_tier_reason")),
                "hidden_insight_rejection_reason": _clean(
                    insight_metadata.get("hidden_insight_rejection_reason")
                ),
                "confidence_ceiling": _clean(
                    insight_metadata.get("finding_tier_confidence_ceiling")
                ),
                "novel_proposition": _clean(insight_metadata.get("novel_proposition")),
                "triggering_anomaly": _clean(insight_metadata.get("triggering_anomaly")),
                "insight_quality_check": _json_safe(
                    insight_metadata.get("insight_quality_check", {})
                ),
                "derivation_summary": _json_safe(
                    insight_metadata.get("insight_derivation", {})
                ),
                "likelihood_assessment": _json_safe(
                    insight_metadata.get("likelihood_assessment", {})
                ),
                "confidence_dimensions": _json_safe(
                    insight_metadata.get("confidence_dimensions", {})
                ),
            }
            if text:
                strategic_findings.append(finding_record)
            if text and publish_hidden:
                hidden_insights.append(dict(finding_record))


            value = _clean(
                insight.get(
                    "second_order_effect"
                )
            )

            if value and publish_hidden:
                second_order_effects.append(
                    {
                        "text": value,
                        "insight_id": (
                            insight.get(
                                "insight_id"
                            )
                        ),
                        "evidence_ids": (
                            evidence_ids
                        ),
                    }
                )

            for alternative in (
                insight.get(
                    "alternative_explanations",
                    [],
                )
                or []
            ):
                alternatives.append(
                    _json_safe(
                        alternative
                    )
                )

            for counter_id in _unique_strings(
                insight.get("counterevidence_ids", []) or []
            ):
                record = evidence_lookup.get(counter_id, {})
                if not isinstance(record, Mapping):
                    continue
                counter_text = _clean(
                    record.get("text")
                    or record.get("source_excerpt")
                    or record.get("claim")
                    or record.get("statement")
                )
                if counter_text:
                    alternatives.append({
                        "type": "counterevidence",
                        "text": counter_text,
                        "evidence_ids": [counter_id],
                        "source_id": record.get("source_id"),
                    })

            for counter_id in _unique_strings(
                insight.get("counter_incentive_evidence_ids", []) or []
            ):
                record = evidence_lookup.get(counter_id, {})
                if not isinstance(record, Mapping):
                    continue
                counter_text = _clean(
                    record.get("text")
                    or record.get("source_excerpt")
                    or record.get("claim")
                    or record.get("statement")
                )
                if counter_text:
                    alternatives.append({
                        "type": "counter_incentive",
                        "text": counter_text,
                        "evidence_ids": [counter_id],
                        "source_id": record.get("source_id"),
                    })

            for unknown in (
                insight.get(
                    "critical_unknowns",
                    [],
                )
                or []
            ):
                cleaned = _clean(
                    unknown
                )

                if cleaned:
                    critical_unknowns.append(
                        cleaned
                    )

            for signal in (
                insight.get(
                    "watch_signals",
                    [],
                )
                or []
            ):
                watch_signals.append(
                    _json_safe(
                        signal
                    )
                )

        evidence_trail = [
            {
                "evidence_id": (
                    item.get(
                        "evidence_id"
                    )
                ),
                "evidence_type": (
                    item.get(
                        "evidence_type"
                    )
                ),
                "text": (
                    item.get(
                        "text"
                    )
                ),
                "source_excerpt": (
                    item.get(
                        "source_excerpt"
                    )
                ),
                "source_id": (
                    item.get(
                        "source_id"
                    )
                ),
                "event_id": (
                    item.get(
                        "event_id"
                    )
                ),
                "claim_status": (
                    item.get(
                        "claim_status"
                    )
                ),
                "temporal_status": (
                    item.get(
                        "temporal_status"
                    )
                ),
            }
            for item
            in evidence
        ]

        if isinstance(graph_context, Mapping):
            relationships = list(graph_context.get("relationships", []) or [])
        else:
            relationships = []

        # Preserve the StrategicInferenceAgent's explicit top-level Connections
        # instead of reconstructing Connections from the mechanism text.
        for connection in strategic_connections or []:
            if not isinstance(connection, Mapping):
                continue
            statement = _clean(
                connection.get("statement")
                or connection.get("text")
                or connection.get("connection")
            )
            evidence_ids = _unique_strings(
                connection.get("evidence_ids", []) or []
            )
            if not statement or not evidence_ids:
                continue
            relationships.append({
                "text": statement,
                "connection": statement,
                "connection_id": connection.get("connection_id"),
                "connection_type": (
                    connection.get("connection_type")
                    or "cross_event_inference"
                ),
                "event_ids": _json_safe(
                    connection.get("event_ids", []) or []
                ),
                "evidence_ids": evidence_ids,
                "support_level": connection.get("support_level"),
                "uncertainty": connection.get("uncertainty"),
            })

        underlying = list(self._constraint_list(constraints) or [])

        # Fast mode may skip graph projection and ConstraintAgent, but it has
        # already synthesized cross-event mechanisms. Surface those mechanisms
        # in the canonical report sections instead of returning blank tabs.
        for insight in insights:
            if not isinstance(insight, Mapping):
                continue
            evidence_ids = self._insight_evidence_ids(insight)
            mechanism_value = insight.get("mechanism")
            if isinstance(mechanism_value, Mapping):
                mechanism_text = _clean(
                    mechanism_value.get("description")
                    or mechanism_value.get("text")
                    or mechanism_value.get("mechanism")
                )
            else:
                mechanism_text = _clean(mechanism_value)
            why_it_matters = _clean(insight.get("why_it_matters"))
            insight_id = _clean(insight.get("insight_id"))

            connection_text = _clean(
                insight.get("connection")
            )

            finding_tier = self._finding_tier(insight)
            if connection_text:
                relationships.append({
                    "text": connection_text,
                    "connection": connection_text,
                    "insight_id": insight_id,
                    "evidence_ids": evidence_ids,
                    "connection_type": "cross_event_inference",
                    "finding_tier": finding_tier,
                })

            if finding_tier == "strategic_connection" and _clean(insight.get("insight") or insight.get("text")):
                relationships.append({
                    "text": _clean(insight.get("insight") or insight.get("text")),
                    "connection": _clean(insight.get("insight") or insight.get("text")),
                    "insight_id": insight_id,
                    "evidence_ids": evidence_ids,
                    "connection_type": "strategic_connection",
                    "finding_tier": finding_tier,
                })

            if finding_tier == "early_signal" and _clean(insight.get("insight") or insight.get("text")):
                watch_signals.append({
                    "signal": _clean(insight.get("insight") or insight.get("text")),
                    "text": _clean(insight.get("insight") or insight.get("text")),
                    "why_it_matters": why_it_matters,
                    "insight_id": insight_id,
                    "evidence_ids": evidence_ids,
                    "finding_tier": finding_tier,
                    "status": "emerging",
                })

            if mechanism_text or why_it_matters:
                parts = []
                if mechanism_text:
                    parts.append(mechanism_text)
                if why_it_matters:
                    parts.append(why_it_matters)
                underlying_text = " ".join(parts)
                if finding_tier == "underlying_dynamic":
                    finding_text = _clean(insight.get("insight") or insight.get("text"))
                    if finding_text:
                        underlying_text = finding_text
                underlying.append({
                    "text": underlying_text,
                    "description": underlying_text,
                    "insight_id": insight_id,
                    "evidence_ids": evidence_ids,
                    "analysis_type": "underlying_dynamic",
                    "finding_tier": finding_tier,
                })

        # Deduplicate textual relationship/underlying records.
        def _dedupe_report_items(values: Sequence[Any]) -> list[Any]:
            output = []
            seen = set()
            for value in values:
                if isinstance(value, Mapping):
                    key = _clean(
                        value.get("text")
                        or value.get("connection")
                        or value.get("description")
                        or value.get("statement")
                    ).casefold()
                else:
                    key = _clean(value).casefold()
                if not key:
                    continue
                if key in seen:
                    continue
                seen.add(key)
                output.append(value)
            return output

        relationships = _dedupe_report_items(relationships)
        underlying = _dedupe_report_items(underlying)
        second_order_effects = _dedupe_report_items(second_order_effects)
        alternatives = _dedupe_report_items(alternatives)
        watch_signals = _dedupe_report_items(watch_signals)

        if not second_order_effects:
            second_order_effects = [
                {
                    "status": (
                        "not_established"
                    ),
                    "note": (
                        "No evidence-backed second-order effect was established for the released insight."
                    ),
                }
            ]

        if not alternatives:
            alternatives = [
                {
                    "status": (
                        "not_established"
                    ),
                    "note": (
                        "No independent counterevidence or alternative explanation was established."
                    ),
                }
            ]

        if not watch_signals:
            watch_signals = [
                {
                    "status": (
                        "not_established"
                    ),
                    "note": (
                        "No evidence-backed watch signal was established."
                    ),
                }
            ]

        if not critical_unknowns:
            critical_unknowns = [
                (
                    "Broader or newer evidence may materially change the conclusion."
                )
            ]

        # V3.13 auditability: every analytical citation must be represented by
        # an event visible in Event Landscape.  Include connection/underlying/
        # second-order drivers as well as Hidden Insight drivers, and expand the
        # cap when necessary instead of silently hiding cited events.
        landscape_drivers: list[Mapping[str, Any]] = []
        for collection in (
            list(insights),
            strategic_findings,
            relationships,
            underlying,
            second_order_effects,
            alternatives,
            watch_signals,
        ):
            for item in collection or []:
                if isinstance(item, Mapping):
                    landscape_drivers.append(item)

        evidence_event_lookup = {
            _clean(item.get("evidence_id")).upper(): _clean(item.get("event_id")).upper()
            for item in evidence
            if isinstance(item, Mapping) and _clean(item.get("evidence_id"))
        }
        pre_landscape_cited_evidence = _unique_strings(
            evidence_id
            for item in landscape_drivers
            for evidence_id in (
                item.get("evidence_ids", [])
                or item.get("supporting_evidence_ids", [])
                or []
            )
            if isinstance(item, Mapping)
        )
        pre_landscape_cited_events = {
            evidence_event_lookup.get(_clean(evidence_id).upper(), "")
            for evidence_id in pre_landscape_cited_evidence
            if evidence_event_lookup.get(_clean(evidence_id).upper(), "")
        }
        for item in landscape_drivers:
            pre_landscape_cited_events.update(
                _clean(value).upper()
                for value in (item.get("event_ids", []) or [])
                if _clean(value)
            )
        considered_event_ids = {
            _clean(item.get("event_id")).upper()
            for item in evidence
            if isinstance(item, Mapping) and _clean(item.get("event_id"))
        }
        event_underlying_lookup = {
            _clean(item.get("event_id")).upper(): _clean(item.get("underlying_event_id") or item.get("event_id")).upper()
            for item in events
            if isinstance(item, Mapping) and _clean(item.get("event_id"))
        }
        considered_underlying_ids = {
            event_underlying_lookup.get(event_id, event_id)
            for event_id in considered_event_ids
        }
        landscape_maximum = max(
            6 if _enum(depth) == "fast" else 8,
            len(pre_landscape_cited_events),
            len(considered_underlying_ids),
        )
        material_landscape = self._material_event_landscape(
            events=events,
            evidence=evidence,
            insights=landscape_drivers,
            maximum=landscape_maximum,
        )

        report = {
            "research_question": (
                topic
            ),
            "run_id": (
                run_id
            ),
            "research_depth": (
                depth
            ),
            "executive_synthesis": (
                strategic_findings[:2]
            ),
            "event_landscape": material_landscape,
            "event_landscape_metadata": {
                "raw_considered_event_count": len(considered_event_ids),
                "underlying_event_count": len(considered_underlying_ids),
                "all_considered_events_represented": None,
                "representation_policy": "every_evidence_backed_event_or_underlying_event_summary_with_deterministic_backfill",
            },
            "connections": (
                relationships
            ),
            "what_is_happening_underneath": (
                underlying
            ),
            "hidden_insights": (
                hidden_insights
            ),
            "strategic_findings": (
                strategic_findings
            ),
            "second_order_effects": (
                second_order_effects
            ),
            "counterevidence_and_alternative_explanations": (
                alternatives
            ),
            "what_to_watch": (
                watch_signals
            ),
            "evidence_trail": (
                evidence_trail
            ),
            "critical_unknowns": (
                _unique_strings(
                    critical_unknowns
                )
            ),
            "grounding_by_insight": (
                _json_safe(
                    grounding
                )
            ),
            "analysis_metadata": {
                "analysis_mode": _enum(depth) or depth,
                "synthesis_mode": (
                    "bounded_evidence_conditioned_llm"
                    if _enum(depth) == "fast"
                    else "deep_strategic_inference"
                ),
                "fallback_used": any(
                    bool(insight.get("fallback_used"))
                    for insight in insights
                    if isinstance(insight, Mapping)
                ),
                "degraded": any(
                    bool(insight.get("degraded"))
                    for insight in insights
                    if isinstance(insight, Mapping)
                ),
                "insight_count": len(hidden_insights),
                "publishable_insight_count": len(hidden_insights),
                "strategic_finding_count": len(strategic_findings),
                "finding_tier_counts": {
                    tier: sum(1 for item in strategic_findings if _enum(item.get("finding_tier")) == tier)
                    for tier in ("hidden_insight", "underlying_dynamic", "strategic_connection", "early_signal")
                },
                "top_finding_tier": (
                    _clean(strategic_findings[0].get("finding_tier"))
                    if strategic_findings else ""
                ),
                "evidence_record_count": len(evidence),
                "event_count": len(events),
            },
            "epistemic_note": (
                "Facts, connections, "
                "mechanisms, inferences and "
                "uncertainties are not treated "
                "as equivalent claims."
            ),
        }

        citation_integrity = self._report_citation_integrity(
            report=report,
            evidence=evidence,
        )
        report.setdefault("analysis_metadata", {})[
            "citation_integrity"
        ] = _json_safe(citation_integrity)
        report.setdefault("event_landscape_metadata", {})[
            "all_considered_events_represented"
        ] = not bool(citation_integrity.get("unrepresented_considered_event_ids"))
        logger.info(
            "report_citation_integrity=%s",
            json.dumps(_json_safe(citation_integrity), ensure_ascii=False, default=str),
        )
        if not citation_integrity.get("ok"):
            raise ResearchPipelineError(
                "Report citation integrity failed before release. diagnostics="
                + json.dumps(
                    _json_safe(citation_integrity),
                    ensure_ascii=False,
                    default=str,
                )
            )

        report["outcome"] = (
            "hidden_insight_released"
            if hidden_insights
            else "strategic_finding_released"
        )
        report.setdefault("analysis_metadata", {})["outcome"] = report["outcome"]

        report[
            "underlying_dynamics"
        ] = report[
            "what_is_happening_underneath"
        ]

        report[
            "counterevidence_and_alternatives"
        ] = report[
            "counterevidence_and_alternative_explanations"
        ]

        report[
            "uncertainty"
        ] = report[
            "critical_unknowns"
        ]

        return report

    def _red_team(
        self,
        *,
        topic: str,
        evidence: Sequence[
            Mapping[str, Any]
        ],
        constraints: Any,
        report: Mapping[
            str,
            Any,
        ],
        grounding: Mapping[
            str,
            Any,
        ],
    ) -> dict[str, Any]:

        result = (
            self._call_method(
                self.red_team_agent,
                (
                    "review",
                ),
                {
                    "topic": topic,
                    "research_question": topic,
                    "evidence": evidence,
                    "evidence_records": evidence,
                    "constraints": constraints,
                    "analysis": report,
                    "report": report,
                    "grounding": grounding,
                    "grounding_results": grounding,
                },
            )
        )

        return (
            _as_mapping(
                result
            )
        )

    def _release_decision(
        self,
        *,
        blueprint: Mapping[
            str,
            Any,
        ],
        report: Mapping[
            str,
            Any,
        ],
        grounding: Mapping[
            str,
            Any,
        ],
        red_team: Mapping[
            str,
            Any,
        ],
        state: Mapping[
            str,
            Any,
        ],
    ) -> dict[str, Any]:

        if (
            self.policy_enforcement_service
            is None
        ):
            return {
                "allowed": False,
                "release_allowed": False,
                "authorizes_release": False,
                "decision": (
                    "block"
                ),
                "release_decision_id": (
                    "RELEASE-"
                    + uuid.uuid4()
                    .hex[:24]
                    .upper()
                ),
                "authority": (
                    "policy_enforcement_unavailable"
                ),
                "reasons": [
                    (
                        "PolicyEnforcementService "
                        "is not configured; release "
                        "cannot be authorized."
                    )
                ],
            }

        insight_grounding = (
            state.get(
                "grounding",
                {},
            )
        )

        policy_grounding = (
            self
            ._policy_grounding_assessments(
                insight_grounding
            )
        )

        policy_red_team = (
            self
            ._policy_red_team_assessments(
                red_team,
                report=report,
            )
        )

        try:
            result = (
                self._call_method(
                    self
                    .policy_enforcement_service,
                    (
                        "validate_release",
                    ),
                    {
                        "blueprint": blueprint,
                        "report": report,
                        "grounding_assessments": (
                            policy_grounding
                        ),
                        "red_team_assessments": (
                            policy_red_team
                        ),
                        "reasoning_context": (
                            None
                        ),
                        "quality_metrics": (
                            None
                        ),
                        "audit_receipt": (
                            None
                        ),
                    },
                )
            )

        except Exception as error:
            logger.exception(
                (
                    "Policy release validation "
                    "failed."
                )
            )

            return {
                "allowed": False,
                "release_allowed": False,
                "authorizes_release": False,
                "decision": (
                    "block"
                ),
                "release_decision_id": (
                    "RELEASE-"
                    + uuid.uuid4()
                    .hex[:24]
                    .upper()
                ),
                "authority": (
                    "policy_validation_error"
                ),
                "reasons": [
                    (
                        f"{type(error).__name__}: "
                        f"{error}"
                    )
                ],
            }

        result_map = (
            _as_mapping(
                result
            )
        )

        if not result_map:
            return {
                "allowed": False,
                "release_allowed": False,
                "authorizes_release": False,
                "decision": (
                    "block"
                ),
                "release_decision_id": (
                    "RELEASE-"
                    + uuid.uuid4()
                    .hex[:24]
                    .upper()
                ),
                "authority": (
                    "empty_policy_decision"
                ),
                "reasons": [
                    (
                        "PolicyEnforcementService "
                        "returned an empty release "
                        "decision."
                    )
                ],
            }

        return (
            self._normalize_release_decision(
                result_map
            )
        )

    @staticmethod
    def _policy_grounding_assessments(
        grounding: Any,
    ) -> list[dict[str, Any]]:

        if not isinstance(
            grounding,
            Mapping,
        ):
            return []

        assessments: list[
            dict[str, Any]
        ] = []

        for (
            insight_key,
            raw,
        ) in grounding.items():

            result = (
                _as_mapping(
                    raw
                )
            )

            if not result:
                continue

            statuses = [
                _enum(
                    claim.get(
                        "grounding_status"
                    )
                    or claim.get(
                        "epistemic_status"
                    )
                )
                for claim
                in (
                    result.get(
                        "claims",
                        [],
                    )
                    or []
                )
                if isinstance(
                    claim,
                    Mapping,
                )
            ]

            statuses = [
                status
                for status
                in statuses
                if status
            ]

            if (
                "contradicted"
                in statuses
            ):
                epistemic_status = (
                    "contradicted"
                )

            elif (
                "unsupported"
                in statuses
                and not any(
                    status
                    in {
                        "supported",
                        "partially_supported",
                        "inference_supported",
                    }
                    for status
                    in statuses
                )
            ):
                epistemic_status = (
                    "unsupported"
                )

            elif (
                "inference_supported"
                in statuses
            ):
                epistemic_status = (
                    "inference_supported"
                )

            elif (
                "partially_supported"
                in statuses
                or (
                    "unsupported"
                    in statuses
                    and "supported"
                    in statuses
                )
            ):
                epistemic_status = (
                    "partially_supported"
                )

            elif (
                statuses
                and all(
                    status
                    == "supported"
                    for status
                    in statuses
                )
            ):
                epistemic_status = (
                    "supported"
                )

            else:
                epistemic_status = (
                    "unresolved"
                )

            evidence_ids: list[
                str
            ] = []

            invalid_ids: list[
                str
            ] = []

            for claim in (
                result.get(
                    "claims",
                    [],
                )
                or []
            ):
                if not isinstance(
                    claim,
                    Mapping,
                ):
                    continue

                evidence_ids.extend(
                    claim.get(
                        "valid_evidence_ids",
                        [],
                    )
                    or claim.get(
                        "supporting_evidence_ids",
                        [],
                    )
                    or []
                )

                invalid_ids.extend(
                    claim.get(
                        "invalid_evidence_ids",
                        [],
                    )
                    or []
                )

            evidence_ids = (
                _unique_strings(
                    evidence_ids
                )
            )

            invalid_ids = (
                _unique_strings(
                    invalid_ids
                )
            )

            seed = (
                _clean(
                    insight_key
                )
                + "|"
                + epistemic_status
                + "|"
                + "|".join(
                    evidence_ids
                )
            )

            assessment_id = (
                "GROUNDING-"
                + uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    seed,
                )
                .hex[:24]
                .upper()
            )

            assessments.append(
                {
                    "grounding_assessment_id": (
                        assessment_id
                    ),
                    "insight_revision_id": (
                        _clean(
                            insight_key
                        )
                    ),
                    "review_status": (
                        _clean(
                            result.get(
                                "review_status"
                            )
                        )
                        or "complete"
                    ),
                    "epistemic_status": (
                        epistemic_status
                    ),
                    "grounding_status": (
                        epistemic_status
                    ),
                    "claims": (
                        _json_safe(
                            result.get(
                                "claims",
                                [],
                            )
                        )
                    ),
                    "evidence_manifest": {
                        "referenced_evidence_ids": (
                            evidence_ids
                        ),
                        "invalid_referenced_evidence_ids": (
                            invalid_ids
                        ),
                    },
                    "metadata": {
                        "search_performed": (
                            False
                        ),
                        "source_grounding_result": (
                            _json_safe(
                                result.get(
                                    "metadata",
                                    {},
                                )
                            )
                        ),
                    },
                }
            )

        return assessments

    @staticmethod
    def _policy_red_team_assessments(
        red_team: Any,
        *,
        report: Mapping[
            str,
            Any,
        ],
    ) -> list[dict[str, Any]]:

        result = (
            _as_mapping(
                red_team
            )
        )

        if not result:
            return []

        output = dict(
            result
        )

        assessment_id = _clean(
            output.get(
                "red_team_assessment_id"
            )
            or output.get(
                "assessment_id"
            )
        )

        if not assessment_id:
            seed = (
                _clean(
                    report.get(
                        "run_id"
                    )
                )
                + "|"
                + _clean(
                    output.get(
                        "status"
                    )
                    or output.get(
                        "review_status"
                    )
                )
            )

            assessment_id = (
                "REDTEAM-"
                + uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    seed,
                )
                .hex[:24]
                .upper()
            )

        output[
            "red_team_assessment_id"
        ] = assessment_id

        status = _enum(
            output.get(
                "status"
            )
            or output.get(
                "review_status"
            )
        )

        output[
            "red_team_complete"
        ] = bool(
            output.get(
                "red_team_complete"
            )
            is True
            or status
            in {
                "complete",
                "completed",
                "ok",
                "pass",
                "passed",
            }
        )

        metadata = (
            _as_mapping(
                output.get(
                    "metadata"
                )
            )
        )

        metadata.setdefault(
            "search_performed",
            False,
        )

        output[
            "metadata"
        ] = metadata

        return [
            output
        ]

    @staticmethod
    def _normalize_release_decision(
        value: Mapping[
            str,
            Any,
        ],
    ) -> dict[str, Any]:

        output = dict(
            value
        )

        release_allowed_raw = (
            output.get(
                "release_allowed"
            )
        )

        allowed_raw = (
            output.get(
                "allowed"
            )
        )

        authorizes_raw = (
            output.get(
                "authorizes_release"
            )
        )

        if (
            authorizes_raw
            is None
            and isinstance(
                output.get(
                    "metadata"
                ),
                Mapping,
            )
        ):
            authorizes_raw = (
                output[
                    "metadata"
                ].get(
                    "authorizes_release"
                )
            )

        release_allowed = (
            release_allowed_raw
            is True
            or (
                release_allowed_raw
                is None
                and allowed_raw
                is True
            )
        )

        authorizes_release = (
            authorizes_raw
            is True
        )

        allowed = bool(
            release_allowed
            and authorizes_release
        )

        decision_id = _clean(
            output.get(
                "release_decision_id"
            )
            or output.get(
                "decision_id"
            )
        )

        if not decision_id:
            decision_id = (
                "RELEASE-"
                + uuid.uuid4()
                .hex[:24]
                .upper()
            )

        output[
            "release_decision_id"
        ] = decision_id

        output[
            "decision_id"
        ] = (
            _clean(
                output.get(
                    "decision_id"
                )
            )
            or decision_id
        )

        output[
            "release_allowed"
        ] = release_allowed

        output[
            "authorizes_release"
        ] = authorizes_release

        output[
            "allowed"
        ] = allowed

        output.setdefault(
            "decision",
            (
                "allow"
                if allowed
                else "block"
            ),
        )

        output.setdefault(
            "authority",
            (
                "PolicyEnforcementService."
                "validate_release"
            ),
        )

        return output

    @staticmethod
    def _release_is_authorized(
        decision: Mapping[
            str,
            Any,
        ],
    ) -> bool:
        return bool(
            decision.get(
                "release_allowed"
            )
            is True
            and decision.get(
                "authorizes_release"
            )
            is True
        )

    @staticmethod
    def _fast_deferred_release_decision(
        *,
        report: Mapping[
            str,
            Any,
        ],
        evidence: Sequence[
            Mapping[str, Any]
        ],
        report_grounding: Mapping[
            str,
            Any,
        ],
        red_team: Mapping[
            str,
            Any,
        ],
        policy_decision: Mapping[
            str,
            Any,
        ],
    ) -> dict[str, Any]:
        """
        Temporary Fast-only release bridge.

        Actual displayed analytical claims must cite admitted EVID-* records.
        But a correctly abstaining Fast run is also releasable when there are no
        analytical claims and its material-event landscape is grounded.  The
        bridge therefore never requires the system to manufacture a Hidden
        Insight merely to make the UI persist a report.
        """

        valid_ids = {
            _clean(item.get("evidence_id"))
            for item in evidence
            if (
                isinstance(item, Mapping)
                and _clean(item.get("evidence_id"))
                .upper()
                .startswith("EVID-")
            )
        }
        valid_event_ids = {
            _clean(item.get("event_id"))
            for item in evidence
            if (
                isinstance(item, Mapping)
                and _clean(item.get("event_id"))
            )
        }

        grounding_status = _enum(
            report_grounding.get("review_status")
            or report_grounding.get("status")
        )
        grounding_ok = bool(
            grounding_status
            in {"complete", "ok", "pass", "passed"}
            and not (
                report_grounding.get(
                    "missing_or_invalid_evidence_links",
                    [],
                )
                or []
            )
        )

        red_team_status = _enum(
            red_team.get("review_status")
            or red_team.get("status")
        )
        critical = int(red_team.get("critical_issue_count", 0) or 0)
        high = int(red_team.get("high_issue_count", 0) or 0)
        red_team_ok = bool(
            red_team_status in {"complete", "ok", "pass", "passed"}
            and critical == 0
            and high == 0
        )

        displayed_claims: list[Mapping[str, Any]] = []

        def _append_claims(values: Any) -> None:
            for item in values or []:
                if not isinstance(item, Mapping):
                    continue
                if _enum(item.get("status")) in {
                    "not_established",
                    "not_established",
                    "abstained",
                    "insufficient_evidence",
                }:
                    continue
                value = _clean(
                    item.get("text")
                    or item.get("connection")
                    or item.get("description")
                    or item.get("statement")
                    or item.get("insight")
                )
                if value:
                    displayed_claims.append(item)

        _append_claims(report.get("hidden_insights", []))
        _append_claims(report.get("executive_synthesis", []))
        _append_claims([
            item
            for item in report.get("connections", []) or []
            if isinstance(item, Mapping)
            and (
                _enum(item.get("connection_type")) == "cross_event_inference"
                or (item.get("evidence_ids") or [])
            )
        ])
        _append_claims([
            item
            for item in report.get("what_is_happening_underneath", []) or []
            if isinstance(item, Mapping)
            and (
                _enum(item.get("analysis_type")) == "underlying_dynamic"
                or (item.get("evidence_ids") or [])
            )
        ])
        _append_claims(report.get("second_order_effects", []))

        claim_links_ok = True
        for item in displayed_claims:
            ids = _unique_strings(
                item.get("evidence_ids", [])
                or item.get("support_evidence_ids", [])
                or item.get("supporting_evidence_ids", [])
                or []
            )
            if (
                not ids
                or any(evidence_id not in valid_ids for evidence_id in ids)
            ):
                claim_links_ok = False
                break

        event_landscape = [
            item
            for item in report.get("event_landscape", []) or []
            if isinstance(item, Mapping)
        ]
        event_links_ok = bool(event_landscape) and all(
            _clean(item.get("event_id")) in valid_event_ids
            for item in event_landscape
            if _clean(item.get("event_id"))
        )

        honest_abstention = bool(
            not displayed_claims
            and report_grounding.get("honest_abstention") is True
            and event_links_ok
        )

        evidence_links_ok = bool(
            claim_links_ok
            and (
                bool(displayed_claims)
                or honest_abstention
            )
        )

        allowed = bool(
            grounding_ok
            and red_team_ok
            and evidence_links_ok
        )

        decision_id = (
            "FAST-DEFERRED-RELEASE-"
            + uuid.uuid4().hex[:16].upper()
        )

        if allowed and honest_abstention:
            reasons = [
                "Temporary Fast bridge allowed an honest abstention report: "
                "no unsupported analytical claim is displayed and every material "
                "event maps to admitted evidence."
            ]
        elif allowed:
            reasons = [
                "Temporary Fast bridge allowed release because report grounding "
                "is complete, Fast red-team has no critical/high issues, and "
                "every displayed analytical claim references admitted EVID-* records."
            ]
        else:
            reasons = [
                "Temporary Fast bridge blocked release because one or more "
                "evidence/grounding/red-team checks failed."
            ]

        return {
            "decision_id": decision_id,
            "release_decision_id": decision_id,
            "release_allowed": allowed,
            "authorizes_release": allowed,
            "allowed": allowed,
            "decision": "allow" if allowed else "block",
            "authority": "temporary_fast_evidence_grounding_redteam_bridge",
            "governance_deferred": True,
            "policy_decision_retained": _json_safe(policy_decision),
            "checks": {
                "report_grounding_complete": grounding_ok,
                "red_team_clean": red_team_ok,
                "displayed_claim_evidence_links_valid": claim_links_ok,
                "material_event_links_valid": event_links_ok,
                "honest_abstention": honest_abstention,
                # Backward-compatible key retained for diagnostics/UI code.
                "insight_evidence_links_valid": evidence_links_ok,
            },
            "reasons": reasons,
        }

    @staticmethod
    def _red_team_issue_count(
        red_team: Mapping[
            str,
            Any,
        ],
        severity: str,
    ) -> int:

        count = 0

        for issue in (
            red_team.get(
                "issues",
                [],
            )
            or []
        ):
            if (
                isinstance(
                    issue,
                    Mapping,
                )
                and _enum(
                    issue.get(
                        "severity"
                    )
                )
                == severity
            ):
                count += 1

        return count

    def _persist_insight_lifecycle(
        self,
        *,
        topic: str,
        run_id: str,
        insights: Sequence[
            Mapping[str, Any]
        ],
        grounding: Mapping[
            str,
            Any,
        ],
        state: dict[str, Any],
    ) -> None:

        if (
            self.insight_registry_service
            is None
        ):
            return

        registered = []

        for insight in insights:
            insight_id = _clean(
                insight.get(
                    "insight_id"
                )
            )

            revision_id = _clean(
                insight.get(
                    "insight_revision_id"
                )
            )

            text = _clean(
                insight.get(
                    "insight"
                )
                or insight.get(
                    "text"
                )
            )

            insight_type = _enum(
                insight.get(
                    "insight_type"
                )
                or "descriptive"
            )

            mechanism = (
                insight.get(
                    "mechanism"
                )
                or {}
            )

            if isinstance(
                mechanism,
                Mapping,
            ):
                mechanism_text = (
                    _clean(
                        mechanism.get(
                            "description"
                        )
                    )
                )
            else:
                mechanism_text = (
                    _clean(
                        mechanism
                    )
                )

            try:
                result = (
                    self._call_method(
                        self
                        .insight_registry_service,
                        (
                            "register_revision",
                            "create_insight",
                        ),
                        {
                            "revision": insight,
                            "insight_id": (
                                insight_id
                            ),
                            "insight_revision_id": (
                                revision_id
                            ),
                            "insight_type": (
                                insight_type
                            ),
                            "text": (
                                text
                            ),
                            "research_question": (
                                topic
                            ),
                            "run_id": (
                                run_id
                            ),
                            "supporting_evidence_ids": (
                                self
                                ._insight_evidence_ids(
                                    insight
                                )
                            ),
                            "counterevidence_ids": (
                                insight.get(
                                    "counterevidence_ids",
                                    [],
                                )
                            ),
                            "mechanism": (
                                mechanism_text
                            ),
                            "second_order_effect": (
                                insight.get(
                                    "second_order_effect",
                                    "",
                                )
                            ),
                            "critical_unknowns": (
                                insight.get(
                                    "critical_unknowns",
                                    [],
                                )
                            ),
                            "metadata": {
                                "canonical_revision": (
                                    _json_safe(
                                        insight
                                    )
                                )
                            },
                        },
                    )
                )

                registered.append(
                    _json_safe(
                        result
                    )
                )

            except Exception as error:
                logger.warning(
                    (
                        "Insight registry "
                        "persistence failed "
                        "for %s: %s"
                    ),
                    insight_id,
                    type(error).__name__,
                )

        state[
            "registered_insights"
        ] = registered

    def _persist_report(
        self,
        *,
        topic: str,
        report: Mapping[
            str,
            Any,
        ],
        release_decision: Mapping[
            str,
            Any,
        ],
    ) -> dict[str, Any]:

        if (
            self.report_service
            is None
        ):
            return {
                "saved": False,
                "state": "",
                "reason": (
                    "report_service_not_configured"
                ),
            }

        canonical_sections = {
            "event_landscape": (
                report.get(
                    "event_landscape",
                    [],
                )
            ),
            "connections": (
                report.get(
                    "connections",
                    [],
                )
            ),
            "what_is_happening_underneath": (
                report.get(
                    "what_is_happening_underneath",
                    [],
                )
            ),
            "hidden_insights": (
                report.get(
                    "hidden_insights",
                    [],
                )
            ),
            "strategic_findings": (
                report.get(
                    "strategic_findings",
                    [],
                )
            ),
            "second_order_effects": (
                report.get(
                    "second_order_effects",
                    [],
                )
            ),
            "counterevidence_and_alternative_explanations": (
                report.get(
                    "counterevidence_and_alternative_explanations",
                    [],
                )
            ),
            "what_to_watch": (
                report.get(
                    "what_to_watch",
                    [],
                )
            ),
            "evidence_trail": (
                report.get(
                    "evidence_trail",
                    [],
                )
            ),
            "critical_unknowns": (
                report.get(
                    "critical_unknowns",
                    [],
                )
            ),
            "grounding_by_insight": (
                _json_safe(
                    report.get(
                        "grounding_by_insight",
                        {},
                    )
                )
            ),
            "analysis_metadata": (
                _json_safe(
                    report.get(
                        "analysis_metadata",
                        {},
                    )
                )
            ),
        }

        persisted_findings = (
            report.get("strategic_findings", [])
            or report.get("hidden_insights", [])
            or []
        )
        insight_ids = (
            _unique_strings(
                item.get("insight_id")
                for item in persisted_findings
                if isinstance(item, Mapping)
            )
        )

        revision_ids = (
            _unique_strings(
                item.get("insight_revision_id")
                for item in persisted_findings
                if isinstance(item, Mapping)
            )
        )

        report_analysis_metadata = _as_mapping(report.get("analysis_metadata"))
        metadata = {
            "research_depth": (
                report.get(
                    "research_depth"
                )
            ),
            "epistemic_note": (
                report.get(
                    "epistemic_note"
                )
            ),
            "orchestrator_service_version": (
                SERVICE_VERSION
            ),
            "orchestrator_patch_build_id": (
                PATCH_BUILD_ID
            ),
            "research_outcome": (
                report.get("outcome")
                or report_analysis_metadata.get("outcome")
            ),
            "publishable_insight_count": report_analysis_metadata.get(
                "publishable_insight_count",
                report_analysis_metadata.get("insight_count"),
            ),
            "strategic_finding_count": report_analysis_metadata.get(
                "strategic_finding_count",
                len(report.get("strategic_findings", []) or []),
            ),
            "top_finding_tier": report_analysis_metadata.get("top_finding_tier"),
            "finding_tier_counts": _json_safe(
                report_analysis_metadata.get("finding_tier_counts") or {}
            ),
            "abstention_classification": (
                report.get("abstention_classification")
                or report_analysis_metadata.get("abstention_classification")
            ),
            "run_observability": _json_safe(
                report_analysis_metadata.get("run_observability") or {}
            ),
        }

        try:
            created = (
                self._call_method(
                    self.report_service,
                    (
                        "create_report",
                    ),
                    {
                        "research_question": (
                            topic
                        ),
                        "sections": (
                            canonical_sections
                        ),
                        "run_id": (
                            report.get(
                                "run_id",
                                "",
                            )
                        ),
                        "insight_ids": (
                            insight_ids
                        ),
                        "insight_revision_ids": (
                            revision_ids
                        ),
                        "blueprint_id": (
                            report.get(
                                "blueprint_id",
                                "",
                            )
                        ),
                        "metadata": (
                            metadata
                        ),
                    },
                )
            )

            created_map = (
                _as_mapping(
                    created
                )
            )

            report_id = _clean(
                created_map.get(
                    "report_id"
                )
            )

            if not report_id:
                raise ResearchPipelineError(
                    (
                        "ReportService."
                        "create_report() returned "
                        "no report_id."
                    )
                )

            applied = (
                self._call_method(
                    self.report_service,
                    (
                        "apply_release_decision",
                    ),
                    {
                        "report_id": (
                            report_id
                        ),
                        "release_decision": (
                            release_decision
                        ),
                    },
                )
            )

            applied_map = (
                _as_mapping(
                    applied
                )
            )

            final_state = (
                _enum(
                    applied_map.get(
                        "state"
                    )
                )
                or "blocked"
            )

            return {
                "saved": True,
                "report_id": (
                    report_id
                ),
                "report_revision_id": (
                    _clean(
                        applied_map.get(
                            "report_revision_id"
                        )
                        or created_map.get(
                            "report_revision_id"
                        )
                    )
                ),
                "state": (
                    final_state
                ),
                "released": (
                    final_state
                    == "released"
                ),
                "create_result": (
                    _json_safe(
                        created_map
                    )
                ),
                "release_result": (
                    _json_safe(
                        applied_map
                    )
                ),
            }

        except Exception as error:
            logger.exception(
                (
                    "Report persistence "
                    "failed."
                )
            )

            return {
                "saved": False,
                "state": "",
                "reason": (
                    type(error).__name__
                ),
                "message": (
                    str(error)
                ),
            }

    def _compile_blueprint(
        self,
        *,
        topic: str,
        profile: ResearchExecutionProfile,
        run_id: str,
    ) -> dict[str, Any]:

        if (
            self.blueprint_compiler
            is not None
            and self.policy_registry
            is not None
        ):
            try:
                policy_snapshot = (
                    self._call_method(
                        self.policy_registry,
                        (
                            "get_policy_snapshot",
                            "policy_snapshot",
                            "snapshot",
                        ),
                        {
                            "analysis_mode": (
                                "research"
                            ),
                            "research_depth": (
                                profile.name
                            ),
                        },
                    )
                )

                validated_intent = {
                    "intent_id": (
                        "INTENT-"
                        + run_id.removeprefix(
                            "RUN-"
                        )
                    ),
                    "intent_type": (
                        "research"
                    ),
                    "research_question": (
                        topic
                    ),
                    "analysis_mode": (
                        "research"
                    ),
                    "research_depth": (
                        profile.name
                    ),
                    "requested_output": (
                        "research_report"
                    ),
                    "constraints": {},
                    "source_preferences": {},
                    "insight_preferences": {},
                }

                result = (
                    self._call_method(
                        self.blueprint_compiler,
                        (
                            "compile",
                            "compile_blueprint",
                            "build",
                        ),
                        {
                            "validated_intent": (
                                validated_intent
                            ),
                            "policy_snapshot": (
                                _as_mapping(
                                    policy_snapshot
                                )
                            ),
                            "user_preferences": (
                                None
                            ),
                        },
                    )
                )

                mapping = (
                    _as_mapping(
                        result
                    )
                )

                if mapping:
                    return mapping

            except Exception as error:
                logger.warning(
                    (
                        "BlueprintCompiler "
                        "could not compile "
                        "run blueprint: %s: %s"
                    ),
                    type(error).__name__,
                    str(error),
                )

        return {
            "blueprint_version": (
                "orchestrator-fallback-v1"
            ),
            "research_question": (
                topic
            ),
            "execution_policy": {
                "research_depth": (
                    profile.name
                ),
                "max_runtime_seconds": (
                    profile
                    .max_runtime_seconds
                ),
                "max_selected_insights": (
                    profile
                    .max_selected_insights
                ),
                "max_refinement_rounds": (
                    profile
                    .max_refinement_rounds
                ),
            },
            "reasoning_policy": {
                "desired_depth": (
                    "deep"
                    if profile.name == "deep"
                    else "standard"
                ),
                "require_cross_event_synthesis": True,
                "require_mechanism": True,
                "require_second_order_effects": bool(profile.name == "deep"),
                "require_counterevidence": True,
                "require_explicit_uncertainty": True,
                "reject_fact_restatement_as_hidden_insight": True,
            },
            "selection_policy": {
                "max_candidate_insights": (
                    5 if profile.name == "deep" else 2
                ),
            },
            "cost_policy": {
                "paid_external_services_allowed": False,
                "paid_llm_calls_allowed": False,
                "paid_search_apis_allowed": False,
            },
            "epistemic_policy": {
                "search_result_is_evidence": False,
                "event_is_evidence": False,
                "vector_similarity_is_support": False,
                "grounding_required": True,
                "red_team_required": True,
                "counterevidence_required_where_relevant": True,
            },
        }

    @staticmethod
    def _compact_rescue_rounds(
        rescue: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        compact: list[dict[str, Any]] = []
        for raw in (rescue.get("rounds") or []):
            if not isinstance(raw, Mapping):
                continue
            material = _as_mapping(raw.get("material_information_assessment"))
            material_rows = [
                dict(item)
                for item in (material.get("event_assessments") or [])
                if isinstance(item, Mapping)
            ]
            rescue_rejection_reasons: dict[str, int] = {}
            for item in material_rows:
                if item.get("admitted_for_rescue"):
                    continue
                reason = _clean(item.get("rejection_reason")) or "not_admitted"
                rescue_rejection_reasons[reason] = (
                    rescue_rejection_reasons.get(reason, 0) + 1
                )
            rescue_admission = _as_mapping(raw.get("strategic_event_admission"))
            compact.append({
                "round": raw.get("round"),
                "status": _clean(raw.get("status")),
                "trigger_reason": _clean(raw.get("reason")),
                "investigation_score": raw.get("investigation_score"),
                "candidate_insight_id": _clean(raw.get("candidate_insight_id")),
                "queries": list(raw.get("queries") or [])[:3],
                "search_selected_source_ids": list(
                    raw.get("search_selected_source_ids") or []
                )[:8],
                "new_source_ids": list(raw.get("new_source_ids") or [])[:8],
                "new_event_count": raw.get("new_event_count"),
                "strategically_eligible_event_count": raw.get(
                    "strategically_eligible_event_count"
                ),
                "strategically_eligible_evidence_count": raw.get(
                    "strategically_eligible_evidence_count"
                ),
                "admitted_gap_closing_event_ids": list(
                    material.get("admitted_event_ids") or []
                )[:20],
                "admitted_gap_closing_event_count": material.get(
                    "admitted_event_count"
                ),
                "new_independent_underlying_event_count": material.get(
                    "new_independent_underlying_event_count"
                ),
                "rejected_rescue_event_count": sum(
                    rescue_rejection_reasons.values()
                ),
                "rescue_rejection_reasons": rescue_rejection_reasons,
                "rescue_strategic_admission": {
                    "admitted_event_count": rescue_admission.get("admitted_event_count"),
                    "considered_event_count": rescue_admission.get("considered_event_count"),
                    "excluded_event_count": rescue_admission.get("excluded_event_count"),
                    "considered_evidence_count": rescue_admission.get("considered_evidence_count"),
                    "exclusion_reasons": _json_safe(
                        rescue_admission.get("exclusion_reasons") or {}
                    ),
                },
                "new_gap_closing_evidence_count": raw.get(
                    "new_gap_closing_evidence_count"
                ),
                "candidate_count_after_rerun": raw.get("candidate_count"),
                "post_search_rejection_reasons": list(
                    raw.get("post_search_rejection_reasons") or []
                )[:20],
                "hypothesis_matrix_changed": raw.get("hypothesis_matrix_changed"),
                "search_failure_count": len(raw.get("search_failures") or []),
                "extraction_failure_count": len(raw.get("extraction_failures") or []),
            })
        return compact

    def _run_observability_summary(
        self,
        *,
        state: Mapping[str, Any],
        events: Optional[Sequence[Mapping[str, Any]]] = None,
        evidence: Optional[Sequence[Mapping[str, Any]]] = None,
        report: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        rescue = _as_mapping(state.get("strategic_evidence_rescue"))
        refill = _as_mapping(state.get("strategic_corpus_refill"))
        expansion = _as_mapping(state.get("strategic_cluster_expansion"))
        discovery_circuit = _as_mapping(state.get("fresh_discovery_circuit_breaker"))
        admission = _as_mapping(state.get("strategic_event_admission"))
        initial = _as_mapping(state.get("strategic_initial_inference_diagnostics"))
        abstention = _as_mapping(state.get("strategic_abstention_diagnostics"))
        persistence = _as_mapping(state.get("report_persistence"))
        report_map = _as_mapping(report or state.get("report"))
        analysis_metadata = _as_mapping(report_map.get("analysis_metadata"))
        return {
            "run_id": _clean(state.get("run_id")),
            "research_depth": _clean(state.get("research_depth")),
            "source_count": len(state.get("source_ids") or []),
            "source_ids": list(state.get("source_ids") or [])[:20],
            "fresh_discovery_circuit_breaker": {
                "open": bool(
                    state.get("fresh_discovery_unavailable_for_run")
                    or discovery_circuit.get("open")
                ),
                "reason": _clean(discovery_circuit.get("reason")),
                "opened_phase": _clean(discovery_circuit.get("opened_phase")),
                "phases": list(discovery_circuit.get("phases") or [])[:12],
                "provider_error": _json_safe(
                    discovery_circuit.get("provider_error")
                    or state.get("source_discovery_provider_error")
                    or {}
                ),
            },
            "admitted_event_count_before_strategic_filter": len(
                state.get("admitted_events_before_strategic_filter") or []
            ),
            "admitted_evidence_count_before_strategic_filter": len(
                state.get("admitted_evidence_before_strategic_filter") or []
            ),
            "strategic_considered_event_count": len(
                events if events is not None else (state.get("events") or [])
            ),
            "strategic_considered_evidence_count": len(
                evidence if evidence is not None else (state.get("evidence") or [])
            ),
            "strategic_event_admission": {
                "considered_event_count": admission.get("considered_event_count"),
                "excluded_event_count": admission.get("excluded_event_count"),
                "considered_evidence_count": admission.get("considered_evidence_count"),
                "cluster_distribution": _json_safe(
                    admission.get("cluster_distribution") or {}
                ),
                "topic_linkage_distribution": _json_safe(
                    admission.get("topic_linkage_distribution") or {}
                ),
                "exclusion_reasons": _json_safe(
                    admission.get("exclusion_reasons") or {}
                ),
                "considered_event_ids": list(
                    admission.get("considered_event_ids") or []
                )[:60],
            },
            "strategic_corpus_refill": {
                "attempted": bool(refill.get("attempted")),
                "initial_assessment": _json_safe(refill.get("initial_assessment") or {}),
                "rounds": _json_safe(refill.get("rounds") or []),
                "new_material_event_count": refill.get("new_material_event_count"),
                "final_source_count": refill.get("final_source_count"),
                "final_event_count": refill.get("final_event_count"),
                "final_evidence_count": refill.get("final_evidence_count"),
                "final_assessment": _json_safe(refill.get("final_assessment") or {}),
                "stop_reason": _clean(refill.get("stop_reason")),
            },
            "strategic_cluster_expansion": {
                "attempted": bool(expansion.get("attempted")),
                "selected_cluster_ids": list(expansion.get("selected_cluster_ids") or [])[:10],
                "new_material_event_count": expansion.get("new_material_event_count"),
                "final_source_count": expansion.get("final_source_count"),
                "final_event_count": expansion.get("final_event_count"),
                "final_evidence_count": expansion.get("final_evidence_count"),
                "stop_reason": _clean(expansion.get("stop_reason")),
                "cluster_runs": _json_safe([
                    {
                        "cluster_id": _clean(item.get("cluster_id")),
                        "initial_score": item.get("initial_score"),
                        "final_score": item.get("final_score"),
                        "seed_basis": item.get("seed_basis"),
                        "saturated": bool(item.get("saturated")),
                        "stop_reason": _clean(item.get("stop_reason")),
                        "rounds": [
                            {
                                "round": raw.get("round"),
                                "status": _clean(raw.get("status")),
                                "queries": list(raw.get("queries") or [])[:3],
                                "new_source_count": len(raw.get("new_source_ids") or []),
                                "new_raw_event_count": raw.get("new_raw_event_count"),
                                "new_strategically_eligible_event_count": raw.get("new_strategically_eligible_event_count"),
                                "new_material_event_count": raw.get("new_material_event_count", _as_mapping(raw.get("material_information_assessment")).get("admitted_event_count")),
                                "rejection_reasons": _json_safe(_as_mapping(raw.get("material_information_assessment")).get("rejection_reasons") or {}),
                            }
                            for raw in (item.get("rounds") or [])[:4]
                            if isinstance(raw, Mapping)
                        ],
                    }
                    for item in (expansion.get("cluster_runs") or [])[:6]
                    if isinstance(item, Mapping)
                ]),
            },
            "initial_inference": {
                "candidate_count": initial.get("candidate_count"),
                "support_cluster_count": initial.get("support_cluster_count"),
                "candidate_rejection_reasons": list(
                    initial.get("candidate_rejection_reasons") or []
                )[:20],
                "final_candidate_rejection_reasons": list(
                    initial.get("final_candidate_rejection_reasons") or []
                )[:20],
                "last_evidence_linkage_diagnostics": _json_safe(
                    initial.get("last_evidence_linkage_diagnostics") or {}
                ),
            },
            "strategic_rescue": {
                "attempted": bool(rescue.get("attempted")),
                "round_count": len(rescue.get("rounds") or []),
                "rounds": self._compact_rescue_rounds(rescue),
                "final_candidate_count": rescue.get("final_candidate_count"),
                "final_source_count": rescue.get("final_source_count"),
                "final_event_count": rescue.get("final_event_count"),
                "final_evidence_count": rescue.get("final_evidence_count"),
            },
            "final_strategic_outcome": {
                "research_outcome": (
                    _clean(state.get("research_outcome"))
                    or _clean(report_map.get("outcome"))
                    or ("published" if state.get("final_insights") else "")
                ),
                "final_candidate_rejection_reasons": list(
                    abstention.get("final_candidate_rejection_reasons")
                    or abstention.get("candidate_rejection_reasons")
                    or []
                )[:20],
                "support_cluster_count": abstention.get("support_cluster_count"),
                "last_evidence_linkage_diagnostics": _json_safe(
                    abstention.get("last_evidence_linkage_diagnostics") or {}
                ),
                "publishable_insight_count": analysis_metadata.get(
                    "publishable_insight_count",
                    analysis_metadata.get("insight_count"),
                ),
            },
            "report_persistence": {
                "saved": persistence.get("saved"),
                "state": _clean(persistence.get("state")),
                "released": persistence.get("released"),
                "report_id": _clean(persistence.get("report_id")),
                "report_revision_id": _clean(
                    persistence.get("report_revision_id")
                ),
                "report_released": bool(state.get("report_released", False)),
            },
        }

    def _emit_telemetry(
        self,
        state: Mapping[
            str,
            Any,
        ],
    ) -> None:

        if (
            self.telemetry_service
            is None
        ):
            return

        try:
            self._call_method(
                self.telemetry_service,
                (
                    "record_event",
                    "log_event",
                    "emit",
                    "record",
                ),
                {
                    "event_type": (
                        "research_completed"
                        if state.get(
                            "status"
                        )
                        == "completed"
                        else "research_failed"
                    ),
                    "run_id": (
                        state.get(
                            "run_id"
                        )
                    ),
                    "dimensions": {
                        "research_depth": (
                            state.get(
                                "research_depth"
                            )
                        ),
                        "status": (
                            state.get(
                                "status"
                            )
                        ),
                        "report_released": (
                            state.get(
                                "report_released",
                                False,
                            )
                        ),
                    },
                    "metrics": {
                        "elapsed_seconds": (
                            state.get(
                                "elapsed_seconds"
                            )
                        ),
                        "source_count": (
                            len(
                                state.get(
                                    "source_ids",
                                    [],
                                )
                            )
                        ),
                        "event_count": (
                            len(
                                state.get(
                                    "events",
                                    [],
                                )
                            )
                        ),
                        "evidence_count": (
                            len(
                                state.get(
                                    "evidence",
                                    [],
                                )
                            )
                        ),
                        "insight_count": (
                            len(
                                state.get(
                                    "final_insights",
                                    [],
                                )
                            )
                        ),
                    },
                },
            )

        except Exception:
            logger.debug(
                (
                    "Telemetry write failed."
                ),
                exc_info=True,
            )

    @staticmethod
    def _extract_insights(
        result: Any,
    ) -> list[dict[str, Any]]:

        mapping = (
            _as_mapping(
                result
            )
        )

        for key in (
            "insights",
            "candidate_insights",
            "strategic_insights",
            "insight_revisions",
        ):
            values = (
                mapping.get(
                    key
                )
            )

            if isinstance(
                values,
                list,
            ):
                return [
                    _as_mapping(
                        item
                    )
                    for item
                    in values
                    if _as_mapping(
                        item
                    )
                ]

        analysis = (
            mapping.get(
                "analysis"
            )
        )

        if isinstance(
            analysis,
            Mapping,
        ):
            return (
                ResearchOrchestratorService
                ._extract_insights(
                    analysis
                )
            )

        return []

    @staticmethod
    def _constraint_list(
        value: Any,
    ) -> list[dict[str, Any]]:

        if isinstance(
            value,
            list,
        ):
            return [
                _as_mapping(
                    item
                )
                for item
                in value
                if isinstance(
                    item,
                    Mapping,
                )
            ]

        if not isinstance(
            value,
            Mapping,
        ):
            return []

        output = []

        for (
            category,
            items,
        ) in value.items():

            if not isinstance(
                items,
                list,
            ):
                continue

            for item in items:
                if not isinstance(
                    item,
                    Mapping,
                ):
                    continue

                record = dict(
                    item
                )

                record.setdefault(
                    "category",
                    category,
                )

                output.append(
                    record
                )

        return output

    @staticmethod
    def _validate_evidence_currency(
        evidence: Sequence[
            Mapping[str, Any]
        ],
    ) -> None:

        invalid = []

        for item in evidence:
            evidence_id = _clean(
                item.get(
                    "evidence_id"
                )
            )

            if not (
                evidence_id
                .upper()
                .startswith(
                    "EVID-"
                )
            ):
                invalid.append(
                    evidence_id
                    or "<missing>"
                )

        if invalid:
            raise ResearchPipelineError(
                (
                    "Evidence layer contains "
                    "non-EVID-* grounding currency: "
                    + ", ".join(
                        invalid[:10]
                    )
                )
            )

    @staticmethod
    def _evidence_manifest(
        evidence: Sequence[
            Mapping[str, Any]
        ],
    ) -> dict[str, Any]:

        ids = (
            _unique_strings(
                item.get(
                    "evidence_id"
                )
                for item
                in evidence
            )
        )

        return {
            "all_accepted_evidence_ids": (
                ids
            ),
            "examined_evidence_ids": (
                ids
            ),
            "relevant_evidence_ids": (
                ids
            ),
            "final_reasoning_context_ids": (
                ids
            ),
            "report_referenced_ids": [],
            "unexamined_evidence_ids": [],
        }

    @staticmethod
    def _bind_blueprint_to_profile(
        blueprint: Mapping[str, Any],
        *,
        profile: ResearchExecutionProfile,
    ) -> dict[str, Any]:
        """Bind compiler output to the user's resolved Fast/Deep execution profile.

        The StrategicInferenceAgent reads reasoning_policy.desired_depth, while
        older/fallback blueprints only carried execution_policy.research_depth.
        This method prevents a Deep run from silently degrading to Standard
        strategic reasoning.
        """
        bound = dict(blueprint or {})

        execution_policy = dict(
            _as_mapping(bound.get("execution_policy"))
        )
        execution_policy["research_depth"] = profile.name
        execution_policy.setdefault(
            "max_runtime_seconds",
            profile.max_runtime_seconds,
        )
        execution_policy.setdefault(
            "max_selected_insights",
            profile.max_selected_insights,
        )
        execution_policy.setdefault(
            "max_refinement_rounds",
            profile.max_refinement_rounds,
        )
        bound["execution_policy"] = execution_policy

        reasoning_policy = dict(
            _as_mapping(bound.get("reasoning_policy"))
        )
        if profile.name == "deep":
            existing = _enum(reasoning_policy.get("desired_depth"))
            if existing not in {"deep", "very_deep", "high", "very_high"}:
                reasoning_policy["desired_depth"] = "deep"
        else:
            reasoning_policy["desired_depth"] = "standard"

        reasoning_policy.setdefault("require_cross_event_synthesis", True)
        reasoning_policy.setdefault("require_mechanism", True)
        reasoning_policy.setdefault("require_counterevidence", True)
        reasoning_policy.setdefault("require_explicit_uncertainty", True)
        reasoning_policy.setdefault("reject_fact_restatement_as_hidden_insight", True)
        if profile.name == "deep":
            reasoning_policy.setdefault("require_second_order_effects", True)

        bound["reasoning_policy"] = reasoning_policy

        selection_policy = dict(
            _as_mapping(bound.get("selection_policy"))
        )
        selection_policy["max_candidate_insights"] = (
            5 if profile.name == "deep" else max(2, profile.max_selected_insights)
        )
        bound["selection_policy"] = selection_policy

        return bound

    @classmethod
    def _final_release_rejection_reason(
        cls,
        insight: Mapping[str, Any],
    ) -> str:
        """Tier-aware deterministic last-line defense against generic filler."""
        text = _clean(insight.get("insight") or insight.get("text"))
        if not text:
            return "missing_insight_text"

        tier = cls._finding_tier(insight)
        lowered = text.casefold()
        forbidden_generic_phrases = (
            "sit in different parts of the same",
            "same adoption system",
            "is converging with",
            "are converging with",
            "competitive advantage may shift",
            "competitive advantage in",
            "across distinct events rather than one headline",
            "adjacent capabilities such as",
            "may capture more value",
            "watch for additional events",
        )
        if any(phrase in lowered for phrase in forbidden_generic_phrases):
            return "forbidden_generic_analyst_language"

        metadata = insight.get("metadata") if isinstance(insight.get("metadata"), Mapping) else {}
        quality_check = metadata.get("insight_quality_check") if isinstance(metadata, Mapping) else {}
        if not isinstance(quality_check, Mapping):
            quality_check = {}
        scope = metadata.get("scope_validation") if isinstance(metadata, Mapping) else {}
        if not isinstance(scope, Mapping):
            scope = insight.get("scope_validation", {})
        if not isinstance(scope, Mapping):
            scope = {}
        linkage = metadata.get("evidence_linkage_diagnostics") if isinstance(metadata, Mapping) else {}
        if not isinstance(linkage, Mapping):
            linkage = {}

        if bool(scope.get("could_be_written_without_events")):
            return "could_be_written_without_specific_events"
        dependency = _enum(scope.get("event_dependency"))
        if dependency and dependency not in {"necessary", "partial"}:
            return "finding_not_materially_event_derived"

        supporting = _unique_strings(
            insight.get("supporting_evidence_ids", [])
            or insight.get("evidence_ids", [])
            or []
        )
        mechanism = insight.get("mechanism")
        mechanism_text = (
            _clean(
                mechanism.get("description")
                or mechanism.get("text")
                or mechanism.get("mechanism")
            )
            if isinstance(mechanism, Mapping)
            else _clean(mechanism)
        )
        event_count = int(scope.get("distinct_event_count") or 0)
        cluster_resolved = bool(linkage.get("support_cluster_resolved"))
        cluster_reason = _clean(linkage.get("cluster_coherence_reason"))

        if tier == "hidden_insight":
            if bool(quality_check.get("is_merely_explanatory")):
                return "merely_explanatory_hidden_insight"
            if dependency and dependency != "necessary":
                return "insight_not_necessarily_event_derived"
            if len(supporting) < 2:
                return "fewer_than_two_supporting_evidence_records"
            if len(mechanism_text) < 80:
                return "mechanism_too_thin_for_release"
            if len(text) < 120:
                return "insight_too_thin_for_release"
            return ""

        if tier == "underlying_dynamic":
            if len(supporting) < 2 or event_count < 2:
                return "underlying_dynamic_requires_multiple_evidence_backed_events"
            if not cluster_resolved or not cluster_reason:
                return "underlying_dynamic_requires_coherent_support_cluster"
            if len(mechanism_text) < 60:
                return "underlying_dynamic_mechanism_too_thin"
            if len(text) < 80:
                return "underlying_dynamic_text_too_thin"
            return ""

        if tier == "strategic_connection":
            if len(supporting) < 2 or event_count < 2:
                return "strategic_connection_requires_multiple_evidence_backed_events"
            if not cluster_resolved or not cluster_reason:
                return "strategic_connection_requires_coherent_support_cluster"
            if len(mechanism_text) < 20:
                return "strategic_connection_requires_explicit_relation_or_mechanism"
            if len(text) < 65:
                return "strategic_connection_text_too_thin"
            return ""

        if tier == "early_signal":
            if len(supporting) < 1 or event_count < 1:
                return "early_signal_requires_event_backed_evidence"
            pattern_status = _enum(scope.get("pattern_status"))
            insight_type = _enum(insight.get("insight_type"))
            insight_mode = _enum(metadata.get("insight_mode"))
            if not (
                pattern_status == "emerging"
                or insight_type == "prospective"
                or insight_mode == "prospective_scenario"
            ):
                return "early_signal_requires_emerging_or_prospective_status"
            derivation = metadata.get("insight_derivation") if isinstance(metadata, Mapping) else {}
            if not isinstance(derivation, Mapping):
                derivation = {}
            uncertainty_present = bool(
                [x for x in (insight.get("critical_unknowns") or []) if _clean(x)]
                or [x for x in (derivation.get("watch_signals") or []) if _clean(x)]
            )
            if not uncertainty_present:
                return "early_signal_requires_explicit_uncertainty_or_watch_signal"
            if len(text) < 65:
                return "early_signal_text_too_thin"
            return ""

        return "invalid_finding_tier"

    @staticmethod
    def _resolve_profile(
        value: str,
    ) -> ResearchExecutionProfile:

        key = _enum(
            value
        )

        profile = (
            EXECUTION_PROFILES.get(
                key
            )
        )

        if profile is None:
            raise ResearchConfigurationError(
                (
                    "research_depth must "
                    "be 'fast' or 'deep'."
                )
            )

        return profile

    @staticmethod
    def _stage_ok(
        state: dict[str, Any],
        name: str,
        **details: Any,
    ) -> None:

        state[
            "stage_status"
        ][
            name
        ] = {
            "status": "ok",
            **details,
        }

        logger.info(
            (
                "Research stage complete | "
                "stage=%s details=%s"
            ),
            name,
            _json_safe(
                details
            ),
        )

    @staticmethod
    def _call_method(
        target: Any,
        method_names: Sequence[
            str
        ],
        arguments: Mapping[
            str,
            Any,
        ],
    ) -> Any:

        for method_name in (
            method_names
        ):
            method = getattr(
                target,
                method_name,
                None,
            )

            if not callable(
                method
            ):
                continue

            try:
                signature = (
                    inspect.signature(
                        method
                    )
                )

            except (
                TypeError,
                ValueError,
            ):
                return (
                    method(
                        **dict(
                            arguments
                        )
                    )
                )

            parameters = (
                signature.parameters
            )

            accepts_kwargs = any(
                parameter.kind
                ==
                inspect.Parameter
                .VAR_KEYWORD
                for parameter
                in parameters.values()
            )

            if accepts_kwargs:
                kwargs = dict(
                    arguments
                )

            else:
                kwargs = {
                    key: value
                    for key, value
                    in arguments.items()
                    if key
                    in parameters
                }

            missing_required = []

            for (
                name,
                parameter,
            ) in parameters.items():

                if name == "self":
                    continue

                if (
                    parameter.kind
                    in {
                        inspect.Parameter
                        .VAR_POSITIONAL,
                        inspect.Parameter
                        .VAR_KEYWORD,
                    }
                ):
                    continue

                if (
                    parameter.default
                    is inspect.Parameter.empty
                    and name
                    not in kwargs
                ):
                    missing_required.append(
                        name
                    )

            if missing_required:
                continue

            return method(
                **kwargs
            )

        raise AttributeError(
            (
                f"{type(target).__name__} "
                "exposes none of the "
                "compatible methods: "
                + ", ".join(
                    method_names
                )
            )
        )

    @staticmethod
    def _import_class(
        module_name: str,
        class_name: str,
    ) -> Any:

        module = (
            importlib.import_module(
                module_name
            )
        )

        return getattr(
            module,
            class_name,
        )

    @classmethod
    def _construct(
        cls,
        module_name: str,
        class_name: str,
    ) -> Any:

        target = (
            cls._import_class(
                module_name,
                class_name,
            )
        )

        return (
            target()
        )

    @classmethod
    def _construct_optional(
        cls,
        module_name: str,
        class_name: str,
    ) -> Any:

        try:
            return (
                cls._construct(
                    module_name,
                    class_name,
                )
            )

        except Exception:
            logger.debug(
                (
                    "Optional component "
                    "unavailable: %s.%s"
                ),
                module_name,
                class_name,
                exc_info=True,
            )

            return None

    @classmethod
    def _construct_web_search_service(
        cls,
    ) -> Any:

        module = (
            importlib.import_module(
                "services.web_search_service"
            )
        )

        factory = getattr(
            module,
            "build_default_web_search_service",
            None,
        )

        if not callable(
            factory
        ):
            raise ResearchPipelineError(
                (
                    "services.web_search_service "
                    "must expose "
                    "build_default_web_search_service()."
                )
            )

        return factory()

    @classmethod
    def _construct_web_search_service_optional(
        cls,
    ) -> Any:
        """
        Construct fresh web discovery when configured.

        Web search is optional at orchestrator construction time because the
        research pipeline can legitimately continue from persisted SOURCE-*
        records for the exact same topic. Missing provider credentials must
        therefore not prevent the entire application from starting.
        """

        try:
            return cls._construct_web_search_service()

        except Exception as error:
            logger.warning(
                (
                    "Fresh web-search discovery is unavailable at "
                    "orchestrator construction time. error=%s message=%s. "
                    "Cached persisted sources or explicit source_ids may "
                    "still be used."
                ),
                type(error).__name__,
                str(error),
            )
            return None

    @classmethod
    def _construct_grounding_service(
        cls,
    ) -> Any:

        target = (
            cls._import_class(
                "services.grounding_service",
                "GroundingService",
            )
        )

        try:
            return target()

        except TypeError:
            pass

        from services.llm_service import (
            LLMService
        )

        llm = (
            LLMService()
        )

        ask = getattr(
            llm,
            "ask",
            None,
        )

        if not callable(
            ask
        ):
            raise ResearchConfigurationError(
                (
                    "GroundingService requires "
                    "a classifier but "
                    "LLMService.ask is unavailable."
                )
            )

        try:
            return target(
                support_classifier=(
                    ask
                )
            )

        except TypeError:
            return target(
                classifier=(
                    ask
                )
            )


__all__ = [
    "SERVICE_VERSION",
    "PATCH_BUILD_ID",
    "ResearchExecutionProfile",
    "FAST_PROFILE",
    "DEEP_PROFILE",
    "EXECUTION_PROFILES",
    "ResearchOrchestratorError",
    "ResearchPipelineError",
    "ResearchConfigurationError",
    "ResearchOrchestratorService",
]