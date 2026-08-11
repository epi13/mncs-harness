from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from epi13_local_harness.metrics import MetricsStore
from epi13_local_harness.models import (
    ModelAttempt,
    RoutePlan,
    SemanticRouteResult,
    SessionTargets,
    TaskProfile,
    VerificationResult,
)


class MetricsTests(unittest.TestCase):
    @staticmethod
    def _simple_plan() -> RoutePlan:
        return RoutePlan(
            primary_role="e2b",
            escalation_roles=(),
            reasons=("simple",),
            profile=TaskProfile(
                text="hello",
                word_count=1,
                has_code=False,
                asks_for_edit=False,
                asks_for_execution=False,
                asks_for_explanation=False,
                is_high_risk=False,
                is_complex=False,
                has_image=False,
                file_reference_count=0,
            ),
        )

    def test_existing_database_is_migrated_for_semantic_and_provider_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.executescript(
                    """
                    CREATE TABLE runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL,
                        task_sha256 TEXT NOT NULL,
                        prompt_text TEXT,
                        primary_role TEXT NOT NULL,
                        route_reasons TEXT NOT NULL
                    );
                    CREATE TABLE attempts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id INTEGER NOT NULL,
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
                    CREATE TABLE tool_calls (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        attempt_id INTEGER NOT NULL,
                        tool_name TEXT NOT NULL,
                        success INTEGER NOT NULL,
                        risk TEXT NOT NULL,
                        reason TEXT NOT NULL
                    );
                    """
                )

            store = MetricsStore(path)
            with sqlite3.connect(path) as connection:
                run_columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(runs)").fetchall()
                }
                attempt_columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(attempts)").fetchall()
                }
            self.assertIn("semantic_backend", run_columns)
            self.assertIn("semantic_latency_ms", run_columns)
            self.assertNotIn("provider", run_columns)
            self.assertIn("provider", attempt_columns)
            self.assertIn("fabric_worker", attempt_columns)
            self.assertIn("placement_mode", attempt_columns)
            self.assertIn("tokens_per_second", attempt_columns)
            self.assertIn("inference_target", attempt_columns)
            self.assertIn("workspace_target", attempt_columns)
            self.assertIn("tool_execution_target", attempt_columns)

            run_id = store.begin_run("hello", self._simple_plan())
            store.record_attempt(
                run_id,
                0,
                ModelAttempt(
                    role="e2b",
                    model="fixture",
                    content="ok",
                    thinking="",
                    metrics={
                        "provider": "ollama",
                        "execution_source": "local",
                    },
                    tool_executions=[],
                    verification=VerificationResult(True, (), ()),
                ),
                None,
            )
            row = store.recent(1)[0]
            self.assertEqual(row["provider"], "ollama")
            self.assertEqual(row["execution_source"], "local")

    def test_attempt_records_physical_placement_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.sqlite3"
            store = MetricsStore(path)
            run_id = store.begin_run("hello", self._simple_plan())
            store.record_attempt(
                run_id,
                0,
                ModelAttempt(
                    role="e2b",
                    model="fixture",
                    content="ok",
                    thinking="",
                    metrics={
                        "provider": "ollama-via-mncs-fabric",
                        "fabric_enabled": True,
                        "fabric_worker": "gpu-fixture",
                        "placement_mode": "full-accelerator",
                        "fabric_receipt_identity": "sha256:" + "a" * 64,
                    },
                    tool_executions=[],
                    verification=VerificationResult(True, (), ()),
                    session_targets=SessionTargets.remote_inference("gpu-fixture"),
                ),
                None,
            )
            row = store.recent(1)[0]
            self.assertEqual(row["fabric_worker"], "gpu-fixture")
            self.assertEqual(row["placement_mode"], "full-accelerator")
            self.assertEqual(row["inference_target"], "fabric-worker:gpu-fixture")
            self.assertEqual(row["workspace_target"], "controller")
            self.assertEqual(row["tool_execution_target"], "controller")

    def test_begin_run_records_semantic_router_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.sqlite3"
            store = MetricsStore(path)
            semantic = SemanticRouteResult(
                selected_lane="coding",
                selected_score=0.8,
                runner_up_lane="review",
                runner_up_score=0.1,
                margin=0.7,
                all_scores={"coding": 0.8, "review": 0.1},
                backend="transformers",
                reason="semantic lane selected",
                revision="a" * 40,
                latency_ms=11.5,
            )
            plan = RoutePlan(
                primary_role="coder",
                escalation_roles=("reviewer",),
                reasons=("semantic lane selected",),
                profile=TaskProfile(
                    text="fix it",
                    word_count=2,
                    has_code=True,
                    asks_for_edit=True,
                    asks_for_execution=False,
                    asks_for_explanation=False,
                    is_high_risk=False,
                    is_complex=False,
                    has_image=False,
                    file_reference_count=0,
                ),
                lane="coding",
                semantic=semantic,
            )
            run_id = store.begin_run("fix it", plan)
            with sqlite3.connect(path) as connection:
                row = connection.execute(
                    """
                    SELECT semantic_backend, semantic_revision, semantic_lane,
                           semantic_score, semantic_margin, semantic_latency_ms
                    FROM runs WHERE id = ?
                    """,
                    (run_id,),
                ).fetchone()
            self.assertEqual(row[0], "transformers")
            self.assertEqual(row[1], "a" * 40)
            self.assertEqual(row[2], "coding")
            self.assertAlmostEqual(row[5], 11.5)


if __name__ == "__main__":
    unittest.main()
