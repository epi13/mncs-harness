from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

from epi13_local_harness.portable_cli import WRAPPERS, install_portable_cli, wrapper_text


class PortableCliTests(unittest.TestCase):
    def test_posix_wrapper_uses_sibling_python(self) -> None:
        text = wrapper_text("epi13_local_harness.cli")
        self.assertTrue(text.startswith("#!/bin/sh"))
        self.assertIn('exec "$dir/python" -m epi13_local_harness.cli', text)
        self.assertNotIn("/home/", text)
        self.assertNotIn("Documents/Projects", text)

    def test_install_writes_executable_relocatable_wrappers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            written = install_portable_cli(Path(directory))
            names = {path.name for path in written}
            self.assertTrue(
                {
                    "mncs-harness",
                    "mncs-harness-tui",
                    "mncs-harness-fabric",
                    "elh",
                    "elh-tui",
                    "elh-fabric",
                    "epi13-harness",
                }
                <= names
            )
            for path in written:
                mode = path.stat().st_mode
                self.assertTrue(mode & stat.S_IXUSR)
                text = path.read_text(encoding="utf-8")
                self.assertIn('exec "$dir/python" -m ' + WRAPPERS[path.name], text)
                self.assertNotRegex(text, r"#!/home/")


class RepoLauncherTests(unittest.TestCase):
    def test_checked_in_scripts_elh_is_relocatable(self) -> None:
        launcher = Path(__file__).resolve().parents[1] / "scripts" / "elh"
        text = launcher.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/bin/sh"))
        self.assertIn('.venv/bin/python', text)
        self.assertIn("epi13_local_harness.cli", text)
        self.assertNotIn("/home/epi13/", text)

    def test_checked_in_scripts_mncs_harness_is_the_canonical_launcher(self) -> None:
        root = Path(__file__).resolve().parents[1]
        canonical = (root / "scripts" / "mncs-harness").read_text(encoding="utf-8")
        compatibility = (root / "scripts" / "elh").read_text(encoding="utf-8")
        self.assertEqual(canonical, compatibility)
        self.assertIn("epi13_local_harness.cli", canonical)
