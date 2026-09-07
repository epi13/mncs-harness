"""Runtime build identity of a loaded package, not a neighboring checkout.

A sibling Git HEAD is developer context. This module binds identity to the
module that is actually imported by the running process.
"""

from __future__ import annotations

import hashlib
import importlib
import subprocess
from pathlib import Path
from typing import Any


def _git_head(root: Path) -> str | None:
    if not (root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = (result.stdout or "").strip()
    return value or None


def _package_root_from_module(module: Any) -> Path | None:
    path = getattr(module, "__file__", None)
    if not path:
        return None
    current = Path(path).resolve().parent
    for _ in range(6):
        if (current / "pyproject.toml").is_file() or (current / ".git").exists():
            return current
        if current.parent == current:
            break
        current = current.parent
    return Path(path).resolve().parent


def runtime_build_identity(
    package: str,
    *,
    version: str | None = None,
    source_commit: str | None = None,
    artifact_digest: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe the running package. Missing immutable fields stay explicit."""

    loaded_version = version
    module_path = None
    loaded_commit = source_commit
    try:
        module = importlib.import_module(package)
    except Exception:
        module = None
    if module is not None:
        loaded_version = loaded_version or str(getattr(module, "__version__", None) or "unknown")
        module_path = str(getattr(module, "__file__", None) or "")
        if loaded_commit is None:
            root = _package_root_from_module(module)
            loaded_commit = _git_head(root) if root is not None else None
    identity = {
        "package": package,
        "version": loaded_version or "unknown",
        "source_commit": loaded_commit,
        "artifact_digest": artifact_digest,
        "module_path": module_path,
    }
    if extra:
        identity.update(extra)
    digest_source = f"{identity['package']}|{identity['version']}|{identity['source_commit'] or ''}|{identity['artifact_digest'] or ''}"
    identity["build_identity"] = (
        "sha256:" + hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
        if loaded_commit or artifact_digest
        else None
    )
    return identity


def identity_is_complete(identity: dict[str, Any] | None) -> bool:
    if not isinstance(identity, dict):
        return False
    if not identity.get("package") or not identity.get("version"):
        return False
    return bool(identity.get("source_commit") or identity.get("artifact_digest") or identity.get("build_identity"))
