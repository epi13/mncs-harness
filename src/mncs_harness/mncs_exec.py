"""Canonical production decision entrypoints: every function here EXECUTES MNCS.

Each entrypoint marshals bounded host observations, calls the shipped frozen
artifact through :mod:`mncs_harness.mncs_runtime`, decodes the typed result,
and returns it. Production modules (router, policy, verifiers,
atlas_binding, …) must import these — never any Python reimplementation.

Fail-closed: any MNCS execution defect raises ``MncsRuntimeError`` to the
caller. There is no Python fallback: a missing executor, broken artifact,
or malformed result fails the operation, never silently re-decides it in
Python.
"""

from __future__ import annotations

from typing import Any, Literal

from . import mncs_runtime

HostRole = Literal["e2b", "e4b", "reviewer"]
HostStatus = Literal["PASS", "FAIL", "UNKNOWN"]
HostGrant = Literal["GRANTED", "UNKNOWN", "REFUSED"]

_ROUTING = "harness_routing"
_POLICY = "harness_policy"
_VERDICT = "harness_verdict"
_ATLAS = "harness_atlas"
_PINS = "harness_pins"
_FABRIC = "harness_fabric"
_READINESS = "harness_readiness"
_ELIGIBILITY = "harness_eligibility"
_FRESHNESS = "harness_freshness"

_ROUTING_MODULE = "mncs.harness.routing.v1"
_POLICY_MODULE = "mncs.harness.policy.v1"
_VERDICT_MODULE = "mncs.harness.verdict.v1"
_ATLAS_MODULE = "mncs.harness.atlas.v1"
_PINS_MODULE = "mncs.harness.pins.v1"
_FABRIC_MODULE = "mncs.harness.fabric.v1"
_READINESS_MODULE = "mncs.harness.readiness.v1"
_ELIGIBILITY_MODULE = "mncs.harness.eligibility.v1"
_FRESHNESS_MODULE = "mncs.harness.freshness.v1"


def _finite(module: str, enum: str, variant: str) -> tuple[str, str, str]:
    return (module, enum, variant)


def _variant(result: Any, function: str) -> str:
    if not isinstance(result, tuple):
        raise mncs_runtime.MncsRuntimeError(f"MNCS {function} returned non-enum {result!r}")
    return result[0]


def _as_variant(result: Any, function: str, valid: tuple[str, ...]) -> str:
    """Decode a kernel enum from the real-execution ``(variant, payload)`` shape."""
    variant = _variant(result, function)
    if variant not in valid:
        raise mncs_runtime.MncsRuntimeError(f"MNCS {function} returned unknown {variant!r}")
    return variant


def _call(kernel: str, function: str, args: list[Any]) -> Any:
    return mncs_runtime.call(kernel, function, args)


def primary_role(
    has_code: bool,
    asks_edit: bool,
    asks_exec: bool,
    is_high_risk: bool,
    has_image: bool,
    is_complex: bool,
) -> HostRole:
    """Execute ``mncs.harness.routing.v1::classify_route``. Returns e2b/e4b/reviewer."""
    result = _call(
        _ROUTING,
        "classify_route",
        [has_code, asks_edit, asks_exec, is_high_risk, has_image, is_complex],
    )
    variant = _as_variant(result, "classify_route", ("E2B", "E4B", "REVIEWER"))
    return {"E2B": "e2b", "E4B": "e4b", "REVIEWER": "reviewer"}[variant]  # type: ignore[return-value]


def needs_coder(primary: str, code_specialist: bool, has_code: bool) -> bool:
    """Execute ``mncs.harness.routing.v1::needs_coder``."""
    inverse = {"e2b": "E2B", "e4b": "E4B", "coder": "CODER", "reviewer": "REVIEWER"}
    if primary not in inverse:
        raise mncs_runtime.MncsRuntimeError(f"unknown host role {primary!r}")
    result = _call(
        _ROUTING,
        "needs_coder",
        [_finite(_ROUTING_MODULE, "Role", inverse[primary]), code_specialist, has_code],
    )
    if not isinstance(result, bool):
        raise mncs_runtime.MncsRuntimeError(f"needs_coder returned non-bool {result!r}")
    return result


