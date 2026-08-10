from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from epi13_local_harness import fabric_profile
from epi13_local_harness.fabric_compat import require_execution_bundle_archive_api
from epi13_local_harness.fabric_models import (
    _configured_models,
    _render_installer,
    _validate_models,
)


class FabricCompatibilityTests(unittest.TestCase):
    def _module(self, client: type[object], version: str) -> types.ModuleType:
        module = types.ModuleType("mncs_fabric")
        module.__file__ = "/tmp/fake-mncs-fabric/__init__.py"
        module.__version__ = version
        module.FabricClient = client
        return module

    def test_guard_accepts_execution_bundle_archive_api(self) -> None:
        class Client:
            def execute(self, plan: object, manifest: object, *, execution_bundle_archive=None):
                return []

        with patch.dict(sys.modules, {"mncs_fabric": self._module(Client, "0.2.0a10")}):
            result = require_execution_bundle_archive_api()
        self.assertEqual(result["version"], "0.2.0a10")
        self.assertEqual(result["required_version"], "0.2.0a10")

    def test_guard_rejects_a9_even_though_signature_exists(self) -> None:
        class Client:
            def execute(self, plan: object, manifest: object, *, execution_bundle_archive=None):
                return []

        with patch.dict(sys.modules, {"mncs_fabric": self._module(Client, "0.2.0a9")}):
            with self.assertRaisesRegex(RuntimeError, "0.2.0a9.*0.2.0a10"):
                require_execution_bundle_archive_api()

    def test_guard_rejects_stale_editable_fabric(self) -> None:
        class Client:
            def execute(self, plan: object, manifest: object):
                return []

        with patch.dict(sys.modules, {"mncs_fabric": self._module(Client, "0.2.0a7")}):
            with self.assertRaisesRegex(RuntimeError, "0.2.0a7.*0.2.0a10"):
                require_execution_bundle_archive_api()


class WorkerLocalModelTests(unittest.TestCase):
    def test_default_model_set_is_accelerator_roles_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            models = _configured_models(Path(directory) / "missing.toml")
        self.assertEqual(models, ("gemma4:e4b", "qwen3:8b", "gemma4:12b"))
        self.assertNotIn("gemma4:e2b", models)

    def test_model_tags_are_deduplicated_and_shell_metacharacters_rejected(self) -> None:
        self.assertEqual(
            _validate_models(["qwen3:8b", "qwen3:8b", "registry.example/a:b"]),
            ("qwen3:8b", "registry.example/a:b"),
        )
        with self.assertRaises(ValueError):
            _validate_models(["qwen3:8b & del C:\\data"])

    def test_installer_pulls_on_worker(self) -> None:
        script = _render_installer(("gemma4:e4b", "qwen3:8b", "gemma4:12b"))
        self.assertIn("ollama pull gemma4:e4b", script)
        self.assertIn("ollama pull qwen3:8b", script)
        self.assertIn("ollama pull gemma4:12b", script)
        self.assertIn("Model blobs will be downloaded by this worker", script)
        self.assertNotIn("scp", script.casefold())

    def test_cli_exposes_worker_local_installer(self) -> None:
        parser = fabric_profile.build_parser()
        args = parser.parse_args(
            [
                "install-models-windows",
                "--ssh-host",
                "192.168.1.78",
                "--ssh-user",
                "epicu",
                "--ssh-key",
                "/tmp/key",
                "--expected-hostname",
                "Collamore02",
                "--stage-only",
            ]
        )
        self.assertEqual(args.command, "install-models-windows")
        self.assertTrue(args.stage_only)


if __name__ == "__main__":
    unittest.main()
