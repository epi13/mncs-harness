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
EXPERIMENT_CERTIFIED_FABRIC_VERSION = "0.2.0a28"
EXPERIMENT_CERTIFIED_FABRIC_COMMIT = "4f657c4d0441073902ebcbae823c11af43c09535"
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
        "forward_compatibility_ref": FABRIC_MAIN_CANARY_REF,
    }


def evaluate_experiment_fabric(
    version: str,
    capabilities: Mapping[str, Any] | None = None,
    *,
    commit: str | None = None,
) -> dict[str, Any]:
    """Classify a Fabric runtime for experiment use.

    Version metadata cannot override a missing required capability. A commit
    match to the certified revision is stronger than a version string alone.
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

    certified_commit = bool(
        commit and commit.lower() == EXPERIMENT_CERTIFIED_FABRIC_COMMIT.lower()
    )
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
    elif certified_commit or version == EXPERIMENT_CERTIFIED_FABRIC_VERSION:
        classification = "EXPERIMENT_CERTIFIED"
        action = "dispatch_allowed"
        reason = "Fabric matches the experiment-certified revision"
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
        "missing_capabilities": missing,
        "required_capabilities": list(EXPERIMENT_REQUIRED_CAPABILITIES),
        **fabric_compatibility_pins(),
    }
