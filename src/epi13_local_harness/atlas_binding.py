"""Atlas-bound execution requirements (carry + enforce, never re-decide).

Authority: MNCS Atlas decides admission and capability status. This module
transports Atlas-issued decisions into downstream execution and enforces
them mechanically. It contains no policy tables, no capability vocabulary,
and no admission logic of its own.

Schema 0.2 (language-owned contract ``mncs-model::authority``) hardens the
0.1 draft:

* every carried decision must prove issuance with a
  ``sha256:canonical-json-v1`` content digest recomputed at load; a forged,
  tampered, or provenance-less decision is unloadable, never promoted;
* every decision must belong to the requirement's admitted session
  (participant + scope); replay out-of-context fails at load;
* duplicate decisions for one capability fold conservatively and
  order-independently (denial wins; otherwise unsatisfied conditions hold
  at UNKNOWN with the sorted union of outstanding evidence);
* each leg declares the target it will run on; the declared target must
  equal the Atlas-authorized one, and ``confirm_execution`` later proves
  the actual Fabric target is that same target on fresh operator proof.

Verdict lattice: GRANTED > UNKNOWN > REFUSED. UNKNOWN is never promoted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

SCHEMA = "mncs.execution-requirement/0.2"
DECISION_SCHEMA = "mncs.atlas-capability-decision/1"
DECISION_DIGEST_ALG = "sha256:canonical-json-v1"

#: Harness-local declaration of which Atlas capabilities its own
#: consequential tools consume. This is an interface contract (what the
#: tool needs), not Atlas policy (whether it is granted): grants still
#: come only from carried Atlas decisions.
TOOL_CAPABILITY_NEEDS: dict[str, tuple[str, ...]] = {
    "run_command": ("worker.dispatch",),
    "write_file": ("repo.edit",),
}
COMMONS_PUBLISH_CAPABILITY_NEEDS: tuple[str, ...] = ("evidence.attest",)
FABRIC_DISPATCH_CAPABILITY_NEEDS: tuple[str, ...] = ("worker.dispatch",)

#: Proof channels whose target observations count as operator authority.
#: Anything else (notably consumer-declared observations) can never
#: manufacture authority.
OPERATOR_PROOF_ORIGINS = ("fabric-inventory", "operator-asserted")

#: Proof channel for Fabric implementations that predate observation
#: provenance (no observation classes, no operator-assert surface). A
#: legacy channel can never GRANT: a positive target mismatch still
#: REFUSEs, anything else is consistent-yet-unproven UNKNOWN.
LEGACY_PROOF_ORIGIN = "fabric-legacy"


class BindingError(ValueError):
    """Malformed, tampered, or context-free Atlas decision/requirement."""


def canonical_envelope_bytes(envelope: Mapping[str, Any]) -> bytes:
    """v1 canonical form, byte-identical with Atlas and mncs-language."""
    body = {key: value for key, value in envelope.items() if key != "decision_digest"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def verify_decision_digest(envelope: Mapping[str, Any]) -> None:
    if not isinstance(envelope, Mapping):
        raise BindingError("atlas decision must be a mapping")
    if envelope.get("decision_digest_alg") != DECISION_DIGEST_ALG:
        raise BindingError(
            f"atlas decision digest algorithm {envelope.get('decision_digest_alg')!r} "
            f"is not {DECISION_DIGEST_ALG}"
        )
    digest = envelope.get("decision_digest")
    if not isinstance(digest, str) or not digest:
        raise BindingError("atlas decision carries no digest (provenance-less grant?)")
    if hashlib.sha256(canonical_envelope_bytes(envelope)).hexdigest() != digest:
        raise BindingError("atlas decision digest mismatch: modified after issuance?")


def requirement_identity(
    task_id: str,
    source_identity: str,
    artifact_identity: str,
    decision_digests: list[str],
    legs: list[dict[str, Any]],
) -> str:
    """Canonical requirement identity, byte-identical with mncs-language."""
    canonical_legs = sorted(
        (
            {
                "backend": str(leg.get("backend", "")),
                "name": str(leg.get("name", "")),
                "needs": sorted(str(item) for item in leg.get("needs", [])),
                "target": str(leg.get("target", "")),
            }
            for leg in legs
        ),
        key=lambda leg: leg["name"],
    )
    body = {
        "artifact_identity": artifact_identity,
        "decision_digests": sorted(decision_digests),
        "legs": canonical_legs,
        "schema_version": SCHEMA,
        "source_identity": source_identity,
        "task_id": task_id,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AtlasDecision:
    capability: str
    status: str  # granted | conditional | denied
    missing: tuple[str, ...] = ()
    session_participant: str = ""
    session_scope: str = ""
    execution_target: str = ""
    authority: str = ""
    decided_by: tuple[str, ...] = ()
    decision_digest: str = ""

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AtlasDecision":
        if not isinstance(value, Mapping):
            raise BindingError("atlas decision must be a mapping")
        if value.get("schema_version") != DECISION_SCHEMA:
            raise BindingError(f"atlas decision schema must be {DECISION_SCHEMA}")
        verify_decision_digest(value)
        capability = value.get("capability")
        status = value.get("status")
        if not isinstance(capability, str) or not capability:
            raise BindingError("atlas decision needs a capability id")
        if status not in ("granted", "conditional", "denied"):
            raise BindingError(f"unknown atlas status {status!r}")
        missing = value.get("missing", [])
        if not isinstance(missing, list) or not all(isinstance(item, str) for item in missing):
            raise BindingError("atlas decision missing must be a string list")
        session = value.get("session", {})
        if (
            not isinstance(session, Mapping)
            or not isinstance(session.get("participant"), str)
            or not session["participant"]
            or not isinstance(session.get("scope"), str)
            or not session["scope"]
        ):
            raise BindingError(
                "atlas decision carries no admitted session binding (replay out-of-context?)"
            )
        decided_by = value.get("decision_by", [])
        if not isinstance(decided_by, list) or not all(
            isinstance(item, str) for item in decided_by
        ):
            raise BindingError("atlas decision decision_by must be a string list")
        return cls(
            capability=capability,
            status=status,
            missing=tuple(missing),
            session_participant=str(session["participant"]),
            session_scope=str(session["scope"]),
            execution_target=str(value.get("execution_target", "")),
            authority=str(value.get("authority", "")),
            decided_by=tuple(decided_by),
            decision_digest=str(value.get("decision_digest", "")),
        )


@dataclass(frozen=True)
class RequirementLeg:
    name: str
    needs: tuple[str, ...]
    backend: str = ""
    target: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Acceptance:
    verdict: str  # GRANTED | UNKNOWN | REFUSED
    leg: str
    reason: str
    binding: str = ""
    outstanding: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionRequirement:
    task_id: str
    source_identity: str
    artifact_identity: str
    decisions: tuple[AtlasDecision, ...]
    legs: tuple[RequirementLeg, ...]
    bounds: Mapping[str, Any] = field(default_factory=dict)
    session_participant: str = ""
    session_scope: str = ""
    requirement_id: str = ""

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExecutionRequirement":
        if not isinstance(value, Mapping):
            raise BindingError("requirement must be a mapping")
        if value.get("schema_version") != SCHEMA:
            raise BindingError(f"requirement schema must be {SCHEMA}")
        for name in ("task_id", "source_identity", "artifact_identity"):
            if not isinstance(value.get(name), str) or not value[name]:
                raise BindingError(f"requirement needs {name}")
        session = value.get("session", {})
        if (
            not isinstance(session, Mapping)
            or not isinstance(session.get("participant"), str)
            or not session["participant"]
            or not isinstance(session.get("scope"), str)
            or not session["scope"]
        ):
            raise BindingError("requirement needs an admitted session context")
        decisions = value.get("atlas_decisions", [])
        if not isinstance(decisions, list) or not decisions:
            # No Atlas binding at all: the requirement cannot be told apart
            # from a bypass attempt, so it is unloadable, not default-allowed.
            raise BindingError("requirement carries no atlas decisions")
        parsed = tuple(AtlasDecision.from_dict(item) for item in decisions)
        for decision in parsed:
            if (
                decision.session_participant != session["participant"]
                or decision.session_scope != session["scope"]
            ):
                raise BindingError(
                    f"decision {decision.capability} belongs to "
                    f"participant={decision.session_participant} "
                    f"scope={decision.session_scope}: replay out-of-context?"
                )
        legs = value.get("legs", [])
        if not isinstance(legs, list) or not legs:
            raise BindingError("requirement needs at least one leg")
        parsed_legs = []
        for leg in legs:
            if not isinstance(leg, Mapping) or not leg.get("name"):
                raise BindingError("each leg needs a name")
            needs = leg.get("needs", [])
            if not isinstance(needs, list) or not all(isinstance(item, str) for item in needs):
                raise BindingError(f"leg {leg.get('name')!r} needs a string list")
            evidence = leg.get("evidence", {})
            if not isinstance(evidence, Mapping):
                raise BindingError(f"leg {leg.get('name')!r} evidence must map")
            parsed_legs.append(
                RequirementLeg(
                    name=str(leg["name"]),
                    needs=tuple(needs),
                    backend=str(leg.get("backend", "")),
                    target=str(leg.get("target", "")),
                    evidence=evidence,
                )
            )
        bounds = value.get("bounds", {})
        if not isinstance(bounds, Mapping):
            raise BindingError("bounds must map")
        requirement_id = requirement_identity(
            str(value["task_id"]),
            str(value["source_identity"]),
            str(value["artifact_identity"]),
            [decision.decision_digest for decision in parsed],
            [dict(leg) for leg in legs if isinstance(leg, Mapping)],
        )
        return cls(
            task_id=str(value["task_id"]),
            source_identity=str(value["source_identity"]),
            artifact_identity=str(value["artifact_identity"]),
            decisions=parsed,
            legs=tuple(parsed_legs),
            bounds=bounds,
            session_participant=str(session["participant"]),
            session_scope=str(session["scope"]),
            requirement_id=requirement_id,
        )

    def _folds(self) -> dict[str, Acceptance]:
        """Fold duplicates per capability, conservatively and order-free."""
        grouped: dict[str, list[AtlasDecision]] = {}
        for decision in self.decisions:
            grouped.setdefault(decision.capability, []).append(decision)
        return {
            capability: _fold_capability(capability, matches)
            for capability, matches in grouped.items()
        }

    def _authorized_targets(self) -> dict[str, list[str]]:
        targets: dict[str, list[str]] = {}
        for decision in self.decisions:
            if decision.execution_target:
                known = targets.setdefault(decision.capability, [])
                if decision.execution_target not in known:
                    known.append(decision.execution_target)
        return targets

    def accept(self, leg_name: str, observed_artifact: str = "") -> Acceptance:
        """Decide one leg purely from carried, digest-verified Atlas decisions.

        Granted covers needs. Denied refuses. Conditional is satisfied only
        when every missing evidence name is supplied by carried leg evidence
        or by a target bound to an Atlas-authorized one; otherwise UNKNOWN.
        A leg that declares no target while Atlas constrains one refuses:
        authorized placement cannot be shown. An observed artifact digest
        that differs from the bound one refuses (tamper fail-closed).
        """
        leg = next((item for item in self.legs if item.name == leg_name), None)
        if leg is None:
            return Acceptance("REFUSED", leg_name, "unknown leg", "")
        if observed_artifact and observed_artifact != self.artifact_identity:
            return Acceptance(
                "REFUSED",
                leg_name,
                "artifact digest mismatch: observed differs from bound",
                "",
            )
        folds = self._folds()
        authorized = self._authorized_targets()
        verdict = "GRANTED"
        reasons: list[str] = []
        binding = ""
        for need in leg.needs:
            fold = folds.get(need)
            if fold is None:
                verdict = "REFUSED"
                reasons.append(f"{need}: no atlas decision carried (bypass?)")
                continue
            binding = binding or fold.binding
            # Multiple authorized targets are placement options, not
            # conflicting authority: the leg must name one of them.
            distinct = sorted(set(authorized.get(need, ())))
            if distinct and leg.target not in distinct:
                verdict = "REFUSED"
                reasons.append(
                    f"{need}: target mismatch: leg declares {leg.target!r}, "
                    f"atlas authorizes {distinct}"
                )
                continue
            constrained = bool(leg.target) and bool(distinct)
            if fold.verdict == "REFUSED":
                verdict = "REFUSED"
                reasons.append(f"{need}: {fold.reason}")
            elif fold.verdict == "UNKNOWN":
                supplied = set(leg.evidence)
                if constrained:
                    supplied.add("execution.target")
                outstanding = [item for item in fold.outstanding if item not in supplied]
                if outstanding:
                    if verdict == "GRANTED":
                        verdict = "UNKNOWN"
                    reasons.append(f"{need}: conditional, outstanding {outstanding}")
                else:
                    reasons.append(f"{need}: conditional satisfied by carried evidence")
            else:
                reasons.append(f"{need}: {fold.reason}")
        return Acceptance(verdict, leg_name, "; ".join(reasons), binding)

    def confirm_execution(
        self,
        leg_name: str,
        *,
        actual_target: str = "",
        proof_origin: str = "",
        proof_fresh: bool = True,
        observed_artifact: str = "",
    ) -> Acceptance:
        """Bind the actual execution target to the authorized one.

        Call after Fabric placement/execution with the observed worker
        identity and the proof channel it arrived on. Only fresh operator
        proof (fabric-inventory, operator-asserted) counts: consumer-declared
        observations can never authorize, and stale proof stays UNKNOWN.
        A legacy implementation that predates observation provenance can
        never GRANT either: a positive target mismatch still REFUSEs, and
        anything else is UNKNOWN (gate-checked but unconfirmed).
        """
        acceptance = self.accept(leg_name, observed_artifact=observed_artifact)
        if acceptance.verdict != "GRANTED":
            return acceptance
        leg = next(item for item in self.legs if item.name == leg_name)
        if proof_origin == LEGACY_PROOF_ORIGIN:
            if actual_target and leg.target and actual_target != leg.target:
                return Acceptance(
                    "REFUSED",
                    leg_name,
                    f"target mismatch: authorized {leg.target}, actual {actual_target}",
                    acceptance.binding,
                )
            return Acceptance(
                "UNKNOWN",
                leg_name,
                f"actual target {actual_target or 'unreported'} consistent with "
                f"authorized {leg.target or 'unbound'} but unproven: Fabric "
                "implementation predates observation provenance",
                acceptance.binding,
            )
        if not actual_target:
            return Acceptance("REFUSED", leg_name, "no actual target evidence", acceptance.binding)
        if proof_origin not in OPERATOR_PROOF_ORIGINS:
            return Acceptance(
                "REFUSED",
                leg_name,
                f"proof origin {proof_origin!r} is not operator authority",
                acceptance.binding,
            )
        if not proof_fresh:
            return Acceptance(
                "UNKNOWN",
                leg_name,
                f"stale target evidence for {actual_target}",
                acceptance.binding,
            )
        if not leg.target:
            return Acceptance(
                "UNKNOWN",
                leg_name,
                f"target unbound end-to-end: actual {actual_target} has no declared leg target",
                acceptance.binding,
            )
        if actual_target != leg.target:
            return Acceptance(
                "REFUSED",
                leg_name,
                f"target mismatch: authorized {leg.target}, actual {actual_target}",
                acceptance.binding,
            )
        return Acceptance(
            "GRANTED",
            leg_name,
            f"actual target {actual_target} matches authorized {leg.target} "
            f"via {proof_origin}; {acceptance.reason}",
            acceptance.binding,
        )


def _fold_capability(capability: str, matches: list[AtlasDecision]) -> Acceptance:
    verdict = "GRANTED"
    outstanding: list[str] = []
    reasons: list[str] = []
    binding = ""
    for decision in matches:
        binding = binding or (
            f"{decision.capability}={decision.status}"
            f" participant={decision.session_participant}"
            f" scope={decision.session_scope}"
        )
        if decision.status == "denied":
            verdict = "REFUSED"
            reasons.append(f"{decision.capability}: atlas denied")
        elif decision.status == "conditional":
            missing = sorted(set(decision.missing))
            if missing:
                if verdict == "GRANTED":
                    verdict = "UNKNOWN"
                for item in missing:
                    if item not in outstanding:
                        outstanding.append(item)
                outstanding.sort()
                reasons.append(f"{decision.capability}: conditional, outstanding {missing}")
            else:
                reasons.append(f"{decision.capability}: conditional with no outstanding evidence")
        else:
            reasons.append(f"{decision.capability}: atlas granted")
    if verdict == "UNKNOWN":
        reasons.append(f"outstanding union: {outstanding}")
    return Acceptance(verdict, capability, "; ".join(reasons), binding, tuple(outstanding))


def build_requirement(
    *,
    task_id: str,
    source_identity: str,
    artifact_identity: str,
    atlas_decisions: list[Mapping[str, Any]],
    legs: list[Mapping[str, Any]],
    session_participant: str,
    session_scope: str,
    bounds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Carry Atlas-issued decisions into a requirement envelope.

    This stamps context, never authority: decisions must already carry
    Atlas digests. Anything forged, tampered, or out-of-context fails at
    :meth:`ExecutionRequirement.from_dict`.
    """
    return {
        "schema_version": SCHEMA,
        "task_id": task_id,
        "source_identity": source_identity,
        "artifact_identity": artifact_identity,
        "session": {"participant": session_participant, "scope": session_scope},
        "atlas_decisions": [dict(item) for item in atlas_decisions],
        "legs": [dict(item) for item in legs],
        "bounds": dict(bounds or {}),
    }
