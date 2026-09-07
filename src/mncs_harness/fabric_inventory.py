"""Live Ollama model inventory for explicitly managed Fabric workers.

Inventory discovery queries the worker-local Ollama HTTP API over the existing
operator SSH maintenance channel.  It does not require ``ollama.exe`` to be on
the non-interactive SSH PATH and it does not copy model blobs or expose Ollama
on the LAN.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from . import fabric_commission as _commission

INVENTORY_SCHEMA = "epi13-local-harness.model-inventory.v0.1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_text(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


def normalize_ollama_models(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Normalize every model reported by Ollama's ``/api/tags`` endpoint."""

    raw_models = payload.get("models", [])
    if not isinstance(raw_models, list):
        return ()
    by_name: dict[str, dict[str, Any]] = {}
    for raw in raw_models:
        if not isinstance(raw, Mapping):
            continue
        name = _safe_text(raw.get("name") or raw.get("model"))
        if not name:
            continue
        details = raw.get("details") if isinstance(raw.get("details"), Mapping) else {}
        size = raw.get("size")
        entry = {
            "name": name,
            "model": _safe_text(raw.get("model")) or name,
            "size": size if isinstance(size, int) and not isinstance(size, bool) else None,
            "digest": _safe_text(raw.get("digest")),
            "modified_at": _safe_text(raw.get("modified_at")),
            "details": {
                "format": _safe_text(details.get("format")),
                "family": _safe_text(details.get("family")),
                "families": [str(item) for item in details.get("families", [])]
                if isinstance(details.get("families"), list)
                else [],
                "parameter_size": _safe_text(details.get("parameter_size")),
                "quantization_level": _safe_text(details.get("quantization_level")),
            },
        }
        by_name[name] = entry
    return tuple(by_name[name] for name in sorted(by_name, key=str.casefold))


def _scan_windows(
    *,
    host: str,
    user: str,
    key: Path,
    expected_hostname: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    if timeout_seconds <= 0:
        raise ValueError("timeout-seconds must be positive")
    if not key.expanduser().is_file():
        raise ValueError(f"SSH key does not exist: {key.expanduser()}")
    request_timeout = max(1, min(60, int(timeout_seconds)))
    script = (
        "$ErrorActionPreference='Stop';"
        f"$response=Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec {request_timeout};"
        "$value=[ordered]@{hostname=$env:COMPUTERNAME;ollama_url='http://127.0.0.1:11434';"
        "models=@($response.models)};"
        "$value | ConvertTo-Json -Depth 8 -Compress"
    )
    result = _commission._run_powershell(
        host=host,
        user=user,
        key=key.expanduser().resolve(),
        script=script,
        timeout=timeout_seconds + 5,
    )
    value = _commission._powershell_json(result, "worker-local Ollama model inventory")
    observed = str(value.get("hostname", ""))
    if observed.casefold() != expected_hostname.casefold():
        raise RuntimeError(
            f"Windows hostname mismatch: expected {expected_hostname!r}, observed {observed!r}"
        )
    models = normalize_ollama_models(value)
    return {
        "schema_version": INVENTORY_SCHEMA,
        "captured_at": _utc_now(),
        "source": "worker-local-ollama-api",
        "transport": "operator-ssh-maintenance",
        "host": host,
        "hostname": observed,
        "ollama_url": value.get("ollama_url", "http://127.0.0.1:11434"),
        "model_count": len(models),
        "model_names": [model["name"] for model in models],
        "models": list(models),
        "claim_boundary": (
            "live worker-local Ollama inventory; model presence does not imply a routing role, "
            "capability classification, GPU residency, or successful inference"
        ),
    }


def add_scan_windows_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ssh-host", required=True)
    parser.add_argument("--ssh-user", required=True)
    parser.add_argument("--ssh-key", type=Path, required=True)
    parser.add_argument("--expected-hostname", required=True)
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=15.0,
        help="Maximum time for the live worker inventory query; default: 15 seconds",
    )


def scan_models_windows(args: argparse.Namespace) -> int:
    inventory = _scan_windows(
        host=args.ssh_host,
        user=args.ssh_user,
        key=args.ssh_key,
        expected_hostname=args.expected_hostname,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(inventory, indent=2, sort_keys=True))
    return 0
