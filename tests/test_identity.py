from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from epi13_local_harness.config import (
    APP_NAME,
    DEFAULT_CONTROLLER_ID,
    LEGACY_APP_NAME,
    default_config_path,
    preferred_config_path,
)
from epi13_local_harness.portable_cli import WRAPPERS


class IdentityTests(unittest.TestCase):
    def test_canonical_project_identity(self) -> None:
        import mncs_harness

        self.assertEqual(APP_NAME, "mncs-harness")
        self.assertEqual(DEFAULT_CONTROLLER_ID, "mncs-harness")
        self.assertEqual(LEGACY_APP_NAME, "epi13-local-harness")
        self.assertEqual(mncs_harness.PROJECT_ID, "mncs-harness")
        self.assertEqual(mncs_harness.PROJECT_NAME, "MNCS Harness")

    def test_preferred_cli_wrappers_include_canonical_and_legacy_names(self) -> None:
        self.assertEqual(WRAPPERS["mncs-harness"], "epi13_local_harness.cli")
        self.assertEqual(WRAPPERS["elh"], "epi13_local_harness.cli")
        self.assertEqual(WRAPPERS["epi13-harness"], "epi13_local_harness.cli")

    def test_environment_overrides_prefer_canonical_then_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            canonical = Path(directory) / "canonical.toml"
            legacy = Path(directory) / "legacy.toml"
            env = {
                "MNCS_HARNESS_CONFIG": str(canonical),
                "EPI13_HARNESS_CONFIG": str(legacy),
            }
            with patch.dict(os.environ, env, clear=False):
                self.assertEqual(default_config_path(), canonical)
                self.assertEqual(preferred_config_path(), canonical)

    def test_legacy_config_is_used_when_canonical_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            canonical = home / ".config" / APP_NAME / "config.toml"
            legacy = home / ".config" / LEGACY_APP_NAME / "config.toml"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("[ollama]\n", encoding="utf-8")
            env = {
                key: value
                for key, value in os.environ.items()
                if key not in {"MNCS_HARNESS_CONFIG", "EPI13_HARNESS_CONFIG"}
            }
            with patch.dict(os.environ, env, clear=True), patch(
                "epi13_local_harness.config.Path.home", return_value=home
            ):
                self.assertEqual(default_config_path(), legacy)
                self.assertEqual(preferred_config_path(), canonical)
                self.assertFalse(canonical.exists())
