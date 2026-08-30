"""Install relocatable CLI wrappers that do not embed a host Python shebang.

Generated setuptools/hatch console scripts point at the absolute interpreter
used during ``pip install``. That path is invisible inside MNCS Control's
workspace mount, so ``./.venv/bin/elh`` fails even when
``./.venv/bin/python -m epi13_local_harness.cli`` works.

The wrappers exec the ``python`` next to themselves and therefore survive
workspace remounts.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

WRAPPERS = {
    "mncs-harness": "epi13_local_harness.cli",
    "mncs-harness-tui": "epi13_local_harness.tui",
    "mncs-harness-fabric": "epi13_local_harness.fabric_controller_light",
    "elh": "epi13_local_harness.cli",
    "epi13-harness": "epi13_local_harness.cli",
    "elh-tui": "epi13_local_harness.tui",
    "elh-fabric": "epi13_local_harness.fabric_controller_light",
}

_POSIX_WRAPPER = """\
#!/bin/sh
# Relocatable MNCS harness launcher. Do not replace with an absolute shebang.
dir=$(CDPATH= cd -- "$(dirname -- \"$0\")" && pwd)
exec "$dir/python" -m {module} "$@"
"""

_WINDOWS_WRAPPER = """\
@echo off
REM Relocatable MNCS harness launcher. Do not replace with an absolute shebang.
\"%~dp0python.exe\" -m {module} %*
"""


def wrapper_text(module: str, *, windows: bool = False) -> str:
    template = _WINDOWS_WRAPPER if windows else _POSIX_WRAPPER
    return template.format(module=module)


def default_bin_dir() -> Path:
    return Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin")


def install_portable_cli(bin_dir: Path | None = None) -> list[Path]:
    """Write relocatable wrappers beside the current interpreter."""

    destination = Path(bin_dir) if bin_dir is not None else default_bin_dir()
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    windows = os.name == "nt"
    for name, module in WRAPPERS.items():
        path = destination / (f"{name}.cmd" if windows else name)
        path.write_text(wrapper_text(module, windows=windows), encoding="utf-8")
        if not windows:
            mode = path.stat().st_mode
            path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        written.append(path)
    return written


def main() -> int:
    written = install_portable_cli()
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
