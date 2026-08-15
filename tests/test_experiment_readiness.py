from __future__ import annotations

import unittest

from epi13_local_harness.experiment_readiness import (
    BLOCKED,
    DEGRADED,
    READY,
    evaluate_layers,
)
from epi13_local_harness.experiment_stack import CLAIM_BOUNDARY, digest_record
from epi13_local_harness.fabric_compat import (
    EXPERIMENT_CERTIFIED_FABRIC_VERSION,
    EXPERIMENT_REQUIRED_CAPABILITIES,
)


def _caps() -> dict[str, bool]:
    return {name: True for name in EXPERIMENT_REQUIRED_CAPABILITIES}


class ExperimentReadinessTests(unittest.TestCase):
    def test_ready_base_inference_with_available_workers(self) -> None:
        result = evaluate_layers(
            control={"available": True, "status": READY},
            harness={"available": True, "status": READY},
            fabric={
                "available": True,
                "version": EXPERIMENT_CERTIFIED_FABRIC_VERSION,
                "controller_connected": True,
                "persistent_service_support": _caps(),
                "workers": [
                    {
                        "worker_id": "fabric-worker-01",
                        "availability": "AVAILABLE",
                        "capability_inventory_status": "FRESH",
                        "worker_service_version": EXPERIMENT_CERTIFIED_FABRIC_VERSION,
                        "model_inventory": [{"name": "granite3.3:2b"}],
                    }
                ],
            },
            commons={"available": True, "consumerReadCapable": True, "operatorPublicationCapable": True},
            routing={"available": True, "local_fallback": False},
            artifact_write={"writable": True},
        )
        self.assertEqual(result["status"], READY)
        self.assertEqual(result["claim_boundary"], CLAIM_BOUNDARY)
        self.assertTrue(result["experiment_stack"]["experiment_stack_identity"].startswith("sha256:"))

    def test_stale_inventory_is_degraded_not_unavailable(self) -> None:
        result = evaluate_layers(
            control={"available": True, "status": READY},
            harness={"available": True, "status": READY},
            fabric={
                "available": True,
                "version": EXPERIMENT_CERTIFIED_FABRIC_VERSION,
                "controller_connected": True,
                "persistent_service_support": _caps(),
                "stale_capability_inventory": True,
                "available_workers": [{"worker_id": "fabric-worker-01"}],
                "workers": [
                    {
                        "worker_id": "fabric-worker-01",
                        "availability": "AVAILABLE",
                        "capability_inventory_status": "STALE",
                        "model_inventory": [{"name": "granite3.3:2b"}],
                    }
                ],
            },
            commons={"available": True, "consumerReadCapable": True},
            routing={"available": True, "fallback_explicit": True},
            artifact_write={"writable": True},
        )
        self.assertEqual(result["layers"]["workers"]["status"], DEGRADED)
        self.assertNotEqual(result["layers"]["workers"]["status"], BLOCKED)
        self.assertIn("STALE capability inventory is not worker UNAVAILABLE", str(result["layers"]["fleet"]["detail"]))
        self.assertEqual(result["status"], DEGRADED)

    def test_unavailable_worker_blocks_without_local_fallback(self) -> None:
        result = evaluate_layers(
            control={"available": True, "status": READY},
            harness={"available": True, "status": READY},
            fabric={
                "available": True,
                "version": EXPERIMENT_CERTIFIED_FABRIC_VERSION,
                "controller_connected": True,
                "persistent_service_support": _caps(),
                "workers": [
                    {
                        "worker_id": "fabric-worker-01",
                        "availability": "UNAVAILABLE",
                    }
                ],
            },
            commons={"available": True, "consumerReadCapable": True},
            routing={"local_fallback": True, "fallback_explicit": False},
            artifact_write={"writable": True},
        )
        self.assertEqual(result["layers"]["fleet"]["status"], BLOCKED)
        self.assertEqual(result["layers"]["routing"]["status"], BLOCKED)
        self.assertEqual(result["status"], BLOCKED)

    def test_missing_fabric_capabilities_block_certified_version(self) -> None:
        result = evaluate_layers(
            control={"available": True, "status": READY},
            harness={"available": True, "status": READY},
            fabric={
                "available": True,
                "version": EXPERIMENT_CERTIFIED_FABRIC_VERSION,
                "controller_connected": True,
                "persistent_service_support": {},
                "workers": [{"worker_id": "w", "availability": "AVAILABLE"}],
            },
            commons={"available": True, "consumerReadCapable": True},
            artifact_write={"writable": True},
        )
        self.assertEqual(result["layers"]["fabric_controller"]["status"], BLOCKED)
        self.assertEqual(result["status"], BLOCKED)

    def test_stack_digest_is_stable_for_same_identity_body(self) -> None:
        first = {"schema": "mncs.experiment-stack.v1", "mncs_fabric_commit": "abc"}
        second = {"mncs_fabric_commit": "abc", "schema": "mncs.experiment-stack.v1"}
        self.assertEqual(digest_record(first), digest_record(second))


if __name__ == "__main__":
    unittest.main()
