from __future__ import annotations

import inspect
from pathlib import Path

print("\n=== EVENT INTELLIGENCE RUNTIME CHECK ===\n")

# Extractor
try:
    import agents.extraction_agent as e
    print("EXTRACTOR FILE:", getattr(e, "__file__", "<missing>"))
    agent_cls = getattr(e, "ExtractionAgent", None)
    print("EXTRACTOR CLASS:", bool(agent_cls))
    print(
        "EXTRACTOR VERSION:",
        getattr(agent_cls, "PROMPT_VERSION", "<missing>") if agent_cls else "<no class>",
    )
    if agent_cls:
        try:
            print("EXTRACT SIGNATURE:", inspect.signature(agent_cls.extract))
        except Exception as exc:
            print("EXTRACT SIGNATURE ERROR:", type(exc).__name__, str(exc))

    p = Path(e.__file__)
    text = p.read_text(encoding="utf-8", errors="ignore")
    print("EXTRACTOR TEXT HAS V4.3:", "extraction-agent-v4.3-fast-shallow-fallback" in text)
    print("EXTRACTOR TEXT HAS FAST FALLBACK:", "_deterministic_fast_fallback" in text)
except Exception as exc:
    print("EXTRACTOR IMPORT ERROR:", type(exc).__name__, str(exc))

print()

# Orchestrator
try:
    import services.research_orchestrator_service as r
    print("ORCHESTRATOR FILE:", getattr(r, "__file__", "<missing>"))
    orch_cls = getattr(r, "ResearchOrchestratorService", None)
    print("ORCHESTRATOR CLASS:", bool(orch_cls))
    print("ORCHESTRATOR VERSION:", getattr(r, "SERVICE_VERSION", "<missing>"))

    if orch_cls:
        try:
            print("EVENT STAGE SIGNATURE:", inspect.signature(orch_cls._extract_events))
        except Exception as exc:
            print("EVENT STAGE SIGNATURE ERROR:", type(exc).__name__, str(exc))

    p = Path(r.__file__)
    text = p.read_text(encoding="utf-8", errors="ignore")
    print("ORCH TEXT HAS CLASS:", "class ResearchOrchestratorService" in text)
    print("ORCH TEXT HAS SERVICE_VERSION:", "SERVICE_VERSION" in text)
    print(
        "ORCH TEXT HAS V1.1:",
        "research-orchestrator-service-v1.1-fast-resilient" in text,
    )
    print(
        "ORCH TEXT PASSES RESEARCH DEPTH:",
        "research_depth=research_depth" in text
        or '"research_depth": research_depth' in text
        or "research_depth=(" in text,
    )
    print(
        "ORCH TEXT STILL HAS 12000 REJECTION:",
        "Fast-mode source exceeds 12000 text characters" in text,
    )
except Exception as exc:
    print("ORCHESTRATOR IMPORT ERROR:", type(exc).__name__, str(exc))

print("\n=== END CHECK ===\n")