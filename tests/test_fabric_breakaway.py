from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from epi13_local_harness import fabric_profile_breakaway
from epi13_local_harness.fabric_commission_breakaway import (
    _launcher_source,
    _launcher_start_script,
)
from epi13_local_harness import windows_worker_launcher


class FabricBreakawayCommissionTests(unittest.TestCase):
    def test_start_script_uses_staged_native_launcher_not_start_process(self) -> None:
        script = _launcher_start_script(
            remote_root="C:/Users/operator/mncs-fabric-worker",
            python="C:/gpu/python.exe",
            worker_id="windows-gpu",
            controller_id="local-harness",
            port=7443,
        )
        self.assertIn("windows_worker_launcher.py", script)
        self.assertIn("--worker-id", script)
        self.assertIn("windows-gpu", script)
        self.assertIn("--controller-id", script)
        self.assertIn("local-harness", script)
        self.assertIn("--bundle-root", script)
        self.assertIn("--max-requests", script)
        self.assertIn("1000000", script)
        self.assertNotIn("Start-Process", script)

    def test_launcher_is_packaged_next_to_commissioning_code(self) -> None:
        source = _launcher_source()
        self.assertTrue(source.is_file())
        self.assertEqual(source.name, "windows_worker_launcher.py")

    def test_creation_flags_require_detach_group_and_breakaway(self) -> None:
        with (
            patch.object(windows_worker_launcher.os, "name", "nt"),
            patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x1, create=True),
            patch.object(subprocess, "DETACHED_PROCESS", 0x2, create=True),
            patch.object(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x4, create=True),
        ):
            self.assertEqual(windows_worker_launcher._creation_flags(), 0x7)

    def test_creation_flags_fail_closed_without_breakaway(self) -> None:
        with (
            patch.object(windows_worker_launcher.os, "name", "nt"),
            patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x1, create=True),
            patch.object(subprocess, "DETACHED_PROCESS", 0x2, create=True),
            patch.object(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0, create=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "CREATE_BREAKAWAY_FROM_JOB"):
                windows_worker_launcher._creation_flags()

    def test_cli_shim_preserves_non_commission_commands(self) -> None:
        parser = fabric_profile_breakaway._base.build_parser()
        args = parser.parse_args(["show"])
        self.assertEqual(args.command, "show")
        self.assertIs(args.func, fabric_profile_breakaway._base.show_fabric)


if __name__ == "__main__":
    unittest.main()
