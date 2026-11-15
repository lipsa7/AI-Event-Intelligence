#!/usr/bin/env python3
"""
gold_set_tool.py

Blind gold-set preparation + deterministic admission derivation for the
AI Event Intelligence 38-event diagnostic corpus.

Recommended location:
    project root, beside offline_corpus_integrity_diagnostic.py

PREPARE (read-only against the app/database; writes only evaluation artifacts):
    python gold_set_tool.py prepare \
        --project-root . \
        --report-id REPORT-FF59222B19F714AB8918D407 \
        --topic "artificial intelligence"

DERIVE (run only after manual labels are frozen):
    python gold_set_tool.py derive \
        --labels data/gold_set/gold_set_blind.csv \
        --key data/gold_set/gold_set_key.json

Blind-labeling guarantees
-------------------------
The labeling CSV intentionally excludes:
- EVENT-* ids;
- SOURCE-* ids;
- old actor output;
- actor-sidecar output;
- relevance/materiality/linkage scores;
- admission verdicts and exclusion reasons.

A separate key file maps B001... labels back to EVENT-* ids. Do not inspect the
key until the labeling pass is frozen.

`should_admit` is NEVER manually labelled. `derive` computes it from the frozen
semantic labels.

No NewsAPI, web retrieval, LLM, Streamlit or production writes are performed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

TOOL_VERSION = "gold-set-tool-v1.0-blind-derived-admission"
DEFINITION_VERSION = "gold-semantics-v1.0"

ARTICLE_TOPIC_VALUES = {
    "AI_PRIMARY",
    "AI_MATERIAL",
    "AI_CONTEXTUAL",
    "NOT_AI",
    "UNRESOLVED",
}
ACTOR_ROLE_VALUES = {
    "action_subject",
    "passive_agent",
    "counterparty",
    "non_actor",
    "unresolved",
}
IDENTITY_STATUS_VALUES = {
    "explicit",
    "source_local_alias",
    "strict_coreference",
    "bridging",
    "unresolved",
}
IDENTITY_RELATION_VALUES = {
    "same_entity",
    "organizational_subunit_of",
    "subsidiary_of",
    "none",
    "unresolved",
}
LINKAGE_VALUES = {
    "DIRECT_MATERIAL",
    "INDIRECT_MATERIAL",
    "CONTEXTUAL_OR_NONE",
    "UNRESOLVED",
}
YES_NO_UNRESOLVED = {"YES", "NO", "UNRESOLVED"}
CONFIDENCE_VALUES = {"confident", "uncertain"}

BLIND_COLUMNS = [
    "blind_id",
    "source_blind_id",
    "event_text",
    "source_title",
    "source_file",
    "evidence_file",
    "actor_surface",
    "actor_role",
    "actor_role_confidence",
    "canonical_actor",
    "identity_status",
    "identity_relation",
    "identity_confidence",
    "article_topic",
    "article_topic_confidence",
    "event_ai_linkage",
    "linkage_confidence",
    "material_development",
    "material_development_confidence",
    "supporting_source_spans",
    "label_notes",
    "definition_version",
]


def clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def import_project_module(root: Path, name: str):
    root_text = str(root.resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    return importlib.import_module(name)


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(v) for v in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def corpus_fingerprint(rows: Iterable[Mapping[str, Any]]) -> str:
    payload = [
        {
            "event_id": clean(row.get("event_id")).upper(),
            "source_id": clean(row.get("source_id")).upper(),
            "event_text": clean(row.get("event_text")),
            "source_text_sha256": clean(row.get("source_text_sha256")),
        }
        for row in rows
    ]
    payload.sort(key=lambda x: (x["event_id"], x["source_id"]))
    return sha256_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def source_title(row: Mapping[str, Any]) -> str:
    for key in ("title", "headline", "name", "article_title"):
        value = clean(row.get(key))
        if value:
            return value
    for nested_key in ("metadata", "source_metadata", "payload", "article", "data"):
        nested = row.get(nested_key)
        if isinstance(nested, Mapping):
            for key in ("title", "headline", "name", "article_title"):
                value = clean(nested.get(key))
                if value:
                    return value
    return ""


def evidence_by_event(records: Iterable[Mapping[str, Any]]):
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if not isinstance(record, Mapping):
            continue
        event_id = clean(
            record.get("event_id")
            or record.get("linked_event_id")
            or record.get("canonical_event_id")
        ).upper()
        if event_id:
            output[event_id].append(dict(record))
    return output


def evidence_text(record: Mapping[str, Any]) -> str:
    return clean(
        record.get("claim")
        or record.get("text")
        or record.get("source_excerpt")
        or record.get("statement")
        or record.get("excerpt")
    )


def write_definitions(path: Path) -> None:
    text = f"""# AI Event Intelligence Gold-Set Definitions

