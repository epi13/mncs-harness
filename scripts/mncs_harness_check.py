#!/usr/bin/env python3
"""Owner-native enforcement-boundary check for the mncs-harness family boundary.

Runs the Atlas-bound execution-requirement hostile matrix plus the choke-point
enforcement tests (the boundary this badge attests) and writes one
mncs.check-result/1 document. Exit 0 always carries the verdict file; a FAIL
verdict is data, never a crash.

Scope is deliberately the enforcement boundary, not the full suite:
``python -m unittest discover`` plus ruff stay in this repo's own CI. The
boundary declaration in the caller workflow says exactly this.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

RESULT_SCHEMA = "mncs.check-result/1"
CHECK_ID = "harness-enforcement-boundary"
PROVIDER = "mncs-harness-pytest"


def _ensure_pytest() -> tuple[str, str]:
    """Return ``(python, note)`` able to run the boundary matrix.

    The family-verify job checks out only this repository with system
    Python: no dev extras, no installed package. The boundary matrix is
    stdlib-only apart from pytest itself. When pytest is already present
    the ambient interpreter is used untouched. Otherwise a throwaway
    virtualenv is built in the runner temp area (never the system
    interpreter, never the repo) and pytest is installed there.
    """
    try:
        subprocess.run(
            [sys.executable, "-m", "pytest", "--version"],
            capture_output=True,
            check=True,
            timeout=120,
        )
        return sys.executable, "pytest present; ambient interpreter untouched"
    except Exception:
        pass
    scratch = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    venv_dir = scratch / "mncs-harness-check-venv"
    created = subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    if created.returncode != 0:
        raise RuntimeError(f"check venv failed: {created.stderr[-400:]}")
    venv_python = str(venv_dir / "bin" / "python")
    installed = subprocess.run(
        [venv_python, "-m", "pip", "install", "-q", "pytest>=8.0"],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    if installed.returncode != 0:
        raise RuntimeError(f"pytest bootstrap failed: {installed.stderr[-400:]}")
    return venv_python, f"pytest bootstrapped in throwaway venv {venv_dir}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-file", required=True)
    parser.add_argument("--revision", default="working-tree")
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    try:
        python, setup_note = _ensure_pytest()
    except Exception as exc:
        return _write(args.result_file, args.revision, "FAIL", f"setup: {exc}")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo / "src") + (
        f":{env['PYTHONPATH']}" if env.get("PYTHONPATH") else ""
    )
    completed = subprocess.run(
        [
            python,
            "-m",
            "pytest",
            "tests/test_atlas_binding.py",
            "tests/test_atlas_enforcement.py",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=repo,
        timeout=1200,
        env=env,
    )
    verdict = "PASS" if completed.returncode == 0 else "FAIL"
    tail = (completed.stdout + completed.stderr)[-800:]
    last = tail.strip().splitlines()[-1] if tail.strip() else "no output"
    summary = (
        f"{setup_note}; pytest tests/test_atlas_binding.py "
        "tests/test_atlas_enforcement.py "
        f"exit={completed.returncode}: {last}"
    )
    return _write(args.result_file, args.revision, verdict, summary)


def _write(result_file: str, revision: str, verdict: str, summary: str) -> int:
    result = {
        "schema_version": RESULT_SCHEMA,
        "id": CHECK_ID,
        "provider": PROVIDER,
        "verdict": verdict,
        "summary": summary,
        "subject": {"repository": "mncs-harness", "revision": revision},
    }
    destination = Path(result_file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"id": CHECK_ID, "verdict": verdict}))
    return 0


if __name__ == "__main__":
    main()
