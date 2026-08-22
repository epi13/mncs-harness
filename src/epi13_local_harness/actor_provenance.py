"""Exact Harness actor/route provenance for Concept Experiments."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

ACTOR_PROVENANCE_SCHEMA = "mncs-harness.actor-provenance.v0.1"
BOOTSTRAP_EXPERIMENT_ROLES = frozenset(
    {
        "experimenter",
        "builder",
        "experiment-investigator",
        "adaptive-experiment-critic",
        "reviewer",
        "skeptic",
    }
)


def _text(value: object, field: str, maximum: int = 2048) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError(f"{field} must be bounded non-empty text")
    return value.strip()


def _identity(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def build_actor_provenance(
    *,
    role: str,
    model_identity: str,
    provider_identity: str,
    worker_identity: str,
    route_identity: str,
    tool_exposure: Iterable[str],
    policy_profile: str,
    prompt_digest: str,
    session_identity: str,
    observed_at: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an identity-addressed Harness record; a role never changes its producer."""

    from . import __version__ as harness_version

    role = _text(role, "role", 128)
    tools = sorted({_text(item, "tool_exposure[]", 256) for item in tool_exposure})
    material: dict[str, Any] = {
        "schema_version": ACTOR_PROVENANCE_SCHEMA,
        "producer": "mncs-harness",
        "role": role,
        "model_identity": _text(model_identity, "model_identity"),
        "provider_identity": _text(provider_identity, "provider_identity"),
        "worker_identity": _text(worker_identity, "worker_identity"),
        "harness_version": harness_version,
        "route_identity": _text(route_identity, "route_identity"),
        "tool_exposure": tools,
        "policy_profile": _text(policy_profile, "policy_profile"),
        "prompt_digest": _text(prompt_digest, "prompt_digest"),
        "session_identity": _text(session_identity, "session_identity"),
        "identity_boundary": (
            "Harness role only; this producer is never RAVEL or MNEL unless such a producer "
            "actually emitted a separate native record"
        ),
    }
    if extra:
        material["extra"] = dict(extra)
    digest = _identity(material)
    return {
        **material,
        "stable_id": f"mncs-harness://actor-route/{digest[7:]}",
        "content_digest": digest,
        "observed_at": _text(observed_at, "observed_at", 128),
        "bootstrap_role": role in BOOTSTRAP_EXPERIMENT_ROLES,
    }
