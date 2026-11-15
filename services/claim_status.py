"""One definition of what the claim checker's verdicts mean (14 Nov 2026).

Why this file exists: on 12 Nov the gate was changed so that a judgment the sources do not
state is released with a label, but two older checks still read "unsupported" the old way and
filed a forward prediction as "restates its sources". Cemri et al. 2025 ("Why Do Multi-Agent
LLM Systems Fail?", MAST) class this as a specification / inter-stage misalignment failure; the
remedy is one shared contract plus a test over every value (tests/test_status_contract.py).

A verdict means different things in the two lanes (ICD 203: keep information and judgment apart):

verdict               as a FACT (premise, control, dependency)    as a JUDGMENT (conclusion, mechanism,
                                                                   incentive, forecast, lab thesis)
supported             stated by its cited records: passes         the sources already say it: a restatement
inference_supported   follows from its cited records: passes,     follows from the sources: an inference
                      labelled
partially_supported   passes, labelled "partly verified"          judgment, partly backed: released labelled
unsupported           BLOCKS (ALCE / FActScore: a stated fact      judgment not stated by the sources:
                      must be backed by its citation)              released labelled
contradicted          BLOCKS                                       BLOCKS

New code must import these names instead of writing its own set of strings.
"""
from __future__ import annotations

import os

SUPPORTED = "supported"
INFERENCE_SUPPORTED = "inference_supported"
PARTIALLY_SUPPORTED = "partially_supported"
UNSUPPORTED = "unsupported"
CONTRADICTED = "contradicted"

ALL = (SUPPORTED, INFERENCE_SUPPORTED, PARTIALLY_SUPPORTED, UNSUPPORTED, CONTRADICTED)

# Fact lane
FACT_PASSES = frozenset({SUPPORTED, INFERENCE_SUPPORTED})
FACT_PASSES_LABELLED = frozenset({PARTIALLY_SUPPORTED})
FACT_BLOCKS = frozenset({UNSUPPORTED, CONTRADICTED})

# Judgment lane
JUDGMENT_RESTATES_SOURCES = frozenset({SUPPORTED})
JUDGMENT_INFERRED = frozenset({INFERENCE_SUPPORTED, PARTIALLY_SUPPORTED, UNSUPPORTED})
JUDGMENT_BLOCKS = frozenset({CONTRADICTED})


def legacy_gate() -> bool:
    """True when the pre-12-Nov rules are switched back on (EVENT_INTELLIGENCE_GATE_MODE=legacy)."""
    return os.getenv("EVENT_INTELLIGENCE_GATE_MODE", "two_lane").strip().casefold() == "legacy"
