from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import ModelAttempt, RoutePlan


class MetricsStore:
    def __init__(self, path: Path, store_prompt_text: bool = False):
        self.path = path.expanduser()
        self.store_prompt_text = store_prompt_text
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        declaration: str,
    ) -> None:
        existing = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
            )

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    task_sha256 TEXT NOT NULL,
                    prompt_text TEXT,
                    primary_role TEXT NOT NULL,
                    route_reasons TEXT NOT NULL,
                    semantic_backend TEXT,
                    semantic_revision TEXT,
                    semantic_lane TEXT,
                    semantic_score REAL,
                    semantic_margin REAL,
                    semantic_latency_ms REAL,
                    semantic_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(id),
                    attempt_index INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    model TEXT NOT NULL,
                    escalated_from TEXT,
                    passed INTEGER NOT NULL,
                    error TEXT,
                    total_duration_ns INTEGER,
                    load_duration_ns INTEGER,
                    prompt_eval_count INTEGER,
                    prompt_eval_duration_ns INTEGER,
                    eval_count INTEGER,
                    eval_duration_ns INTEGER,
                    tool_call_count INTEGER NOT NULL,
                    verification_failures TEXT NOT NULL,
                    provider TEXT,
                    backend TEXT,
                    fabric_enabled INTEGER,
                    execution_source TEXT,
                    fabric_worker TEXT,
                    placement_mode TEXT,
                    accelerator_backend TEXT,
                    precision TEXT,
                    placement_reason TEXT,
                    placement_reason_code TEXT,
                    fabric_request_identity TEXT,
                    resource_snapshot_identity TEXT,
                    fabric_record_identity TEXT,
                    fabric_receipt_identity TEXT,
                    fabric_dispatch_ms REAL,
                    provider_latency_ms REAL,
                    tokens_per_second REAL
                );
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    attempt_id INTEGER NOT NULL REFERENCES attempts(id),
                    tool_name TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    risk TEXT NOT NULL,
                    reason TEXT NOT NULL
                );
                """
            )
            for column, declaration in (
                ("semantic_backend", "TEXT"),
                ("semantic_revision", "TEXT"),
                ("semantic_lane", "TEXT"),
                ("semantic_score", "REAL"),
                ("semantic_margin", "REAL"),
                ("semantic_latency_ms", "REAL"),
                ("semantic_reason", "TEXT"),
                ("provider", "TEXT"),
                ("backend", "TEXT"),
                ("fabric_enabled", "INTEGER"),
                ("execution_source", "TEXT"),
                ("fabric_worker", "TEXT"),
                ("placement_mode", "TEXT"),
                ("accelerator_backend", "TEXT"),
                ("precision", "TEXT"),
                ("placement_reason", "TEXT"),
                ("placement_reason_code", "TEXT"),
                ("fabric_request_identity", "TEXT"),
                ("resource_snapshot_identity", "TEXT"),
                ("fabric_record_identity", "TEXT"),
                ("fabric_receipt_identity", "TEXT"),
                ("fabric_dispatch_ms", "REAL"),
                ("provider_latency_ms", "REAL"),
                ("tokens_per_second", "REAL"),
            ):
                self._ensure_column(connection, "runs", column, declaration)

    def begin_run(self, task: str, route: RoutePlan) -> int:
        fingerprint = hashlib.sha256(task.encode("utf-8")).hexdigest()
        prompt = task if self.store_prompt_text else None
        semantic = route.semantic
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO runs(
                    created_at, task_sha256, prompt_text, primary_role, route_reasons,
                    semantic_backend, semantic_revision, semantic_lane, semantic_score,
                    semantic_margin, semantic_latency_ms, semantic_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    fingerprint,
                    prompt,
                    route.primary_role,
                    json.dumps(route.reasons),
                    semantic.backend if semantic else None,
                    semantic.revision if semantic else None,
                    route.lane,
                    semantic.selected_score if semantic else None,
                    semantic.margin if semantic else None,
                    semantic.latency_ms if semantic else None,
                    semantic.reason if semantic else None,
                ),
            )
            return int(cursor.lastrowid)

    def record_attempt(
        self,
        run_id: int,
        index: int,
        attempt: ModelAttempt,
        escalated_from: str | None,
    ) -> None:
        metrics = attempt.metrics
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO attempts(
                    run_id, attempt_index, role, model, escalated_from, passed, error,
                    total_duration_ns, load_duration_ns, prompt_eval_count,
                    prompt_eval_duration_ns, eval_count, eval_duration_ns,
                    tool_call_count, verification_failures, provider, backend,
                    fabric_enabled, execution_source, fabric_worker, placement_mode,
                    accelerator_backend, precision, placement_reason,
                    placement_reason_code, fabric_request_identity,
                    resource_snapshot_identity, fabric_record_identity,
                    fabric_receipt_identity, fabric_dispatch_ms, provider_latency_ms,
                    tokens_per_second
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    run_id,
                    index,
                    attempt.role,
                    attempt.model,
                    escalated_from,
                    int(attempt.verification.passed),
                    attempt.error,
                    metrics.get("total_duration"),
                    metrics.get("load_duration"),
                    metrics.get("prompt_eval_count"),
                    metrics.get("prompt_eval_duration"),
                    metrics.get("eval_count"),
                    metrics.get("eval_duration"),
                    len(attempt.tool_executions),
                    json.dumps(attempt.verification.failures),
                    metrics.get("provider"),
                    metrics.get("backend"),
                    int(bool(metrics["fabric_enabled"])) if "fabric_enabled" in metrics else None,
                    metrics.get("execution_source"),
                    metrics.get("fabric_worker"),
                    metrics.get("placement_mode"),
                    metrics.get("accelerator_backend"),
                    metrics.get("precision"),
                    metrics.get("placement_reason"),
                    metrics.get("placement_reason_code"),
                    metrics.get("fabric_request_identity"),
                    metrics.get("resource_snapshot_identity"),
                    metrics.get("fabric_record_identity"),
                    metrics.get("fabric_receipt_identity"),
                    metrics.get("fabric_dispatch_ms"),
                    metrics.get("provider_latency_ms"),
                    metrics.get("tokens_per_second"),
                ),
            )
            attempt_id = int(cursor.lastrowid)
            connection.executemany(
                """
                INSERT INTO tool_calls(attempt_id, tool_name, success, risk, reason)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        attempt_id,
                        execution.name,
                        int(execution.success),
                        execution.decision.risk,
                        execution.decision.reason,
                    )
                    for execution in attempt.tool_executions
                ],
            )

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT r.created_at, r.task_sha256, r.primary_role,
                       r.semantic_backend, r.semantic_revision, r.semantic_lane,
                       r.semantic_score, r.semantic_margin, r.semantic_latency_ms,
                       a.attempt_index, a.role, a.model, a.passed,
                       a.tool_call_count, a.eval_count, a.eval_duration_ns, a.error,
                       a.provider, a.backend, a.fabric_enabled, a.execution_source,
                       a.fabric_worker, a.placement_mode, a.accelerator_backend,
                       a.precision, a.placement_reason, a.placement_reason_code,
                       a.fabric_request_identity, a.resource_snapshot_identity,
                       a.fabric_record_identity, a.fabric_receipt_identity,
                       a.fabric_dispatch_ms, a.provider_latency_ms, a.tokens_per_second
                FROM attempts a
                JOIN runs r ON r.id = a.run_id
                ORDER BY a.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
