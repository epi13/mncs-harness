"""TEST-ONLY oracle for the MNCS decision kernels. NEVER IMPORT FROM src/.

This module duplicates the truth tables of ``mncs/harness_*.mncs`` in Python
for one purpose only: independent agreement checks in ``tests/``
(``test_mncs_exec.py`` compares real MNCS execution against this oracle, and
``test_mncs_logic.py`` checks it against ``corpora/*.json``).

It is NOT a production implementation and NOT a fallback: production code
must call ``mncs_exec`` (real MNCS execution, fail-closed). CI fails any
``src/mncs_harness`` import of this module.

Authority for these truth tables is the MNCS source under ``mncs/``::

    mncs/harness_routing.mncs  (mncs.harness.routing.v1)
    mncs/harness_policy.mncs   (mncs.harness.policy.v1)
    mncs/harness_verdict.mncs  (mncs.harness.verdict.v1)
    mncs/harness_atlas.mncs    (mncs.harness.atlas.v1)
    mncs/harness_pins.mncs     (mncs.harness.pins.v1)
    mncs/harness_fabric.mncs   (mncs.harness.fabric.v1)
    mncs/harness_readiness.mncs (mncs.harness.readiness.v1)
    mncs/harness_eligibility.mncs (mncs.harness.eligibility.v1)
    mncs/harness_freshness.mncs (mncs.harness.freshness.v1)

It is a projection, never a second authority: change the ``.mncs`` source
and corpus first, re-run the backend evidence, then update the oracle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

REPO_ROOT = Path(__file__).resolve().parents[1]
MNCS_DIR = REPO_ROOT / "mncs"
CORPORA_DIR = REPO_ROOT / "corpora"

Role = Literal["e2b", "e4b", "reviewer"]
Status = Literal["PASS", "FAIL", "UNKNOWN"]
Grant = Literal["GRANTED", "UNKNOWN", "REFUSED"]

MNCS_ROLE = {"E2B": "e2b", "E4B": "e4b", "CODER": "coder", "REVIEWER": "reviewer"}
HOST_ROLE = {value: key for key, value in MNCS_ROLE.items()}


def classify_route(
    has_code: bool,
    asks_edit: bool,
    asks_exec: bool,
    is_high_risk: bool,
    has_image: bool,
    is_complex: bool,
) -> Role:
    """Mirror of ``mncs.harness.routing.v1::classify_route``."""
    if is_high_risk or has_image or is_complex:
        return "reviewer"
    if has_code and (asks_edit or asks_exec):
        return "e4b"
    if asks_edit or asks_exec:
        return "e4b"
    return "e2b"


def needs_coder(primary: str, code_specialist: bool, has_code: bool) -> bool:
    """Mirror of ``mncs.harness.routing.v1::needs_coder``."""
    return primary == "e4b" and code_specialist and has_code


def route_task_profile(profile, code_specialist: bool) -> Role:
    """Classify a router ``TaskProfile`` through the MNCS kernel.

    The host owns tokenization and profiling (``router.profile_task``);
    the primary-role decision itself follows the MNCS truth table.
    """
    return classify_route(
        profile.has_code,
        profile.asks_for_edit,
        profile.asks_for_execution,
        profile.is_high_risk,
        profile.has_image,
        profile.is_complex,
    )


# Reason codes mirror ``mncs.harness.policy.v1`` exactly.
ALLOW = 0
BLOCKED_EXECUTABLE = 1
NOT_ALLOWLISTED = 2
SHELL_OPERATOR = 3
SHELL_RULE = 4
PYTHON_RULE = 5
GIT_RULE = 6
PATH_RULE = 7
PROTECTED_WRITE = 8


def command_reason(
    executable_blocked: bool,
    allowlisted: bool,
    has_shell_operator: bool,
    shell_ok: bool,
    python_ok: bool,
    git_ok: bool,
    path_ok: bool,
) -> int:
    """Mirror of ``mncs.harness.policy.v1::evaluate_command`` as a code."""
    if executable_blocked:
        return BLOCKED_EXECUTABLE
    if not allowlisted:
        return NOT_ALLOWLISTED
    if has_shell_operator:
        return SHELL_OPERATOR
    if not shell_ok:
        return SHELL_RULE
    if not python_ok:
        return PYTHON_RULE
    if not git_ok:
        return GIT_RULE
    if not path_ok:
        return PATH_RULE
    return ALLOW


def write_reason(targets_protected_internals: bool) -> int:
    """Mirror of ``mncs.harness.policy.v1::evaluate_write`` as a code."""
    return PROTECTED_WRITE if targets_protected_internals else ALLOW


def dominate(left: Status, right: Status) -> Status:
    """Mirror of ``mncs.harness.verdict.v1::dominate`` (also stdlib status)."""
    if left == "FAIL" or right == "FAIL":
        return "FAIL"
    if left == "UNKNOWN" or right == "UNKNOWN":
        return "UNKNOWN"
    return "PASS"


def combine(checks: list[Status]) -> Status:
    """Fold a verifier envelope; denial wins, UNKNOWN never promotes."""
    result: Status = "PASS"
    for check in checks:
        result = dominate(result, check)
    return result


def is_decided(status: Status) -> bool:
    """Mirror of ``mncs.harness.verdict.v1::is_decided``."""
    return status in ("PASS", "FAIL")


def fold_pair(left: Grant, right: Grant) -> Grant:
    """Mirror of ``mncs.harness.atlas.v1::fold_pair``; denial wins."""
    if left == "REFUSED" or right == "REFUSED":
        return "REFUSED"
    if left == "UNKNOWN" or right == "UNKNOWN":
        return "UNKNOWN"
    return "GRANTED"


def fold(decisions: list[Grant]) -> Grant:
    """Order-independent conservative fold of duplicate capability decisions."""
    result: Grant = "GRANTED"
    for decision in decisions:
        result = fold_pair(result, decision)
    return result


def tool_admission(granted: bool, covered: bool) -> Grant:
    """Mirror of ``mncs.harness.atlas.v1::tool_admission``."""
    if not granted or not covered:
        return "REFUSED"
    return "GRANTED"


def dispatch_gate(folded: Grant, target_matches: bool) -> Grant:
    """Mirror of ``mncs.harness.atlas.v1::dispatch_gate``."""
    if target_matches:
        return folded
    if folded == "GRANTED":
        return "REFUSED"
    return folded


def corpus_cases(name: str) -> list[dict]:
    """Load an executable MNCS corpus (e.g. ``harness-routing``)."""
    path = CORPORA_DIR / f"{name}-corpus.json"
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


_PIN_SHAPE = {
    "AUTO": (False, False, False),
    "ROLE": (True, False, False),
    "MODEL": (False, False, True),
    "WORKER": (False, True, False),
    "WORKER_MODEL": (False, True, True),
    "WORKER_MODEL_ROLE": (True, True, True),
}


def pin_fields_valid(mode: str, has_role: bool, has_worker: bool, has_model: bool) -> bool:
    """Mirror of ``mncs.harness.pins.v1::pin_fields_valid`` (shape table only)."""
    return _PIN_SHAPE.get(mode) == (has_role, has_worker, has_model)


def admit_placement(auto_or_role: bool, selection_present: bool, allow_fallback: bool) -> str:
    """Mirror of ``mncs.harness.pins.v1::admit_placement``."""
    if auto_or_role:
        return "AUTO_ALLOWED"
    if selection_present:
        return "PIN_HONORED"
    if allow_fallback:
        return "FALLBACK_ALLOWED"
    return "PIN_FAILED_CLOSED"


def is_exact_pin(mode: str) -> bool:
    """Mirror of ``mncs.harness.pins.v1::is_exact_pin``."""
    return mode in ("MODEL", "WORKER", "WORKER_MODEL", "WORKER_MODEL_ROLE")


def publication_gate(configured: bool, session_ready: bool, record_present: bool) -> str:
    """Mirror of ``mncs.harness.policy.v1::publication_gate`` (ready-first)."""
    if not session_ready:
        return "SKIP_NOT_READY"
    if not configured:
        return "SKIP_DISABLED"
    if not record_present:
        return "SKIP_NO_RECORD"
    return "PROCEED"


def capability_source(
    available: bool,
    inventory_current: bool,
    observation_present: bool,
    legacy_present: bool,
) -> str:
    """Mirror of ``mncs.harness.eligibility.v1::capability_source``."""
    if available and inventory_current and observation_present:
        return "OBSERVATION"
    if inventory_current and legacy_present:
        return "LEGACY"
    return "NONE"


def classify_fabric(
    missing_any: bool,
    parseable: bool,
    too_old: bool,
    exact: bool,
    version_is_certified: bool,
) -> str:
    """Mirror of ``mncs.harness.fabric.v1::classify_fabric``."""
    if missing_any:
        return "INCOMPATIBLE"
    if not parseable:
        return "UNKNOWN"
    if too_old:
        return "TOO_OLD"
    if exact:
        return "EXPERIMENT_CERTIFIED_EXACT"
    if version_is_certified:
        return "COMPATIBLE_VERSION_ONLY"
    return "COMPATIBLE_NEWER"


def fabric_dispatch_allowed(classification: str) -> bool:
    """Mirror of ``mncs.harness.fabric.v1::dispatch_allowed``."""
    return classification in (
        "EXPERIMENT_CERTIFIED_EXACT",
        "COMPATIBLE_VERSION_ONLY",
        "COMPATIBLE_NEWER",
    )


def dominate_readiness(left: str, right: str) -> str:
    """Mirror of ``mncs.harness.readiness.v1::dominate_readiness``."""
    order = {"BLOCKED": 3, "UNKNOWN": 2, "DEGRADED": 1, "READY": 0}
    if left not in order or right not in order:
        raise ValueError(f"bad readiness value {left!r}/{right!r}")
    return left if order[left] >= order[right] else right


def fold_readiness(layers: list[str]) -> str:
    """Mirror of the generic ``fold_readiness`` over a layer envelope."""
    result = "READY"
    for layer in layers:
        result = dominate_readiness(result, layer)
    return result


def readiness_ready(state: str) -> bool:
    """Mirror of ``mncs.harness.readiness.v1::is_ready``."""
    return state == "READY"


def capability_eligible(
    capability: str,
    observed: str,
    claimed: bool,
    unknown_policy: str,
    require_observed: bool,
    allow_unclaimed: bool,
) -> bool:
    """Mirror of ``mncs.harness.eligibility.v1::capability_eligible``."""
    if observed == "FAIL":
        return False
    if capability == "CODE_EDIT":
        return True
    if observed == "PASS":
        return True
    if claimed:
        return not require_observed if capability == "TOOLS" else True
    if capability == "COMPLETION":
        return not (unknown_policy == "FAIL_CLOSED" and require_observed)
    if unknown_policy == "EXPLORE":
        return True
    if unknown_policy == "PROVIDER_CLAIM_COMPAT":
        return allow_unclaimed
    return False


def residency_admit(has_conflicts: bool, reject_conflicting: bool, already_loaded: bool) -> bool:
    """Mirror of ``mncs.harness.eligibility.v1::residency_admit``."""
    if has_conflicts and reject_conflicting and not already_loaded:
        return False
    return True


def resource_gate(
    facts_complete: bool,
    size: int,
    budget: int,
    has_available: bool,
    available: int,
) -> str:
    """Mirror of ``mncs.harness.eligibility.v1::resource_gate``."""
    if not facts_complete:
        return "MISSING_FACTS"
    if size > budget:
        return "OVER_BUDGET"
    if has_available and size > available:
        return "OVER_AVAILABLE"
    return "OK"


def observation_fresh(stored_ms: int, now_ms: int, max_age_ms: int) -> bool:
    """Mirror of ``mncs.harness.freshness.v1::observation_fresh``."""
    if now_ms >= stored_ms:
        return now_ms - stored_ms <= max_age_ms
    return True


def attestation_window_ok(issued_at: int, now: int, max_age: int) -> bool:
    """Mirror of ``mncs.harness.freshness.v1::attestation_window_ok``."""
    if issued_at > now:
        return False
    return now - issued_at <= max_age
