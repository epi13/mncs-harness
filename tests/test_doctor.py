from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from epi13_local_harness.cli import _bounded_probe, doctor_outcome
from epi13_local_harness.config import load_config
from epi13_local_harness.fleet import FleetService
from epi13_local_harness.models import resolve_execution_profile


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
            def __init__(self) -> None:
                self.status_calls = 0

            def status(self) -> object:
                self.status_calls += 1
                raise AssertionError("role availability must not re-read Fabric status")

            def resolve_model_from_status(
                self, status: object, role: str, model: object, routing_override=None
            ) -> tuple[object, object]:
                del status, role, routing_override
                return model, SimpleNamespace(
                    selected_model=getattr(model, "name"),
                    worker_id="fabric-worker-01",
                    available=True,
                    reason="persistent inventory",
                )

        session = Session()
        fleet = FleetService(config, session)
        snapshot = {
            "controller": {"installed_models": []},
            "fabric": {"workers": []},
        }
        rows = fleet.role_availability(snapshot, status=object())
        self.assertEqual(session.status_calls, 0)
        self.assertGreaterEqual(len(rows), 1)
        self.assertTrue(all(row["available"] for row in rows))
        self.assertTrue(all(row["worker"] == "fabric-worker-01" for row in rows))

    def test_doctor_outcome_is_degraded_when_a_configured_role_is_unusable(self) -> None:
        subsystems = [
            {"name": "Commons", "status": "PASS"},
            {"name": "Fabric", "status": "PASS"},
            {"name": "Ollama", "status": "PASS"},
            {"name": "Worker fabric-worker-01", "status": "PASS"},
        ]
        roles = [
            {"role": "e2b", "provider": "fabric", "available": True},
            {"role": "e4b", "provider": "fabric", "available": False, "reason": "STALE"},
        ]
        self.assertEqual(doctor_outcome(subsystems, roles), "DEGRADED")
        self.assertEqual(
            doctor_outcome(subsystems, [{"role": "e2b", "provider": "fabric", "available": True}]),
            "PASS",
        )
        self.assertEqual(
            doctor_outcome([{"name": "Fabric", "status": "ERROR"}], roles),
            "ERROR",
        )

    def test_unused_local_ollama_failure_does_not_fail_fabric_backed_doctor(self) -> None:
        subsystems = [
            {"name": "Commons", "status": "PASS"},
            {"name": "Fabric", "status": "PASS"},
            {"name": "Ollama", "status": "ERROR", "required": False},
            {"name": "Worker fabric-worker-01", "status": "PASS"},
        ]
        roles = [{"role": "e2b", "provider": "fabric", "available": True}]
        self.assertEqual(doctor_outcome(subsystems, roles), "PASS")
        self.assertEqual(
            doctor_outcome(
                [
                    {"name": "Commons", "status": "PASS"},
                    {"name": "Fabric", "status": "PASS"},
                    {"name": "Ollama", "status": "ERROR", "required": True},
                ],
                roles,
            ),
            "ERROR",
        )
        self.assertEqual(
            doctor_outcome(
                [
                    {"name": "Commons", "status": "PASS"},
                    {"name": "Fabric", "status": "ERROR", "detail": "timed out"},
                    {"name": "Ollama", "status": "PASS"},
                ],
                roles,
            ),
            "ERROR",
        )

    def test_submit_profile_uses_matching_role_not_e2b(self) -> None:
        config = load_config(None)
        role, profile = resolve_execution_profile(config.models, model_name="gemma4:e4b")
        self.assertEqual(role, "e4b")
        self.assertEqual(profile.num_ctx, config.models["e4b"].num_ctx)
        self.assertEqual(profile.think, config.models["e4b"].think)
        unmanaged_role, unmanaged = resolve_execution_profile(
            config.models, model_name="unknown:tag"
        )
        self.assertEqual(unmanaged_role, "unmanaged")
        self.assertEqual(unmanaged.num_ctx, 8192)
        self.assertFalse(unmanaged.think)

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
