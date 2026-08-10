from __future__ import annotations

import base64
import unittest
from pathlib import Path

from epi13_local_harness.fabric_commission import (
    _encoded_powershell,
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


if __name__ == "__main__":
    unittest.main()
