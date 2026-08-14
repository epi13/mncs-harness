from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from epi13_local_harness.cli import _bounded_probe
from epi13_local_harness.config import load_config
from epi13_local_harness.fleet import FleetService


class DoctorProbeTests(unittest.TestCase):
    def test_bounded_probe_returns_timeout_instead_of_hanging(self) -> None:
        started = time.monotonic()
        result = _bounded_probe("Ollama", lambda: time.sleep(5), timeout_seconds=0.2)
        elapsed = time.monotonic() - started
        self.assertEqual(result["status"], "TIMEOUT")
        self.assertLess(elapsed, 2.0)
        self.assertIn("Ollama", result["detail"])

    def test_bounded_probe_reports_subsystem_errors(self) -> None:
        result = _bounded_probe("Commons", lambda: (_ for _ in ()).throw(RuntimeError("boom")), timeout_seconds=1)
        self.assertEqual(result["status"], "ERROR")
        self.assertIn("boom", result["detail"])

    def test_bounded_probe_passes(self) -> None:
        result = _bounded_probe("Fabric", lambda: {"state": "available"}, timeout_seconds=1)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["detail"]["state"], "available")

    def test_role_availability_uses_persistent_inventory(self) -> None:
        config = load_config(Path("/missing/config.toml"))
        models = {
            name: replace(model, provider="fabric")
            for name, model in config.models.items()
        }
        config = replace(config, models=models, fabric=replace(config.fabric, enabled=True))

        class Session:
            def resolve_model(self, role: str, model: object) -> tuple[object, object]:
                return model, SimpleNamespace(
                    selected_model=getattr(model, "name"),
                    worker_id="fabric-worker-01",
                    available=True,
                    reason="persistent inventory",
                )

        fleet = FleetService(config, Session())
        snapshot = {
            "controller": {"installed_models": []},
            "fabric": {"workers": []},
        }
        rows = fleet.role_availability(snapshot)
        self.assertGreaterEqual(len(rows), 1)
        self.assertTrue(all(row["available"] for row in rows))
        self.assertTrue(all(row["worker"] == "fabric-worker-01" for row in rows))

    def test_doctor_json_stdout_is_pure_json(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "epi13_local_harness.cli",
                "--config",
                "/missing/elh-doctor-regression.toml",
                "doctor",
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
        self.assertNotIn("elh doctor:", completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertIn("role_availability", payload)
        self.assertIn("subsystems", payload)
        self.assertTrue(completed.stderr.startswith("elh doctor:"))
