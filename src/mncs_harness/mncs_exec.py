"""Canonical production decision entrypoints: every function here EXECUTES MNCS.

Each entrypoint marshals bounded host observations, calls the shipped frozen
artifact through :mod:`mncs_harness.mncs_runtime`, decodes the typed result,
and returns it. Production modules (router, policy, verifiers,
atlas_binding, …) must import these — never the transitional Python mirror.

Fail-closed: any MNCS execution defect raises ``MncsRuntimeError`` to the
caller. The ONLY exception is the explicitly-marked transitional branch
requiring operator opt-in via ``MNCS_HARNESS_TRANSITIONAL_PYTHON_DECISIONS=1``,
which warns loudly and is never conformance-bearing.
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

_ROUTING_MODULE = "mncs.harness.routing.v1"
_POLICY_MODULE = "mncs.harness.policy.v1"
_VERDICT_MODULE = "mncs.harness.verdict.v1"
_ATLAS_MODULE = "mncs.harness.atlas.v1"
_PINS_MODULE = "mncs.harness.pins.v1"
_FABRIC_MODULE = "mncs.harness.fabric.v1"
_READINESS_MODULE = "mncs.harness.readiness.v1"


def _finite(module: str, enum: str, variant: str) -> tuple[str, str, str]:
    return (module, enum, variant)


def _variant(result: Any, function: str) -> str:
    if not isinstance(result, tuple):
        raise mncs_runtime.MncsRuntimeError(f"MNCS {function} returned non-enum {result!r}")
    return result[0]


def _as_variant(result: Any, function: str, valid: tuple[str, ...]) -> str:
    """Decode a kernel enum from either shape.

    Real execution returns the ``(variant, payload)`` tuple decoded from the
    artifact ABI. The explicitly-marked transitional mirror returns the bare
    host variant string. Both are validated against the kernel's vocabulary.
    """
    if isinstance(result, tuple):
        variant = _variant(result, function)
    elif isinstance(result, str):
        variant = result
    else:
        raise mncs_runtime.MncsRuntimeError(f"MNCS {function} returned {result!r}")
    if variant not in valid:
        raise mncs_runtime.MncsRuntimeError(f"MNCS {function} returned unknown {variant!r}")
    return variant


def _transitional(kernel: str, function: str, *args: Any) -> Any:
    """TRANSITIONAL-ONLY Python fallback. Requires operator opt-in. Not canonical."""
    if not mncs_runtime.transitional_allowed():
        raise mncs_runtime.MncsRuntimeError(
            f"MNCS execution failed for {function} and no transitional fallback is enabled"
        )
    mncs_runtime.warn_transitional(f"{kernel}::{function}")
    from . import mncs_logic  # TRANSITIONAL-ONLY import; production path never reaches here

    mirror = {
        (_ROUTING, "classify_route"): mncs_logic.classify_route,
        (_ROUTING, "needs_coder"): mncs_logic.needs_coder,
        (_POLICY, "evaluate_command"): mncs_logic.command_reason,
        (_POLICY, "evaluate_write"): mncs_logic.write_reason,
        (_VERDICT, "combine8"): lambda chunk: mncs_logic.combine(list(chunk)),
        (_VERDICT, "combine4"): lambda group: mncs_logic.combine(list(group)),
        (_VERDICT, "is_decided"): mncs_logic.is_decided,
        (_ATLAS, "fold4"): lambda group: mncs_logic.fold(list(group)),
        (_ATLAS, "fold_pair"): mncs_logic.fold_pair,
        (_ATLAS, "dispatch_gate"): mncs_logic.dispatch_gate,
        (_PINS, "pin_fields_valid"): mncs_logic.pin_fields_valid,
        (_PINS, "admit_placement"): mncs_logic.admit_placement,
        (_FABRIC, "classify_fabric"): mncs_logic.classify_fabric,
        (_FABRIC, "dispatch_allowed"): mncs_logic.fabric_dispatch_allowed,
        (_READINESS, "dominate_readiness"): mncs_logic.dominate_readiness,
        (_READINESS, "fold4"): lambda quad: mncs_logic.fold_readiness(list(quad)),
        (_READINESS, "fold8"): lambda envelope: mncs_logic.fold_readiness(list(envelope)),
        (_READINESS, "is_ready"): mncs_logic.readiness_ready,
    }[(kernel, function)]
    return mirror(*args)


def _call(
    kernel: str,
    function: str,
    args: list[Any],
    mirror_args: tuple[Any, ...],
) -> Any:
    try:
        return mncs_runtime.call(kernel, function, args)
    except mncs_runtime.MncsRuntimeError:
        if not mncs_runtime.transitional_allowed():
            raise
        return _transitional(kernel, function, *mirror_args)


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
        (has_code, asks_edit, asks_exec, is_high_risk, has_image, is_complex),
    )
    if isinstance(result, str) and result in ("e2b", "e4b", "reviewer"):
        return result  # type: ignore[return-value]  # transitional mirror value
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
        (primary, code_specialist, has_code),
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
        (
            executable_blocked,
            allowlisted,
            has_shell_operator,
            shell_ok,
            python_ok,
            git_ok,
            path_ok,
        ),
    )
    if isinstance(result, int) and 0 <= result <= 8:
        return result  # transitional mirror value
    variant, payload = result if isinstance(result, tuple) else ("", {})
    if variant == "Allow":
        return 0
    if variant == "Block" and isinstance(payload.get("reason"), int):
        return payload["reason"]
    raise mncs_runtime.MncsRuntimeError(f"evaluate_command returned {result!r}")


def write_allowed(targets_protected_internals: bool) -> bool:
    """Execute ``mncs.harness.policy.v1::evaluate_write``."""
    result = _call(
        _POLICY,
        "evaluate_write",
        [targets_protected_internals],
        (targets_protected_internals,),
    )
    if isinstance(result, bool):
        return result  # transitional mirror value
    if isinstance(result, int) and result in (0, 8):
        return result == 0  # transitional mirror value
    variant = _as_variant(result, "evaluate_write", ("Allow", "Block"))
    return variant == "Allow"


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
                _call(
                    _VERDICT,
                    "combine8",
                    [_status_arg(status) for status in padded],
                    (chunk,),
                ),
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
                    _call(
                        _VERDICT,
                        "combine4",
                        [_status_arg(status) for status in group],
                        (group,),
                    ),
                    "combine4",
                    ("PASS", "FAIL", "UNKNOWN"),
                )
            )
        halves = folded
    if len(halves) == 1:
        return halves[0]  # type: ignore[return-value]
    padded = (halves + ["PASS"] * 4)[:4]
    return _as_variant(
        _call(_VERDICT, "combine4", [_status_arg(status) for status in padded], (padded,)),
        "combine4",
        ("PASS", "FAIL", "UNKNOWN"),
    )  # type: ignore[return-value]


def verdict_decided(status: str) -> bool:
    """Execute ``mncs.harness.verdict.v1::is_decided``."""
    result = _call(_VERDICT, "is_decided", [_status_arg(status)], (status,))
    if not isinstance(result, bool):
        raise mncs_runtime.MncsRuntimeError(f"is_decided returned non-bool {result!r}")
    return result


def _grant_arg(grant: str) -> tuple[str, str, str]:
    if grant not in ("GRANTED", "UNKNOWN", "REFUSED"):
        raise mncs_runtime.MncsRuntimeError(f"cannot marshal grant {grant!r}")
    return _finite(_ATLAS_MODULE, "Grant", grant)


def fold_pair(left: str, right: str) -> HostGrant:
    """Execute ``mncs.harness.atlas.v1::fold_pair``."""
    result = _call(
        _ATLAS, "fold_pair", [_grant_arg(left), _grant_arg(right)], (left, right)
    )
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
                _call(_ATLAS, "fold4", [_grant_arg(g) for g in group], (group,)),
                "fold4",
                ("GRANTED", "UNKNOWN", "REFUSED"),
            )
        )
    result: str = halves[0]
    for extra in halves[1:]:
        result = fold_pair(result, extra)
    return result  # type: ignore[return-value]


def dispatch_gate(folded: str, target_matches: bool) -> HostGrant:
    """Execute ``mncs.harness.atlas.v1::dispatch_gate``."""
    result = _call(
        _ATLAS, "dispatch_gate", [_grant_arg(folded), target_matches], (folded, target_matches)
    )
    return _as_variant(result, "dispatch_gate", ("GRANTED", "UNKNOWN", "REFUSED"))  # type: ignore[return-value]


_PIN_MODES = ("AUTO", "ROLE", "MODEL", "WORKER", "WORKER_MODEL", "WORKER_MODEL_ROLE")


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
        (mode, has_role, has_worker, has_model),
    )
    if not isinstance(result, bool):
        raise mncs_runtime.MncsRuntimeError(f"pin_fields_valid returned non-bool {result!r}")
    return result


def admit_placement(auto_or_role: bool, selection_present: bool, allow_fallback: bool) -> str:
    """Execute ``mncs.harness.pins.v1::admit_placement``."""
    result = _call(
        _PINS,
        "admit_placement",
        [auto_or_role, selection_present, allow_fallback],
        (auto_or_role, selection_present, allow_fallback),
    )
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
        (missing_any, parseable, too_old, exact, version_is_certified),
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
        (classification,),
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
        _READINESS,
        "dominate_readiness",
        [_readiness_arg(left), _readiness_arg(right)],
        (left, right),
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
                    _call(
                        _READINESS,
                        "fold4",
                        [_readiness_arg(state) for state in quad],
                        (quad,),
                    ),
                    "fold4",
                    _READINESS_STATES,
                )
            )
        else:
            partials.append(
                _as_variant(
                    _call(
                        _READINESS,
                        "fold8",
                        [_readiness_arg(state) for state in padded],
                        (padded,),
                    ),
                    "fold8",
                    _READINESS_STATES,
                )
            )
    while tail:
        quad, tail = (tail[:4] + ["READY"] * 4)[:4], tail[4:]
        partials.append(
            _as_variant(
                _call(
                    _READINESS,
                    "fold4",
                    [_readiness_arg(state) for state in quad],
                    (quad,),
                ),
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
    result = _call(
        _READINESS, "is_ready", [_readiness_arg(state)], (state,)
    )
    if not isinstance(result, bool):
        raise mncs_runtime.MncsRuntimeError(f"is_ready returned non-bool {result!r}")
    return result
