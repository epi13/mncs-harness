"""Unified operator/runtime view of controller and Fabric model availability."""

from __future__ import annotations

from typing import Any

from .models import HarnessConfig
from .ollama import OllamaClient, OllamaError
from .residency import ResidencyManager
from .semantic_router import router_status


class FleetService:
    """One source of truth consumed by routing, CLI, TUI, Doctor, and Models."""

    def __init__(self, config: HarnessConfig, fabric_session: Any) -> None:
        self.config = config
        self.fabric_session = fabric_session
        self.local_ollama = OllamaClient(config.ollama)
        self.residency = ResidencyManager(config, fabric_session)

    @staticmethod
    def _name(model: dict[str, Any]) -> str:
        return str(model.get("name") or model.get("model") or "")

    def snapshot(self) -> dict[str, Any]:
        fabric = self.fabric_session.status()
        try:
            installed = self.local_ollama.tags()
            running = self.local_ollama.running()
            local_error = None
        except OllamaError as exc:
            installed = []
            running = []
            local_error = str(exc)
        running_names = {self._name(model) for model in running}
        local_models = [
            {**dict(model), "loaded": self._name(model) in running_names}
            for model in installed
        ]
        assignments = {
            item["worker_id"]: item for item in self.residency.plan(fabric)
        }
        workers: list[dict[str, Any]] = []
        for source in fabric.workers:
            worker = dict(source)
            description = worker.get("description") or {}
            node = description.get("node") or {}
            resources = worker.get("resource_snapshot") or {}
            inventory = [
                dict(model)
                for model in worker.get("model_inventory") or []
                if isinstance(model, dict)
            ]
            workers.append(
                {
                    **worker,
                    "architecture": node.get("architecture") or resources.get("architecture"),
                    "worker_service_version": description.get("worker_service_version"),
                    "model_inventory": inventory,
                    "installed_model_count": len(inventory),
                    "loaded_model_names": sorted(
                        self._name(model) for model in inventory if model.get("loaded")
                    ),
                    "residency": assignments.get(str(worker.get("worker_id"))),
                }
            )
        semantic = router_status(self.config)
        return {
            "controller": {
                "generation_policy": self.config.controller.generation_policy,
                "ollama_error": local_error,
                "installed_models": local_models,
                "installed_model_count": len(local_models),
                "loaded_generation_models": sorted(running_names),
                "generation_model_loaded": bool(running_names),
                "semantic_router": {
                    "state": semantic.state,
                    "active": semantic.active,
                    "cached": semantic.cached,
                    "model": semantic.model,
                    "device": semantic.device,
                },
            },
            "fabric": {
                "state": fabric.state,
                "detail": fabric.detail,
                "registered_worker_count": len(workers),
                "reachable_worker_count": sum(
                    1 for worker in workers if worker.get("availability") == "AVAILABLE"
                ),
                "workers": workers,
            },
            "residency": {
                "enabled": self.config.model_residency.enabled,
                "warm_on_startup": self.config.model_residency.warm_on_startup,
                "assignments": list(assignments.values()),
            },
        }

    def refresh(self, *, reconcile: bool = True) -> dict[str, Any]:
        refresher = getattr(self.fabric_session, "refresh_model_inventory", None)
        if callable(refresher):
            refresher()
        if reconcile and self.config.model_residency.enabled:
            self.residency.reconcile()
        return self.snapshot()

