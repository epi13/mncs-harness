"""Atlas-bound execution requirements (carry + enforce, never re-decide).

Authority: MNCS Atlas decides admission and capability status. This module
transports Atlas-issued decisions into downstream execution and enforces
them mechanically. It contains no policy tables, no capability vocabulary,
and no admission logic of its own.

Trust model (explicit): untrusted callers may submit requirement
envelopes, but every authority-bearing component is independently
authenticated at load, and Harness constructs no authority of its own:

* Atlas decisions are authentic only with an Ed25519 issuer signature
  over the canonical issued bytes, verified here against
  operator-configured trust roots (key id -> public key). A content
  digest proves integrity, never issuance: forged JSON with a valid
  digest is unloadable. ``authority`` / ``decision_by`` fields are
  informational labels, never proof.
* Harness assembles the requirement envelope (legs, bounds, session
  echo) from caller input, but the canonical requirement identity binds
  every consequential field (task, source, artifact, session, bounds,
  decision digests, legs with evidence), so tampering changes the
  identity and every decision's own signature still has to verify.
* Conditional evidence promotes UNKNOWN to GRANTED only via
  issuer-authenticated attestations bound to name, session, scope, and
  freshness (subject-bound attestations are recorded and identity-bound
  but do not promote yet: load-time verification carries no per-item
  subject). Bare caller-written values never promote;
  ``execution.target`` is system-derived from the verified target
  binding, never caller-claimed.
* At dispatch, the executor additionally requires the requirement
  session to match operator-supplied expected participant/scope (when
  provided) and refuses requirement-bound dispatch before any Fabric
  call when the implementation cannot produce operator-grade proof.

Schema 0.3 (language-owned contract ``mncs-model::authority``):

* every carried decision must prove authentic Atlas issuance (digest +
  Ed25519 issuer signature against trust roots); forged, tampered,
  wrong-issuer, or provenance-less decisions are unloadable;
* every decision must belong to the requirement's admitted session
  (participant + scope); replay out-of-context fails at load;
* duplicate decisions for one capability fold conservatively and
  order-independently (denial wins; otherwise unsatisfied conditions hold
  at UNKNOWN with the sorted union of outstanding evidence);
* each leg declares the target it will run on; the declared target must
  equal the Atlas-authorized one, and ``confirm_execution`` later proves
  the actual Fabric target is that same target on fresh operator proof
  (canonical semantics owned by ``mncs-model::authority``; this
  implementation is the golden-vector-pinned Python projection).

Verdict lattice: GRANTED > UNKNOWN > REFUSED. UNKNOWN is never promoted.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from . import mncs_exec

SCHEMA = "mncs.execution-requirement/0.3"
DECISION_SCHEMA = "mncs.atlas-capability-decision/1"
DECISION_DIGEST_ALG = "sha256:canonical-json-v1"

#: Issuer signature algorithm. Matches the family attestation envelope
#: convention (mncs-rights-provenance) and ``mncs-model::authority``.
ISSUANCE_SIGNATURE_ALG = "ed25519"
#: Authenticated evidence attestation envelope (see ``check_attestation``).
EVIDENCE_SCHEMA = "mncs.evidence-attestation/1"

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
        raise BindingError("atlas decision carries no digest (integrity-less grant?)")
    if hashlib.sha256(canonical_envelope_bytes(envelope)).hexdigest() != digest:
        raise BindingError("atlas decision digest mismatch: modified after issuance?")


def issuance_signed_bytes(envelope: Mapping[str, Any]) -> bytes:
    """Canonical bytes the issuer signs: the envelope minus
    ``decision_digest`` and ``issuer_signature``. Byte-identical with
    ``mncs-model::authority``; the issuer block stays covered."""
    body = {
        key: value
        for key, value in envelope.items()
        if key not in ("decision_digest", "issuer_signature")
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def verify_issuance(envelope: Mapping[str, Any], trusted_issuers: Mapping[str, bytes]) -> str:
    """Verify authentic issuance of one envelope against trust roots.

    Checks the content digest, then the issuer block shape and algorithm,
    then that the key id is trusted, then the Ed25519 signature over
    :func:`issuance_signed_bytes`. Returns the issuing key id. Anything
    unsigned, mislabeled, untrusted, or mathematically invalid raises
    :class:`BindingError`: a forged envelope with a valid content digest
    still fails here for lack of authentic issuance.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    # Decisions always carry a digest (checked by the caller); evidence
    # attestations carry none and skip this step.
    if "decision_digest" in envelope:
        verify_decision_digest(envelope)
    issuer = envelope.get("issuer")
    if not isinstance(issuer, Mapping) or not issuer.get("key_id"):
        raise BindingError(
            "envelope carries no issuer binding: content integrity is not issuance"
        )
    if issuer.get("algorithm") != ISSUANCE_SIGNATURE_ALG:
        raise BindingError(f"issuer algorithm {issuer.get('algorithm')!r} is not ed25519")
    key_id = str(issuer["key_id"])
    public = trusted_issuers.get(key_id)
    if public is None:
        raise BindingError(f"issuer {key_id!r} is not trusted by this verifier")
    signature = envelope.get("issuer_signature")
    if (
        not isinstance(signature, str)
        or len(signature) != 128
        or any(char not in "0123456789abcdefABCDEF" for char in signature)
    ):
        raise BindingError("issuer signature is not 64-byte hex")
    try:
        Ed25519PublicKey.from_public_bytes(bytes(public)).verify(
            bytes.fromhex(signature), issuance_signed_bytes(envelope)
        )
    except InvalidSignature as exc:
        raise BindingError("envelope signature does not verify against the trusted issuer key") from exc
    except ValueError as exc:
        raise BindingError(f"trusted issuer {key_id!r} has an invalid public key") from exc
    return key_id


