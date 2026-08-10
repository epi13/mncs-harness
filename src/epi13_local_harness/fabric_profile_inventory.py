"""Extend ``elh-fabric`` with live worker model inventory commands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from . import fabric_profile_breakaway as _base
from .fabric_inventory import add_scan_windows_arguments, scan_models_windows

_SCAN_COMMAND = "scan-models-windows"


def _scan_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="elh-fabric",
        description="Configure, commission, inspect, and inventory MNCS Fabric execution.",
    )
    parser.add_argument("--config", type=Path, help="Harness TOML configuration path")
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan = subparsers.add_parser(
        _SCAN_COMMAND,
        help="Live-scan every Ollama model installed on one explicit Windows worker",
    )
    add_scan_windows_arguments(scan)
    scan.set_defaults(func=scan_models_windows)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(argv if argv is not None else sys.argv[1:])
    if _SCAN_COMMAND not in raw:
        return _base.main(raw)
    args = _scan_parser().parse_args(raw)
    try:
        return int(args.func(args))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
