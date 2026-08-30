from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from epi13_local_harness.config import load_config
from epi13_local_harness.experiment_readiness import (
    BLOCKED,
    DEGRADED,
    READY,
    evaluate_layers,
    evaluate_model_experiment_eligibility,
    evaluate_worker_experiment_eligibility,
    inspect_live_config,
    probe_artifact_write,
)
from epi13_local_harness.experiment_stack import (
    CLAIM_BOUNDARY,
    PROVENANCE_INCOMPLETE,
    digest_record,
)
from epi13_local_harness.fabric_compat import (
    EXPERIMENT_CERTIFIED_FABRIC_COMMIT,
    EXPERIMENT_CERTIFIED_FABRIC_VERSION,
    EXPERIMENT_REQUIRED_CAPABILITIES,
    evaluate_experiment_fabric,
)


def _caps() -> dict[str, bool]:
    return {name: True for name in EXPERIMENT_REQUIRED_CAPABILITIES}


def _ready_worker(**overrides: object) -> dict[str, object]:
    worker: dict[str, object] = {
        "worker_id": "fabric-worker-01",
        "availability": "AVAILABLE",
        "management_state": "READY",
        "certification_status": "CERTIFIED",
        "certification": {
            "disposition": "CERTIFIED",
            "inventory_identity": "sha256:inventory-1",
        },
        "inventory_identity": "sha256:inventory-1",
        "inventory": {"inventory_identity": "sha256:inventory-1"},
        "desired_state_identity": "sha256:desired-1",
        "conformance": {
            "disposition": "CONFORMANT",
            "blocking_failures": [],
            "desired_state_identity": "sha256:desired-1",
            "inventory_identity": "sha256:inventory-1",
        },
        "capability_inventory_status": "CURRENT",
        "worker_service_version": EXPERIMENT_CERTIFIED_FABRIC_VERSION,
        "schedulable": True,
        "model_inventory": [
            {
                "name": "granite3.3:2b",
                "namespace": "ollama",
                "subject_identity": "07bd1f170855240f9e162bf54ea494a8bc1c73d8cbd1365d7fccbeb7d2504947",
            }
        ],
    }
    worker.update(overrides)
    return worker


def _runtime_identities() -> dict[str, dict[str, str]]:
    return {
        "control": {"package": "mncs-control-mcp", "version": "0.4.7", "source_commit": "c" * 40},
        "harness": {"package": "mncs-harness", "version": "0.6.9", "source_commit": "h" * 40},
        "fabric_controller": {
            "package": "mncs-fabric",
            "version": EXPERIMENT_CERTIFIED_FABRIC_VERSION,
            "source_commit": EXPERIMENT_CERTIFIED_FABRIC_COMMIT,
        },
        "commons": {"package": "mncs-commons", "version": "0.5.0.dev1", "source_commit": "m" * 40},
        "reference_studies": {
            "package": "mncs-reference-studies",
            "version": "0",
            "source_commit": "s" * 40,
        },
    }


def _ready_layers(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "control": {"available": True, "status": READY},
        "harness": {"available": True, "status": READY},
        "fabric": {
            "available": True,
            "version": EXPERIMENT_CERTIFIED_FABRIC_VERSION,
            "commit": EXPERIMENT_CERTIFIED_FABRIC_COMMIT,
            "controller_connected": True,
            "persistent_service_support": _caps(),
            "workers": [_ready_worker()],
        },
        "commons": {"available": True, "consumerReadCapable": True, "operatorPublicationCapable": True},
        "routing": {"available": True, "local_fallback": False},
        "artifact_write": {"writable": True, "path": "/tmp/metrics"},
        "runtime_identities": _runtime_identities(),
        "forge": {"available": False},
    }
    payload.update(overrides)
    return evaluate_layers(**payload)  # type: ignore[arg-type]


