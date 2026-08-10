from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

from epi13_local_harness.config import initialize_config, load_config
from epi13_local_harness.fabric_profile import configure_remote, upsert_toml_section


class FabricProfileTests(unittest.TestCase):
    def test_upsert_toml_section_replaces_and_adds_without_duplicate_table(self) -> None:
        source = "[fabric]\nenabled = false\n\n[metrics]\nstore_prompt_text = false\n"
        updated = upsert_toml_section(
            source,
            "fabric",
            {"enabled": True, "fallback_to_local": True},
        )
        self.assertEqual(updated.count("[fabric]"), 1)
        self.assertIn("enabled = true", updated)
        self.assertIn("fallback_to_local = true", updated)
        self.assertIn("[metrics]", updated)

    def test_configure_remote_enables_fabric_and_gpu_roles_in_normal_user_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            initialize_config(config_path)
            trust_paths = {}
            for name in ("ca.pem", "controller.pem", "controller.key", "trust.jsonl"):
                path = root / name
                path.write_text("fixture", encoding="utf-8")
                trust_paths[name] = path
            args = argparse.Namespace(
                config=config_path,
                worker_id="gpu-worker",
                host="192.0.2.10",
                port=7443,
                ca_file=trust_paths["ca.pem"],
                client_certificate=trust_paths["controller.pem"],
                client_key=trust_paths["controller.key"],
                trust_state=trust_paths["trust.jsonl"],
                capability=None,
                accelerator_role=None,
                local_role=None,
                gpu_reserve_mib=512,
                fallback_to_local=True,
            )
            self.assertEqual(configure_remote(args), 0)
            self.assertTrue((root / "config.toml.pre-fabric").is_file())
            config = load_config(config_path)
            self.assertTrue(config.fabric.enabled)
            self.assertTrue(config.fabric.fallback_to_local)
            self.assertEqual(config.fabric.workers[0].worker_id, "gpu-worker")
            self.assertEqual(config.fabric.workers[0].host, "192.0.2.10")
            self.assertEqual(config.fabric.workers[0].port, 7443)
            self.assertEqual(config.models["e2b"].provider, "ollama")
            for role in ("e4b", "coder", "reviewer"):
                model = config.models[role]
                self.assertEqual(model.provider, "fabric")
                self.assertEqual(model.execution_device, "accelerator")
                self.assertEqual(model.accelerator_backend, "cuda")
                self.assertEqual(model.offload, "auto")
                self.assertEqual(model.gpu_reserve_bytes, 512 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