def command_reason(
    executable_blocked: bool,
    allowlisted: bool,
    has_shell_operator: bool,
    shell_ok: bool,
    python_ok: bool,
    git_ok: bool,
    path_ok: bool,
) -> int:
    """Execute ``mncs.harness.policy.v1::evaluate_command``. Returns a reason code."""
    result = _call(
        _POLICY,
        "evaluate_command",
        [
            executable_blocked,
            allowlisted,
            has_shell_operator,
            shell_ok,
            python_ok,
            git_ok,
            path_ok,
        ],
    )
    variant, payload = result if isinstance(result, tuple) else ("", {})
    if variant == "Allow":
        return 0
    if variant == "Block" and isinstance(payload.get("reason"), int):
        return payload["reason"]
    raise mncs_runtime.MncsRuntimeError(f"evaluate_command returned {result!r}")


def write_allowed(targets_protected_internals: bool) -> bool:
    """Execute ``mncs.harness.policy.v1::evaluate_write``."""
    result = _call(_POLICY, "evaluate_write", [targets_protected_internals])
    variant = _as_variant(result, "evaluate_write", ("Allow", "Block"))
    return variant == "Allow"


_PUBLICATION = ("PROCEED", "SKIP_DISABLED", "SKIP_NOT_READY", "SKIP_NO_RECORD")


def publication_gate(configured: bool, session_ready: bool, record_present: bool) -> str:
    """Execute ``mncs.harness.policy.v1::publication_gate``.

    Returns PROCEED or the ordered skip reason (disabled beats not-ready
    beats no-record). The host performs the publish effect or maps the skip
    to its error code; the precedence itself is MNCS-owned.
    """
    result = _call(_POLICY, "publication_gate", [configured, session_ready, record_present])
    return _as_variant(result, "publication_gate", _PUBLICATION)


def _status_arg(status: str) -> tuple[str, str, str]:
    if status not in ("PASS", "FAIL", "UNKNOWN"):
        raise mncs_runtime.MncsRuntimeError(f"cannot marshal status {status!r}")
    return _finite(_VERDICT_MODULE, "Status", status)


def combine(checks: list[str]) -> HostStatus:
    """Execute the verdict lattice fold over a check envelope.

    Wide envelopes fold as two halves through ``combine8``/``combine4`` —
    the same fixed tree the corpus pins — so arbitrarily long host lists
    never invent a new semantic.
    """
    for status in checks:
        if status not in ("PASS", "FAIL", "UNKNOWN"):
            raise mncs_runtime.MncsRuntimeError(f"cannot marshal status {status!r}")
    if not checks:
        return "PASS"
    halves: list[str] = []
    chunked = [checks[index : index + 8] for index in range(0, len(checks), 8)]
    for chunk in chunked:
        padded = chunk + ["PASS"] * (8 - len(chunk))
        halves.append(
            _as_variant(
                _call(_VERDICT, "combine8", [_status_arg(status) for status in padded]),
                "combine8",
                ("PASS", "FAIL", "UNKNOWN"),
            )
        )
    while len(halves) > 4:
        folded: list[str] = []
        for index in range(0, len(halves), 4):
            group = (halves[index : index + 4] + ["PASS"] * 4)[:4]
            folded.append(
                _as_variant(
                    _call(_VERDICT, "combine4", [_status_arg(status) for status in group]),
                    "combine4",
                    ("PASS", "FAIL", "UNKNOWN"),
                )
            )
        halves = folded
    if len(halves) == 1:
        return halves[0]  # type: ignore[return-value]
    padded = (halves + ["PASS"] * 4)[:4]
    return _as_variant(
        _call(_VERDICT, "combine4", [_status_arg(status) for status in padded]),
        "combine4",
        ("PASS", "FAIL", "UNKNOWN"),
    )  # type: ignore[return-value]


def verdict_decided(status: str) -> bool:
    """Execute ``mncs.harness.verdict.v1::is_decided``."""
    result = _call(_VERDICT, "is_decided", [_status_arg(status)])
    if not isinstance(result, bool):
        raise mncs_runtime.MncsRuntimeError(f"is_decided returned non-bool {result!r}")
    return result


def _grant_arg(grant: str) -> tuple[str, str, str]:
    if grant not in ("GRANTED", "UNKNOWN", "REFUSED"):
        raise mncs_runtime.MncsRuntimeError(f"cannot marshal grant {grant!r}")
    return _finite(_ATLAS_MODULE, "Grant", grant)


