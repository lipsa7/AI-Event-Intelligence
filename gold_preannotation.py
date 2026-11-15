#!/usr/bin/env python3
"""Deterministic calibration selection. No LLM calls.

Run from the project root:
    python gold_preannotation.py

Reads:
    data/gold_set/gold_set_blind.csv
    data/gold_set/gold_set_manifest.json

Creates:
    calibration_selection.json
    calibration_manual.csv
    preannotation_manifest.json

The original CSV remains unchanged. After manual review, calibration labels
will be merged back by blind_id. gold_set_tool.py derive needs no code change.

The recovered preview mapping comes from an earlier saved script.
Original preview chat excerpts were not recovered.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import random
import sys
from collections import defaultdict
from pathlib import Path


VERSION = "gold-preannotation-selection-v1.2"
SELECTION_SEED = 20260928

EXPECTED_REPORT_ID = "REPORT-FF59222B19F714AB8918D407"
EXPECTED_DEFINITION_VERSION = "gold-semantics-v1.0"
EXPECTED_CORPUS_FINGERPRINT = (
    "75193842d1c9b3582b21c4af7b781cbb72375cc2fc52863c0b9eb60cb6b62391"
)

RECOVERED_PREVIEW_IDS = frozenset({
    "B012",
    "B013",
    "B024",
    "B031",
})

# Additional checks guard against blindly applying these IDs to
# a reshuffled CSV paired with an unchanged manifest.
PREVIEW_ANCHORS = {
    "B012": ("servier", "pharma.ai"),
    "B013": ("garutadustat", "bethesda"),
    "B024": ("lilly", "collaboration"),
    "B031": ("insilico", "interim results"),
}

PREVIEW_MARKER = "PRIOR_PREVIEW_EXPOSURE_LABEL_LAST"

PREVIEW_PROVENANCE = (
    "Recovered from an earlier saved gold_preannotation.py dated "
    "2026-08-30. Event descriptions matched against the supplied "
    "blind CSV; corresponding anchors also appear in "
    "test_strategic_topic_signal_service.py. "
    "Original preview chat excerpts were not recovered."
)

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

LABEL_COLUMNS = BLIND_COLUMNS[6:21]

OUTPUT_NAMES = (
    "calibration_selection.json",
    "calibration_manual.csv",
    "preannotation_manifest.json",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def select(gold_dir: Path) -> None:
    gold_dir = gold_dir.resolve()
    outputs = [gold_dir / name for name in OUTPUT_NAMES]

    for path in outputs:
        if os.path.lexists(path):
            raise FileExistsError(
                f"Refusing to overwrite existing artifact: {path}"
            )

    csv_path = gold_dir / "gold_set_blind.csv"
    manifest_path = gold_dir / "gold_set_manifest.json"

    csv_raw = csv_path.read_bytes()
    manifest_raw = manifest_path.read_bytes()

    manifest = json.loads(manifest_raw.decode("utf-8-sig"))
    require(
        isinstance(manifest, dict),
        "Gold manifest must be a JSON object.",
    )

    require(
        manifest.get("report_id") == EXPECTED_REPORT_ID,
        "Report ID changed. Recovered preview IDs cannot be reused.",
    )
    require(
        manifest.get("corpus_fingerprint")
        == EXPECTED_CORPUS_FINGERPRINT,
        "Corpus fingerprint changed. Recovered preview IDs "
        "cannot be reused.",
    )
    require(
        manifest.get("definition_version")
        == EXPECTED_DEFINITION_VERSION,
        "Definition version changed. Review the frozen protocol.",
    )

    reader = csv.DictReader(
        io.StringIO(csv_raw.decode("utf-8-sig"), newline="")
    )
    require(
        reader.fieldnames == BLIND_COLUMNS,
        "CSV header differs from the complete "
        "gold_set_tool.py BLIND_COLUMNS.",
    )

    rows = list(reader)

    require(
        len(rows) == 38,
        f"Expected 38 rows; found {len(rows)}. "
        "The blind set may have been regenerated; "
        "verify the frozen corpus.",
    )
    require(
        all(
            None not in row
            and all(value is not None for value in row.values())
            for row in rows
        ),
        "Malformed CSV row: extra or missing cells.",
    )
    require(
        manifest.get("row_count") == len(rows),
        "Manifest row_count mismatch.",
    )

    definition = manifest["definition_version"]

    seen: set[str] = set()
    rows_by_id: dict[str, dict[str, str]] = {}

    for row in rows:
        bid = row["blind_id"]
        sid = row["source_blind_id"]

        require(
            bool(bid.strip()) and bid == bid.strip(),
            "Blank/untrimmed blind_id.",
        )
        require(
            bool(sid.strip()) and sid == sid.strip(),
            f"Invalid source ID: {bid}",
        )
        require(
            bid not in seen,
            f"Duplicate blind_id: {bid}",
        )
        require(
            row["definition_version"] == definition,
            f"Definition version mismatch: {bid}",
        )

        seen.add(bid)
        rows_by_id[bid] = row

    require(
        RECOVERED_PREVIEW_IDS.issubset(rows_by_id),
        "One or more recovered preview rows are missing.",
    )

    for bid, anchors in PREVIEW_ANCHORS.items():
        row = rows_by_id[bid]
        event_text = " ".join(row["event_text"].casefold().split())

        require(
            row["source_blind_id"] == "S002",
            f"Recovered preview row {bid} no longer belongs to S002. "
            "The blind set may have been regenerated.",
        )
        require(
            all(anchor in event_text for anchor in anchors),
            f"Event text for {bid} does not match its recovered anchor. "
            "Do not apply this preview mapping to a different blind set.",
        )

    # Notes do not determine preview membership. However, an explicit
    # conflicting marker indicates inconsistent input and must be resolved.
    marked_ids = {
        row["blind_id"]
        for row in rows
        if row["label_notes"].strip() == PREVIEW_MARKER
    }
    require(
        not marked_ids or marked_ids == RECOVERED_PREVIEW_IDS,
        "Explicit preview markers conflict with the recovered mapping.",
    )

    all_by_source: dict[str, list[dict[str, str]]] = defaultdict(list)
    eligible: dict[str, list[dict[str, str]]] = defaultdict(list)
    preview: dict[str, list[dict[str, str]]] = defaultdict(list)

    for row in rows:
        bid = row["blind_id"]
        sid = row["source_blind_id"]

        all_by_source[sid].append(row)

        is_preview = bid in RECOVERED_PREVIEW_IDS
        target = preview if is_preview else eligible
        target[sid].append(row)

    missing = sorted(
        sid for sid in all_by_source if not eligible[sid]
    )
    require(
        not missing,
        f"Sources with NO non-preview row: {missing}",
    )
    require(
        len(all_by_source) == 8,
        f"Expected 8 sources; found {len(all_by_source)}. "
        "The blind set may have been regenerated.",
    )
    require(
        manifest.get("source_count") == len(all_by_source),
        "Manifest source_count mismatch.",
    )

    preview_ids = sorted(
        row["blind_id"]
        for group in preview.values()
        for row in group
    )
    require(
        len(preview_ids) == 4,
        f"Expected 4 preview rows; found {len(preview_ids)}. "
        "The blind set may have been regenerated.",
    )
    require(
        sum(map(len, eligible.values())) == 34,
        "Expected 34 non-preview rows. "
        "The blind set may have been regenerated.",
    )

    # Independent generator per source, using the fixed literal seed.
    selected = [
        random.Random(f"{SELECTION_SEED}:{sid}").choice(
            sorted(
                eligible[sid],
                key=lambda row: row["blind_id"],
            )
        )
        for sid in sorted(all_by_source)
    ]

    selected_ids = {row["blind_id"] for row in selected}

    draft_ids = sorted(
        row["blind_id"]
        for group in eligible.values()
        for row in group
        if row["blind_id"] not in selected_ids
    )
    require(
        len(draft_ids) == 26,
        "Expected 26 remaining draft-eligible rows. "
        "The blind set may have been regenerated.",
    )

    counts = {
        sid: {
            "total_rows": len(all_by_source[sid]),
            "non_preview_rows": len(eligible[sid]),
            "preview_rows": len(preview[sid]),
        }
        for sid in sorted(all_by_source)
    }

    draws = {
        row["source_blind_id"]: row["blind_id"]
        for row in selected
    }
    csv_hash = sha256(csv_raw)

    # Print all diagnostics before writing any output.
    print(f"Blind CSV SHA-256: {csv_hash}")
    print(f"Literal seed: {SELECTION_SEED}")
    print("Preview identification: recovered corpus-specific ID mapping")
    print("Preview IDs: " + ", ".join(preview_ids))
    print(
        "source_blind_id | total | non-preview | preview | "
        "calibration blind_id"
    )

    for sid, count in counts.items():
        print(
            f"{sid} | {count['total_rows']} | "
            f"{count['non_preview_rows']} | "
            f"{count['preview_rows']} | {draws[sid]}"
        )

    sys.stdout.flush()

    preview_metadata = {
        "preview_detection": "recovered corpus-specific blind-ID mapping",
        "preview_mapping_provenance": PREVIEW_PROVENANCE,
        "preview_original_chat_verified": False,
        "preview_manual_last_blind_ids": preview_ids,
        "corpus_fingerprint": manifest["corpus_fingerprint"],
        "preview_anchor_checks_passed": True,
    }

    selection = {
        "version": VERSION,
        "seed": SELECTION_SEED,
        "algorithm": (
            "per-source random.Random("
            "f'{seed}:{source_blind_id}').choice; sorted blind IDs"
        ),
        "python_version": sys.version.split()[0],
        "blind_csv_sha256": csv_hash,
        "gold_manifest_sha256": sha256(manifest_raw),
        "definition_version": definition,
        "rows_per_source": counts,
        "selected_by_source": draws,
        "calibration_blind_ids": [
            row["blind_id"] for row in selected
        ],
        "draft_eligible_blind_ids": draft_ids,
        **preview_metadata,
    }

    csv_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        csv_buffer,
        fieldnames=BLIND_COLUMNS,
        lineterminator="\n",
    )
    writer.writeheader()

    for row in selected:
        clean_row = dict(row)
        for column in LABEL_COLUMNS:
            clean_row[column] = ""
        writer.writerow(clean_row)

    manual_raw = csv_buffer.getvalue().encode("utf-8-sig")
    selection_raw = json_bytes(selection)

    preannotation = {
        "version": VERSION,
        "stage": "selection_only_manual_calibration_pending",
        "seed": SELECTION_SEED,
        "llm_called": False,
        "calibration_label_origin": "pending_unaided_human",
        "llm_model": None,
        "llm_prompt_version": None,
        "drafts_generated": False,
        "agreement_measured": False,
        "sampling_design": (
            "one non-preview event per source; source-stratified"
        ),
        "sampling_limitation": (
            "Unequal event counts per source: calibration agreement "
            "is not a representative event-level estimate; "
            "8 rows do not justify a numeric gate."
        ),
        "label_merge_policy": (
            "After human review, merge LABEL_COLUMNS by blind_id "
            "into gold_set_blind.csv; verify source_blind_id and "
            "definition_version, preserve other rows and metadata, "
            "and reject conflicting existing labels. "
            "No merge occurs here. "
            "derive continues reading gold_set_blind.csv unchanged."
        ),
        "label_columns": LABEL_COLUMNS,
        "blind_csv_sha256": csv_hash,
        "gold_manifest_sha256": sha256(manifest_raw),
        "calibration_selection_sha256": sha256(selection_raw),
        "calibration_manual_initial_sha256": sha256(manual_raw),
        "definition_version": definition,
        "split_counts": {
            "calibration": 8,
            "draft_eligible": 26,
            "preview_last": 4,
        },
        **preview_metadata,
    }

    # Detect input edits made during this run.
    require(
        csv_path.read_bytes() == csv_raw,
        "Blind CSV changed during selection.",
    )
    require(
        manifest_path.read_bytes() == manifest_raw,
        "Manifest changed during selection.",
    )

    payloads = (
        selection_raw,
        manual_raw,
        json_bytes(preannotation),
    )

    # Exclusive creation: never overwrite existing artifacts.
    # On interrupted writes, retain partial files for inspection.
    for path, payload in zip(outputs, payloads):
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    print("Created: " + ", ".join(OUTPUT_NAMES))
    print("Original gold_set_blind.csv was not modified.")
    print(
        "Next: manually label calibration_manual.csv "
        "using the frozen rubric and source files."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gold-dir",
        type=Path,
        default=Path("data/gold_set"),
    )
    args = parser.parse_args()

    try:
        select(args.gold_dir)
    except (OSError, ValueError, csv.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())