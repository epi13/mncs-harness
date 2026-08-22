"""Canonical MNCS experiment-readiness contract.

Schema: mncs.experiment-readiness.v1

Harness owns orchestration-stack readiness. Consumers project this result
and may add protected-environment evidence. This gate never auto-repairs
and never classifies infrastructure smoke as a research result.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any, Mapping

from .experiment_stack import CLAIM_BOUNDARY, PROVENANCE_INCOMPLETE, build_experiment_stack_record
from .fabric_compat import evaluate_experiment_fabric

READINESS_SCHEMA = "mncs.experiment-readiness.v1"
READY = "READY"
DEGRADED = "DEGRADED"
BLOCKED = "BLOCKED"
UNKNOWN = "UNKNOWN"

try:
    from mncs_fabric.conformance import UNRESOLVED_UPDATE_STATES
    from mncs_fabric.management import SCHEDULABLE_STATES
except Exception:  # pragma: no cover - Fabric may be absent in isolated unit tests
    UNRESOLVED_UPDATE_STATES = frozenset({
        "UPDATE_PLANNED",
        "DRAINING",
        "UPDATE_APPLYING",
        "UPDATE_APPLIED",
        "RESTART_PENDING",
        "DISCONNECT_EXPECTED",
        "RECONNECTING",
        "VERSION_VERIFYING",
        "CERTIFYING",
        "ROLLBACK_APPLYING",
    })
    SCHEDULABLE_STATES = frozenset({"READY", "BUSY"})

FRESH_INVENTORY_STATUSES = frozenset({"CURRENT", "FRESH"})
PROFILES = {
    "base-inference": {
        "required": (
            "harness",
            "fabric_controller",
            "fleet",
            "workers",
            "models",
            "commons_consumer",
            "artifact_write",
        ),
        "optional": ("control", "commons_operator", "joern", "forge", "scheduler", "reference_studies"),
        "roles": ("generation",),
    },
    "code-analysis": {
        "required": (
            "harness",
            "fabric_controller",
            "fleet",
            "workers",
            "models",
            "commons_consumer",
            "artifact_write",
            "joern",
            "forge",
        ),
        "optional": ("control", "commons_operator", "scheduler", "reference_studies"),
        "roles": ("generation", "review"),
    },
    "multi-agent": {
        "required": (
            "harness",
            "fabric_controller",
            "fleet",
            "workers",
            "models",
            "routing",
            "commons_consumer",
            "artifact_write",
        ),
        "optional": ("control", "commons_operator", "joern", "forge", "scheduler", "reference_studies"),
        "roles": ("generation", "review"),
    },
    "sustained-experiment": {
        "required": (
            "harness",
            "fabric_controller",
            "fleet",
            "workers",
            "models",
            "routing",
            "residency",
            "commons_consumer",
            "artifact_write",
        ),
        "optional": (
            "control",
            "commons_operator",
            "joern",
            "forge",
            "scheduler",
            "reference_studies",
        ),
        "roles": ("generation", "review"),
    },
    "MNEL": {
        "required": (
            "harness",
            "fabric_controller",
            "fleet",
            "workers",
            "models",
            "routing",
            "commons_consumer",
            "commons_operator",
            "reference_studies",
            "artifact_write",
        ),
        "optional": ("control", "joern", "forge", "scheduler"),
        "roles": ("generation", "review"),
    },
    "RAVEL": {
        "required": (
            "harness",
            "fabric_controller",
            "fleet",
            "workers",
            "models",
            "commons_consumer",
            "reference_studies",
            "forge",
            "artifact_write",
        ),
        "optional": ("control", "commons_operator", "joern", "scheduler"),
        "roles": ("generation",),
    },
}


def _layer(name: str, status: str, detail: Any, evidence: str | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "evidence": evidence,
    }


def _overall(layers: list[dict[str, Any]], required: tuple[str, ...]) -> tuple[str, list[dict[str, Any]]]:
    by_name = {item["name"]: item["status"] for item in layers}
    required_states = [by_name.get(name, UNKNOWN) for name in required]
    if any(state == BLOCKED for state in required_states):
        status = BLOCKED
    elif any(state == UNKNOWN for state in required_states):
        status = UNKNOWN
    elif any(state == DEGRADED for state in required_states):
        status = DEGRADED
    else:
        status = READY
    warnings = [
        {
            "layer": item["name"],
            "status": item["status"],
            "detail": item.get("detail"),
        }
        for item in layers
        if item["name"] not in required and item["status"] in {DEGRADED, BLOCKED, UNKNOWN}
    ]
    return status, warnings


def probe_artifact_write(path: Any) -> dict[str, Any]:
    """Bounded create/write/read/delete on the intended experiment destination."""

    target = Path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"writable": False, "path": str(target), "reason": f"mkdir failed: {exc}"}
    if not target.is_dir():
        return {"writable": False, "path": str(target), "reason": "path is not a directory"}
    marker = target / f".mncs-experiment-readiness-{os.getpid()}-{secrets.token_hex(8)}.tmp"
    payload = b"mncs.experiment-readiness.v1\n"
    try:
        with marker.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        read_back = marker.read_bytes()
        if read_back != payload:
            return {"writable": False, "path": str(target), "reason": "readback mismatch"}
        return {"writable": True, "path": str(target), "tested_marker": marker.name}
    except OSError as exc:
        return {"writable": False, "path": str(target), "reason": str(exc)}
    finally:
        try:
            marker.unlink()
        except OSError:
            pass


def os_access_writable(path: Any) -> bool:
    """Legacy helper. Prefer probe_artifact_write for experiment readiness."""

    return os.access(Path(path), os.W_OK)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _upper(value: Any) -> str:
    return str(value or "").upper()


def _management_record(worker: Mapping[str, Any]) -> dict[str, Any]:
    management = worker.get("management")
    if isinstance(management, Mapping):
        inner = management.get("management")
        if isinstance(inner, Mapping) and inner.get("state"):
            return dict(inner)
        if management.get("state"):
            return dict(management)
    return {}


def _management_state(worker: Mapping[str, Any]) -> str | None:
    if worker.get("management_state"):
        return str(worker["management_state"])
    record = _management_record(worker)
    if record.get("state"):
        return str(record["state"])
    return None


def _unresolved_update(worker: Mapping[str, Any]) -> Mapping[str, Any] | None:
    transaction = worker.get("update_transaction")
    if transaction is None:
        management = worker.get("management")
        if isinstance(management, Mapping):
            transaction = management.get("update_transaction")
    if isinstance(transaction, Mapping):
        state = str(transaction.get("state") or "")
        if state in UNRESOLVED_UPDATE_STATES:
            return transaction
    return None


def _inventory_identity(worker: Mapping[str, Any]) -> str | None:
    inventory = worker.get("inventory")
    if isinstance(inventory, Mapping) and inventory.get("inventory_identity"):
        return str(inventory["inventory_identity"])
    if worker.get("inventory_identity"):
        return str(worker["inventory_identity"])
    return None


def _desired_state_identity(worker: Mapping[str, Any]) -> str | None:
    if worker.get("desired_state_identity"):
        return str(worker["desired_state_identity"])
    management = worker.get("management")
    if isinstance(management, Mapping) and management.get("desired_state_identity"):
        return str(management["desired_state_identity"])
    return None


def _certification_status(worker: Mapping[str, Any]) -> str | None:
    certification = worker.get("certification")
    if isinstance(certification, Mapping):
        if certification.get("disposition"):
            return str(certification["disposition"])
        if certification.get("status"):
            return str(certification["status"])
    record = _management_record(worker)
    if record.get("certification_status"):
        return str(record["certification_status"])
    if worker.get("certification_status"):
        return str(worker["certification_status"])
    return None


def _conformance_blocking(worker: Mapping[str, Any]) -> list[str]:
    conformance = worker.get("conformance")
    if isinstance(conformance, Mapping):
        return [str(item) for item in (conformance.get("blocking_failures") or [])]
    management = worker.get("management")
    if isinstance(management, Mapping):
        blocking = management.get("blocking_failures")
        if isinstance(blocking, list):
            return [str(item) for item in blocking]
    return []


def _conformance_disposition(worker: Mapping[str, Any]) -> str | None:
    conformance = worker.get("conformance")
    if isinstance(conformance, Mapping) and conformance.get("disposition"):
        return str(conformance["disposition"])
    if worker.get("conformance_disposition"):
        return str(worker["conformance_disposition"])
    management = worker.get("management")
    if isinstance(management, Mapping) and management.get("conformance_disposition"):
        return str(management["conformance_disposition"])
    return None


def _certification_inventory_identity(worker: Mapping[str, Any]) -> str | None:
    certification = worker.get("certification")
    if isinstance(certification, Mapping) and certification.get("inventory_identity"):
        return str(certification["inventory_identity"])
    record = _management_record(worker)
    if record.get("last_inventory_identity"):
        return str(record["last_inventory_identity"])
    return None


def _conformance_desired_identity(worker: Mapping[str, Any]) -> str | None:
    conformance = worker.get("conformance")
    if isinstance(conformance, Mapping) and conformance.get("desired_state_identity"):
        return str(conformance["desired_state_identity"])
    return _desired_state_identity(worker)


def evaluate_worker_experiment_eligibility(
    worker: Mapping[str, Any],
    *,
    require_fresh_inventory: bool = True,
) -> dict[str, Any]:
    """Project Fabric management authority. Availability alone is not READY."""

    availability = _upper(worker.get("availability"))
    management_state = _management_state(worker)
    certification = _certification_status(worker)
    inventory_status = _upper(worker.get("capability_inventory_status"))
    inventory_identity = _inventory_identity(worker)
    desired_identity = _desired_state_identity(worker)
    cert_inventory = _certification_inventory_identity(worker)
    conformance_desired = _conformance_desired_identity(worker)
    blocking = _conformance_blocking(worker)
    conformance_disposition = _conformance_disposition(worker)
    unresolved = _unresolved_update(worker)
    schedulable = worker.get("schedulable")
    ready_decision = worker.get("ready_decision")
    service_compatible = worker.get("service_compatible")
    blockers: list[str] = []

    if availability != "AVAILABLE":
        blockers.append(f"availability:{availability or 'UNKNOWN'}")

    if isinstance(ready_decision, Mapping) and ready_decision.get("ready") is False:
        blockers.append("fabric_ready_decision")
        blockers.extend(str(item) for item in (ready_decision.get("blockers") or []) if item)
    elif isinstance(ready_decision, Mapping) and ready_decision.get("ready") is True:
        pass
    elif management_state in SCHEDULABLE_STATES and certification == "CERTIFIED":
        # Project Fabric's ledger READY decision. A freshly collected inspect
        # inventory must not be treated as the certified inventory identity.
        if schedulable is False:
            blockers.append("not_schedulable")
        if blocking:
            blockers.append("conformance_blocking")
        if unresolved is not None:
            blockers.append(f"unresolved_update:{unresolved.get('state')}")
        if service_compatible is False:
            blockers.append("incompatible_worker_service")
    else:
        if management_state is None:
            blockers.append("management_evidence_missing")
        elif management_state not in SCHEDULABLE_STATES:
            blockers.append(f"management_state:{management_state}")
        if schedulable is False:
            blockers.append("not_schedulable")
        if certification is None:
            blockers.append("certification_evidence_missing")
        elif certification != "CERTIFIED":
            blockers.append(f"certification:{certification}")
        if (
            certification == "CERTIFIED"
            and cert_inventory
            and inventory_identity
            and not worker.get("inventory_is_inspect_observation")
            and cert_inventory != inventory_identity
        ):
            blockers.append("certification_inventory_mismatch")
        if blocking:
            blockers.append("conformance_blocking")
        elif conformance_disposition is None and management_state is not None:
            blockers.append("conformance_evidence_missing")
        if desired_identity and conformance_desired and desired_identity != conformance_desired:
            blockers.append("conformance_desired_state_mismatch")
        if unresolved is not None:
            blockers.append(f"unresolved_update:{unresolved.get('state')}")
        if service_compatible is False:
            blockers.append("incompatible_worker_service")

    inventory_fresh = inventory_status in FRESH_INVENTORY_STATUSES
    operational_available = availability == "AVAILABLE"
    fabric_eligible = operational_available and not blockers
    strict_epoch_eligible = fabric_eligible and (inventory_fresh if require_fresh_inventory else True)
    if not operational_available:
        status = BLOCKED
    elif not fabric_eligible:
        status = DEGRADED
    elif require_fresh_inventory and not inventory_fresh:
        status = DEGRADED
        blockers.append(f"capability_inventory:{inventory_status or 'UNKNOWN'}")
    else:
        status = READY
    return {
        "worker_id": worker.get("worker_id"),
        "status": status,
        "availability": availability or None,
        "management_state": management_state,
        "certification_status": certification,
        "capability_inventory_status": inventory_status or None,
        "inventory_fresh": inventory_fresh,
        "desired_state_identity": desired_identity,
        "inventory_identity": inventory_identity,
        "operational_available": operational_available,
        "experiment_eligible": strict_epoch_eligible,
        "fabric_eligible": fabric_eligible,
        "strict_epoch_eligible": strict_epoch_eligible,
        "blockers": blockers,
        "worker_service_version": worker.get("worker_service_version"),
        "schedulable": schedulable,
    }


def _model_name(item: Any) -> str | None:
    if isinstance(item, Mapping):
        return item.get("name") or item.get("tag") or item.get("model")
    if isinstance(item, str) and item:
        return item
    return None


def _model_digest(item: Mapping[str, Any]) -> str | None:
    return (
        item.get("digest")
        or item.get("subject_identity")
        or (item.get("attributes") or {}).get("digest")
    )


def _observed_failure(item: Mapping[str, Any]) -> bool:
    observed = str(item.get("observed") or item.get("verification") or item.get("observation") or "").upper()
    return observed in {"FAIL", "OBSERVED_FAIL", "FAILED"}


def evaluate_model_experiment_eligibility(
    model: Mapping[str, Any] | str,
    worker: Mapping[str, Any],
    *,
    required_role: str | None = None,
) -> dict[str, Any]:
    payload = dict(model) if isinstance(model, Mapping) else {"name": model}
    name = _model_name(payload)
    digest = _model_digest(payload) if isinstance(model, Mapping) else None
    worker_eval = (
        worker
        if worker.get("experiment_eligible") is not None and "blockers" in worker
        else evaluate_worker_experiment_eligibility(worker)
    )
    blockers: list[str] = []
    if not name:
        blockers.append("model_identity_unknown")
    if not worker_eval.get("fabric_eligible"):
        blockers.append("worker_not_experiment_eligible")
    elif not worker_eval.get("experiment_eligible"):
        blockers.append("worker_inventory_not_fresh")
    if _observed_failure(payload):
        blockers.append("observed_fail")
    if payload.get("routing_permitted") is False:
        blockers.append("routing_denied")
    if required_role and payload.get("roles") and required_role not in set(payload.get("roles") or []):
        blockers.append(f"role_unsatisfied:{required_role}")
    operational = (
        bool(name)
        and bool(worker_eval.get("fabric_eligible"))
        and "observed_fail" not in blockers
        and "routing_denied" not in blockers
    )
    return {
        "name": name,
        "digest": digest,
        "provider": payload.get("namespace") or payload.get("provider") or payload.get("runtime"),
        "worker_id": worker_eval.get("worker_id"),
        "eligible": not blockers,
        "operational_eligible": operational,
        "blockers": blockers,
        "observed": payload.get("observed") or payload.get("verification"),
    }


def digest_routing_config(payload: Mapping[str, Any] | None) -> str | None:
    if not payload:
        return None
    sanitized = {
        key: payload[key]
        for key in (
            "profile",
            "roles",
            "fallback_policy",
            "worker_eligibility",
            "resident_model_policy",
            "automatic_placement",
            "local_fallback",
            "fallback_explicit",
        )
        if key in payload
    }
    encoded = json.dumps(sanitized, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def evaluate_layers(
    *,
    control: Mapping[str, Any] | None = None,
    harness: Mapping[str, Any] | None = None,
    fabric: Mapping[str, Any] | None = None,
    commons: Mapping[str, Any] | None = None,
    forge: Mapping[str, Any] | None = None,
    joern: Mapping[str, Any] | None = None,
    reference_studies: Mapping[str, Any] | None = None,
    routing: Mapping[str, Any] | None = None,
    residency: Mapping[str, Any] | None = None,
    scheduler: Mapping[str, Any] | None = None,
    artifact_write: Mapping[str, Any] | None = None,
    runtime_identities: Mapping[str, Any] | None = None,
    profile: str = "base-inference",
    require_fresh_inventory: bool = True,
    require_complete_provenance: bool = True,
) -> dict[str, Any]:
    """Classify a supplied evidence snapshot. Callers must not mutate systems."""

    if profile not in PROFILES:
        raise ValueError(f"unknown experiment profile: {profile}")
    layers: list[dict[str, Any]] = []

    control_state = (control or {}).get("status") or (
        READY if control and control.get("available") else UNKNOWN if control is None else BLOCKED
    )
    layers.append(_layer("control", str(control_state), dict(control or {}), (control or {}).get("evidence")))

    harness_state = (harness or {}).get("status") or (
        READY if harness and harness.get("available") else UNKNOWN if harness is None else BLOCKED
    )
    layers.append(_layer("harness", str(harness_state), dict(harness or {}), (harness or {}).get("evidence")))

    fabric = dict(fabric or {})
    compat = evaluate_experiment_fabric(
        str(fabric.get("version") or fabric.get("controller_version") or "unknown"),
        fabric.get("capabilities") or fabric.get("persistent_service_support") or {},
        commit=fabric.get("commit") or fabric.get("source_commit"),
        artifact_digest=fabric.get("artifact_digest"),
    )
    if compat["action"] != "dispatch_allowed":
        fabric_state = BLOCKED
    elif fabric.get("controller_connected") is False:
        fabric_state = BLOCKED
    elif fabric.get("available"):
        fabric_state = READY
    else:
        fabric_state = UNKNOWN
    layers.append(
        _layer(
            "fabric_controller",
            fabric_state,
            {
                "compatibility": compat,
                "version": fabric.get("version") or fabric.get("controller_version"),
                "available": fabric.get("available"),
                "exact_certification": compat["classification"] == "EXPERIMENT_CERTIFIED_EXACT",
            },
            fabric.get("contract_identity"),
        )
    )

    workers = list(fabric.get("workers") or fabric.get("known_nodes") or [])
    worker_evals = [
        evaluate_worker_experiment_eligibility(worker, require_fresh_inventory=require_fresh_inventory)
        for worker in workers
    ]
    available = [item for item in worker_evals if item["operational_available"]]
    eligible = [item for item in worker_evals if item["experiment_eligible"]]
    stale_inventory = [
        item
        for item in worker_evals
        if item["operational_available"] and not item["inventory_fresh"]
    ]
    if not workers:
        fleet_state = UNKNOWN
        worker_state = UNKNOWN
        fleet_detail: Any = "no worker observations"
    elif not available:
        fleet_state = BLOCKED
        worker_state = BLOCKED
        fleet_detail = "no AVAILABLE workers"
    elif not eligible:
        fleet_state = DEGRADED
        worker_state = DEGRADED
        fleet_detail = {
            "available": [item.get("worker_id") for item in available],
            "experiment_eligible": [],
            "stale_capability_inventory": [item.get("worker_id") for item in stale_inventory],
            "note": "STALE capability inventory is not worker UNAVAILABLE",
            "experiment_note": "STALE is not fresh experiment certification",
        }
    else:
        fleet_state = READY
        worker_state = READY
        fleet_detail = {
            "available": [item.get("worker_id") for item in available],
            "experiment_eligible": [item.get("worker_id") for item in eligible],
            "stale_capability_inventory": [item.get("worker_id") for item in stale_inventory],
            "note": "STALE capability inventory is not worker UNAVAILABLE",
            "experiment_note": "STALE is not fresh experiment certification",
        }
    layers.append(_layer("fleet", fleet_state, fleet_detail, fabric.get("fleet_identity")))
    layers.append(_layer("workers", worker_state, {"workers": worker_evals}))

    model_records: list[dict[str, Any]] = []
    workers_by_id = {str(worker.get("worker_id")): worker for worker in workers if worker.get("worker_id")}
    evals_by_id = {str(item.get("worker_id")): item for item in worker_evals if item.get("worker_id")}
    for worker in workers:
        worker_id = str(worker.get("worker_id") or "")
        worker_eval = evals_by_id.get(worker_id) or evaluate_worker_experiment_eligibility(worker)
        for item in worker.get("model_inventory") or worker.get("installed_models") or []:
            model_records.append(evaluate_model_experiment_eligibility(item, {**worker, **worker_eval}))
    for item in fabric.get("models") or []:
        if isinstance(item, Mapping) and item.get("worker") and item.get("worker") in workers_by_id:
            worker = workers_by_id[str(item.get("worker"))]
            worker_eval = evals_by_id.get(str(item.get("worker"))) or evaluate_worker_experiment_eligibility(worker)
            model_records.append(evaluate_model_experiment_eligibility(item, {**worker, **worker_eval}))
        elif not model_records:
            model_records.append(
                evaluate_model_experiment_eligibility(
                    item,
                    {"worker_id": None, "availability": None},
                )
            )
    eligible_models = [item for item in model_records if item.get("eligible")]
    operational_models = [item for item in model_records if item.get("operational_eligible")]
    if eligible_models:
        model_state = READY
    elif operational_models:
        model_state = DEGRADED
    elif available and not model_records:
        model_state = BLOCKED
    elif model_records:
        model_state = BLOCKED
    else:
        model_state = UNKNOWN
    layers.append(
        _layer(
            "models",
            model_state,
            {
                "count": len(model_records),
                "eligible_count": len(eligible_models),
                "identities": [
                    {
                        "provider": item.get("provider"),
                        "tag": item.get("name"),
                        "digest": item.get("digest"),
                        "worker": item.get("worker_id"),
                    }
                    for item in model_records[:32]
                ],
            },
        )
    )

    routing = dict(routing or {})
    if routing.get("status"):
        route_state = str(routing["status"])
    elif routing.get("local_fallback") is True and routing.get("fallback_explicit") is not True:
        route_state = BLOCKED
    elif eligible and routing.get("available") is not False:
        route_state = READY
    elif available and routing.get("available") is True:
        route_state = DEGRADED
    elif routing:
        route_state = DEGRADED
    else:
        route_state = UNKNOWN
    layers.append(_layer("routing", route_state, routing))

    residency = dict(residency or {})
    residency_features = residency.get("persistent_service_support") or {}
    lifecycle_transport = all(
        isinstance(residency_features, Mapping) and residency_features.get(name) is True
        for name in (
            "persistent_service_execution",
            "persistent_detached_execution",
            "persistent_service_capability_ingestion",
        )
    )
    if residency.get("experiment_keep_alive") == 0:
        residency_state = BLOCKED
    elif lifecycle_transport and residency.get("provider_lifecycle_supported") is True:
        residency_state = READY
    elif residency:
        residency_state = BLOCKED
    else:
        residency_state = UNKNOWN
    layers.append(
        _layer(
            "residency",
            residency_state,
            {
                **residency,
                "lifecycle_transport": lifecycle_transport,
                "model_residency_is_conversation_state": False,
                "conversation_state_authority": "mncs-control-mcp/harness caller",
            },
        )
    )

    commons = dict(commons or {})
    consumer_ready = commons.get("consumerReadCapable") or commons.get("read_capable") or commons.get("available")
    operator_ready = commons.get("operatorPublicationCapable") or commons.get("publication_capable")
    layers.append(
        _layer(
            "commons_consumer",
            READY if consumer_ready else BLOCKED if commons else UNKNOWN,
            commons,
            commons.get("evidence"),
        )
    )
    layers.append(
        _layer(
            "commons_operator",
            READY if operator_ready else DEGRADED if commons else UNKNOWN,
            {
                "operator_publication_independent_of_model_policy": True,
                "ready": bool(operator_ready),
            },
        )
    )

    forge = dict(forge or {})
    if forge.get("status"):
        forge_state = str(forge["status"])
    elif forge.get("callable") is True or forge.get("sandbox_callable") is True:
        forge_state = READY
    elif forge.get("available"):
        forge_state = DEGRADED
    elif forge:
        forge_state = DEGRADED
    else:
        forge_state = UNKNOWN
    layers.append(_layer("forge", forge_state, forge))

    joern = dict(joern or {})
    if joern.get("status"):
        joern_state = str(joern["status"])
    elif joern.get("sandbox_callable") is True:
        joern_state = READY
    elif joern.get("host_visible") or joern.get("available"):
        joern_state = DEGRADED
    elif joern:
        joern_state = DEGRADED
    else:
        joern_state = UNKNOWN
    layers.append(_layer("joern", joern_state, joern))

    studies = dict(reference_studies or {})
    ravel_limitation = studies.get("ravel_0_5_limitation") or studies.get("historical_limitation")
    current_ravel_valid = studies.get("current_ravel_lane_valid")
    if studies.get("status"):
        studies_state = str(studies["status"])
    elif studies.get("commit") and studies.get("schema_available") is not False and studies.get("available") is not False:
        studies_state = READY
    elif studies.get("available"):
        studies_state = DEGRADED
    elif studies:
        studies_state = DEGRADED
    else:
        studies_state = UNKNOWN
    if profile == "RAVEL" and (
        (isinstance(ravel_limitation, Mapping) and ravel_limitation)
        or current_ravel_valid is False
    ):
        if current_ravel_valid is not True:
            studies_state = BLOCKED
            studies = {
                **studies,
                "ravel_blocked_reason": "historical 0.5 canonical evidence unavailable",
            }
    layers.append(_layer("reference_studies", studies_state, studies))

    scheduler = dict(scheduler or {})
    layers.append(
        _layer(
            "scheduler",
            READY if scheduler.get("available") else DEGRADED if scheduler else UNKNOWN,
            scheduler,
        )
    )
    artifacts = dict(artifact_write or {})
    layers.append(
        _layer(
            "artifact_write",
            READY if artifacts.get("writable") is True else BLOCKED if artifacts else UNKNOWN,
            artifacts,
        )
    )

    required = PROFILES[profile]["required"]
    status, optional_warnings = _overall(layers, required)
    desired_state_identities = {
        str(item.get("worker_id")): str(item.get("desired_state_identity"))
        for item in worker_evals
        if item.get("worker_id") and item.get("desired_state_identity")
    }
    routing_digest = digest_routing_config(
        {
            "profile": profile,
            "roles": list(PROFILES[profile]["roles"]),
            "fallback_policy": routing.get("fallback_policy") or routing.get("local_fallback"),
            "worker_eligibility": [item.get("worker_id") for item in eligible],
            "resident_model_policy": routing.get("resident_model_policy"),
            "automatic_placement": routing.get("automatic_placement"),
            "local_fallback": routing.get("local_fallback"),
            "fallback_explicit": routing.get("fallback_explicit"),
        }
    )
    stack = build_experiment_stack_record(
        fabric_version=str(fabric.get("version") or fabric.get("controller_version") or ""),
        fabric_commit=fabric.get("commit") or fabric.get("source_commit"),
        worker_ids=[str(worker.get("worker_id")) for worker in workers if worker.get("worker_id")],
        worker_service_versions={
            str(worker.get("worker_id")): str(worker.get("worker_service_version"))
            for worker in workers
            if worker.get("worker_id") and worker.get("worker_service_version")
        },
        worker_build_identities={
            str(worker.get("worker_id")): worker.get("runtime_identity") or worker.get("build_identity")
            for worker in workers
            if worker.get("worker_id") and (worker.get("runtime_identity") or worker.get("build_identity"))
        },
        model_identities=[str(item.get("name")) for item in model_records if item.get("name")],
        model_records=[
            {
                "provider": item.get("provider"),
                "tag": item.get("name"),
                "digest": item.get("digest"),
                "worker": item.get("worker_id"),
            }
            for item in model_records
            if item.get("name")
        ],
        desired_state_identities=desired_state_identities,
        runtime_identities=runtime_identities,
        routing_config_digest=routing_digest,
        require_complete=require_complete_provenance,
    )
    if require_complete_provenance and stack.get("provenance_status") == PROVENANCE_INCOMPLETE:
        if status == READY:
            status = DEGRADED
    return {
        "schema": READINESS_SCHEMA,
        "status": status,
        "profile": profile,
        "profile_status": status,
        "claim_boundary": CLAIM_BOUNDARY,
        "layers": {item["name"]: item for item in layers},
        "required_layers": list(required),
        "optional_layers": list(PROFILES[profile]["optional"]),
        "optional_warnings": optional_warnings,
        "experiment_stack": stack,
        "local_fallback": bool(routing.get("local_fallback")),
        "local_fallback_explicit": bool(routing.get("fallback_explicit")),
        "fabric_classification": compat["classification"],
    }


def _enrich_workers_from_inspect(client: Any, workers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if client is None or not hasattr(client, "inspect_fleet"):
        return workers
    try:
        inspected = client.inspect_fleet()
    except Exception:
        return workers
    by_id: dict[str, dict[str, Any]] = {}
    for item in inspected.get("workers") or []:
        if not isinstance(item, Mapping):
            continue
        management = item.get("management") if isinstance(item.get("management"), Mapping) else {}
        inventory = item.get("inventory") if isinstance(item.get("inventory"), Mapping) else {}
        worker_id = str(
            item.get("worker_id")
            or management.get("worker_id")
            or inventory.get("worker_identity")
            or ""
        )
        if worker_id:
            by_id[worker_id] = dict(item)
    enriched: list[dict[str, Any]] = []
    for worker in workers:
        merged = dict(worker)
        extra = by_id.get(str(worker.get("worker_id") or ""))
        if extra:
            management = extra.get("management") if isinstance(extra.get("management"), Mapping) else {}
            inventory = extra.get("inventory") if isinstance(extra.get("inventory"), Mapping) else {}
            merged["management"] = management
            inner = management.get("management") if isinstance(management.get("management"), Mapping) else management
            if isinstance(inner, Mapping):
                merged["management_state"] = inner.get("state")
                merged["certification_status"] = inner.get("certification_status")
                merged["inventory_identity"] = inner.get("last_inventory_identity") or inventory.get(
                    "inventory_identity"
                )
            merged["schedulable"] = management.get("schedulable")
            merged["desired_state_identity"] = management.get("desired_state_identity")
            merged["conformance_disposition"] = management.get("conformance_disposition")
            merged["update_transaction"] = management.get("update_transaction")
            if inventory:
                merged["inspect_inventory"] = inventory
                merged["inventory_is_inspect_observation"] = True
                fabric = inventory.get("fabric") if isinstance(inventory.get("fabric"), Mapping) else {}
                merged["worker_service_version"] = merged.get("worker_service_version") or fabric.get(
                    "worker_version"
                )
                if fabric.get("source_commit") or fabric.get("artifact_digest"):
                    merged["runtime_identity"] = {
                        "package": "mncs-fabric",
                        "version": fabric.get("worker_version"),
                        "source_commit": fabric.get("source_commit"),
                        "artifact_digest": fabric.get("artifact_digest"),
                    }
        enriched.append(merged)
    return enriched


def inspect_live_config(config: Any, *, profile: str = "base-inference") -> dict[str, Any]:
    """Observe the loaded Harness configuration without mutating the fleet."""

    from . import __version__
    from .commons import CommonsSession
    from .fabric import FabricSession
    from .fleet import FleetService
    from .runtime_identity import runtime_build_identity

    fabric_session = FabricSession(config.fabric)
    fabric_session.initialize()
    fabric_status = fabric_session.status()
    fleet = FleetService(config, fabric_session)
    snapshot = fleet.snapshot(fabric_status)
    commons_session = CommonsSession(config.commons)
    commons_session.initialize()
    commons = commons_session.status()
    metrics_path = Path(config.metrics.path)
    artifact = probe_artifact_write(metrics_path.parent)
    workers = list(fabric_status.workers or (snapshot.get("fabric") or {}).get("workers") or [])
    client = getattr(fabric_session, "client", None)
    workers = _enrich_workers_from_inspect(client, [dict(worker) for worker in workers])
    capabilities: dict[str, Any] = {}
    controller_status: dict[str, Any] = {}
    try:
        from .fabric_compat import EXPERIMENT_REQUIRED_CAPABILITIES

        status = client.controller_status() if client is not None else {}
        controller_status = status if isinstance(status, dict) else {}
        features = controller_status.get("service_features") if isinstance(controller_status, dict) else {}
        if not isinstance(features, dict):
            features = {}
        capabilities = {name: features.get(name) is True for name in EXPERIMENT_REQUIRED_CAPABILITIES}
    except Exception:
        capabilities = {}
    studies_root = Path(__file__).resolve().parents[3] / "mncs-reference-studies"
    studies_commit = None
    schema_available = False
    ravel_limitation = None
    if studies_root.is_dir():
        from .experiment_stack import _git_head

        studies_commit = _git_head(studies_root)
        schema_available = (studies_root / "schemas" / "study.schema.json").is_file()
        limitation_path = studies_root / "case-studies" / "ravel" / "ravel-0.5-historical-limitation.json"
        if limitation_path.is_file():
            try:
                ravel_limitation = json.loads(limitation_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                ravel_limitation = {"disposition": "KNOWN_HISTORICAL_LIMITATION"}
        elif not (
            studies_root / "case-studies" / "ravel" / "ravel-0.5-source-and-execution-manifest.json"
        ).is_file():
            ravel_limitation = {"disposition": "KNOWN_HISTORICAL_LIMITATION"}
    fabric_identity = controller_status.get("runtime_identity") if isinstance(controller_status, dict) else None
    if not isinstance(fabric_identity, dict):
        fabric_identity = {
            "package": "mncs-fabric",
            "version": fabric_status.controller_version,
            "source_commit": controller_status.get("source_commit") if isinstance(controller_status, dict) else None,
            "artifact_digest": controller_status.get("artifact_digest") if isinstance(controller_status, dict) else None,
            "build_identity": None,
            "note": "imported package checkout is not the running controller",
        }
    return evaluate_layers(
        harness={
            "available": True,
            "status": READY,
            "version": __version__,
            "runtime_identity": runtime_build_identity("epi13_local_harness", version=__version__),
        },
        fabric={
            "available": fabric_status.state in {"available", "configured"},
            "version": fabric_status.controller_version,
            "controller_connected": fabric_status.state == "available",
            "persistent_service_support": capabilities,
            "workers": workers,
            "commit": fabric_identity.get("source_commit") if isinstance(fabric_identity, dict) else None,
            "source_commit": fabric_identity.get("source_commit") if isinstance(fabric_identity, dict) else None,
            "artifact_digest": fabric_identity.get("artifact_digest") if isinstance(fabric_identity, dict) else None,
            "contract_identity": getattr(fabric_status, "controller_contract_identity", None)
            or (controller_status.get("public_contract_identity") if isinstance(controller_status, dict) else None),
        },
        commons={
            "available": commons.ready,
            "consumerReadCapable": commons.read_capable or commons.ready,
            "operatorPublicationCapable": commons.publication_capable,
            "code": commons.code,
            "record_count": commons.record_count,
        },
        routing={
            "available": any(
                str(worker.get("availability") or "").upper() == "AVAILABLE" for worker in workers
            ),
            "local_fallback": bool(getattr(config.fabric, "fallback_to_local", False)),
            "fallback_explicit": True,
            "resident_model_policy": "experiment-pinned",
        },
        residency={
            "persistent_service_support": capabilities,
            "provider_lifecycle_supported": any(
                str(model.get("namespace") or model.get("provider") or "ollama") == "ollama"
                for worker in workers
                for model in worker.get("model_inventory") or []
                if isinstance(model, Mapping)
            ),
            "experiment_keep_alive": config.model_residency.experiment_keep_alive,
            "release_on_experiment_end": config.model_residency.release_on_experiment_end,
            "max_pinned_models_per_worker": (
                config.model_residency.max_pinned_models_per_worker
            ),
            "observation_max_age_seconds": (
                config.model_residency.observation_max_age_seconds
            ),
            "current_worker_observations": [
                {
                    "worker_id": worker.get("worker_id"),
                    "inventory_status": worker.get("capability_inventory_status"),
                    "loaded_model_names": worker.get("loaded_model_names", []),
                    "captured_at": (
                        (worker.get("capability_observation") or {}).get("captured_at")
                    ),
                }
                for worker in workers
            ],
        },
        artifact_write=artifact,
        scheduler={"available": True, "detail": "inspection only; no schedule tick"},
        reference_studies={
            "available": studies_root.is_dir(),
            "path": str(studies_root) if studies_root.is_dir() else None,
            "commit": studies_commit,
            "schema_available": schema_available,
            "output_path": str(studies_root / "evidence" / "actual") if studies_root.is_dir() else None,
            "ravel_0_5_limitation": ravel_limitation,
            "current_ravel_lane_valid": False if ravel_limitation else None,
        },
        runtime_identities={
            "harness": runtime_build_identity("epi13_local_harness", version=__version__),
            "fabric_controller": fabric_identity,
            "commons": runtime_build_identity("mncs_commons"),
            "control": runtime_build_identity("mncs_control_mcp"),
            "reference_studies": {
                "package": "mncs-reference-studies",
                "version": None,
                "source_commit": studies_commit,
                "artifact_digest": None,
            },
        },
        profile=profile,
    )
