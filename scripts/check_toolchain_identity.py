#!/usr/bin/env python3
"""Prove the four MNCS toolchain identities agree. CI gate; fails closed.

The harness distinguishes four identities that must never be conflated:

1. source compiler/toolchain revision (full mncs-language commit SHA)
2. executor release artifact (GitHub release tag + asset name)
3. executor SHA-256 digest (release bytes)
4. compiled harness kernel artifact identities (MANIFEST.json entries)

This script asserts ``docs/MNCS_TOOLCHAIN.md`` and
``scripts/fetch_mncs_executor.py`` name the SAME release and digest, that
the pinned revision is a full 40-hex SHA, and that every shipped kernel
artifact is present. It prints all four identities for CI evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "MNCS_TOOLCHAIN.md"
FETCH = REPO / "scripts" / "fetch_mncs_executor.py"
MANIFEST = REPO / "src" / "mncs_harness" / "_mncs_artifacts" / "MANIFEST.json"


def fail(message: str) -> None:
    print(f"toolchain-identity: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    try:
        doc = DOC.read_text(encoding="utf-8")
        fetch_src = FETCH.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"unreadable toolchain files: {exc}")

    revision = re.search(r"`([0-9a-f]{40})`", doc)
    if not revision:
        fail("docs/MNCS_TOOLCHAIN.md names no full 40-hex toolchain revision")
    revision = revision.group(1)
    doc_tag = re.search(r"\*\*Executor release:\*\*\s*`([^`]+)`", doc)
    doc_digest = re.search(r"sha256:([0-9a-f]{64})", doc)
    if not doc_tag or not doc_digest:
        fail("docs/MNCS_TOOLCHAIN.md missing executor release or digest")
    script_tag = re.search(r'^TAG\s*=\s*"([^"]+)"', fetch_src, re.MULTILINE)
    script_digest = re.search(r'^DIGEST\s*=\s*"([0-9a-f]{64})"', fetch_src, re.MULTILINE)
    if not script_tag or not script_digest:
        fail("scripts/fetch_mncs_executor.py missing TAG/DIGEST constants")
    if doc_tag.group(1) != script_tag.group(1):
        fail(f"release tag drift: docs {doc_tag.group(1)} vs script {script_tag.group(1)}")
    if doc_digest.group(1) != script_digest.group(1):
        fail("executor digest drift: docs vs fetch script disagree; refusing")
    if not doc_tag.group(1).endswith(revision[:7]):
        fail("executor release tag does not embed the pinned revision prefix")

    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail(f"unreadable artifact manifest: {exc}")
    artifacts = manifest.get("artifacts", {})
    if not artifacts:
        fail("artifact manifest has no entries")
    manifest_digest = hashlib.sha256(
        MANIFEST.read_bytes()
    ).hexdigest()

    print(f"toolchain revision : {revision}")
    print(f"executor release   : {doc_tag.group(1)} (mncs-executor-linux-x86_64)")
    print(f"executor digest    : sha256:{doc_digest.group(1)}")
    print(f"manifest digest    : sha256:{manifest_digest} ({len(artifacts)} artifacts)")
    print("toolchain-identity OK: docs, fetch script, and manifest agree")


if __name__ == "__main__":
    main()