class WorkerEligibilityTests(unittest.TestCase):
    def test_available_degraded_management_is_not_ready(self) -> None:
        result = evaluate_worker_experiment_eligibility(
            _ready_worker(management_state="DEGRADED")
        )
        self.assertNotEqual(result["status"], READY)
        self.assertFalse(result["experiment_eligible"])
        self.assertIn("management_state:DEGRADED", result["blockers"])

    def test_available_uncertified_is_not_ready(self) -> None:
        result = evaluate_worker_experiment_eligibility(
            _ready_worker(certification_status="UNKNOWN", certification={"disposition": "UNKNOWN"})
        )
        self.assertFalse(result["experiment_eligible"])
        self.assertIn("certification:UNKNOWN", result["blockers"])

    def test_available_blocking_conformance_is_not_ready(self) -> None:
        result = evaluate_worker_experiment_eligibility(
            _ready_worker(conformance={"disposition": "NONCONFORMANT", "blocking_failures": ["tool:git"]})
        )
        self.assertFalse(result["experiment_eligible"])
        self.assertIn("conformance_blocking", result["blockers"])

    def test_available_unresolved_update_is_not_ready(self) -> None:
        result = evaluate_worker_experiment_eligibility(
            _ready_worker(update_transaction={"state": "UPDATE_APPLYING"})
        )
        self.assertFalse(result["experiment_eligible"])
        self.assertIn("unresolved_update:UPDATE_APPLYING", result["blockers"])

    def test_available_stale_inventory_is_not_strict_epoch_ready(self) -> None:
        result = evaluate_worker_experiment_eligibility(
            _ready_worker(capability_inventory_status="STALE")
        )
        self.assertTrue(result["operational_available"])
        self.assertTrue(result["fabric_eligible"])
        self.assertFalse(result["strict_epoch_eligible"])
        self.assertEqual(result["status"], DEGRADED)

    def test_full_invariant_is_eligible(self) -> None:
        result = evaluate_worker_experiment_eligibility(_ready_worker())
        self.assertEqual(result["status"], READY)
        self.assertTrue(result["experiment_eligible"])

    def test_inspect_observation_does_not_invalidate_fabric_ready(self) -> None:
        result = evaluate_worker_experiment_eligibility(
            _ready_worker(
                inventory={"inventory_identity": "sha256:fresh-inspect"},
                inventory_is_inspect_observation=True,
            )
        )
        self.assertTrue(result["fabric_eligible"])
        self.assertTrue(result["experiment_eligible"])


class ModelEligibilityTests(unittest.TestCase):
    def test_available_worker_with_zero_models_is_not_model_ready(self) -> None:
        result = _ready_layers(fabric={
            "available": True,
            "version": EXPERIMENT_CERTIFIED_FABRIC_VERSION,
            "commit": EXPERIMENT_CERTIFIED_FABRIC_COMMIT,
            "controller_connected": True,
            "persistent_service_support": _caps(),
            "workers": [_ready_worker(model_inventory=[])],
        })
        self.assertEqual(result["layers"]["models"]["status"], BLOCKED)

    def test_model_on_noneligible_worker_is_not_ready(self) -> None:
        worker = _ready_worker(management_state="DEGRADED")
        result = evaluate_model_experiment_eligibility(
            {"name": "granite3.3:2b", "subject_identity": "abc"},
            worker,
        )
        self.assertFalse(result["eligible"])
        self.assertIn("worker_not_experiment_eligible", result["blockers"])

    def test_provider_claim_with_observed_fail_is_not_ready(self) -> None:
        result = evaluate_model_experiment_eligibility(
            {"name": "granite3.3:2b", "subject_identity": "abc", "observed": "FAIL"},
            _ready_worker(),
        )
        self.assertFalse(result["eligible"])
        self.assertIn("observed_fail", result["blockers"])

    def test_eligible_model_on_eligible_worker_is_ready(self) -> None:
        result = _ready_layers()
        self.assertEqual(result["layers"]["models"]["status"], READY)
        self.assertEqual(result["status"], READY)


