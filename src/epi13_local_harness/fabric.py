"""Optional MNCS Fabric consumer/session and bounded inference adapter."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .models import FabricConfig, ModelConfig
from .ollama import OllamaClient


class FabricUnavailable(RuntimeError):
    """Fabric cannot currently provide a session or eligible worker."""


class FabricExecutionError(RuntimeError):
    """Fabric ran or admitted a request but the bounded invocation failed."""


@dataclass(frozen=True)
class FabricStatus:
    enabled: bool
    state: str
    controller_id: str
    workers: tuple[dict[str, Any], ...] = ()
    detail: str | None = None
    last_inference: dict[str, Any] | None = None

    @property
    def available_workers(self) -> int:
        return sum(1 for worker in self.workers if worker.get("availability") == "AVAILABLE")

    @property
    def accelerator_count(self) -> int:
        count = 0
        for worker in self.workers:
            snapshot = worker.get("resource_snapshot") or {}
            count += len(snapshot.get("accelerators", []))
        return count

    @property
    def cuda_ready_count(self) -> int:
        return sum(
            1
            for worker in self.workers
            if worker.get("availability") == "AVAILABLE"
            and (worker.get("runtime_observation") or {}).get("accelerator_backend") == "cuda"
            and (worker.get("runtime_observation") or {}).get("runtime_execution_probe") == "PASS"
        )

    @property
    def offload_capable_count(self) -> int:
        return sum(
            1
            for worker in self.workers
            if "placement:sequential-cpu-offload" in worker.get("capabilities", [])
        )


def _identity(value: object) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _loopback_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("Fabric provider Ollama URL must be an HTTP(S) URL without credentials")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Fabric provider Ollama URL must target the worker loopback interface")
    return value.rstrip("/")


def _invocation_script() -> str:
    return '''from __future__ import annotations

import json
from pathlib import Path
import urllib.error
import urllib.request


request = json.loads(Path("request.json").read_text(encoding="utf-8"))
payload = request["payload"]
url = request["base_url"] + "/api/chat"
encoded = json.dumps(payload).encode("utf-8")
http_request = urllib.request.Request(
    url,
    data=encoded,
    method="POST",
    headers={"Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(http_request, timeout=request["timeout_seconds"]) as response:
        body = response.read().decode("utf-8")
except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
    raise SystemExit(f"worker-local Ollama invocation failed: {exc}") from exc
try:
    result = json.loads(body)
except json.JSONDecodeError as exc:
    raise SystemExit(f"worker-local Ollama returned invalid JSON: {body[:500]}") from exc
print("ELH_FABRIC_RESPONSE " + json.dumps(result, separators=(",", ":"), ensure_ascii=True))
'''


def _runtime_probe_script() -> str:
    return '''from __future__ import annotations

from datetime import datetime, timezone
import json
import sys


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


value = {
    "captured_at": now(),
    "python_version": sys.version.split()[0],
    "accelerator_backend": "cuda",
    "accelerator": None,
    "runtime_version": None,
    "execution_probe": "UNKNOWN",
    "precision_probes": {},
}
try:
    import torch
except Exception as exc:
    value["diagnostic"] = "torch import failed: " + type(exc).__name__
else:
    value["runtime_version"] = str(getattr(torch.version, "cuda", None) or "unknown")
    try:
        available = bool(torch.cuda.is_available())
    except Exception:
        available = False
    if not available:
        value["diagnostic"] = "torch.cuda.is_available() is false"
    else:
        try:
            index = torch.cuda.current_device()
            value["accelerator"] = str(torch.cuda.get_device_properties(index).name)
            device = "cuda:" + str(index)

            def probe(dtype):
                try:
                    left = torch.ones((64, 64), device=device, dtype=dtype)
                    right = torch.full((64, 64), 2, device=device, dtype=dtype)
                    result = left @ right
                    torch.cuda.synchronize()
                    return "PASS" if bool(torch.isfinite(result).all().item()) else "FAIL"
                except Exception:
                    return "UNKNOWN"

            fp32 = probe(torch.float32)
            value["precision_probes"]["float32"] = fp32
            value["execution_probe"] = "PASS" if fp32 == "PASS" else "FAIL"
            value["precision_probes"]["float16"] = probe(torch.float16)
            if hasattr(torch, "bfloat16"):
                value["precision_probes"]["bfloat16"] = probe(torch.bfloat16)
            torch.cuda.synchronize()
        except Exception as exc:
            value["execution_probe"] = "FAIL"
            value["diagnostic"] = "synchronized CUDA probe failed: " + type(exc).__name__
print("ELH_FABRIC_RUNTIME_PROBE " + json.dumps(value, separators=(",", ":"), ensure_ascii=True))
'''


class FabricSession:
    """Own the optional Fabric client and expose only consumer-level evidence."""

    def __init__(
        self,
        config: FabricConfig,
        *,
        client_factory: Callable[[str, Path], Any] | None = None,
    ) -> None:
        self.config = config
        self._client_factory = client_factory
        self.client: Any | None = None
        self._detail: str | None = None
        self._state = "disabled" if not config.enabled else "initializing"
        self.last_inference: dict[str, Any] | None = None
        self.last_execution_record: dict[str, Any] | None = None
        self._consumer_context: dict[str, str] | None = None

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def set_consumer_context(
        self,
        *,
        workload_identity: str,
        provider_identity: str,
        partition_identity: str,
    ) -> None:
        self._consumer_context = {
            "workload_identity": workload_identity,
            "provider_identity": provider_identity,
            "partition_identity": partition_identity,
        }

    def initialize(self) -> None:
        if not self.config.enabled:
            self._state = "disabled"
            return
        try:
            if self._client_factory is None:
                from mncs_fabric.api import FabricClient

                factory = FabricClient
            else:
                factory = self._client_factory
            self.client = factory(self.config.controller_id, self.config.state_path)
            from mncs_fabric.api import LocalWorkerConfig, RemoteWorkerConfig

            registered = 0
            errors: list[str] = []
            # Explicit worker tables are backward-compatible operator overrides.
            # Registry entries with the same identity must retain the same
            # endpoint; FabricClient rejects contradictory duplicates.
            for worker in self.config.workers:
                try:
                    if worker.kind == "local":
                        bundle_root = worker.bundle_root or self.config.worker_bundle_root
                        bundle_root.mkdir(parents=True, exist_ok=True)
                        self.client.register_local_worker(
                            LocalWorkerConfig(worker.worker_id, bundle_root, worker.state_path)
                        )
                    else:
                        required = (
                            worker.host,
                            worker.port,
                            worker.ca_file,
                            worker.client_certificate,
                            worker.client_key,
                            worker.trust_state,
                        )
                        if any(value is None for value in required):
                            raise ValueError("remote worker requires host, port, and all trust paths")
                        self.client.register_remote_worker(
                            RemoteWorkerConfig(
                                worker.worker_id,
                                worker.host or "",
                                worker.port or 0,
                                worker.capabilities,
                                worker.ca_file or Path(),
                                worker.client_certificate or Path(),
                                worker.client_key or Path(),
                                worker.trust_state or Path(),
                                concurrency_limit=worker.concurrency_limit,
                                timeout=worker.timeout_seconds,
                                connect_timeout=worker.connect_timeout_seconds,
                                control_timeout=worker.control_timeout_seconds,
                                execution_timeout_overhead=(
                                    worker.execution_timeout_overhead_seconds
                                ),
                            )
                        )
                    registered += 1
                except Exception as exc:  # configuration is reported, not hidden
                    errors.append(f"{worker.worker_id}: {exc}")
            if self.config.registry_path is not None:
                try:
                    report = self.client.load_registry(self.config.registry_path)
                    registered = len(getattr(self.client, "remote_configs", {})) + len(
                        getattr(getattr(self.client, "local", None), "workers", {})
                    )
                    errors.extend(
                        f"{worker_id}: {detail}"
                        for worker_id, detail in report.get("errors", {}).items()
                    )
                except Exception as exc:
                    errors.append(f"registry: {exc}")
            if self.config.refresh_on_startup and registered:
                self._refresh_remote_workers()
                errors.extend(self._ensure_cuda_runtime_observations())
            if registered == 0:
                self._state = "unavailable"
                self._detail = "; ".join(errors) or "no Fabric workers are configured"
            else:
                self._state = "available"
                combined = [item for item in (self._detail, *errors) if item]
                self._detail = "; ".join(dict.fromkeys(combined)) if combined else None
        except ImportError as exc:
            self.client = None
            self._state = "unavailable"
            self._detail = f"mncs-fabric is not installed: {exc}"
        except Exception as exc:
            self.client = None
            self._state = "unavailable"
            self._detail = str(exc)

    def _refresh_remote_workers(self) -> None:
        if self.client is None:
            return
        remote_ids = sorted(
            getattr(self.client, "remote_configs", {})
            or [
                worker.worker_id
                for worker in self.config.workers
                if worker.kind == "remote"
            ]
        )
        if not remote_ids:
            return
        failures: list[str] = []

        def refresh(worker_id: str) -> None:
            self.client.refresh_worker(worker_id)

        executor = ThreadPoolExecutor(max_workers=max(1, len(remote_ids)))
        try:
            futures = {executor.submit(refresh, worker_id): worker_id for worker_id in remote_ids}
            for future, worker_id in ((future, worker_id) for future, worker_id in futures.items()):
                try:
                    future.result(timeout=self.config.refresh_timeout_seconds)
                except FutureTimeout:
                    failures.append(f"{worker_id}: refresh timed out")
                except Exception as exc:
                    failures.append(f"{worker_id}: {exc}")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        if failures:
            self._detail = "; ".join(failures)

    @staticmethod
    def _worker_has_cuda(worker: dict[str, Any]) -> bool:
        snapshot = worker.get("resource_snapshot") or {}
        return any(
            accelerator.get("backend") == "cuda"
            for accelerator in snapshot.get("accelerators", [])
            if isinstance(accelerator, dict)
        )

    def _runtime_observation_is_fresh(self, observation: dict[str, Any] | None) -> bool:
        if not observation:
            return False
        try:
            from mncs_fabric.runtime import runtime_observation_is_fresh

            return runtime_observation_is_fresh(
                observation,
                max_age_seconds=self.config.runtime_probe_max_age_seconds,
            )
        except Exception:
            return False

    def _probe_runtime(self, worker_id: str) -> dict[str, Any]:
        if self.client is None:
            raise FabricUnavailable("Fabric client is unavailable")
        self.config.state_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="elh-fabric-probe-", dir=self.config.state_path.parent
        ) as directory:
            source_root = Path(directory)
            (source_root / "probe.py").write_text(_runtime_probe_script(), encoding="utf-8")
            from mncs_fabric.artifacts import build_manifest
            from mncs_fabric.bundles import build_bundle_archive
            from mncs_fabric.models import validate_job_plan

            manifest = build_manifest(source_root)
            archive = source_root / "execution-bundle.zip"
            build_bundle_archive(source_root, archive)
            plan = validate_job_plan(
                {
                    "schema_version": "mncs-fabric.job-plan.v0.1",
                    "job_id": "elh-cuda-runtime-probe",
                    "candidate_identity": _identity(
                        {"worker_id": worker_id, "probe": "synchronized-cuda-v1"}
                    ),
                    "evaluator_identity": None,
                    "artifact_manifest_identity": manifest["manifest_identity"],
                    "argv": ["@python", "probe.py"],
                    "working_directory": ".",
                    "timeout_seconds": self.config.runtime_probe_timeout_seconds,
                    "output_limit_bytes": 256 * 1024,
                    "environment": {"PYTHONHASHSEED": "0"},
                    "required_capabilities": ["python"],
                    "result_paths": [],
                    "network_policy": "DECLARED_OFFLINE",
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
            reason = result.get("reason") or record.get("termination_reason") or "probe failed"
            raise FabricExecutionError(f"CUDA runtime probe failed on {worker_id}: {reason}")
        stdout = ((record.get("stdout") or {}).get("captured_utf8") or "").splitlines()
        response_line = next(
            (line for line in stdout if line.startswith("ELH_FABRIC_RUNTIME_PROBE ")),
            None,
        )
        if response_line is None:
            raise FabricExecutionError(f"CUDA runtime probe returned no result on {worker_id}")
        try:
            probe = json.loads(response_line.removeprefix("ELH_FABRIC_RUNTIME_PROBE "))
        except json.JSONDecodeError as exc:
            raise FabricExecutionError(f"CUDA runtime probe returned invalid JSON on {worker_id}") from exc
        return self.client.ingest_runtime_observation(worker_id, probe)

    def _ensure_cuda_runtime_observations(self, *, force: bool = False) -> list[str]:
        if self.client is None or not self.config.runtime_probe_on_refresh:
            return []
        try:
            workers = [dict(worker) for worker in self.client.workers()]
        except Exception as exc:
            return [f"runtime probe worker inspection failed: {exc}"]
        failures: list[str] = []
        for worker in workers:
            if worker.get("source") != "remote" or worker.get("availability") != "AVAILABLE":
                continue
            if not self._worker_has_cuda(worker):
                continue
            observation = worker.get("runtime_observation")
            if not force and self._runtime_observation_is_fresh(observation):
                continue
            worker_id = str(worker.get("worker_id"))
            try:
                refreshed = self._probe_runtime(worker_id)
                if refreshed.get("runtime_execution_probe") != "PASS":
                    failures.append(
                        f"{worker_id}: CUDA execution probe "
                        f"{refreshed.get('runtime_execution_probe', 'UNKNOWN')}"
                    )
            except Exception as exc:
                failures.append(f"{worker_id}: CUDA runtime probe failed: {exc}")
        return failures

    def refresh(self) -> FabricStatus:
        if self._state == "available":
            self._refresh_remote_workers()
            failures = self._ensure_cuda_runtime_observations()
            if failures:
                existing = [self._detail] if self._detail else []
                self._detail = "; ".join([*existing, *failures])
        return self.status()

    def status(self) -> FabricStatus:
        workers: tuple[dict[str, Any], ...] = ()
        if self.client is not None:
            try:
                workers = tuple(dict(worker) for worker in self.client.workers())
            except Exception as exc:
                return FabricStatus(
                    self.config.enabled,
                    "unavailable",
                    self.config.controller_id,
                    detail=str(exc),
                    last_inference=self.last_inference,
                )
        return FabricStatus(
            self.config.enabled,
            self._state,
            self.config.controller_id,
            workers,
            self._detail,
            self.last_inference,
        )

    def _placement(self, model: ModelConfig) -> Any:
        from mncs_fabric.api import PlacementRequest

        return PlacementRequest(
            execution_device=model.execution_device,
            accelerator_backend=model.accelerator_backend,
            offload=model.offload,
            precision=model.precision,
            model_storage_bytes=model.model_storage_bytes,
            estimated_workspace_bytes=model.estimated_workspace_bytes,
            minimum_host_memory_bytes=model.minimum_host_memory_bytes,
            gpu_reserve_bytes=model.gpu_reserve_bytes,
            maximum_vram_bytes=model.maximum_vram_bytes,
            minimum_accelerator_working_bytes=model.minimum_accelerator_working_bytes,
            runtime_supports_sequential_cpu_offload=model.runtime_supports_sequential_cpu_offload,
            required_capabilities=model.required_capabilities,
            resource_max_age_seconds=model.resource_max_age_seconds,
        )

    def chat(
        self,
        model: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        images: list[Path] | None = None,
        *,
        worker_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.client is None or self._state != "available":
            raise FabricUnavailable(self._detail or "Fabric is unavailable")
        self.last_execution_record = None
        if model.execution_device == "accelerator" or model.accelerator_backend == "cuda":
            failures = self._ensure_cuda_runtime_observations()
            if failures:
                self._detail = "; ".join(failures)
        if not images:
            encoded_images: list[str] = []
        else:
            encoded_images = OllamaClient.encode_images(images)
        prepared = [dict(message) for message in messages]
        if encoded_images:
            for message in reversed(prepared):
                if message.get("role") == "user":
                    message["images"] = encoded_images
                    break
        payload: dict[str, Any] = {
            "model": model.name,
            "messages": prepared,
            "stream": False,
            "keep_alive": model.keep_alive,
            "think": model.think,
            "options": {
                "num_ctx": model.num_ctx,
                "temperature": model.temperature,
                "top_p": model.top_p,
                "top_k": model.top_k,
            },
        }
        if tools:
            payload["tools"] = tools
        request = {
            "base_url": _loopback_url(self.config.provider_ollama_base_url),
            "timeout_seconds": self.config.provider_timeout_seconds,
            "payload": payload,
        }
        request_identity = _identity(request)
        self.config.state_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="elh-fabric-", dir=self.config.state_path.parent) as directory:
            source_root = Path(directory)
            (source_root / "invoke.py").write_text(_invocation_script(), encoding="utf-8")
            (source_root / "request.json").write_text(
                json.dumps(request, ensure_ascii=True, separators=(",", ":")), encoding="utf-8"
            )
            local_roots = [
                worker.bundle_root or self.config.worker_bundle_root
                for worker in self.config.workers
                if worker.kind == "local"
            ]
            for local_root in local_roots:
                local_root.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_root / "invoke.py", local_root / "invoke.py")
                shutil.copyfile(source_root / "request.json", local_root / "request.json")
            try:
                from mncs_fabric.artifacts import build_manifest
                from mncs_fabric.bundles import build_bundle_archive
                from mncs_fabric.models import validate_job_plan

                manifest = build_manifest(source_root)
                archive = source_root / "execution-bundle.zip"
                build_bundle_archive(source_root, archive)
                plan = validate_job_plan(
                    {
                        "schema_version": "mncs-fabric.job-plan.v0.1",
                        "job_id": "elh-fabric-inference",
                        "candidate_identity": _identity(request),
                        "evaluator_identity": None,
                        "artifact_manifest_identity": manifest["manifest_identity"],
                        "argv": ["@python", "invoke.py"],
                        "working_directory": ".",
                        "timeout_seconds": (
                            self.config.provider_timeout_seconds
                            + self.config.job_timeout_overhead_seconds
                        ),
                        "output_limit_bytes": 2 * 1024 * 1024,
                        "environment": {"PYTHONHASHSEED": "0"},
                        "required_capabilities": list(
                            dict.fromkeys(("python", *model.required_capabilities))
                        ),
                        "result_paths": [],
                        "network_policy": "UNRESTRICTED",
                    }
                )
                started = time.perf_counter()
                try:
                    consumer_context = None
                    if self._consumer_context is not None:
                        from mncs_fabric import ConsumerContext

                        consumer_context = ConsumerContext(
                            "epi13-local-harness",
                            self._consumer_context["workload_identity"],
                            provider_identity=self._consumer_context["provider_identity"],
                            partition_identity=self._consumer_context["partition_identity"],
                        )
                    result = self.client.execute(
                        plan,
                        manifest,
                        worker_id=worker_id,
                        placement=self._placement(model),
                        execution_bundle_archive=archive,
                        consumer_context=consumer_context,
                    )[0]
                except FabricExecutionError:
                    raise
                except Exception as exc:
                    raise FabricExecutionError(f"Fabric dispatch failed: {exc}") from exc
                elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
            finally:
                for local_root in local_roots:
                    for filename in ("invoke.py", "request.json"):
                        (local_root / filename).unlink(missing_ok=True)
        record = result.get("record") or {}
        self.last_execution_record = dict(record) if isinstance(record, dict) and record else None
        admission = result.get("placement_admission") or {}
        self.last_inference = {
            "worker": result.get("worker_identity"),
            "placement": admission.get("admission_mode"),
            "precision": admission.get("precision"),
            "disposition": result.get("disposition"),
            "reason": result.get("reason") or admission.get("reason"),
            "request_identity": result.get("request_identity"),
        }
        if result.get("disposition") != "EXECUTED" or record.get("outcome") != "PASS":
            reason = result.get("reason") or record.get("termination_reason") or "Fabric execution failed"
            raise FabricExecutionError(str(reason))
        stdout = ((record.get("stdout") or {}).get("captured_utf8") or "").splitlines()
        response_line = next((line for line in stdout if line.startswith("ELH_FABRIC_RESPONSE ")), None)
        if response_line is None:
            raise FabricExecutionError("Fabric execution returned no provider response")
        try:
            response = json.loads(response_line.removeprefix("ELH_FABRIC_RESPONSE "))
        except json.JSONDecodeError as exc:
            raise FabricExecutionError("Fabric provider response was invalid JSON") from exc
        metadata = {
            "provider": "ollama-via-mncs-fabric",
            "backend": "ollama",
            "fabric_enabled": True,
            "fabric_worker": result.get("worker_identity"),
            "execution_source": (
                "local"
                if any(
                    worker.worker_id == result.get("worker_identity") and worker.kind == "local"
                    for worker in self.config.workers
                )
                else "remote"
            ),
            "accelerator_backend": model.accelerator_backend,
            "placement_mode": admission.get("admission_mode"),
            "precision": admission.get("precision"),
            "placement_reason": admission.get("reason"),
            "placement_reason_code": admission.get("reason_code"),
            "fabric_request_identity": result.get("request_identity"),
            "resource_snapshot_identity": (result.get("resource_snapshot") or {}).get(
                "resource_snapshot_identity"
            ),
            "runtime_observation_identity": (result.get("runtime_observation") or {}).get(
                "runtime_observation_identity"
            ),
            "fabric_record_identity": result.get("record_identity"),
            "fabric_receipt_identity": result.get("receipt_identity"),
            "fabric_consumer_context_identity": result.get("consumer_context_identity"),
            "fabric_provenance_binding_identity": (
                result.get("provenance_binding") or {}
            ).get("binding_identity"),
            "fabric_dispatch_ms": elapsed_ms,
            "request_identity": request_identity,
        }
        return response, metadata