def fold_pair(left: str, right: str) -> HostGrant:
    """Execute ``mncs.harness.atlas.v1::fold_pair``."""
    result = _call(_ATLAS, "fold_pair", [_grant_arg(left), _grant_arg(right)])
    return _as_variant(result, "fold_pair", ("GRANTED", "UNKNOWN", "REFUSED"))  # type: ignore[return-value]


def fold(decisions: list[str]) -> HostGrant:
    """Fold duplicate capability decisions through ``fold4`` trees (denial wins)."""
    for grant in decisions:
        if grant not in ("GRANTED", "UNKNOWN", "REFUSED"):
            raise mncs_runtime.MncsRuntimeError(f"cannot marshal grant {grant!r}")
    if not decisions:
        return "GRANTED"
    halves: list[str] = []
    for index in range(0, len(decisions), 4):
        group = (decisions[index : index + 4] + ["GRANTED"] * 4)[:4]
        halves.append(
            _as_variant(
                _call(_ATLAS, "fold4", [_grant_arg(g) for g in group]),
                "fold4",
                ("GRANTED", "UNKNOWN", "REFUSED"),
            )
        )
    result: str = halves[0]
    for extra in halves[1:]:
        result = fold_pair(result, extra)
    return result  # type: ignore[return-value]


def tool_admission(granted: bool, covered: bool) -> HostGrant:
    """Execute ``mncs.harness.atlas.v1::tool_admission``."""
    result = _call(_ATLAS, "tool_admission", [granted, covered])
    return _as_variant(result, "tool_admission", ("GRANTED", "UNKNOWN", "REFUSED"))  # type: ignore[return-value]


def dispatch_gate(folded: str, target_matches: bool) -> HostGrant:
    """Execute ``mncs.harness.atlas.v1::dispatch_gate``."""
    result = _call(_ATLAS, "dispatch_gate", [_grant_arg(folded), target_matches])
    return _as_variant(result, "dispatch_gate", ("GRANTED", "UNKNOWN", "REFUSED"))  # type: ignore[return-value]


_PIN_MODES = ("AUTO", "ROLE", "MODEL", "WORKER", "WORKER_MODEL", "WORKER_MODEL_ROLE")


def is_exact_pin(mode: str) -> bool:
    """Execute ``mncs.harness.pins.v1::is_exact_pin``.

    AUTO and ROLE route automatically; every other operator mode carries an
    exact pin. Shared by the router (exact pins skip the escalation cascade)
    and the agent (exact-pin progress branch).
    """
    if mode not in _PIN_MODES:
        raise mncs_runtime.MncsRuntimeError(f"unknown pin mode {mode!r}")
    result = _call(_PINS, "is_exact_pin", [_finite(_PINS_MODULE, "PinMode", mode)])
    if not isinstance(result, bool):
        raise mncs_runtime.MncsRuntimeError(f"is_exact_pin returned non-bool {result!r}")
    return result


def pin_fields_valid(mode: str, has_role: bool, has_worker: bool, has_model: bool) -> bool:
    """Execute ``mncs.harness.pins.v1::pin_fields_valid`` (shape table only)."""
    if mode not in _PIN_MODES:
        raise mncs_runtime.MncsRuntimeError(f"unknown pin mode {mode!r}")
    result = _call(
        _PINS,
        "pin_fields_valid",
        [
            _finite(_PINS_MODULE, "PinMode", mode),
            has_role,
            has_worker,
            has_model,
        ],
    )
    if not isinstance(result, bool):
        raise mncs_runtime.MncsRuntimeError(f"pin_fields_valid returned non-bool {result!r}")
    return result


def admit_placement(auto_or_role: bool, selection_present: bool, allow_fallback: bool) -> str:
    """Execute ``mncs.harness.pins.v1::admit_placement``."""
    result = _call(_PINS, "admit_placement", [auto_or_role, selection_present, allow_fallback])
    return _as_variant(
        result,
        "admit_placement",
        ("AUTO_ALLOWED", "PIN_HONORED", "PIN_FAILED_CLOSED", "FALLBACK_ALLOWED"),
    )


