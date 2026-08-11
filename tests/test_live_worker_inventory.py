from __future__ import annotations

import unittest
from dataclasses import replace

from epi13_local_harness.config import load_config
from epi13_local_harness.fabric_inventory_session import (
    InventoryAwareFabricSession,
    _execution_succeeded,
    _fresh_request_id,
    _FreshDispatchClient,
    _inventory_script,
    _model_capability_entries,
    _model_from_capability,
)


class _RecordingClient:
    def __init__(self) -> None:
        self.request_ids: list[str | None] = []

    def execute(self, plan=None, manifest=None, *, request_id=None, **kwargs):
        self.request_ids.append(request_id)
        return []


class _WorkerListClient:
    def workers(self):
        return [
            {
                "worker_id": "collamore02-windows",
                "source": "remote",
                "availability": "AVAILABLE",
            }
        ]


class LiveWorkerInventoryTests(unittest.TestCase):
    def test_inventory_probe_collects_optional_ollama_capabilities(self) -> None:
        script = _inventory_script()
        self.assertIn("/api/show", script)
        self.assertIn('"capabilities"', script)

    def test_ollama_capabilities_survive_fabric_observation_round_trip(self) -> None:
        entries = _model_capability_entries(
            ({"name": "granite3.3:2b", "capabilities": ["completion", "tools"]},)
        )
        model = _model_from_capability(entries[1])
        self.assertEqual(model["capabilities"], ["completion", "tools"])

    def test_fresh_request_ids_are_bounded_and_unique(self) -> None:
        first = _fresh_request_id("elh-inventory:collamore02-windows")
        second = _fresh_request_id("elh-inventory:collamore02-windows")
        self.assertNotEqual(first, second)
        self.assertLessEqual(len(first), 256)
        self.assertTrue(first.startswith("elh-inventory:collamore02-windows:"))

    def test_dispatch_wrapper_does_not_replay_identical_semantic_work(self) -> None:
        client = _RecordingClient()
        wrapped = _FreshDispatchClient(client)
        wrapped.execute({}, {})
        wrapped.execute({}, {})
        self.assertEqual(len(client.request_ids), 2)
        self.assertIsNotNone(client.request_ids[0])
        self.assertIsNotNone(client.request_ids[1])
        self.assertNotEqual(client.request_ids[0], client.request_ids[1])

    def test_dispatch_wrapper_preserves_explicit_request_id(self) -> None:
        client = _RecordingClient()
        wrapped = _FreshDispatchClient(client)
        wrapped.execute({}, {}, request_id="operator-request")
        self.assertEqual(client.request_ids, ["operator-request"])

    def test_successful_idempotent_replay_is_not_mislabeled_as_failure(self) -> None:
        self.assertTrue(
            _execution_succeeded(
                {"disposition": "DUPLICATE_IDEMPOTENT"},
                {"outcome": "PASS", "termination_reason": "COMPLETED"},
            )
        )

    def test_failed_fresh_scan_drops_stale_inventory(self) -> None:
        base = load_config(None).fabric
        session = InventoryAwareFabricSession(replace(base, enabled=True))
        session.client = _WorkerListClient()
        session._state = "available"
        calls = 0

        def probe(worker_id: str):
            nonlocal calls
            calls += 1
            if calls == 1:
                return (
                    {"name": "gemma4:e4b", "size": 1},
                    {"name": "extra-model:latest", "size": 2},
                )
            raise RuntimeError("fresh inventory unavailable")

        session._probe_model_inventory = probe  # type: ignore[method-assign]
        first = session.refresh_model_inventory()
        self.assertEqual(first.workers[0]["model_count"], 2)
        self.assertEqual(
            first.workers[0]["model_names"],
            ["extra-model:latest", "gemma4:e4b"],
        )

        second = session.refresh_model_inventory()
        self.assertNotIn("model_names", second.workers[0])
        self.assertIn("fresh inventory unavailable", second.workers[0]["model_inventory_error"])


if __name__ == "__main__":
    unittest.main()
