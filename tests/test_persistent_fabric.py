from __future__ import annotations

import importlib.util
import json
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from epi13_local_harness.config import load_config
from epi13_local_harness.fabric import FabricExecutionError, FabricSession
from epi13_local_harness.fabric_target_tools import FabricTargetToolExecutor
from epi13_local_harness.models import FabricConfig, FabricWorkerConfig, SessionTargets
from epi13_local_harness.tools import ToolRegistry

OPENSSL = shutil.which("openssl")


class _OllamaFixture(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        length = int(self.headers["Content-Length"])
        json.loads(self.rfile.read(length))
        body = json.dumps(
            {
                "message": {"role": "assistant", "content": "persistent fixture response"},
                "eval_count": 2,
                "eval_duration": 1_000_000,
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


def _certificates(root: Path) -> dict[str, Path]:
    assert OPENSSL
    ca_key, ca_cert = root / "ca.key", root / "ca.pem"
    subprocess.run(
        [
            OPENSSL, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(ca_key), "-out", str(ca_cert), "-subj",
            "/CN=Harness Fabric test CA", "-days", "1", "-addext",
            "basicConstraints=critical,CA:TRUE", "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    result: dict[str, Path] = {"ca": ca_cert}
    for name in ("server", "client"):
        key, csr, cert = root / f"{name}.key", root / f"{name}.csr", root / f"{name}.pem"
        subprocess.run(
            [
                OPENSSL, "req", "-new", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(key), "-out", str(csr), "-subj", f"/CN=Harness Fabric {name}",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                OPENSSL, "x509", "-req", "-in", str(csr), "-CA", str(ca_cert),
                "-CAkey", str(ca_key), "-CAcreateserial", "-out", str(cert),
                "-days", "1", "-sha256",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        result[name] = cert
        result[f"{name}_key"] = key
    return result


@unittest.skipUnless(importlib.util.find_spec("mncs_fabric"), "mncs-fabric is not installed")
class PersistentFabricTests(unittest.TestCase):
    def setUp(self) -> None:
        from mncs_fabric.controller_service import ControllerConfig, ControllerService

        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.socket = self.root / "controller.sock"
        self.service = ControllerService(
            ControllerConfig(
                "persistent-fixture",
                self.root / "lifecycle.jsonl",
                service_log=self.root / "controller-service.jsonl",
                socket_path=self.socket,
                admin_socket_path=self.root / "controller-admin.sock",
            )
        )
        self.thread = threading.Thread(
            target=self.service.run,
            kwargs={"max_seconds": 30.0},
            daemon=True,
        )
        self.thread.start()
        for _ in range(100):
            if self.socket.is_socket():
                return
            time.sleep(0.02)
        self.fail("persistent Fabric consumer socket did not start")

    def tearDown(self) -> None:
        self.service.request_stop()
        self.thread.join(timeout=3)
        self.directory.cleanup()

    def _config(self) -> FabricConfig:
        return FabricConfig(
            enabled=True,
            controller_mode="service",
            service_socket=self.socket,
            service_timeout_seconds=2.0,
            refresh_on_startup=False,
        )

    def test_service_session_reads_fleet_without_owning_controller(self) -> None:
        first = FabricSession(self._config())
        second = FabricSession(self._config())
        first.initialize()
        second.initialize()

        self.assertEqual(first.status().controller_state, "connected")
        self.assertEqual(first.status().fleet_state, "empty")
        self.assertEqual(first.status().execution_transport, "unsupported")
        self.assertEqual(first.status().target_execution_transport, "unsupported")
        self.assertEqual(first.status().controller_version, self.service.status()["fabric_version"])
        self.assertEqual(first.status().controller_contract_identity, self.service.status()["public_contract_identity"])
        self.assertEqual(first.status().fleet_authority, "persistent-controller")
        self.assertEqual(first.status().workers, second.status().workers)

        with self.assertRaisesRegex(
            FabricExecutionError, "FABRIC_SERVICE_EXECUTION_UNSUPPORTED"
        ):
            first.chat(load_config(Path("/missing/config.toml")).models["e2b"], [{"role": "user", "content": "hello"}])

        first.close()
        self.assertEqual(self.service.status()["service_runtime"], "RUNNING")
        second.close()
        self.assertEqual(self.service.status()["service_runtime"], "RUNNING")

    def test_service_socket_failure_is_fail_closed(self) -> None:
        config = self._config()
        config = FabricConfig(
            enabled=True,
            controller_mode="service",
            service_socket=Path(self.directory.name) / "missing.sock",
            refresh_on_startup=False,
        )
        session = FabricSession(config)
        session.initialize()
        status = session.status()
        self.assertEqual(status.controller_state, "unavailable")
        self.assertIn("FABRIC_CONTROLLER_UNAVAILABLE", status.detail or "")
        self.assertIsNone(session.client)

    @unittest.skipUnless(OPENSSL, "openssl is required for ephemeral Fabric TLS certificates")
    def test_service_mode_inference_uses_controller_owned_worker_backend(self) -> None:
        from mncs_fabric.controller_service import ControllerConfig, ControllerService
        from mncs_fabric.enrollment import TrustStore, certificate_fingerprint
        from mncs_fabric.lifecycle import LifecycleStore
        from mncs_fabric.registry import RegistryWorker, WorkerRegistry
        from mncs_fabric.transport import TLSWorkerServer
        from mncs_fabric.worker import LocalWorker

        self.service.request_stop()
        self.thread.join(timeout=3)

        cert_root = self.root / "certificates"
        cert_root.mkdir()
        cert = _certificates(cert_root)
        controller_trust_path = self.root / "controller-trust.jsonl"
        worker_trust = TrustStore(self.root / "worker-trust.jsonl")
        TrustStore(controller_trust_path).enroll(
            "worker",
            "persistent-worker",
            certificate_fingerprint(
                ssl.PEM_cert_to_DER_cert(cert["server"].read_text(encoding="ascii"))
            ),
        )
        worker_trust.enroll(
            "controller",
            "persistent-fixture",
            certificate_fingerprint(
                ssl.PEM_cert_to_DER_cert(cert["client"].read_text(encoding="ascii"))
            ),
        )
        worker_root = self.root / "worker-root"
        worker_root.mkdir()
        worker = LocalWorker(
            "persistent-worker",
            worker_root,
            self.root / "worker-ledger.jsonl",
            bundle_cache_root=self.root / "worker-bundles",
        )
        worker_server = TLSWorkerServer(
            worker,
            "127.0.0.1",
            0,
            ca_file=cert["ca"],
            server_cert=cert["server"],
            server_key=cert["server_key"],
            controller_id="persistent-fixture",
            worker_id="persistent-worker",
            trust_store=worker_trust,
            timeout=3,
        )
        worker_port = worker_server.bind()
        worker_thread = threading.Thread(
            target=worker_server.serve_forever,
            kwargs={"max_requests": 40, "idle_timeout": 15},
            daemon=True,
        )
        worker_thread.start()
        # a17 does not separate TLS connect readiness from the job deadline.
        # Wait for the listener to accept before the persistent-service request.
        worker_ready = False
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", worker_port), timeout=0.1):
                    worker_ready = True
                    break
            except OSError:
                time.sleep(0.05)
        self.assertTrue(worker_ready, "worker TLS listener did not become ready")

        lifecycle = LifecycleStore(self.root / "lifecycle.jsonl")
        authorization = lifecycle.create_authorization(
            expected_worker_identity="persistent-worker"
        )
        public_key = subprocess.run(
            [OPENSSL, "x509", "-in", str(cert["server"]), "-pubkey", "-noout"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        enrollment = lifecycle.build_request(
            worker_identity="persistent-worker",
            public_key_pem=public_key,
            hostname_hint="persistent-worker.test",
            operating_system="linux",
            architecture="x86_64",
            authorization_id=str(authorization["authorization_id"]),
        )
        lifecycle.submit_request(enrollment, str(authorization["token"]))
        lifecycle.approve_request(str(enrollment["request_id"]))

        registry_path = self.root / "workers.json"
        registry = WorkerRegistry(registry_path, controller_id="persistent-fixture")
        registry.register(
            RegistryWorker(
                worker_id="persistent-worker",
                host="127.0.0.1",
                port=worker_port,
                capabilities=tuple(sorted(worker.capabilities())),
                ca_file=str(cert["ca"]),
                client_certificate=str(cert["client"]),
                client_key=str(cert["client_key"]),
                trust_state=str(controller_trust_path),
            )
        )
        execution_root = self.root / "execution-bundles"
        config = ControllerConfig(
            "persistent-fixture",
            self.root / "lifecycle.jsonl",
            service_log=self.root / "controller-service.jsonl",
            socket_path=self.socket,
            admin_socket_path=self.root / "controller-admin.sock",
            worker_registry_path=registry_path,
            worker_state_path=self.root / "controller-workers.jsonl",
            execution_bundle_root=execution_root,
        )
        self.service = ControllerService(config)
        self.thread = threading.Thread(
            target=self.service.run,
            kwargs={"max_seconds": 30.0},
            daemon=True,
        )
        self.thread.start()
        for _ in range(150):
            if self.socket.is_socket():
                break
            time.sleep(0.02)
        self.assertTrue(self.socket.is_socket())

        ollama = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaFixture)
        ollama_thread = threading.Thread(target=ollama.serve_forever, daemon=True)
        ollama_thread.start()
        session = None
        try:
            config = replace(
                self._config(),
                state_path=self.root / "harness-private-state" / "fabric.jsonl",
                provider_ollama_base_url=f"http://127.0.0.1:{ollama.server_port}",
            )
            session = FabricSession(config)
            session.initialize()
            self.assertEqual(session.status().execution_transport, "persistent-service")
            self.assertEqual(
                session.status().target_execution_transport,
                "persistent-service",
            )
            model = replace(
                load_config(Path("/missing/config.toml")).models["e2b"],
                provider="fabric",
                execution_device="cpu",
            )
            response, metadata = session.chat(
                model,
                [{"role": "user", "content": "hello from persistent Harness"}],
            )
            self.assertEqual(response["message"]["content"], "persistent fixture response")
            self.assertEqual(metadata["fabric_worker"], "persistent-worker")
            self.assertEqual(metadata["execution_transport"], "persistent-service")

            session.set_consumer_context(
                workload_identity="sha256:" + "a" * 64,
                provider_identity="sha256:" + "b" * 64,
                partition_identity="sha256:" + "c" * 64,
            )
            observation = session.client.ingest_capability_observation(
                "persistent-worker",
                [{"kind": "runtime", "namespace": "system", "name": "python"}],
            )
            workspace = self.root / "tool-workspace"
            workspace.mkdir()
            script = workspace / "remote_tool.py"
            script.write_text("print('persistent-target-tool-ok')\n", encoding="utf-8")
            registry = ToolRegistry(
                workspace,
                load_config(Path("/missing/config.toml")).policy,
                auto_approve=True,
                interactive=False,
            )
            executor = FabricTargetToolExecutor(session, registry)
            # Consumer-declared observations are context, never authorization:
            # exact-target admission refuses until an operator asserts.
            refused = executor.execute(
                "persistent-worker",
                ["python", str(script)],
                source_root=workspace,
            )
            self.assertFalse(refused.execution.success)
            self.assertEqual(refused.fabric_result["disposition"], "UNKNOWN")
            from mncs_fabric.api import FabricAdminClient

            admin = FabricAdminClient.connect(
                self.root / "controller-admin.sock",
                client_identity="fixture-operator",
                timeout=5,
            )
            try:
                admin.assert_worker_capability(
                    "persistent-worker",
                    [{"kind": "runtime", "namespace": "system", "name": "python"}],
                    observation_source="fixture-operator-asserted-capability",
                )
            finally:
                admin.close()
            result = executor.execute(
                "persistent-worker",
                ["python", str(script)],
                source_root=workspace,
            )
            self.assertTrue(result.execution.success, result.execution.output)
            self.assertIn("persistent-target-tool-ok", result.execution.output)
            self.assertEqual(result.target.label, "fabric-worker:persistent-worker")
            self.assertIsNotNone(result.authorization_identity)
            self.assertEqual(result.fabric_result["disposition"], "EXECUTED")
            self.assertEqual(
                result.fabric_result["target_execution_evidence"]["worker_identity"],
                "persistent-worker",
            )
            self.assertEqual(
                result.fabric_result["target_execution_evidence"][
                    "consumer_authorization_identity"
                ],
                result.authorization_identity,
            )
            self.assertEqual(result.fabric_result["record"]["declared_argv"][0], "@python")
            self.assertEqual(result.fabric_result["record"]["declared_argv"][1], "remote_tool.py")
            self.assertNotIn(str(workspace), json.dumps(result.fabric_result))
            self.assertEqual(observation["worker_identity"], "persistent-worker")

            execution_count = len(worker.ledger.records(record_type="execution.record"))
            duplicate = executor.execute(
                "persistent-worker",
                ["python", str(script)],
                source_root=workspace,
            )
            self.assertTrue(duplicate.execution.success, duplicate.execution.output)
            self.assertEqual(
                duplicate.fabric_result["disposition"],
                "DUPLICATE_IDEMPOTENT",
            )
            self.assertEqual(
                len(worker.ledger.records(record_type="execution.record")),
                execution_count,
            )

            denied = executor.execute(
                "persistent-worker",
                ["rm", "remote_tool.py"],
                source_root=workspace,
            )
            self.assertFalse(denied.execution.success)
            self.assertIsNone(denied.fabric_result)
            self.assertTrue(script.exists())
            self.assertEqual(config.workers, ())
            self.assertIsNone(config.registry_path)
            session.close()
            session = None
            self.assertEqual(self.service.status()["service_runtime"], "RUNNING")
            self.assertEqual(self.service.status()["fleet"]["workers"][0]["availability"], "AVAILABLE")
        finally:
            if session is not None:
                session.close()
            ollama.shutdown()
            ollama_thread.join(timeout=3)
            ollama.server_close()
            worker_server.request_stop()
            worker_thread.join(timeout=5)

    def test_transitional_keeps_persistent_fleet_authority(self) -> None:
        config = FabricConfig(
            enabled=True,
            controller_mode="transitional",
            service_socket=self.socket,
            service_timeout_seconds=2.0,
            refresh_on_startup=False,
        )
        session = FabricSession(config)
        session.initialize()
        status = session.status()
        self.assertEqual(status.controller_state, "connected")
        self.assertEqual(status.fleet_state, "empty")
        self.assertEqual(status.execution_transport, "embedded-direct-compatibility")
        session.close()
        self.assertEqual(self.service.status()["service_runtime"], "RUNNING")

    def test_consumer_client_cannot_perform_admin_operation(self) -> None:
        from mncs_fabric.api import FabricClient
        from mncs_fabric.errors import ProtocolError

        client = FabricClient.connect(self.socket, client_identity="fixture-consumer", timeout=2)
        try:
            with self.assertRaisesRegex(ProtocolError, "FabricAdminClient"):
                client.create_enrollment_authorization()
        finally:
            client.close()


class PersistentFabricConfigTests(unittest.TestCase):
    def test_target_tool_argv_rejects_paths_outside_selected_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            bundle = workspace / "bundle"
            bundle.mkdir()
            script = bundle / "tool.py"
            script.write_text("print('ok')\n", encoding="utf-8")
            outside = workspace / "outside.py"
            outside.write_text("print('outside')\n", encoding="utf-8")
            registry = ToolRegistry(
                workspace,
                load_config(Path("/missing/config.toml")).policy,
                auto_approve=True,
                interactive=False,
            )
            executor = FabricTargetToolExecutor(FabricSession(FabricConfig()), registry)

            self.assertEqual(
                executor._remote_argv(["python", str(script)], bundle),
                ["@python", "tool.py"],
            )
            self.assertEqual(
                executor._remote_argv(["python", "tool.py"], bundle),
                ["@python", "tool.py"],
            )
            with self.assertRaisesRegex(ValueError, "parent traversal"):
                executor._remote_argv(["python", str(script), "../../secret"], bundle)
            with self.assertRaisesRegex(ValueError, "outside the selected"):
                executor._remote_argv(["python", str(outside)], bundle)
            with self.assertRaisesRegex(ValueError, "Windows paths"):
                executor._remote_argv(["python", str(script), r"C:\\Users\\secret"], bundle)
            for hostile in (
                r"C:\foo",
                "C:/foo",
                r"C:foo",
                r"\foo",
                r"\\server\share",
                r"\\?\C:\foo",
                r"\\.\pipe\fabric",
                r"folder\..\secret",
                r"folder/..\secret",
            ):
                with self.subTest(hostile=hostile):
                    with self.assertRaises(ValueError):
                        executor._remote_argv(["python", str(script), hostile], bundle)
            self.assertEqual(
                executor._remote_argv(
                    ["python", str(script), "model:name", "https://example.invalid/x"],
                    bundle,
                ),
                ["@python", "tool.py", "model:name", "https://example.invalid/x"],
            )

    def test_session_targets_split_inference_and_tool_workers(self) -> None:
        targets = SessionTargets.remote_inference_and_tools("worker-a", "worker-b")
        self.assertEqual(targets.inference.label, "fabric-worker:worker-a")
        self.assertEqual(targets.workspace.label, "controller")
        self.assertEqual(targets.tools.label, "fabric-worker:worker-b")

    def test_direct_and_toml_defaults_are_service_without_worker_ownership(self) -> None:
        direct = FabricConfig()
        loaded = load_config(Path("/definitely/not/a/real/config.toml")).fabric
        self.assertEqual(direct.controller_mode, "service")
        self.assertEqual(loaded.controller_mode, direct.controller_mode)
        self.assertEqual(direct.workers, ())
        self.assertIsNone(direct.registry_path)

    def test_bundled_config_defaults_to_service_without_worker_ownership(self) -> None:
        config = load_config(Path("/definitely/not/a/real/config.toml"))
        self.assertEqual(config.fabric.controller_mode, "service")
        self.assertEqual(config.fabric.workers, ())
        self.assertIsNone(config.fabric.registry_path)

    def test_service_config_rejects_legacy_worker_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                """
[fabric]
enabled = true
controller_mode = "service"
registry_path = "workers.json"
""".strip(),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "controller_mode=service"):
                load_config(path)

    def test_direct_service_config_rejects_legacy_worker_ownership(self) -> None:
        with self.assertRaisesRegex(ValueError, "controller_mode=service"):
            FabricConfig(
                controller_mode="service",
                registry_path=Path("workers.json"),
                workers=(
                    FabricWorkerConfig(
                        worker_id="legacy",
                        kind="remote",
                        state_path=Path("worker.jsonl"),
                    ),
                ),
            )


if __name__ == "__main__":
    unittest.main()
