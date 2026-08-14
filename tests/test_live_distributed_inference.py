"""Live distributed inference E2E against the persistent Fabric fleet.

This is not a mock. It requires the persistent controller socket and the two
currently enrolled workers. Each placement is an exact pin and expects an
unmistakable marker back from worker-local Ollama.
"""

from __future__ import annotations

import json
import os
import time
import unittest
from dataclasses import replace
from pathlib import Path

from epi13_local_harness.commons import CommonsSession
from epi13_local_harness.config import load_config
from epi13_local_harness.fabric_inventory_session import InventoryAwareFabricSession

PLACEMENTS = (
    ("fabric-worker-01", "granite3.3:2b", "MNCS_E2E_LINUX_OK"),
    ("collamore02-windows", "gemma4:e4b", "MNCS_E2E_WINDOWS_OK"),
)
CONTROLLER_SOCKET = Path("~/.local/state/mncs-fabric/controller.sock").expanduser()


def _live_configured() -> bool:
    if os.environ.get("MNCS_SKIP_LIVE_INFERENCE") == "1":
        return False
    return CONTROLLER_SOCKET.exists()


@unittest.skipUnless(_live_configured(), "persistent Fabric controller socket is not present")
class LiveDistributedInferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.session = InventoryAwareFabricSession(
            cls.config.fabric, residency_config=cls.config.model_residency
        )
        cls.session.initialize(refresh_inventory=False)
        status = cls.session.status()
        if status.state != "available":
            raise unittest.SkipTest(f"Fabric is not available: {status.detail}")
        cls.workers = {
            str(worker.get("worker_id")): worker
            for worker in status.workers
            if worker.get("worker_id")
        }
        cls.commons = CommonsSession(cls.config.commons)
        cls.commons.initialize()

    @classmethod
    def tearDownClass(cls) -> None:
        closer = getattr(cls.session, "close", None)
        if callable(closer):
            closer()

    def _installed_models(self, worker: dict) -> set[str]:
        observation = worker.get("capability_observation") or {}
        names: set[str] = set()
        for item in observation.get("capabilities") or []:
            if isinstance(item, dict) and item.get("kind") == "model" and item.get("name"):
                names.add(str(item["name"]))
        for item in worker.get("model_inventory") or []:
            if isinstance(item, dict):
                name = item.get("name") or item.get("model")
                if name:
                    names.add(str(name))
        return names

    def test_worker_and_model_discovery_covers_required_placements(self) -> None:
        missing = []
        for worker_id, model, _marker in PLACEMENTS:
            worker = self.workers.get(worker_id)
            if worker is None or worker.get("availability") != "AVAILABLE":
                missing.append(f"{worker_id} unavailable")
                continue
            if model not in self._installed_models(worker):
                missing.append(f"{worker_id} missing {model}")
        if missing:
            raise unittest.SkipTest("; ".join(missing))

    def test_exact_linux_and_windows_inference_returns_markers(self) -> None:
        failures: list[str] = []
        evidence: list[dict[str, object]] = []
        template = self.config.models["e2b"]
        for worker_id, model_name, marker in PLACEMENTS:
            worker = self.workers.get(worker_id)
            stage = "worker-discovery"
            if worker is None or worker.get("availability") != "AVAILABLE":
                failures.append(f"{worker_id}: {stage} failed")
                continue
            stage = "model-discovery"
            if model_name not in self._installed_models(worker):
                failures.append(f"{worker_id}: {stage} failed for {model_name}")
                continue
            model = replace(
                template,
                name=model_name,
                think=False,
                num_ctx=2048,
                keep_alive="5m",
                temperature=0.0,
            )
            prompt = f"Reply with exactly: {marker}"
            stage = "fabric-dispatch"
            started = time.perf_counter()
            try:
                response, metadata = self.session.chat(
                    model,
                    [{"role": "user", "content": prompt}],
                    worker_id=worker_id,
                )
            except Exception as exc:
                failures.append(
                    f"{worker_id}/{model_name}: {stage} {type(exc).__name__}: {exc}"
                )
                continue
            elapsed = time.perf_counter() - started
            content = ((response.get("message") or {}).get("content") or "")
            last_stage = metadata.get("inference_stage") or "unknown"
            if marker not in content:
                failures.append(
                    f"{worker_id}/{model_name}: marker missing after {last_stage} "
                    f"in {elapsed:.1f}s: {repr(content)[:200]}"
                )
                continue
            record = getattr(self.session, "last_execution_record", None)
            published = None
            if isinstance(record, dict) and self.commons.ready:
                try:
                    published = self.commons.publish_fabric_evidence(record)
                except Exception as exc:
                    failures.append(
                        f"{worker_id}/{model_name}: Commons publication failed: {exc}"
                    )
                    continue
            evidence.append(
                {
                    "worker": worker_id,
                    "model": model_name,
                    "marker": marker,
                    "stage": last_stage,
                    "elapsed_seconds": round(elapsed, 3),
                    "fabric_record_identity": metadata.get("fabric_record_identity"),
                    "commons": published.get("receipt") if isinstance(published, dict) else published,
                }
            )
        if failures:
            self.fail("distributed inference E2E failed: " + " | ".join(failures))
        self.assertEqual(len(evidence), len(PLACEMENTS), json.dumps(evidence, default=str))
        for item in evidence:
            self.assertEqual(item["stage"], "completed")
            self.assertTrue(item["fabric_record_identity"])
