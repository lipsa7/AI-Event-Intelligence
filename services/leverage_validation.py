"""Shared conservative leverage checks. Unknown alternatives are not scarce alternatives."""
import re

KEYS = ("holder", "counterparty", "dependency", "control_point", "outside_option", "holder_dependence")
UNKNOWN = re.compile(r"\b(?:unknown|not (?:known|established|stated|identified|specified)|none (?:named|identified|stated|specified)|no (?:alternatives?|options?) (?:named|identified|stated|specified)|insufficient (?:data|evidence)|unclear|not available|n/?a)\b", re.I)

def alternatives_known(value):
    text = " ".join(str(value or "").split())
    return bool(text) and not UNKNOWN.search(text)

def result(lev, checks):
    if not all(str(lev.get(k) or "").strip() for k in KEYS):
        return "incomplete"
    if not alternatives_known(lev.get("outside_option")):
        return "not_established"
    return "passed" if all(checks.get(k) in {"supported", "inference_supported"}
                           for k in ("leverage", "leverage_alternatives", "leverage_asymmetry")) else "not_established"
