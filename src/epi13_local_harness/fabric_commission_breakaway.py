"""Windows commissioning hot path using a native breakaway launcher.

The underlying commissioning workflow remains in :mod:`fabric_commission`.
This module replaces only the Windows process-start primitive so the persistent
worker is not owned by the provisioning OpenSSH job object.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from . import fabric_commission as _base
from .fabric_compat import require_execution_bundle_archive_api

add_windows_commission_arguments = _base.add_windows_commission_arguments


def _launcher_source() -> Path:
    path = Path(__file__).with_name("windows_worker_launcher.py")
    if not path.is_file():
        raise _base.CommissioningError(f"Windows worker launcher is missing: {path}")
    return path


def _launcher_start_script(
    *,
    remote_root: str,
    python: str,
    worker_id: str,
    controller_id: str,
    port: int,
) -> str:
    root = _base._ps_quote(remote_root)
    return (
        f"$root={root};$python={_base._ps_quote(python)};"
        "$launcher=\"$root\\launcher\\windows_worker_launcher.py\";"
        "$env:PYTHONPATH=\"$root\\src\";"
        "$launcherArgs=@('start',"
        "'--state',\"$root\\state\\launcher.json\","
        f"'--worker-id',{_base._ps_quote(worker_id)},"
        f"'--controller-id',{_base._ps_quote(controller_id)},"
        "'--stdout',\"$root\\logs\\worker.stdout.log\","
        "'--stderr',\"$root\\logs\\worker.stderr.log\","
        "'--cwd',$root,'--',$python,'-m','mncs_fabric','worker','serve',"
        f"'--worker-id',{_base._ps_quote(worker_id)},"
        f"'--controller-id',{_base._ps_quote(controller_id)},"
        "'--bundle-root',\"$root\\empty-bundle\","
        "'--state',\"$root\\state\\worker-ledger.jsonl\","
        "'--trust-state',\"$root\\trust\\worker-trust.jsonl\","
        "'--ca',\"$root\\certs\\ca.pem\","
        "'--certificate',\"$root\\certs\\worker.pem\","
        "'--key',\"$root\\certs\\worker.key\","
        "'--host','0.0.0.0','--port',"
        f"'{port}','--timeout','30','--max-requests','1000000',"
        "'--max-concurrent-connections','1','--bundle-cache',"
        "\"$root\\bundle-cache\");"
        "& $python $launcher @launcherArgs;"
        "$code=$LASTEXITCODE;"
        "if($code -ne 0){exit $code}"
    )


def _stage_launcher(
    *,
    host: str,
    user: str,
    key: Path,
    remote_root: str,
) -> None:
    root = _base._ps_quote(remote_root)
    result = _base._run_powershell(
        host=host,
        user=user,
        key=key,
        script=(
            f"$root={root};"
            "New-Item -ItemType Directory -Force -Path \"$root\\launcher\" | Out-Null;"
            "@{outcome='PASS'}|ConvertTo-Json -Compress"
        ),
    )
    _base._powershell_json(result, "prepare Windows breakaway launcher")
    remote = _base._windows_scp_path(remote_root)
    _base._scp_file(
        host=host,
        user=user,
        key=key,
        source=_launcher_source(),
        destination=f"{remote}/launcher/windows_worker_launcher.py",
    )


def _start_remote_worker(
    *,
    host: str,
    user: str,
    key: Path,
    remote_root: str,
    python: str,
    worker_id: str,
    controller_id: str,
    port: int,
) -> dict[str, Any]:
    _stage_launcher(host=host, user=user, key=key, remote_root=remote_root)
    result = _base._run_powershell(
        host=host,
        user=user,
        key=key,
        script=_launcher_start_script(
            remote_root=remote_root,
            python=python,
            worker_id=worker_id,
            controller_id=controller_id,
            port=port,
        ),
        timeout=30,
    )
    value = _base._powershell_json(result, "start breakaway persistent Fabric worker")
    if value.get("outcome") != "PASS" or not isinstance(value.get("pid"), int):
        raise _base.CommissioningError(
            f"Windows breakaway launcher did not start the worker: {value}"
        )
    if not isinstance(value.get("process_token"), str) or not value["process_token"]:
        raise _base.CommissioningError("Windows breakaway launcher returned no process token")
    return value


def commission_windows(args: argparse.Namespace) -> int:
    """Run normal commissioning with compatibility and OpenSSH process guards."""
    require_execution_bundle_archive_api()
    original = _base._start_remote_worker
    _base._start_remote_worker = _start_remote_worker
    try:
        return _base.commission_windows(args)
    finally:
        _base._start_remote_worker = original
