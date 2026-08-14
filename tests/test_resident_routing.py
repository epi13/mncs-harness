from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from epi13_local_harness.agent import LocalAgent
from epi13_local_harness.config import load_config
from epi13_local_harness.fabric import FabricStatus
from epi13_local_harness.fabric_inventory_session import InventoryAwareFabricSession
from epi13_local_harness.models import (
    ModelAttempt,
    ModelResidencyConfig,
    ResidentWorkerConfig,
    RoutingOverride,
    SessionTargets,
    VerificationResult,
)
from epi13_local_harness.residency import ResidencyManager


def _model(name: str, size: int, *, loaded: bool = False) -> dict[str, object]:
    return {"name": name, "size": size, "loaded": loaded}


def _worker(
    worker_id: str,
    models: list[dict[str, object]],
    *,
    memory: int = 32_000_000_000,
    availability: str = "AVAILABLE",
    freshness: str = "CURRENT",
) -> dict[str, object]:
    return {
        "worker_id": worker_id,
        "source": "remote",
        "availability": availability,
        "model_inventory_status": freshness,
        "model_inventory": models,
        "resource_snapshot": {"host_memory_total_bytes": memory},
    }


class _ResidencySession:
    def __init__(self, status: FabricStatus) -> None:
        self._status = status
        self.warmed: list[tuple[str, str]] = []

    def status(self) -> FabricStatus:
        return self._status

    def warm_model(
        self, worker_id: str, model: str, *, keep_alive: object, timeout_seconds: float
    ) -> dict[str, object]:
        del keep_alive, timeout_seconds
        self.warmed.append((worker_id, model))
        return {"worker_id": worker_id, "model": model, "loaded": True}


class ResidentRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(None)
        self.residency = ModelResidencyConfig(
            enabled=True,
            prefer_resident_for_auto_routing=True,
            workers=(
                ResidentWorkerConfig("gpu", "gemma4:e4b"),
                ResidentWorkerConfig("cpu", "qwen3:8b"),
                ResidentWorkerConfig("arm", "gemma4:e2b"),
            ),
        )
        self.config = replace(self.config, model_residency=self.residency)
        self.workers = (
            _worker("gpu", [_model("gemma4:e4b", 9_000_000_000, loaded=True)]),
            _worker("cpu", [_model("qwen3:8b", 5_000_000_000)]),
            _worker(
                "arm",
                [_model("gemma4:e2b", 1_500_000_000, loaded=True)],
                memory=4_000_000_000,
            ),
        )
        self.status = FabricStatus(
            True, "available", "controller", workers=self.workers
        )

    def test_heterogeneous_policy_selects_one_model_and_preserves_warm_state(self) -> None:
        session = _ResidencySession(self.status)
        manager = ResidencyManager(self.config, session)
        plan = manager.plan()
        self.assertEqual(len(plan), 3)
        self.assertEqual(
            {item["worker_id"]: item["resident_model"] for item in plan},
            {"gpu": "gemma4:e4b", "cpu": "qwen3:8b", "arm": "gemma4:e2b"},
        )
        results = manager.reconcile()
        self.assertEqual(session.warmed, [("cpu", "qwen3:8b")])
        self.assertEqual(
            {item["worker_id"]: item["loaded"] for item in results},
            {"gpu": True, "cpu": True, "arm": True},
        )

    def _inventory_session(self) -> InventoryAwareFabricSession:
        session = object.__new__(InventoryAwareFabricSession)
        session.residency_config = self.residency
        session.capability_api_available = False
        session.model_inventory_errors = {}
        session.model_inventories = {
            str(worker["worker_id"]): tuple(worker["model_inventory"])
            for worker in self.workers
        }
        session.config = replace(self.config.fabric, enabled=True)
        session.status = lambda: self.status
        return session

    def test_exact_pin_uses_persistent_capability_inventory(self) -> None:
        worker = {
            "worker_id": "fabric-worker-01",
            "source": "remote",
            "availability": "AVAILABLE",
            "capability_inventory_status": "CURRENT",
            "capability_observation": {
                "availability": "AVAILABLE",
                "capabilities": [
                    {
                        "kind": "model",
                        "name": "granite3.3:2b",
                        "namespace": "ollama",
                        "attributes": {"size_bytes": 1_500_000_000, "loaded": True},
                    }
                ],
            },
        }
        session = object.__new__(InventoryAwareFabricSession)
        session.residency_config = self.residency
        session.capability_api_available = True
        session.model_inventory_errors = {}
        session.model_inventories = {}
        session.config = replace(self.config.fabric, enabled=True)
        session.status = lambda: FabricStatus(True, "available", "controller", workers=(worker,))
        _effective, selection = session.resolve_model(
            "e2b",
            self.config.models["e2b"],
            RoutingOverride.from_values(worker="fabric-worker-01", model="granite3.3:2b"),
        )
        self.assertTrue(selection.available)
        self.assertEqual(selection.worker_id, "fabric-worker-01")
        self.assertEqual(selection.selected_model, "granite3.3:2b")

    def test_exact_pin_accepts_stale_persistent_capability_inventory(self) -> None:
        worker = {
            "worker_id": "fabric-worker-01",
            "source": "remote",
            "availability": "AVAILABLE",
            "capability_inventory_status": "STALE",
            "capability_observation": {
                "availability": "AVAILABLE",
                "capabilities": [
                    {
                        "kind": "model",
                        "name": "granite3.3:2b",
                        "namespace": "ollama",
                        "attributes": {"size_bytes": 1_500_000_000, "loaded": True},
                    }
                ],
            },
        }
        session = object.__new__(InventoryAwareFabricSession)
        session.residency_config = self.residency
        session.capability_api_available = True
        session.model_inventory_errors = {}
        session.model_inventories = {}
        session.config = replace(self.config.fabric, enabled=True)
        session.status = lambda: FabricStatus(True, "available", "controller", workers=(worker,))
        _effective, selection = session.resolve_model(
            "e2b",
            self.config.models["e2b"],
            RoutingOverride.from_values(worker="fabric-worker-01", model="granite3.3:2b"),
        )
        self.assertTrue(selection.available)
        self.assertEqual(selection.selected_model, "granite3.3:2b")

    def test_manual_worker_model_pair_is_exact_and_fail_closed(self) -> None:
        session = self._inventory_session()
        model = self.config.models["e4b"]
        effective, selection = session.resolve_model(
            "e4b",
            model,
            RoutingOverride.from_values(worker="gpu", model="gemma4:e4b"),
        )
        self.assertEqual(effective.name, "gemma4:e4b")
        self.assertEqual(selection.worker_id, "gpu")
        self.assertTrue(selection.loaded)

        _effective, missing = session.resolve_model(
            "e4b",
            model,
            RoutingOverride.from_values(worker="arm", model="gemma4:e4b"),
        )
        self.assertFalse(missing.available)
        self.assertEqual(missing.worker_id, "arm")
        self.assertIn("PINNED_MODEL_MISSING", missing.reason)

        _effective, fallback = session.resolve_model(
            "e4b",
            model,
            RoutingOverride.from_values(
                worker="arm", model="gemma4:e4b", allow_fallback=True
            ),
        )
        self.assertTrue(fallback.available)
        self.assertEqual(fallback.worker_id, "gpu")
        self.assertIn("explicit manual fallback enabled", fallback.reason)

    def test_selected_model_capabilities_disable_unsupported_thinking(self) -> None:
        self.workers = (
            _worker(
                "granite-worker",
                [
                    {
                        "name": "granite3.3:2b",
                        "size": 1_500_000_000,
                        "capabilities": ["completion", "tools"],
                    }
                ],
            ),
        )
        self.status = FabricStatus(True, "available", "controller", workers=self.workers)
        session = self._inventory_session()
        effective, selection = session.resolve_model(
            "e4b",
            self.config.models["e4b"],
            RoutingOverride.from_values(worker="granite-worker", model="granite3.3:2b"),
        )
        self.assertFalse(effective.think)
        self.assertIn("does not advertise thinking", selection.reason)

    def test_pinned_worker_unavailable_or_stale_is_not_replaced(self) -> None:
        self.workers = (
            _worker(
                "gpu",
                [_model("gemma4:e4b", 9_000_000_000)],
                availability="UNAVAILABLE",
            ),
            _worker("cpu", [_model("gemma4:e4b", 9_000_000_000)]),
            _worker(
                "arm",
                [_model("gemma4:e2b", 1_500_000_000)],
                freshness="STALE",
            ),
        )
        self.status = FabricStatus(True, "available", "controller", workers=self.workers)
        session = self._inventory_session()
        _effective, unavailable = session.resolve_model(
            "e4b", self.config.models["e4b"], RoutingOverride.from_values(worker="gpu")
        )
        self.assertFalse(unavailable.available)
        self.assertIn("PINNED_WORKER_UNAVAILABLE", unavailable.reason)
        _effective, stale = session.resolve_model(
            "e4b", self.config.models["e4b"], RoutingOverride.from_values(worker="arm")
        )
        self.assertTrue(stale.available)
        self.assertEqual(stale.worker_id, "arm")
        self.assertEqual(stale.selected_model, "gemma4:e2b")

    def test_model_and_worker_modes_use_per_worker_inventory(self) -> None:
        session = self._inventory_session()
        configured = self.config.models["e4b"]
        _effective, by_model = session.resolve_model(
            "e4b", configured, RoutingOverride.from_values(model="gemma4:e2b")
        )
        self.assertEqual(by_model.worker_id, "arm")
        _effective, by_worker = session.resolve_model(
            "e4b", configured, RoutingOverride.from_values(worker="cpu")
        )
        self.assertEqual(by_worker.worker_id, "cpu")
        self.assertEqual(by_worker.selected_model, "qwen3:8b")

    def test_auto_prefers_loaded_eligible_instance_and_never_localizes(self) -> None:
        duplicate = _worker("cpu", [_model("gemma4:e4b", 9_000_000_000)])
        self.workers = (self.workers[0], duplicate, self.workers[2])
        self.status = FabricStatus(True, "available", "controller", workers=self.workers)
        session = self._inventory_session()
        _effective, selection = session.resolve_model("e4b", self.config.models["e4b"])
        self.assertEqual(selection.worker_id, "gpu")
        self.assertTrue(selection.loaded)

        unavailable = tuple(
            {**worker, "availability": "UNAVAILABLE"} for worker in self.workers
        )
        self.status = FabricStatus(True, "available", "controller", workers=unavailable)
        _effective, denied = session.resolve_model("e4b", self.config.models["e4b"])
        self.assertFalse(denied.available)
        self.assertIsNone(denied.worker_id)
        self.assertEqual(denied.inventory_status, "WORKER_UNAVAILABLE")

    def test_remote_attempt_restores_evicted_resident_model(self) -> None:
        agent = object.__new__(LocalAgent)
        agent.config = self.config
        agent._last_residency_results = ()
        agent._lifecycle_stages = []
        refreshes: list[str] = []
        reconciled: list[str | None] = []

        class Session:
            def refresh_model_inventory(self) -> None:
                refreshes.append("refresh")

            def status(self) -> None:
                return None

        class Residency:
            def reconcile(self, *, force_worker: str | None = None) -> tuple[dict[str, object], ...]:
                reconciled.append(force_worker)
                return (
                    {
                        "worker_id": "gpu",
                        "resident_model": "gemma4:e4b",
                        "outcome": "PASS",
                        "code": "RESIDENCY_WARMED",
                        "loaded": True,
                    },
                )

        agent.fabric_session = Session()
        agent.fleet = SimpleNamespace(residency=Residency())
        attempt = ModelAttempt(
            role="coder",
            model="qwen3:8b",
            content="done",
            thinking="",
            metrics={},
            tool_executions=[],
            verification=VerificationResult(True, (), ()),
            session_targets=SessionTargets.remote_inference("gpu"),
        )

        agent._restore_residency_after_attempt(attempt)

        self.assertEqual(refreshes, [])
        self.assertEqual(reconciled, ["gpu"])
        self.assertEqual(
            attempt.metrics["residency_reconciliation"][0]["code"],
            "RESIDENCY_WARMED",
        )
        self.assertTrue(attempt.metrics["residency_reconciliation"][0]["loaded"])

    def test_restore_skips_when_exact_route_used_declared_resident(self) -> None:
        agent = object.__new__(LocalAgent)
        agent.config = self.config
        agent._last_residency_results = ()
        agent._lifecycle_stages = []
        calls: list[str] = []

        class Residency:
            def reconcile(self, *, force_worker: str | None = None) -> tuple[dict[str, object], ...]:
                calls.append(force_worker or "*")
                return ()

        agent.fleet = SimpleNamespace(residency=Residency())
        attempt = ModelAttempt(
            role="e4b",
            model="gemma4:e4b",
            content="done",
            thinking="",
            metrics={},
            tool_executions=[],
            verification=VerificationResult(True, (), ()),
            session_targets=SessionTargets.remote_inference("gpu"),
        )
        agent._restore_residency_after_attempt(attempt)
        self.assertEqual(calls, [])
        self.assertEqual(
            attempt.metrics["residency_reconciliation"][0]["code"],
            "RESIDENCY_UNCHANGED",
        )

    def test_restore_reports_not_configured_when_no_declared_resident(self) -> None:
        agent = object.__new__(LocalAgent)
        agent.config = replace(self.config, model_residency=ModelResidencyConfig(enabled=True))
        agent._last_residency_results = ()
        agent._lifecycle_stages = []
        agent.fleet = SimpleNamespace(residency=SimpleNamespace(reconcile=lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not reconcile"))))
        attempt = ModelAttempt(
            role="e4b",
            model="gemma4:e4b",
            content="done",
            thinking="",
            metrics={},
            tool_executions=[],
            verification=VerificationResult(True, (), ()),
            session_targets=SessionTargets.remote_inference("gpu"),
        )
        agent._restore_residency_after_attempt(attempt)
        self.assertEqual(
            attempt.metrics["residency_reconciliation"][0]["code"],
            "RESIDENCY_NOT_CONFIGURED",
        )
        self.assertIsNone(attempt.metrics["residency_reconciliation"][0]["loaded"])

    def test_exact_pin_run_does_not_refresh_or_warm_fleet(self) -> None:
        agent = object.__new__(LocalAgent)
        agent.config = replace(self.config, fabric=replace(self.config.fabric, enabled=True))
        agent._lifecycle_stages = []
        calls: list[str] = []

        def refresh(**kwargs: object) -> None:
            calls.append(f"refresh:{kwargs}")

        def restore(attempt: ModelAttempt) -> None:
            del attempt
            calls.append("restore")

        agent.refresh_fabric_inventory = refresh  # type: ignore[method-assign]
        agent._restore_residency_after_attempt = restore  # type: ignore[method-assign]
        agent.metrics = SimpleNamespace(
            begin_run=lambda *args, **kwargs: "run",
            record_attempt=lambda *args, **kwargs: None,
        )
        attempt = ModelAttempt(
            role="e2b",
            model="granite3.3:2b",
            content="MNCS_PIN_OK",
            thinking="",
            metrics={},
            tool_executions=[],
            verification=VerificationResult(True, (), ()),
            session_targets=SessionTargets.remote_inference("fabric-worker-01"),
        )
        agent._run_attempt = lambda *args, **kwargs: attempt  # type: ignore[method-assign]
        result = LocalAgent.run(
            agent,
            "Reply with exactly: MNCS_PIN_OK",
            workspace=Path("."),
            routing_override=RoutingOverride.from_values(
                worker="fabric-worker-01", model="granite3.3:2b"
            ),
        )
        self.assertEqual(result.final_content, "MNCS_PIN_OK")
        self.assertFalse(any(item.startswith("refresh:") for item in calls))
        self.assertEqual(calls, ["restore"])


if __name__ == "__main__":
    unittest.main()
