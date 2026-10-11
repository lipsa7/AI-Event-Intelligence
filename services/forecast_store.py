"""Append-only binary forecast ledger. No automatic or model-invented outcomes.

Run `python -m services.forecast_store list` or `resolve --help` from the project.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


class ForecastStore:
    def __init__(self, path: str | Path = "data/forecasts/forecasts.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS forecasts (
                    forecast_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS resolutions (
                    forecast_id TEXT PRIMARY KEY REFERENCES forecasts(forecast_id),
                    resolved_at TEXT NOT NULL, outcome INTEGER NOT NULL CHECK(outcome IN (0,1)),
                    evidence_url TEXT NOT NULL, evidence_note TEXT NOT NULL, brier_score REAL);
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            # sqlite3's context manager commits/rolls back a transaction; it
            # does not close the connection. Close explicitly on every exit,
            # including setup errors, validation errors and failed SQL.
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def contract(candidate: Mapping[str, Any]) -> dict[str, Any]:
        meta = candidate.get("metadata") or {}
        raw = meta.get("forecast_contract") or candidate.get("forecast_contract") or {}
        likelihood = meta.get("likelihood_assessment") or {}
        probability = likelihood.get("numeric_probability")
        if isinstance(probability, bool) or not isinstance(probability, (float, int)) or not math.isfinite(probability) or not 0 <= probability <= 1:
            probability = None
        if probability is not None and not (
            likelihood.get("numeric_basis") and (likelihood.get("probability_evidence_ids")
                or (likelihood.get("base_rate") or {}).get("evidence_ids"))
        ):
            probability = None
        return {
            "question": str(raw.get("question") or "").strip(),
            "deadline": str(raw.get("deadline") or "").strip(),
            "resolution_criteria": str(raw.get("resolution_criteria") or "").strip(),
            "probability": probability, "likelihood_label": likelihood.get("label"),
            "numeric_basis": likelihood.get("numeric_basis"),
            "probability_evidence_ids": likelihood.get("probability_evidence_ids") or [],
            "base_rate": likelihood.get("base_rate") or {},
        }

    def register(self, *, run_id: str, candidate: Mapping[str, Any], created_at: str = "") -> dict[str, Any]:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00")) if created_at else datetime.now(timezone.utc)
        if created.tzinfo is None:
            raise ValueError("Forecast creation time requires a timezone")
        data = self.contract(candidate)
        missing = [key for key in ("question", "deadline", "resolution_criteria") if not data[key]]
        try:
            deadline = datetime.fromisoformat(data["deadline"].replace("Z", "+00:00"))
            if deadline.tzinfo is None or deadline <= created:
                missing.append("future_deadline_with_timezone")
        except ValueError:
            missing.append("future_deadline_with_timezone")
        if data["probability"] is None:
            missing.append("supported_numeric_probability")
        data.update({
            "insight_id": candidate.get("insight_id"), "insight_revision_id": candidate.get("insight_revision_id"),
            "claim": candidate.get("insight"), "evidence_ids": candidate.get("supporting_evidence_ids") or [],
            "research": (candidate.get("metadata") or {}).get("hypothesis_research") or {},
            "scorable": not missing, "missing_for_scoring": sorted(set(missing)),
        })
        encoded = json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False)
        fid = "FORECAST-" + hashlib.sha256((run_id + encoded).encode()).hexdigest()[:24].upper()
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO forecasts VALUES(?,?,?,?)", (fid, run_id, created.isoformat(), encoded))
        return {"forecast_id": fid, **data}

    def resolve(self, forecast_id: str, *, outcome: int, evidence_url: str, evidence_note: str) -> float:
        if type(outcome) is not int or outcome not in (0, 1):
            raise ValueError("Outcome must be 0 or 1")
        if not evidence_url.startswith(("https://", "http://")) or not evidence_note.strip():
            raise ValueError("Resolution requires a source URL and an explanation against the frozen criteria")
        with self._connect() as db:
            row = db.execute("SELECT payload FROM forecasts WHERE forecast_id=?", (forecast_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown forecast")
            data = json.loads(row["payload"])
            if not data["scorable"]:
                raise ValueError("Forecast lacks a frozen, scorable question/deadline/probability")
            deadline = datetime.fromisoformat(data["deadline"].replace("Z", "+00:00"))
            if outcome == 0 and datetime.now(timezone.utc) < deadline:
                raise ValueError("A negative by-deadline outcome cannot resolve before its deadline")
            score = (data["probability"] - outcome) ** 2
            db.execute("INSERT INTO resolutions VALUES(?,?,?,?,?,?)", (
                forecast_id, datetime.now(timezone.utc).isoformat(), outcome, evidence_url, evidence_note, score))
            return score

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT f.*, r.outcome, r.brier_score, r.resolved_at FROM forecasts f LEFT JOIN resolutions r USING(forecast_id) ORDER BY f.created_at").fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/forecasts/forecasts.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    resolve = commands.add_parser("resolve")
    resolve.add_argument("forecast_id")
    resolve.add_argument("--outcome", type=int, choices=(0, 1), required=True)
    resolve.add_argument("--evidence-url", required=True)
    resolve.add_argument("--evidence-note", required=True)
    args = parser.parse_args()
    store = ForecastStore(args.db)
    if args.command == "list":
        print(json.dumps(store.list(), indent=2, ensure_ascii=False))
    else:
        print(json.dumps({"brier_score": store.resolve(args.forecast_id, outcome=args.outcome,
            evidence_url=args.evidence_url, evidence_note=args.evidence_note)}))
