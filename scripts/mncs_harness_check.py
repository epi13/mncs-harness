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
import subprocess
import sys
from pathlib import Path

RESULT_SCHEMA = "mncs.check-result/1"
CHECK_ID = "harness-enforcement-boundary"
PROVIDER = "mncs-harness-pytest"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-file", required=True)
    parser.add_argument("--revision", default="working-tree")
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
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
    )
    verdict = "PASS" if completed.returncode == 0 else "FAIL"
    tail = (completed.stdout + completed.stderr)[-800:]
    last = tail.strip().splitlines()[-1] if tail.strip() else "no output"
    result = {
        "schema_version": RESULT_SCHEMA,
        "id": CHECK_ID,
        "provider": PROVIDER,
        "verdict": verdict,
        "summary": (
            "pytest tests/test_atlas_binding.py tests/test_atlas_enforcement.py "
            f"exit={completed.returncode}: {last}"
        ),
        "subject": {"repository": "mncs-harness", "revision": args.revision},
    }
    destination = Path(args.result_file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"id": CHECK_ID, "verdict": verdict}))
    return 0


if __name__ == "__main__":
    main()
