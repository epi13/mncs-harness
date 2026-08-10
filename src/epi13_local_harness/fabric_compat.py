"""Compatibility guards for the installed MNCS Fabric consumer API."""

from __future__ import annotations

import inspect
import pathlib
import re

REQUIRED_FABRIC_VERSION = "0.2.0a9"
_REQUIRED_EXECUTE_PARAMETER = "execution_bundle_archive"
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:a(\d+))?")


def _version_key(value: str) -> tuple[int, int, int, int] | None:
    match = _VERSION_RE.match(value)
    if match is None:
        return None
    major, minor, patch, alpha = match.groups()
    prerelease = int(alpha) if alpha is not None else 1_000_000_000
    return int(major), int(minor), int(patch), prerelease


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
    loaded_key = _version_key(version)
    required_key = _version_key(REQUIRED_FABRIC_VERSION)
    version_too_old = (
        loaded_key is None or required_key is None or loaded_key < required_key
    )
    if _REQUIRED_EXECUTE_PARAMETER not in parameters or version_too_old:
        raise RuntimeError(
            "incompatible mncs-fabric consumer runtime: "
            f"loaded version {version} from {module_path}; Local Harness requires "
            f"mncs-fabric >= {REQUIRED_FABRIC_VERSION} with canonical transferred-bundle dispatch "
            f"binding and FabricClient.execute(..., {_REQUIRED_EXECUTE_PARAMETER}=...)"
        )
    return {
        "version": version,
        "module_path": str(module_path),
        "required_version": REQUIRED_FABRIC_VERSION,
        "required_execute_parameter": _REQUIRED_EXECUTE_PARAMETER,
    }
