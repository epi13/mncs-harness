from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from epi13_local_harness.config import initialize_config, load_config


class ConfigTests(unittest.TestCase):
    def test_bundled_config_has_required_roles(self) -> None:
        config = load_config(Path("/definitely/not/a/real/config.toml"))
        self.assertTrue({"e2b", "e4b", "coder", "reviewer"}.issubset(config.models))
        self.assertEqual(config.models["e2b"].name, "gemma4:e2b")

    def test_initialize_config_refuses_overwrite_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "config.toml"
            initialize_config(destination)
            with self.assertRaises(FileExistsError):
                initialize_config(destination)
            initialize_config(destination, force=True)
            self.assertIn("[models.e2b]", destination.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
