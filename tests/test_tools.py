from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from epi13_local_harness.config import load_config
from epi13_local_harness.tools import ToolRegistry


class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        policy = load_config(Path("/missing/config.toml")).policy
        self.registry = ToolRegistry(
            self.workspace,
            policy,
            auto_approve=True,
            interactive=False,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_write_and_read_workspace_file(self) -> None:
        written = self.registry.execute(
            "write_file", {"path": "example.txt", "content": "alpha\nbeta\n"}
        )
        self.assertTrue(written.success)
        read = self.registry.execute("read_file", {"path": "example.txt"})
        self.assertTrue(read.success)
        self.assertIn("1: alpha", read.output)
        self.assertIn(self.workspace / "example.txt", self.registry.modified_paths)

    def test_write_escape_is_blocked(self) -> None:
        result = self.registry.execute(
            "write_file", {"path": "../outside.txt", "content": "no"}
        )
        self.assertFalse(result.success)

    def test_unknown_tool_is_blocked(self) -> None:
        result = self.registry.execute("unrestricted_shell", {})
        self.assertFalse(result.success)
        self.assertEqual(result.decision.risk, "blocked")

    def test_safe_command_runs_without_shell(self) -> None:
        result = self.registry.execute(
            "run_command", {"argv": ["python", "-m", "compileall", "."]}
        )
        self.assertTrue(result.success)
        self.assertIn("exit_code=0", result.output)


if __name__ == "__main__":
    unittest.main()
