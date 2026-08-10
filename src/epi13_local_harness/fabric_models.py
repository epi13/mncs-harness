"""Worker-local Ollama model provisioning for explicit Windows Fabric workers.

Only a tiny command file and model names cross the controller/worker bootstrap
channel.  ``ollama pull`` runs on the worker itself, so model blobs are fetched
by the worker directly from its configured Ollama registry rather than copied
through Fabric or over the controller LAN connection.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import fabric_commission as _commission
from .fabric_profile import DEFAULT_ACCELERATOR_ROLES

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_INSTALLER_NAME = "install-models.cmd"


def add_windows_model_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ssh-host", required=True)
    parser.add_argument("--ssh-user", required=True)
    parser.add_argument("--ssh-key", type=Path, required=True)
    parser.add_argument("--expected-hostname", required=True)
    parser.add_argument(
        "--remote-root",
        help="Persistent Windows worker root; defaults under the SSH user's profile",
    )
    parser.add_argument(
        "--model",
        action="append",
        help="Explicit Ollama model tag; repeat for multiple models. Defaults to routed Fabric roles.",
    )
    parser.add_argument(
        "--stage-only",
        action="store_true",
        help="Stage install-models.cmd on the worker without running it",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=14400.0,
        help="Maximum time for worker-local model pulls; default: 4 hours",
    )


def _validate_models(models: list[str]) -> tuple[str, ...]:
    unique: list[str] = []
    for raw in models:
        model = raw.strip()
        if not _MODEL_RE.fullmatch(model):
            raise ValueError(
                f"unsupported Ollama model tag {raw!r}; allowed characters are letters, numbers, . _ : / -"
            )
        if model not in unique:
            unique.append(model)
    if not unique:
        raise ValueError("no Ollama models were selected for worker-local provisioning")
    return tuple(unique)


def _configured_models(config_path: Path | None) -> tuple[str, ...]:
    from .config import load_config

    config = load_config(config_path)
    routed = [
        model.name
        for model in config.models.values()
        if getattr(model, "provider", None) == "fabric"
    ]
    if routed:
        return _validate_models(routed)
    defaults = [
        config.models[role].name
        for role in DEFAULT_ACCELERATOR_ROLES
        if role in config.models
    ]
    return _validate_models(defaults)


def _render_installer(models: tuple[str, ...]) -> str:
    lines = [
        "@echo off",
        "setlocal EnableExtensions DisableDelayedExpansion",
        "where ollama >nul 2>&1",
        "if errorlevel 1 (",
        "  echo ERROR: ollama.exe is not available in PATH on this worker. 1>&2",
        "  exit /b 2",
        ")",
        "ollama list >nul 2>&1",
        "if errorlevel 1 (",
        "  echo ERROR: Ollama is installed but its local service is not reachable. 1>&2",
        "  exit /b 3",
        ")",
        "echo Worker-local Ollama model provisioning",
        "echo Model blobs will be downloaded by this worker, not copied from the controller.",
    ]
    for model in models:
        lines.extend(
            [
                f"echo ==== Pulling {model} ====",
                f"ollama pull {model}",
                "if errorlevel 1 exit /b %errorlevel%",
            ]
        )
    lines.extend(["echo ==== Installed models ====", "ollama list", "exit /b 0", ""])
    return "\r\n".join(lines)


def _verify_host(*, host: str, user: str, key: Path, expected_hostname: str) -> dict[str, Any]:
    result = _commission._run_powershell(
        host=host,
        user=user,
        key=key,
        script=(
            "$ollama=Get-Command ollama -ErrorAction SilentlyContinue;"
            "$value=[ordered]@{hostname=$env:COMPUTERNAME;"
            "ollama_command=if($ollama){$ollama.Source}else{$null}};"
            "$value | ConvertTo-Json -Compress"
        ),
    )
    value = _commission._powershell_json(result, "Windows Ollama provisioning preflight")
    observed = str(value.get("hostname", ""))
    if observed.casefold() != expected_hostname.casefold():
        raise RuntimeError(
            f"Windows hostname mismatch: expected {expected_hostname!r}, observed {observed!r}"
        )
    return value


def _stage_installer(
    *,
    host: str,
    user: str,
    key: Path,
    remote_root: str,
    models: tuple[str, ...],
) -> str:
    root = _commission._ps_quote(remote_root)
    result = _commission._run_powershell(
        host=host,
        user=user,
        key=key,
        script=(
            f"$root={root};"
            "New-Item -ItemType Directory -Force -Path $root | Out-Null;"
            "@{outcome='PASS';root=$root}|ConvertTo-Json -Compress"
        ),
    )
    _commission._powershell_json(result, "prepare Windows model installer root")
    remote = _commission._windows_scp_path(remote_root)
    with tempfile.TemporaryDirectory(prefix="elh-fabric-models-") as directory:
        installer = Path(directory) / _INSTALLER_NAME
        installer.write_text(_render_installer(models), encoding="ascii", newline="")
        _commission._scp_file(
            host=host,
            user=user,
            key=key,
            source=installer,
            destination=f"{remote}/{_INSTALLER_NAME}",
        )
    return remote_root.rstrip("/\\") + "/" + _INSTALLER_NAME


def _run_installer(
    *,
    host: str,
    user: str,
    key: Path,
    remote_script: str,
    timeout: float,
) -> None:
    if timeout <= 0:
        raise ValueError("timeout-seconds must be positive")
    command = _commission._ssh_base(host, user, key) + [
        "cmd.exe",
        "/d",
        "/c",
        remote_script.replace("/", "\\"),
    ]
    try:
        result = subprocess.run(command, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"worker-local Ollama model provisioning exceeded {timeout:g} seconds"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"worker-local Ollama model installer exited with code {result.returncode}"
        )


def install_models_windows(args: argparse.Namespace) -> int:
    ssh_key = args.ssh_key.expanduser().resolve()
    if not ssh_key.is_file():
        raise ValueError(f"SSH key does not exist: {ssh_key}")
    remote_root = args.remote_root or f"C:/Users/{args.ssh_user}/mncs-fabric-worker"
    models = _validate_models(list(args.model)) if args.model else _configured_models(args.config)
    preflight = _verify_host(
        host=args.ssh_host,
        user=args.ssh_user,
        key=ssh_key,
        expected_hostname=args.expected_hostname,
    )
    remote_script = _stage_installer(
        host=args.ssh_host,
        user=args.ssh_user,
        key=ssh_key,
        remote_root=remote_root,
        models=models,
    )
    summary: dict[str, Any] = {
        "outcome": "PASS",
        "host": args.ssh_host,
        "hostname": preflight.get("hostname"),
        "models": list(models),
        "installer": remote_script.replace("/", "\\"),
        "ollama_command": preflight.get("ollama_command"),
        "stage_only": bool(args.stage_only),
        "transfer_boundary": (
            "only the installer and model tags cross SSH; Ollama model blobs are fetched directly by the worker"
        ),
    }
    if args.stage_only:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    _run_installer(
        host=args.ssh_host,
        user=args.ssh_user,
        key=ssh_key,
        remote_script=remote_script,
        timeout=args.timeout_seconds,
    )
    try:
        installed = _commission._remote_ollama_models(
            host=args.ssh_host,
            user=args.ssh_user,
            key=ssh_key,
        )
    except RuntimeError as exc:
        summary["outcome"] = "UNKNOWN"
        summary["post_install_probe_error"] = str(exc)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 2
    missing = sorted(set(models) - set(installed))
    summary["installed_models"] = list(installed)
    summary["missing_models"] = missing
    summary["outcome"] = "PASS" if not missing else "UNKNOWN"
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if not missing else 2
