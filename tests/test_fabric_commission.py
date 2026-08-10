from __future__ import annotations

import base64
import unittest
from pathlib import Path

from epi13_local_harness.fabric_commission import (
    _encoded_powershell,
    _managed_stop_script,
    _scp_base,
    _ssh_base,
    _windows_scp_path,
)


class FabricCommissionTests(unittest.TestCase):
    def test_ssh_bootstrap_is_public_key_only_and_strict(self) -> None:
        args = _ssh_base("192.0.2.10", "operator", Path("/tmp/key"))
        joined = " ".join(args)
        self.assertIn("IdentitiesOnly=yes", joined)
        self.assertIn("PreferredAuthentications=publickey", joined)
        self.assertIn("PasswordAuthentication=no", joined)
        self.assertIn("KbdInteractiveAuthentication=no", joined)
        self.assertIn("BatchMode=yes", joined)
        self.assertIn("StrictHostKeyChecking=yes", joined)
        self.assertIn("operator@192.0.2.10", joined)

    def test_scp_bootstrap_has_the_same_authentication_boundary(self) -> None:
        joined = " ".join(_scp_base(Path("/tmp/key")))
        self.assertIn("PasswordAuthentication=no", joined)
        self.assertIn("BatchMode=yes", joined)
        self.assertIn("StrictHostKeyChecking=yes", joined)

    def test_windows_scp_path_matches_windows_openssh_form(self) -> None:
        self.assertEqual(
            _windows_scp_path("C:/Users/operator/mncs-fabric-worker"),
            "/Users/operator/mncs-fabric-worker",
        )
        self.assertEqual(
            _windows_scp_path(r"C:\Users\operator\mncs-fabric-worker"),
            "/Users/operator/mncs-fabric-worker",
        )

    def test_powershell_encoding_round_trips_utf16le(self) -> None:
        source = "$ProgressPreference='SilentlyContinue'; Write-Output 'ok'"
        decoded = base64.b64decode(_encoded_powershell(source)).decode("utf-16le")
        self.assertEqual(decoded, source)

    def test_stop_script_removes_stale_launcher_when_pid_is_gone(self) -> None:
        script = _managed_stop_script(
            "C:/Users/operator/mncs-fabric-worker",
            "gpu-worker",
            "fixture-controller",
        )
        self.assertIn("STALE_STATE_REMOVED", script)
        self.assertIn("Remove-Item -Force $state", script)
        self.assertIn("if(!$proc)", script)

    def test_stop_script_migrates_legacy_state_only_after_process_identity_check(self) -> None:
        script = _managed_stop_script(
            "C:/Users/operator/mncs-fabric-worker",
            "gpu-worker",
            "fixture-controller",
        )
        self.assertIn("Get-CimInstance Win32_Process", script)
        self.assertIn("-m mncs_fabric worker serve", script)
        self.assertIn("--worker-id", script)
        self.assertIn("gpu-worker", script)
        self.assertIn("--controller-id", script)
        self.assertIn("fixture-controller", script)
        self.assertIn("--bundle-root", script)
        self.assertIn("legacy launcher PID is live but does not match", script)
        self.assertIn("LEGACY_WORKER_STOPPED", script)

    def test_stop_script_preserves_process_token_guard_for_current_state(self) -> None:
        script = _managed_stop_script(
            "C:/Users/operator/mncs-fabric-worker",
            "gpu-worker",
            "fixture-controller",
        )
        self.assertIn("process_token", script)
        self.assertIn("recorded worker PID was reused", script)
        self.assertIn("refusing to stop an unrelated process", script)


if __name__ == "__main__":
    unittest.main()
