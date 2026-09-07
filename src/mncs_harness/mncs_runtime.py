"""Real MNCS execution boundary for the harness product path.

Production decision flow for converted kernels::

    Python host
        -> marshal bounded inputs (bools/ints/enums, never raw text)
        -> execute shipped frozen MNCS artifact (no recompilation)
        -> decode typed/bounded result
        -> perform only the required host-side effect

Artifacts under ``_mncs_artifacts/`` are built by
``scripts/build_mncs_artifacts.py`` from ``mncs/*.mncs`` + ``corpora/`` and
validated by identity/digest at load. Execution uses the MNCS executor
binary (``experiment execute`` over a frozen artifact); the compiler is
never invoked per request.

Fail-closed: missing executor, unreadable/tampered artifact, identity or
digest mismatch, executor error, malformed output, or an ``UNKNOWN`` where
the caller requires decided — all raise :class:`MncsRuntimeError`. There is
no fallback to a Python copy of the policy: MNCS is the single executable
authority for every converted kernel.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

EXECUTOR_ENV = "MNCS_EXECUTOR"
BACKEND_ENV = "MNCS_HARNESS_BACKEND"  # "wasm" (default) or "bytecode"

WASM_SUFFIX = "wasm"
BYTECODE_SUFFIX = "bytecode"


class MncsRuntimeError(RuntimeError):
    """Raised when required MNCS execution cannot be completed. Fail closed."""


@dataclass(frozen=True)
class KernelArtifact:
    module: str
    backend: str
    path: Path
    identity: str
    bytes_sha256: str
    exports: tuple[str, ...]

    def raw(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MncsRuntimeError(f"cannot read artifact {self.path}: {exc}") from exc


def _artifacts_dir() -> Path:
    return Path(str(files("mncs_harness").joinpath("_mncs_artifacts")))


def _manifest() -> dict[str, Any]:
    path = _artifacts_dir() / "MANIFEST.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MncsRuntimeError(f"cannot read MNCS artifact manifest: {exc}") from exc


def find_executor() -> str:
    """Locate the MNCS executor binary. Raises; never guesses silently."""
    override = os.environ.get(EXECUTOR_ENV)
    if override:
        if Path(override).is_file():
            return override
        raise MncsRuntimeError(f"{EXECUTOR_ENV}={override!r} is not a file")
    found = shutil.which("mncs-executor")
    if found:
        return found
    here = Path(__file__).resolve()
    for sibling in (
        here.parents[3] / "mncs-language" / "target" / "debug" / "mncs",
        here.parents[3] / "mncs-language" / "target" / "release" / "mncs",
    ):
        if sibling.is_file():
            return str(sibling)
    raise MncsRuntimeError(
        "no MNCS executor available: set MNCS_EXECUTOR, put mncs-executor on PATH "
        "(python3 scripts/fetch_mncs_executor.py), or check out mncs-language "
        "next to mncs-harness and build it (cargo build -p mncs-cli)"
    )


def selected_backend() -> str:
    choice = os.environ.get(BACKEND_ENV, WASM_SUFFIX).strip().lower()
    if choice in (WASM_SUFFIX, "portable-wasm", "portable-wasm-mvp"):
        return WASM_SUFFIX
    if choice in (BYTECODE_SUFFIX, "research-bytecode"):
        return BYTECODE_SUFFIX
    raise MncsRuntimeError(f"{BACKEND_ENV}={choice!r}: want 'wasm' or 'bytecode'")


def load_kernel(kernel: str, backend: str | None = None) -> KernelArtifact:
    """Load and validate one shipped kernel artifact. Raises on any defect."""
    backend = backend or selected_backend()
    manifest = _manifest()
    name = f"{kernel}.{backend}.json"
    entry = manifest.get("artifacts", {}).get(name)
    if entry is None:
        raise MncsRuntimeError(f"artifact {name} missing from MANIFEST.json")
    path = _artifacts_dir() / name
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MncsRuntimeError(f"cannot read shipped artifact {name}: {exc}") from exc
    if raw.get("identity") != entry["identity"]:
        raise MncsRuntimeError(f"artifact {name} identity does not match manifest")
    if raw.get("status") != "PASS":
        raise MncsRuntimeError(f"artifact {name} is not a PASS artifact")
    try:
        artifact_bytes = bytes.fromhex(raw.get("bytes_hex", ""))
    except ValueError as exc:
        raise MncsRuntimeError(f"artifact {name} has malformed bytes_hex") from exc
    digest = hashlib.sha256(artifact_bytes).hexdigest()
    declared = str(raw.get("bytes_sha256", "")).removeprefix("sha256:")
    if digest != declared:
        raise MncsRuntimeError(f"artifact {name} bytes do not match bytes_sha256")
    if digest != str(entry["bytes_sha256"]).removeprefix("sha256:"):
        raise MncsRuntimeError(f"artifact {name} bytes do not match manifest digest")
    return KernelArtifact(
        module=entry["module"],
        backend=entry["backend"],
        path=path,
        identity=entry["identity"],
        bytes_sha256=entry["bytes_sha256"],
        exports=tuple(entry.get("exports", [])),
    )


def _discriminant_map(artifact: KernelArtifact, function: str) -> dict[str, int]:
    """Resolve (type_identity, variant) -> discriminant from artifact contracts."""
    raw = artifact.raw()
    contracts = raw.get("function_value_contracts", {}).get(function, {})
    mapping: dict[str, int] = {}
    for entry in contracts.get("inputs", []) + contracts.get("outputs", []):
        finite = entry.get("finite", {})
        type_identity = finite.get("type_identity", "")
        for discriminant, variant_identity in (finite.get("variants", {}) or {}).items():
            short = str(variant_identity).rsplit("::", 1)[-1]
            mapping[f"{type_identity}::{short}"] = int(discriminant)
            mapping[f"{type_identity}::{variant_identity}"] = int(discriminant)
    for composite in (raw.get("composite_value_contracts", {}) or {}).values():
        finite = composite.get("finite", {})
        type_identity = finite.get("type_identity", "")
        for discriminant, variant_identity in (finite.get("variants", {}) or {}).items():
            short = str(variant_identity).rsplit("::", 1)[-1]
            mapping[f"{type_identity}::{short}"] = int(discriminant)
    return mapping


def _encode(discriminants: dict[str, int], value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolean": {"value": value}}
    if isinstance(value, int):
        return {"integer": {"value": value, "type": {"bits": 64, "signed": True}}}
    if isinstance(value, tuple) and len(value) == 3:
        module, enum, variant = value
        type_identity = f"mncs:0.2:finite-type:{module}::{enum}"
        key = f"{type_identity}::{variant}"
        if key not in discriminants:
            raise MncsRuntimeError(f"variant {variant!r} not in artifact contracts")
        return {
            "finite": {
                "type_identity": type_identity,
                "variant_identity": f"mncs:0.2:finite-variant:{module}::{enum}::{variant}",
                "discriminant": discriminants[key],
            }
        }
    raise MncsRuntimeError(f"cannot marshal MNCS ABI value: {value!r}")


def _decode(value: Any) -> Any:
    if not isinstance(value, dict) or len(value) != 1:
        raise MncsRuntimeError(f"malformed MNCS return value: {value!r}")
    kind, payload = next(iter(value.items()))
    if kind == "boolean":
        result = payload.get("value")
        if not isinstance(result, bool):
            raise MncsRuntimeError(f"malformed MNCS boolean: {value!r}")
        return result
    if kind == "integer":
        result = payload.get("value")
        if not isinstance(result, int):
            raise MncsRuntimeError(f"malformed MNCS integer: {value!r}")
        return result
    if kind == "finite":
        variant = str(payload.get("variant_identity", "")).rsplit("::", 1)[-1]
        if not variant:
            raise MncsRuntimeError(f"malformed MNCS finite value: {value!r}")
        decoded_payload = {}
        for name, item in payload.get("payload", []) or []:
            decoded_payload[name] = _decode(item)
        return (variant, decoded_payload)
    raise MncsRuntimeError(f"unsupported MNCS return kind: {kind!r}")


def call(kernel: str, function: str, args: list[Any], *, timeout: int = 120) -> Any:
    """Execute one kernel function through the shipped artifact.

    Returns the decoded single return value. Raises :class:`MncsRuntimeError`
    on any failure, including a backend-reported non-returned case. Never
    falls back to Python.
    """
    artifact = load_kernel(kernel)
    if function not in artifact.exports:
        raise MncsRuntimeError(f"{artifact.module}::{function} is not an artifact export")
    discriminants = _discriminant_map(artifact, function)
    corpus = {
        "schema_version": "0.1",
        "name": "harness-runtime-call",
        "cases": [
            {
                "id": "call",
                "request": {
                    "schema_version": "0.1",
                    "target": {"module": artifact.module, "function": function},
                    "arguments": [_encode(discriminants, value) for value in args],
                    "step_budget": 4096,
                },
            }
        ],
    }
    executor = find_executor()
    with tempfile.TemporaryDirectory(prefix="mncs-harness-call-") as tmp:
        corpus_path = Path(tmp) / "corpus.json"
        corpus_path.write_text(json.dumps(corpus), encoding="utf-8")
        try:
            completed = subprocess.run(
                [executor, "experiment", "execute", str(artifact.path), str(corpus_path)],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MncsRuntimeError(f"MNCS executor failed for {function}: {exc}") from exc
    if completed.returncode != 0:
        raise MncsRuntimeError(f"MNCS executor error for {function}: {completed.stderr[-2000:]}")
    try:
        observations = json.loads(completed.stdout)
        case = observations[0]
    except (ValueError, IndexError, KeyError) as exc:
        raise MncsRuntimeError(f"malformed MNCS executor output for {function}: {exc}") from exc
    if case.get("status") != "returned" or case.get("failure_reason"):
        raise MncsRuntimeError(
            f"MNCS {function} did not return: status={case.get('status')} "
            f"reason={case.get('failure_reason')}"
        )
    returned = case.get("returned", [])
    if len(returned) != 1:
        raise MncsRuntimeError(f"MNCS {function} returned {len(returned)} values, want 1")
    return _decode(returned[0])
