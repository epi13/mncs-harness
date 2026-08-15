"""Operational provenance for one MNCS experiment-stack epoch.

This record identifies the orchestration stack that produced evidence. It is
not a scientific result and does not restate normative MNCS rules. Branch
names are never sufficient provenance. Sibling Git HEADs are supplemental
diagnostics and must not masquerade as runtime evidence.
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
PROVENANCE_COMPLETE = "COMPLETE"
PROVENANCE_INCOMPLETE = "PROVENANCE_INCOMPLETE"

REQUIRED_RUNTIME_KEYS = (
    "control",
    "harness",
    "fabric_controller",
    "commons",
    "reference_studies",
)


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
    """Supplemental checkout HEADs. Not runtime evidence."""

    roots = sibling_roots(harness_root)
    return {name: _git_head(path) for name, path in roots.items()}


def _runtime_commit(identities: Mapping[str, Any], key: str) -> str | None:
    value = identities.get(key)
    if isinstance(value, dict):
        return value.get("source_commit") or value.get("artifact_digest")
    if isinstance(value, str) and value:
        return value
    return None


def provenance_gaps(
    *,
    runtime_identities: Mapping[str, Any] | None,
    worker_service_versions: Mapping[str, str] | None = None,
    desired_state_identities: Mapping[str, str] | None = None,
    model_records: list[Mapping[str, Any]] | None = None,
) -> list[str]:
    gaps: list[str] = []
    identities = dict(runtime_identities or {})
    for key in REQUIRED_RUNTIME_KEYS:
        identity = identities.get(key)
        if not isinstance(identity, dict):
            gaps.append(f"{key}:missing")
            continue
        if not identity.get("source_commit") and not identity.get("artifact_digest"):
            gaps.append(f"{key}:no_immutable_identity")
    if not worker_service_versions:
        gaps.append("worker_service_versions:missing")
    if not desired_state_identities:
        gaps.append("desired_state_identities:missing")
    if model_records:
        for item in model_records:
            if not item.get("digest") and not item.get("subject_identity"):
                gaps.append(f"model:{item.get('tag') or item.get('name') or 'unknown'}:no_digest")
    return gaps


def build_experiment_stack_record(
    *,
    fabric_version: str | None = None,
    fabric_commit: str | None = None,
    worker_ids: list[str] | None = None,
    worker_service_versions: Mapping[str, str] | None = None,
    worker_build_identities: Mapping[str, Any] | None = None,
    model_identities: list[str] | None = None,
    model_records: list[Mapping[str, Any]] | None = None,
    desired_state_identities: Mapping[str, str] | None = None,
    runtime_identities: Mapping[str, Any] | None = None,
    routing_config_digest: str | None = None,
    extra: Mapping[str, Any] | None = None,
    started_at: str | None = None,
    harness_root: Path | None = None,
    require_complete: bool = False,
) -> dict[str, Any]:
    """Build a canonical, digestable experiment-stack identity."""

    checkout_commits = collect_component_commits(harness_root)
    identities = dict(runtime_identities or {})
    pins = fabric_compatibility_pins()
    runtime_fabric_commit = fabric_commit or _runtime_commit(identities, "fabric_controller")
    gaps = provenance_gaps(
        runtime_identities=identities,
        worker_service_versions=worker_service_versions,
        desired_state_identities=desired_state_identities,
        model_records=model_records,
    )
    provenance_status = PROVENANCE_INCOMPLETE if gaps else PROVENANCE_COMPLETE
    record: dict[str, Any] = {
        "schema": STACK_SCHEMA,
        "claim_boundary": CLAIM_BOUNDARY,
        "timestamp": started_at or _utc_now(),
        "harness_project": PROJECT_ID,
        "harness_version": __version__,
        "runtime_identities": identities,
        "checkout_commits": checkout_commits,
        "mncs_harness_commit": _runtime_commit(identities, "harness") or None,
        "mncs_control_commit": _runtime_commit(identities, "control") or None,
        "mncs_fabric_commit": runtime_fabric_commit,
        "mncs_commons_commit": _runtime_commit(identities, "commons") or None,
        "mncs_reference_studies_commit": _runtime_commit(identities, "reference_studies") or None,
        "fabric_version": fabric_version,
        "fabric_pins": pins,
        "worker_ids": list(worker_ids or []),
        "worker_service_versions": dict(worker_service_versions or {}),
        "worker_build_identities": dict(worker_build_identities or {}),
        "model_identities": list(model_identities or []),
        "model_records": [dict(item) for item in (model_records or [])],
        "desired_state_identities": dict(desired_state_identities or {}),
        "routing_config_digest": routing_config_digest,
        "provenance_status": provenance_status,
        "provenance_gaps": gaps,
    }
    if extra:
        record["extra"] = dict(extra)
    if require_complete and provenance_status != PROVENANCE_COMPLETE:
        record["epoch_freeze"] = "BLOCKED"
    else:
        record["epoch_freeze"] = "ALLOWED" if provenance_status == PROVENANCE_COMPLETE else "BLOCKED"
    identity_body = {key: value for key, value in record.items() if key != "timestamp"}
    record["experiment_stack_identity"] = digest_record(identity_body)
    record["record_digest"] = digest_record(record)
    return record
