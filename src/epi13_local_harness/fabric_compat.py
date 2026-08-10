"""Compatibility guards for the installed MNCS Fabric consumer API."""

from __future__ import annotations

import inspect
import pathlib

REQUIRED_FABRIC_VERSION = "0.2.0a8"
_REQUIRED_EXECUTE_PARAMETER = "execution_bundle_archive"


def require_execution_bundle_archive_api() -> dict[str, object]:
    """Fail before commissioning if the active Fabric package lacks the required API.

    The local harness stages the *currently imported* ``mncs_fabric`` package onto
    remote workers. Checking the actual callable surface is therefore more reliable
    than trusting packaging metadata alone, especially for editable installs.
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
    if _REQUIRED_EXECUTE_PARAMETER not in parameters:
        raise RuntimeError(
            "incompatible mncs-fabric consumer API: "
            f"loaded version {version} from {module_path} does not expose "
            f"FabricClient.execute(..., {_REQUIRED_EXECUTE_PARAMETER}=...); "
            f"update/reinstall mncs-fabric to >= {REQUIRED_FABRIC_VERSION} before commissioning"
        )
    return {
        "version": version,
        "module_path": str(module_path),
        "required_version": REQUIRED_FABRIC_VERSION,
        "required_execute_parameter": _REQUIRED_EXECUTE_PARAMETER,
    }
