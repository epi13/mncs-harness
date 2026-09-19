from __future__ import annotations

import json
import subprocess
from pathlib import Path

from mncs_harness.family_context import load_family_context


def _packet(state: str) -> str:
    return json.dumps(
        {
            "schema_version": "mncs.family-agent-context/1",
            "status": "answered",
            "completeness": {"state": state, "complete": state == "complete"},
            "language": {},
            "architecture": {},
            "provenance": [],
        }
    )


def test_family_context_uses_language_service_and_preserves_complete_state(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"args": args, **kwargs})
        return subprocess.CompletedProcess(args[0], 0, _packet("complete"), "")  # type: ignore[arg-type]

    result = load_family_context(tmp_path, runner=runner)

    assert result.status == "complete"
    assert result.available
    command = calls[0]["args"][0]  # type: ignore[index]
    assert command[-4:] == ["--workspace", str(tmp_path.resolve()), "--max-items", "16"]
    assert calls[0]["cwd"] == tmp_path


def test_family_context_preserves_unknown_authority_state(tmp_path: Path) -> None:
    result = load_family_context(
        tmp_path,
        runner=lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, _packet("unknown"), ""
        ),
    )

    assert result.status == "unknown"
    assert result.available
    assert result.packet is not None
    assert result.packet["completeness"] == {"state": "unknown", "complete": False}


def test_family_context_fails_closed_on_unsupported_packet(tmp_path: Path) -> None:
    result = load_family_context(
        tmp_path,
        runner=lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, json.dumps({"schema_version": "old"}), ""
        ),
    )

    assert result.status == "unavailable"
    assert not result.available
    assert "unsupported context packet" in result.detail
