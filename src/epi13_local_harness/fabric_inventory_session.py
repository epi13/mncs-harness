"""Fabric session extension with worker-local Ollama inventory discovery.

Unlike the operator SSH inventory command, this runtime path uses Fabric itself
to execute a tiny Python probe on the already-enrolled worker. The probe only
queries the worker loopback Ollama API. This keeps SSH out of inference/runtime
routing and lets the semantic router choose among models that are actually
installed on the worker.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from .fabric import FabricExecutionError, FabricSession, FabricStatus
from .model_selection import ModelSelection, select_installed_model
from .models import FabricConfig, ModelConfig

_INVENTORY_PREFIX = "ELH_FABRIC_MODEL_INVENTORY "
_SUCCESS_DISPOSITIONS = {"EXECUTED", "DUPLICATE_IDEMPOTENT"}


def _identity(value: object) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _fresh_request_id(prefix: str) -> str:
    """Return a bounded request id for work that must execute again now.

    Fabric's default request identity is deliberately deterministic so retries
    can be replay-safe. Inventory scans, runtime observations, and provider
    invocations are different: they represent a new observation or user attempt
    and must not be satisfied forever by an older idempotent result.
    """

    return f"{prefix}:{uuid.uuid4().hex}"


def _execution_failure(result: dict[str, Any], record: dict[str, Any], fallback: str) -> str:
    reason = result.get("reason") or record.get("termination_reason") or fallback
    detail = record.get("detail")
    if detail and str(detail) != str(reason):
        return f"{reason}: {detail}"
    return str(reason)


def _execution_succeeded(result: dict[str, Any], record: dict[str, Any]) -> bool:
    return result.get("disposition") in _SUCCESS_DISPOSITIONS and record.get("outcome") == "PASS"


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


class _FreshDispatchClient:
    """Delegate Fabric calls while making observation/inference dispatches fresh.

    The underlying Fabric API keeps deterministic request identities by default.
    This wrapper uses the public ``request_id`` seam so Local Harness does not
    replay an old model inventory or an old failed inference simply because the
    semantic payload is identical to a prior attempt.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        if "request_id" not in kwargs:
            kwargs["request_id"] = _fresh_request_id("elh-dispatch")
        return self._client.execute(*args, **kwargs)


