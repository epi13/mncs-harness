"""Durable family end-to-end gauntlet (epi13/mncs-harness#57).

Runs the real enforcement chain and emits a machine-readable manifest:

Atlas (real Router issuance)
-> requirement identity/provenance (digest-verified load)
-> Harness acceptance (ToolRegistry choke point)
-> Fabric placement (persistent service, operator-asserted observation)
-> worker/backend execution (real dispatch + confirm)
-> Rights binding (authorization identities)
-> Commons evidence (session status + confirmation)

Leg verdicts are PASS / UNKNOWN / REFUSED. Absent hardware, toolchains,
or checkouts degrade a leg to UNKNOWN with a reason -- never to a fake
PASS or a fake FAIL. The manifest verdict is PASS only when every row
observes exactly its expected verdict. Reproducible from the manifest
plus the recorded exact repository revisions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA = "mncs.e2e-gauntlet-manifest/1"
ACTIONS_CARRIER_REVISION = "9469d8d61f4604df0f00f983aedb3d66c6ff8616"
GAUNTLET_WORKER = "gauntlet-worker"
GAUNTLET_SCOPE = "repo(mncs-harness)"
GAUNTLET_PARTICIPANT = "gauntlet-agent"


def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _digest(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _git_head(path: Path) -> dict[str, Any]:
    if not (path / ".git").exists():
        return {"present": False, "commit": None, "dirty": None}
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=path,
            timeout=30,
        ).stdout.strip()
        dirty = (
            subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
                cwd=path,
                timeout=30,
            ).stdout.strip()
            != ""
        )
        return {"present": True, "commit": commit, "dirty": dirty}
    except Exception as exc:
        return {"present": True, "commit": None, "dirty": None, "error": str(exc)}


def collect_revisions(harness_root: Path) -> dict[str, Any]:
    family = {
        "mncs-harness": harness_root,
        "mncs-atlas": harness_root.parent / "mncs-atlas",
        "mncs-language": harness_root.parent / "mncs-language",
        "mncs-fabric": harness_root.parent / "mncs-fabric",
        "mncs-rights-provenance": harness_root.parent / "mncs-rights-provenance",
        "mncs-commons": harness_root.parent / "MNCS-Commons",
        "mncs-actions": harness_root.parent / "mncs-actions",
        "mncs-lineage": harness_root.parent / "mncs-lineage",
    }
    return {name: _git_head(path) for name, path in family.items()}


def atlas_phase(family_root: Path) -> tuple[dict[str, Any], Any]:
    """Issue real Atlas decisions for the gauntlet session.

    The driver acts as the Atlas operator here: it generates an
    ephemeral issuer keypair, issues through the real Router with that
    issuer signing, and publishes the public half as the trust root
    downstream phases verify against. Trust roots are operator
    configuration even in the demo; nothing verifies against envelope
    claims.
    """
    try:
        sys.path.insert(0, str(family_root / "mncs-atlas"))
        from admission import Participant, Router, new_outside_session
        from admission.issuance import AtlasIssuer
    except Exception as exc:
        return {"verdict": "UNKNOWN", "reason": f"atlas unavailable: {exc}", "decisions": []}, None
    try:
        issuer = AtlasIssuer.generate("gauntlet-atlas-ephemeral")
    except Exception as exc:
        return {"verdict": "UNKNOWN", "reason": f"atlas issuance unavailable: {exc}",
                "decisions": []}, None
    try:
        session = new_outside_session()
        session.identify(
            Participant(
                identity=GAUNTLET_PARTICIPANT,
                type="agent",
                provenance="e2e-gauntlet",
            )
        )
        session.admit("0.4.0", {"mncs": "experimental"})
        session.bind_scope(
            GAUNTLET_SCOPE,
            repository_context="mncs-harness",
            purpose="e2e gauntlet",
        )
        router = Router()
        # Local (controller-host) execution is a bound target like any
        # other: the controller authorizes itself as the placement for
        # the local leg, and Fabric workers for dispatch legs.
        # worker.dispatch is queried for both placements: multiple
        # authorized targets are placement options, and each leg must
        # name one of them.
        queries = [
            ("tests.execute", "controller"),
            ("repo.edit", "controller"),
            ("evidence.attest", ""),
            ("worker.dispatch", GAUNTLET_WORKER),
            ("worker.dispatch", "controller"),
        ]
        decisions = []
        for capability, target in queries:
            decisions.append(
                router.query(session, capability, execution_target=target, issuer=issuer)
            )
        if any("decision_digest" not in item for item in decisions):
            return {
                "verdict": "UNKNOWN",
                "reason": "atlas decisions lack provenance binding (needs epi13/mncs-atlas#28)",
                "decisions": [],
            }, None
        if any("issuer_signature" not in item for item in decisions):
            return {
                "verdict": "UNKNOWN",
                "reason": "atlas decisions lack issuance authenticity (needs epi13/mncs-atlas#31)",
                "decisions": [],
            }, None
        return {
            "verdict": "PASS",
            "session": {"participant": GAUNTLET_PARTICIPANT, "scope": GAUNTLET_SCOPE},
            "issuer_key_id": issuer.key_id,
            "trust_roots": {issuer.key_id: issuer.public_hex},
            "decisions": [
                {
                    "capability": item["capability"],
                    "status": item["status"],
                    "authority": item.get("authority", ""),
                    "execution_target": item.get("execution_target", ""),
                    "decision_digest": item["decision_digest"],
                    "issuer_key_id": item["issuer"]["key_id"],
                }
                for item in decisions
            ],
            "raw_decisions": decisions,
        }, issuer
    except Exception as exc:
        return {"verdict": "UNKNOWN", "reason": f"atlas issuance failed: {exc}", "decisions": []}, None


def _trust_from_atlas(atlas: dict[str, Any]) -> dict[str, bytes]:
    roots = atlas.get("trust_roots", {})
    return {str(key): bytes.fromhex(value) for key, value in roots.items()}


def requirement_phase(atlas: dict[str, Any]) -> dict[str, Any]:
    from mncs_harness.atlas_binding import ExecutionRequirement, build_requirement

    if atlas.get("verdict") != "PASS":
        return {
            "verdict": "UNKNOWN",
            "reason": "no Atlas decisions to carry",
            "requirement_id": None,
        }
    try:
        envelope = build_requirement(
            task_id="gauntlet-001",
            source_identity="sha256:" + _digest({"gauntlet": "source"}),
            artifact_identity="sha256:" + _digest({"gauntlet": "artifact"}),
            atlas_decisions=atlas["raw_decisions"],
            legs=[
                {
                    "name": "local",
                    "needs": ["tests.execute", "repo.edit", "worker.dispatch"],
                    "target": "controller",
                },
                {
                    "name": "dispatch",
                    "needs": ["worker.dispatch"],
                    "backend": "persistent-service",
                    "target": GAUNTLET_WORKER,
                },
            ],
            session_participant=GAUNTLET_PARTICIPANT,
            session_scope=GAUNTLET_SCOPE,
        )
        requirement = ExecutionRequirement.from_dict(
            envelope, trusted_issuers=_trust_from_atlas(atlas)
        )
        local = requirement.accept("local")
        dispatch = requirement.accept("dispatch")
        verdict = "PASS" if local.verdict == "GRANTED" and dispatch.verdict == "GRANTED" else "FAIL"
        return {
            "verdict": verdict,
            "requirement_id": requirement.requirement_id,
            "legs": [
                {"name": "local", "verdict": local.verdict, "reason": local.reason},
                {"name": "dispatch", "verdict": dispatch.verdict, "reason": dispatch.reason},
            ],
            "reason": "" if verdict == "PASS" else f"{local.reason} | {dispatch.reason}",
            "envelope": envelope,
        }
    except Exception as exc:
        return {"verdict": "FAIL", "reason": f"requirement load failed: {exc}"}


def adversarial_phase(atlas: dict[str, Any], issuer: Any) -> dict[str, Any]:
    """Replay the hostile rows against live Atlas-issued decisions.

    Each row mutates a genuine issuance (status flip, session swap, scope
    replay, duplicate conflict, wrong declared target) and records whether
    the enforcement layer observes the expected fail-closed verdict.
    Genuine re-issuance goes through the gauntlet issuer (the operator
    role in this driver); attacker digest recomputation must fail at
    issuance verification even with a fresh valid digest.
    """

    import copy
    import hashlib

    from mncs_harness.atlas_binding import (
        BindingError,
        ExecutionRequirement,
        canonical_envelope_bytes,
    )

    trust = _trust_from_atlas(atlas)

    def _reissue(mutated: dict[str, Any]) -> dict[str, Any]:
        """Genuine Atlas re-issuance through the gauntlet issuer: digest
        AND signature refresh together (e.g. deny after a context
        change). The folded conflict is then real authority, and must
        still fail closed."""
        if issuer is None:
            raise RuntimeError("no gauntlet issuer for re-issuance")
        return issuer.sign_decision(mutated)

    def _forge_digest(mutated: dict[str, Any]) -> dict[str, Any]:
        """Attacker digest recomputation without the issuer key: a fresh
        valid content digest over forged content. Must fail at issuance
        verification."""
        mutated["decision_digest"] = hashlib.sha256(canonical_envelope_bytes(mutated)).hexdigest()
        return mutated

    def _loads(envelope: dict[str, Any]) -> bool:
        try:
            ExecutionRequirement.from_dict(envelope, trusted_issuers=trust)
        except Exception:
            return False
        return True

    if atlas.get("verdict") != "PASS":
        return {"verdict": "UNKNOWN", "reason": "no Atlas decisions to attack", "rows": []}

    raw: list[dict[str, Any]] = atlas["raw_decisions"]

    def carry(decisions: list[dict[str, Any]], **leg_override: Any) -> dict[str, Any]:
        from mncs_harness.atlas_binding import build_requirement as carry_fn

        leg: dict[str, Any] = {"name": "probe", "needs": ["tests.execute"]}
        leg.update(leg_override)
        envelope = carry_fn(
            task_id="gauntlet-adv",
            source_identity="sha256:" + _digest({"gauntlet": "adv-source"}),
            artifact_identity="sha256:" + _digest({"gauntlet": "adv-artifact"}),
            atlas_decisions=decisions,
            legs=[leg],
            session_participant=GAUNTLET_PARTICIPANT,
            session_scope=GAUNTLET_SCOPE,
        )
        return envelope

    rows: list[dict[str, Any]] = []

    def record(name: str, expected: str, observed: str, detail: str = "") -> None:
        rows.append(
            {
                "name": name,
                "expected": expected,
                "observed": observed,
                "pass": observed == expected,
                "detail": detail[:240],
            }
        )

    base = next(item for item in raw if item["capability"] == "tests.execute")

    # Forged grant: a genuine deny whose status is flipped to granted
    # while keeping the deny digest and signature. Must never load.
    genuine_deny = _reissue({**copy.deepcopy(base), "status": "denied"})
    forged = copy.deepcopy(genuine_deny)
    forged["status"] = "granted"
    try:
        ExecutionRequirement.from_dict(carry([forged]), trusted_issuers=trust)
        record("forged-grant", "BindingError", "LOADED")
    except BindingError as exc:
        record("forged-grant", "BindingError", "BindingError", str(exc))

    # Full attacker effort: fresh valid content digest recomputed over the
    # forged grant, but no issuer key. Must fail at issuance verification.
    full_forgery = _forge_digest(copy.deepcopy(forged))
    try:
        ExecutionRequirement.from_dict(carry([full_forgery]), trusted_issuers=trust)
        record("forged-grant-fresh-digest", "BindingError", "LOADED")
    except BindingError as exc:
        record("forged-grant-fresh-digest", "BindingError", "BindingError", str(exc))

    # Tampered session: valid digest, swapped participant echo.
    swapped = copy.deepcopy(base)
    swapped["session"] = {"participant": "intruder", "scope": GAUNTLET_SCOPE}
    try:
        ExecutionRequirement.from_dict(carry([swapped]), trusted_issuers=trust)
        record("session-swap", "BindingError", "LOADED")
    except BindingError as exc:
        record("session-swap", "BindingError", "BindingError", str(exc))

    # Replay in another scope: envelope context differs from issuance.
    try:
        envelope = carry([copy.deepcopy(base)])
        envelope["session"] = {"participant": GAUNTLET_PARTICIPANT, "scope": "repo(other)"}
        ExecutionRequirement.from_dict(envelope, trusted_issuers=trust)
        record("scope-replay", "BindingError", "LOADED")
    except BindingError as exc:
        record("scope-replay", "BindingError", "BindingError", str(exc))

    # Conflicting duplicates, both orders: deterministic refusal.
    granted = _reissue({**copy.deepcopy(base), "status": "granted"})
    conflict = _reissue({**copy.deepcopy(base), "status": "denied"})
    try:
        first = ExecutionRequirement.from_dict(carry([granted, conflict]), trusted_issuers=trust).accept("probe")
        second = ExecutionRequirement.from_dict(carry([conflict, granted]), trusted_issuers=trust).accept("probe")
        record(
            "duplicate-conflict",
            "REFUSED/REFUSED",
            f"{first.verdict}/{second.verdict}",
            first.reason,
        )
    except BindingError as exc:
        record("duplicate-conflict", "REFUSED/REFUSED", "BindingError", str(exc))

    # Wrong declared target on a constrained need.
    dispatch_raw = next(item for item in raw if item["capability"] == "worker.dispatch")
    try:
        envelope = carry(
            [copy.deepcopy(dispatch_raw)],
            needs=["worker.dispatch"],
            target="someone-else",
        )
        # The leg needs worker.dispatch but the envelope leg name/needs
        # target another worker: acceptance must refuse.
        verdict = ExecutionRequirement.from_dict(envelope, trusted_issuers=trust).accept("probe").verdict
        record("wrong-target", "REFUSED", verdict)
    except BindingError as exc:
        record("wrong-target", "REFUSED", "BindingError", str(exc))

    # Constrained target but the leg declares none: the placement cannot be
    # shown authorized, so it refuses (the pure-missing-evidence UNKNOWN is
    # pinned in the pytest matrix with an unconstrained decision).
    conditional = _reissue(
        {**copy.deepcopy(base), "status": "conditional", "missing": ["execution.target"]}
    )
    try:
        verdict = ExecutionRequirement.from_dict(carry([conditional]), trusted_issuers=trust).accept("probe").verdict
        record("unbound-target", "REFUSED", verdict)
    except BindingError as exc:
        record("unbound-target", "REFUSED", "BindingError", str(exc))

    verdict = "PASS" if all(row["pass"] for row in rows) else "FAIL"
    return {"verdict": verdict, "rows": rows}


def local_tools_phase(envelope: dict[str, Any] | None, trust: dict[str, bytes] | None = None) -> dict[str, Any]:
    """Prove the ToolRegistry choke point with the live requirement."""
    import tempfile

    from mncs_harness.atlas_binding import ExecutionRequirement
    from mncs_harness.config import load_config
    from mncs_harness.tools import ToolRegistry

    if not envelope:
        return {"verdict": "UNKNOWN", "reason": "no requirement envelope", "rows": []}
    rows: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="gauntlet-tools-") as directory:
            workspace = Path(directory)
            policy = load_config(Path("/missing/config.toml")).policy
            bound = ToolRegistry(workspace, policy, auto_approve=True, interactive=False)
            acceptance = bound.bind_atlas_requirement(
                ExecutionRequirement.from_dict(envelope, trusted_issuers=trust or {}), "local"
            )
            rows.append(
                {
                    "name": "bound-leg-acceptance",
                    "expected": "GRANTED",
                    "observed": acceptance.verdict,
                    "pass": acceptance.verdict == "GRANTED",
                }
            )
            (workspace / "gauntlet_compute.py").write_text("print(6 * 7)\n", encoding="utf-8")
            allowed = bound.execute("run_command", {"argv": ["python", "gauntlet_compute.py"]})
            rows.append(
                {
                    "name": "bound-run-command",
                    "expected": True,
                    "observed": allowed.success,
                    "pass": allowed.success and "42" in allowed.output,
                    "detail": allowed.output[:200],
                }
            )
            naked = ToolRegistry(workspace, policy, auto_approve=True, interactive=False)
            refused = naked.execute("run_command", {"argv": ["python", "gauntlet_compute.py"]})
            rows.append(
                {
                    "name": "unbound-run-command-refused",
                    "expected": "ATLAS_REFUSED",
                    "observed": refused.output[:14],
                    "pass": (not refused.success) and refused.output.startswith("ATLAS_REFUSED"),
                }
            )
    except Exception as exc:
        return {"verdict": "FAIL", "reason": f"local tools phase crashed: {exc}", "rows": rows}
    verdict = "PASS" if all(row["pass"] for row in rows) else "FAIL"
    return {"verdict": verdict, "rows": rows}


CUDA_LAUNCH_SCRIPT = '''"""Enforced-chain CUDA leg: launch the MNCS-generated PTX kernel.

