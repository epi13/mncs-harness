"""Compatibility guards for the installed MNCS Fabric consumer API."""

from __future__ import annotations

import inspect
import pathlib

from packaging.version import InvalidVersion, Version

REQUIRED_FABRIC_VERSION = "0.2.0a15"
_REQUIRED_EXECUTE_PARAMETER = "execution_bundle_archive"
_REQUIRED_CAPABILITY_METHODS = (
    "ingest_capability_observation",
    "load_registry",
    "workers",
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
