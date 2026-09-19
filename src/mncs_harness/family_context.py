"""Authoritative Language Service family-agent-context adapter.

Harness owns routing and prompt assembly only. It asks Language Service for
the bounded family packet and preserves incomplete/UNKNOWN states; Atlas is a
separate optional orientation fragment.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SERVICE_COMMAND_ENV = "MNCS_LANGUAGE_SERVICE_COMMAND"
SERVICE_ROOT_ENV = "MNCS_LANGUAGE_SERVICE_ROOT"
CONTEXT_MAX_CHARS = 12_000
CONTEXT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class FamilyContext:
    status: str
    packet: dict[str, object] | None = None
    detail: str = ""

    @property
    def available(self) -> bool:
        return self.status in {"complete", "partial", "unknown"} and self.packet is not None

    def prompt_fragment(self) -> str | None:
        if not self.available or self.packet is None:
            return None
        completeness = self.packet.get("completeness") or {}
        state = completeness.get("state", "unknown") if isinstance(completeness, dict) else "unknown"
        rendered = json.dumps(self.packet, ensure_ascii=False, sort_keys=True, indent=2)
        return f"MNCS authoritative family context (Language Service; state={state}):\n{rendered}"


def _service_command(workspace: Path) -> list[str]:
    configured = os.environ.get(SERVICE_COMMAND_ENV)
    if configured:
        return shlex.split(configured)
    root = os.environ.get(SERVICE_ROOT_ENV)
    if root:
        service_root = Path(root).expanduser()
    else:
        service_root = workspace.parent / "mncs-language-service"
    if (service_root / "Cargo.toml").is_file():
        return [
            "cargo",
            "run",
            "--quiet",
            "--manifest-path",
            str(service_root / "Cargo.toml"),
            "-p",
            "mncs-service-core",
            "--bin",
            "mncs-family-context",
            "--",
        ]
    return ["mncs-family-context"]


def load_family_context(
    workspace: Path,
    *,
    timeout: float = CONTEXT_TIMEOUT_SECONDS,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> FamilyContext:
    command = _service_command(workspace)
    command.extend(["--workspace", str(workspace.resolve()), "--max-items", "16"])
    invoke = runner or subprocess.run
    try:
        result = invoke(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return FamilyContext("unavailable", detail="Language Service family context timed out")
    except OSError as exc:
        return FamilyContext("unavailable", detail=f"Language Service query failed: {exc}")
    if result.returncode != 0:
        detail = result.stderr.strip() or f"Language Service exited with status {result.returncode}"
        return FamilyContext("unavailable", detail=detail[:500])
    if len(result.stdout) > CONTEXT_MAX_CHARS:
        return FamilyContext("unavailable", detail="Language Service context exceeded the local bound")
    try:
        packet = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return FamilyContext("unavailable", detail=f"Language Service returned invalid JSON: {exc}")
    if not isinstance(packet, dict) or packet.get("schema_version") != "mncs.family-agent-context/1":
        return FamilyContext("unavailable", detail="Language Service returned an unsupported context packet")
    completeness = packet.get("completeness")
    if not isinstance(completeness, dict):
        return FamilyContext("unavailable", detail="Language Service packet omitted completeness")
    state = str(completeness.get("state", "unknown"))
    return FamilyContext(state if state in {"complete", "partial", "unknown"} else "unknown", packet)
