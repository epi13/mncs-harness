#!/usr/bin/env python3
"""Build (or verify) the shipped frozen MNCS backend artifacts.

For every production kernel in KERNELS and every backend in BACKENDS, runs
``mncs experiment run`` over the kernel's executable corpus, asserts the
result is PASS with every expectation met, and ships the frozen
``backend-artifact.json`` under ``src/mncs_harness/_mncs_artifacts/``.

``--verify`` rebuilds into a temp dir and fails if any rebuilt artifact
identity differs from the shipped one (source/artifact drift gate for CI).

The executor binary is resolved exactly like the product runtime does
(see ``mncs_runtime.find_executor``): ``MNCS_EXECUTOR`` env, then
``mncs-executor`` on PATH, then a sibling ``mncs-language`` checkout build.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = REPO / "src" / "mncs_harness" / "_mncs_artifacts"

KERNELS = (
    ("harness_routing", "mncs.harness.routing.v1"),
    ("harness_policy", "mncs.harness.policy.v1"),
    ("harness_verdict", "mncs.harness.verdict.v1"),
    ("harness_atlas", "mncs.harness.atlas.v1"),
    ("harness_pins", "mncs.harness.pins.v1"),
    ("harness_fabric", "mncs.harness.fabric.v1"),
    ("harness_readiness", "mncs.harness.readiness.v1"),
    ("harness_eligibility", "mncs.harness.eligibility.v1"),
)
BACKENDS = ("mncs-portable-wasm-mvp", "mncs-research-bytecode")

SHORT_BACKEND = {
    "mncs-portable-wasm-mvp": "wasm",
    "mncs-research-bytecode": "bytecode",
}


def find_executor() -> str:
    """Resolve the mncs compiler/executor binary (build-time twin of runtime)."""
    override = __import__("os").environ.get("MNCS_EXECUTOR")
    if override:
        return override
    found = shutil.which("mncs-executor")
    if found:
        return found
    for sibling in (
        REPO.parent / "mncs-language" / "target" / "debug" / "mncs",
        REPO.parent / "mncs-language" / "target" / "release" / "mncs",
    ):
        if sibling.is_file():
            return str(sibling)
    raise SystemExit(
        "no MNCS executor found: set MNCS_EXECUTOR, put mncs-executor on PATH "
        "(python3 scripts/fetch_mncs_executor.py), or check out mncs-language "
        "next to mncs-harness and build it"
    )


def build_one(executor: str, kernel: str, backend: str, out_dir: Path) -> dict:
    source = REPO / "mncs" / f"{kernel}.mncs"
    corpus = REPO / "corpora" / f"{kernel.replace('harness_', 'harness-')}-corpus.json"
    if not source.is_file():
        raise SystemExit(f"missing MNCS source: {source}")
    if not corpus.is_file():
        raise SystemExit(f"missing corpus: {corpus}")
    completed = subprocess.run(
        [
            executor,
            "experiment",
            "run",
            str(source),
            "--backend",
            backend,
            "--corpus",
            str(corpus),
            "--output-dir",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if completed.returncode != 0:
        raise SystemExit(f"experiment run failed for {kernel}/{backend}:\n{completed.stderr[-3000:]}")
    result = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
    cases = result.get("cases", [])
    unmet = [case.get("id") for case in cases if not case.get("expectation_met")]
    if result.get("status") != "PASS" or unmet:
        raise SystemExit(f"corpus not green for {kernel}/{backend}: status={result.get('status')} unmet={unmet}")
    artifact = json.loads((out_dir / "backend-artifact.json").read_text(encoding="utf-8"))
    if artifact.get("status") != "PASS":
        raise SystemExit(f"artifact not PASS for {kernel}/{backend}")
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="rebuild and compare, do not write")
    args = parser.parse_args()
    executor = find_executor()
    print(f"executor: {executor}")

    if args.verify:
        failures: list[str] = []
        with tempfile.TemporaryDirectory(prefix="mncs-artifact-verify-") as tmp:
            for kernel, module in KERNELS:
                for backend in BACKENDS:
                    name = f"{kernel}.{SHORT_BACKEND[backend]}.json"
                    shipped = json.loads((ARTIFACT_DIR / name).read_text(encoding="utf-8"))
                    rebuilt = build_one(executor, kernel, backend, Path(tmp) / kernel / backend)
                    if rebuilt["identity"] != shipped["identity"]:
                        failures.append(f"{name}: identity drift")
                    if rebuilt["bytes_sha256"] != shipped["bytes_sha256"]:
                        failures.append(f"{name}: bytes drift")
                    print(f"  {name}: {'DRIFT' if failures and failures[-1].startswith(name) else 'match'}")
        if failures:
            raise SystemExit("artifact drift detected:\n" + "\n".join(failures))
        print("all shipped artifacts match rebuilt identities")
        return

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"schema": "mncs.harness-artifact-manifest.v1", "artifacts": {}}
    for kernel, module in KERNELS:
        source = REPO / "mncs" / f"{kernel}.mncs"
        source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
        for backend in BACKENDS:
            with tempfile.TemporaryDirectory(prefix="mncs-artifact-build-") as tmp:
                artifact = build_one(executor, kernel, backend, Path(tmp))
            name = f"{kernel}.{SHORT_BACKEND[backend]}.json"
            (ARTIFACT_DIR / name).write_text(json.dumps(artifact, indent=1) + "\n", encoding="utf-8")
            manifest["artifacts"][name] = {
                "module": module,
                "backend": backend,
                "identity": artifact["identity"],
                "bytes_sha256": artifact["bytes_sha256"],
                "exports": artifact.get("exports", []),
                "source": f"mncs/{kernel}.mncs",
                "source_sha256": f"sha256:{source_sha256}",
                "corpus": f"corpora/{kernel.replace('harness_', 'harness-')}-corpus.json",
            }
            print(f"  {name}: {artifact['identity'][:64]}")
    (ARTIFACT_DIR / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(manifest['artifacts'])} artifacts + MANIFEST.json")


if __name__ == "__main__":
    main()
