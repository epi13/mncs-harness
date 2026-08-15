"""Inventory-aware model selection.

Model identity is data, not policy. Exact operator pins win. Automatic
selection uses provider-reported claims, MNCS-observed evidence, resources,
and explicit policy. Model-name substrings never confer capability.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .model_capabilities import (
    RoleRequirements,
    SelectionPolicy,
    model_name,
    provider_claims,
    requirements_for,
    stored_size,
)
from .model_evidence import CapabilityEvidence, latest_outcome


@dataclass(frozen=True)
class ModelSelection:
    role: str
    configured_model: str
    selected_model: str
    stored_size_bytes: int
    reason: str
    worker_id: str | None = None
    inventory_status: str = "CURRENT"
    available: bool = True
    loaded: bool = False
    resident: bool = False
    route_mode: str = "AUTO"
    rejected: tuple[dict[str, str], ...] = ()


def _dedupe_models(models: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for item in models:
        name = model_name(item)
        if name and name not in by_name:
            by_name[name] = dict(item)
    return [by_name[name] for name in sorted(by_name)]


def _observed(
    evidence: Sequence[CapabilityEvidence],
    name: str,
    capability: str,
) -> CapabilityEvidence | None:
    return latest_outcome(evidence, model=name, capability=capability)


def _eligible(
    item: dict[str, Any],
    requirements: RoleRequirements,
    evidence: Sequence[CapabilityEvidence],
    policy: SelectionPolicy,
) -> tuple[bool, list[str]]:
    name = model_name(item)
    claims = provider_claims(item)
    reasons: list[str] = []
    if requirements.needs_completion:
        observed = _observed(evidence, name, "reachability")
        if observed and observed.outcome == "FAIL":
            return False, [f"observed reachability=FAIL ({observed.failure_class or 'failed'})"]
        if "completion" in claims:
            reasons.append("provider-reported completion")
        elif observed and observed.outcome == "PASS":
            reasons.append("observed reachability=PASS")
        elif requirements.unknown_policy == "fail-closed" and policy.require_observed_for_mutation:
            return False, ["completion is unknown and fail-closed policy applies"]
        else:
            reasons.append("completion unknown")

    if requirements.needs_tools:
        observed = _observed(evidence, name, "tool_call")
        if observed and observed.outcome == "FAIL":
            return False, [f"observed tool_call=FAIL ({observed.failure_class or 'failed'})"]
        if observed and observed.outcome == "PASS":
            reasons.append("observed tool_call=PASS")
        elif "tools" in claims:
            reasons.append("provider-reported tools claim; MNCS-observed tool capability is unknown")
            if policy.require_observed_for_mutation:
                return False, ["mutation requires observed tool capability"]
        elif requirements.unknown_policy == "explore":
            reasons.append("tools unknown; explore policy permits a low-risk attempt")
        elif requirements.unknown_policy == "provider-claim-compat":
            if not policy.allow_size_policy_without_claims:
                return False, ["tools unknown and compatibility size-policy is disabled"]
            reasons.append(
                "compatibility: no provider or observed tool capability; ranked by policy only"
            )
        else:
            return False, ["required tools capability is unknown"]

    if requirements.needs_code_edit:
        observed = _observed(evidence, name, "code_edit")
        if observed and observed.outcome == "FAIL":
            return False, [f"observed code_edit=FAIL ({observed.failure_class or 'failed'})"]
        if observed and observed.outcome == "PASS":
            reasons.append("observed code_edit=PASS")
        elif observed is None:
            reasons.append("code_edit not demonstrated")

    return True, reasons


def _size_rank(item: dict[str, Any], requirements: RoleRequirements, policy: SelectionPolicy) -> int:
    size = stored_size(item)
    if requirements.prefer_small:
        return size if size else 2**62
    if requirements.prefer_large:
        return -(size or 0)
    if size and 0 < size <= policy.max_coder_bytes:
        return -size
    if size:
        return size
    return 2**61


def select_installed_model(
    role: str,
    configured_model: str,
    models: Iterable[dict[str, Any]],
    *,
    evidence: Sequence[CapabilityEvidence] | None = None,
    policy: SelectionPolicy | None = None,
) -> ModelSelection | None:
    """Resolve one role to an installed model without name-based capability inference.

    Policy order:
    1. exact configured tag if present (operator preference/pin);
    2. eligible candidates by provider claims, observed evidence, and policy;
    3. deterministic ranking: observed proof, provider claims, residency, size policy, name tie-break.

    Presence is not a capability proof. Unknown remains unknown.
    """

    available = _dedupe_models(models)
    if not available:
        return None
    records = tuple(evidence or ())
    selected_policy = policy or SelectionPolicy()
    requirements = requirements_for(role)

    exact = next((item for item in available if model_name(item) == configured_model), None)
    if exact is not None:
        return ModelSelection(
            role=role,
            configured_model=configured_model,
            selected_model=configured_model,
            stored_size_bytes=stored_size(exact),
            reason="operator preference is installed on the Fabric worker inventory",
            loaded=bool(exact.get("loaded", False)),
        )

    has_tool_signal = any(
        "tools" in provider_claims(item)
        or (
            _observed(records, model_name(item), "tool_call") is not None
            and _observed(records, model_name(item), "tool_call").outcome == "PASS"
        )
        for item in available
    )
    rejected: list[dict[str, str]] = []
    ranked: list[tuple[tuple[object, ...], dict[str, Any], list[str]]] = []
    for item in available:
        name = model_name(item)
        ok, facts = _eligible(item, requirements, records, selected_policy)
        if (
            ok
            and requirements.needs_tools
            and has_tool_signal
            and "tools" not in provider_claims(item)
            and not (
                _observed(records, name, "tool_call")
                and _observed(records, name, "tool_call").outcome == "PASS"
            )
        ):
            ok = False
            facts = ["no provider or observed tools while other candidates have a tools signal"]
        if not ok:
            rejected.append({"model": name, "reason": "; ".join(facts)})
            continue
        observed_tools = _observed(records, name, "tool_call")
        observed_code = _observed(records, name, "code_edit")
        key = (
            0 if requirements.needs_tools and observed_tools and observed_tools.outcome == "PASS" else 1,
            0 if requirements.needs_code_edit and observed_code and observed_code.outcome == "PASS" else 1,
            0 if requirements.needs_tools and "tools" in provider_claims(item) else 1,
            0 if item.get("loaded") else 1,
            _size_rank(item, requirements, selected_policy),
            name,
        )
        ranked.append((key, item, facts))

    if not ranked:
        return None

    ranked.sort(key=lambda row: row[0])
    _key, chosen, facts = ranked[0]
    reasons = [
        f"configured preference {configured_model!r} is not installed",
        *facts,
    ]
    if requirements.prefer_small:
        reasons.append("policy prefers smaller models for cheap generation")
    elif requirements.prefer_large:
        reasons.append("policy prefers larger models for review")
    else:
        reasons.append("policy ranks remaining eligible candidates by resources and evidence")
    reasons.append(f"selected opaque implementation identity {model_name(chosen)!r}")
    return ModelSelection(
        role=role,
        configured_model=configured_model,
        selected_model=model_name(chosen),
        stored_size_bytes=stored_size(chosen),
        reason="; ".join(reasons),
        loaded=bool(chosen.get("loaded", False)),
        rejected=tuple(rejected),
    )


__all__ = ["ModelSelection", "select_installed_model"]
