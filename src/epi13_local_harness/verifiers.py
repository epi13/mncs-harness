from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from pathlib import Path

from .models import VerificationConfig, VerificationResult


class Verifier:
    def __init__(self, workspace: Path, config: VerificationConfig):
        self.workspace = workspace.resolve()
        self.config = config

    def _run(self, argv: list[str], label: str, timeout: int = 120) -> tuple[bool, str]:
        try:
            completed = subprocess.run(
                argv,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"{label}: {exc}"
        if completed.returncode == 0:
            return True, f"{label}: passed"
        detail = (completed.stderr or completed.stdout).strip()
        return False, f"{label}: exit {completed.returncode}: {detail[:2000]}"

    def verify(self, paths: list[Path]) -> VerificationResult:
        unique = sorted({path.resolve() for path in paths if path.exists()})
        expanded: list[Path] = []
        for path in unique:
            if path.is_dir():
                expanded.extend(
                    child for child in path.rglob("*") if child.is_file() and not child.is_symlink()
                )
            elif path.is_file():
                expanded.append(path)

        checks: list[str] = []
        failures: list[str] = []
        python_files: list[Path] = []
        shell_files: list[Path] = []

        for path in sorted(set(expanded)):
            try:
                relative = path.relative_to(self.workspace)
            except ValueError:
                failures.append(f"Verifier refused path outside workspace: {path}")
                continue
            suffix = path.suffix.lower()
            if suffix == ".py":
                python_files.append(relative)
            elif suffix in {".sh", ".bash"}:
                shell_files.append(relative)
            elif suffix == ".json":
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                    checks.append(f"JSON parse {relative}: passed")
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    failures.append(f"JSON parse {relative}: {exc}")
            elif suffix == ".toml":
                try:
                    with path.open("rb") as handle:
                        tomllib.load(handle)
                    checks.append(f"TOML parse {relative}: passed")
                except (OSError, tomllib.TOMLDecodeError) as exc:
                    failures.append(f"TOML parse {relative}: {exc}")

        if python_files:
            ok, detail = self._run(
                ["python", "-m", "py_compile", *[str(path) for path in python_files]],
                "Python syntax",
            )
            (checks if ok else failures).append(detail)

        for relative in shell_files:
            ok, detail = self._run(["bash", "-n", str(relative)], f"Shell syntax {relative}")
            (checks if ok else failures).append(detail)
            if self.config.use_shellcheck_when_available and shutil.which("shellcheck"):
                ok, detail = self._run(["shellcheck", str(relative)], f"ShellCheck {relative}")
                (checks if ok else failures).append(detail)

        if self.config.run_unit_tests and self.config.unit_test_command:
            ok, detail = self._run(list(self.config.unit_test_command), "Configured unit tests", 300)
            (checks if ok else failures).append(detail)

        if not expanded:
            checks.append("No modified files required deterministic syntax verification")

        return VerificationResult(
            passed=not failures,
            checks=tuple(checks),
            failures=tuple(failures),
        )
