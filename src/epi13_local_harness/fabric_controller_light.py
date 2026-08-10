"""Controller-light Fabric profile and inventory-aware status command."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from . import fabric_profile as _profile
from . import fabric_profile_inventory as _base

_CONTROLLER_LIGHT = "controller-light"
_STATUS = "status"


def _remove_toml_keys(text: str, section: str, keys: set[str]) -> str:
    """Remove flat keys from one TOML table without disturbing neighboring tables."""

    lines = text.splitlines()
    in_section = False
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped == f"[{section}]"
            output.append(line)
            continue
        if in_section and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in keys:
                continue
        output.append(line)
    return "\n".join(output) + "\n"


def _apply_controller_light(config_path: Path | None) -> int:
    from .config import load_config
    from .semantic_router import router_status

    path = _profile._config_path(config_path)
    _profile._ensure_user_config(path)
    backup = _profile._backup_once(path)
    config = load_config(path)
    text = path.read_text(encoding="utf-8")
    text = _profile.upsert_toml_section(
        text,
        "fabric",
        {
            "enabled": True,
            "fallback_to_local": False,
            "refresh_on_startup": True,
            # The Fabric Python worker is only the transport-side Ollama client.
            # Its Torch/CUDA state does not prove or gate worker-local Ollama GPU use.
            "runtime_probe_on_refresh": False,
        },
    )
    text = _profile.upsert_toml_section(
        text,
        "router",
        {
            "mode": "hybrid",
            "backend": "transformers",
            "enable_semantic_routing": True,
            "device": "cpu",
            "fallback": "deterministic",
        },
    )
    for role in config.models:
        section = f"models.{role}"
        text = _profile.upsert_toml_section(
            text,
            section,
            {
                "provider": "fabric",
                # The Fabric bundle is only a small HTTP client talking to worker-local
                # Ollama. Ollama owns GPU/CPU placement and model loading.
                "execution_device": "cpu",
                "offload": "auto",
                "precision": "auto",
            },
        )
        text = _remove_toml_keys(text, section, {"accelerator_backend"})
    _profile._atomic_write(path, text)
    effective = load_config(path)
    router = router_status(effective)
    payload = {
        "outcome": "PASS",
        "config": str(path),
        "backup": str(backup),
        "controller_mode": "light-router-only",
        "semantic_router_enabled": effective.router.enable_semantic_routing,
        "semantic_router_state": router.state,
        "semantic_router_model": router.model,
        "semantic_router_device": router.device,
        "fabric_fallback_to_local": effective.fabric.fallback_to_local,
        "runtime_probe_on_refresh": effective.fabric.runtime_probe_on_refresh,
        "generation_roles": {
            role: {
                "provider": model.provider,
                "execution_device": model.execution_device,
                "accelerator_backend": model.accelerator_backend,
            }
            for role, model in effective.models.items()
        },
        "provider_boundary": (
            "Fabric places the lightweight provider-call bundle on the remote worker; worker-local "
            "Ollama owns model loading, GPU residency, and CPU/GPU split."
        ),
        "note": (
            "The semantic router chooses a lane locally; response-generation models are Fabric-routed. "
            "If the semantic router is not active, deterministic routing remains the bounded fallback."
        ),
    }
    if router.state in {"missing-dependencies", "not-cached", "unpinned", "unsupported"}:
        payload["router_action"] = (
            "Run `python -m pip install -e '.[router]'` and `elh router prepare` if semantic routing "
            "is desired immediately; deterministic routing remains available meanwhile."
        )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _inventory_status(config_path: Path | None) -> int:
    from .config import load_config
    from .fabric_inventory_session import InventoryAwareFabricSession

    path = _profile._config_path(config_path)
    config = load_config(path)
    session = InventoryAwareFabricSession(config.fabric)
    session.initialize()
    status = session.status()
    payload = _profile._status_payload(path, status)
    provider_ready = [
        worker
        for worker in status.workers
        if worker.get("source") == "remote"
        and worker.get("availability") == "AVAILABLE"
        and bool(worker.get("model_names"))
    ]
    payload["ollama_inventory_ready_count"] = len(provider_ready)
    payload["provider_execution_boundary"] = (
        "remote Fabric bundle is a lightweight Ollama client; cuda_ready_count describes the "
        "worker Python runtime and does not gate worker-local Ollama inference"
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if status.state in {"available", "disabled"} else 1


def _target_parser(command: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="elh-fabric")
    parser.add_argument("--config", type=Path, help="Harness TOML configuration path")
    subparsers = parser.add_subparsers(dest="command", required=True)
    if command == _CONTROLLER_LIGHT:
        action = subparsers.add_parser(
            _CONTROLLER_LIGHT,
            help="Keep only the lightweight semantic router local and route generation through Fabric",
        )
        action.set_defaults(func=lambda args: _apply_controller_light(args.config))
    else:
        action = subparsers.add_parser(_STATUS)
        action.set_defaults(func=lambda args: _inventory_status(args.config))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(argv if argv is not None else sys.argv[1:])
    command = next((item for item in raw if item in {_CONTROLLER_LIGHT, _STATUS}), None)
    if command is None:
        return _base.main(raw)
    parser = _target_parser(command)
    args = parser.parse_args(raw)
    try:
        return int(args.func(args))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