def classify_fabric(
    missing_any: bool,
    parseable: bool,
    too_old: bool,
    exact: bool,
    version_is_certified: bool,
) -> str:
    """Execute ``mncs.harness.fabric.v1::classify_fabric``."""
    result = _call(
        _FABRIC,
        "classify_fabric",
        [missing_any, parseable, too_old, exact, version_is_certified],
    )
    return _as_variant(
        result,
        "classify_fabric",
        (
            "INCOMPATIBLE",
            "UNKNOWN",
            "TOO_OLD",
            "EXPERIMENT_CERTIFIED_EXACT",
            "COMPATIBLE_VERSION_ONLY",
            "COMPATIBLE_NEWER",
        ),
    )


def fabric_dispatch_allowed(classification: str) -> bool:
    """Execute ``mncs.harness.fabric.v1::dispatch_allowed``."""
    valid = (
        "INCOMPATIBLE",
        "UNKNOWN",
        "TOO_OLD",
        "EXPERIMENT_CERTIFIED_EXACT",
        "COMPATIBLE_VERSION_ONLY",
        "COMPATIBLE_NEWER",
    )
    if classification not in valid:
        raise mncs_runtime.MncsRuntimeError(f"cannot marshal FabricClass {classification!r}")
    result = _call(
        _FABRIC,
        "dispatch_allowed",
        [_finite(_FABRIC_MODULE, "FabricClass", classification)],
    )
    if not isinstance(result, bool):
        raise mncs_runtime.MncsRuntimeError(f"dispatch_allowed returned non-bool {result!r}")
    return result


_READINESS_STATES = ("READY", "DEGRADED", "BLOCKED", "UNKNOWN")


def _readiness_arg(state: str) -> tuple[str, str, str]:
    if state not in _READINESS_STATES:
        raise mncs_runtime.MncsRuntimeError(f"cannot marshal readiness {state!r}")
    return _finite(_READINESS_MODULE, "Readiness", state)


def dominate_readiness(left: str, right: str) -> str:
    """Execute ``mncs.harness.readiness.v1::dominate_readiness``."""
    result = _call(
        _READINESS, "dominate_readiness", [_readiness_arg(left), _readiness_arg(right)]
    )
    return _as_variant(result, "dominate_readiness", _READINESS_STATES)


def fold_readiness(layers: list[str]) -> str:
    """Fold a required-layer envelope through the generic-readiness envelopes.

    Envelopes of up to 8 fold in one ``fold8`` call; wider envelopes fold as
    8-wide head plus 4-wide tail pieces combined with ``dominate_readiness``.
    """
    for state in layers:
        if state not in _READINESS_STATES:
            raise mncs_runtime.MncsRuntimeError(f"cannot marshal readiness {state!r}")
    if not layers:
        return "READY"
    partials: list[str] = []
    head, tail = layers[:8], layers[8:]
    if head:
        padded = (head + ["READY"] * 8)[:8]
        if len(head) <= 4 and not tail:
            quad = (head + ["READY"] * 4)[:4]
            partials.append(
                _as_variant(
                    _call(_READINESS, "fold4", [_readiness_arg(state) for state in quad]),
                    "fold4",
                    _READINESS_STATES,
                )
            )
        else:
            partials.append(
                _as_variant(
                    _call(_READINESS, "fold8", [_readiness_arg(state) for state in padded]),
                    "fold8",
                    _READINESS_STATES,
                )
            )
    while tail:
        quad, tail = (tail[:4] + ["READY"] * 4)[:4], tail[4:]
        partials.append(
            _as_variant(
                _call(_READINESS, "fold4", [_readiness_arg(state) for state in quad]),
                "fold4",
                _READINESS_STATES,
            )
        )
    result = partials[0]
    for extra in partials[1:]:
        result = dominate_readiness(result, extra)
    return result


def readiness_ready(state: str) -> bool:
    """Execute ``mncs.harness.readiness.v1::is_ready``."""
    result = _call(_READINESS, "is_ready", [_readiness_arg(state)])
    if not isinstance(result, bool):
        raise mncs_runtime.MncsRuntimeError(f"is_ready returned non-bool {result!r}")
    return result


_CAPABILITIES = ("COMPLETION", "TOOLS", "CODE_EDIT")
_OBSERVED = ("NONE", "PASS", "FAIL")
_UNKNOWN_POLICIES = ("FAIL_CLOSED", "EXPLORE", "PROVIDER_CLAIM_COMPAT", "OTHER")
_RESOURCE_VERDICTS = ("OK", "MISSING_FACTS", "OVER_BUDGET", "OVER_AVAILABLE")
_CAP_SOURCES = ("OBSERVATION", "LEGACY", "NONE")


