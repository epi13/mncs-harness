from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from epi13_local_harness.config import load_config
from epi13_local_harness.policy import CommandPolicy, WorkspaceGuard


class PolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.config = load_config(Path("/missing/config.toml")).policy
        self.guard = WorkspaceGuard(self.workspace)
        self.policy = CommandPolicy(self.config, self.guard)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_workspace_escape_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.guard.resolve("../outside.txt")

    def test_hidden_path_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.guard.resolve(".git/config")

    def test_git_status_is_allowed_but_needs_approval(self) -> None:
        argv, decision = self.policy.evaluate(["git", "status", "--short"])
        self.assertEqual(argv[0], "git")
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.requires_approval)

    def test_sudo_is_blocked(self) -> None:
        _, decision = self.policy.evaluate(["sudo", "dnf", "install", "x"])
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.risk, "blocked")

    def test_shell_command_string_is_blocked(self) -> None:
        _, decision = self.policy.evaluate(["bash", "-c", "echo unsafe"])
        self.assertFalse(decision.allowed)

    def test_python_unapproved_module_is_blocked(self) -> None:
        _, decision = self.policy.evaluate(["python", "-m", "http.server"])
        self.assertFalse(decision.allowed)


if __name__ == "__main__":
    unittest.main()
