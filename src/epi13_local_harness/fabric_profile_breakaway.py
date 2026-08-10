"""CLI shim that routes Windows commissioning through the native breakaway launcher."""

from __future__ import annotations

import sys
from typing import Sequence

from . import fabric_profile as _base
from .fabric_commission_breakaway import commission_windows


def main(argv: Sequence[str] | None = None) -> int:
    args = _base.build_parser().parse_args(argv)
    if args.command == "commission-windows":
        args.func = commission_windows
    try:
        return int(args.func(args))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
