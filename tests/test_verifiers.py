from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from epi13_local_harness.config import load_config
from epi13_local_harness.verifiers import Verifier


class VerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        config = load_config(Path("/missing/config.toml")).verification
        self.verifier = Verifier(self.workspace, config)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_valid_python_and_toml_pass(self) -> None:
        python_file = self.workspace / "valid.py"
        toml_file = self.workspace / "valid.toml"
        python_file.write_text("value = 1\n", encoding="utf-8")
        toml_file.write_text("name = \"ok\"\n", encoding="utf-8")
        result = self.verifier.verify([python_file, toml_file])
        self.assertTrue(result.passed, result.failures)

    def test_invalid_python_fails(self) -> None:
        python_file = self.workspace / "invalid.py"
        python_file.write_text("def broken(:\n", encoding="utf-8")
        result = self.verifier.verify([python_file])
        self.assertFalse(result.passed)
        self.assertTrue(any("Python syntax" in failure for failure in result.failures))

    def test_outside_path_is_refused(self) -> None:
        outside = self.workspace.parent / "outside-test.py"
        outside.write_text("value = 1\n", encoding="utf-8")
        try:
            result = self.verifier.verify([outside])
            self.assertFalse(result.passed)
        finally:
            outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
