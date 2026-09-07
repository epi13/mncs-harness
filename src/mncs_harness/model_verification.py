"""Generic model verification against Fabric-discovered implementations.

Tiers:
  0 reachability — provider/model exists and returns a bounded response
  1 protocol — exact marker / instruction following
  2 capability — only claims the provider or role actually needs
  3 integration — Fabric receipt when a session is available

Verification never assumes a brand, worker OS, or historical tag. A failed
code-edit probe means that capability is not demonstrated, not that the model
is invalid.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .model_capabilities import model_name, provider_claims
from .model_evidence import CapabilityEvidence, append_evidence, utc_now

Probe = Callable[[str, dict[str, Any]], tuple[str, str | None]]


@dataclass(frozen=True)
class VerificationResult:
    worker_id: str
    model: str
    records: tuple[CapabilityEvidence, ...]
    summary: str


def _record(
    *,
    worker_id: str,
    model: str,
    capability: str,
    outcome: str,
    tier: int,
    failure_class: str | None = None,
    detail: str | None = None,
    receipt: str | None = None,
) -> CapabilityEvidence:
    return CapabilityEvidence(
        subject_worker=worker_id,
        subject_model=model,
        capability=capability,
        outcome=outcome,
        tier=tier,
        freshness="CURRENT",
        recorded_at=utc_now(),
        validator_identity="mncs-harness/model-verify/v1",
        failure_class=failure_class,
        execution_receipt=receipt,
        fixture_identity=f"tier{tier}:{capability}",
        detail=detail,
    )


def planned_probes(item: dict[str, Any], *, tiers: set[int]) -> tuple[tuple[int, str], ...]:
    claims = provider_claims(item)
    planned: list[tuple[int, str]] = []
    if 0 in tiers:
        planned.append((0, "reachability"))
    if 1 in tiers:
        planned.append((1, "marker_response"))
    if 2 in tiers:
        if "tools" in claims:
            planned.append((2, "tool_call"))
        if "vision" in claims:
            planned.append((2, "file_read"))
    if 3 in tiers:
        planned.append((3, "fabric_receipt"))
    return tuple(planned)


def verify_candidate(
    worker_id: str,
    item: dict[str, Any],
    *,
    probes: dict[str, Probe],
    tiers: set[int] | None = None,
    persist: bool = False,
) -> VerificationResult:
    selected_tiers = tiers or {0, 1, 2}
    name = model_name(item)
    records: list[CapabilityEvidence] = []
    for tier, capability in planned_probes(item, tiers=selected_tiers):
        probe = probes.get(capability)
        if probe is None:
            records.append(
                _record(
                    worker_id=worker_id,
                    model=name,
                    capability=capability,
                    outcome="UNKNOWN",
                    tier=tier,
                    failure_class="probe_unavailable",
                    detail="no probe registered for this capability",
                )
            )
            continue
        outcome, detail = probe(name, item)
        failure = None if outcome == "PASS" else (detail or "probe_failed")
        record = _record(
            worker_id=worker_id,
            model=name,
            capability=capability,
            outcome=outcome,
            tier=tier,
            failure_class=failure,
            detail=detail,
        )
        records.append(record)
        if persist:
            append_evidence(record)
    passed = sum(1 for item in records if item.outcome == "PASS")
    return VerificationResult(
        worker_id=worker_id,
        model=name,
        records=tuple(records),
        summary=f"{name} on {worker_id}: {passed}/{len(records)} demonstrated",
    )


def discover_candidates(workers: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """Enumerate CURRENT AVAILABLE worker/model pairs. Identity is opaque."""

    found: list[tuple[str, dict[str, Any]]] = []
    for worker in workers:
        if worker.get("availability") != "AVAILABLE":
            continue
        if str(worker.get("capability_inventory_status") or worker.get("model_inventory_status")) != "CURRENT":
            continue
        inventory = worker.get("model_inventory")
        if not isinstance(inventory, list):
            observation = worker.get("capability_observation")
            capabilities = observation.get("capabilities") if isinstance(observation, dict) else None
            if isinstance(capabilities, list):
                inventory = [
                    {"name": entry.get("name"), "capabilities": (entry.get("attributes") or {}).get("ollama_capabilities", [])}
                    if isinstance(entry, dict)
                    else entry
                    for entry in capabilities
                    if isinstance(entry, dict) and entry.get("kind") == "model"
                ]
        if not isinstance(inventory, list):
            continue
        for item in inventory:
            if isinstance(item, dict) and model_name(item):
                found.append((str(worker.get("worker_id")), item))
    return found
