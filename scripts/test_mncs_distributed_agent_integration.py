#!/usr/bin/env python3
"""Run the sibling-checkout Commons/Harness/Fabric contract scenario."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT.parent
CHECKS = {
    "Commons": (
        PARENT / "MNCS-Commons",
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_local_agent_node.py",
            "-k",
            "module_entrypoint or fabric_translation",
        ],
    ),
    "Harness": (
        ROOT,
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_commons.py",
            "tests/test_distributed_capabilities.py",
            "-k",
            "commons or worker_disappearing",
        ],
    ),
    "Fabric": (
        PARENT / "mncs-fabric",
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_transport.py",
            "-k",
            "execution_response or job_timeout",
        ],
    ),
}


def main() -> int:
    statuses: dict[str, str] = {}
    diagnostics: dict[str, str] = {}
    for name, (repository, argv) in CHECKS.items():
        if not (repository / "pyproject.toml").is_file():
            statuses[name] = "UNKNOWN"
            diagnostics[name] = f"sibling checkout missing: {repository}"
            continue
        try:
            completed = subprocess.run(
                argv,
                cwd=repository,
                capture_output=True,
                text=True,
                timeout=180,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired:
            statuses[name] = "FAIL"
            diagnostics[name] = "bounded integration check timed out"
            continue
        statuses[name] = "PASS" if completed.returncode == 0 else "FAIL"
        diagnostics[name] = (completed.stdout + completed.stderr).strip()[-2000:]

    for name in CHECKS:
        print(f"{name}: {statuses[name]}")
        if statuses[name] != "PASS" and diagnostics[name]:
            print(f"  {diagnostics[name]}")

    if all(value == "PASS" for value in statuses.values()):
        print("remote inference placement: fabric-worker:fixture-worker")
        print("workspace target: controller")
        print("tool target: controller")
        print("Commons target: controller-local stdio MCP/store")
        print("Fabric evidence: execution record + opaque consumer provenance")
        print("Commons publication: inert Observation; source PASS != verification PASS")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
