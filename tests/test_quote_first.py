"""Quote-first premises (14 Nov 2026). Basis: ALCE (Gao et al. 2023) - a statement written from
memory with a citation attached afterwards had the worst citation quality; Anthropic guidance -
extract word-for-word quotes first. The check can add a citation; it never blocks."""
from services import claim_support_gate as gate
from services import quote_first as qf

EVIDENCE = {"EVID-A": "The White House accord is voluntary and carries no penalties for companies that breach it.",
            "EVID-B": "The FTC opened a probe into OpenAI’s image tool on 3 March.",
            "EVID-C": "Microsoft holds exclusive cloud rights until 2030."}


def premise(ids):
    return {"premise_id": "P1", "text": "t", "evidence_ids": list(ids), "status": "unresolved", "notes": ""}


def test_quote_in_cited_record_changes_nothing():
    p = premise(["EVID-A"])
    assert qf.check_premise(p, "carries no penalties", EVIDENCE) == "cited"
    assert p["evidence_ids"] == ["EVID-A"] and "quote_check=cited" in p["notes"]


def test_wrong_id_gets_the_record_that_holds_the_quote():
    p = premise(["EVID-A"])
    assert qf.check_premise(p, "\u201cThe FTC opened a probe into OpenAI's image tool\u201d", EVIDENCE) == "elsewhere"
    assert p["evidence_ids"] == ["EVID-A", "EVID-B"]            # added; the model's citation is kept
    assert "citation_added_from_quote=EVID-B" in p["notes"]


def test_never_drops_a_citation_and_never_exceeds_three_alce():
    p = premise(["EVID-A", "EVID-B", "EVID-D"])
    assert qf.check_premise(p, "exclusive cloud rights", EVIDENCE) == "elsewhere"
    assert p["evidence_ids"] == ["EVID-A", "EVID-B", "EVID-D"]   # already three: nothing added, nothing lost
    assert "citation_added" not in p["notes"]


def test_common_span_is_ambiguous_and_adds_nothing():
    """A one-word 'quote' sits in many records; adding one would fake corroboration."""
    evidence = dict(EVIDENCE, **{"EVID-E": "OpenAI and Microsoft renewed their agreement."})
    p = premise(["EVID-A"])
    assert qf.check_premise(p, "OpenAI", evidence) == "ambiguous"
    assert p["evidence_ids"] == ["EVID-A"] and "quote_check=ambiguous" in p["notes"]


def test_quote_found_nowhere_is_marked_not_blocked():
    p = premise(["EVID-A"])
    assert qf.check_premise(p, "OpenAI was fined one billion dollars", EVIDENCE) == "not_found"
    assert p["evidence_ids"] == ["EVID-A"] and p["text"] == "t" and "quote_check=not_found" in p["notes"]


def test_no_quote_is_left_alone():
    p = premise(["EVID-A"])
    assert qf.check_premise(p, "", EVIDENCE) == "" and p["notes"] == ""


def test_prompt_gets_rule_and_quote_field_once(monkeypatch):
    base = "TASK\nRELEASE CONTRACT - X\n{" + qf.SCHEMA_OLD + "}"
    out = qf.apply_to_prompt(base)
    assert qf.PROMPT_RULE in out and qf.SCHEMA_NEW in out and qf.SCHEMA_OLD not in out
    assert out.index(qf.PROMPT_RULE) < out.index("RELEASE CONTRACT")
    assert qf.apply_to_prompt(out) == out                        # not added twice
    assert qf.apply_to_prompt("another prompt") == "another prompt"
    monkeypatch.setenv("EVENT_INTELLIGENCE_QUOTE_FIRST", "0")
    assert qf.apply_to_prompt(base) == base


def test_real_synthesis_prompt_still_holds_the_schema_line_this_module_rewrites():
    """If the synthesis prompt's premises line is edited, quote-first would silently stop working."""
    import inspect
    from agents import strategic_inference_agent as agent
    source = inspect.getsource(agent)
    assert qf.SCHEMA_OLD in source and "RELEASE CONTRACT - " in source
    assert "quote_first.apply_to_prompt(prompt)" in source and "quote_first.check_premise(" in source


def test_leverage_field_examples_in_the_prompt_match_the_gate():
    for name in ("Microsoft", "EU regulators"):
        assert gate.named_party(name)
    for category in ("investors", "AI firms", "enterprise customers"):
        assert not gate.named_party(category)
