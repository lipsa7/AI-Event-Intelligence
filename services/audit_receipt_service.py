"""
Tamper-evident audit receipt service.

Recommended location:
    services/audit_receipt_service.py

Use this instead of creating a separate article_receipt_service.py unless
article ingestion later gets its own distinct compliance/audit requirement.

Purpose
-------
Record what happened during a pipeline run:
- run / receipt IDs
- intent
- blueprint ID/version
- policy versions
- model / prompt versions
- evidence / source / event IDs
- quality measurements
- policy checks
- routing reason
- final outcome
- input / output fingerprints
- previous receipt hash
- receipt hash
- optional HMAC signature

Important:
- This proves integrity / tamper evidence of the recorded receipt.
- It does not prove AI correctness.
- It does not decide release.
- It does not make receipts immutable or undeletable.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence


RECEIPT_SCHEMA_VERSION = "1.0"


class AuditReceiptError(RuntimeError):
    pass


class AuditReceiptValidationError(AuditReceiptError):
    pass


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return value.isoformat()

    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]

    return str(value)


def _unique_strings(values: Any) -> list[str]:
    if values is None:
        return []

    if isinstance(values, str):
        values = [values]

    if not isinstance(values, (list, tuple, set)):
        values = [values]

    result = []
    seen = set()

    for value in values:
        if isinstance(value, Mapping):
            value = (
                value.get("id")
                or value.get("evidence_id")
                or value.get("source_id")
                or value.get("event_id")
                or ""
            )

        text = _clean(value)

        if text and text not in seen:
            seen.add(text)
            result.append(text)

    return result


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        _json_safe(dict(payload)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def fingerprint_payload(payload: Any) -> str:
    if isinstance(payload, Mapping):
        raw = canonical_json_bytes(payload)
    else:
        raw = json.dumps(
            _json_safe(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    return sha256_hex(raw)


def _hashable_receipt(receipt: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in receipt.items()
        if key not in {"receipt_hash", "signature"}
    }


def compute_receipt_hash(receipt: Mapping[str, Any]) -> str:
    return sha256_hex(
        canonical_json_bytes(_hashable_receipt(receipt))
    )


def sign_receipt_hash(
    receipt_hash: str,
    secret_key: str | bytes,
) -> str:
    if isinstance(secret_key, str):
        secret_key = secret_key.encode("utf-8")

    return hmac.new(
        secret_key,
        receipt_hash.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class AuditReceiptService:
    def __init__(
        self,
        storage_dir: str | Path = "data/audit_receipts",
        *,
        signing_key: str | bytes | None = None,
        signing_key_env: str | None = None,
    ) -> None:
        if signing_key is not None and signing_key_env is not None:
            raise AuditReceiptValidationError(
                "Provide signing_key or signing_key_env, not both."
            )

        if signing_key_env is not None:
            signing_key = os.getenv(signing_key_env)

            if signing_key is None:
                raise AuditReceiptValidationError(
                    f"Environment variable {signing_key_env!r} is not set."
                )

        self.storage_dir = Path(storage_dir)
        self.signing_key = signing_key

    def create_receipt(
        self,
        *,
        run_id: str,
        final_outcome: str,
        intent: Optional[Mapping[str, Any]] = None,
        blueprint: Optional[Mapping[str, Any]] = None,
        policy_versions: Optional[Mapping[str, Any]] = None,
        model_versions: Optional[Mapping[str, Any]] = None,
        prompt_versions: Optional[Mapping[str, Any]] = None,
        evidence_ids: Optional[Iterable[str]] = None,
        source_ids: Optional[Iterable[str]] = None,
        event_ids: Optional[Iterable[str]] = None,
        quality_measurements: Optional[Mapping[str, Any]] = None,
        policy_checks: Optional[Sequence[Mapping[str, Any]]] = None,
        routing_reason: str = "",
        input_artifact: Any = None,
        output_artifact: Any = None,
        input_fingerprint: str = "",
        output_fingerprint: str = "",
        previous_receipt_hash: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
        created_at: str = "",
    ) -> Dict[str, Any]:
        run_id = _clean(run_id)
        final_outcome = _clean(final_outcome)

        if not run_id:
            raise AuditReceiptValidationError("run_id cannot be empty.")

        if not final_outcome:
            raise AuditReceiptValidationError(
                "final_outcome cannot be empty."
            )

        created_at = _clean(created_at) or _utc_now_iso()

        if not input_fingerprint and input_artifact is not None:
            input_fingerprint = fingerprint_payload(input_artifact)

        if not output_fingerprint and output_artifact is not None:
            output_fingerprint = fingerprint_payload(output_artifact)

        receipt_id = "RECEIPT-" + sha256_hex(
            canonical_json_bytes(
                {
                    "run_id": run_id,
                    "created_at": created_at,
                    "previous_receipt_hash": _clean(previous_receipt_hash),
                }
            )
        )[:24].upper()

        receipt: Dict[str, Any] = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "receipt_id": receipt_id,
            "run_id": run_id,
            "created_at": created_at,
            "intent": _json_safe(dict(intent or {})),
            "blueprint": _json_safe(dict(blueprint or {})),
            "policy_versions": _json_safe(dict(policy_versions or {})),
            "model_versions": _json_safe(dict(model_versions or {})),
            "prompt_versions": _json_safe(dict(prompt_versions or {})),
            "evidence_ids": _unique_strings(evidence_ids),
            "source_ids": _unique_strings(source_ids),
            "event_ids": _unique_strings(event_ids),
            "quality_measurements": _json_safe(
                dict(quality_measurements or {})
            ),
            "policy_checks": [
                _json_safe(dict(check))
                for check in (policy_checks or [])
                if isinstance(check, Mapping)
            ],
            "routing_reason": _clean(routing_reason),
            "final_outcome": final_outcome,
            "input_fingerprint": _clean(input_fingerprint),
            "output_fingerprint": _clean(output_fingerprint),
            "previous_receipt_hash": _clean(previous_receipt_hash),
            "hash_algorithm": "sha256",
            "signature_algorithm": (
                "hmac-sha256" if self.signing_key is not None else ""
            ),
            "metadata": _json_safe(dict(metadata or {})),
        }

        receipt["receipt_hash"] = compute_receipt_hash(receipt)

        receipt["signature"] = (
            sign_receipt_hash(
                receipt["receipt_hash"],
                self.signing_key,
            )
            if self.signing_key is not None
            else ""
        )

        return receipt

    def verify_receipt(
        self,
        receipt: Mapping[str, Any],
        *,
        expected_previous_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        stored_hash = _clean(receipt.get("receipt_hash"))
        computed_hash = compute_receipt_hash(receipt)

        valid_hash = bool(
            stored_hash
            and hmac.compare_digest(stored_hash, computed_hash)
        )

        signature = _clean(receipt.get("signature"))

        if signature:
            if self.signing_key is None:
                valid_signature: Optional[bool] = None
            else:
                expected_signature = sign_receipt_hash(
                    stored_hash,
                    self.signing_key,
                )
                valid_signature = hmac.compare_digest(
                    signature,
                    expected_signature,
                )
        else:
            valid_signature = None

        if expected_previous_hash is None:
            previous_hash_matches: Optional[bool] = None
        else:
            previous_hash_matches = (
                _clean(receipt.get("previous_receipt_hash"))
                == _clean(expected_previous_hash)
            )

        schema_version_supported = (
            _clean(receipt.get("schema_version"))
            == RECEIPT_SCHEMA_VERSION
        )

        valid = (
            valid_hash
            and schema_version_supported
            and valid_signature is not False
            and previous_hash_matches is not False
        )

        return {
            "valid_hash": valid_hash,
            "valid_signature": valid_signature,
            "previous_hash_matches": previous_hash_matches,
            "schema_version_supported": schema_version_supported,
            "valid": valid,
        }

    def save_receipt(
        self,
        receipt: Mapping[str, Any],
    ) -> Path:
        verification = self.verify_receipt(receipt)

        if not verification["valid"]:
            raise AuditReceiptValidationError(
                "Refusing to save an invalid receipt."
            )

        receipt_id = _clean(receipt.get("receipt_id"))

        if not receipt_id:
            raise AuditReceiptValidationError(
                "receipt_id is missing."
            )

        self.storage_dir.mkdir(parents=True, exist_ok=True)

        path = self.storage_dir / f"{receipt_id}.json"
        temp_path = path.with_suffix(".json.tmp")

        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(
                _json_safe(dict(receipt)),
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )

        temp_path.replace(path)

        return path

    def load_receipt(
        self,
        receipt_id: str,
    ) -> Dict[str, Any]:
        receipt_id = _clean(receipt_id)

        if not receipt_id:
            raise AuditReceiptValidationError(
                "receipt_id cannot be empty."
            )

        path = self.storage_dir / f"{receipt_id}.json"

        if not path.exists():
            raise AuditReceiptError(
                f"Receipt not found: {receipt_id}"
            )

        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if not isinstance(payload, dict):
            raise AuditReceiptError(
                "Receipt file must contain a JSON object."
            )

        return payload

    def verify_chain(
        self,
        receipts: Sequence[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        results = []
        previous_hash = ""

        for index, receipt in enumerate(receipts):
            result = self.verify_receipt(
                receipt,
                expected_previous_hash=(
                    previous_hash if index > 0 else ""
                ),
            )

            results.append(
                {
                    "receipt_id": receipt.get("receipt_id"),
                    **result,
                }
            )

            previous_hash = _clean(
                receipt.get("receipt_hash")
            )

        return {
            "valid": all(item["valid"] for item in results),
            "receipt_count": len(results),
            "results": results,
        }