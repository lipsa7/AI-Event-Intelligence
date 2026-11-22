"""Reference-class base rates for procedural forecasts (v13.29; research report of 22 Nov, P2).

Shown to the forecast judges so "OpenAI will file a demurrer" is recognised as near-certain whatever the
insight says, and the narrow reference class (same plaintiff type, defendant type, legal theory) is
preferred to a generic federal statistic. Only figures the report attributes to a primary source are
marked verified; the rest are labelled "verify before relying on it", as the report itself says.
Add rows here as the case library grows (report P3).
"""
from __future__ import annotations

ROWS = (
    {"step": "motion to dismiss filed (high-stakes state AG suit against a large tech company)",
     "rate": "close to universal", "reference_class": "narrow",
     "source": "research report of 22 Nov, section 2 (practice in AG suits; no single statistic)", "verified": False},
    {"step": "Rule 12(b)(6) motion filed within 90 days (all federal civil cases)", "rate": "4.0% (2005-06), 6.2% (2009-10)",
     "reference_class": "generic - the wrong class for AG suits against tech companies",
     "source": "Federal Judicial Center, Cecil, Cort, Williams & Bataillon, 'Motions to Dismiss for Failure to "
               "State a Claim After Iqbal', March 2011", "verified": True},
    {"step": "Rule 12(b)(6) motion granted at least in part, when filed", "rate": "66% (2006) to 75% (2010)",
     "reference_class": "generic federal", "source": "Federal Judicial Center, March 2011 (as above); a grant "
     "rarely ends the case - no increase 2006-2010 in grants terminating the case", "verified": True},
    {"step": "federal civil case reaches trial", "rate": "about 0.4% of terminations (year to Sep 2025)",
     "reference_class": "generic federal", "source": "US Courts tables via a secondary summary - verify",
     "verified": False},
    {"step": "motions to dismiss in big-tech youth-harm litigation (MDL 3047, N.D. Cal.)",
     "rate": "granted in part and denied in part; no case ended", "reference_class": "narrow",
     "source": "research report of 22 Nov, section 2 (MDL 3047 rulings)", "verified": False},
)


def prompt_block() -> str:
    lines = [f"- {r['step']}: {r['rate']} [{r['reference_class']}; {r['source']}"
             + ("" if r["verified"] else "; verify before relying on it") + "]" for r in ROWS]
    return "REFERENCE-CLASS BASE RATES (prefer the narrow class when it fits):\n" + "\n".join(lines)
