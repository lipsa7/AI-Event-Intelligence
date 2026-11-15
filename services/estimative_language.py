"""ICD 203 estimative language for likelihood labels (12 Nov 2026).

ICD 203 fixes seven likelihood terms with probability bands and forbids putting a confidence
level and a likelihood in the same sentence; Sherman Kent ("Words of Estimative Probability")
found readers took "serious possibility" to mean anything from 20% to 80%. The app's internal
labels (low ... high) feed its confidence ceilings, so they are kept and mapped here for display
and for the forecast ledger (band midpoint as the probability).
"""
from __future__ import annotations

ICD203 = {
    "almost_no_chance": ("almost no chance", 0.01, 0.05),
    "very_unlikely": ("very unlikely", 0.05, 0.20),
    "unlikely": ("unlikely", 0.20, 0.45),
    "roughly_even": ("roughly even chance", 0.45, 0.55),
    "likely": ("likely", 0.55, 0.80),
    "very_likely": ("very likely", 0.80, 0.95),
    "almost_certain": ("almost certain", 0.95, 0.99),
}
INTERNAL_TO_ICD203 = {
    "low": "very_unlikely", "low_moderate": "unlikely", "moderate": "roughly_even",
    "moderate_high": "likely", "high": "very_likely",
}


def icd203(label: str | None) -> dict | None:
    key = INTERNAL_TO_ICD203.get(str(label or "").strip().casefold(), str(label or "").strip().casefold())
    if key not in ICD203:
        return None
    term, low, high = ICD203[key]
    return {"key": key, "term": term, "low": low, "high": high, "midpoint": round((low + high) / 2, 3),
            "text": f"{term} ({round(low * 100)}-{round(high * 100)}%)"}
