"""Operational provenance for one MNCS experiment-stack epoch.

This record identifies the orchestration stack that produced evidence. It is
not a scientific result and does not restate normative MNCS rules. Branch
names are never sufficient provenance.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from . import PROJECT_ID, __version__
from .fabric_compat import fabric_compatibility_pins

STACK_SCHEMA = "mncs.experiment-stack.v1"
CLAIM_BOUNDARY = "infrastructure validation"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def digest_record(payload: Mapping[str, Any]) -> str:
    encoded = _canonical_json(payload).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def sibling_roots(harness_root: Path | None = None) -> dict[str, Path]:
    root = (harness_root or Path(__file__).resolve().parents[2]).parent
    return {
        "mncs_harness": root / "mncs-harness",
        "mncs_fabric": root / "mncs-fabric",
        "mncs_control": root / "mncs-control-mcp",
        "mncs_commons": root / "MNCS-Commons",
        "mncs_reference_studies": root / "mncs-reference-studies",
    }


def collect_component_commits(harness_root: Path | None = None) -> dict[str, str | None]:
    roots = sibling_roots(harness_root)
    return {name: _git_head(path) for name, path in roots.items()}


def build_experiment_stack_record(
    *,
    fabric_version: str | None = None,
    fabric_commit: str | None = None,
    worker_ids: list[str] | None = None,
    worker_service_versions: Mapping[str, str] | None = None,
    model_identities: list[str] | None = None,
    desired_state_identities: Mapping[str, str] | None = None,
    extra: Mapping[str, Any] | None = None,
    started_at: str | None = None,
    harness_root: Path | None = None,
) -> dict[str, Any]:
    """Build a canonical, digestable experiment-stack identity."""

    commits = collect_component_commits(harness_root)
    pins = fabric_compatibility_pins()
    record: dict[str, Any] = {
        "schema": STACK_SCHEMA,
        "claim_boundary": CLAIM_BOUNDARY,
        "timestamp": started_at or _utc_now(),
        "harness_project": PROJECT_ID,
        "harness_version": __version__,
        "mncs_harness_commit": commits.get("mncs_harness"),
        "mncs_control_commit": commits.get("mncs_control"),
        "mncs_fabric_commit": fabric_commit or commits.get("mncs_fabric"),
        "mncs_commons_commit": commits.get("mncs_commons"),
        "mncs_reference_studies_commit": commits.get("mncs_reference_studies"),
        "fabric_version": fabric_version,
        "fabric_pins": pins,
        "worker_ids": list(worker_ids or []),
        "worker_service_versions": dict(worker_service_versions or {}),
        "model_identities": list(model_identities or []),
        "desired_state_identities": dict(desired_state_identities or {}),
    }
    if extra:
        record["extra"] = dict(extra)
    identity_body = {key: value for key, value in record.items() if key != "timestamp"}
    record["experiment_stack_identity"] = digest_record(identity_body)
    record["record_digest"] = digest_record(record)
    return record