**Definition version:** `{DEFINITION_VERSION}`

Freeze this file before labeling. If any definition changes during the labeling
pass, increment the definition version and relabel every row created under the
old definition.

## Labeling protocol

1. Label from the event text and full source document only.
2. Do not inspect EVENT-* ids, current model outputs, old admission reasons or
   actor-sidecar outputs while labeling.
3. Label actor/identity/article/linkage/material-development first.
4. Never label `should_admit` manually; it is derived after labels are frozen.
5. Use `uncertain` rather than forcing certainty.
6. A positive semantic judgment should have a source span in
   `supporting_source_spans` when the support is not explicit in the event text.

## Article topic

### AI_PRIMARY
Artificial intelligence or AI-enabled technology is a primary subject of the
article and materially organizes the article's claims.

Positive: an article centered on an AI drug-discovery platform and its deals.
Negative: a general earnings article that mentions AI once.

### AI_MATERIAL
AI is not necessarily the article's main subject, but AI materially affects the
article's central development.

Positive: a semiconductor capacity article where the capacity is explicitly
being built for AI training/inference demand.
Negative: a generic data-center article with no evidenced AI relationship.

### AI_CONTEXTUAL
AI appears as surrounding context but does not materially determine the
article's central development.

### NOT_AI
The article does not establish a meaningful AI relationship.

### UNRESOLVED
The source does not support a confident article-topic judgment.

## Event AI linkage (gate-critical taxonomy)

### DIRECT_MATERIAL
The event itself materially involves AI or AI-enabled technology as part of the
action, asset, product, service, agreement or observed development.

Positive: `Company X signed a collaboration to use its generative-AI discovery platform.`
Negative: `Company X reported quarterly cash.`

### INDIRECT_MATERIAL
The source establishes a material causal, enabling, constraining or consequence
relationship between this event and AI, although AI is not the direct object of
the event action.

Positive: `A licensing transaction commercializes a molecule that the source
establishes was produced through the company's AI platform.`
Negative: `An AI company raised generic financing with no source evidence tying
that financing to AI activity.`

### CONTEXTUAL_OR_NONE
The article or actor may be AI-related, but this particular event lacks a
material source-supported AI relationship.

Positive: `A drug candidate completed patient dosing` where the article gives no
source-grounded reason that the dosing milestone itself materially involves AI.

### UNRESOLVED
The source is insufficient or genuinely ambiguous.

## Actor identity

### explicit
The source explicitly names the canonical institutional actor in the event or
an unambiguous equivalent construction.

### source_local_alias
A source-local alias/abbreviation explicitly maps to the canonical actor, e.g.
`China Medical System (CMS)` followed by `CMS`.

### strict_coreference
The mention and antecedent refer to the same entity, e.g. `Insilico Medicine ...
the Company ...`.

### bridging
The mention is a distinct subunit/related entity whose parent institutional
actor is source-grounded, e.g. `Insilico Medicine's UAE team` -> canonical actor
`Insilico Medicine`, relation `organizational_subunit_of`.

### unresolved
The source does not uniquely resolve the canonical actor.

## Material development

### YES
The event describes an observed or committed real-world action, change, outcome,
transaction, launch, approval, restriction, milestone or other material
development rather than stable background/boilerplate.

### NO
Stable background, generic description, boilerplate, opinion or non-development.

### UNRESOLVED
The source is insufficient or ambiguous.

## Derived admission contract

`should_admit = YES` only when all are true:

- article topic is `AI_PRIMARY` or `AI_MATERIAL`;
- event linkage is `DIRECT_MATERIAL` or `INDIRECT_MATERIAL`;
- actor role is `action_subject` or `passive_agent`;
- actor identity is `explicit`, `source_local_alias`, `strict_coreference` or `bridging`;
- a canonical actor is present;
- material development is `YES`.