def capability_eligible(
    capability: str,
    observed: str,
    claimed: bool,
    unknown_policy: str,
    require_observed: bool,
    allow_unclaimed: bool,
) -> bool:
    """Execute ``mncs.harness.eligibility.v1::capability_eligible``."""
    if capability not in _CAPABILITIES:
        raise mncs_runtime.MncsRuntimeError(f"cannot marshal capability {capability!r}")
    if observed not in _OBSERVED:
        raise mncs_runtime.MncsRuntimeError(f"cannot marshal observed {observed!r}")
    if unknown_policy not in _UNKNOWN_POLICIES:
        raise mncs_runtime.MncsRuntimeError(f"cannot marshal unknown policy {unknown_policy!r}")
    result = _call(
        _ELIGIBILITY,
        "capability_eligible",
        [
            _finite(_ELIGIBILITY_MODULE, "Capability", capability),
            _finite(_ELIGIBILITY_MODULE, "Observed", observed),
            claimed,
            _finite(_ELIGIBILITY_MODULE, "UnknownPolicy", unknown_policy),
            require_observed,
            allow_unclaimed,
        ],
    )
    if not isinstance(result, bool):
        raise mncs_runtime.MncsRuntimeError(f"capability_eligible returned {result!r}")
    return result


def capability_source(
    available: bool,
    inventory_current: bool,
    observation_present: bool,
    legacy_present: bool,
) -> str:
    """Execute ``mncs.harness.eligibility.v1::capability_source``.

    Selects which capability inventory — live observation, legacy worker
    inventory, or none — the host capability graph may project. The host
    encodes availability/currency/presence bools; string matching stays
    host-side.
    """
    result = _call(
        _ELIGIBILITY,
        "capability_source",
        [available, inventory_current, observation_present, legacy_present],
    )
    return _as_variant(result, "capability_source", _CAP_SOURCES)


def residency_admit(has_conflicts: bool, reject_conflicting: bool, already_loaded: bool) -> bool:
    """Execute ``mncs.harness.eligibility.v1::residency_admit``.

    True means the worker may proceed to warm the assigned model; False is
    the implicit-eviction veto (other models loaded, policy rejects thrash,
    assigned model not already loaded). The host maps False to the
    RESIDENCY_CONFLICTING_LOADED_MODELS record.
    """
    result = _call(
        _ELIGIBILITY,
        "residency_admit",
        [has_conflicts, reject_conflicting, already_loaded],
    )
    if not isinstance(result, bool):
        raise mncs_runtime.MncsRuntimeError(f"residency_admit returned non-bool {result!r}")
    return result


def resource_gate(
    facts_complete: bool,
    size: int,
    budget: int,
    has_available: bool,
    available: int,
) -> str:
    """Execute ``mncs.harness.eligibility.v1::resource_gate``."""
    result = _call(
        _ELIGIBILITY,
        "resource_gate",
        [facts_complete, size, budget, has_available, available],
    )
    return _as_variant(result, "resource_gate", _RESOURCE_VERDICTS)


def observation_fresh(stored_ms: int, now_ms: int, max_age_ms: int) -> bool:
    """Execute ``mncs.harness.freshness.v1::observation_fresh``.

    Integer-millisecond facts in, freshness verdict out. The host owns
    clock reads and timestamp parsing; the window decision is
    machine-native. Future-dated stores read fresh (skew floor).
    """
    result = _call(
        _FRESHNESS,
        "observation_fresh",
        [stored_ms, now_ms, max_age_ms],
    )
    if not isinstance(result, bool):
        raise mncs_runtime.MncsRuntimeError(
            f"observation_fresh returned non-bool {result!r}"
        )
    return result


def attestation_window_ok(issued_at: int, now: int, max_age: int) -> bool:
    """Execute ``mncs.harness.freshness.v1::attestation_window_ok``.

    Strict window for issuance attestations: future-dated never supplies.
    Units are caller-consistent with the caller (seconds for Atlas).
    """
    result = _call(
        _FRESHNESS,
        "attestation_window_ok",
        [issued_at, now, max_age],
    )
    if not isinstance(result, bool):
        raise mncs_runtime.MncsRuntimeError(
            f"attestation_window_ok returned non-bool {result!r}"
        )
    return result