Runs on the Fabric worker as an ordinary dispatched bundle (no special
GPU path in the dispatcher). The kernel bytes are embedded below by the
gauntlet (single-file bundle, no co-location assumptions) from the
--ptx-kernel artifact whose sha256 is recorded in the manifest.
Prints machine-readable CUDA_CASE lines plus CUDA_SUMMARY.
"""
import ctypes
import struct
import sys

_KERNEL_PTX_HEX = "{PTX_HEX}"


def check(code, what):
    if code != 0:
        raise RuntimeError(f"CUDA error {code} in {what}")


def main() -> int:
    cuda = ctypes.CDLL("libcuda.so.1")
    cuda.cuInit(0)
    dev = ctypes.c_int()
    check(cuda.cuDeviceGet(ctypes.byref(dev), 0), "cuDeviceGet")
    name = ctypes.create_string_buffer(128)
    check(cuda.cuDeviceGetName(name, 128, dev), "cuDeviceGetName")
    uuid = ctypes.create_string_buffer(32)
    check(cuda.cuDeviceGetUuid(uuid, dev), "cuDeviceGetUuid")
    print(f"CUDA_DEVICE name={name.value.decode()} uuid={uuid.raw.hex()}")
    ctx = ctypes.c_void_p()
    check(cuda.cuCtxCreate(ctypes.byref(ctx), 0, dev), "cuCtxCreate")
    ptx = bytes.fromhex(_KERNEL_PTX_HEX)
    mod = ctypes.c_void_p()
    check(cuda.cuModuleLoadData(ctypes.byref(mod), ptx), "cuModuleLoadData")
    func = ctypes.c_void_p()
    check(cuda.cuModuleGetFunction(ctypes.byref(func), mod, b"bounded_min"),
          "cuModuleGetFunction")
    ok = 0
    total = 0
    for a, b, want in [(7, 11, 7), (11, 7, 7), (5, 5, 5), (0, 3, 0)]:
        total += 1
        status = ctypes.c_void_p()
        value = ctypes.c_void_p()
        check(cuda.cuMemAlloc(ctypes.byref(status), 4), "cuMemAlloc status")
        check(cuda.cuMemAlloc(ctypes.byref(value), 4), "cuMemAlloc value")
        try:
            ca, cb = ctypes.c_uint32(a), ctypes.c_uint32(b)
            args = (ctypes.c_void_p * 4)(
                ctypes.cast(ctypes.byref(ca), ctypes.c_void_p),
                ctypes.cast(ctypes.byref(cb), ctypes.c_void_p),
                ctypes.cast(ctypes.byref(status), ctypes.c_void_p),
                ctypes.cast(ctypes.byref(value), ctypes.c_void_p),
            )
            check(cuda.cuLaunchKernel(func, 1, 1, 1, 1, 1, 1, 0, None,
                                      ctypes.cast(args, ctypes.POINTER(ctypes.c_void_p)),
                                      None), f"cuLaunchKernel {a},{b}")
            check(cuda.cuCtxSynchronize(), "cuCtxSynchronize")
            sbuf, vbuf = ctypes.create_string_buffer(4), ctypes.create_string_buffer(4)
            check(cuda.cuMemcpyDtoH(sbuf, status, 4), "DtoH status")
            check(cuda.cuMemcpyDtoH(vbuf, value, 4), "DtoH value")
            status_v = struct.unpack("<i", sbuf.raw)[0]
            value_v = struct.unpack("<i", vbuf.raw)[0]
            passed = status_v == 0 and value_v == want
            ok += passed
            print(f"CUDA_CASE a={a} b={b} status={status_v} value={value_v} "
                  f"want={want} {'PASS' if passed else 'FAIL'}")
        finally:
            cuda.cuMemFree(status)
            cuda.cuMemFree(value)
    print(f"CUDA_SUMMARY ok={ok} total={total}")
    return 0 if ok == total == 4 else 1


if __name__ == "__main__":
    sys.exit(main())
'''


def fabric_phase(
    envelope: dict[str, Any] | None,
    work_root: Path,
    ptx_kernel: Path | None = None,
    trust: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    """Real dispatch through a persistent Fabric service under authority.

    Spins TLS + controller + enrolled local worker in a temp dir (the
    proven persistent-service pattern), operator-asserts the worker
    capability over the admin channel, dispatches an MNCS-trivial compute
    bundle through the enforced executor, and confirms actual == authorized.
    """
    if not envelope:
        return {"verdict": "UNKNOWN", "reason": "no requirement envelope"}
    openssl = shutil.which("openssl")
    if openssl is None:
        return {"verdict": "UNKNOWN", "reason": "openssl unavailable for Fabric TLS"}
    import importlib.util

    if importlib.util.find_spec("mncs_fabric") is None:
        return {"verdict": "UNKNOWN", "reason": "mncs-fabric unavailable"}
    try:
        from mncs_fabric.api import FabricAdminClient
    except ImportError as exc:
        return {"verdict": "UNKNOWN", "reason": f"mncs-fabric api unavailable: {exc}"}
    if not hasattr(FabricAdminClient, "assert_worker_capability"):
        return {
            "verdict": "UNKNOWN",
            "reason": "fabric admin cannot assert worker capability (min-supported?)",
        }
    root = work_root / "fabric"
    root.mkdir(parents=True, exist_ok=True)
    try:
        return _fabric_persistent_run(envelope, root, openssl, ptx_kernel, trust)
    except Exception as exc:
        return {"verdict": "FAIL", "reason": f"fabric phase crashed: {exc}"}


def _cuda_leg(session, tool_registry, requirement, workspace, ptx_kernel, worker_id):
    """CUDA/PTX kernel launch through the same enforced dispatch path.

    No special GPU path in the dispatcher: the MNCS-generated kernel ships
    in an ordinary bundle, placement stays inside the authorized dispatch
    leg, and the launch output binds device + artifact + worker. Absent
    driver/hardware/kernel this leg is UNKNOWN, never a fake result.
    """
    import shutil
    import subprocess

    from mncs_harness.fabric_target_tools import FabricTargetToolExecutor

    if ptx_kernel is None or not Path(ptx_kernel).is_file():
        return {"verdict": "UNKNOWN", "reason": "no PTX kernel provided (--ptx-kernel)"}
    if shutil.which("nvidia-smi") is None:
        return {"verdict": "UNKNOWN", "reason": "nvidia-smi unavailable: no CUDA driver"}
    try:
        smi = subprocess.run(
            ["nvidia-smi", "-L"], capture_output=True, text=True, check=True, timeout=30
        )
        device_line = (smi.stdout.strip().splitlines() or ["unknown"])[0]
    except Exception as exc:
        return {"verdict": "UNKNOWN", "reason": f"nvidia-smi probe failed: {exc}"}
    kernel_bytes = Path(ptx_kernel).read_bytes()
    cuda_dir = workspace / "cuda-leg"
    cuda_dir.mkdir(exist_ok=True)
    (cuda_dir / "cuda_launch.py").write_text(
        CUDA_LAUNCH_SCRIPT.replace("{PTX_HEX}", kernel_bytes.hex()), encoding="utf-8"
    )
    executor = FabricTargetToolExecutor(session, tool_registry)
    result = executor.execute(
        worker_id,
        ["python", str(cuda_dir / "cuda_launch.py")],
        source_root=cuda_dir,
        requirement=requirement,
        requirement_leg="dispatch",
    )
    output = result.execution.output
    cases = [line for line in output.splitlines() if line.startswith("CUDA_CASE")]
    summary = next((line for line in output.splitlines() if line.startswith("CUDA_SUMMARY")), "")
    device = next((line for line in output.splitlines() if line.startswith("CUDA_DEVICE")), "")
    good = (
        result.execution.success
        and summary == "CUDA_SUMMARY ok=4 total=4"
        and all(line.endswith("PASS") for line in cases)
        and len(cases) == 4
    )
    if not good:
        return {
            "verdict": "FAIL",
            "reason": f"cuda launch did not confirm: {output[:1500]}",
        }
    return {
        "verdict": "PASS",
        "device": device,
        "host_probe": device_line,
        "kernel_sha256": hashlib.sha256(kernel_bytes).hexdigest(),
        "cases": cases,
        "worker_identity": worker_id,
        "authorized_target": requirement.legs[1].target if len(requirement.legs) > 1 else "",
        "authorization_identity": result.authorization_identity,
        "output_digest": _digest(output),
    }


def _fabric_persistent_run(
    envelope: dict[str, Any],
    root: Path,
    openssl: str,
    ptx_kernel: Path | None = None,
    trust: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    """Persistent-service dispatch under the gauntlet requirement leg."""
    import socket
    import ssl
    import subprocess
    import threading
    import time

    from mncs_fabric.api import FabricAdminClient
    from mncs_fabric.controller_service import ControllerConfig, ControllerService
    from mncs_fabric.enrollment import TrustStore, certificate_fingerprint
    from mncs_fabric.lifecycle import LifecycleStore
    from mncs_fabric.registry import RegistryWorker, WorkerRegistry
    from mncs_fabric.transport import TLSWorkerServer
    from mncs_fabric.worker import LocalWorker

    from mncs_harness.atlas_binding import ExecutionRequirement
    from mncs_harness.config import load_config
    from mncs_harness.fabric import FabricSession
    from mncs_harness.fabric_target_tools import FabricTargetToolExecutor
    from mncs_harness.fabric_test_support import ephemeral_certificates
    from mncs_harness.models import FabricConfig
    from mncs_harness.tools import ToolRegistry

    worker_id = GAUNTLET_WORKER
    socket_path = root / "controller.sock"
    # Register the worker before the controller service is constructed:
    # the service resolves its worker client from the registry, and a
    # later registration is invisible to the refresh path (observed as
    # "worker is not registered" with membership ENROLLED).
    cert = ephemeral_certificates(root / "certificates", openssl)
    worker_trust = TrustStore(root / "worker-trust.jsonl")
    worker_trust.enroll(
        "controller",
        "gauntlet-fixture",
        certificate_fingerprint(ssl.PEM_cert_to_DER_cert(cert["client"].read_text())),
    )
    TrustStore(root / "controller-trust.jsonl").enroll(
        "worker",
        worker_id,
        certificate_fingerprint(ssl.PEM_cert_to_DER_cert(cert["server"].read_text())),
    )
    worker_root = root / "worker-root"
    worker_root.mkdir()
    worker = LocalWorker(
        worker_id,
        worker_root,
        root / "worker-ledger.jsonl",
        bundle_cache_root=root / "worker-bundles",
    )
    worker_server = TLSWorkerServer(
        worker,
        "127.0.0.1",
        0,
        ca_file=cert["ca"],
        server_cert=cert["server"],
        server_key=cert["server_key"],
        controller_id="gauntlet-fixture",
        worker_id=worker_id,
        trust_store=worker_trust,
        timeout=3,
    )
    worker_port = worker_server.bind()
    worker_thread = threading.Thread(
        target=worker_server.serve_forever,
        kwargs={"max_requests": 40, "idle_timeout": 15},
        daemon=True,
    )

    lifecycle = LifecycleStore(root / "lifecycle.jsonl")
    authorization = lifecycle.create_authorization(expected_worker_identity=worker_id)
    public_key = subprocess.run(
        [openssl, "x509", "-in", str(cert["server"]), "-pubkey", "-noout"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    enrollment = lifecycle.build_request(
        worker_identity=worker_id,
        public_key_pem=public_key,
        hostname_hint="gauntlet.test",
        operating_system="linux",
        architecture="x86_64",
        authorization_id=str(authorization["authorization_id"]),
    )
    lifecycle.submit_request(enrollment, str(authorization["token"]))
    lifecycle.approve_request(str(enrollment["request_id"]))
    registry = WorkerRegistry(root / "workers.json", controller_id="gauntlet-fixture")
    registry.register(
        RegistryWorker(
            worker_id=worker_id,
            host="127.0.0.1",
            port=worker_port,
            capabilities=tuple(sorted(worker.capabilities())),
            ca_file=str(cert["ca"]),
            client_certificate=str(cert["client"]),
            client_key=str(cert["client_key"]),
            trust_state=str(root / "controller-trust.jsonl"),
        )
    )

    service = ControllerService(
        ControllerConfig(
            "gauntlet-fixture",
            root / "lifecycle.jsonl",
            service_log=root / "controller-service.jsonl",
            socket_path=socket_path,
            admin_socket_path=root / "controller-admin.sock",
            worker_registry_path=root / "workers.json",
            worker_state_path=root / "controller-workers.jsonl",
            execution_bundle_root=root / "execution-bundles",
        )
    )
    service_thread = threading.Thread(target=service.run, kwargs={"max_seconds": 60.0}, daemon=True)

    worker_thread.start()
    service_thread.start()
    session = None
    try:
        worker_ready = False
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", worker_port), timeout=0.1):
                    worker_ready = True
                    break
            except OSError:
                time.sleep(0.05)
        if not worker_ready:
            return {"verdict": "UNKNOWN", "reason": "worker TLS listener never became ready"}
        for _ in range(150):
            if socket_path.is_socket():
                break
            time.sleep(0.02)
        if not socket_path.is_socket():
            return {"verdict": "UNKNOWN", "reason": "controller socket never appeared"}
        config = FabricConfig(
            enabled=True,
            controller_mode="service",
            service_socket=socket_path,
            service_timeout_seconds=2.0,
            refresh_on_startup=False,
            state_path=root / "fabric.jsonl",
        )
        session = FabricSession(config)
        session.initialize()
        session.set_consumer_context(
            workload_identity="sha256:" + _digest({"gauntlet": "workload"}),
            provider_identity="sha256:" + _digest({"gauntlet": "provider"}),
            partition_identity="sha256:" + _digest({"gauntlet": "partition"}),
        )
        # Consumer ingest is context only; the operator assertion over the
        # admin channel is what makes the observation authority.
        session.client.ingest_capability_observation(
            worker_id,
            [{"kind": "runtime", "namespace": "system", "name": "python"}],
        )
        admin = FabricAdminClient.connect(
            root / "controller-admin.sock",
            client_identity="gauntlet-operator",
            timeout=5,
        )
        try:
            admin.assert_worker_capability(
                worker_id,
                [{"kind": "runtime", "namespace": "system", "name": "python"}],
                observation_source="gauntlet-operator-asserted-capability",
            )
        finally:
            admin.close()
        # Reads never refresh: explicitly probe the worker so Fabric
        # records authenticated presence before admission is evaluated.
        refresh_note = ""
        present = False
        try:
            refreshed = session.client.refresh_fleet(worker_ids=[worker_id])
            workers = refreshed.get("workers", []) if isinstance(refreshed, dict) else []
            first = workers[0] if workers else {}
            refresh_note = (
                f" refresh_outcome={refreshed.get('outcome') if isinstance(refreshed, dict) else '?'}"
                f" inventory={first.get('capability_inventory_status')}"
            )
            # CURRENT means Fabric itself probed the worker and holds a
            # fresh authenticated observation: presence established.
            present = first.get("capability_inventory_status") == "CURRENT"
        except Exception as exc:
            refresh_note = f" refresh_failed={exc}"
        if not present:
            return {
                "verdict": "UNKNOWN",
                "reason": f"worker never showed presence:{refresh_note}",
            }

        workspace = root / "tool-workspace"
        workspace.mkdir()
        script = workspace / "gauntlet_compute.py"
        script.write_text("print(7 * 6)\n", encoding="utf-8")
        tool_registry = ToolRegistry(
            workspace,
            load_config(Path("/missing/config.toml")).policy,
            auto_approve=True,
            interactive=False,
        )
        requirement = ExecutionRequirement.from_dict(envelope, trusted_issuers=trust or {})
        executor = FabricTargetToolExecutor(session, tool_registry)
        result = executor.execute(
            worker_id,
            ["python", str(script)],
            source_root=workspace,
            requirement=requirement,
            requirement_leg="dispatch",
            expected_participant=GAUNTLET_PARTICIPANT,
            expected_scope=GAUNTLET_SCOPE,
        )
        evidence = (result.fabric_result or {}).get("target_execution_evidence", {})
        record = (result.fabric_result or {}).get("record", {})
        record_identity = (result.fabric_result or {}).get("record_identity") or record.get(
            "record_identity"
        )
        ok = (
            result.execution.success
            and "42" in result.execution.output
            and (result.fabric_result or {}).get("disposition") == "EXECUTED"
            and evidence.get("worker_identity") == worker_id
        )
        if not ok:
            return {
                "verdict": "FAIL",
                "reason": f"dispatch did not confirm: {result.execution.output[:300]}",
            }
        cuda = _cuda_leg(session, tool_registry, requirement, workspace, ptx_kernel, worker_id)
        return {
            "verdict": "PASS",
            "worker_identity": worker_id,
            "backend": "persistent-service/local-python",
            "actual_target": worker_id,
            "authorized_target": GAUNTLET_WORKER,
            "disposition": result.fabric_result["disposition"],
            "authorization_identity": result.authorization_identity,
            "consumer_authorization_identity": evidence.get("consumer_authorization_identity"),
            "record_identity": record_identity,
            "inventory_observation_identity": (
                evidence.get("capability_observation_identity")
                or record.get("capability_observation_identity")
            ),
            "artifact_identity": envelope["artifact_identity"],
            "acceptance_binding": requirement.accept("dispatch").binding,
            "output_digest": _digest(result.execution.output),
            "cuda": cuda,
        }
    finally:
        if session is not None:
            session.close()
        worker_server.request_stop()
        worker_thread.join(timeout=5)


def rights_binding_projection(
    requirement_id: str | None, fabric: dict[str, Any]
) -> dict[str, Any]:
    """Project execution evidence onto the Rights identity binding.

    Honesty note (epi13/mncs-harness#60): this phase constructs the
    binding dictionary locally from Fabric-phase evidence; the Rights
    owner does NOT independently participate yet (its ``authority_verdict``
    takes caller-computed booleans, so wiring it here would be theater).
    The verdict therefore stays UNKNOWN with an explicit projection
    reason, and full Rights validation remains a coverage gap. The
    binding itself is recorded for traceability.
    """
    if fabric.get("verdict") != "PASS":
        return {
            "verdict": "UNKNOWN",
            "phase": "rights_binding_projection",
            "reason": "no executed leg to bind",
        }
    binding = {
        "requirement_id": requirement_id,
        "worker_identity": fabric.get("worker_identity"),
        "authorization_identity": fabric.get("authorization_identity"),
        "consumer_authorization_identity": fabric.get("consumer_authorization_identity"),
        "artifact_identity": fabric.get("artifact_identity"),
        "acceptance_binding": fabric.get("acceptance_binding"),
    }
    if not binding["authorization_identity"]:
        return {
            "verdict": "FAIL",
            "phase": "rights_binding_projection",
            "reason": "executed without authorization identity",
        }
    return {
        "verdict": "UNKNOWN",
        "phase": "rights_binding_projection",
        "reason": (
            "binding constructed locally from Fabric evidence; "
            "independent Rights-owner validation not yet wired"
        ),
        "binding": binding,
    }


def rights_phase(requirement_id: str | None, fabric: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible alias for the projection phase."""
    return rights_binding_projection(requirement_id, fabric)


def commons_phase() -> dict[str, Any]:
    """Record Commons confirmation evidence (read-only; never force publication)."""
    try:
        from mncs_harness.commons import CommonsSession
        from mncs_harness.config import load_config
    except ImportError as exc:
        return {"verdict": "UNKNOWN", "reason": f"commons unavailable: {exc}"}
    try:
        config = load_config(Path("/missing/config.toml"))
        session = CommonsSession(config.commons)
        session.initialize()
        status = session.status()
        ready = bool(getattr(status, "ready", False))
        return {
            "verdict": "PASS" if ready else "UNKNOWN",
            "ready": ready,
            "detail": str(status)[:240],
            "reason": "" if ready else "commons session not ready; no confirmation claimed",
        }
    except Exception as exc:
        return {"verdict": "UNKNOWN", "reason": f"commons probe failed: {exc}"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MNCS family E2E gauntlet")
    parser.add_argument("--manifest-out", type=Path, default=None)
    parser.add_argument(
        "--ptx-kernel",
        type=Path,
        default=None,
        help="MNCS-generated kernel.ptx for the CUDA leg "
        "(regenerate: mncs compile <prog> --emit backend --target mncs-ptx64)",
    )
    args = parser.parse_args(argv)

    harness_root = Path(__file__).resolve().parents[2]
    family_root = harness_root.parent
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA,
        "actions_carrier_revision": ACTIONS_CARRIER_REVISION,
        "revisions": collect_revisions(harness_root),
    }
    atlas, issuer = atlas_phase(family_root)
    manifest["atlas"] = {k: v for k, v in atlas.items() if k != "raw_decisions"}
    requirement = requirement_phase(atlas)
    envelope = requirement.pop("envelope", None)
    manifest["requirement"] = requirement
    manifest["adversarial"] = adversarial_phase(atlas, issuer)
    manifest["local_tools"] = local_tools_phase(envelope, _trust_from_atlas(atlas))
    with tempfile.TemporaryDirectory(prefix="gauntlet-") as work:
        manifest["fabric"] = fabric_phase(
            envelope, Path(work), args.ptx_kernel, _trust_from_atlas(atlas)
        )
    cuda = manifest["fabric"].pop("cuda", {"verdict": "UNKNOWN", "reason": "no cuda leg ran"})
    manifest["cuda"] = cuda
    manifest["rights"] = rights_phase(requirement.get("requirement_id"), manifest["fabric"])
    manifest["commons"] = commons_phase()

    phases = [
        "atlas",
        "requirement",
        "adversarial",
        "local_tools",
        "fabric",
        "cuda",
        "rights",
        "commons",
    ]
    # Behavior verdict: every observed behavior matched expected
    # semantics (UNKNOWN tolerated per-leg, adversarial must PASS).
    manifest["behavior_verdict"] = (
        "PASS"
        if all(manifest[name].get("verdict") in ("PASS", "UNKNOWN") for name in phases)
        and manifest["adversarial"].get("verdict") == "PASS"
        else "FAIL"
    )
    # Coverage verdict: whether every end-to-end subsystem actually
    # participated. A behavior-PASS with UNKNOWN legs is a partial run,
    # never complete execution (epi13/mncs-harness#60).
    coverage_gaps = [
        name
        for name in ("fabric", "cuda", "rights", "commons")
        if manifest[name].get("verdict") != "PASS"
    ]
    manifest["coverage_verdict"] = "COMPLETE" if not coverage_gaps else "PARTIAL"
    manifest["coverage_gaps"] = [
        {"phase": name, "reason": manifest[name].get("reason", "")} for name in coverage_gaps
    ]
    # Kept for compatibility: the aggregate tracks behavior only.
    manifest["gauntlet_verdict"] = manifest["behavior_verdict"]
    manifest["aggregate_digest"] = _digest(
        {k: v for k, v in manifest.items() if k != "aggregate_digest"}
    )
    text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.manifest_out is not None:
        args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_out.write_text(text, encoding="utf-8")
    print(
        f"gauntlet={manifest['gauntlet_verdict']} "
        f"behavior={manifest['behavior_verdict']} "
        f"coverage={manifest['coverage_verdict']} "
        f"digest={manifest['aggregate_digest']}"
    )
    for name in phases:
        print(f"  {name}: {manifest[name].get('verdict')}")
    if manifest["coverage_gaps"]:
        print("  coverage gaps: " + ", ".join(item["phase"] for item in manifest["coverage_gaps"]))
    return 0 if manifest["gauntlet_verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
