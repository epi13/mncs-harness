"""Prove the canonical path EXECUTES MNCS (never a Python implementation).

These tests run every production decision entrypoint in ``mncs_exec``
through the shipped frozen artifacts and the real executor binary, and
compare against the test-only oracle (``tests/mncs_oracle.py``, never
imported from ``src/``). They fail closed when:

- the executor or artifacts are missing/tampered,
- a production module reintroduces a Python decision implementation,
- required MNCS execution is silently replaced by Python.
"""

from __future__ import annotations

import itertools
import json
import os
import stat
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import mncs_oracle

from mncs_harness import mncs_exec, mncs_runtime

REPO = Path(__file__).resolve().parents[1]


class ArtifactIntegrityTests(unittest.TestCase):
    def test_all_shipped_artifacts_validate(self) -> None:
        manifest_path = REPO / "src" / "mncs_harness" / "_mncs_artifacts" / "MANIFEST.json"
        self.assertTrue(manifest_path.is_file(), "artifact manifest must ship with the package")
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
            "harness_policy": {"evaluate_command", "evaluate_write", "publication_gate"},
            "harness_verdict": {"dominate", "combine4", "combine8", "is_decided"},
            "harness_atlas": {"fold_pair", "fold4", "dispatch_gate", "tool_admission"},
            "harness_pins": {"pin_fields_valid", "admit_placement", "is_exact_pin"},
            "harness_fabric": {"classify_fabric", "dispatch_allowed"},
            "harness_readiness": {"dominate_readiness", "fold4", "fold8", "is_ready"},
            "harness_eligibility": {
                "capability_eligible",
                "capability_source",
                "resource_gate",
            },
            "harness_freshness": {
                "observation_fresh",
                "attestation_window_ok",
            },
        }
        for kernel, functions in required.items():
            artifact = mncs_runtime.load_kernel(kernel)
            self.assertTrue(functions <= set(artifact.exports), kernel)


