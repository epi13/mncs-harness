"""Bounded experiment-readiness inspection.

This gate answers whether the orchestration stack may start experiments. It
never auto-repairs, never treats service reachability as readiness, and never
classifies infrastructure smoke as a research result.
"""

from __future__ import annotations

from typing import Any, Mapping

from .experiment_stack import CLAIM_BOUNDARY, build_experiment_stack_record
from .fabric_compat import evaluate_experiment_fabric

READY = "READY"
DEGRADED = "DEGRADED"
BLOCKED = "BLOCKED"
UNKNOWN = "UNKNOWN"

PROFILES = {
    "base-inference": {
        "required": (
            "control",
            "harness",
            "fabric_controller",
            "fleet",
            "workers",
            "models",
            "commons_consumer",
            "artifact_write",
        ),
        "optional": ("commons_operator", "joern", "forge", "scheduler"),
    },
    "code-analysis": {
        "required": (
            "control",
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
        "optional": ("commons_operator", "scheduler"),
    },
    "multi-agent": {
        "required": (
            "control",
            "harness",
            "fabric_controller",
            "fleet",
            "workers",
            "models",
            "routing",
            "commons_consumer",
            "artifact_write",
        ),
        "optional": ("commons_operator", "joern", "forge", "scheduler"),
    },
    "MNEL": {
        "required": (
            "control",
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
        "optional": ("joern", "forge", "scheduler"),
    },
    "RAVEL": {
        "required": (
            "control",
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
        "optional": ("commons_operator", "joern", "scheduler"),
    },
}


def _layer(name: str, status: str, detail: Any, evidence: str | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "evidence": evidence,
    }


def _overall(layers: list[dict[str, Any]], required: tuple[str, ...]) -> str:
    by_name = {item["name"]: item["status"] for item in layers}
    required_states = [by_name.get(name, UNKNOWN) for name in required]
    if any(state == BLOCKED for state in required_states):
        return BLOCKED
    if any(state == UNKNOWN for state in required_states):
        return UNKNOWN
    if any(state == DEGRADED for state in required_states):
        return DEGRADED
    optional_failed = [
        item
        for item in layers
        if item["name"] not in required and item["status"] in {DEGRADED, BLOCKED}
    ]
    if optional_failed:
        return DEGRADED
    return READY


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
    scheduler: Mapping[str, Any] | None = None,
    artifact_write: Mapping[str, Any] | None = None,
    profile: str = "base-inference",
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
        commit=fabric.get("commit"),
    )
    if compat["action"] != "dispatch_allowed":
        fabric_state = BLOCKED
    elif fabric.get("controller_connected") is False:
        fabric_state = BLOCKED
    elif fabric.get("stale_capability_inventory") and not fabric.get("available_workers"):
        fabric_state = BLOCKED
    elif fabric.get("stale_capability_inventory"):
        fabric_state = DEGRADED
    elif fabric.get("available"):
        fabric_state = READY
    else:
        fabric_state = UNKNOWN
    layers.append(
        _layer(
            "fabric_controller",
            fabric_state,
            {"compatibility": compat, **{k: fabric.get(k) for k in ("version", "controller_version", "available")}},
            fabric.get("contract_identity"),
        )
    )

    workers = list(fabric.get("workers") or fabric.get("known_nodes") or [])
    available = [
        worker
        for worker in workers
        if str(worker.get("availability") or "").upper() == "AVAILABLE"
    ]
    stale_inventory = [
        worker
        for worker in available
        if str(worker.get("capability_inventory_status") or "").upper() == "STALE"
    ]
    unavailable = [
        worker
        for worker in workers
        if str(worker.get("availability") or "").upper() in {"UNAVAILABLE", "UNKNOWN", ""}
    ]
    if not workers:
        fleet_state = UNKNOWN
        worker_state = UNKNOWN
        fleet_detail = "no worker observations"
    elif not available:
        fleet_state = BLOCKED
        worker_state = BLOCKED
        fleet_detail = "no AVAILABLE workers"
    else:
        fleet_state = DEGRADED if stale_inventory else READY
        worker_state = DEGRADED if stale_inventory else READY
        fleet_detail = {
            "available": [worker.get("worker_id") for worker in available],
            "stale_capability_inventory": [worker.get("worker_id") for worker in stale_inventory],
            "unavailable": [worker.get("worker_id") for worker in unavailable],
            "note": "STALE capability inventory is not worker UNAVAILABLE",
        }
    layers.append(_layer("fleet", fleet_state, fleet_detail, fabric.get("fleet_identity")))
    layers.append(
        _layer(
            "workers",
            worker_state,
            {
                "workers": [
                    {
                        "worker_id": worker.get("worker_id"),
                        "availability": worker.get("availability"),
                        "capability_inventory_status": worker.get("capability_inventory_status"),
                        "worker_service_version": worker.get("worker_service_version"),
                    }
                    for worker in workers
                ]
            },
        )
    )

    models = list(fabric.get("models") or [])
    if not models and available:
        for worker in available:
            models.extend(worker.get("model_inventory") or worker.get("installed_models") or [])
    layers.append(
        _layer(
            "models",
            READY if models else UNKNOWN,
            {"count": len(models), "identities": [item.get("name") if isinstance(item, dict) else item for item in models[:32]]},
        )
    )

    routing = dict(routing or {})
    if routing.get("status"):
        route_state = str(routing["status"])
    elif routing.get("local_fallback") is True and routing.get("fallback_explicit") is not True:
        route_state = BLOCKED
    elif routing.get("available") is True:
        route_state = READY
    elif routing:
        route_state = DEGRADED
    else:
        route_state = UNKNOWN
    layers.append(_layer("routing", route_state, routing))

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
    layers.append(
        _layer(
            "forge",
            READY if forge.get("available") else DEGRADED if forge else UNKNOWN,
            forge,
        )
    )
    joern = dict(joern or {})
    layers.append(
        _layer(
            "joern",
            READY if joern.get("available") else DEGRADED if joern else UNKNOWN,
            joern,
        )
    )
    studies = dict(reference_studies or {})
    layers.append(
        _layer(
            "reference_studies",
            READY if studies.get("available") else DEGRADED if studies else UNKNOWN,
            studies,
        )
    )
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
    status = _overall(layers, required)
    stack = build_experiment_stack_record(
        fabric_version=str(fabric.get("version") or fabric.get("controller_version") or ""),
        fabric_commit=fabric.get("commit"),
        worker_ids=[str(worker.get("worker_id")) for worker in workers if worker.get("worker_id")],
        worker_service_versions={
            str(worker.get("worker_id")): str(worker.get("worker_service_version"))
            for worker in workers
            if worker.get("worker_id") and worker.get("worker_service_version")
        },
        model_identities=[
            str(item.get("name") if isinstance(item, dict) else item) for item in models[:32]
        ],
    )
    return {
        "status": status,
        "profile": profile,
        "claim_boundary": CLAIM_BOUNDARY,
        "layers": {item["name"]: item for item in layers},
        "required_layers": list(required),
        "optional_layers": list(PROFILES[profile]["optional"]),
        "experiment_stack": stack,
        "local_fallback": bool(routing.get("local_fallback")),
        "local_fallback_explicit": bool(routing.get("fallback_explicit")),
    }


def inspect_live_config(config: Any, *, profile: str = "base-inference") -> dict[str, Any]:
    """Observe the loaded Harness configuration without mutating the fleet."""

    from pathlib import Path

    from . import __version__
    from .commons import CommonsSession
    from .fabric import FabricSession
    from .fleet import FleetService

    fabric_session = FabricSession(config.fabric)
    fabric_session.initialize()
    fabric_status = fabric_session.status()
    fleet = FleetService(config, fabric_session)
    snapshot = fleet.snapshot(fabric_status)
    commons_session = CommonsSession(config.commons)
    commons_session.initialize()
    commons = commons_session.status()
    metrics_path = Path(config.metrics.path)
    writable = os_access_writable(metrics_path.parent)
    workers = list(fabric_status.workers or (snapshot.get("fabric") or {}).get("workers") or [])
    capabilities: dict[str, Any] = {}
    try:
        import mncs_fabric as fabric_pkg

        contract = fabric_pkg.FabricClient.contract()
        features = contract.get("features", {}) if isinstance(contract, dict) else {}
        from .fabric_compat import EXPERIMENT_REQUIRED_CAPABILITIES

        capabilities = {
            name: features.get(name) is True for name in EXPERIMENT_REQUIRED_CAPABILITIES
        }
    except Exception:
        capabilities = {}
    studies_root = Path(__file__).resolve().parents[3] / "mncs-reference-studies"
    return evaluate_layers(
        harness={"available": True, "status": READY, "version": __version__},
        fabric={
            "available": fabric_status.state in {"available", "configured"},
            "version": fabric_status.controller_version,
            "controller_connected": fabric_status.state == "available",
            "persistent_service_support": capabilities,
            "workers": workers,
            "stale_capability_inventory": any(
                str(worker.get("capability_inventory_status") or "").upper() == "STALE"
                for worker in workers
            ),
            "available_workers": [
                worker
                for worker in workers
                if str(worker.get("availability") or "").upper() == "AVAILABLE"
            ],
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
        },
        artifact_write={"writable": writable, "path": str(metrics_path)},
        scheduler={"available": True, "detail": "inspection only; no schedule tick"},
        reference_studies={"available": studies_root.is_dir()},
        profile=profile,
    )


def os_access_writable(path: Any) -> bool:
    import os
    from pathlib import Path

    return os.access(Path(path), os.W_OK)
