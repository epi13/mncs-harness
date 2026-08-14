from __future__ import annotations

import time
import unittest

from epi13_local_harness.cli import _bounded_probe


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
