"""Capability vocabulary for model-agnostic selection.

Model identity is data, not policy. This module distinguishes:

- provider-reported capabilities (claims from Ollama or another runtime)
- MNCS-observed capabilities (evidence from bounded verification)
- policy preferences (operator/system ranking choices)

It does not infer capability from model name, family, or brand substrings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ProviderClaim = Literal["completion", "tools", "vision", "embedding", "thinking"]
ObservedCapability = Literal[
    "reachability",
    "marker_response",
    "structured_output",
    "tool_call",
    "file_read",
    "file_write",
    "code_edit",
    "command_invocation",
]
Freshness = Literal["CURRENT", "STALE", "UNKNOWN", "UNAVAILABLE"]
UnknownPolicy = Literal["explore", "provider-claim-compat", "fail-closed"]


@dataclass(frozen=True)
class RoleRequirements:
    """What a role needs. Roles are policy labels, not model families."""

    role: str
    needs_completion: bool = True
    needs_tools: bool = False
    needs_mutation: bool = False
    needs_code_edit: bool = False
    prefer_small: bool = False
    prefer_large: bool = False
    unknown_policy: UnknownPolicy = "fail-closed"


ROLE_REQUIREMENTS: dict[str, RoleRequirements] = {
    "e2b": RoleRequirements(
        "e2b",
        needs_tools=False,
        needs_mutation=False,
        prefer_small=True,
        unknown_policy="explore",
    ),
    "e4b": RoleRequirements(
        "e4b",
        needs_tools=True,
        needs_mutation=True,
        unknown_policy="provider-claim-compat",
    ),
    "coder": RoleRequirements(
        "coder",
        needs_tools=True,
        needs_mutation=True,
        needs_code_edit=True,
        unknown_policy="provider-claim-compat",
    ),
    "reviewer": RoleRequirements(
        "reviewer",
        needs_tools=True,
        needs_mutation=True,
        prefer_large=True,
        unknown_policy="provider-claim-compat",
    ),
}


@dataclass(frozen=True)
class SelectionPolicy:
    """Operator/system ranking preferences. Not capabilities."""

    prefer_resident: bool = True
    max_coder_bytes: int = 10 * 1024 * 1024 * 1024
    require_observed_for_mutation: bool = False
    allow_size_policy_without_claims: bool = True


def requirements_for(role: str) -> RoleRequirements:
    return ROLE_REQUIREMENTS.get(
        role,
        RoleRequirements(role, unknown_policy="provider-claim-compat"),
    )


def provider_claims(item: dict[str, object]) -> frozenset[str]:
    raw = item.get("capabilities")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(value) for value in raw if isinstance(value, str) and value)


def model_name(item: dict[str, object]) -> str:
    return str(item.get("name") or item.get("model") or "").strip()


def stored_size(item: dict[str, object]) -> int:
    value = item.get("size")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0
