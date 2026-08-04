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
        self.assertEqual(config.router.backend, "transformers")
        self.assertEqual(config.router.revision, PINNED_REVISION)
        self.assertFalse(config.router.enable_semantic_routing)

    def test_initialize_config_refuses_overwrite_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "config.toml"
            initialize_config(destination)
            with self.assertRaises(FileExistsError):
                initialize_config(destination)
            initialize_config(destination, force=True)
            text = destination.read_text(encoding="utf-8")
            self.assertIn("[models.e2b]", text)
            self.assertIn(PINNED_REVISION, text)

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


if __name__ == "__main__":
    unittest.main()
