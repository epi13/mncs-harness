"""Persistent Windows worker commissioning for the local harness.

Commissioning is an operator action.  SSH/SCP are used only to provision the
explicitly named Windows host; inference and runtime evidence subsequently flow
through Fabric's mutually authenticated transport.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Sequence

DEFAULT_CONTROLLER_ID = "epi13-local-harness"
DEFAULT_WORKER_ID = "collamore02-windows"
DEFAULT_PORT = 7443


class CommissioningError(RuntimeError):
    """Persistent worker commissioning could not complete safely."""


def add_windows_commission_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ssh-host", required=True)
    parser.add_argument("--ssh-user", required=True)
    parser.add_argument("--ssh-key", type=Path, required=True)
    parser.add_argument("--expected-hostname", required=True)
    parser.add_argument("--worker-id", default=DEFAULT_WORKER_ID)
    parser.add_argument("--controller-id", default=DEFAULT_CONTROLLER_ID)
    parser.add_argument("--worker-host", help="Fabric endpoint; defaults to --ssh-host")
    parser.add_argument("--worker-port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--windows-python",
        default="python",
        help="Python executable on Windows; must be the GPU/Fabric runtime",
    )
    parser.add_argument(
        "--remote-root",
        help="Persistent Windows worker root; defaults under the SSH user's profile",
    )
    parser.add_argument(
        "--enrollment-root",
        type=Path,
        help="Local persistent PKI/trust root",
    )
    parser.add_argument("--certificate-days", type=int, default=365)
    parser.add_argument(
        "--rotate-enrollment",
        action="store_true",
        help="Explicitly replace the existing worker enrollment and certificates",
    )
    parser.add_argument("--gpu-reserve-mib", type=int, default=512)
    parser.add_argument(
        "--fallback-to-local",
        action=argparse.BooleanOptionalAction,
        default=True,
    )


def _local_enrollment_root(worker_id: str) -> Path:
    return (
        Path.home()
        / ".local"
        / "state"
        / "epi13-local-harness"
        / "fabric-enrollment"
        / worker_id
    )


def _ssh_base(host: str, user: str, key: Path) -> list[str]:
    return [
        "ssh",
        "-i",
        str(key),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PreferredAuthentications=publickey",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=5",
        f"{user}@{host}",
    ]


def _scp_base(key: Path) -> list[str]:
    return [
        "scp",
        "-i",
        str(key),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PreferredAuthentications=publickey",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=5",
    ]


def _encoded_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def _run_powershell(
    *,
    host: str,
    user: str,
    key: Path,
    script: str,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    prelude = "$ProgressPreference='SilentlyContinue';$ErrorActionPreference='Stop';"
    command = _ssh_base(host, user, key) + [
        "powershell",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        _encoded_powershell(prelude + script),
    ]
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise CommissioningError("Windows bootstrap command timed out") from exc


def _powershell_json(result: subprocess.CompletedProcess[str], label: str) -> dict[str, Any]:
    if result.returncode != 0:
        diagnostic = (result.stderr or result.stdout or "unknown error")[-2000:]
        raise CommissioningError(f"{label} failed: {diagnostic.strip()}")
    for line in reversed(result.stdout.splitlines()):
        line = line.strip()
        if not line or line.startswith("#< CLIXML"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise CommissioningError(f"{label} returned no JSON result")


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run_checked(command: Sequence[str], *, label: str, cwd: Path | None = None) -> None:
    try:
        result = subprocess.run(
            list(command),
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CommissioningError(f"{label} could not run: {exc}") from exc
    if result.returncode != 0:
        diagnostic = (result.stderr or result.stdout or "unknown error")[-2000:]
        raise CommissioningError(f"{label} failed: {diagnostic.strip()}")


def _certificate_fingerprint(path: Path) -> str:
    from mncs_fabric.enrollment import certificate_fingerprint

    pem = path.read_text(encoding="ascii")
    der = ssl.PEM_cert_to_DER_cert(pem)
    return certificate_fingerprint(der)


def _pki_files(root: Path) -> dict[str, Path]:
    pki = root / "pki"
    trust = root / "trust"
    return {
        "ca": pki / "ca.pem",
        "ca_key": pki / "ca.key",
        "controller": pki / "controller.pem",
        "controller_key": pki / "controller.key",
        "worker": pki / "worker.pem",
        "worker_key": pki / "worker.key",
        "controller_trust": trust / "controller-trust.jsonl",
        "worker_trust": trust / "worker-trust.jsonl",
    }


def _complete_enrollment(files: dict[str, Path]) -> bool:
    return all(path.is_file() for path in files.values())


def _generate_enrollment(
    root: Path,
    *,
    controller_id: str,
    worker_id: str,
    days: int,
    rotate: bool,
) -> dict[str, Path]:
    if days < 1 or days > 3650:
        raise CommissioningError("certificate-days must be between 1 and 3650")
    files = _pki_files(root)
    if root.exists() and rotate:
        shutil.rmtree(root)
    if root.exists() and not _complete_enrollment(files):
        raise CommissioningError(
            f"persistent enrollment is incomplete: {root}; use --rotate-enrollment to replace it"
        )
    if _complete_enrollment(files):
        return files

    if shutil.which("openssl") is None:
        raise CommissioningError("openssl is required to create the persistent Fabric enrollment")
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    (root / "pki").mkdir(mode=0o700, exist_ok=True)
    (root / "trust").mkdir(mode=0o700, exist_ok=True)
    pki = root / "pki"

    _run_checked(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:3072",
            "-nodes",
            "-keyout",
            str(files["ca_key"]),
            "-out",
            str(files["ca"]),
            "-days",
            str(days),
            "-sha256",
            "-subj",
            "/CN=Epi13 Local Harness Fabric CA",
        ],
        label="Fabric CA creation",
    )
    for name, identity in (("controller", controller_id), ("worker", worker_id)):
        key = files[f"{name}_key"]
        csr = pki / f"{name}.csr"
        cert = files[name]
        _run_checked(
            [
                "openssl",
                "req",
                "-newkey",
                "rsa:3072",
                "-nodes",
                "-keyout",
                str(key),
                "-out",
                str(csr),
                "-subj",
                f"/CN={identity}",
            ],
            label=f"{name} key creation",
        )
        sign = [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(csr),
            "-CA",
            str(files["ca"]),
            "-CAkey",
            str(files["ca_key"]),
            "-out",
            str(cert),
            "-days",
            str(days),
            "-sha256",
        ]
        if name == "controller":
            sign.append("-CAcreateserial")
        else:
            sign.extend(["-CAserial", str(pki / "ca.srl")])
        _run_checked(sign, label=f"{name} certificate signing")
        csr.unlink(missing_ok=True)

    from mncs_fabric.enrollment import TrustStore

    controller_trust = TrustStore(files["controller_trust"])
    controller_trust.enroll(
        "worker",
        worker_id,
        _certificate_fingerprint(files["worker"]),
        metadata={"purpose": "epi13-local-harness-persistent-worker"},
    )
    worker_trust = TrustStore(files["worker_trust"])
    worker_trust.enroll(
        "controller",
        controller_id,
        _certificate_fingerprint(files["controller"]),
        metadata={"purpose": "epi13-local-harness-persistent-controller"},
    )
    for path in (files["ca_key"], files["controller_key"], files["worker_key"]):
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return files


def _fabric_package_archive(destination: Path) -> tuple[Path, str]:
    try:
        import mncs_fabric
    except ImportError as exc:
        raise CommissioningError("mncs-fabric must be installed before commissioning") from exc
    package = Path(mncs_fabric.__file__).resolve().parent
    version = str(getattr(mncs_fabric, "__version__", "unknown"))
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            archive.write(path, Path("mncs_fabric") / path.relative_to(package))
    return destination, version


def _scp_file(
    *,
    host: str,
    user: str,
    key: Path,
    source: Path,
    destination: str,
) -> None:
    command = _scp_base(key) + [str(source), f"{user}@{host}:{destination}"]
    _run_checked(command, label=f"stage {source.name}")


def _preflight_windows(
    *,
    host: str,
    user: str,
    key: Path,
    expected_hostname: str,
    python: str,
) -> dict[str, Any]:
    if not key.expanduser().is_file():
        raise CommissioningError(f"SSH key does not exist: {key.expanduser()}")
    result = _run_powershell(
        host=host,
        user=user,
        key=key.expanduser(),
        script=(
            f"$python={_ps_quote(python)};"
            "$py=& $python -c 'import json,sys; "
            "print(json.dumps({\"python\":sys.executable,\"version\":sys.version.split()[0]}))';"
            "$value=[ordered]@{hostname=$env:COMPUTERNAME;python=($py | Select-Object -Last 1)};"
            "$value | ConvertTo-Json -Compress"
        ),
    )
    value = _powershell_json(result, "Windows preflight")
    observed = str(value.get("hostname", ""))
    if observed.casefold() != expected_hostname.casefold():
        raise CommissioningError(
            f"Windows hostname mismatch: expected {expected_hostname!r}, observed {observed!r}"
        )
    python_record = value.get("python")
    if isinstance(python_record, str):
        try:
            value["python"] = json.loads(python_record)
        except json.JSONDecodeError:
            pass
    return value


def _prepare_remote_root(
    *,
    host: str,
    user: str,
    key: Path,
    remote_root: str,
) -> None:
    root = _ps_quote(remote_root)
    script = (
        f"$root={root};"
        "$dirs=@($root,\"$root\\src\",\"$root\\certs\",\"$root\\trust\","
        "\"$root\\state\",\"$root\\logs\",\"$root\\bundle-cache\","
        "\"$root\\empty-bundle\");"
        "foreach($dir in $dirs){New-Item -ItemType Directory -Force -Path $dir | Out-Null};"
        "if(Test-Path \"$root\\src\\mncs_fabric\"){"
        "Remove-Item -Recurse -Force \"$root\\src\\mncs_fabric\"};"
        "@{outcome='PASS';root=$root}|ConvertTo-Json -Compress"
    )
    result = _run_powershell(host=host, user=user, key=key, script=script)
    _powershell_json(result, "prepare persistent Windows worker root")


def _stage_remote(
    *,
    host: str,
    user: str,
    key: Path,
    remote_root: str,
    files: dict[str, Path],
    package_archive: Path,
) -> None:
    remote = remote_root.replace("\\", "/").rstrip("/")
    _scp_file(
        host=host,
        user=user,
        key=key,
        source=package_archive,
        destination=f"{remote}/mncs-fabric-package.zip",
    )
    for name, remote_name in (
        ("ca", "ca.pem"),
        ("worker", "worker.pem"),
        ("worker_key", "worker.key"),
    ):
        _scp_file(
            host=host,
            user=user,
            key=key,
            source=files[name],
            destination=f"{remote}/certs/{remote_name}",
        )
    _scp_file(
        host=host,
        user=user,
        key=key,
        source=files["worker_trust"],
        destination=f"{remote}/trust/worker-trust.jsonl",
    )
    root = _ps_quote(remote_root)
    script = (
        f"$root={root};"
        "Expand-Archive -Force -Path \"$root\\mncs-fabric-package.zip\" "
        "-DestinationPath \"$root\\src\";"
        "Remove-Item -Force \"$root\\mncs-fabric-package.zip\";"
        "@{outcome='PASS'}|ConvertTo-Json -Compress"
    )
    result = _run_powershell(host=host, user=user, key=key, script=script, timeout=60)
    _powershell_json(result, "stage Fabric package")


def _start_remote_worker(
    *,
    host: str,
    user: str,
    key: Path,
    remote_root: str,
    python: str,
    worker_id: str,
    controller_id: str,
    port: int,
) -> dict[str, Any]:
    root = _ps_quote(remote_root)
    script = (
        f"$root={root};$python={_ps_quote(python)};"
        "$state=\"$root\\state\\launcher.json\";"
        "if(Test-Path $state){"
        "$old=Get-Content -Raw $state | ConvertFrom-Json;"
        "$proc=Get-Process -Id $old.pid -ErrorAction SilentlyContinue;"
        "if($proc){Stop-Process -Id $old.pid -Force;Start-Sleep -Milliseconds 500}};"
        "$env:PYTHONPATH=\"$root\\src\";"
        "$arguments=@('-m','mncs_fabric','worker','serve',"
        f"'--worker-id',{_ps_quote(worker_id)},'--controller-id',{_ps_quote(controller_id)},"
        "'--bundle-root',\"$root\\empty-bundle\","
        "'--state',\"$root\\state\\worker-ledger.jsonl\","
        "'--trust-state',\"$root\\trust\\worker-trust.jsonl\","
        "'--ca',\"$root\\certs\\ca.pem\","
        "'--certificate',\"$root\\certs\\worker.pem\","
        "'--key',\"$root\\certs\\worker.key\","
        "'--host','0.0.0.0','--port',"
        f"'{port}','--timeout','30','--max-requests','1000000',"
        "'--max-concurrent-connections','1','--bundle-cache',"
        "\"$root\\bundle-cache\");"
        "$proc=Start-Process -FilePath $python -ArgumentList $arguments "
        "-WorkingDirectory $root -WindowStyle Hidden -PassThru "
        "-RedirectStandardOutput \"$root\\logs\\worker.stdout.log\" "
        "-RedirectStandardError \"$root\\logs\\worker.stderr.log\";"
        "$record=[ordered]@{schema_version='epi13-local-harness.fabric-launcher.v0.1';"
        f"pid=$proc.Id;worker_id={_ps_quote(worker_id)};controller_id={_ps_quote(controller_id)};"
        "started_at=(Get-Date).ToUniversalTime().ToString('o')};"
        "$record | ConvertTo-Json -Compress | Set-Content -Encoding UTF8 $state;"
        "$record | ConvertTo-Json -Compress"
    )
    result = _run_powershell(host=host, user=user, key=key, script=script, timeout=30)
    return _powershell_json(result, "start persistent Fabric worker")


def _wait_for_port(host: str, port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    last: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError as exc:
            last = exc
            time.sleep(0.5)
    raise CommissioningError(f"Fabric worker did not open {host}:{port}: {last}")


def _remote_ollama_models(
    *,
    host: str,
    user: str,
    key: Path,
) -> tuple[str, ...]:
    script = (
        "$response=Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:11434/api/tags' "
        "-TimeoutSec 5;"
        "$names=@($response.models | ForEach-Object {$_.name});"
        "@{outcome='PASS';models=$names}|ConvertTo-Json -Compress"
    )
    result = _run_powershell(host=host, user=user, key=key, script=script, timeout=15)
    value = _powershell_json(result, "worker-local Ollama check")
    models = value.get("models", [])
    if isinstance(models, str):
        return (models,)
    if not isinstance(models, list):
        return ()
    return tuple(str(item) for item in models)


def commission_windows(args: argparse.Namespace) -> int:
    if not args.worker_id or not args.controller_id:
        raise CommissioningError("worker and controller identities are required")
    if args.worker_port < 1 or args.worker_port > 65535:
        raise CommissioningError("worker port must be between 1 and 65535")
    ssh_key = args.ssh_key.expanduser().resolve()
    worker_host = args.worker_host or args.ssh_host
    remote_root = args.remote_root or f"C:/Users/{args.ssh_user}/mncs-fabric-worker"
    enrollment_root = (
        args.enrollment_root.expanduser().resolve()
        if args.enrollment_root
        else _local_enrollment_root(args.worker_id)
    )

    preflight = _preflight_windows(
        host=args.ssh_host,
        user=args.ssh_user,
        key=ssh_key,
        expected_hostname=args.expected_hostname,
        python=args.windows_python,
    )
    files = _generate_enrollment(
        enrollment_root,
        controller_id=args.controller_id,
        worker_id=args.worker_id,
        days=args.certificate_days,
        rotate=args.rotate_enrollment,
    )
    _prepare_remote_root(
        host=args.ssh_host,
        user=args.ssh_user,
        key=ssh_key,
        remote_root=remote_root,
    )
    with tempfile.TemporaryDirectory(prefix="elh-fabric-commission-") as directory:
        package_archive, fabric_version = _fabric_package_archive(
            Path(directory) / "mncs-fabric-package.zip"
        )
        _stage_remote(
            host=args.ssh_host,
            user=args.ssh_user,
            key=ssh_key,
            remote_root=remote_root,
            files=files,
            package_archive=package_archive,
        )
    launcher = _start_remote_worker(
        host=args.ssh_host,
        user=args.ssh_user,
        key=ssh_key,
        remote_root=remote_root,
        python=args.windows_python,
        worker_id=args.worker_id,
        controller_id=args.controller_id,
        port=args.worker_port,
    )
    _wait_for_port(worker_host, args.worker_port)

    from .fabric_profile import configure_remote

    configure_args = argparse.Namespace(
        config=args.config,
        controller_id=args.controller_id,
        worker_id=args.worker_id,
        host=worker_host,
        port=args.worker_port,
        ca_file=files["ca"],
        client_certificate=files["controller"],
        client_key=files["controller_key"],
        trust_state=files["controller_trust"],
        capability=None,
        accelerator_role=None,
        local_role=None,
        gpu_reserve_mib=args.gpu_reserve_mib,
        fallback_to_local=args.fallback_to_local,
    )
    configure_remote(configure_args)

    from .config import load_config
    from .fabric import FabricSession

    config = load_config(args.config)
    session = FabricSession(config.fabric)
    session.initialize()
    status = session.status()
    worker = next(
        (item for item in status.workers if item.get("worker_id") == args.worker_id),
        None,
    )
    runtime = (worker or {}).get("runtime_observation") or {}
    ollama_models: tuple[str, ...]
    try:
        ollama_models = _remote_ollama_models(
            host=args.ssh_host,
            user=args.ssh_user,
            key=ssh_key,
        )
    except CommissioningError:
        ollama_models = ()
    routed_names = {
        config.models[role].name
        for role in ("e4b", "coder", "reviewer")
        if role in config.models and config.models[role].provider == "fabric"
    }
    summary = {
        "outcome": (
            "PASS"
            if status.state == "available"
            and (worker or {}).get("availability") == "AVAILABLE"
            and runtime.get("runtime_execution_probe") == "PASS"
            else "UNKNOWN"
        ),
        "controller_id": args.controller_id,
        "worker_id": args.worker_id,
        "worker_host": worker_host,
        "worker_port": args.worker_port,
        "remote_root": remote_root,
        "enrollment_root": str(enrollment_root),
        "fabric_version": fabric_version,
        "windows_preflight": preflight,
        "launcher_pid": launcher.get("pid"),
        "fabric_state": status.state,
        "worker_availability": (worker or {}).get("availability"),
        "cuda_execution_probe": runtime.get("runtime_execution_probe"),
        "cuda_precision_probes": runtime.get("precision_probes", {}),
        "ollama_models": list(ollama_models),
        "missing_routed_models": sorted(routed_names - set(ollama_models)),
        "claim_boundary": (
            "operator-controlled persistent commissioning; authenticated worker and runtime "
            "observations are not hardware attestation or independent assurance"
        ),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["outcome"] == "PASS" else 2
