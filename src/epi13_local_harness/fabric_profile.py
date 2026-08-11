"""Operator-owned Fabric configuration helpers.

`elh-fabric configure-remote` makes a bounded, section-aware edit to the normal
user TOML configuration. Trust material itself is never copied into the
configuration; only operator-provided paths are stored. A one-time backup is
created before the first managed edit.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

DEFAULT_ACCELERATOR_ROLES = ("e4b", "coder", "reviewer")
DEFAULT_LOCAL_ROLES = ("e2b",)
KNOWN_ROLES = frozenset((*DEFAULT_LOCAL_ROLES, *DEFAULT_ACCELERATOR_ROLES))
_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$")
_KEY_RE = re.compile(r"^(\s*)([A-Za-z0-9_-]+)\s*=")
_WORKER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CONTROLLER_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")


def _checked_path(value: Path, label: str) -> Path:
    path = value.expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {path}")
    return path


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"unsupported TOML value: {type(value).__name__}")


def upsert_toml_section(text: str, section: str, values: Mapping[str, object]) -> str:
    """Replace/add flat keys in one TOML table while preserving the rest of the file."""
    lines = text.splitlines()
    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        match = _SECTION_RE.match(line)
        if not match:
            continue
        if match.group(1) == section:
            start = index
            continue
        if start is not None and index > start:
            end = index
            break

    rendered = {key: f"{key} = {_toml_value(value)}" for key, value in values.items()}
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"[{section}]")
        lines.extend(rendered.values())
        return "\n".join(lines) + "\n"

    seen: set[str] = set()
    for index in range(start + 1, end):
        match = _KEY_RE.match(lines[index])
        if not match:
            continue
        key = match.group(2)
        if key in rendered:
            lines[index] = match.group(1) + rendered[key]
            seen.add(key)
    additions = [rendered[key] for key in values if key not in seen]
    if additions:
        lines[end:end] = additions
    return "\n".join(lines) + "\n"


def _config_path(value: Path | None) -> Path:
    from .config import default_config_path

    return (value or default_config_path()).expanduser()


def _ensure_user_config(path: Path) -> None:
    if path.exists():
        return
    from .config import initialize_config

    initialize_config(path)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _backup_once(path: Path) -> Path:
    backup = path.with_name(path.name + ".pre-fabric")
    if not backup.exists():
        shutil.copy2(path, backup)
    return backup


def configure_remote(args: argparse.Namespace) -> int:
    if not _WORKER_ID_RE.fullmatch(args.worker_id):
        raise ValueError("worker id may contain only letters, numbers, '_' and '-'")
    if not _CONTROLLER_ID_RE.fullmatch(args.controller_id):
        raise ValueError("controller id contains unsupported characters")
    if not args.host.strip():
        raise ValueError("worker host cannot be empty")
    if args.port < 1 or args.port > 65535:
        raise ValueError("worker port must be between 1 and 65535")
    if args.gpu_reserve_mib < 0:
        raise ValueError("GPU reserve cannot be negative")

    accelerator_roles = tuple(dict.fromkeys(args.accelerator_role or DEFAULT_ACCELERATOR_ROLES))
    local_roles = tuple(dict.fromkeys(args.local_role or DEFAULT_LOCAL_ROLES))
    overlap = sorted(set(accelerator_roles) & set(local_roles))
    if overlap:
        raise ValueError(f"roles cannot be both local and Fabric-routed: {', '.join(overlap)}")

    ca_file = _checked_path(args.ca_file, "CA file")
    client_certificate = _checked_path(args.client_certificate, "client certificate")
    client_key = _checked_path(args.client_key, "client key")
    trust_state = _checked_path(args.trust_state, "trust state")
    capabilities = list(dict.fromkeys(args.capability or ("python",)))
    if "python" not in capabilities:
        capabilities.insert(0, "python")

    path = _config_path(args.config)
    _ensure_user_config(path)
    backup = _backup_once(path)
    text = path.read_text(encoding="utf-8")
    fabric_values: dict[str, object] = {
        "enabled": True,
        "controller_id": args.controller_id,
        "fallback_to_local": args.fallback_to_local,
        "refresh_on_startup": True,
        "refresh_timeout_seconds": 5.0,
        "runtime_probe_on_refresh": True,
        "runtime_probe_timeout_seconds": 45.0,
        "runtime_probe_max_age_seconds": 1800.0,
    }
    registry_path = getattr(args, "registry_path", None)
    if registry_path is not None:
        fabric_values["registry_path"] = str(registry_path.expanduser())
    text = upsert_toml_section(
        text,
        "fabric",
        fabric_values,
    )
    text = upsert_toml_section(
        text,
        f"fabric.workers.{args.worker_id}",
        {
            "kind": "remote",
            "host": args.host,
            "port": args.port,
            "state_path": f"~/.local/state/epi13-local-harness/fabric-{args.worker_id}.jsonl",
            "capabilities": capabilities,
            "ca_file": str(ca_file),
            "client_certificate": str(client_certificate),
            "client_key": str(client_key),
            "trust_state": str(trust_state),
            "concurrency_limit": 1,
            "timeout_seconds": 5.0,
        },
    )
    for role in local_roles:
        text = upsert_toml_section(text, f"models.{role}", {"provider": "ollama"})
    for role in accelerator_roles:
        text = upsert_toml_section(
            text,
            f"models.{role}",
            {
                "provider": "fabric",
                "execution_device": "accelerator",
                "accelerator_backend": "cuda",
                "offload": "auto",
                "precision": "auto",
                "gpu_reserve_bytes": args.gpu_reserve_mib * 1024 * 1024,
                "resource_max_age_seconds": 300.0,
            },
        )
    _atomic_write(path, text)
    print(path)
    print(f"backup: {backup}")
    return 0


def disable_fabric(args: argparse.Namespace) -> int:
    path = _config_path(args.config)
    if not path.exists():
        print(f"No user configuration: {path}", file=sys.stderr)
        return 1
    text = upsert_toml_section(path.read_text(encoding="utf-8"), "fabric", {"enabled": False})
    _atomic_write(path, text)
    print(path)
    return 0


def _status_payload(config: Any, status: Any) -> dict[str, Any]:
    return {
        "config": str(config),
        "enabled": status.enabled,
        "state": status.state,
        "controller_id": status.controller_id,
        "detail": status.detail,
        "available_workers": status.available_workers,
        "accelerator_count": status.accelerator_count,
        "cuda_ready_count": status.cuda_ready_count,
        "offload_capable_count": status.offload_capable_count,
        "workers": list(status.workers),
    }


def status_fabric(args: argparse.Namespace) -> int:
    from .config import load_config
    from .fabric import FabricSession

    path = _config_path(args.config)
    config = load_config(path)
    session = FabricSession(config.fabric)
    session.initialize()
    status = session.refresh() if session.enabled else session.status()
    print(json.dumps(_status_payload(path, status), indent=2, sort_keys=True))
    return 0 if status.state in {"available", "disabled"} else 1


def show_fabric(args: argparse.Namespace) -> int:
    from .config import load_config

    path = _config_path(args.config)
    config = load_config(path)
    payload = {
        "config": str(path),
        "enabled": config.fabric.enabled,
        "controller_id": config.fabric.controller_id,
        "registry_path": (
            str(config.fabric.registry_path) if config.fabric.registry_path else None
        ),
        "fallback_to_local": config.fabric.fallback_to_local,
        "runtime_probe_on_refresh": config.fabric.runtime_probe_on_refresh,
        "runtime_probe_timeout_seconds": config.fabric.runtime_probe_timeout_seconds,
        "runtime_probe_max_age_seconds": config.fabric.runtime_probe_max_age_seconds,
        "workers": [
            {
                "worker_id": worker.worker_id,
                "kind": worker.kind,
                "host": worker.host,
                "port": worker.port,
                "capabilities": list(worker.capabilities),
                "ca_file": str(worker.ca_file) if worker.ca_file else None,
                "client_certificate": str(worker.client_certificate) if worker.client_certificate else None,
                "client_key": str(worker.client_key) if worker.client_key else None,
                "trust_state": str(worker.trust_state) if worker.trust_state else None,
            }
            for worker in config.fabric.workers
        ],
        "roles": {
            role: {
                "provider": model.provider,
                "execution_device": model.execution_device,
                "accelerator_backend": model.accelerator_backend,
                "offload": model.offload,
                "precision": model.precision,
                "gpu_reserve_bytes": model.gpu_reserve_bytes,
            }
            for role, model in config.models.items()
        },
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="elh-fabric",
        description="Configure, commission, and inspect MNCS Fabric execution.",
    )
    parser.add_argument("--config", type=Path, help="Harness TOML configuration path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser(
        "configure-remote",
        help="Enable one explicit mTLS Fabric worker and GPU routing policy",
    )
    configure.add_argument("--controller-id", default="epi13-local-harness")
    configure.add_argument("--worker-id", required=True)
    configure.add_argument("--host", required=True)
    configure.add_argument("--port", type=int, default=7443)
    configure.add_argument("--ca-file", type=Path, required=True)
    configure.add_argument("--client-certificate", type=Path, required=True)
    configure.add_argument("--client-key", type=Path, required=True)
    configure.add_argument("--trust-state", type=Path, required=True)
    configure.add_argument(
        "--registry-path",
        type=Path,
        help="Point Harness at an operator-owned Fabric worker registry",
    )
    configure.add_argument("--capability", action="append")
    configure.add_argument(
        "--accelerator-role",
        action="append",
        choices=sorted(KNOWN_ROLES),
        help="Role routed through Fabric CUDA; defaults to e4b, coder, reviewer",
    )
    configure.add_argument(
        "--local-role",
        action="append",
        choices=sorted(KNOWN_ROLES),
        help="Role kept on local Ollama; defaults to e2b",
    )
    configure.add_argument("--gpu-reserve-mib", type=int, default=512)
    configure.add_argument(
        "--fallback-to-local",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fall back to local Ollama when Fabric cannot execute",
    )
    configure.set_defaults(func=configure_remote)

    from .fabric_commission import add_windows_commission_arguments, commission_windows

    commission = subparsers.add_parser(
        "commission-windows",
        help="Create a persistent mTLS enrollment and provision one explicit Windows worker",
    )
    add_windows_commission_arguments(commission)
    commission.set_defaults(func=commission_windows)

    from .fabric_models import add_windows_model_arguments, install_models_windows

    install_models = subparsers.add_parser(
        "install-models-windows",
        help="Stage and run worker-local Ollama pulls without transferring model blobs over the LAN",
    )
    add_windows_model_arguments(install_models)
    install_models.set_defaults(func=install_models_windows)

    show = subparsers.add_parser("show", help="Show effective Fabric routing configuration")
    show.set_defaults(func=show_fabric)
    disable = subparsers.add_parser("disable", help="Disable Fabric without deleting worker settings")
    disable.set_defaults(func=disable_fabric)
    status = subparsers.add_parser("status", help="Initialize Fabric and print worker state")
    status.set_defaults(func=status_fabric)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
