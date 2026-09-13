"""Small, local Atlas context capsules for agent preflight.

Harness deliberately treats Atlas as an optional local producer.  It discovers
the existing Atlas CLI and asks it for a context capsule; it does not parse the
registry, fetch repositories, or maintain a second family-knowledge model.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ATLAS_ROOT_ENV = "MNCS_ATLAS_ROOT"
CAPSULE_HEADER = "MNCS FAMILY CONTEXT"
CAPSULE_MAX_CHARS = 6_000
CAPSULE_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class AtlasContext:
    """Result of the optional local Atlas preflight."""

    status: str
    text: str = ""
    atlas_root: Path | None = None
    detail: str = ""

    @property
    def available(self) -> bool:
        return self.status == "available" and bool(self.text)

    def prompt_fragment(self) -> str | None:
        if not self.available:
            return None
        return (
            "MNCS family context (local Atlas capsule; use targeted Atlas queries for more detail):\n"
            f"{self.text}"
        )


def _looks_like_atlas(root: Path) -> bool:
    return (root / "registry" / "__main__.py").is_file() and (
        root / "registry" / "compiled.json"
    ).is_file()


def find_atlas_root(workspace: Path, explicit_root: Path | None = None) -> Path | None:
    """Find a local Atlas checkout without network access or broad scanning."""

    workspace = workspace.resolve()
    if explicit_root is not None:
        candidates = [explicit_root]
    else:
        candidates = []
        configured = os.environ.get(ATLAS_ROOT_ENV)
        if configured:
            candidates.append(Path(configured))
        candidates.extend(
            (
                workspace,
                workspace.parent / "mncs-atlas",
                workspace.parent.parent / "mncs-atlas",
            )
        )
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if _looks_like_atlas(resolved):
            return resolved
    return None


def load_atlas_context(
    workspace: Path,
    *,
    atlas_root: Path | None = None,
    timeout: float = CAPSULE_TIMEOUT_SECONDS,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> AtlasContext:
    """Load a bounded capsule from the local Atlas CLI, failing open.

    Atlas remains authoritative for registry semantics.  A missing, stale, or
    malformed local checkout must not prevent ordinary Harness operation.
    """

    root = find_atlas_root(workspace, atlas_root)
    if root is None:
        return AtlasContext("unavailable", detail="no local Atlas checkout found")
    invoke = runner or subprocess.run
    try:
        result = invoke(
            [sys.executable, "-m", "registry", "context", str(workspace.resolve())],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return AtlasContext("stale", atlas_root=root, detail="Atlas context query timed out")
    except OSError as exc:
        return AtlasContext("unavailable", atlas_root=root, detail=f"Atlas query failed: {exc}")

    output = result.stdout.strip()
    if result.returncode != 0:
        detail = result.stderr.strip() or f"Atlas query exited with status {result.returncode}"
        return AtlasContext("stale", atlas_root=root, detail=detail[:500])
    if not output.startswith(CAPSULE_HEADER):
        return AtlasContext("stale", atlas_root=root, detail="Atlas returned an invalid capsule")
    if len(output) > CAPSULE_MAX_CHARS:
        return AtlasContext("stale", atlas_root=root, detail="Atlas capsule exceeded local size bound")
    return AtlasContext("available", text=output, atlas_root=root)
