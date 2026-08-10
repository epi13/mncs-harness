from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from epi13_local_harness import fabric_controller_light
from epi13_local_harness.config import initialize_config, load_config


class ControllerLightTests(unittest.TestCase):
    def test_profile_routes_generation_roles_to_fabric_and_disables_local_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            initialize_config(config_path)
            with contextlib.redirect_stdout(io.StringIO()):
                result = fabric_controller_light._apply_controller_light(config_path)
            self.assertEqual(result, 0)
            config = load_config(config_path)
            self.assertTrue(config.fabric.enabled)
            self.assertFalse(config.fabric.fallback_to_local)
            self.assertTrue(config.router.enable_semantic_routing)
            self.assertEqual(config.router.device, "cpu")
            for model in config.models.values():
                self.assertEqual(model.provider, "fabric")
                self.assertEqual(model.execution_device, "cpu")
                self.assertIsNone(model.accelerator_backend)

    def test_profile_removes_stale_accelerator_backend_from_existing_user_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            initialize_config(config_path)
            text = config_path.read_text(encoding="utf-8")
            text = fabric_controller_light._profile.upsert_toml_section(
                text,
                "models.coder",
                {
                    "provider": "fabric",
                    "execution_device": "accelerator",
                    "accelerator_backend": "cuda",
                },
            )
            config_path.write_text(text, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                fabric_controller_light._apply_controller_light(config_path)
            config = load_config(config_path)
            self.assertEqual(config.models["coder"].execution_device, "cpu")
            self.assertIsNone(config.models["coder"].accelerator_backend)

    def test_outer_wrapper_preserves_non_target_commands(self) -> None:
        with patch.object(fabric_controller_light._base, "main", return_value=9) as base:
            result = fabric_controller_light.main(["scan-models-windows", "--help"])
        self.assertEqual(result, 9)
        base.assert_called_once_with(["scan-models-windows", "--help"])


if __name__ == "__main__":
    unittest.main()
