"""Compatibility guards for the installed MNCS Fabric consumer API."""

from __future__ import annotations

import inspect
import pathlib
from typing import Any, Mapping

from packaging.version import InvalidVersion, Version

# Commissioning floor: capability observations + transferred-bundle dispatch.
REQUIRED_FABRIC_VERSION = "0.2.0a15"
# Experiment-stack floor: persistent service consumer contract used live.
MIN_SUPPORTED_FABRIC_VERSION = "0.2.0a17"
MIN_SUPPORTED_FABRIC_COMMIT = "6285f7d3f49994e926aa0468a6cc2b644f9a3e85"
# Exact Fabric revision the experiment stack is certified against.
# Live-proven on controller + fabric-worker-01 + collamore02-windows.
EXPERIMENT_CERTIFIED_FABRIC_VERSION = "0.2.0a30"
EXPERIMENT_CERTIFIED_FABRIC_COMMIT = "02fea5b5571e3b43a532d904f56468f99c75e482"
EXPERIMENT_CERTIFIED_FABRIC_ARTIFACT_DIGEST = (
    "sha256:188d6b6a64d215871147c157b60a5d066776505b1c7d5d6d52434de45db9c940"
)
# Forward-compatibility canary only; never the sole experiment guarantee.
FABRIC_MAIN_CANARY_REF = "main"

_REQUIRED_EXECUTE_PARAMETER = "execution_bundle_archive"
_REQUIRED_CAPABILITY_METHODS = (
    "ingest_capability_observation",
    "load_registry",
    "workers",
)
EXPERIMENT_REQUIRED_CAPABILITIES = (
    "persistent_fleet_read",
    "persistent_fleet_refresh",
    "classified_fleet_refresh",
    "persistent_service_execution",
    "persistent_detached_execution",
    "scheduled_work_queue",
)
def require_execution_bundle_archive_api() -> dict[str, object]:
    """Fail before commissioning if the active Fabric package lacks the required behavior.

    The local harness stages the *currently imported* ``mncs_fabric`` package onto
    remote workers. Checking both version and callable surface is therefore more
    reliable than trusting packaging metadata alone, especially for editable installs.
    """

    try:
        import mncs_fabric
        from mncs_fabric import FabricClient
    except ImportError as exc:
        raise RuntimeError(
            "mncs-fabric is required for Fabric commissioning; install the harness "
            "Fabric extra or an editable mncs-fabric checkout first"
        ) from exc

    version = str(getattr(mncs_fabric, "__version__", "unknown"))
    module_path = pathlib.Path(mncs_fabric.__file__).resolve()
    parameters = inspect.signature(FabricClient.execute).parameters
    try:
        version_too_old = Version(version) < Version(REQUIRED_FABRIC_VERSION)
    except InvalidVersion:
        version_too_old = True
    missing_methods = [
        name for name in _REQUIRED_CAPABILITY_METHODS if not callable(getattr(FabricClient, name, None))
    ]
    if _REQUIRED_EXECUTE_PARAMETER not in parameters or missing_methods or version_too_old:
        raise RuntimeError(
            "incompatible mncs-fabric consumer runtime: "
            f"loaded version {version} from {module_path}; MNCS Harness requires "
            f"mncs-fabric >= {REQUIRED_FABRIC_VERSION} with worker capability observations, "
            "canonical transferred-bundle dispatch binding, published-cache recovery, and "
            f"FabricClient.execute(..., {_REQUIRED_EXECUTE_PARAMETER}=...) and "
            f"capability methods {', '.join(_REQUIRED_CAPABILITY_METHODS)}"
        )
    return {
        "version": version,
        "module_path": str(module_path),
        "required_version": REQUIRED_FABRIC_VERSION,
        "required_execute_parameter": _REQUIRED_EXECUTE_PARAMETER,
        "required_capability_methods": _REQUIRED_CAPABILITY_METHODS,
    }


def fabric_compatibility_pins() -> dict[str, str]:
    """Declare the three Fabric compatibility identities as separate concepts."""

    return {
        "minimum_supported_version": MIN_SUPPORTED_FABRIC_VERSION,
        "minimum_supported_commit": MIN_SUPPORTED_FABRIC_COMMIT,
        "experiment_certified_version": EXPERIMENT_CERTIFIED_FABRIC_VERSION,
        "experiment_certified_commit": EXPERIMENT_CERTIFIED_FABRIC_COMMIT,
        "experiment_certified_artifact_digest": EXPERIMENT_CERTIFIED_FABRIC_ARTIFACT_DIGEST,
        "forward_compatibility_ref": FABRIC_MAIN_CANARY_REF,
    }


def evaluate_experiment_fabric(
    version: str,
    capabilities: Mapping[str, Any] | None = None,
    *,
    commit: str | None = None,
    artifact_digest: str | None = None,
    certified_artifact_digest: str | None = None,
) -> dict[str, Any]:
    """Classify a Fabric runtime for experiment use.

    Version metadata cannot override a missing required capability. Exact
    experiment certification requires an immutable identity (source commit or
    package artifact digest). A matching version string alone is
    COMPATIBLE_VERSION_ONLY.
    """

    advertised = dict(capabilities or {})
    missing = [
        name
        for name in EXPERIMENT_REQUIRED_CAPABILITIES
        if advertised.get(name) is not True
    ]
    try:
        parsed = Version(version)
        too_old = parsed < Version(MIN_SUPPORTED_FABRIC_VERSION)
        parseable = True
    except InvalidVersion:
        parsed = None
        too_old = True
        parseable = False

    if certified_artifact_digest is None:
        certified_artifact_digest = EXPERIMENT_CERTIFIED_FABRIC_ARTIFACT_DIGEST
    certified_commit = bool(
        commit and commit.lower() == EXPERIMENT_CERTIFIED_FABRIC_COMMIT.lower()
    )
    certified_digest = bool(
        artifact_digest
        and certified_artifact_digest
        and artifact_digest.lower() == certified_artifact_digest.lower()
    )
    exact = certified_commit or certified_digest
    if missing:
        classification = "INCOMPATIBLE"
        action = "dispatch_blocked"
        reason = "required persistent-service capabilities are missing"
    elif not parseable:
        classification = "UNKNOWN"
        action = "dispatch_blocked"
        reason = "Fabric version is not a parseable PEP 440 identifier"
    elif too_old:
        classification = "TOO_OLD"
        action = "dispatch_blocked"
        reason = (
            f"Fabric {version} is older than minimum supported "
            f"{MIN_SUPPORTED_FABRIC_VERSION}"
        )
    elif exact:
        classification = "EXPERIMENT_CERTIFIED_EXACT"
        action = "dispatch_allowed"
        reason = "Fabric matches the experiment-certified immutable identity"
    elif version == EXPERIMENT_CERTIFIED_FABRIC_VERSION:
        classification = "COMPATIBLE_VERSION_ONLY"
        action = "dispatch_allowed"
        reason = (
            "Fabric version matches the certified revision but no source "
            "commit or artifact digest is bound"
        )
    else:
        classification = "COMPATIBLE_NEWER"
        action = "dispatch_allowed"
        reason = (
            f"Fabric {version} is newer than the certified revision and "
            "advertises the required capabilities"
        )
    return {
        "classification": classification,
        "action": action,
        "reason": reason,
        "version": version,
        "commit": commit,
        "artifact_digest": artifact_digest,
        "exact": exact,
        "missing_capabilities": missing,
        "required_capabilities": list(EXPERIMENT_REQUIRED_CAPABILITIES),
        **fabric_compatibility_pins(),
    }
