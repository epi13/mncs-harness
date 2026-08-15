from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from epi13_local_harness.config import initialize_config, load_config

PINNED_REVISION = "35ca4a0469f180f1cf05a630df8842fa17ac18e3"


class ConfigTests(unittest.TestCase):
    def test_bundled_config_has_required_roles(self) -> None:
        config = load_config(Path("/definitely/not/a/real/config.toml"))
        self.assertTrue({"e2b", "e4b", "coder", "reviewer"}.issubset(config.models))
        self.assertEqual(config.models["e2b"].name, "gemma4:e2b")
        self.assertEqual(config.router.backend, "deterministic")
        self.assertEqual(config.router.revision, "")
        self.assertFalse(config.router.enable_semantic_routing)
        self.assertEqual(config.router.model, "")

    def test_initialize_config_refuses_overwrite_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "config.toml"
            initialize_config(destination)
            with self.assertRaises(FileExistsError):
                initialize_config(destination)
            initialize_config(destination, force=True)
            text = destination.read_text(encoding="utf-8")
            self.assertIn("[models.e2b]", text)
            self.assertIn("enable_semantic_routing = false", text)
            self.assertNotIn("LiquidAI/LFM2.5-Encoder-350M-Prompt-Router", text)

    def test_lane_config_is_loaded_from_toml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "config.toml"
            content = """
[router]
mode = "hybrid"
backend = "transformers"
model = "LiquidAI/LFM2.5-Encoder-350M-Prompt-Router"
revision = "__REVISION__"
minimum_score = 0.60
minimum_margin = 0.12

[lanes.chat]
description = "Simple conversation"
worker_role = "e2b"
enabled = true
requires_image = false

[lanes.coding]
description = "Code work"
worker_role = "coder"
enabled = true
requires_image = false
""".strip().replace("__REVISION__", PINNED_REVISION)
            destination.write_text(content, encoding="utf-8")
            config = load_config(destination)
            self.assertEqual(config.router.mode, "hybrid")
            self.assertIn("chat", config.lanes)
            self.assertIn("coding", config.lanes)
            self.assertEqual(config.lanes["chat"].worker_role, "e2b")

    def test_fabric_configuration_is_optional_and_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "config.toml"
            destination.write_text(
                """
[fabric]
enabled = true
controller_mode = "embedded"
fallback_to_local = false
refresh_on_startup = false
state_path = "state/fabric.jsonl"

[fabric.workers.local-fixture]
kind = "local"
state_path = "state/worker.jsonl"
bundle_root = "state/bundle"
capabilities = ["python", "placement:cpu-precision:float16"]
""".strip(),
                encoding="utf-8",
            )
            config = load_config(destination)
            self.assertTrue(config.fabric.enabled)
            self.assertFalse(config.fabric.fallback_to_local)
            self.assertEqual(config.fabric.workers[0].worker_id, "local-fixture")
            self.assertEqual(config.fabric.workers[0].kind, "local")
            self.assertIn("fabric", config.models["e2b"].provider)

    def test_malformed_fabric_worker_kind_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "config.toml"
            destination.write_text(
                """
[fabric.workers.bad]
kind = "ssh"
""".strip(),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "local or remote"):
                load_config(destination)

    def test_defaults_contain_no_fabric_credentials(self) -> None:
        text = Path("src/epi13_local_harness/default_config.toml").read_text(encoding="utf-8")
        self.assertNotIn("client_key", text)
        self.assertNotIn("password", text.lower())
        self.assertNotIn("token", text.lower())


if __name__ == "__main__":
    unittest.main()
