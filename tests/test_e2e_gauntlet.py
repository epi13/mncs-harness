"""Durable gauntlet pin: the checked-in driver must reproduce the chain."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from epi13_local_harness.e2e_gauntlet import MANIFEST_SCHEMA, _cuda_leg, main


def _canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _atlas_checkout_present() -> bool:
    return (Path(__file__).resolve().parents[2] / "mncs-atlas" / "admission").is_dir()


class GauntletTests(unittest.TestCase):
    @unittest.skipUnless(
        _atlas_checkout_present(),
        "mncs-atlas sibling checkout unavailable: gauntlet needs live Atlas issuance",
    )
    def test_gauntlet_reproduces_full_chain(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gauntlet-test-") as directory:
            manifest_path = Path(directory) / "manifest.json"
            code = main(["--manifest-out", str(manifest_path)])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(code, 0, json.dumps(manifest, indent=1)[:2000])
            self.assertEqual(manifest["schema_version"], MANIFEST_SCHEMA)
            self.assertEqual(manifest["gauntlet_verdict"], "PASS")
            # Behavior and coverage report separately: observed behavior
            # must be correct AND the run must say which subsystems never
            # participated (epi13/mncs-harness#60).
            self.assertEqual(manifest["behavior_verdict"], "PASS")
            # Aggregate digest recomputes from the manifest itself.
            body = {k: v for k, v in manifest.items() if k != "aggregate_digest"}
            self.assertEqual(
                manifest["aggregate_digest"], hashlib.sha256(_canonical(body)).hexdigest()
            )
            # Exact revisions for every family component.
            for repo in (
                "mncs-harness",
                "mncs-atlas",
                "mncs-language",
                "mncs-fabric",
                "mncs-rights-provenance",
                "mncs-commons",
                "mncs-actions",
                "mncs-lineage",
            ):
                self.assertIn(repo, manifest["revisions"])
            # Atlas issuance is digest-bound AND issuer-signed.
            self.assertTrue(manifest["atlas"]["issuer_key_id"], manifest["atlas"])
            self.assertTrue(manifest["atlas"]["trust_roots"], manifest["atlas"])
            for decision in manifest["atlas"]["decisions"]:
                self.assertTrue(decision["decision_digest"], decision)
                self.assertEqual(
                    decision["issuer_key_id"], manifest["atlas"]["issuer_key_id"], decision
                )
            # Every adversarial row observes its expected verdict,
            # including the fresh-digest forgery row.
            self.assertEqual(manifest["adversarial"]["verdict"], "PASS")
            row_names = set()
            for row in manifest["adversarial"]["rows"]:
                self.assertTrue(row["pass"], row)
                row_names.add(row["name"])
            self.assertIn("forged-grant-fresh-digest", row_names)
            # The CUDA leg degrades honestly without a kernel artifact.
            cuda = manifest["cuda"]
            fabric = manifest["fabric"]
            if fabric["verdict"] == "UNKNOWN":
                # Legacy Fabric cannot assert worker capability, so the
                # execution leg the CUDA leg rides on never establishes;
                # the skip itself must stay explicit.
                self.assertIn("min-supported", fabric["reason"])
                self.assertEqual(cuda["reason"], "no cuda leg ran")
                self.assertEqual(manifest["rights"]["verdict"], "UNKNOWN")
                return
            self.assertEqual(cuda["verdict"], "UNKNOWN")
            self.assertIn("--ptx-kernel", cuda["reason"])
            # Fabric proves authorized == actual on a real execution.
            self.assertEqual(fabric["verdict"], "PASS")
            self.assertEqual(fabric["disposition"], "EXECUTED")
            self.assertEqual(fabric["actual_target"], fabric["authorized_target"])
            self.assertTrue(fabric["authorization_identity"], fabric)
            # Rights is an honest projection, not an owner PASS: the
            # binding is recorded for traceability while independent
            # Rights validation stays a coverage gap.
            rights = manifest["rights"]
            self.assertEqual(rights["verdict"], "UNKNOWN")
            self.assertEqual(rights["phase"], "rights_binding_projection")
            binding = rights["binding"]
            self.assertEqual(
                binding["authorization_identity"], fabric["authorization_identity"]
            )
            self.assertEqual(binding["requirement_id"], manifest["requirement"]["requirement_id"])
            # Coverage is PARTIAL while legs stay UNKNOWN: behavior-PASS
            # must never read as complete end-to-end execution.
            self.assertEqual(manifest["coverage_verdict"], "PARTIAL")
            gap_phases = {item["phase"] for item in manifest["coverage_gaps"]}
            self.assertIn("cuda", gap_phases)
            self.assertIn("rights", gap_phases)

    def test_cuda_leg_degrades_without_kernel(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gauntlet-cuda-") as directory:
            workspace = Path(directory)
            missing = _cuda_leg(None, None, None, workspace, None, "worker-01")
            self.assertEqual(missing["verdict"], "UNKNOWN")
            absent = _cuda_leg(
                None, None, None, workspace, Path(directory) / "nope.ptx", "worker-01"
            )
            self.assertEqual(absent["verdict"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
