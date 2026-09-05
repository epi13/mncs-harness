"""Fabric session extension with worker-local Ollama inventory discovery.

Unlike the operator SSH inventory command, this runtime path uses Fabric itself
to execute a tiny Python probe on the already-enrolled worker. The probe only
queries the worker loopback Ollama API. This keeps SSH out of inference/runtime
routing so selection can rank models that are actually installed on the worker.
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
from .model_evidence import load_evidence
from .model_selection import ModelSelection, select_installed_model
from .models import FabricConfig, ModelConfig, ModelResidencyConfig, RoutingOverride

_INVENTORY_PREFIX = "ELH_FABRIC_MODEL_INVENTORY "
_RESIDENCY_PREFIX = "ELH_FABRIC_RESIDENCY "
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


def stage(name, **fields):
    payload = {"stage": name}
    payload.update(fields)
    print("ELH_FABRIC_STAGE " + json.dumps(payload, separators=(",", ":"), ensure_ascii=True), flush=True)


stage("worker-started")
try:
    values = {}
    stage("provider-connecting")
    for name, endpoint in (("installed", "tags"), ("running", "ps"), ("version", "version")):
        with urllib.request.urlopen("http://127.0.0.1:11434/api/" + endpoint, timeout=10) as response:
            values[name] = json.loads(response.read().decode("utf-8"))
    stage("provider-ready")
except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
    stage("failed", subsystem="ollama", error=str(exc))
    raise SystemExit("worker-local Ollama inventory failed: " + str(exc)) from exc
installed = values["installed"].get("models", [])
running = values["running"].get("models", [])
if not isinstance(installed, list) or not isinstance(running, list):
    raise SystemExit("worker-local Ollama inventory returned a non-list models field")
for index, item in enumerate(installed):
    if not isinstance(item, dict):
        continue
    name = item.get("name") or item.get("model")
    if not isinstance(name, str) or not name:
        continue
    try:
        show_request = urllib.request.Request(
            "http://127.0.0.1:11434/api/show",
            data=json.dumps({"model": name}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(show_request, timeout=10) as response:
            shown = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        continue
    capabilities = shown.get("capabilities") if isinstance(shown, dict) else None
    if isinstance(capabilities, list) and all(isinstance(value, str) for value in capabilities):
        installed[index] = dict(item, capabilities=capabilities)
print("ELH_FABRIC_MODEL_INVENTORY " + json.dumps({
    "installed": installed,
    "running": running,
    "version": values["version"].get("version"),
}, separators=(",", ":"), ensure_ascii=True), flush=True)
stage("completed")
'''


def _residency_script() -> str:
    return '''from __future__ import annotations

import json
from pathlib import Path
import urllib.error
import urllib.request

def stage(name, **fields):
    payload = {"stage": name}
    payload.update(fields)
    print("ELH_FABRIC_STAGE " + json.dumps(payload, separators=(",", ":"), ensure_ascii=True), flush=True)


stage("worker-started")
request = json.loads(Path("request.json").read_text(encoding="utf-8"))
try:
    stage("provider-connecting", model=request["model"])
    with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=10) as response:
        before = json.loads(response.read().decode("utf-8")).get("models", [])
    if not isinstance(before, list):
        raise SystemExit("worker-local Ollama residency returned malformed running state")
    preexisting = next((item for item in before if isinstance(item, dict) and (
        item.get("name") == request["model"] or item.get("model") == request["model"]
    )), None)
    if request["action"] == "release" and preexisting is None:
        running = before
    else:
        payload = json.dumps({
            "model": request["model"],
            "prompt": "",
            "stream": False,
            "keep_alive": 0 if request["action"] == "release" else request["keep_alive"],
        }).encode("utf-8")
        operation = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        stage("residency-operation-started", action=request["action"], model=request["model"])
        with urllib.request.urlopen(operation, timeout=request["timeout_seconds"]) as response:
            json.loads(response.read().decode("utf-8"))
        with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=10) as response:
            running = json.loads(response.read().decode("utf-8")).get("models", [])
    stage("residency-operation-completed", action=request["action"], model=request["model"])
except urllib.error.HTTPError as exc:
    try:
        detail = exc.read().decode("utf-8", "replace")[:1024]
    except OSError:
        detail = "<error body unavailable>"
    stage("failed", subsystem="ollama", error="HTTP " + str(exc.code))
    raise SystemExit(
        "worker-local Ollama residency failed: HTTP "
        + str(exc.code)
        + ": "
        + detail
    ) from exc
except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
    stage("failed", subsystem="ollama", error=str(exc))
    raise SystemExit("worker-local Ollama residency failed: " + str(exc)) from exc
if not isinstance(running, list):
    raise SystemExit("worker-local Ollama residency returned malformed running state")
selected = next((item for item in running if isinstance(item, dict) and (
    item.get("name") == request["model"] or item.get("model") == request["model"]
)), None)
print("ELH_FABRIC_RESIDENCY " + json.dumps({
    "action": request["action"],
    "provider": "ollama",
    "model": request["model"],
    "loaded": selected is not None,
    "preexisting_loaded": preexisting is not None,
    "running": selected,
    "remaining_loaded_models": sorted(str(item.get("name") or item.get("model")) for item in running
        if isinstance(item, dict) and (item.get("name") or item.get("model"))),
}, separators=(",", ":"), ensure_ascii=True), flush=True)
stage("completed")
'''


class _FreshDispatchClient:
    """Delegate Fabric calls while making observation/inference dispatches fresh.

    The underlying Fabric API keeps deterministic request identities by default.
    This wrapper uses the public ``request_id`` seam so MNCS Harness does not
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
        transport = getattr(self._client, "_service_transport", None)
        previous_timeout = getattr(transport, "timeout", None)
        if transport is not None and isinstance(previous_timeout, (int, float)):
            plan = args[0] if args else None
            plan_timeout = plan.get("timeout_seconds") if isinstance(plan, dict) else None
            if isinstance(plan_timeout, (int, float)) and plan_timeout > previous_timeout:
                # A provider job can legitimately outlive the short control
                # request timeout. Keep the local socket bound while honoring
                # the transport's hard 30-second safety ceiling.
                transport.timeout = min(30.0, max(float(previous_timeout), float(plan_timeout)))
        try:
            return self._client.execute(*args, **kwargs)
        finally:
            if transport is not None and previous_timeout is not None:
                transport.timeout = previous_timeout


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
    provider_versions = sorted(
        {
            str(model["provider_version"])
            for model in models
            if isinstance(model.get("provider_version"), str)
        }
    )
    entries: list[dict[str, Any]] = [
        {
            "kind": "runtime",
            "namespace": "ollama",
            "name": "ollama",
            "attributes": {
                "interface": "loopback-http-api",
                "residency_actions": ["observe", "release", "warm"],
                **({"version": provider_versions[0]} if len(provider_versions) == 1 else {}),
            },
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
        attributes["loaded"] = bool(model.get("loaded", False))
        attributes["residency_control"] = "provider-loopback-api"
        for key in ("size_vram", "context_length"):
            value = model.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                attributes[key] = value
        factual_fields = {
            "modified_at": model.get("modified_at"),
            "format": details.get("format"),
            "family": details.get("family"),
            "parameter_size": details.get("parameter_size"),
            "quantization": details.get("quantization_level"),
            "expires_at": model.get("expires_at"),
            "provider_version": model.get("provider_version"),
        }
        attributes.update(
            {key: value for key, value in factual_fields.items() if isinstance(value, str) and value}
        )
        capabilities = model.get("capabilities")
        if isinstance(capabilities, list) and all(
            isinstance(value, str) for value in capabilities
        ):
            attributes["ollama_capabilities"] = list(capabilities)
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
    model: dict[str, Any] = {
        "name": entry.get("name"),
        "provider": entry.get("namespace"),
    }
    if isinstance(attributes.get("size_bytes"), int):
        model["size"] = attributes["size_bytes"]
    if entry.get("subject_identity") is not None:
        model["digest"] = entry["subject_identity"]
    if attributes.get("modified_at") is not None:
        model["modified_at"] = attributes["modified_at"]
    model["loaded"] = bool(attributes.get("loaded", False))
    for key in ("size_vram", "context_length", "expires_at", "provider_version"):
        if attributes.get(key) is not None:
            model[key] = attributes[key]
    capabilities = attributes.get("ollama_capabilities")
    if isinstance(capabilities, list) and all(isinstance(value, str) for value in capabilities):
        model["capabilities"] = list(capabilities)
    if details:
        model["details"] = details
    return model


class InventoryAwareFabricSession(FabricSession):
    """Fabric session that tracks the live model inventory of remote workers."""

    def __init__(
        self,
        config: FabricConfig,
        *,
        residency_config: ModelResidencyConfig | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        self.residency_config = residency_config or ModelResidencyConfig()
        self.model_inventories: dict[str, tuple[dict[str, Any], ...]] = {}
        self.model_inventory_errors: dict[str, str] = {}
        self.capability_api_available = False

    def _service_execution_supported(self) -> bool:
        return (
            self.config.controller_mode == "service"
            and self._execution_transport == "persistent-service"
            and self._capability_inventory == "persistent-service"
        )

    def initialize(self, *, refresh_inventory: bool = True) -> None:
        super().initialize()
        if self.enabled and self.client is not None:
            if _supports_request_id(self.client) and not isinstance(self.client, _FreshDispatchClient):
                self.client = _FreshDispatchClient(self.client)
            self.capability_api_available = (
                (
                    self.config.controller_mode == "embedded"
                    or self._service_execution_supported()
                )
                and callable(getattr(self.client, "ingest_capability_observation", None))
            )
            if refresh_inventory and (
                self.config.controller_mode == "embedded" or self._service_execution_supported()
            ):
                self._refresh_model_inventories()

    def refresh(self) -> FabricStatus:
        super().refresh()
        if (
            self.enabled
            and self.client is not None
            and (
                self.config.controller_mode == "embedded"
                or self._service_execution_supported()
            )
        ):
            self._refresh_model_inventories()
        return self.status()

    def refresh_model_inventory(self) -> FabricStatus:
        """Refresh worker availability and query Ollama tags again right now."""

        if (
            self.enabled
            and self.client is not None
            and (
                self.config.controller_mode == "embedded"
                or self._service_execution_supported()
            )
        ):
            if self._state == "available":
                self._refresh_remote_workers()
            self._refresh_model_inventories()
        return self.status()

    def _probe_model_inventory(self, worker_id: str) -> tuple[dict[str, Any], ...]:
        if self.config.controller_mode != "embedded" and not self._service_execution_supported():
            raise FabricExecutionError(
                "FABRIC_SERVICE_EXECUTION_UNSUPPORTED: worker inventory probes require persistent execution dispatch"
            )
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
                    "environment": {"PYTHONHASHSEED": "0", "PYTHONUNBUFFERED": "1"},
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
        if not isinstance(payload, dict):
            raise FabricExecutionError(f"model inventory returned a non-object on {worker_id}")
        installed = payload.get("installed")
        running = payload.get("running")
        version = payload.get("version")
        if not isinstance(installed, list) or not isinstance(running, list):
            raise FabricExecutionError(
                f"model inventory returned malformed installed/running lists on {worker_id}"
            )
        running_by_name = {
            str(item.get("name") or item.get("model")): item
            for item in running
            if isinstance(item, dict) and (item.get("name") or item.get("model"))
        }
        models: list[dict[str, Any]] = []
        for item in installed:
            if not isinstance(item, dict):
                continue
            model = dict(item)
            name = str(model.get("name") or model.get("model") or "")
            loaded = running_by_name.get(name)
            model["loaded"] = loaded is not None
            if loaded is not None:
                for key in ("size_vram", "context_length", "expires_at"):
                    if loaded.get(key) is not None:
                        model[key] = loaded[key]
            if isinstance(version, str) and version:
                model["provider_version"] = version
                if version == "0.0.0":
                    model["provider_version_trust"] = "untrusted-packaging"
            models.append(model)
        return tuple(models)

    @staticmethod
    def residency_capability(provider_namespace: str | None) -> dict[str, Any]:
        """Return factual lifecycle actions for a provider namespace.

        This is the Harness abstraction boundary. Provider-specific request
        fields remain inside the bounded worker operation.
        """

        provider = str(provider_namespace or "ollama")
        actions = ("observe", "release", "warm") if provider == "ollama" else ()
        return {
            "provider": provider,
            "supported": bool(actions),
            "actions": list(actions),
            "conversation_state": "controller-supplied-per-request",
            "weights_state": "provider-worker-local",
        }

    def warm_model(
        self,
        worker_id: str,
        model_name: str,
        *,
        keep_alive: str | int = -1,
        timeout_seconds: float = 300.0,
    ) -> dict[str, Any]:
        """Compatibility wrapper for background/operator residency warming."""

        return self.establish_model_residency(
            worker_id,
            model_name,
            provider_namespace="ollama",
            keep_alive=keep_alive,
            timeout_seconds=timeout_seconds,
            policy_mode="background-pinned",
        )

    def establish_model_residency(
        self,
        worker_id: str,
        model_name: str,
        *,
        provider_namespace: str,
        keep_alive: str | int = -1,
        timeout_seconds: float = 300.0,
        policy_mode: str = "experiment-pinned",
        experiment_id: str | None = None,
    ) -> dict[str, Any]:
        """Establish and authoritatively observe worker-local model residency."""

        return self._change_model_residency(
            worker_id,
            model_name,
            action="warm",
            provider_namespace=provider_namespace,
            keep_alive=keep_alive,
            timeout_seconds=timeout_seconds,
            policy_mode=policy_mode,
            experiment_id=experiment_id,
        )

    def release_model_residency(
        self,
        worker_id: str,
        model_name: str,
        *,
        provider_namespace: str,
        timeout_seconds: float = 300.0,
        experiment_id: str | None = None,
    ) -> dict[str, Any]:
        """Release only the named model and verify the provider observation."""

        return self._change_model_residency(
            worker_id,
            model_name,
            action="release",
            provider_namespace=provider_namespace,
            keep_alive=0,
            timeout_seconds=timeout_seconds,
            policy_mode="explicit-release",
            experiment_id=experiment_id,
        )

    def _change_model_residency(
        self,
        worker_id: str,
        model_name: str,
        *,
        action: str,
        provider_namespace: str,
        keep_alive: str | int,
        timeout_seconds: float,
        policy_mode: str,
        experiment_id: str | None,
    ) -> dict[str, Any]:
        """Run one provider-specific residency operation through exact Fabric placement."""

        if (
            not model_name
            or len(model_name) > 256
            or any(ord(character) < 32 for character in model_name)
        ):
            raise FabricExecutionError("RESIDENCY_MODEL_INVALID: model name is invalid")
        if not 1 <= timeout_seconds <= 3600:
            raise FabricExecutionError("RESIDENCY_TIMEOUT_INVALID: warm timeout is out of bounds")
        if action not in {"warm", "release"}:
            raise FabricExecutionError("RESIDENCY_ACTION_INVALID: unsupported residency action")
        capability = self.residency_capability(provider_namespace)
        if action not in capability["actions"]:
            raise FabricExecutionError(
                f"RESIDENCY_PROVIDER_UNSUPPORTED: {provider_namespace} does not support {action}"
            )
        if self.config.controller_mode == "service" and not self._service_execution_supported():
            raise FabricExecutionError(
                "FABRIC_SERVICE_EXECUTION_UNSUPPORTED: model residency requires persistent execution dispatch"
            )
        status = self.status()
        worker = next(
            (item for item in status.workers if item.get("worker_id") == worker_id),
            None,
        )
        if worker is None:
            raise FabricExecutionError(f"RESIDENCY_WORKER_UNKNOWN: {worker_id}")
        if worker.get("availability") != "AVAILABLE":
            raise FabricExecutionError(f"RESIDENCY_WORKER_UNAVAILABLE: {worker_id}")
        if worker.get("model_inventory_status") != "CURRENT":
            raise FabricExecutionError(f"RESIDENCY_INVENTORY_NOT_CURRENT: {worker_id}")
        inventory = worker.get("model_inventory") or []
        installed_model = next(
            (
                item
                for item in inventory
                if isinstance(item, dict)
                and str(item.get("name") or item.get("model") or "") == model_name
            ),
            None,
        )
        if installed_model is None:
            raise FabricExecutionError(
                f"RESIDENCY_MODEL_NOT_INSTALLED: {model_name} on {worker_id}"
            )
        observed_provider = str(
            installed_model.get("provider")
            or installed_model.get("namespace")
            or "ollama"
        )
        if observed_provider != provider_namespace:
            raise FabricExecutionError(
                "RESIDENCY_PROVIDER_MISMATCH: "
                f"{model_name} on {worker_id} is observed under {observed_provider}, "
                f"not {provider_namespace}"
            )
        if self.client is None:
            raise FabricExecutionError("Fabric client is unavailable")
        request = {
            "action": action,
            "model": model_name,
            "keep_alive": keep_alive,
            "timeout_seconds": timeout_seconds,
            "provider_namespace": provider_namespace,
            "policy_mode": policy_mode,
            "experiment_id": experiment_id,
        }
        self.config.state_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="elh-fabric-residency-", dir=self.config.state_path.parent
        ) as directory:
            source_root = Path(directory)
            (source_root / "residency.py").write_text(
                _residency_script(), encoding="utf-8"
            )
            (source_root / "request.json").write_text(
                json.dumps(request, ensure_ascii=True, separators=(",", ":")),
                encoding="utf-8",
            )
            from mncs_fabric.artifacts import build_manifest
            from mncs_fabric.bundles import build_bundle_archive
            from mncs_fabric.models import validate_job_plan

            manifest = build_manifest(source_root)
            archive = source_root / "execution-bundle.zip"
            build_bundle_archive(source_root, archive)
            plan = validate_job_plan(
                {
                    "schema_version": "mncs-fabric.job-plan.v0.1",
                    "job_id": f"elh-worker-model-residency-{action}",
                    "candidate_identity": _identity(
                        {"worker_id": worker_id, "residency": request}
                    ),
                    "evaluator_identity": None,
                    "artifact_manifest_identity": manifest["manifest_identity"],
                    "argv": ["@python", "residency.py"],
                    "working_directory": ".",
                    "timeout_seconds": timeout_seconds + 5.0,
                    "output_limit_bytes": 256 * 1024,
                    "environment": {"PYTHONHASHSEED": "0", "PYTHONUNBUFFERED": "1"},
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
                request_id=_fresh_request_id(f"elh-residency:{worker_id}"),
            )[0]
        record = result.get("record") or {}
        if not _execution_succeeded(result, record):
            reason = _execution_failure(result, record, "residency warm failed")
            raise FabricExecutionError(
                f"RESIDENCY_{action.upper()}_FAILED: {worker_id}/{model_name}: {reason}"
            )
        response_line = next(
            (
                line
                for line in ((record.get("stdout") or {}).get("captured_utf8") or "").splitlines()
                if line.startswith(_RESIDENCY_PREFIX)
            ),
            None,
        )
        if response_line is None:
            raise FabricExecutionError("RESIDENCY_RESPONSE_MALFORMED: no residency result")
        try:
            provider = json.loads(response_line.removeprefix(_RESIDENCY_PREFIX))
        except json.JSONDecodeError as exc:
            raise FabricExecutionError(
                "RESIDENCY_RESPONSE_MALFORMED: invalid JSON"
            ) from exc
        expected_loaded = action == "warm"
        if not isinstance(provider, dict) or provider.get("loaded") is not expected_loaded:
            raise FabricExecutionError(
                f"RESIDENCY_VERIFICATION_FAILED: provider reported loaded="
                f"{provider.get('loaded') if isinstance(provider, dict) else None}; "
                f"expected {expected_loaded} for {model_name}"
            )
        self._refresh_model_inventories()
        return {
            "outcome": "PASS",
            "action": action,
            "worker_id": worker_id,
            "model": model_name,
            "provider": provider_namespace,
            "policy_mode": policy_mode,
            "experiment_id": experiment_id,
            "loaded": expected_loaded,
            "preexisting_loaded": bool(provider.get("preexisting_loaded")),
            "remaining_loaded_models": list(provider.get("remaining_loaded_models") or []),
            "provider_observation": provider,
            "fabric_request_identity": result.get("request_identity"),
            "fabric_record_identity": result.get("record_identity"),
            "fabric_receipt_identity": result.get("receipt_identity"),
        }

    def _refresh_model_inventories(self) -> None:
        if (
            self.client is None
            or (
                self.config.controller_mode != "embedded"
                and not self._service_execution_supported()
            )
        ):
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

    @staticmethod
    def _inventory_freshness(worker: dict[str, Any]) -> str:
        return str(
            worker.get("model_inventory_status")
            or worker.get("capability_inventory_status")
            or "UNKNOWN"
        )

    def _worker_inventory(
        self,
        worker: dict[str, Any],
        *,
        allow_stale: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        worker_id = str(worker.get("worker_id") or "")
        if worker_id in self.model_inventory_errors:
            return ()
        if self.capability_api_available:
            freshness = str(worker.get("capability_inventory_status") or "UNKNOWN")
            if freshness == "CURRENT":
                pass
            elif freshness == "STALE" and allow_stale:
                pass
            else:
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
        routing_override: RoutingOverride | None = None,
        *,
        status: FabricStatus | None = None,
    ) -> tuple[ModelConfig, ModelSelection | None]:
        """Select a worker-installed model for one Fabric-backed role."""

        if model.provider != "fabric":
            return model, None
        captured = status if status is not None else self.status()
        return self.resolve_model_from_status(
            captured, role, model, routing_override=routing_override
        )

    def resolve_model_from_status(
        self,
        status: FabricStatus,
        role: str,
        model: ModelConfig,
        routing_override: RoutingOverride | None = None,
    ) -> tuple[ModelConfig, ModelSelection | None]:
        """Resolve a role against one already-captured Fabric status.

        Automatic/role placement uses last-known CURRENT or STALE inventory.
        STALE remains usable last-known truth; it is not treated as empty.
        UNKNOWN/UNAVAILABLE still cannot authorize a route. A newly installed
        model requires an explicit inventory refresh, not a service restart.
        """

        if model.provider != "fabric":
            return model, None
        requested = routing_override or RoutingOverride()
        allow_stale = True
        accepted_freshness = {"CURRENT", "STALE"}
        workers = [dict(worker) for worker in status.workers]
        current_candidates = [
            (
                str(worker.get("worker_id")),
                self._worker_inventory(worker, allow_stale=allow_stale),
            )
            for worker in workers
            if worker.get("source") in {None, "", "remote", "registry"}
            and worker.get("availability") == "AVAILABLE"
            and self._inventory_freshness(worker) in accepted_freshness
        ]
        candidates = [
            (worker_id, inventory)
            for worker_id, inventory in current_candidates
            if inventory
        ]
        explicit_resident = {
            item.worker_id: item.model
            for item in self.residency_config.workers
            if item.model is not None
        }

        def named(inventory: tuple[dict[str, Any], ...], name: str) -> dict[str, Any] | None:
            return next(
                (
                    item
                    for item in inventory
                    if str(item.get("name") or item.get("model") or "") == name
                ),
                None,
            )

        def selected(
            worker_id: str,
            selected_model: str,
            inventory: tuple[dict[str, Any], ...],
            reason: str,
        ) -> ModelSelection:
            item = named(inventory, selected_model) or {}
            size = item.get("size")
            worker = next(
                (row for row in workers if row.get("worker_id") == worker_id),
                {},
            )
            return ModelSelection(
                role=role,
                configured_model=model.name,
                selected_model=selected_model,
                stored_size_bytes=(
                    size if isinstance(size, int) and not isinstance(size, bool) else 0
                ),
                reason=reason,
                worker_id=worker_id,
                inventory_status=self._inventory_freshness(worker),
                loaded=bool(item.get("loaded", False)),
                resident=explicit_resident.get(worker_id) == selected_model,
                route_mode=requested.mode,
            )

        def unavailable(
            code: str,
            reason: str,
            *,
            worker_id: str | None = None,
            selected_model: str | None = None,
        ) -> ModelSelection:
            return ModelSelection(
                role=role,
                configured_model=model.name,
                selected_model=selected_model or model.name,
                stored_size_bytes=model.model_storage_bytes,
                reason=f"{code}: {reason}",
                worker_id=worker_id,
                inventory_status=code,
                available=False,
                route_mode=requested.mode,
            )

        selection: ModelSelection | None = None

        if requested.mode in {"WORKER", "WORKER_MODEL", "WORKER_MODEL_ROLE"}:
            target = next(
                (worker for worker in workers if worker.get("worker_id") == requested.worker),
                None,
            )
            failure: ModelSelection | None = None
            if target is None or target.get("availability") != "AVAILABLE":
                failure = unavailable(
                    "PINNED_WORKER_UNAVAILABLE",
                    f"selected worker {requested.worker} is unavailable or unknown",
                    worker_id=requested.worker,
                    selected_model=requested.model,
                )
            elif self._inventory_freshness(target) not in (
                {"CURRENT", "STALE"} if requested.mode == "WORKER_MODEL" else {"CURRENT"}
            ):
                failure = unavailable(
                    "PINNED_INVENTORY_NOT_CURRENT",
                    f"selected worker {requested.worker} has no current model inventory",
                    worker_id=requested.worker,
                    selected_model=requested.model,
                )
            else:
                inventory = self._worker_inventory(target, allow_stale=True)
                if requested.mode in {"WORKER_MODEL", "WORKER_MODEL_ROLE"}:
                    if named(inventory, str(requested.model)) is None:
                        failure = unavailable(
                            "PINNED_MODEL_MISSING",
                            f"model {requested.model} is not reported on {requested.worker}",
                            worker_id=requested.worker,
                            selected_model=requested.model,
                        )
                    else:
                        selection = selected(
                            str(requested.worker),
                            str(requested.model),
                            inventory,
                            "operator pinned the exact Fabric worker/model pair",
                        )
                else:
                    resolved = select_installed_model(
                        role, model.name, inventory, evidence=load_evidence()
                    )
                    if resolved is None:
                        failure = unavailable(
                            "PINNED_WORKER_HAS_NO_ELIGIBLE_MODEL",
                            f"selected worker {requested.worker} has no eligible installed model",
                            worker_id=requested.worker,
                        )
                    else:
                        selection = selected(
                            str(requested.worker),
                            resolved.selected_model,
                            inventory,
                            "operator pinned the Fabric worker; " + resolved.reason,
                        )
            if selection is None and failure is not None and not requested.allow_fallback:
                selection = failure

        if selection is None and requested.mode == "MODEL":
            # A model-only pin asserts a capability without naming a worker,
            # so it needs fresh proof: STALE remains usable for opportunistic
            # AUTO routing and exact worker/model pairs, but never for a
            # location-free capability assertion.
            fresh = {
                str(worker.get("worker_id"))
                for worker in workers
                if self._inventory_freshness(worker) == "CURRENT"
            }
            eligible = [
                (worker_id, inventory)
                for worker_id, inventory in candidates
                if worker_id in fresh
                and named(inventory, str(requested.model)) is not None
            ]
            if eligible:
                eligible.sort(
                    key=lambda item: (
                        not bool(named(item[1], str(requested.model)).get("loaded", False)),
                        explicit_resident.get(item[0]) != requested.model,
                        item[0],
                    )
                )
                worker_id, inventory = eligible[0]
                selection = selected(
                    worker_id,
                    str(requested.model),
                    inventory,
                    f"selected {requested.model} on {worker_id} because the operator pinned the exact model",
                )
            elif not requested.allow_fallback:
                selection = unavailable(
                    "PINNED_MODEL_UNAVAILABLE",
                    f"model {requested.model} is not currently reported by an available worker",
                    selected_model=requested.model,
                )

        manual_failed_open = (
            requested.mode != "AUTO"
            and requested.mode != "ROLE"
            and selection is None
            and requested.allow_fallback
        )
        auto_allowed = requested.mode in {"AUTO", "ROLE"} or manual_failed_open
        if auto_allowed:
            exact_candidates = [
                (worker_id, inventory)
                for worker_id, inventory in candidates
                if named(inventory, model.name) is not None
            ]
            if exact_candidates:
                exact_candidates.sort(
                    key=lambda item: (
                        not bool(named(item[1], model.name).get("loaded", False))
                        if self.residency_config.prefer_resident_for_auto_routing
                        else False,
                        explicit_resident.get(item[0]) != model.name,
                        item[0],
                    )
                )
                worker_id, inventory = exact_candidates[0]
                loaded = bool(named(inventory, model.name).get("loaded"))
                reason = (
                    f"selected {model.name} on {worker_id} because it exactly matches "
                    f"role {role} and is {'already resident' if loaded else 'installed'}"
                )
                if manual_failed_open:
                    reason = "explicit manual fallback enabled; " + reason
                selection = selected(worker_id, model.name, inventory, reason)

        if selection is None and auto_allowed and candidates:
            combined = [item for _worker_id, inventory in candidates for item in inventory]
            fallback = select_installed_model(
                role, model.name, combined, evidence=load_evidence()
            )
            if fallback is not None:
                eligible = [
                    (worker_id, inventory)
                    for worker_id, inventory in candidates
                    if named(inventory, fallback.selected_model) is not None
                ]
                if eligible:
                    eligible.sort(
                        key=lambda item: (
                            not bool(
                                named(item[1], fallback.selected_model).get("loaded", False)
                            )
                            if self.residency_config.prefer_resident_for_auto_routing
                            else False,
                            explicit_resident.get(item[0]) != fallback.selected_model,
                            item[0],
                        )
                    )
                    worker_id, inventory = eligible[0]
                    reason = fallback.reason
                    if manual_failed_open:
                        reason = "explicit manual fallback enabled; " + reason
                    selection = selected(
                        worker_id, fallback.selected_model, inventory, reason
                    )
        if selection is None and auto_allowed and self.enabled:
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
                route_mode=requested.mode,
            )
        selected_name = selection.selected_model if selection is not None else model.name
        selected_size = (
            selection.stored_size_bytes
            if selection is not None and selection.stored_size_bytes > 0
            else model.model_storage_bytes
        )
        selected_capabilities: list[str] | None = None
        if selection is not None and selection.worker_id:
            selected_inventory = next(
                (
                    inventory
                    for worker_id, inventory in candidates
                    if worker_id == selection.worker_id
                ),
                (),
            )
            selected_item = named(selected_inventory, selected_name)
            capabilities = selected_item.get("capabilities") if selected_item else None
            if isinstance(capabilities, list) and all(
                isinstance(value, str) for value in capabilities
            ):
                selected_capabilities = capabilities
        effective_think = model.think
        if (
            model.think
            and selected_capabilities is not None
            and "thinking" not in selected_capabilities
        ):
            effective_think = False
            if selection is not None:
                selection = replace(
                    selection,
                    reason=(
                        selection.reason
                        + "; selected Ollama model does not advertise thinking; disabled"
                    ),
                )
        effective = replace(
            model,
            name=selected_name,
            model_storage_bytes=selected_size,
            think=effective_think,
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
            inventory = self._worker_inventory(item, allow_stale=True)
            if self.capability_api_available:
                item["model_inventory_status"] = item.get(
                    "capability_inventory_status", "UNKNOWN"
                )
            elif inventory:
                item["model_inventory_status"] = "CURRENT"
            inventory_known = (
                self.capability_api_available
                and item.get("model_inventory_status") in {"CURRENT", "STALE"}
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
                item["loaded_model_names"] = sorted(
                    {
                        str(model.get("name") or model.get("model"))
                        for model in inventory
                        if model.get("loaded")
                        and (model.get("name") or model.get("model"))
                    }
                )
                item["loaded_model_count"] = len(item["loaded_model_names"])
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
            controller_mode=base.controller_mode,
            controller_state=base.controller_state,
            fleet_state=base.fleet_state,
            execution_transport=base.execution_transport,
            capability_inventory=base.capability_inventory,
            fleet_authority=base.fleet_authority,
            inventory_transport=base.inventory_transport,
            controller_version=base.controller_version,
            controller_contract_identity=base.controller_contract_identity,
            service_contract=base.service_contract,
        )
