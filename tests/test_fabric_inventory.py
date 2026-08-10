from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from epi13_local_harness import fabric_inventory, fabric_profile_inventory


class FabricInventoryTests(unittest.TestCase):
    def test_normalize_reports_every_distinct_installed_model(self) -> None:
        models = fabric_inventory.normalize_ollama_models(
            {
                "models": [
                    {
                        "name": "gemma4:e4b",
                        "model": "gemma4:e4b",
                        "size": 123,
                        "digest": "sha256:a",
                        "modified_at": "2026-08-10T00:00:00Z",
                        "details": {
                            "format": "gguf",
                            "family": "gemma",
                            "families": ["gemma"],
                            "parameter_size": "4B",
                            "quantization_level": "Q4_K_M",
                        },
                    },
                    {"name": "custom/coder:latest", "size": 456},
                    {"name": "gemma4:e4b", "size": 789},
                    {"not_a_model": True},
                ]
            }
        )
        self.assertEqual([item["name"] for item in models], ["custom/coder:latest", "gemma4:e4b"])
        self.assertEqual(models[1]["size"], 789)
        self.assertEqual(models[1]["details"]["family"], None)

    def test_scan_uses_worker_local_api_not_ollama_executable_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "worker-key"
            key.write_text("fixture", encoding="utf-8")
            with (
                patch.object(fabric_inventory._commission, "_run_powershell") as run,
                patch.object(
                    fabric_inventory._commission,
                    "_powershell_json",
                    return_value={
                        "hostname": "COLLAMORE02",
                        "ollama_url": "http://127.0.0.1:11434",
                        "models": [{"name": "arbitrary-model:7b", "size": 42}],
                    },
                ),
            ):
                run.return_value = object()
                inventory = fabric_inventory._scan_windows(
                    host="192.168.1.78",
                    user="epicu",
                    key=key,
                    expected_hostname="Collamore02",
                    timeout_seconds=15,
                )
            script = run.call_args.kwargs["script"]
            self.assertIn("http://127.0.0.1:11434/api/tags", script)
            self.assertNotIn("Get-Command ollama", script)
            self.assertEqual(inventory["model_names"], ["arbitrary-model:7b"])
            self.assertEqual(inventory["model_count"], 1)

    def test_hostname_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "worker-key"
            key.write_text("fixture", encoding="utf-8")
            with (
                patch.object(fabric_inventory._commission, "_run_powershell", return_value=object()),
                patch.object(
                    fabric_inventory._commission,
                    "_powershell_json",
                    return_value={"hostname": "WRONGHOST", "models": []},
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "hostname mismatch"):
                    fabric_inventory._scan_windows(
                        host="192.168.1.78",
                        user="epicu",
                        key=key,
                        expected_hostname="Collamore02",
                        timeout_seconds=15,
                    )

    def test_profile_wrapper_preserves_existing_commands(self) -> None:
        with patch.object(fabric_profile_inventory._base, "main", return_value=7) as base:
            result = fabric_profile_inventory.main(["show"])
        self.assertEqual(result, 7)
        base.assert_called_once_with(["show"])

    def test_scan_command_is_exposed_through_elh_fabric(self) -> None:
        with patch.object(fabric_profile_inventory, "scan_models_windows", return_value=0):
            parser = fabric_profile_inventory._scan_parser()
            args = parser.parse_args(
                [
                    "scan-models-windows",
                    "--ssh-host",
                    "192.168.1.78",
                    "--ssh-user",
                    "epicu",
                    "--ssh-key",
                    "/tmp/key",
                    "--expected-hostname",
                    "Collamore02",
                ]
            )
        self.assertEqual(args.command, "scan-models-windows")
        self.assertIsInstance(args, argparse.Namespace)


if __name__ == "__main__":
    unittest.main()
