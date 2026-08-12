from __future__ import annotations

import importlib.util
import tempfile
import threading
import time
import unittest
from pathlib import Path

from epi13_local_harness.config import load_config
from epi13_local_harness.fabric import FabricExecutionError, FabricSession
from epi13_local_harness.models import FabricConfig


@unittest.skipUnless(importlib.util.find_spec("mncs_fabric"), "mncs-fabric is not installed")
class PersistentFabricTests(unittest.TestCase):
    def setUp(self) -> None:
        from mncs_fabric.controller_service import ControllerConfig, ControllerService

        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.socket = root / "controller.sock"
        self.service = ControllerService(
            ControllerConfig(
                "persistent-fixture",
                root / "lifecycle.jsonl",
                service_log=root / "controller-service.jsonl",
                socket_path=self.socket,
                admin_socket_path=root / "controller-admin.sock",
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


if __name__ == "__main__":
    unittest.main()
