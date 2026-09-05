"""Atlas-bound execution requirements (carry + enforce, never re-decide).

Authority: MNCS Atlas decides admission and capability status. This module
transports an Atlas decision into downstream execution and enforces it
mechanically. It contains no policy tables, no capability vocabulary, and
no admission logic of its own: a requirement whose needed capabilities are
not covered by granted Atlas decisions is refused, and the refusal cites
the Atlas decision that binds it.

Verdict lattice: GRANTED > UNKNOWN > REFUSED. UNKNOWN is never promoted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

SCHEMA = "mncs.execution-requirement/0.1"
DECISION_SCHEMA = "mncs.atlas-capability-decision/1"


class BindingError(ValueError):
    """Malformed Atlas decision or execution requirement envelope."""


@dataclass(frozen=True)
class AtlasDecision:
    capability: str
    status: str  # granted | conditional | denied
    missing: tuple[str, ...] = ()
    session_participant: str = ""
    session_scope: str = ""
    execution_target: str = ""

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AtlasDecision":
        if not isinstance(value, Mapping):
            raise BindingError("atlas decision must be a mapping")
        if value.get("schema_version") != DECISION_SCHEMA:
            raise BindingError(
                f"atlas decision schema must be {DECISION_SCHEMA}"
            )
        capability = value.get("capability")
        status = value.get("status")
        if not isinstance(capability, str) or not capability:
            raise BindingError("atlas decision needs a capability id")
        if status not in ("granted", "conditional", "denied"):
            raise BindingError(f"unknown atlas status {status!r}")
        missing = value.get("missing", [])
        if not isinstance(missing, list) or not all(
            isinstance(item, str) for item in missing
        ):
            raise BindingError("atlas decision missing must be a string list")
        session = value.get("session", {})
        if not isinstance(session, Mapping):
            raise BindingError("atlas decision session must be a mapping")
        return cls(
            capability=capability,
            status=status,
            missing=tuple(missing),
            session_participant=str(session.get("participant", "")),
            session_scope=str(session.get("scope", "")),
            execution_target=str(value.get("execution_target", "")),
        )


@dataclass(frozen=True)
class RequirementLeg:
    name: str
    needs: tuple[str, ...]
    backend: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Acceptance:
    verdict: str  # GRANTED | UNKNOWN | REFUSED
    leg: str
    reason: str
    binding: str = ""


@dataclass(frozen=True)
class ExecutionRequirement:
    task_id: str
    source_identity: str
    artifact_identity: str
    decisions: tuple[AtlasDecision, ...]
    legs: tuple[RequirementLeg, ...]
    bounds: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExecutionRequirement":
        if not isinstance(value, Mapping):
            raise BindingError("requirement must be a mapping")
        if value.get("schema_version") != SCHEMA:
            raise BindingError(f"requirement schema must be {SCHEMA}")
        for name in ("task_id", "source_identity", "artifact_identity"):
            if not isinstance(value.get(name), str) or not value[name]:
                raise BindingError(f"requirement needs {name}")
        decisions = value.get("atlas_decisions", [])
        if not isinstance(decisions, list) or not decisions:
            # No Atlas binding at all: the requirement cannot be told apart
            # from a bypass attempt, so it is unloadable, not default-allowed.
            raise BindingError("requirement carries no atlas decisions")
        legs = value.get("legs", [])
        if not isinstance(legs, list) or not legs:
            raise BindingError("requirement needs at least one leg")
        parsed_legs = []
        for leg in legs:
            if not isinstance(leg, Mapping) or not leg.get("name"):
                raise BindingError("each leg needs a name")
            needs = leg.get("needs", [])
            if not isinstance(needs, list) or not all(
                isinstance(item, str) for item in needs
            ):
                raise BindingError(f"leg {leg.get('name')!r} needs a string list")
            evidence = leg.get("evidence", {})
            if not isinstance(evidence, Mapping):
                raise BindingError(f"leg {leg.get('name')!r} evidence must map")
            parsed_legs.append(
                RequirementLeg(
                    name=str(leg["name"]),
                    needs=tuple(needs),
                    backend=str(leg.get("backend", "")),
                    evidence=evidence,
                )
            )
        bounds = value.get("bounds", {})
        provenance = value.get("provenance", {})
        if not isinstance(bounds, Mapping) or not isinstance(provenance, Mapping):
            raise BindingError("bounds and provenance must map")
        return cls(
            task_id=str(value["task_id"]),
            source_identity=str(value["source_identity"]),
            artifact_identity=str(value["artifact_identity"]),
            decisions=tuple(AtlasDecision.from_dict(item) for item in decisions),
            legs=tuple(parsed_legs),
            bounds=bounds,
            provenance=provenance,
        )

    def accept(self, leg_name: str, observed_artifact: str = "") -> Acceptance:
        """Decide one leg purely from carried Atlas decisions.

        Granted covers needs. Denied refuses. Conditional is satisfied only
        when every missing evidence name is supplied by the leg evidence or
        the bound execution target; otherwise UNKNOWN. An observed artifact
        digest that differs from the bound one refuses (tamper fail-closed).
        """
        leg = next((item for item in self.legs if item.name == leg_name), None)
        if leg is None:
            return Acceptance("REFUSED", leg_name, "unknown leg", "")
        if observed_artifact and observed_artifact != self.artifact_identity:
            return Acceptance(
                "REFUSED",
                leg_name,
                f"artifact digest mismatch: observed {observed_artifact[:24]}… "
                f"!= bound {self.artifact_identity[:24]}…",
                "",
            )
        by_capability = {}
        for decision in self.decisions:
            by_capability.setdefault(decision.capability, []).append(decision)
        worst = "GRANTED"
        reasons: list[str] = []
        binding = ""
        for need in leg.needs:
            matches = by_capability.get(need, [])
            if not matches:
                worst = "REFUSED"
                reasons.append(f"{need}: no atlas decision carried (bypass?)")
                continue
            decision = matches[0]
            binding = binding or (
                f"{decision.capability}={decision.status}"
                f" participant={decision.session_participant}"
                f" scope={decision.session_scope}"
            )
            if decision.status == "denied":
                worst = "REFUSED"
                reasons.append(f"{need}: atlas denied")
            elif decision.status == "conditional":
                supplied = set(leg.evidence) | (
                    {"execution.target"} if decision.execution_target else set()
                )
                outstanding = [item for item in decision.missing if item not in supplied]
                if outstanding:
                    if worst == "GRANTED":
                        worst = "UNKNOWN"
                    reasons.append(f"{need}: conditional, outstanding {outstanding}")
                else:
                    reasons.append(f"{need}: conditional satisfied by carried evidence")
            else:
                reasons.append(f"{need}: atlas granted")
        return Acceptance(worst, leg_name, "; ".join(reasons), binding)
