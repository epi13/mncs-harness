#!/usr/bin/env python3
"""Fetch the pinned MNCS executor binary with digest verification.

Downloads the release asset named in docs/MNCS_TOOLCHAIN.md, verifies its
SHA-256 digest BEFORE marking it executable, and writes it to --dest
(default: ~/.local/bin/mncs-executor). Fails closed on any mismatch.

This is an explicit operator/CI action. The product runtime
(mncs_runtime.find_executor) never downloads; it only resolves a local
binary or raises.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = "epi13/mncs-harness"
TAG = "toolchain/mncs-executor-8a96527"
ASSET = "mncs-executor-linux-x86_64"
DIGEST = "6dc6b2b20e600ee4cd90a99ae08c59a76a258254036bcddfc94e5138cda69bae"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dest",
        default=str(Path.home() / ".local" / "bin" / "mncs-executor"),
        help="destination path for the verified executor binary",
    )
    parser.add_argument(
        "--digest",
        default=DIGEST,
        help="expected SHA-256 digest (must match docs/MNCS_TOOLCHAIN.md to change)",
    )
    args = parser.parse_args()

    url = f"https://github.com/{REPO}/releases/download/{TAG}/{ASSET}"
    print(f"downloading {url}")
    try:
        with urllib.request.urlopen(url, timeout=300) as response:  # noqa: S310
            data = response.read()
    except Exception as exc:
        print(f"download failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    digest = hashlib.sha256(data).hexdigest()
    if digest != args.digest:
        print(
            f"DIGEST MISMATCH: got sha256:{digest}, want sha256:{args.digest}; refusing",
            file=sys.stderr,
        )
        raise SystemExit(1)
    dest = Path(os.path.expanduser(args.dest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=str(dest.parent), delete=False) as staged:
        staged.write(data)
        staged_path = Path(staged.name)
    staged_path.chmod(staged_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    staged_path.rename(dest)
    print(f"verified sha256:{digest} -> {dest}")


if __name__ == "__main__":
    main()
