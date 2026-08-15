from __future__ import annotations

import hashlib
import inspect
import json
import sys
from pathlib import Path
from typing import Any

from .capability_graph import build_capability_graph
from .commons import CommonsError, CommonsSession, CommonsStatus
from .fabric import FabricStatus
from .fabric_inventory_session import InventoryAwareFabricSession
from .fleet import FleetService
from .metrics import MetricsStore
from .models import (
    AgentResult,
    HarnessConfig,
    ModelAttempt,
    RoutingOverride,
    SessionTargets,
    VerificationResult,
)
from .ollama import OllamaClient, OllamaError
from .prompts import system_prompt
from .provider import FabricOllamaProvider, LocalOllamaProvider, ProviderError
from .router import plan_route
from .tools import ToolRegistry
from .verifiers import Verifier


class LocalAgent:
    def __init__(
        self,
        config: HarnessConfig,
        *,
        refresh_inventory: bool = True,
        warm_residency: bool | None = None,
    ):
        self.config = config
        # ``client`` remains a compatibility seam used by existing callers and
        # tests. Provider selection is performed per model role below.
        self.client = OllamaClient(config.ollama)
        self.fabric_session = InventoryAwareFabricSession(
            config.fabric, residency_config=config.model_residency
        )
        self.fabric_session.initialize(refresh_inventory=refresh_inventory)
        self.commons_session = CommonsSession(config.commons)
        self.commons_session.initialize()
        self.metrics = MetricsStore(config.metrics.path, config.metrics.store_prompt_text)
        self.fleet = FleetService(config, self.fabric_session)
        self._last_residency_results: tuple[dict[str, Any], ...] = ()
        self._lifecycle_stages: list[dict[str, Any]] = []
        should_warm = (
            config.model_residency.enabled
            and (
                config.model_residency.warm_on_startup
                if warm_residency is None
                else warm_residency
            )
        )
        if should_warm:
            self._last_residency_results = self.fleet.residency.reconcile()

    def fabric_status(self) -> FabricStatus:
        return self.fabric_session.status()

    def commons_status(self) -> CommonsStatus:
        return self.commons_session.status()

    def capability_graph(self, workspace: Path | None = None) -> dict[str, object]:
        return build_capability_graph(
            self.fabric_status(),
            workspace=workspace,
            controller_tools=tuple(
                sorted({tool for model in self.config.models.values() for tool in model.tools})
            ),
            commons_status=self.commons_status(),
        )

    @staticmethod
    def _provenance_identity(value: object) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def _configure_fabric_provenance(self, task: str, role: str, model: object) -> None:
        setter = getattr(self.fabric_session, "set_consumer_context", None)
        if not callable(setter):
            return
        workload = self._provenance_identity(
            {"source_project": "mncs-harness", "task_fingerprint": hashlib.sha256(task.encode("utf-8")).hexdigest()}
        )
        setter(
            workload_identity=workload,
            provider_identity=self._provenance_identity(
                {"provider": getattr(model, "provider", "unknown"), "model": getattr(model, "name", "unknown")}
            ),
            partition_identity=self._provenance_identity(
                {"workload": workload, "role": role}
            ),
        )

    def _publish_fabric_evidence(self, metadata: dict[str, Any]) -> None:
        if not self.config.commons.publish_fabric_evidence or not self.commons_session.ready:
            return
        record = getattr(self.fabric_session, "last_execution_record", None)
        if not isinstance(record, dict):
            return
        try:
            result = self.commons_session.publish_fabric_evidence(record)
            receipt = result.get("receipt") if isinstance(result, dict) else None
            translated_result = result.get("translated") if isinstance(result, dict) else None
            translated = (
                translated_result.get("record")
                if isinstance(translated_result, dict)
                else None
            )
            metadata["commons_evidence_publication"] = "PUBLISHED"
            metadata["commons_evidence_receipt"] = (
                receipt.get("contentDigest") if isinstance(receipt, dict) else None
            )
            metadata["commons_evidence_verification_status"] = (
                translated.get("details", {}).get("claimVerificationStatus")
                if isinstance(translated, dict)
                else None
            )
        except CommonsError as exc:
            metadata["commons_evidence_publication"] = exc.code
            metadata["commons_evidence_error"] = exc.detail

    def _progress(self, stage: str, **fields: Any) -> None:
        payload = {"stage": stage, **fields}
        stages = getattr(self, "_lifecycle_stages", None)
        if stages is None:
            self._lifecycle_stages = [payload]
        else:
            stages.append(payload)
        extras = " ".join(
            f"{key}={value}"
            for key, value in fields.items()
            if value is not None and not isinstance(value, (list, dict))
        )
        line = f"elh: {stage}" + (f" {extras}" if extras else "")
        print(line, file=sys.stderr, flush=True)

    @staticmethod
    def _exact_manual_route(override: RoutingOverride | None) -> bool:
        return override is not None and override.mode in {"MODEL", "WORKER", "WORKER_MODEL"}

    def _declared_resident(self, worker_id: str | None) -> str | None:
        if not worker_id:
            return None
        for item in self.config.model_residency.workers:
            if item.worker_id == worker_id:
                return item.model
        return None

    def refresh_fabric_inventory(
        self,
        *,
        reconcile: bool = True,
        worker_id: str | None = None,
    ) -> FabricStatus | None:
        """Actively refresh worker inventory. This is not a persistent read.

        Exact-pinned asks must not call this. Use ``status()`` for the current
        controller-projected inventory, ``elh fabric refresh`` for a probe, and
        ``elh residency warm`` for warming.
        """

        self._progress(
            "inventory-refresh",
            worker=worker_id or "fleet",
            reconcile=reconcile,
        )
        refresher = getattr(self.fabric_session, "refresh_model_inventory", None)
        if callable(refresher):
            refreshed = refresher()
            if reconcile and self.config.model_residency.enabled:
                self._progress("residency-reconciliation", worker=worker_id or "fleet")
                self._last_residency_results = self.fleet.residency.reconcile(
                    force_worker=worker_id
                )
                status = getattr(self.fabric_session, "status", None)
                if callable(status):
                    return status()
            return refreshed
        status = getattr(self.fabric_session, "status", None)
        if callable(status):
            return status()
        return None

    def _restore_residency_after_attempt(self, attempt: ModelAttempt) -> None:
        """Best-effort restore of one worker after a transient non-resident model.

        Exact pins of the declared resident model do nothing. Restoration never
        refreshes or warms the rest of the fleet, and never changes the
        completed inference result.
        """

        if (
            not self.config.model_residency.enabled
            or attempt.session_targets.inference.kind != "fabric-worker"
        ):
            return
        worker_id = attempt.session_targets.inference.worker_identity
        declared = self._declared_resident(worker_id)
        if declared is None:
            attempt.metrics["residency_reconciliation"] = [
                {
                    "worker_id": worker_id,
                    "resident_model": None,
                    "outcome": "PASS",
                    "code": "RESIDENCY_NOT_CONFIGURED",
                    "loaded": None,
                    "detail": "no declared resident model for this worker",
                }
            ]
            return
        if declared == attempt.model:
            attempt.metrics["residency_reconciliation"] = [
                {
                    "worker_id": worker_id,
                    "resident_model": declared,
                    "outcome": "PASS",
                    "code": "RESIDENCY_UNCHANGED",
                    "loaded": True,
                    "detail": "inference used the declared resident model; no restore",
                }
            ]
            return
        try:
            self._progress(
                "residency-restore",
                worker=worker_id,
                resident_model=declared,
                used_model=attempt.model,
            )
            results = self.fleet.residency.reconcile(force_worker=worker_id)
            self._last_residency_results = results
            attempt.metrics["residency_reconciliation"] = [
                {
                    key: item.get(key)
                    for key in (
                        "worker_id",
                        "resident_model",
                        "outcome",
                        "code",
                        "loaded",
                        "detail",
                    )
                }
                for item in results
            ]
        except Exception as exc:
            attempt.metrics["residency_reconciliation"] = [
                {
                    "worker_id": worker_id,
                    "outcome": "UNKNOWN",
                    "code": "RESIDENCY_POST_ATTEMPT_RECONCILE_FAILED",
                    "detail": str(exc),
                }
            ]

    def _provider_for_model(self, model, model_selection, targets: SessionTargets) -> object:
        local = LocalOllamaProvider(self.client)
        if self.config.fabric.enabled and model.provider == "fabric":
            worker_id = (
                model_selection.worker_id
                if model_selection is not None and model_selection.available
                else None
            )
            placement_error = (
                model_selection.reason
                if model_selection is not None and not model_selection.available
                else None
            )
            return FabricOllamaProvider(
                self.fabric_session,
                local,
                self.config.fabric.fallback_to_local,
                inference_worker_id=worker_id,
                placement_error=placement_error,
                session_targets=targets,
            )
        return local

    def _resolve_model(self, role: str, routing_override: RoutingOverride):
        configured_model = self.config.models[role]
        resolver = getattr(self.fabric_session, "resolve_model", None)
        if callable(resolver):
            try:
                parameters = inspect.signature(resolver).parameters.values()
                supports_override = any(
                    parameter.name == "routing_override"
                    or parameter.kind == inspect.Parameter.VAR_KEYWORD
                    for parameter in parameters
                )
            except (TypeError, ValueError):
                supports_override = False
            if supports_override:
                return resolver(role, configured_model, routing_override=routing_override)
            return resolver(role, configured_model)
        # Preserve the existing public/testing session seam. Inventory-aware
        # resolution is additive rather than a new requirement on custom sessions.
        return configured_model, None

    @staticmethod
    def _tool_call_parts(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        function = call.get("function", {})
        name = str(function.get("name", ""))
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        return name, arguments

    @staticmethod
    def _response_metrics(
        response: dict[str, Any], metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        keys = (
            "total_duration",
            "load_duration",
            "prompt_eval_count",
            "prompt_eval_duration",
            "eval_count",
            "eval_duration",
        )
        metrics = {key: response.get(key) for key in keys if key in response}
        if "total_duration" in metrics:
            metrics["provider_latency_ms"] = metrics["total_duration"] / 1_000_000
        if metrics.get("eval_count") and metrics.get("eval_duration"):
            metrics["tokens_per_second"] = metrics["eval_count"] / (
                metrics["eval_duration"] / 1_000_000_000
            )
        if metadata:
            metrics.update(metadata)
        return metrics

    def _attempt_prompt(
        self,
        task: str,
        previous: ModelAttempt | None,
    ) -> str:
        if previous is None:
            return task
        failure_text = "\n".join(previous.verification.failures) or previous.error or "unknown"
        return (
            f"Original task:\n{task}\n\n"
            f"A smaller model previously answered:\n{previous.content}\n\n"
            f"Deterministic verification or tool execution reported:\n{failure_text}\n\n"
            "Inspect the current workspace state, correct the problem, verify it, and answer the "
            "original task. Do not merely critique the previous attempt."
        )

    def _run_attempt(
        self,
        role: str,
        task: str,
        workspace: Path,
        images: list[Path] | None,
        auto_approve: bool,
        interactive_approval: bool | None,
        previous: ModelAttempt | None,
        cumulative_modified: list[Path],
        routing_override: RoutingOverride,
    ) -> ModelAttempt:
        model, model_selection = self._resolve_model(role, routing_override)
        interactive = (
            sys.stdin.isatty() if interactive_approval is None else interactive_approval
        )
        registry = ToolRegistry(
            workspace,
            self.config.policy,
            auto_approve=auto_approve,
            interactive=interactive,
            commons=self.commons_session,
        )
        verifier = Verifier(registry.workspace, self.config.verification)
        enabled_tools = tuple(dict.fromkeys((*model.tools, *self.commons_session.tool_names)))
        tools = registry.available_schemas(enabled_tools)
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": system_prompt(
                    role, registry.workspace, commons_available=self.commons_session.ready
                ),
            },
            {"role": "user", "content": self._attempt_prompt(task, previous)},
        ]
        executions = []
        last_response: dict[str, Any] = {}
        final_content = ""
        final_thinking = ""
        error: str | None = None
        provider_metadata: dict[str, Any] = {}
        if model_selection is not None and model_selection.worker_id and model_selection.available:
            session_targets = SessionTargets.remote_inference(model_selection.worker_id)
        elif self.config.fabric.enabled and model.provider == "fabric":
            session_targets = SessionTargets.unresolved_inference()
        else:
            session_targets = SessionTargets()
        provider = self._provider_for_model(model, model_selection, session_targets)
        self._configure_fabric_provenance(task, role, model)

        try:
            for _step in range(self.config.ollama.max_tool_steps + 1):
                last_response = provider.chat(model, messages, tools=tools, images=images)
                provider_metadata = dict(getattr(provider, "last_metadata", {}))
                if self.commons_session.ready:
                    provider_metadata["commons_target"] = "controller"
                self._publish_fabric_evidence(provider_metadata)
                for key, value in session_targets.as_metadata().items():
                    provider_metadata.setdefault(key, value)
                if model_selection is not None:
                    provider_metadata.update(
                        {
                            "configured_model": model_selection.configured_model,
                            "selected_model": model_selection.selected_model,
                            "model_selection_reason": model_selection.reason,
                            "model_selection_source": "worker-inventory",
                            "model_inventory_status": model_selection.inventory_status,
                            "model_worker": model_selection.worker_id,
                            "model_loaded": model_selection.loaded,
                            "model_resident": model_selection.resident,
                            "routing_mode": model_selection.route_mode,
                            "routing_fallback_allowed": routing_override.allow_fallback,
                        }
                    )
                message = dict(last_response.get("message", {}))
                message.setdefault("role", "assistant")
                final_content = str(message.get("content", ""))
                final_thinking = str(message.get("thinking", ""))
                messages.append(message)

                calls = message.get("tool_calls") or []
                if not calls:
                    break
                if _step >= self.config.ollama.max_tool_steps:
                    error = f"Model exceeded {self.config.ollama.max_tool_steps} tool steps"
                    break

                for call in calls:
                    name, arguments = self._tool_call_parts(call)
                    execution = registry.execute(name, arguments)
                    executions.append(execution)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_name": name,
                            "content": execution.output,
                        }
                    )
            else:  # pragma: no cover - loop has an explicit max-step branch
                error = "Tool loop ended unexpectedly"
        except (OllamaError, ProviderError) as exc:
            # Provider failures are still physical execution observations. Keep
            # their provider/worker/placement metadata rather than making failed
            # remote attempts look like local Ollama calls in metrics and the TUI.
            provider_metadata = dict(getattr(provider, "last_metadata", {}))
            if self.commons_session.ready:
                provider_metadata["commons_target"] = "controller"
            self._publish_fabric_evidence(provider_metadata)
            for key, value in session_targets.as_metadata().items():
                provider_metadata.setdefault(key, value)
            if model_selection is not None:
                provider_metadata.update(
                    {
                        "configured_model": model_selection.configured_model,
                        "selected_model": model_selection.selected_model,
                        "model_selection_reason": model_selection.reason,
                        "model_selection_source": "worker-inventory",
                        "model_inventory_status": model_selection.inventory_status,
                        "model_worker": model_selection.worker_id,
                        "model_loaded": model_selection.loaded,
                        "model_resident": model_selection.resident,
                        "routing_mode": model_selection.route_mode,
                        "routing_fallback_allowed": routing_override.allow_fallback,
                    }
                )
            error = str(exc)

        for path in registry.modified_paths:
            if path not in cumulative_modified:
                cumulative_modified.append(path)
        verification = verifier.verify(cumulative_modified)

        tool_failures = [
            f"{item.name}: {item.output}" for item in executions if not item.success
        ]
        failures = list(verification.failures)
        if error:
            failures.append(error)
        if self.config.routing.escalate_on_tool_error:
            failures.extend(tool_failures)
        if not final_content.strip() and error is None:
            failures.append("Model returned no final content")

        if failures:
            verification = VerificationResult(
                passed=False,
                checks=verification.checks,
                failures=tuple(dict.fromkeys(failures)),
            )

        effective_targets = (
            SessionTargets()
            if provider_metadata.get("inference_target") == "controller"
            else session_targets
        )
        return ModelAttempt(
            role=role,
            model=model.name,
            content=final_content,
            thinking=final_thinking,
            metrics=self._response_metrics(last_response, provider_metadata),
            tool_executions=executions,
            verification=verification,
            error=error,
            session_targets=effective_targets,
        )

    def run(
        self,
        task: str,
        *,
        workspace: Path,
        images: list[Path] | None = None,
        forced_role: str | None = None,
        routing_override: RoutingOverride | None = None,
        auto_approve: bool = False,
        interactive_approval: bool | None = None,
    ) -> AgentResult:
        override = routing_override or RoutingOverride()
        if self.config.fabric.enabled and not self._exact_manual_route(override):
            # Auto-routing reads persistent controller inventory. Active
            # fleet probes and residency warming are operator/TUI actions.
            self._progress("persistent-inventory-read")
        elif self.config.fabric.enabled:
            self._progress(
                "exact-pin",
                worker=override.worker,
                model=override.model,
                mode=override.mode,
            )

        route = plan_route(
            task,
            self.config,
            images,
            forced_role,
            routing_override=routing_override,
        )
        self._progress(
            "route-planned",
            mode=route.routing_override.mode,
            roles=",".join(route.all_roles),
        )
        run_id = self.metrics.begin_run(task, route)
        attempts: list[ModelAttempt] = []
        cumulative_modified: list[Path] = []
        previous: ModelAttempt | None = None

        for index, role in enumerate(route.all_roles):
            self._progress("fabric-dispatch", role=role)
            attempt = self._run_attempt(
                role,
                task,
                workspace,
                images,
                auto_approve,
                interactive_approval,
                previous,
                cumulative_modified,
                route.routing_override,
            )
            attempts.append(attempt)
            self._restore_residency_after_attempt(attempt)
            attempt.metrics["lifecycle_stages"] = list(getattr(self, "_lifecycle_stages", []))
            last_stage = attempt.metrics.get("inference_stage")
            if last_stage:
                self._progress(
                    "inference-result",
                    last_stage=last_stage,
                    worker=attempt.session_targets.inference.worker_identity,
                    model=attempt.model,
                )
            self.metrics.record_attempt(
                run_id,
                index,
                attempt,
                escalated_from=previous.role if previous else None,
            )
            if attempt.verification.passed:
                break
            if not self.config.routing.escalate_on_verifier_failure:
                break
            previous = attempt

        final_content = attempts[-1].content if attempts else "No model attempt was made."
        return AgentResult(route=route, attempts=attempts, final_content=final_content)