class ArtifactWriteTests(unittest.TestCase):
    def test_directory_exists_but_unwritable_is_not_ready(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root can write read-only directories")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            path.chmod(stat.S_IRUSR | stat.S_IXUSR)
            try:
                result = probe_artifact_write(path)
            finally:
                path.chmod(stat.S_IRWXU)
        self.assertFalse(result["writable"])

    def test_bounded_write_read_delete_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = probe_artifact_write(directory)
            leftovers = list(Path(directory).glob(".mncs-experiment-readiness-*.tmp"))
        self.assertTrue(result["writable"])
        self.assertEqual(leftovers, [])


class ProvenanceTests(unittest.TestCase):
    def test_matching_version_without_build_is_not_exact(self) -> None:
        result = evaluate_experiment_fabric(EXPERIMENT_CERTIFIED_FABRIC_VERSION, _caps())
        self.assertEqual(result["classification"], "COMPATIBLE_VERSION_ONLY")
        self.assertFalse(result["exact"])

    def test_matching_exact_build_is_exact_certified(self) -> None:
        result = evaluate_experiment_fabric(
            EXPERIMENT_CERTIFIED_FABRIC_VERSION,
            _caps(),
            commit=EXPERIMENT_CERTIFIED_FABRIC_COMMIT,
        )
        self.assertEqual(result["classification"], "EXPERIMENT_CERTIFIED_EXACT")
        self.assertTrue(result["exact"])

    def test_null_runtime_identity_blocks_strict_epoch_freeze(self) -> None:
        result = evaluate_layers(
            harness={"available": True, "status": READY},
            fabric={
                "available": True,
                "version": EXPERIMENT_CERTIFIED_FABRIC_VERSION,
                "commit": EXPERIMENT_CERTIFIED_FABRIC_COMMIT,
                "controller_connected": True,
                "persistent_service_support": _caps(),
                "workers": [_ready_worker()],
            },
            commons={"available": True, "consumerReadCapable": True},
            artifact_write={"writable": True},
            runtime_identities={},
            require_complete_provenance=True,
        )
        self.assertEqual(result["experiment_stack"]["provenance_status"], PROVENANCE_INCOMPLETE)
        self.assertEqual(result["experiment_stack"]["epoch_freeze"], "BLOCKED")
        self.assertNotEqual(result["status"], READY)


class ProfileSemanticsTests(unittest.TestCase):
    def test_optional_missing_capability_does_not_block_unrelated_profile(self) -> None:
        result = _ready_layers()
        self.assertEqual(result["profile_status"], READY)
        warning_layers = {item["layer"] for item in result["optional_warnings"]}
        self.assertIn("forge", warning_layers)

    def test_code_analysis_profile_is_provider_neutral(self) -> None:
        result = _ready_layers(profile="code-analysis", forge={"status": READY})
        self.assertNotIn("joern", result["layers"])
        self.assertEqual(result["layers"]["forge"]["status"], READY)
        self.assertEqual(result["status"], READY)

    def test_ravel_historical_limitation_does_not_block_base_inference(self) -> None:
        result = _ready_layers(
            reference_studies={
                "available": True,
                "commit": "s" * 40,
                "schema_available": True,
                "ravel_0_5_limitation": {"disposition": "KNOWN_HISTORICAL_LIMITATION"},
                "current_ravel_lane_valid": False,
            }
        )
        self.assertEqual(result["status"], READY)
        ravel = _ready_layers(
            profile="RAVEL",
            forge={"sandbox_callable": True},
            reference_studies={
                "available": True,
                "commit": "s" * 40,
                "schema_available": True,
                "ravel_0_5_limitation": {"disposition": "KNOWN_HISTORICAL_LIMITATION"},
                "current_ravel_lane_valid": False,
            },
        )
        self.assertEqual(ravel["layers"]["reference_studies"]["status"], BLOCKED)
        self.assertEqual(ravel["status"], BLOCKED)

    def test_sustained_experiment_requires_provider_residency_lifecycle(self) -> None:
        support = {
            "persistent_service_execution": True,
            "persistent_detached_execution": True,
            "persistent_service_capability_ingestion": True,
        }
        ready = _ready_layers(
            profile="sustained-experiment",
            residency={
                "persistent_service_support": support,
                "provider_lifecycle_supported": True,
                "experiment_keep_alive": -1,
            },
        )
        self.assertEqual(ready["layers"]["residency"]["status"], READY)
        self.assertEqual(ready["status"], READY)
        self.assertFalse(
            ready["layers"]["residency"]["detail"][
                "model_residency_is_conversation_state"
            ]
        )

        unload_each_call = _ready_layers(
            profile="sustained-experiment",
            residency={
                "persistent_service_support": support,
                "provider_lifecycle_supported": True,
                "experiment_keep_alive": 0,
            },
        )
        self.assertEqual(unload_each_call["layers"]["residency"]["status"], BLOCKED)
        self.assertEqual(unload_each_call["status"], BLOCKED)


class ExperimentReadinessTests(unittest.TestCase):
    def test_ready_base_inference_with_eligible_workers(self) -> None:
        result = _ready_layers()
        self.assertEqual(result["status"], READY)
        self.assertEqual(result["schema"], "mncs.experiment-readiness.v1")
        self.assertEqual(result["claim_boundary"], CLAIM_BOUNDARY)
        self.assertEqual(result["fabric_classification"], "EXPERIMENT_CERTIFIED_EXACT")
        self.assertTrue(result["experiment_stack"]["desired_state_identities"])
        self.assertTrue(result["experiment_stack"]["experiment_stack_identity"].startswith("sha256:"))

    def test_stale_inventory_is_degraded_not_unavailable(self) -> None:
        result = _ready_layers(
            fabric={
                "available": True,
                "version": EXPERIMENT_CERTIFIED_FABRIC_VERSION,
                "commit": EXPERIMENT_CERTIFIED_FABRIC_COMMIT,
                "controller_connected": True,
                "persistent_service_support": _caps(),
                "workers": [_ready_worker(capability_inventory_status="STALE")],
            }
        )
        self.assertEqual(result["layers"]["workers"]["status"], DEGRADED)
        self.assertNotEqual(result["layers"]["workers"]["status"], BLOCKED)
        self.assertIn("STALE capability inventory is not worker UNAVAILABLE", str(result["layers"]["fleet"]["detail"]))
        self.assertIn("STALE is not fresh experiment certification", str(result["layers"]["fleet"]["detail"]))
        self.assertEqual(result["status"], DEGRADED)

    def test_unavailable_worker_blocks_without_local_fallback(self) -> None:
        result = evaluate_layers(
            control={"available": True, "status": READY},
            harness={"available": True, "status": READY},
            fabric={
                "available": True,
                "version": EXPERIMENT_CERTIFIED_FABRIC_VERSION,
                "commit": EXPERIMENT_CERTIFIED_FABRIC_COMMIT,
                "controller_connected": True,
                "persistent_service_support": _caps(),
                "workers": [{"worker_id": "fabric-worker-01", "availability": "UNAVAILABLE"}],
            },
            commons={"available": True, "consumerReadCapable": True},
            routing={"local_fallback": True, "fallback_explicit": False},
            artifact_write={"writable": True},
            runtime_identities=_runtime_identities(),
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
                "commit": EXPERIMENT_CERTIFIED_FABRIC_COMMIT,
                "controller_connected": True,
                "persistent_service_support": {},
                "workers": [_ready_worker()],
            },
            commons={"available": True, "consumerReadCapable": True},
            artifact_write={"writable": True},
            runtime_identities=_runtime_identities(),
        )
        self.assertEqual(result["layers"]["fabric_controller"]["status"], BLOCKED)
        self.assertEqual(result["status"], BLOCKED)

    def test_inspect_live_config_uses_fabric_section_not_root_config(self) -> None:
        config = load_config(Path("/missing/config.toml"))
        result = inspect_live_config(config, profile="base-inference")
        self.assertIn(result["status"], {READY, DEGRADED, BLOCKED, "UNKNOWN"})
        self.assertEqual(result["profile"], "base-inference")
        self.assertEqual(result["schema"], "mncs.experiment-readiness.v1")

    def test_stack_digest_is_stable_for_same_identity_body(self) -> None:
        first = {"schema": "mncs.experiment-stack.v1", "mncs_fabric_commit": "abc"}
        second = {"mncs_fabric_commit": "abc", "schema": "mncs.experiment-stack.v1"}
        self.assertEqual(digest_record(first), digest_record(second))


if __name__ == "__main__":
    unittest.main()