Evidence count is deliberately **not** part of event admission. The downstream
StrategicInference two-support-record requirement applies at candidate/insight
scope, not as a per-event admission rule.
"""
    path.write_text(text, encoding="utf-8")


def prepare(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    report_id = clean(args.report_id).upper()
    topic = clean(args.topic)
    output_dir = (root / args.output_dir).resolve()
    source_dir = output_dir / "sources"
    evidence_dir = output_dir / "evidence"
    output_dir.mkdir(parents=True, exist_ok=True)
    source_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    diag = import_project_module(root, "offline_corpus_integrity_diagnostic")
    databases = diag.sqlite_candidates(root)
    report, report_lookup = diag.find_report(databases, report_id)
    if report is None:
        print("ERROR: report not found.", file=sys.stderr)
        return 2

    admission = diag.find_admission(report)
    if not admission:
        print("ERROR: strategic admission diagnostics not found.", file=sys.stderr)
        return 3

    event_ids, _, _ = diag.event_ids_from_admission(admission)
    event_rows, event_table_info = diag.locate_rows_by_id(databases, event_ids, "event_id")

    source_ids = {
        clean(event.get("source_id")).upper()
        for event in event_rows.values()
        if clean(event.get("source_id"))
    }
    source_rows, source_table_info = diag.locate_rows_by_id(databases, source_ids, "source_id")

    source_payloads = [
        diag.normalize_source_payload(source_id, source_rows[source_id])
        for source_id in sorted(source_ids)
        if source_id in source_rows
    ]
    persisted_events = [
        {
            k: v
            for k, v in event_rows[event_id].items()
            if not str(k).startswith("_diagnostic_")
        }
        for event_id in sorted(event_ids)
        if event_id in event_rows
    ]

    evidence_records, evidence_diag = diag.rebuild_evidence_deterministically(
        root=root,
        topic=topic,
        events=persisted_events,
        sources=source_payloads,
    )
    if not evidence_diag.get("success"):
        print(
            "ERROR: deterministic evidence rebuild failed: "
            f"{evidence_diag.get('error')}",
            file=sys.stderr,
        )
        return 4
    evid_by_event = evidence_by_event(evidence_records)

    raw_rows: list[dict[str, Any]] = []
    for event_id in sorted(event_ids):
        persisted = event_rows.get(event_id)
        if not isinstance(persisted, Mapping):
            continue
        event = {
            k: v
            for k, v in persisted.items()
            if not str(k).startswith("_diagnostic_")
        }
        source_id = clean(event.get("source_id")).upper()
        source_row = source_rows.get(source_id, {})
        source_text = diag.source_text_from_row(source_row)
        anchor = diag.event_anchor_text(event)
        raw_rows.append(
            {
                "event_id": event_id,
                "source_id": source_id,
                "event_text": anchor,
                "source_title": source_title(source_row),
                "source_text": source_text,
                "source_text_sha256": sha256_text(source_text),
                "evidence": evid_by_event.get(event_id, []),
            }
        )

    fingerprint = corpus_fingerprint(raw_rows)
    seed = args.seed if args.seed is not None else int(fingerprint[:12], 16)
    rng = random.Random(seed)
    rng.shuffle(raw_rows)

    source_alias_by_id: dict[str, str] = {}
    for row in raw_rows:
        source_id = row["source_id"]
        if source_id and source_id not in source_alias_by_id:
            source_alias_by_id[source_id] = f"S{len(source_alias_by_id) + 1:03d}"

    blind_rows: list[dict[str, Any]] = []
    key_rows: list[dict[str, Any]] = []
    evidence_counts: list[int] = []

    for index, row in enumerate(raw_rows, start=1):
        blind_id = f"B{index:03d}"
        source_blind_id = source_alias_by_id.get(row["source_id"], "S000")
        source_filename = f"{source_blind_id}.txt"
        evidence_filename = f"{blind_id}_evidence.txt"

        source_path = source_dir / source_filename
        if not source_path.exists():
            source_path.write_text(row["source_text"], encoding="utf-8")

        evidence_items = list(row["evidence"])
        evidence_counts.append(len(evidence_items))
        lines = [
            f"Blind event: {blind_id}",
            f"Source: {source_blind_id}",
            "",
        ]
        if not evidence_items:
            lines.append("[No deterministic evidence records reconstructed]")
        else:
            for ev_index, record in enumerate(evidence_items, start=1):
                lines.extend(
                    [
                        f"--- Evidence {ev_index} ---",
                        f"type: {clean(record.get('evidence_type') or record.get('type'))}",
                        evidence_text(record),
                        "",
                    ]
                )
        (evidence_dir / evidence_filename).write_text("\n".join(lines), encoding="utf-8")

        blind_row = {column: "" for column in BLIND_COLUMNS}
        blind_row.update(
            {
                "blind_id": blind_id,
                "source_blind_id": source_blind_id,
                "event_text": row["event_text"],
                "source_title": row["source_title"],
                "source_file": str(Path("sources") / source_filename),
                "evidence_file": str(Path("evidence") / evidence_filename),
                "definition_version": DEFINITION_VERSION,
            }
        )
        blind_rows.append(blind_row)
        key_rows.append(
            {
                "blind_id": blind_id,
                "event_id": row["event_id"],
                "source_blind_id": source_blind_id,
                "source_id": row["source_id"],
                "evidence_count": len(evidence_items),
                "source_text_sha256": row["source_text_sha256"],
            }
        )

    blind_csv = output_dir / "gold_set_blind.csv"
    with blind_csv.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=BLIND_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(blind_rows)

    blind_json = output_dir / "gold_set_blind.json"
    blind_json.write_text(
        json.dumps(
            {
                "tool_version": TOOL_VERSION,
                "definition_version": DEFINITION_VERSION,
                "topic": topic,
                "row_count": len(blind_rows),
                "blind_rows": blind_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    key_path = output_dir / "gold_set_key.json"
    key_path.write_text(
        json.dumps(
            {
                "tool_version": TOOL_VERSION,
                "definition_version": DEFINITION_VERSION,
                "report_id": report_id,
                "topic": topic,
                "corpus_fingerprint": fingerprint,
                "shuffle_seed": seed,
                "report_lookup": json_safe(report_lookup),
                "event_table_info": json_safe(event_table_info),
                "source_table_info": json_safe(source_table_info),
                "rows": key_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    definition_path = output_dir / "GOLD_DEFINITIONS.md"
    write_definitions(definition_path)

    hist = Counter("3+" if n >= 3 else str(n) for n in evidence_counts)
    manifest = {
        "tool_version": TOOL_VERSION,
        "definition_version": DEFINITION_VERSION,
        "report_id": report_id,
        "topic": topic,
        "row_count": len(blind_rows),
        "source_count": len(source_alias_by_id),
        "corpus_fingerprint": fingerprint,
        "evidence_histogram": {
            "0": hist.get("0", 0),
            "1": hist.get("1", 0),
            "2": hist.get("2", 0),
            "3+": hist.get("3+", 0),
        },
        "events_with_at_least_1_evidence": sum(n >= 1 for n in evidence_counts),
        "events_with_at_least_2_evidence": sum(n >= 2 for n in evidence_counts),
        "warning": "Do not inspect gold_set_key.json until manual labels are frozen.",
    }
    (output_dir / "gold_set_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("BLIND GOLD SET PREPARED")
    print(f"Version:     {TOOL_VERSION}")
    print(f"Rows:        {len(blind_rows)}")
    print(f"Sources:     {len(source_alias_by_id)}")
    print(f"Fingerprint: {fingerprint}")
    print(f"Definitions: {definition_path}")
    print(f"Labels:      {blind_csv}")
    print(f"Sources dir: {source_dir}")
    print(f"Evidence:    {evidence_dir}")
    print(f"Key:         {key_path}  <-- DO NOT OPEN UNTIL LABELS ARE FROZEN")
    print(
        "Evidence histogram: "
        f"0={hist.get('0', 0)} 1={hist.get('1', 0)} "
        f"2={hist.get('2', 0)} 3+={hist.get('3+', 0)}"
    )
    return 0


def normalized_label(value: Any) -> str:
    return clean(value)


def validate_choice(
    row_number: int,
    field: str,
    value: str,
    allowed: set[str],
    errors: list[str],
) -> None:
    if value not in allowed:
        errors.append(
            f"row {row_number} {field}: {value!r} not in {sorted(allowed)}"
        )


def derive_admission(row: Mapping[str, Any]) -> bool:
    return bool(
        normalized_label(row.get("article_topic")) in {"AI_PRIMARY", "AI_MATERIAL"}
        and normalized_label(row.get("event_ai_linkage"))
        in {"DIRECT_MATERIAL", "INDIRECT_MATERIAL"}
        and normalized_label(row.get("actor_role"))
        in {"action_subject", "passive_agent"}
        and normalized_label(row.get("identity_status"))
        in {"explicit", "source_local_alias", "strict_coreference", "bridging"}
        and bool(clean(row.get("canonical_actor")))
        and normalized_label(row.get("material_development")) == "YES"
    )


def derive(args: argparse.Namespace) -> int:
    labels_path = Path(args.labels).resolve()
    key_path = Path(args.key).resolve()
    output_path = Path(args.output).resolve() if args.output else labels_path.with_name("gold_set_derived.csv")
    json_output = output_path.with_suffix(".json")

    if not labels_path.exists():
        print(f"ERROR: labels file not found: {labels_path}", file=sys.stderr)
        return 2
    if not key_path.exists():
        print(f"ERROR: key file not found: {key_path}", file=sys.stderr)
        return 3

    key_payload = json.loads(key_path.read_text(encoding="utf-8"))
    key_rows = {
        clean(item.get("blind_id")): item
        for item in key_payload.get("rows", [])
        if isinstance(item, Mapping)
    }

    with labels_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        labels = [dict(row) for row in reader]

    errors: list[str] = []
    seen_blind_ids: set[str] = set()
    derived_rows: list[dict[str, Any]] = []

    for row_number, row in enumerate(labels, start=2):
        blind_id = clean(row.get("blind_id"))
        if not blind_id:
            errors.append(f"row {row_number}: missing blind_id")
            continue
        if blind_id in seen_blind_ids:
            errors.append(f"row {row_number}: duplicate blind_id={blind_id}")
            continue
        seen_blind_ids.add(blind_id)

        key = key_rows.get(blind_id)
        if key is None:
            errors.append(f"row {row_number}: blind_id={blind_id} missing from key")
            continue

        if clean(row.get("definition_version")) != key_payload.get("definition_version"):
            errors.append(
                f"row {row_number}: definition_version={row.get('definition_version')!r} "
                f"does not match {key_payload.get('definition_version')!r}"
            )

        validate_choice(row_number, "actor_role", normalized_label(row.get("actor_role")), ACTOR_ROLE_VALUES, errors)
        validate_choice(row_number, "actor_role_confidence", normalized_label(row.get("actor_role_confidence")), CONFIDENCE_VALUES, errors)
        validate_choice(row_number, "identity_status", normalized_label(row.get("identity_status")), IDENTITY_STATUS_VALUES, errors)
        validate_choice(row_number, "identity_relation", normalized_label(row.get("identity_relation")), IDENTITY_RELATION_VALUES, errors)
        validate_choice(row_number, "identity_confidence", normalized_label(row.get("identity_confidence")), CONFIDENCE_VALUES, errors)
        validate_choice(row_number, "article_topic", normalized_label(row.get("article_topic")), ARTICLE_TOPIC_VALUES, errors)
        validate_choice(row_number, "article_topic_confidence", normalized_label(row.get("article_topic_confidence")), CONFIDENCE_VALUES, errors)
        validate_choice(row_number, "event_ai_linkage", normalized_label(row.get("event_ai_linkage")), LINKAGE_VALUES, errors)
        validate_choice(row_number, "linkage_confidence", normalized_label(row.get("linkage_confidence")), CONFIDENCE_VALUES, errors)
        validate_choice(row_number, "material_development", normalized_label(row.get("material_development")), YES_NO_UNRESOLVED, errors)
        validate_choice(row_number, "material_development_confidence", normalized_label(row.get("material_development_confidence")), CONFIDENCE_VALUES, errors)

        identity_status = normalized_label(row.get("identity_status"))
        identity_relation = normalized_label(row.get("identity_relation"))
        canonical_actor = clean(row.get("canonical_actor"))
        actor_surface = clean(row.get("actor_surface"))

        if identity_status in {"explicit", "source_local_alias", "strict_coreference", "bridging"} and not canonical_actor:
            errors.append(
                f"row {row_number}: resolved identity_status={identity_status} requires canonical_actor"
            )
        if identity_status == "bridging" and identity_relation not in {
            "organizational_subunit_of",
            "subsidiary_of",
        }:
            errors.append(
                f"row {row_number}: bridging requires part/parent identity_relation"
            )
        if identity_status == "strict_coreference" and identity_relation != "same_entity":
            errors.append(
                f"row {row_number}: strict_coreference requires identity_relation=same_entity"
            )
        if normalized_label(row.get("actor_role")) in {"action_subject", "passive_agent"} and not actor_surface:
            errors.append(
                f"row {row_number}: actor role requires actor_surface"
            )

        derived = dict(row)
        derived.update(
            {
                "event_id": clean(key.get("event_id")).upper(),
                "source_id": clean(key.get("source_id")).upper(),
                "evidence_count": int(key.get("evidence_count") or 0),
                "should_admit": "YES" if derive_admission(row) else "NO",
            }
        )
        derived_rows.append(derived)

    missing = sorted(set(key_rows) - seen_blind_ids)
    if missing:
        errors.append(f"missing labelled rows: {', '.join(missing)}")

    if errors:
        print("GOLD SET VALIDATION FAILED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 4

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_columns = BLIND_COLUMNS + [
        "event_id",
        "source_id",
        "evidence_count",
        "should_admit",
    ]
    with output_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(derived_rows)

    uncertain_counts = Counter()
    for row in derived_rows:
        for field in (
            "actor_role_confidence",
            "identity_confidence",
            "article_topic_confidence",
            "linkage_confidence",
            "material_development_confidence",
        ):
            if normalized_label(row.get(field)) == "uncertain":
                uncertain_counts[field] += 1

    admitted_rows = [row for row in derived_rows if row["should_admit"] == "YES"]
    admitted_evidence_hist = Counter(
        "3+" if int(row["evidence_count"]) >= 3 else str(row["evidence_count"])
        for row in admitted_rows
    )
    summary = {
        "tool_version": TOOL_VERSION,
        "definition_version": key_payload.get("definition_version"),
        "corpus_fingerprint": key_payload.get("corpus_fingerprint"),
        "row_count": len(derived_rows),
        "derived_admitted_count": len(admitted_rows),
        "uncertain_counts": dict(uncertain_counts),
        "admitted_event_evidence_histogram": {
            "0": admitted_evidence_hist.get("0", 0),
            "1": admitted_evidence_hist.get("1", 0),
            "2": admitted_evidence_hist.get("2", 0),
            "3+": admitted_evidence_hist.get("3+", 0),
        },
        "admitted_events_with_at_least_2_evidence": sum(
            int(row["evidence_count"]) >= 2 for row in admitted_rows
        ),
        "rows": derived_rows,
    }
    json_output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("GOLD SET DERIVED")
    print(f"Rows:                         {len(derived_rows)}")
    print(f"Derived should_admit=YES:     {len(admitted_rows)}")
    print(f"Uncertain counts:             {dict(uncertain_counts)}")
    print(
        "Admitted evidence histogram:   "
        f"0={admitted_evidence_hist.get('0', 0)} "
        f"1={admitted_evidence_hist.get('1', 0)} "
        f"2={admitted_evidence_hist.get('2', 0)} "
        f"3+={admitted_evidence_hist.get('3+', 0)}"
    )
    print(
        "Admitted events with >=2 EVID: "
        f"{sum(int(row['evidence_count']) >= 2 for row in admitted_rows)}"
    )
    print(f"CSV:  {output_path}")
    print(f"JSON: {json_output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    prepare_parser = sub.add_parser("prepare", help="Build blind labeling artifacts")
    prepare_parser.add_argument("--project-root", default=".")
    prepare_parser.add_argument("--report-id", required=True)
    prepare_parser.add_argument("--topic", default="artificial intelligence")
    prepare_parser.add_argument("--output-dir", default="data/gold_set")
    prepare_parser.add_argument("--seed", type=int, default=None)
    prepare_parser.set_defaults(func=prepare)

    derive_parser = sub.add_parser("derive", help="Validate frozen labels and derive admission")
    derive_parser.add_argument("--labels", default="data/gold_set/gold_set_blind.csv")
    derive_parser.add_argument("--key", default="data/gold_set/gold_set_key.json")
    derive_parser.add_argument("--output", default="")
    derive_parser.set_defaults(func=derive)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
