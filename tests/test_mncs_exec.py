"""Prove the canonical path EXECUTES MNCS (never the Python mirror).

These tests run every production decision entrypoint in ``mncs_exec``
through the shipped frozen artifacts and the real executor binary, and
compare against the transitional oracle. They fail closed when:

- the executor or artifacts are missing/tampered,
- a production module bypasses ``mncs_exec`` for the Python mirror,
- required MNCS execution is silently replaced by Python.
"""

from __future__ import annotations

import itertools
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from mncs_harness import mncs_exec, mncs_logic, mncs_runtime

REPO = Path(__file__).resolve().parents[1]


class ArtifactIntegrityTests(unittest.TestCase):
    def test_all_shipped_artifacts_validate(self) -> None:
        manifest_path = REPO / "src" / "mncs_harness" / "_mncs_artifacts" / "MANIFEST.json"
        self.assertTrue(manifest_path.is_file(), "artifact manifest must ship with the package")
        import json

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(manifest["artifacts"]), 14)
        for name, entry in sorted(manifest["artifacts"].items()):
            kernel = name.split(".")[0]
            for backend in ("wasm", "bytecode"):
                if not name.endswith(f".{backend}.json"):
                    continue
                artifact = mncs_runtime.load_kernel(kernel, backend)
                self.assertEqual(artifact.identity, entry["identity"])
                self.assertTrue(artifact.exports, name)

    def test_required_exports_present(self) -> None:
        required = {
            "harness_routing": {"classify_route", "needs_coder"},
            "harness_policy": {"evaluate_command", "evaluate_write"},
            "harness_verdict": {"dominate", "combine4", "combine8", "is_decided"},
            "harness_atlas": {"fold_pair", "fold4", "dispatch_gate", "tool_admission"},
            "harness_pins": {"pin_fields_valid", "admit_placement"},
            "harness_fabric": {"classify_fabric", "dispatch_allowed"},
            "harness_readiness": {"dominate_readiness", "fold4", "fold8", "is_ready"},
        }
        for kernel, functions in required.items():
            artifact = mncs_runtime.load_kernel(kernel)
            self.assertTrue(functions <= set(artifact.exports), kernel)


class RealExecutionTests(unittest.TestCase):
    def test_routing_executes(self) -> None:
        for flags in itertools.product([False, True], repeat=6):
            self.assertEqual(mncs_exec.primary_role(*flags), mncs_logic.classify_route(*flags))

    def test_policy_executes(self) -> None:
        vectors = [
            (False, True, False, True, True, True, True, 0),
            (True, True, False, True, True, True, True, 1),
            (False, False, False, True, True, True, True, 2),
            (False, True, True, True, True, True, True, 3),
            (False, True, False, False, True, True, True, 4),
            (False, True, False, True, False, True, True, 5),
            (False, True, False, True, True, False, True, 6),
            (False, True, False, True, True, True, False, 7),
        ]
        for *flags, want in vectors:
            self.assertEqual(mncs_exec.command_reason(*flags), want, flags)
        self.assertTrue(mncs_exec.write_allowed(False))
        self.assertFalse(mncs_exec.write_allowed(True))

    def test_verdict_executes(self) -> None:
        for left, right in itertools.product(["PASS", "FAIL", "UNKNOWN"], repeat=2):
            self.assertEqual(
                mncs_exec.combine([left, right]), mncs_logic.combine([left, right])
            )
        self.assertEqual(mncs_exec.combine([]), "PASS")
        self.assertEqual(
            mncs_exec.combine(["PASS"] * 9 + ["FAIL"]), "FAIL"
        )
        for status in ("PASS", "FAIL", "UNKNOWN"):
            self.assertEqual(mncs_exec.verdict_decided(status), mncs_logic.is_decided(status))

    def test_atlas_executes(self) -> None:
        for left, right in itertools.product(
            ["GRANTED", "UNKNOWN", "REFUSED"], repeat=2
        ):
            self.assertEqual(
                mncs_exec.fold_pair(left, right), mncs_logic.fold_pair(left, right)
            )
        self.assertEqual(
            mncs_exec.fold(["GRANTED", "UNKNOWN", "REFUSED"]), "REFUSED"
        )
        self.assertEqual(mncs_exec.fold([]), "GRANTED")
        self.assertEqual(mncs_exec.tool_admission(True, True), "GRANTED")
        self.assertEqual(mncs_exec.tool_admission(False, True), "REFUSED")
        self.assertEqual(mncs_exec.tool_admission(True, False), "REFUSED")
        self.assertEqual(mncs_exec.dispatch_gate("GRANTED", True), "GRANTED")
        self.assertEqual(mncs_exec.dispatch_gate("GRANTED", False), "REFUSED")
        self.assertEqual(mncs_exec.dispatch_gate("UNKNOWN", False), "UNKNOWN")

    def test_pins_execute(self) -> None:
        modes = ["AUTO", "ROLE", "MODEL", "WORKER", "WORKER_MODEL", "WORKER_MODEL_ROLE"]
        for mode in modes:
            for flags in itertools.product([False, True], repeat=3):
                self.assertEqual(
                    mncs_exec.pin_fields_valid(mode, *flags),
                    mncs_logic.pin_fields_valid(mode, *flags),
                    (mode, flags),
                )
        for flags in itertools.product([False, True], repeat=3):
            self.assertEqual(
                mncs_exec.admit_placement(*flags), mncs_logic.admit_placement(*flags), flags
            )

    def test_fabric_executes(self) -> None:
        for flags in itertools.product([False, True], repeat=5):
            want = mncs_logic.classify_fabric(*flags)
            self.assertEqual(mncs_exec.classify_fabric(*flags), want, flags)
            self.assertEqual(
                mncs_exec.fabric_dispatch_allowed(want),
                mncs_logic.fabric_dispatch_allowed(want),
                flags,
            )

    def test_eligibility_executes(self) -> None:
        for capability in ("COMPLETION", "TOOLS", "CODE_EDIT"):
            for observed in ("NONE", "PASS", "FAIL"):
                for unknown in (
                    "FAIL_CLOSED",
                    "EXPLORE",
                    "PROVIDER_CLAIM_COMPAT",
                    "OTHER",
                ):
                    for claimed, req_obs, allow in itertools.product(
                        [False, True], repeat=3
                    ):
                        self.assertEqual(
                            mncs_exec.capability_eligible(
                                capability, observed, claimed, unknown, req_obs, allow
                            ),
                            mncs_logic.capability_eligible(
                                capability, observed, claimed, unknown, req_obs, allow
                            ),
                            (capability, observed, claimed, unknown, req_obs, allow),
                        )
        for facts, size, budget, has_avail, avail in [
            (False, 0, 0, False, 0),
            (True, 300, 200, False, 0),
            (True, 150, 200, True, 100),
            (True, 150, 200, True, 180),
            (True, 200, 200, True, 200),
        ]:
            self.assertEqual(
                mncs_exec.resource_gate(facts, size, budget, has_avail, avail),
                mncs_logic.resource_gate(facts, size, budget, has_avail, avail),
            )

    def test_readiness_executes(self) -> None:
        states = ["READY", "DEGRADED", "BLOCKED", "UNKNOWN"]
        for left, right in itertools.product(states, repeat=2):
            self.assertEqual(
                mncs_exec.dominate_readiness(left, right),
                mncs_logic.dominate_readiness(left, right),
            )
        for width in (1, 3, 4, 5, 8, 9, 12):
            envelope = [states[index % 4] for index in range(width)]
            self.assertEqual(
                mncs_exec.fold_readiness(envelope),
                mncs_logic.fold_readiness(envelope),
                envelope,
            )
        for state in states:
            self.assertEqual(
                mncs_exec.readiness_ready(state), mncs_logic.readiness_ready(state)
            )