class RealExecutionTests(unittest.TestCase):
    def test_routing_executes(self) -> None:
        for flags in itertools.product([False, True], repeat=6):
            self.assertEqual(mncs_exec.primary_role(*flags), mncs_oracle.classify_route(*flags))

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

    def test_publication_gate_executes(self) -> None:
        for flags in itertools.product([False, True], repeat=3):
            self.assertEqual(
                mncs_exec.publication_gate(*flags),
                mncs_oracle.publication_gate(*flags),
                flags,
            )
        # Ready-first precedence is observable: not-ready beats disabled.
        self.assertEqual(mncs_exec.publication_gate(False, False, False), "SKIP_NOT_READY")
        self.assertEqual(mncs_exec.publication_gate(False, True, True), "SKIP_DISABLED")
        self.assertEqual(mncs_exec.publication_gate(True, True, True), "PROCEED")

    def test_verdict_executes(self) -> None:
        for left, right in itertools.product(["PASS", "FAIL", "UNKNOWN"], repeat=2):
            self.assertEqual(
                mncs_exec.combine([left, right]), mncs_oracle.combine([left, right])
            )
        self.assertEqual(mncs_exec.combine([]), "PASS")
        self.assertEqual(
            mncs_exec.combine(["PASS"] * 9 + ["FAIL"]), "FAIL"
        )
        for status in ("PASS", "FAIL", "UNKNOWN"):
            self.assertEqual(mncs_exec.verdict_decided(status), mncs_oracle.is_decided(status))

    def test_atlas_executes(self) -> None:
        for left, right in itertools.product(
            ["GRANTED", "UNKNOWN", "REFUSED"], repeat=2
        ):
            self.assertEqual(
                mncs_exec.fold_pair(left, right), mncs_oracle.fold_pair(left, right)
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
            self.assertEqual(
                mncs_exec.is_exact_pin(mode), mncs_oracle.is_exact_pin(mode), mode
            )
            for flags in itertools.product([False, True], repeat=3):
                self.assertEqual(
                    mncs_exec.pin_fields_valid(mode, *flags),
                    mncs_oracle.pin_fields_valid(mode, *flags),
                    (mode, flags),
                )
        for flags in itertools.product([False, True], repeat=3):
            self.assertEqual(
                mncs_exec.admit_placement(*flags), mncs_oracle.admit_placement(*flags), flags
            )

    def test_fabric_executes(self) -> None:
        for flags in itertools.product([False, True], repeat=5):
            want = mncs_oracle.classify_fabric(*flags)
            self.assertEqual(mncs_exec.classify_fabric(*flags), want, flags)
            self.assertEqual(
                mncs_exec.fabric_dispatch_allowed(want),
                mncs_oracle.fabric_dispatch_allowed(want),
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
                            mncs_oracle.capability_eligible(
                                capability, observed, claimed, unknown, req_obs, allow
                            ),
                            (capability, observed, claimed, unknown, req_obs, allow),
                        )
        for flags in itertools.product([False, True], repeat=4):
            self.assertEqual(
                mncs_exec.capability_source(*flags),
                mncs_oracle.capability_source(*flags),
                flags,
            )
        for flags in itertools.product([False, True], repeat=3):
            self.assertEqual(
                mncs_exec.residency_admit(*flags),
                mncs_oracle.residency_admit(*flags),
                flags,
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
                mncs_oracle.resource_gate(facts, size, budget, has_avail, avail),
            )

    def test_freshness_executes(self) -> None:
        vectors = [
            (1000, 1500, 1000),
            (1000, 2000, 1000),
            (1000, 2001, 1000),
            (3000, 2000, 100),
            (500, 500, 0),
            (0, 0, 0),
            (100, 150, 100),
            (0, 100, 100),
            (0, 101, 100),
            (200, 150, 100),
        ]
        for stored, now, budget in vectors:
            with self.subTest(stored=stored, now=now, budget=budget):
                self.assertEqual(
                    mncs_exec.observation_fresh(stored, now, budget),
                    mncs_oracle.observation_fresh(stored, now, budget),
                    (stored, now, budget),
                )
                self.assertEqual(
                    mncs_exec.attestation_window_ok(stored, now, budget),
                    mncs_oracle.attestation_window_ok(stored, now, budget),
                    (stored, now, budget),
                )

    def test_readiness_executes(self) -> None:
        states = ["READY", "DEGRADED", "BLOCKED", "UNKNOWN"]
        for left, right in itertools.product(states, repeat=2):
            self.assertEqual(
                mncs_exec.dominate_readiness(left, right),
                mncs_oracle.dominate_readiness(left, right),
            )
        for width in (1, 3, 4, 5, 8, 9, 12):
            envelope = [states[index % 4] for index in range(width)]
            self.assertEqual(
                mncs_exec.fold_readiness(envelope),
                mncs_oracle.fold_readiness(envelope),
                envelope,
            )
        for state in states:
            self.assertEqual(
                mncs_exec.readiness_ready(state), mncs_oracle.readiness_ready(state)
            )


class ExecutorDiscoveryTests(unittest.TestCase):
    """One supported discovery story: explicit env, PATH name, or dev checkout."""

    def test_explicit_executor_resolves(self) -> None:
        current = mncs_runtime.find_executor()
        with patch.dict(os.environ, {mncs_runtime.EXECUTOR_ENV: current}):
            self.assertEqual(mncs_runtime.find_executor(), current)

    def test_missing_explicit_executor_fails_closed(self) -> None:
        with patch.dict(os.environ, {mncs_runtime.EXECUTOR_ENV: "/nonexistent/mncs-executor"}):
            with self.assertRaises(mncs_runtime.MncsRuntimeError):
                mncs_runtime.find_executor()

    def test_path_discovered_executor_resolves(self) -> None:
        import tempfile

        current = mncs_runtime.find_executor()
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / "mncs-executor"
            link.symlink_to(current)
            env = {k: v for k, v in os.environ.items() if k != mncs_runtime.EXECUTOR_ENV}
            env["PATH"] = directory
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(mncs_runtime.find_executor(), str(link))

    def test_no_executor_anywhere_fails_closed(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != mncs_runtime.EXECUTOR_ENV}
        with (
            patch.dict(os.environ, env, clear=True),
            patch("shutil.which", return_value=None),
            patch.object(Path, "is_file", return_value=False),
        ):
            with self.assertRaises(mncs_runtime.MncsRuntimeError):
                mncs_runtime.find_executor()


class FetchDigestTests(unittest.TestCase):
    """The fetch script installs nothing unless the digest verifies (no network)."""

    def _run_fetch(self, served: bytes, digest: str, dest: Path) -> int:
        import importlib.util
        import io

        spec = importlib.util.spec_from_file_location(
            "fetch_mncs_executor", REPO / "scripts" / "fetch_mncs_executor.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)

        class FakeResponse(io.BytesIO):
            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, *args: object) -> None:
                return None

        spec.loader.exec_module(module)
        with (
            patch.object(module.urllib.request, "urlopen", return_value=FakeResponse(served)),
            patch.object(
                sys, "argv", ["fetch_mncs_executor.py", "--dest", str(dest), "--digest", digest]
            ),
        ):
            try:
                module.main()
            except SystemExit as exc:
                return int(exc.code or 0)
            return 0

    def test_wrong_digest_installs_nothing(self) -> None:
        import tempfile

        served = b"tampered-executor-bytes"
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / "sub" / "mncs-executor"
            code = self._run_fetch(served, "0" * 64, dest)
            self.assertNotEqual(code, 0)
            self.assertFalse(dest.exists())

    def test_matching_digest_installs_executable(self) -> None:
        import hashlib
        import tempfile

        served = b"executor-bytes-fixture"
        digest = hashlib.sha256(served).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / "sub" / "mncs-executor"
            code = self._run_fetch(served, digest, dest)
            self.assertEqual(code, 0)
            self.assertEqual(dest.read_bytes(), served)
            self.assertTrue(dest.stat().st_mode & stat.S_IXUSR)


class FailClosedTests(unittest.TestCase):
    def test_executor_failure_raises_without_fallback(self) -> None:
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
            with self.assertRaises(mncs_runtime.MncsRuntimeError):
                mncs_exec.is_exact_pin("AUTO")
            with self.assertRaises(mncs_runtime.MncsRuntimeError):
                mncs_exec.publication_gate(True, True, True)
            with self.assertRaises(mncs_runtime.MncsRuntimeError):
                mncs_exec.capability_source(True, True, True, True)

    def test_no_transitional_fallback_exists(self) -> None:
        self.assertFalse(hasattr(mncs_runtime, "transitional_allowed"))
        self.assertFalse(hasattr(mncs_runtime, "warn_transitional"))
        self.assertFalse(hasattr(mncs_runtime, "TRANSITIONAL_ENV"))
        self.assertFalse(hasattr(mncs_exec, "_transitional"))
        # The legacy escape hatch is dead: setting it changes nothing.
        with patch.dict(
            os.environ,
            {"MNCS_HARNESS_TRANSITIONAL_PYTHON_DECISIONS": "1"},
            clear=False,
        ):
            with patch.object(
                mncs_runtime, "call", side_effect=mncs_runtime.MncsRuntimeError("boom")
            ):
                with self.assertRaises(mncs_runtime.MncsRuntimeError):
                    mncs_exec.primary_role(False, False, False, False, False, False)

    def test_tampered_artifact_fails_load(self) -> None:
        with patch.object(
            mncs_runtime, "_artifacts_dir", return_value=REPO / "corpora"
        ):
            with self.assertRaises(mncs_runtime.MncsRuntimeError):
                mncs_runtime.load_kernel("harness_routing")

    def test_tampered_bytes_fail_closed(self) -> None:
        import tempfile

        real = mncs_runtime.load_kernel("harness_routing")
        raw = json.loads(real.path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            staged = Path(directory)
            (staged / "MANIFEST.json").write_text(
                json.dumps(
                    {
                        "artifacts": {
                            "harness_routing.wasm.json": {
                                "module": raw.get("module", "mncs.harness.routing.v1"),
                                "backend": "wasm",
                                "identity": raw["identity"],
                                "bytes_sha256": raw["bytes_sha256"],
                                "exports": ["classify_route"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            tampered = dict(raw)
            blob = bytearray(bytes.fromhex(raw["bytes_hex"]))
            blob[0] ^= 0xFF
            tampered["bytes_hex"] = bytes(blob).hex()
            (staged / "harness_routing.wasm.json").write_text(
                json.dumps(tampered), encoding="utf-8"
            )
            with patch.object(mncs_runtime, "_artifacts_dir", return_value=staged):
                with self.assertRaises(mncs_runtime.MncsRuntimeError):
                    mncs_runtime.load_kernel("harness_routing")

    def test_wrong_identity_fails_closed(self) -> None:
        import tempfile

        real = mncs_runtime.load_kernel("harness_routing")
        raw = json.loads(real.path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            staged = Path(directory)
            (staged / "MANIFEST.json").write_text(
                json.dumps(
                    {
                        "artifacts": {
                            "harness_routing.wasm.json": {
                                "module": "mncs.harness.routing.v1",
                                "backend": "wasm",
                                "identity": "mncs:compiler:backend-artifact:tampered",
                                "bytes_sha256": raw["bytes_sha256"],
                                "exports": ["classify_route"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            (staged / "harness_routing.wasm.json").write_text(
                json.dumps(raw), encoding="utf-8"
            )
            with patch.object(mncs_runtime, "_artifacts_dir", return_value=staged):
                with self.assertRaises(mncs_runtime.MncsRuntimeError):
                    mncs_runtime.load_kernel("harness_routing")

    def test_artifacts_resolve_without_repo_relative_paths(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            try:
                os.chdir(directory)
                artifact = mncs_runtime.load_kernel("harness_routing")
                self.assertTrue(artifact.path.is_file())
            finally:
                os.chdir(previous)

    def test_production_modules_do_not_reimplement_decisions(self) -> None:
        offenders = []
        for path in (REPO / "src" / "mncs_harness").glob("*.py"):
            if path.name == "mncs_exec.py":
                continue
            text = path.read_text(encoding="utf-8")
            if "mncs_logic" in text or "mncs_oracle" in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
