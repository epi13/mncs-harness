from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .fabric import FabricSession, FabricStatus
from .metrics import MetricsStore
from .models import (
    AgentResult,
    HarnessConfig,
    ModelAttempt,
    VerificationResult,
)
from .ollama import OllamaClient, OllamaError
from .prompts import system_prompt
from .provider import FabricOllamaProvider, LocalOllamaProvider, ProviderError
from .router import plan_route
from .tools import ToolRegistry
from .verifiers import Verifier


class LocalAgent:
    def __init__(self, config: HarnessConfig):
        self.config = config
        # ``client`` remains a compatibility seam used by existing callers and
        # tests.  Provider selection is performed per model role below.
        self.client = OllamaClient(config.ollama)
        self.fabric_session = FabricSession(config.fabric)
        self.fabric_session.initialize()
        self.metrics = MetricsStore(config.metrics.path, config.metrics.store_prompt_text)

    def fabric_status(self) -> FabricStatus:
        return self.fabric_session.status()

    def _provider_for_model(self, model) -> object:
        local = LocalOllamaProvider(self.client)
        if self.config.fabric.enabled and model.provider == "fabric":
            return FabricOllamaProvider(
                self.fabric_session,
                local,
                self.config.fabric.fallback_to_local,
            )
        return local

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
    ) -> ModelAttempt:
        model = self.config.models[role]
        interactive = (
            sys.stdin.isatty() if interactive_approval is None else interactive_approval
        )
        registry = ToolRegistry(
            workspace,
            self.config.policy,
            auto_approve=auto_approve,
            interactive=interactive,
        )
        verifier = Verifier(registry.workspace, self.config.verification)
        tools = registry.available_schemas(model.tools)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt(role, registry.workspace)},
            {"role": "user", "content": self._attempt_prompt(task, previous)},
        ]
        executions = []
        last_response: dict[str, Any] = {}
        final_content = ""
        final_thinking = ""
        error: str | None = None
        provider_metadata: dict[str, Any] = {}
        provider = self._provider_for_model(model)

        try:
            for _step in range(self.config.ollama.max_tool_steps + 1):
                last_response = provider.chat(model, messages, tools=tools, images=images)
                provider_metadata = dict(getattr(provider, "last_metadata", {}))
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

        return ModelAttempt(
            role=role,
            model=model.name,
            content=final_content,
            thinking=final_thinking,
            metrics=self._response_metrics(last_response, provider_metadata),
            tool_executions=executions,
            verification=verification,
            error=error,
        )

    def run(
        self,
        task: str,
        *,
        workspace: Path,
        images: list[Path] | None = None,
        forced_role: str | None = None,
        auto_approve: bool = False,
        interactive_approval: bool | None = None,
    ) -> AgentResult:
        route = plan_route(task, self.config, images, forced_role)
        run_id = self.metrics.begin_run(task, route)
        attempts: list[ModelAttempt] = []
        cumulative_modified: list[Path] = []
        previous: ModelAttempt | None = None

        for index, role in enumerate(route.all_roles):
            attempt = self._run_attempt(
                role,
                task,
                workspace,
                images,
                auto_approve,
                interactive_approval,
                previous,
                cumulative_modified,
            )
            attempts.append(attempt)
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
