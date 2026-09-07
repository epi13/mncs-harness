#!/usr/bin/env python3
"""Owner-native MNCS boundary check for the mncs-harness family boundary.

Attests three layers and writes one mncs.check-result/1 document. Exit 0
always carries the verdict file; a FAIL verdict is data, never a crash.

1. Atlas enforcement matrix (existing hostile acceptance tests).
2. Packaged MNCS boundary integrity (stdlib-only, always runs):
   shipped artifact identity/digest/exports vs MANIFEST.json, no Python
   bypass of the MNCS decision boundary, corpus coverage per kernel.
3. LIVE MNCS execution: one hardcoded vector per kernel function through
   the real production path (mncs_exec -> shipped artifact -> executor).
   The executor resolves via MNCS_EXECUTOR, mncs-cli on PATH, or a
   digest-verified release download into the runner temp area. Any live
   failure FAILS the boundary. UNKNOWN is never emitted: decided vectors
   have exact expectations, and every failure mode is FAIL.

The family-verify job checks out only this repository with system Python:
no dev extras, no installed package, no toolchain checkout. pytest (and
cryptography for the Atlas matrix) bootstrap into a throwaway venv when
absent; the executor bootstraps via scripts/fetch_mncs_executor.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

RESULT_SCHEMA = "mncs.check-result/1"
CHECK_ID = "harness-enforcement-boundary"
PROVIDER = "mncs-harness-pytest"

REQUIRED_EXPORTS = {
    "harness_routing": {"classify_route", "needs_coder"},
    "harness_policy": {"evaluate_command", "evaluate_write"},
    "harness_verdict": {"dominate", "combine4", "combine8", "is_decided"},
    "harness_atlas": {"fold_pair", "fold4", "dispatch_gate", "tool_admission"},
    "harness_pins": {"pin_fields_valid", "admit_placement"},
    "harness_fabric": {"classify_fabric", "dispatch_allowed"},
    "harness_readiness": {"dominate_readiness", "fold4", "fold8", "is_ready"},
    "harness_eligibility": {"capability_eligible", "resource_gate"},
}

# (label, mncs_exec function, args, expected) — expectations are hardcoded
# here, never sourced from the Python mirror.
LIVE_VECTORS = [
    ("routing.classify", "primary_role", (True, False, True, False, False, False), "e4b"),
    ("routing.coder", "needs_coder", ("e4b", True, True), True),
    ("policy.command", "command_reason", (False, True, False, True, True, True, True), 0),
    ("policy.write", "write_allowed", (True,), False),
    ("verdict.combine", "combine", (["PASS", "UNKNOWN"],), "UNKNOWN"),
    ("verdict.decided", "verdict_decided", ("UNKNOWN",), False),
    ("atlas.fold", "fold", (["GRANTED", "REFUSED"],), "REFUSED"),
    ("atlas.gate", "dispatch_gate", ("GRANTED", False), "REFUSED"),
    ("atlas.tool", "tool_admission", (True, True), "GRANTED"),
    ("pins.shape", "pin_fields_valid", ("WORKER_MODEL", False, True, True), True),
    ("pins.admit", "admit_placement", (False, False, False), "PIN_FAILED_CLOSED"),
    ("fabric.class", "classify_fabric", (False, True, False, False, False), "COMPATIBLE_NEWER"),
    ("fabric.gate", "fabric_dispatch_allowed", ("UNKNOWN",), False),
    ("readiness.fold", "fold_readiness", (["READY", "DEGRADED"],), "DEGRADED"),
    ("readiness.ready", "readiness_ready", ("READY",), True),
    ("eligibility.gate", "capability_eligible", ("TOOLS", "NONE", False, "FAIL_CLOSED", False, False), False),
    ("eligibility.resource", "resource_gate", (True, 150, 200, True, 100), "OVER_AVAILABLE"),
]


def _ensure_pytest() -> tuple[str, str]:
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
        [venv_python, "-m", "pip", "install", "-q", "pytest>=8.0", "cryptography>=42"],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    if installed.returncode != 0:
        raise RuntimeError(f"check dependency bootstrap failed: {installed.stderr[-400:]}")
    return venv_python, f"pytest+cryptography bootstrapped in throwaway venv {venv_dir}"


def _static_gates(repo: Path) -> list[str]:
    notes: list[str] = []
    artifacts = repo / "src" / "mncs_harness" / "_mncs_artifacts"
    manifest = json.loads((artifacts / "MANIFEST.json").read_text(encoding="utf-8"))
    checked = 0
    for name, entry in sorted(manifest.get("artifacts", {}).items()):
        raw = json.loads((artifacts / name).read_text(encoding="utf-8"))
        if raw.get("identity") != entry["identity"]:
            raise RuntimeError(f"artifact {name} identity drift vs manifest")
        digest = hashlib.sha256(bytes.fromhex(raw.get("bytes_hex", ""))).hexdigest()
        if digest != str(entry["bytes_sha256"]).removeprefix("sha256:"):
            raise RuntimeError(f"artifact {name} digest mismatch")
        if raw.get("status") != "PASS":
            raise RuntimeError(f"artifact {name} is not a PASS artifact")
        kernel = name.split(".")[0]
        if kernel in REQUIRED_EXPORTS and not REQUIRED_EXPORTS[kernel] <= set(entry.get("exports", [])):
            raise RuntimeError(f"artifact {name} missing required exports")
        checked += 1
    notes.append(f"{checked} shipped artifacts validate (identity+digest+exports)")

    offenders = []
    for path in (repo / "src" / "mncs_harness").glob("*.py"):
        if path.name in ("mncs_logic.py", "mncs_exec.py"):
            continue
        if "mncs_logic" in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    if offenders:
        raise RuntimeError(f"Python bypass of MNCS boundary in {offenders}")
    notes.append("no production module imports the Python mirror")

    corpora = {path.stem.replace("-corpus", "").replace("-", "_") for path in (repo / "corpora").glob("*.json")}
    missing = set(REQUIRED_EXPORTS) - corpora
    if missing:
        raise RuntimeError(f"kernels without executable corpora: {sorted(missing)}")
    notes.append(f"{len(corpora)} executable corpora cover all kernels")
    return notes


def _ensure_executor(repo: Path) -> tuple[str, str]:
    override = os.environ.get("MNCS_EXECUTOR")
    if override:
        if Path(override).is_file():
            return override, f"MNCS_EXECUTOR={override}"
        raise RuntimeError(f"MNCS_EXECUTOR={override!r} is not a file; refusing to guess")
    for candidate in ("mncs-cli", "mncs-executor"):
        found = _which(candidate)
        if found:
            return found, f"{candidate} on PATH"
    scratch = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    dest = scratch / "mncs-executor"
    if not dest.is_file():
        completed = subprocess.run(
            [sys.executable, str(repo / "scripts" / "fetch_mncs_executor.py"), "--dest", str(dest)],
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        if completed.returncode != 0 or not dest.is_file():
            raise RuntimeError(f"executor bootstrap failed: {(completed.stdout + completed.stderr)[-500:]}")
        return str(dest), "digest-verified release executor downloaded"
    return str(dest), "cached release executor reused"


def _which(name: str) -> str | None:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _live_gates(repo: Path, python: str, executor: str) -> list[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo / "src") + (f":{env['PYTHONPATH']}" if env.get("PYTHONPATH") else "")
    env["MNCS_EXECUTOR"] = executor
    env.pop("MNCS_HARNESS_TRANSITIONAL_PYTHON_DECISIONS", None)
    probe_path = Path(tempfile.gettempdir()) / "mncs-boundary-probe.py"
    probe_path.write_text(
        "import json, sys\n"
        "from mncs_harness import mncs_exec\n"
        "vectors = json.load(open(sys.argv[1]))\n"
        "out = []\n"
        "for label, fn, args in vectors:\n"
        "    out.append([label, getattr(mncs_exec, fn)(*args)])\n"
        "print(json.dumps(out))\n",
        encoding="utf-8",
    )
    vectors_path = Path(tempfile.gettempdir()) / "mncs-boundary-vectors.json"
    vectors_path.write_text(
        json.dumps([[label, fn, list(args)] for label, fn, args, _ in LIVE_VECTORS]),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [python, str(probe_path), str(vectors_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=repo,
        timeout=600,
        env=env,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"live MNCS execution failed: {(completed.stdout + completed.stderr)[-800:]}")
    try:
        observed = {label: value for label, value in json.loads(completed.stdout)}
    except ValueError as exc:
        raise RuntimeError(f"live MNCS output malformed: {exc}") from exc
    failures = [
        f"{label}: got {observed.get(label)!r}, want {want!r}"
        for label, _, _, want in LIVE_VECTORS
        if observed.get(label) != want
    ]
    if failures:
        raise RuntimeError("live MNCS mismatch: " + "; ".join(failures))
    return [f"{len(LIVE_VECTORS)} live kernel vectors executed with exact expectations"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-file", required=True)
    parser.add_argument("--revision", default="working-tree")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]

    try:
        static_notes = _static_gates(repo)
    except Exception as exc:
        return _write(args.result_file, args.revision, "FAIL", f"packaged boundary: {exc}")

    try:
        python, setup_note = _ensure_pytest()
    except Exception as exc:
        return _write(args.result_file, args.revision, "FAIL", f"setup: {exc}")

    try:
        executor, executor_note = _ensure_executor(repo)
        live_notes = _live_gates(repo, python, executor)
    except Exception as exc:
        return _write(args.result_file, args.revision, "FAIL", f"live MNCS execution: {exc}")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo / "src") + (f":{env['PYTHONPATH']}" if env.get("PYTHONPATH") else "")
    env["MNCS_EXECUTOR"] = executor
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
        f"{setup_note}; {'; '.join(static_notes)}; executor {executor_note}; "
        f"{'; '.join(live_notes)}; pytest atlas matrix exit={completed.returncode}: {last}"
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
