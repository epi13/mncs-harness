"""Harness-owned resident generation-model policy and reconciliation."""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import UTC, datetime
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

    @staticmethod
    def _provider(model: dict[str, Any]) -> str:
        return str(model.get("provider") or model.get("namespace") or "ollama")

    @staticmethod
    def _loaded_names(inventory: list[dict[str, Any]]) -> list[str]:
        return sorted(
            name
            for item in inventory
            if item.get("loaded") and (name := ResidencyManager._model_name(item))
        )

    def _observation_freshness(
        self,
        worker: dict[str, Any],
    ) -> tuple[bool | None, float | None]:
        captured_at = (worker.get("capability_observation") or {}).get("captured_at")
        if not isinstance(captured_at, str):
            return None, None
        try:
            captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
            age = max(0.0, (datetime.now(UTC) - captured.astimezone(UTC)).total_seconds())
        except ValueError:
            return False, None
        return age <= self.policy.observation_max_age_seconds, age

    def _resource_decision(
        self,
        worker: dict[str, Any],
        model: dict[str, Any],
        *,
        require_available: bool = False,
    ) -> tuple[bool, str | None, dict[str, Any]]:
        resources = worker.get("resource_snapshot") or {}
        total = resources.get("host_memory_total_bytes")
        available = resources.get("host_memory_available_bytes")
        size = model.get("size")
        accelerators = [
            item for item in resources.get("accelerators") or [] if isinstance(item, dict)
        ]
        facts = {
            "model_size_bytes": size if isinstance(size, int) else None,
            "host_memory_total_bytes": total if isinstance(total, int) else None,
            "host_memory_available_bytes": available if isinstance(available, int) else None,
            "accelerator_total_memory_bytes": sum(
                int(item.get("total_memory_bytes") or 0) for item in accelerators
            ) or None,
            "accelerator_free_memory_bytes": sum(
                int(item.get("free_memory_bytes") or 0) for item in accelerators
            ) or None,
        }
        if (
            not isinstance(total, int)
            or not isinstance(size, int)
            or total <= 0
            or size <= 0
            or (require_available and (not isinstance(available, int) or available <= 0))
        ):
            return False, "bounded model-size and current host-memory facts are required", facts
        maximum = int(total * self.policy.maximum_model_memory_fraction)
        facts["policy_model_memory_budget_bytes"] = maximum
        if size > maximum:
            return (
                False,
                f"model storage size {size} exceeds conservative policy budget {maximum}",
                facts,
            )
        if isinstance(available, int) and available > 0 and size > available:
            return (
                False,
                f"model storage size {size} exceeds currently available host memory {available}",
                facts,
            )
        return True, None, facts

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
                "policy_mode": "background-pinned",
                "keep_alive": self.policy.keep_alive,
                "warm": False,
                "pinned": False,
                "observation_captured_at": (
                    (worker.get("capability_observation") or {}).get("captured_at")
                ),
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
            resource_ok, resource_detail, resource_facts = self._resource_decision(
                worker, model
            )
            selected_base = {
                **base,
                "resident_model": selected,
                "selection_source": source_reason,
                "installed": True,
                "loaded": bool(model.get("loaded", False)),
                "warm": bool(model.get("loaded", False)),
                "pinned": bool(model.get("loaded", False)),
                "provider": self._provider(model),
                "expires_at": model.get("expires_at"),
                **resource_facts,
            }
            if not resource_ok:
                assignments.append(
                    {
                        **selected_base,
                        "code": (
                            "RESIDENCY_RESOURCE_EVIDENCE_UNKNOWN"
                            if resource_facts.get("model_size_bytes") is None
                            or resource_facts.get("host_memory_available_bytes") is None
                            else "RESIDENCY_RESOURCE_POLICY_REJECTED"
                        ),
                        "detail": resource_detail,
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

    def prepare_experiment(
        self,
        experiment_id: str,
        assignments: list[dict[str, str]],
        *,
        prior_leases: tuple[dict[str, Any], ...] = (),
    ) -> tuple[dict[str, Any], ...]:
        """Warm exact experiment assignments and return provider-backed leases.

        Conversation messages are deliberately absent from this operation. The
        controller remains authoritative for context; this lifecycle concerns
        worker-local model weights only.
        """

        if (
            not experiment_id
            or len(experiment_id) > 256
            or any(ord(character) < 32 for character in experiment_id)
        ):
            raise ValueError("experiment residency identity is invalid")
        requested: dict[tuple[str, str], dict[str, str]] = {}
        by_worker: dict[str, set[str]] = defaultdict(set)
        for item in assignments:
            worker_id = str(item.get("worker_id") or "")
            model_name = str(item.get("model") or "")
            if not worker_id or not model_name:
                raise ValueError("experiment residency assignments require worker_id and model")
            requested[(worker_id, model_name)] = dict(item)
            by_worker[worker_id].add(model_name)
        overflow = {
            worker: sorted(models)
            for worker, models in by_worker.items()
            if len(models) > self.policy.max_pinned_models_per_worker
        }
        if overflow:
            return tuple(
                {
                    "outcome": "FAIL",
                    "code": "RESIDENCY_PIN_LIMIT_EXCEEDED",
                    "worker_id": worker,
                    "models": models,
                    "detail": (
                        f"requested {len(models)} models exceeds per-worker pin limit "
                        f"{self.policy.max_pinned_models_per_worker}"
                    ),
                }
                for worker, models in sorted(overflow.items())
            )
        refresher = getattr(self.session, "refresh_model_inventory", None)
        try:
            if callable(refresher):
                status = refresher()
            else:
                status = self.session.status()
        except Exception as exc:
            return tuple(
                {
                    "outcome": "FAIL",
                    "code": "RESIDENCY_INVENTORY_REFRESH_FAILED",
                    "worker_id": worker_id,
                    "model": model_name,
                    "detail": str(exc),
                }
                for worker_id, model_name in sorted(requested)
            )
        workers = {str(item.get("worker_id")): dict(item) for item in status.workers}
        prior = {
            (str(item.get("worker_id")), str(item.get("model"))): dict(item)
            for item in prior_leases
        }
        results: list[dict[str, Any]] = []
        for (worker_id, model_name), assignment in sorted(requested.items()):
            worker = workers.get(worker_id)
            if worker is None or worker.get("availability") != "AVAILABLE":
                results.append({
                    "outcome": "FAIL",
                    "code": "RESIDENCY_WORKER_UNAVAILABLE",
                    "worker_id": worker_id,
                    "model": model_name,
                    "detail": "assigned worker is unavailable or unknown",
                })
                continue
            observation_fresh, observation_age = self._observation_freshness(worker)
            if observation_fresh is False:
                results.append({
                    "outcome": "FAIL",
                    "code": "RESIDENCY_OBSERVATION_STALE",
                    "worker_id": worker_id,
                    "model": model_name,
                    "observation_age_seconds": observation_age,
                    "observation_max_age_seconds": self.policy.observation_max_age_seconds,
                    "detail": "provider observation exceeds experiment residency freshness policy",
                })
                continue
            if worker.get("model_inventory_status") != "CURRENT":
                results.append({
                    "outcome": "FAIL",
                    "code": "RESIDENCY_INVENTORY_NOT_CURRENT",
                    "worker_id": worker_id,
                    "model": model_name,
                    "detail": "live provider inventory is not current",
                })
                continue
            inventory = [
                dict(item) for item in worker.get("model_inventory") or []
                if isinstance(item, dict)
            ]
            model = next(
                (item for item in inventory if self._model_name(item) == model_name), None
            )
            if model is None:
                results.append({
                    "outcome": "FAIL",
                    "code": "RESIDENCY_MODEL_NOT_INSTALLED",
                    "worker_id": worker_id,
                    "model": model_name,
                    "detail": "assigned model is absent from the current provider inventory",
                })
                continue
            provider = self._provider(model)
            capability = self.session.residency_capability(provider)
            if not capability.get("supported"):
                results.append({
                    "outcome": "FAIL",
                    "code": "RESIDENCY_PROVIDER_UNSUPPORTED",
                    "worker_id": worker_id,
                    "model": model_name,
                    "provider": provider,
                    "detail": "provider does not advertise warm/observe/release lifecycle actions",
                })
                continue
            resource_ok, resource_detail, resource_facts = self._resource_decision(
                worker, model, require_available=True
            )
            if not resource_ok:
                results.append({
                    "outcome": "FAIL",
                    "code": "RESIDENCY_RESOURCE_POLICY_REJECTED",
                    "worker_id": worker_id,
                    "model": model_name,
                    "provider": provider,
                    "detail": resource_detail,
                    **resource_facts,
                })
                continue
            loaded_names = self._loaded_names(inventory)
            conflicts = [name for name in loaded_names if name != model_name]
            if conflicts and self.policy.reject_conflicting_loaded_models and not model.get("loaded"):
                results.append({
                    "outcome": "FAIL",
                    "code": "RESIDENCY_CONFLICTING_LOADED_MODELS",
                    "worker_id": worker_id,
                    "model": model_name,
                    "provider": provider,
                    "conflicting_loaded_models": conflicts,
                    "detail": "policy refuses implicit eviction or model thrash",
                    **resource_facts,
                })
                continue
            try:
                evidence = self.session.establish_model_residency(
                    worker_id,
                    model_name,
                    provider_namespace=provider,
                    keep_alive=self.policy.experiment_keep_alive,
                    timeout_seconds=self.policy.warm_timeout_seconds,
                    policy_mode="experiment-pinned",
                    experiment_id=experiment_id,
                )
            except Exception as exc:
                results.append({
                    "outcome": "FAIL",
                    "code": "RESIDENCY_WARM_FAILED",
                    "worker_id": worker_id,
                    "model": model_name,
                    "provider": provider,
                    "detail": str(exc),
                    **resource_facts,
                })
                continue
            prior_lease = prior.get((worker_id, model_name), {})
            preexisting = bool(evidence.get("preexisting_loaded"))
            managed = bool(prior_lease.get("managed")) or not preexisting
            results.append({
                "outcome": "PASS",
                "code": "RESIDENCY_REUSED" if preexisting else "RESIDENCY_WARMED",
                "experiment_id": experiment_id,
                "worker_id": worker_id,
                "model": model_name,
                "role": assignment.get("role"),
                "provider": provider,
                "policy_mode": "experiment-pinned",
                "keep_alive": self.policy.experiment_keep_alive,
                "loaded": True,
                "warm": True,
                "pinned": True,
                "preexisting_loaded": preexisting,
                "owned": not preexisting,
                "managed": managed,
                "observation_captured_at": (
                    (worker.get("capability_observation") or {}).get("captured_at")
                ),
                "observation_age_seconds": observation_age,
                "evidence": evidence,
                **resource_facts,
            })
        return tuple(results)

    def release_experiment(
        self,
        experiment_id: str,
        leases: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], ...]:
        """Release exactly the experiment-managed leases; safe to repeat."""

        results: list[dict[str, Any]] = []
        for lease in leases:
            worker_id = str(lease.get("worker_id") or "")
            model_name = str(lease.get("model") or "")
            provider = str(lease.get("provider") or "ollama")
            if not lease.get("managed"):
                results.append({
                    "outcome": "PASS",
                    "code": "RESIDENCY_LEFT_PREEXISTING",
                    "worker_id": worker_id,
                    "model": model_name,
                    "provider": provider,
                    "released": False,
                })
                continue
            try:
                evidence = self.session.release_model_residency(
                    worker_id,
                    model_name,
                    provider_namespace=provider,
                    timeout_seconds=self.policy.warm_timeout_seconds,
                    experiment_id=experiment_id,
                )
                results.append({
                    "outcome": "PASS",
                    "code": (
                        "RESIDENCY_RELEASED"
                        if evidence.get("preexisting_loaded")
                        else "RESIDENCY_ALREADY_RELEASED"
                    ),
                    "worker_id": worker_id,
                    "model": model_name,
                    "provider": provider,
                    "released": bool(evidence.get("preexisting_loaded")),
                    "loaded": False,
                    "remaining_loaded_models": evidence.get("remaining_loaded_models", []),
                    "evidence": evidence,
                })
            except Exception as exc:
                results.append({
                    "outcome": "UNKNOWN",
                    "code": "RESIDENCY_RELEASE_FAILED",
                    "worker_id": worker_id,
                    "model": model_name,
                    "provider": provider,
                    "released": False,
                    "detail": str(exc),
                })
        return tuple(results)

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
