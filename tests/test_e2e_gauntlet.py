"""Durable gauntlet pin: the checked-in driver must reproduce the chain."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from epi13_local_harness.e2e_gauntlet import MANIFEST_SCHEMA, main


def _canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


class GauntletTests(unittest.TestCase):
    def test_gauntlet_reproduces_full_chain(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gauntlet-test-") as directory:
            manifest_path = Path(directory) / "manifest.json"
            code = main(["--manifest-out", str(manifest_path)])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(code, 0, json.dumps(manifest, indent=1)[:2000])
            self.assertEqual(manifest["schema_version"], MANIFEST_SCHEMA)
            self.assertEqual(manifest["gauntlet_verdict"], "PASS")
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
            # Atlas issuance is digest-bound.
            for decision in manifest["atlas"]["decisions"]:
                self.assertTrue(decision["decision_digest"], decision)
            # Every adversarial row observes its expected verdict.
            self.assertEqual(manifest["adversarial"]["verdict"], "PASS")
            for row in manifest["adversarial"]["rows"]:
                self.assertTrue(row["pass"], row)
            # Fabric proves authorized == actual on a real execution.
            fabric = manifest["fabric"]
            self.assertEqual(fabric["verdict"], "PASS")
            self.assertEqual(fabric["disposition"], "EXECUTED")
            self.assertEqual(fabric["actual_target"], fabric["authorized_target"])
            self.assertTrue(fabric["authorization_identity"], fabric)
            # Rights binds the same identities.
            binding = manifest["rights"]["binding"]
            self.assertEqual(
                binding["authorization_identity"], fabric["authorization_identity"]
            )
            self.assertEqual(binding["requirement_id"], manifest["requirement"]["requirement_id"])


if __name__ == "__main__":
    unittest.main()