def check_attestation(
    value: Any,
    *,
    name: str,
    participant: str,
    scope: str,
    subject: str,
    trusted_issuers: Mapping[str, bytes],
    now_secs: int,
    max_age_secs: int,
) -> bool:
    """Whether one carried evidence value authentically supplies an
    outstanding conditional item (mirrors ``mncs-model::authority``).

    Only an ``mncs.evidence-attestation/1`` envelope whose issuer binding
    verifies against the trust roots, and whose name, session, scope,
    and freshness all match, supplies. The subject must be empty
    (general attestation); subject-bound values stay inert until a
    per-item subject reaches verification. Absent entries, bare values,
    wrong subjects, stale or future-dated, and untrusted-issuer
    attestations never supply.
    """
    if not isinstance(value, Mapping):
        return False
    if value.get("schema_version") != EVIDENCE_SCHEMA:
        return False
    if value.get("name") != name:
        return False
    if value.get("participant") != participant or value.get("scope") != scope:
        return False
    bound = value.get("subject", "")
    if not isinstance(bound, str) or (bound and bound != subject):
        return False
    issued_at = value.get("issued_at")
    if not isinstance(issued_at, int) or isinstance(issued_at, bool):
        return False
    # Freshness window EXECUTES the MNCS kernel
    # (mncs/harness_freshness.mncs attestation_window_ok via mncs_exec).
    # Shape checks stay host-side; the window verdict is machine-native.
    from . import mncs_exec

    if not mncs_exec.attestation_window_ok(issued_at, now_secs, max_age_secs):
        return False
    try:
        verify_issuance(value, trusted_issuers)
    except BindingError:
        return False
    return True


