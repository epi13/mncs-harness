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
                    route_reasons TEXT NOT NULL
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
                    verification_failures TEXT NOT NULL
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

    def begin_run(self, task: str, route: RoutePlan) -> int:
        fingerprint = hashlib.sha256(task.encode("utf-8")).hexdigest()
        prompt = task if self.store_prompt_text else None
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO runs(created_at, task_sha256, prompt_text, primary_role, route_reasons)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    fingerprint,
                    prompt,
                    route.primary_role,
                    json.dumps(route.reasons),
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
                    tool_call_count, verification_failures
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                       a.attempt_index, a.role, a.model, a.passed,
                       a.tool_call_count, a.eval_count, a.eval_duration_ns, a.error
                FROM attempts a
                JOIN runs r ON r.id = a.run_id
                ORDER BY a.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
