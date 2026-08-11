"""Harness-owned resident generation-model policy and reconciliation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from .fabric import FabricStatus
from .models import HarnessConfig


class ResidencyManager:
    """Select at most one conservative resident generation model per worker."""

    def __init__(self, config: HarnessConfig, session: Any) -> None:
        self.config = config
        self.policy = config.model_residency
        self.session = session
        self.last_results: dict[str, dict[str, Any]] = {}

    def _explicit(self) -> dict[str, str | None]:
        return {item.worker_id: item.model for item in self.policy.workers}

    @staticmethod
    def _model_name(model: dict[str, Any]) -> str:
        return str(model.get("name") or model.get("model") or "")

    def _choose_model(
        self,
        worker: dict[str, Any],
        inventory: list[dict[str, Any]],
    ) -> tuple[str | None, str]:
        explicit = self._explicit()
        if worker["worker_id"] in explicit:
            return explicit[worker["worker_id"]], "operator-explicit"
        installed = {self._model_name(model): model for model in inventory}
        for role in self.policy.role_preference:
            configured = self.config.models.get(role)
            if configured is not None and configured.name in installed:
                return configured.name, f"configured-role:{role}"
        return None, "no configured model is installed on this worker"

    def plan(self, status: FabricStatus | None = None) -> tuple[dict[str, Any], ...]:
        if status is None:
            status = self.session.status()
        assignments: list[dict[str, Any]] = []
        for source in sorted(status.workers, key=lambda item: str(item.get("worker_id"))):
            worker = dict(source)
            worker_id = str(worker.get("worker_id") or "")
            if worker.get("source") == "local" or not worker_id:
                continue
            base: dict[str, Any] = {
                "worker_id": worker_id,
                "availability": worker.get("availability", "UNKNOWN"),
                "resident_model": None,
                "selection_source": None,
                "installed": False,
                "loaded": False,
                "outcome": "UNKNOWN",
                "code": "RESIDENCY_UNKNOWN",
                "detail": None,
            }
            if not self.policy.enabled:
                assignments.append(
                    {**base, "code": "RESIDENCY_DISABLED", "detail": "policy is disabled"}
                )
                continue
            if worker.get("availability") != "AVAILABLE":
                assignments.append(
                    {
                        **base,
                        "code": "RESIDENCY_WORKER_UNAVAILABLE",
                        "detail": "worker is known but unavailable",
                    }
                )
                continue
            if worker.get("model_inventory_status") != "CURRENT":
                assignments.append(
                    {
                        **base,
                        "code": "RESIDENCY_INVENTORY_NOT_CURRENT",
                        "detail": "installed/loaded model inventory is not current",
                    }
                )
                continue
            inventory = [
                dict(model)
                for model in worker.get("model_inventory") or []
                if isinstance(model, dict)
            ]
            selected, source_reason = self._choose_model(worker, inventory)
            if selected is None:
                assignments.append(
                    {
                        **base,
                        "selection_source": source_reason,
                        "code": "RESIDENCY_NO_ELIGIBLE_MODEL",
                        "detail": source_reason,
                    }
                )
                continue
            model = next(
                (item for item in inventory if self._model_name(item) == selected), None
            )
            if model is None:
                assignments.append(
                    {
                        **base,
                        "resident_model": selected,
                        "selection_source": source_reason,
                        "code": "RESIDENCY_MODEL_NOT_INSTALLED",
                        "detail": "selected resident model is not installed on the worker",
                    }
                )
                continue
            resources = worker.get("resource_snapshot") or {}
            memory = resources.get("host_memory_total_bytes")
            size = model.get("size")
            selected_base = {
                **base,
                "resident_model": selected,
                "selection_source": source_reason,
                "installed": True,
                "loaded": bool(model.get("loaded", False)),
                "model_size_bytes": size if isinstance(size, int) else None,
                "host_memory_total_bytes": memory if isinstance(memory, int) else None,
            }
            if not isinstance(memory, int) or not isinstance(size, int) or memory <= 0 or size <= 0:
                assignments.append(
                    {
                        **selected_base,
                        "code": "RESIDENCY_RESOURCE_EVIDENCE_UNKNOWN",
                        "detail": "bounded model-size and host-memory facts are required",
                    }
                )
                continue
            maximum = int(memory * self.policy.maximum_model_memory_fraction)
            if size > maximum:
                assignments.append(
                    {
                        **selected_base,
                        "code": "RESIDENCY_INSUFFICIENT_RESOURCE_EVIDENCE",
                        "detail": (
                            f"model storage size {size} exceeds conservative policy budget {maximum}"
                        ),
                    }
                )
                continue
            assignments.append(
                {
                    **selected_base,
                    "outcome": "PASS",
                    "code": "RESIDENCY_LOADED" if model.get("loaded") else "RESIDENCY_READY_TO_WARM",
                    "detail": None,
                }
            )
        return tuple(assignments)

    def reconcile(self, *, force_worker: str | None = None) -> tuple[dict[str, Any], ...]:
        plan = list(self.plan())
        pending = [
            item
            for item in plan
            if item["code"] == "RESIDENCY_READY_TO_WARM"
            and (force_worker is None or item["worker_id"] == force_worker)
        ]
        if force_worker is not None and not any(
            item["worker_id"] == force_worker for item in plan
        ):
            return (
                {
                    "worker_id": force_worker,
                    "outcome": "UNKNOWN",
                    "code": "RESIDENCY_WORKER_UNKNOWN",
                    "detail": "worker is not present in the fleet view",
                },
            )
        if not pending:
            return tuple(
                item for item in plan if force_worker is None or item["worker_id"] == force_worker
            )

        def warm(item: dict[str, Any]) -> dict[str, Any]:
            try:
                evidence = self.session.warm_model(
                    item["worker_id"],
                    item["resident_model"],
                    keep_alive=self.policy.keep_alive,
                    timeout_seconds=self.policy.warm_timeout_seconds,
                )
                return {
                    **item,
                    "outcome": "PASS",
                    "code": "RESIDENCY_WARMED",
                    "loaded": True,
                    "evidence": evidence,
                }
            except Exception as exc:
                return {
                    **item,
                    "outcome": "UNKNOWN",
                    "code": "RESIDENCY_WARM_FAILED",
                    "loaded": False,
                    "detail": str(exc),
                }

        executor = ThreadPoolExecutor(max_workers=min(8, len(pending)))
        futures = {executor.submit(warm, item): item for item in pending}
        warmed: dict[str, dict[str, Any]] = {}
        try:
            for future, item in futures.items():
                try:
                    result = future.result(timeout=self.policy.warm_timeout_seconds + 10)
                except FutureTimeout:
                    result = {
                        **item,
                        "outcome": "UNKNOWN",
                        "code": "RESIDENCY_WARM_TIMEOUT",
                        "detail": "resident warm exceeded the controller bound",
                    }
                warmed[item["worker_id"]] = result
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        self.last_results.update(warmed)
        return tuple(
            warmed.get(item["worker_id"], item)
            for item in plan
            if force_worker is None or item["worker_id"] == force_worker
        )

