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
                self.assertEqual(model.execution_device, "accelerator")
                self.assertEqual(model.accelerator_backend, "cuda")

    def test_outer_wrapper_preserves_non_target_commands(self) -> None:
        with patch.object(fabric_controller_light._base, "main", return_value=9) as base:
            result = fabric_controller_light.main(["scan-models-windows", "--help"])
        self.assertEqual(result, 9)
        base.assert_called_once_with(["scan-models-windows", "--help"])


if __name__ == "__main__":
    unittest.main()
