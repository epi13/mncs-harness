from __future__ import annotations

import json
import unittest
from pathlib import Path

from epi13_local_harness.fabric_compat import (
    EXPERIMENT_CERTIFIED_FABRIC_ARTIFACT_DIGEST,
    EXPERIMENT_CERTIFIED_FABRIC_COMMIT,
    EXPERIMENT_CERTIFIED_FABRIC_VERSION,
    EXPERIMENT_REQUIRED_CAPABILITIES,
    MIN_SUPPORTED_FABRIC_COMMIT,
    MIN_SUPPORTED_FABRIC_VERSION,
    evaluate_experiment_fabric,
    fabric_compatibility_pins,
)


def _caps(**overrides: bool) -> dict[str, bool]:
    capabilities = {name: True for name in EXPERIMENT_REQUIRED_CAPABILITIES}
    capabilities.update(overrides)
    return capabilities


class FabricCompatibilityPolicyTests(unittest.TestCase):
    def test_pins_are_three_distinct_concepts(self) -> None:
        pins = fabric_compatibility_pins()
        self.assertEqual(pins["minimum_supported_version"], MIN_SUPPORTED_FABRIC_VERSION)
        self.assertEqual(pins["minimum_supported_commit"], MIN_SUPPORTED_FABRIC_COMMIT)
        self.assertEqual(pins["experiment_certified_version"], EXPERIMENT_CERTIFIED_FABRIC_VERSION)
        self.assertEqual(pins["experiment_certified_commit"], EXPERIMENT_CERTIFIED_FABRIC_COMMIT)
        self.assertEqual(
            pins["experiment_certified_artifact_digest"],
            EXPERIMENT_CERTIFIED_FABRIC_ARTIFACT_DIGEST,
        )
        self.assertEqual(pins["forward_compatibility_ref"], "main")
        self.assertNotEqual(
            pins["minimum_supported_commit"], pins["experiment_certified_commit"]
        )
        self.assertNotEqual(pins["experiment_certified_commit"], "main")
        self.assertNotEqual(pins["experiment_certified_commit"], "latest")

    def test_checked_in_pin_file_matches_module(self) -> None:
        path = Path(__file__).resolve().parents[1] / "compat" / "experiment-stack.json"
        declared = json.loads(path.read_text(encoding="utf-8"))
        pins = fabric_compatibility_pins()
        self.assertEqual(declared["fabric"]["minimum_supported_commit"], pins["minimum_supported_commit"])
        self.assertEqual(
            declared["fabric"]["experiment_certified_commit"], pins["experiment_certified_commit"]
        )
        self.assertEqual(
            declared["fabric"]["experiment_certified_artifact_digest"],
            pins["experiment_certified_artifact_digest"],
        )
        self.assertEqual(declared["claim_boundary"], "infrastructure validation")

    def test_ci_binds_certified_and_minimum_as_separate_jobs(self) -> None:
        ci = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(EXPERIMENT_CERTIFIED_FABRIC_COMMIT, ci)
        self.assertIn(MIN_SUPPORTED_FABRIC_COMMIT, ci)
        self.assertIn("fabric-min-supported", ci)
        self.assertIn("fabric-main-canary", ci)
        self.assertIn("Checkout experiment-certified MNCS Fabric", ci)

    def test_too_old_fabric_fails_closed(self) -> None:
        result = evaluate_experiment_fabric("0.2.0a16", _caps())
        self.assertEqual(result["classification"], "TOO_OLD")
        self.assertEqual(result["action"], "dispatch_blocked")

    def test_experiment_certified_fabric_passes(self) -> None:
        result = evaluate_experiment_fabric(
            EXPERIMENT_CERTIFIED_FABRIC_VERSION,
            _caps(),
            commit=EXPERIMENT_CERTIFIED_FABRIC_COMMIT,
        )
        self.assertEqual(result["classification"], "EXPERIMENT_CERTIFIED_EXACT")
        self.assertEqual(result["action"], "dispatch_allowed")

    def test_version_without_immutable_identity_is_not_exact(self) -> None:
        result = evaluate_experiment_fabric(EXPERIMENT_CERTIFIED_FABRIC_VERSION, _caps())
        self.assertEqual(result["classification"], "COMPATIBLE_VERSION_ONLY")
        self.assertEqual(result["action"], "dispatch_allowed")

    def test_matching_artifact_digest_is_exact_without_commit(self) -> None:
        result = evaluate_experiment_fabric(
            EXPERIMENT_CERTIFIED_FABRIC_VERSION,
            _caps(),
            artifact_digest=EXPERIMENT_CERTIFIED_FABRIC_ARTIFACT_DIGEST,
        )
        self.assertEqual(result["classification"], "EXPERIMENT_CERTIFIED_EXACT")
        self.assertEqual(result["action"], "dispatch_allowed")

    def test_newer_compatible_fabric_can_pass(self) -> None:
        result = evaluate_experiment_fabric("0.2.0a31", _caps())
        self.assertEqual(result["classification"], "COMPATIBLE_NEWER")
        self.assertEqual(result["action"], "dispatch_allowed")

    def test_unknown_version_fails_closed(self) -> None:
        result = evaluate_experiment_fabric("not-a-version", _caps())
        self.assertEqual(result["classification"], "UNKNOWN")
        self.assertEqual(result["action"], "dispatch_blocked")

    def test_version_cannot_override_missing_required_capabilities(self) -> None:
        result = evaluate_experiment_fabric(
            EXPERIMENT_CERTIFIED_FABRIC_VERSION,
            _caps(classified_fleet_refresh=False),
            commit=EXPERIMENT_CERTIFIED_FABRIC_COMMIT,
        )
        self.assertEqual(result["classification"], "INCOMPATIBLE")
        self.assertEqual(result["action"], "dispatch_blocked")
        self.assertEqual(result["missing_capabilities"], ["classified_fleet_refresh"])


if __name__ == "__main__":
    unittest.main()
