"""Persistent operator-owned Fabric profile management.

The main harness configuration stays portable and shareable.  This module owns a
small JSON overlay under the user's config directory so remote worker endpoints
and trust paths do not need to be committed to the repository or mixed into the
bundled defaults.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

APP_NAME = "epi13-local-harness"
PROFILE_SCHEMA = "epi13-local-harness.fabric-profile.v0.1"
PROFILE_ENV = "EPI13_HARNESS_FABRIC_PROFILE"
DEFAULT_ACCELERATOR_ROLES = ("e4b", "coder", "reviewer")
DEFAULT_LOCAL_ROLES = ("e2b",)
KNOWN_ROLES = frozenset((*DEFAULT_LOCAL_ROLES, *DEFAULT_ACCELERATOR_ROLES))


def default_fabric_profile_path() -> Path:
    override = os.environ.get(PROFILE_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / APP_NAME / "fabric-profile.json"


def _checked_path(value: Path, label: str) -> Path:
    path = value.expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {path}")
    return path


def load_fabric_profile(path: Path | None = None) -> dict[str, Any]:
    selected = (path or default_fabric_profile_path()).expanduser()
    if not selected.exists():
        return {}
    try:
        value = json.loads(selected.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Fabric profile is not valid JSON: {selected}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != PROFILE_SCHEMA:
        raise ValueError(f"Unsupported Fabric profile schema: {selected}")
    allowed = {"schema_version", "fabric", "models"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"Fabric profile contains unsupported fields: {', '.join(unknown)}")
    fabric = value.get("fabric")
    models = value.get("models")
    if not isinstance(fabric, dict) or not isinstance(models, dict):
        raise ValueError("Fabric profile requires object-valued fabric and models fields")
    return {"fabric": fabric, "models": models}


def build_remote_profile(
    *,
    worker_id: str,
    host: str,
    port: int,
    ca_file: Path,
    client_certificate: Path,
    client_key: Path,
    trust_state: Path,
    capabilities: Sequence[str] = ("python",),
    accelerator_roles: Sequence[str] = DEFAULT_ACCELERATOR_ROLES,
    local_roles: Sequence[str] = DEFAULT_LOCAL_ROLES,
    fallback_to_local: bool = True,
    gpu_reserve_mib: int = 512,
) -> dict[str, Any]:
    if not worker_id.strip():
        raise ValueError("worker id cannot be empty")
    if not host.strip():
        raise ValueError("worker host cannot be empty")
    if port < 1 or port > 65535:
        raise ValueError("worker port must be between 1 and 65535")
    if gpu_reserve_mib < 0:
        raise ValueError("GPU reserve cannot be negative")
    accelerator_roles = tuple(dict.fromkeys(accelerator_roles))
    local_roles = tuple(dict.fromkeys(local_roles))
    unknown_roles = sorted((set(accelerator_roles) | set(local_roles)) - KNOWN_ROLES)
    if unknown_roles:
        raise ValueError(f"unknown model roles: {', '.join(unknown_roles)}")
    overlap = sorted(set(accelerator_roles) & set(local_roles))
    if overlap:
        raise ValueError(f"roles cannot be both local and Fabric-routed: {', '.join(overlap)}")
    capability_list = list(dict.fromkeys(capabilities or ("python",)))
    if "python" not in capability_list:
        capability_list.insert(0, "python")

    model_overrides: dict[str, dict[str, Any]] = {}
    for role in local_roles:
        model_overrides[role] = {"provider": "ollama"}
    for role in accelerator_roles:
        model_overrides[role] = {
            "provider": "fabric",
            "execution_device": "accelerator",
            "accelerator_backend": "cuda",
            "offload": "auto",
            "precision": "auto",
            "gpu_reserve_bytes": gpu_reserve_mib * 1024 * 1024,
            "resource_max_age_seconds": 300.0,
        }

    return {
        "schema_version": PROFILE_SCHEMA,
        "fabric": {
            "enabled": True,
            "fallback_to_local": fallback_to_local,
            "refresh_on_startup": True,
            "refresh_timeout_seconds": 5.0,
            "workers": {
                worker_id: {
                    "kind": "remote",
                    "host": host,
                    "port": port,
                    "capabilities": capability_list,
                    "ca_file": str(ca_file),
                    "client_certificate": str(client_certificate),
                    "client_key": str(client_key),
                    "trust_state": str(trust_state),
                    "timeout_seconds": 5.0,
                    "concurrency_limit": 1,
                }
            },
        },
        "models": model_overrides,
    }


def write_fabric_profile(value: dict[str, Any], path: Path | None = None) -> Path:
    selected = (path or default_fabric_profile_path()).expanduser()
    selected.parent.mkdir(parents=True, exist_ok=True)
    temporary = selected.with_name(selected.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(selected)
    try:
        selected.chmod(0o600)
    except OSError:
        pass
    return selected


def configure_remote(args: argparse.Namespace) -> int:
    accelerator_roles = args.accelerator_role or list(DEFAULT_ACCELERATOR_ROLES)
    local_roles = args.local_role or list(DEFAULT_LOCAL_ROLES)
    value = build_remote_profile(
        worker_id=args.worker_id,
        host=args.host,
        port=args.port,
        ca_file=_checked_path(args.ca_file, "CA file"),
        client_certificate=_checked_path(args.client_certificate, "client certificate"),
        client_key=_checked_path(args.client_key, "client key"),
        trust_state=_checked_path(args.trust_state, "trust state"),
        capabilities=args.capability or ("python",),
        accelerator_roles=accelerator_roles,
        local_roles=local_roles,
        fallback_to_local=args.fallback_to_local,
        gpu_reserve_mib=args.gpu_reserve_mib,
    )
    path = write_fabric_profile(value, args.profile)
    print(path)
    return 0


def show_profile(args: argparse.Namespace) -> int:
    path = (args.profile or default_fabric_profile_path()).expanduser()
    if not path.exists():
        print(f"No Fabric profile: {path}", file=sys.stderr)
        return 1
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def disable_profile(args: argparse.Namespace) -> int:
    path = (args.profile or default_fabric_profile_path()).expanduser()
    if not path.exists():
        print(f"No Fabric profile: {path}", file=sys.stderr)
        return 1
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != PROFILE_SCHEMA:
        raise ValueError(f"Unsupported Fabric profile schema: {path}")
    fabric = value.setdefault("fabric", {})
    if not isinstance(fabric, dict):
        raise ValueError("Fabric profile fabric field is invalid")
    fabric["enabled"] = False
    write_fabric_profile(value, path)
    print(path)
    return 0


def status_profile(args: argparse.Namespace) -> int:
    if args.profile is not None:
        os.environ[PROFILE_ENV] = str(args.profile.expanduser())
    from .config import load_config
    from .fabric import FabricSession

    config = load_config(args.config)
    session = FabricSession(config.fabric)
    session.initialize()
    status = session.refresh() if status_should_refresh(session) else session.status()
    payload = {
        "profile": str(args.profile or default_fabric_profile_path()),
        "enabled": status.enabled,
        "state": status.state,
        "controller_id": status.controller_id,
        "detail": status.detail,
        "available_workers": status.available_workers,
        "accelerator_count": status.accelerator_count,
        "offload_capable_count": status.offload_capable_count,
        "workers": list(status.workers),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if status.state in {"available", "disabled"} else 1


def status_should_refresh(session: Any) -> bool:
    return bool(getattr(session, "enabled", False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="elh-fabric",
        description="Configure and inspect the optional MNCS Fabric execution profile.",
    )
    parser.add_argument("--profile", type=Path, help="Fabric profile JSON path")
    parser.add_argument("--config", type=Path, help="Harness TOML configuration path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser(
        "configure-remote",
        help="Persist one explicit mTLS Fabric worker and GPU routing policy",
    )
    configure.add_argument("--worker-id", required=True)
    configure.add_argument("--host", required=True)
    configure.add_argument("--port", type=int, default=7443)
    configure.add_argument("--ca-file", type=Path, required=True)
    configure.add_argument("--client-certificate", type=Path, required=True)
    configure.add_argument("--client-key", type=Path, required=True)
    configure.add_argument("--trust-state", type=Path, required=True)
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

    show = subparsers.add_parser("show", help="Print the persisted Fabric profile")
    show.set_defaults(func=show_profile)
    disable = subparsers.add_parser("disable", help="Disable Fabric without deleting the profile")
    disable.set_defaults(func=disable_profile)
    status = subparsers.add_parser("status", help="Initialize Fabric and print worker state")
    status.set_defaults(func=status_profile)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