def _supports_request_id(client: Any) -> bool:
    execute = getattr(client, "execute", None)
    if not callable(execute):
        return False
    try:
        parameters = inspect.signature(execute).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "request_id" or parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _model_capability_entries(models: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = [
        {
            "kind": "runtime",
            "namespace": "ollama",
            "name": "ollama",
            "attributes": {"interface": "loopback-http-api"},
        }
    ]
    for model in models:
        name = str(model.get("name") or model.get("model") or "").strip()
        if not name:
            continue
        details = model.get("details") if isinstance(model.get("details"), dict) else {}
        attributes: dict[str, Any] = {}
        size = model.get("size")
        if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
            attributes["size_bytes"] = size
        factual_fields = {
            "modified_at": model.get("modified_at"),
            "format": details.get("format"),
            "family": details.get("family"),
            "parameter_size": details.get("parameter_size"),
            "quantization": details.get("quantization_level"),
        }
        attributes.update(
            {key: value for key, value in factual_fields.items() if isinstance(value, str) and value}
        )
        digest = model.get("digest")
        entries.append(
            {
                "kind": "model",
                "namespace": "ollama",
                "name": name,
                "subject_identity": digest if isinstance(digest, str) and digest else None,
                "attributes": attributes,
            }
        )
    return entries


def _model_from_capability(entry: dict[str, Any]) -> dict[str, Any]:
    attributes = entry.get("attributes") if isinstance(entry.get("attributes"), dict) else {}
    details = {
        key: attributes[source]
        for key, source in (
            ("format", "format"),
            ("family", "family"),
            ("parameter_size", "parameter_size"),
            ("quantization_level", "quantization"),
        )
        if source in attributes
    }
    model: dict[str, Any] = {"name": entry.get("name")}
    if isinstance(attributes.get("size_bytes"), int):
        model["size"] = attributes["size_bytes"]
    if entry.get("subject_identity") is not None:
        model["digest"] = entry["subject_identity"]
    if attributes.get("modified_at") is not None:
        model["modified_at"] = attributes["modified_at"]
    if details:
        model["details"] = details
    return model


class InventoryAwareFabricSession(FabricSession):
    """Fabric session that tracks the live model inventory of remote workers."""

    def __init__(self, config: FabricConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self.model_inventories: dict[str, tuple[dict[str, Any], ...]] = {}
        self.model_inventory_errors: dict[str, str] = {}
        self.capability_api_available = False

    def initialize(self) -> None:
        super().initialize()
        if self.enabled and self.client is not None:
            if _supports_request_id(self.client) and not isinstance(self.client, _FreshDispatchClient):
                self.client = _FreshDispatchClient(self.client)
            self.capability_api_available = callable(
                getattr(self.client, "ingest_capability_observation", None)
            )
            self._refresh_model_inventories()

    def refresh(self) -> FabricStatus:
        super().refresh()
        if self.enabled and self.client is not None:
            self._refresh_model_inventories()
        return self.status()

    def refresh_model_inventory(self) -> FabricStatus:
        """Refresh worker availability and query Ollama tags again right now."""

        if self.enabled and self.client is not None:
            if self._state == "available":
                self._refresh_remote_workers()
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
                request_id=_fresh_request_id(f"elh-inventory:{worker_id}"),
            )[0]
        record = result.get("record") or {}
        if not _execution_succeeded(result, record):
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
        observed_remote_ids: set[str] = set()
        for worker in workers:
            worker_id = str(worker.get("worker_id") or "")
            if worker.get("source") == "remote" and worker_id:
                observed_remote_ids.add(worker_id)
            if (
                not worker_id
                or worker.get("source") != "remote"
                or worker.get("availability") != "AVAILABLE"
            ):
                continue
            try:
                inventory = self._probe_model_inventory(worker_id)
                if self.capability_api_available:
                    self.client.ingest_capability_observation(
                        worker_id,
                        _model_capability_entries(inventory),
                        observation_source="epi13-local-harness:bounded-worker-loopback-probe",
                    )
                    self.model_inventories.pop(worker_id, None)
                else:
                    self.model_inventories[worker_id] = inventory
                self.model_inventory_errors.pop(worker_id, None)
            except Exception as exc:
                # Do not route from a stale inventory after a failed fresh scan.
                self.model_inventories.pop(worker_id, None)
                error = str(exc)
                if self.capability_api_available:
                    try:
                        self.client.ingest_capability_observation(
                            worker_id,
                            [],
                            availability="UNAVAILABLE",
                            observation_source=(
                                "epi13-local-harness:bounded-worker-loopback-probe"
                            ),
                            status_reason=(" ".join(error.split())[:512] or "probe failed"),
                        )
                    except Exception as ingest_exc:
                        error = f"{error}; failed to publish unavailable observation: {ingest_exc}"
                self.model_inventory_errors[worker_id] = error
        for worker_id in tuple(self.model_inventories):
            if worker_id not in observed_remote_ids:
                self.model_inventories.pop(worker_id, None)
        for worker_id in tuple(self.model_inventory_errors):
            if worker_id != "*" and worker_id not in observed_remote_ids:
                self.model_inventory_errors.pop(worker_id, None)

    def _worker_inventory(self, worker: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        worker_id = str(worker.get("worker_id") or "")
        if worker_id in self.model_inventory_errors:
            return ()
        if self.capability_api_available:
            if worker.get("capability_inventory_status") != "CURRENT":
                return ()
            observation = worker.get("capability_observation")
            if not isinstance(observation, dict) or observation.get("availability") != "AVAILABLE":
                return ()
            capabilities = observation.get("capabilities")
            if not isinstance(capabilities, list):
                return ()
            return tuple(
                _model_from_capability(entry)
                for entry in capabilities
                if isinstance(entry, dict) and entry.get("kind") == "model"
            )
        return self.model_inventories.get(worker_id, ())

    @staticmethod
    def _unavailable_status(workers: list[dict[str, Any]], fabric_state: str) -> str:
        remote = [worker for worker in workers if worker.get("source") == "remote"]
        if fabric_state != "available":
            return "FABRIC_UNAVAILABLE"
        if not remote:
            return "UNKNOWN"
        available = [worker for worker in remote if worker.get("availability") == "AVAILABLE"]
        if not available:
            return "WORKER_UNAVAILABLE"
        statuses = {str(worker.get("capability_inventory_status") or "UNKNOWN") for worker in available}
        if "STALE" in statuses:
            return "STALE"
        if statuses == {"CURRENT"}:
            return "MODEL_NOT_INSTALLED"
        return "UNKNOWN"

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
        status = self.status()
        workers = [dict(worker) for worker in status.workers]
        candidates = [
            (str(worker.get("worker_id")), self._worker_inventory(worker))
            for worker in workers
            if worker.get("source") == "remote" and worker.get("availability") == "AVAILABLE"
        ]
        candidates = [(worker_id, inventory) for worker_id, inventory in candidates if inventory]
        selection: ModelSelection | None = None
        for worker_id, inventory in sorted(candidates):
            exact = select_installed_model(role, model.name, inventory)
            if exact is not None and exact.selected_model == model.name:
                selection = replace(exact, worker_id=worker_id)
                break
        if selection is None and candidates:
            combined = [item for _worker_id, inventory in candidates for item in inventory]
            fallback = select_installed_model(role, model.name, combined)
            if fallback is not None:
                eligible = sorted(
                    worker_id
                    for worker_id, inventory in candidates
                    if any(
                        str(item.get("name") or item.get("model") or "")
                        == fallback.selected_model
                        for item in inventory
                    )
                )
                if eligible:
                    selection = replace(fallback, worker_id=eligible[0])
        if selection is None and (
            self.capability_api_available or (self.enabled and status.state != "available")
        ):
            inventory_status = self._unavailable_status(workers, status.state)
            reason = {
                "FABRIC_UNAVAILABLE": "Fabric is unavailable; no current model placement is known",
                "WORKER_UNAVAILABLE": "all enrolled inference workers are unavailable",
                "STALE": "worker model capability inventory is stale",
                "MODEL_NOT_INSTALLED": "configured model is not installed and no compatible fallback is available",
                "UNKNOWN": "worker model capability inventory is unknown",
            }[inventory_status]
            selection = ModelSelection(
                role=role,
                configured_model=model.name,
                selected_model=model.name,
                stored_size_bytes=model.model_storage_bytes,
                reason=reason,
                worker_id=None,
                inventory_status=inventory_status,
                available=False,
            )
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
            inventory = self._worker_inventory(item)
            if self.capability_api_available:
                item["model_inventory_status"] = item.get(
                    "capability_inventory_status", "UNKNOWN"
                )
            elif inventory:
                item["model_inventory_status"] = "CURRENT"
            inventory_known = (
                self.capability_api_available
                and item.get("model_inventory_status") == "CURRENT"
            ) or (not self.capability_api_available and worker_id in self.model_inventories)
            if inventory_known and worker_id not in self.model_inventory_errors:
                item["model_inventory"] = [dict(model) for model in inventory]
                item["model_names"] = sorted(
                    {
                        str(model.get("name") or model.get("model"))
                        for model in inventory
                        if model.get("name") or model.get("model")
                    }
                )
                item["model_count"] = len(item["model_names"])
            if worker_id in self.model_inventory_errors:
                item["model_inventory_error"] = self.model_inventory_errors[worker_id]
                item["model_inventory_status"] = "UNAVAILABLE"
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