class FailClosedTests(unittest.TestCase):
    def test_executor_failure_raises_without_fallback(self) -> None:
        self.assertNotEqual(os.environ.get(mncs_runtime.TRANSITIONAL_ENV), "1")
        with patch.object(
            mncs_runtime, "call", side_effect=mncs_runtime.MncsRuntimeError("boom")
        ):
            with self.assertRaises(mncs_runtime.MncsRuntimeError):
                mncs_exec.primary_role(False, False, False, False, False, False)
            with self.assertRaises(mncs_runtime.MncsRuntimeError):
                mncs_exec.combine(["PASS"])
            with self.assertRaises(mncs_runtime.MncsRuntimeError):
                mncs_exec.fold(["GRANTED"])
            with self.assertRaises(mncs_runtime.MncsRuntimeError):
                mncs_exec.fold_readiness(["READY"])
            with self.assertRaises(mncs_runtime.MncsRuntimeError):
                mncs_exec.classify_fabric(False, True, False, False, False)

    def test_tampered_artifact_fails_load(self) -> None:
        with patch.object(
            mncs_runtime, "_artifacts_dir", return_value=REPO / "corpora"
        ):
            with self.assertRaises(mncs_runtime.MncsRuntimeError):
                mncs_runtime.load_kernel("harness_routing")

    def test_production_modules_do_not_import_mirror(self) -> None:
        offenders = []
        for path in (REPO / "src" / "mncs_harness").glob("*.py"):
            if path.name in ("mncs_logic.py", "mncs_exec.py"):
                continue
            text = path.read_text(encoding="utf-8")
            if "mncs_logic" in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_transitional_fallback_is_explicit_and_loud(self) -> None:
        env = {mncs_runtime.TRANSITIONAL_ENV: "1"}
        with patch.dict(os.environ, env, clear=False):
            with patch.object(
                mncs_runtime,
                "call",
                side_effect=mncs_runtime.MncsRuntimeError("no executor"),
            ):
                import io
                from contextlib import redirect_stderr

                buffer = io.StringIO()
                with redirect_stderr(buffer):
                    self.assertEqual(
                        mncs_exec.primary_role(False, False, False, False, False, False),
                        "e2b",
                    )
                self.assertIn("TRANSITIONAL", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