def _canonical_json(value: Any) -> str:
    """Strict canonical JSON for identity inputs: floats are outside the
    contract and fail closed instead of serializing ambiguously."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise BindingError(f"identity input is not canonical JSON: {exc}") from exc


def requirement_identity(
    task_id: str,
    source_identity: str,
    artifact_identity: str,
    decision_digests: list[str],
    legs: list[dict[str, Any]],
    bounds: Mapping[str, Any] | None = None,
    session_participant: str = "",
    session_scope: str = "",
) -> str:
    """Canonical requirement identity, byte-identical with mncs-language.

    Binds task, source, artifact, session context, bounds, decision
    digests, and legs (names, sorted needs, backends, targets, evidence
    pairs). Anything caller-mutable that can alter authority or execution
    semantics changes the identity.
    """
    canonical_legs = sorted(
        (
            {
                "backend": str(leg.get("backend", "")),
                "evidence": {
                    str(name): json.loads(_canonical_json(item))
                    for name, item in sorted((leg.get("evidence", {}) or {}).items())
                },
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
        "bounds": json.loads(_canonical_json(dict(bounds or {}))),
        "decision_digests": sorted(decision_digests),
        "legs": canonical_legs,
        "schema_version": SCHEMA,
        "session": {"participant": session_participant, "scope": session_scope},
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
    issuer_key_id: str = ""

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, trusted_issuers: Mapping[str, bytes]) -> "AtlasDecision":
        if not isinstance(value, Mapping):
            raise BindingError("atlas decision must be a mapping")
        if value.get("schema_version") != DECISION_SCHEMA:
            raise BindingError(f"atlas decision schema must be {DECISION_SCHEMA}")
        verify_decision_digest(value)
        # Authentic issuance, not just integrity: the key id must be
        # operator-trusted and the signature must verify. ``authority``
        # and ``decision_by`` stay informational labels, never proof.
        issuer_key_id = verify_issuance(value, trusted_issuers)
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
            issuer_key_id=issuer_key_id,
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
    #: Trusted issuer key ids, sorted. Full public keys live with the
    #: caller (trust roots); the requirement carries only which issuers
    #: its decisions and evidence were verified against.
    trusted_key_ids: tuple[str, ...] = ()
    evidence_max_age_secs: int = 86400
    evidence_now_secs: int = 0
    #: (leg name, evidence name) pairs whose carried attestation verified
    #: at load against the trust roots, session, and freshness terms.
    #: Verification concentrates at the load boundary: accept/confirm
    #: decide from authenticated material, never re-verify.
    verified_evidence: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        trusted_issuers: Mapping[str, bytes],
        evidence_max_age_secs: int = 86400,
        now_secs: int | None = None,
    ) -> "ExecutionRequirement":
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
        if not isinstance(trusted_issuers, Mapping) or not trusted_issuers:
            raise BindingError("requirement needs operator trust roots (issuer key ids)")
        if (
            not isinstance(evidence_max_age_secs, int)
            or isinstance(evidence_max_age_secs, bool)
            or evidence_max_age_secs <= 0
        ):
            raise BindingError("evidence max age must be a positive number of seconds")
        clock = now_secs if now_secs is not None else int(time.time())
        parsed = tuple(
            AtlasDecision.from_dict(item, trusted_issuers=trusted_issuers) for item in decisions
        )
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
        verified: list[tuple[str, str]] = []
        for leg in legs:
            if not isinstance(leg, Mapping) or not leg.get("name"):
                raise BindingError("each leg needs a name")
            needs = leg.get("needs", [])
            if not isinstance(needs, list) or not all(isinstance(item, str) for item in needs):
                raise BindingError(f"leg {leg.get('name')!r} needs a string list")
            evidence = leg.get("evidence", {})
            if not isinstance(evidence, Mapping):
                raise BindingError(f"leg {leg.get('name')!r} evidence must map")
            for name, item in evidence.items():
                # Authenticate each carried value now: only attestations
                # bound to this name, session, scope, subject, and
                # freshness verify. Bare values stay carried but inert.
                if check_attestation(
                    item,
                    name=str(name),
                    participant=str(session["participant"]),
                    scope=str(session["scope"]),
                    subject="",
                    trusted_issuers=trusted_issuers,
                    now_secs=clock,
                    max_age_secs=evidence_max_age_secs,
                ):
                    verified.append((str(leg["name"]), str(name)))
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
            dict(bounds),
            str(session["participant"]),
            str(session["scope"]),
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
            trusted_key_ids=tuple(sorted(str(key) for key in trusted_issuers)),
            evidence_max_age_secs=evidence_max_age_secs,
            evidence_now_secs=clock,
            verified_evidence=tuple(verified),
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
        """Decide one leg purely from authenticated carried material.

        Granted covers needs. Denied refuses. Conditional is satisfied only
        when every missing evidence name was authenticated at load (an
        issuer-signed attestation bound to name, session, scope, and
        freshness) or is ``execution.target`` derived from a verified
        target binding; otherwise UNKNOWN. Bare caller-written values
        never satisfy. A leg that declares no target while Atlas
        constrains one refuses: authorized placement cannot be shown. An
        observed artifact digest that differs from the bound one refuses
        (tamper fail-closed).
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
                supplied = {
                    name for leg_name, name in self.verified_evidence if leg_name == leg.name
                }
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
        # Final target gate EXECUTES the MNCS kernel (mncs/harness_atlas.mncs
        # dispatch_gate): a granted fold authorizes dispatch only while the
        # observed target still matches the Atlas-authorized one.
        gate: str = mncs_exec.dispatch_gate("GRANTED", actual_target == leg.target)
        if gate != "GRANTED":
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


def _decision_grant(decision: AtlasDecision) -> str:
    """Project one carried decision onto the MNCS Atlas verdict lattice."""
    if decision.status == "denied":
        return "REFUSED"
    if decision.status == "conditional" and sorted(set(decision.missing)):
        return "UNKNOWN"
    return "GRANTED"


def _fold_capability(capability: str, matches: list[AtlasDecision]) -> Acceptance:
    # The fold verdict EXECUTES the MNCS kernel (mncs/harness_atlas.mncs
    # via mncs_exec.fold): duplicate decisions fold conservatively and
    # order-independently with denial winning; UNKNOWN is never promoted.
    verdict: str = mncs_exec.fold([_decision_grant(decision) for decision in matches])
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
            reasons.append(f"{decision.capability}: atlas denied")
        elif decision.status == "conditional":
            missing = sorted(set(decision.missing))
            if missing:
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
