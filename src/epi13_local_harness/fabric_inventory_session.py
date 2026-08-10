"""Fabric session extension with worker-local Ollama inventory discovery.

Unlike the operator SSH inventory command, this runtime path uses Fabric itself
to execute a tiny Python probe on the already-enrolled worker. The probe only
queries the worker loopback Ollama API. This keeps SSH out of inference/runtime
routing and lets the semantic router choose among models that are actually
installed on the worker.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from .fabric import FabricExecutionError, FabricSession, FabricStatus
from .model_selection import ModelSelection, select_installed_model
from .models import FabricConfig, ModelConfig

_INVENTORY_PREFIX = "ELH_FABRIC_MODEL_INVENTORY "


def _identity(value: object) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _execution_failure(result: dict[str, Any], record: dict[str, Any], fallback: str) -> str:
    reason = result.get("reason") or record.get("termination_reason") or fallback
    detail = record.get("detail")
    if detail and str(detail) != str(reason):
        return f"{reason}: {detail}"
    return str(reason)


def _inventory_script() -> str:
    return '''from __future__ import annotations

import json
import urllib.error
import urllib.request

url = "http://127.0.0.1:11434/api/tags"
try:
    with urllib.request.urlopen(url, timeout=10) as response:
        value = json.loads(response.read().decode("utf-8"))
except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
    raise SystemExit("worker-local Ollama inventory failed: " + str(exc)) from exc
models = value.get("models", [])
if not isinstance(models, list):
    raise SystemExit("worker-local Ollama inventory returned a non-list models field")
print("ELH_FABRIC_MODEL_INVENTORY " + json.dumps(models, separators=(",", ":"), ensure_ascii=True))
'''


class InventoryAwareFabricSession(FabricSession):
    """Fabric session that tracks the live model inventory of remote workers."""

    def __init__(self, config: FabricConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self.model_inventories: dict[str, tuple[dict[str, Any], ...]] = {}
        self.model_inventory_errors: dict[str, str] = {}

    def initialize(self) -> None:
        super().initialize()
        if self.enabled and self.client is not None:
            self._refresh_model_inventories()

    def refresh(self) -> FabricStatus:
        super().refresh()
        if self.enabled and self.client is not None:
            self._refresh_model_inventories()
        return self.status()

    def _probe_model_inventory(self, worker_id: str) -> tuple[dict[str, Any], ...]:
        if self.client is None:
            raise FabricExecutionError("Fabric client is unavailable")
        self.config.state_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="elh-fabric-model-inventory-", dir=self.config.state_path.parent
        ) as directory:
            source_root = Path(directory)
            (source_root / "inventory.py").write_text(_inventory_script(), encoding="utf-8")
            from mncs_fabric.artifacts import build_manifest
            from mncs_fabric.bundles import build_bundle_archive
            from mncs_fabric.models import validate_job_plan

            manifest = build_manifest(source_root)
            archive = source_root / "execution-bundle.zip"
            build_bundle_archive(source_root, archive)
            plan = validate_job_plan(
                {
                    "schema_version": "mncs-fabric.job-plan.v0.1",
                    "job_id": "elh-worker-model-inventory",
                    "candidate_identity": _identity(
                        {"worker_id": worker_id, "probe": "ollama-tags-v1"}
                    ),
                    "evaluator_identity": None,
                    "artifact_manifest_identity": manifest["manifest_identity"],
                    "argv": ["@python", "inventory.py"],
                    "working_directory": ".",
                    "timeout_seconds": 20,
                    "output_limit_bytes": 512 * 1024,
                    "environment": {"PYTHONHASHSEED": "0"},
                    "required_capabilities": ["python"],
                    "result_paths": [],
                    "network_policy": "UNRESTRICTED",
                }
            )
            result = self.client.execute(
                plan,
                manifest,
                worker_id=worker_id,
                execution_bundle_archive=archive,
            )[0]
        record = result.get("record") or {}
        if result.get("disposition") != "EXECUTED" or record.get("outcome") != "PASS":
            reason = _execution_failure(result, record, "inventory probe failed")
            raise FabricExecutionError(f"model inventory failed on {worker_id}: {reason}")
        stdout = ((record.get("stdout") or {}).get("captured_utf8") or "").splitlines()
        response_line = next((line for line in stdout if line.startswith(_INVENTORY_PREFIX)), None)
        if response_line is None:
            raise FabricExecutionError(f"model inventory returned no result on {worker_id}")
        try:
            payload = json.loads(response_line.removeprefix(_INVENTORY_PREFIX))
        except json.JSONDecodeError as exc:
            raise FabricExecutionError(
                f"model inventory returned invalid JSON on {worker_id}"
            ) from exc
        if not isinstance(payload, list):
            raise FabricExecutionError(f"model inventory returned a non-list on {worker_id}")
        return tuple(dict(item) for item in payload if isinstance(item, dict))

    def _refresh_model_inventories(self) -> None:
        if self.client is None:
            return
        try:
            workers = [dict(worker) for worker in self.client.workers()]
        except Exception as exc:
            self.model_inventory_errors["*"] = str(exc)
            return
        for worker in workers:
            worker_id = str(worker.get("worker_id") or "")
            if (
                not worker_id
                or worker.get("source") != "remote"
                or worker.get("availability") != "AVAILABLE"
            ):
                continue
            try:
                self.model_inventories[worker_id] = self._probe_model_inventory(worker_id)
                self.model_inventory_errors.pop(worker_id, None)
            except Exception as exc:
                self.model_inventory_errors[worker_id] = str(exc)

    def _common_remote_inventory(self) -> tuple[dict[str, Any], ...]:
        base = super().status()
        available_remote = [
            str(worker.get("worker_id"))
            for worker in base.workers
            if worker.get("source") == "remote" and worker.get("availability") == "AVAILABLE"
        ]
        if not available_remote or any(worker_id not in self.model_inventories for worker_id in available_remote):
            return ()
        common_names: set[str] | None = None
        metadata: dict[str, dict[str, Any]] = {}
        for worker_id in available_remote:
            inventory = self.model_inventories[worker_id]
            names = {
                str(item.get("name") or item.get("model") or "")
                for item in inventory
                if item.get("name") or item.get("model")
            }
            common_names = names if common_names is None else common_names & names
            for item in inventory:
                name = str(item.get("name") or item.get("model") or "")
                if name and name not in metadata:
                    metadata[name] = dict(item)
        return tuple(metadata[name] for name in sorted(common_names or ()))

    def resolve_model(
        self,
        role: str,
        model: ModelConfig,
    ) -> tuple[ModelConfig, ModelSelection | None]:
        """Select a worker-installed model for one Fabric-backed role.

        The Fabric execution bundle only performs a loopback HTTP request to
        worker-local Ollama. Therefore the bundle itself is a CPU job. Ollama,
        not Fabric's Python runner, owns model loading and GPU/CPU placement.
        """

        if model.provider != "fabric":
            return model, None
        selection = select_installed_model(role, model.name, self._common_remote_inventory())
        selected_name = selection.selected_model if selection is not None else model.name
        selected_size = (
            selection.stored_size_bytes
            if selection is not None and selection.stored_size_bytes > 0
            else model.model_storage_bytes
        )
        effective = replace(
            model,
            name=selected_name,
            model_storage_bytes=selected_size,
            execution_device="cpu",
            accelerator_backend=None,
        )
        return effective, selection

    def _placement(self, model: ModelConfig) -> Any:
        """Place only the lightweight provider-call bundle, not the Ollama model."""

        from mncs_fabric.api import PlacementRequest

        return PlacementRequest(
            execution_device="cpu",
            required_capabilities=model.required_capabilities,
            resource_max_age_seconds=model.resource_max_age_seconds,
        )

    def status(self) -> FabricStatus:
        base = super().status()
        workers: list[dict[str, Any]] = []
        for worker in base.workers:
            item = dict(worker)
            worker_id = str(item.get("worker_id") or "")
            inventory = self.model_inventories.get(worker_id)
            if inventory is not None:
                item["model_inventory"] = [dict(model) for model in inventory]
                item["model_names"] = sorted(
                    {
                        str(model.get("name") or model.get("model"))
                        for model in inventory
                        if model.get("name") or model.get("model")
                    }
                )
            if worker_id in self.model_inventory_errors:
                item["model_inventory_error"] = self.model_inventory_errors[worker_id]
            workers.append(item)
        details = [base.detail] if base.detail else []
        for worker_id, error in sorted(self.model_inventory_errors.items()):
            details.append(f"{worker_id}: model inventory failed: {error}")
        return FabricStatus(
            enabled=base.enabled,
            state=base.state,
            controller_id=base.controller_id,
            workers=tuple(workers),
            detail="; ".join(dict.fromkeys(details)) if details else None,
            last_inference=base.last_inference,
        )
