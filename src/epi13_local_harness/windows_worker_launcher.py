"""Windows-native detached process launcher used during Fabric commissioning.

This module is intentionally small and bootstrap-only. It mirrors the process
boundary already proven by MNCS Fabric's physical Windows launcher: worker
children are created outside the OpenSSH job object when host policy permits,
so closing the commissioning SSH session does not tear the worker down.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "epi13-local-harness.fabric-launcher.v0.2"


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _process_token(pid: int) -> str | None:
    """Return the Windows creation FILETIME as a stable decimal process token."""
    if os.name != "nt" or pid < 1:
        return None
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    created = _FileTime()
    exited = _FileTime()
    kernel = _FileTime()
    user = _FileTime()
    try:
        ok = ctypes.windll.kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None
        return str((int(created.high) << 32) | int(created.low))
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _creation_flags() -> int:
    if os.name != "nt":
        raise RuntimeError("Windows worker launcher must run on Windows")
    values: list[int] = []
    for name in (
        "CREATE_NEW_PROCESS_GROUP",
        "DETACHED_PROCESS",
        "CREATE_BREAKAWAY_FROM_JOB",
    ):
        value = getattr(subprocess, name, None)
        if not isinstance(value, int) or value == 0:
            raise RuntimeError(f"required Windows process flag is unavailable: {name}")
        values.append(value)
    flags = 0
    for value in values:
        flags |= value
    return flags


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def start(args: argparse.Namespace) -> int:
    command = list(args.worker_command or [])
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        raise RuntimeError("worker command is required after --")

    state = Path(args.state)
    stdout_path = Path(args.stdout)
    stderr_path = Path(args.stderr)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)

    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen(
            command,
            cwd=args.cwd or None,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            shell=False,
            creationflags=_creation_flags(),
        )

    token = _process_token(process.pid)
    if token is None:
        try:
            process.terminate()
        except OSError:
            pass
        raise RuntimeError("could not capture launched worker process identity")

    record = {
        "schema_version": SCHEMA_VERSION,
        "pid": process.pid,
        "process_token": token,
        "worker_id": args.worker_id,
        "controller_id": args.controller_id,
        "started_at": _now(),
    }
    _write_json_atomic(state, record)
    print(json.dumps({"outcome": "PASS", **record}, sort_keys=True), flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="detached Windows Fabric worker launcher")
    sub = parser.add_subparsers(dest="action", required=True)
    start_parser = sub.add_parser("start")
    start_parser.add_argument("--state", required=True)
    start_parser.add_argument("--worker-id", required=True)
    start_parser.add_argument("--controller-id", required=True)
    start_parser.add_argument("--stdout", required=True)
    start_parser.add_argument("--stderr", required=True)
    start_parser.add_argument("--cwd")
    start_parser.add_argument("worker_command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "start":
            return start(args)
        raise AssertionError("unreachable action")
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"outcome": "UNKNOWN", "error": str(exc)}, sort_keys=True), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
