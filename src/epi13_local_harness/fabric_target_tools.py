"""Harness-policy-gated bounded tool execution on one exact Fabric worker."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Sequence

from .fabric import FabricExecutionError, FabricSession, _identity
from .models import PolicyDecision, SessionTarget, ToolExecution
from .policy import approval_granted
from .tools import ToolRegistry


@dataclass(frozen=True)
class FabricTargetToolResult:
    """One Harness decision plus the resulting Fabric evidence, if dispatched."""

    execution: ToolExecution
    target: SessionTarget
    authorization_identity: str | None
    fabric_result: dict[str, Any] | None


class FabricTargetToolExecutor:
    """Apply Harness command policy, then dispatch a content bundle with no fallback."""

    def __init__(self, session: FabricSession, registry: ToolRegistry) -> None:
        self.session = session
        self.registry = registry

    def execute(
        self,
        worker_identity: str,
        argv: Sequence[str],
        *,
        source_root: Path | None = None,
        required_capabilities: Sequence[str] = (),
        tool_capability_identity: str | None = None,
        runtime_identity: str | None = None,
        request_id: str | None = None,
    ) -> FabricTargetToolResult:
        """Execute one approved argv workload against one consumer-selected worker."""

        target = SessionTarget("fabric-worker", worker_identity)
        arguments = {"argv": [str(value) for value in argv]}
        normalized, decision = self.registry.command_policy.evaluate(arguments["argv"])
        if not decision.allowed:
            return self._not_dispatched(target, arguments, decision, decision.reason)
        if not approval_granted(
            decision, self.registry.auto_approve, self.registry.interactive
        ):
            return self._not_dispatched(
                target,
                arguments,
                decision,
                "Command denied or not approved for the selected Fabric target.",
            )
        if not self.session.target_execution_supported or self.session.client is None:
            failure = PolicyDecision(
                False,
                "blocked",
                "FABRIC_TARGET_EXECUTION_UNSUPPORTED: running service does not advertise it",
            )
            return self._not_dispatched(target, arguments, failure, failure.reason)

        try:
            root = self._source_root(source_root)
            remote_argv = self._remote_argv(normalized, root)
            return self._dispatch(
                target,
                arguments,
                decision,
                root,
                remote_argv,
                required_capabilities=required_capabilities,
                tool_capability_identity=tool_capability_identity,
                runtime_identity=runtime_identity,
                request_id=request_id,
            )
        except Exception as exc:
            return FabricTargetToolResult(
                ToolExecution(
                    "run_command",
                    arguments,
                    f"FABRIC_TARGET_EXECUTION_FAILED: {exc}",
                    False,
                    decision,
                ),
                target,
                None,
                None,
            )

    def _dispatch(
        self,
        target: SessionTarget,
        arguments: dict[str, Any],
        decision: PolicyDecision,
        source_root: Path,
        remote_argv: list[str],
        *,
        required_capabilities: Sequence[str],
        tool_capability_identity: str | None,
        runtime_identity: str | None,
        request_id: str | None,
    ) -> FabricTargetToolResult:
        from mncs_fabric.artifacts import build_manifest
        from mncs_fabric.bundles import build_bundle_archive
        from mncs_fabric.models import validate_job_plan
        from mncs_fabric.targets import ExecutionTargetReference

        client = self.session.client
        assert client is not None
        context = self.session.consumer_context()
        observation = client.latest_capability_observation(target.worker_identity)
        if not isinstance(observation, dict):
            raise FabricExecutionError("Fabric target has no capability observation")
        worker = client.fleet_status(target.worker_identity)
        if not isinstance(worker, dict):
            raise FabricExecutionError("Fabric target has no current fleet state")

        capability_name = "python"
        capabilities = tuple(dict.fromkeys((capability_name, *required_capabilities)))
        selected_tool_identity = tool_capability_identity or self._tool_identity(
            observation, capability_name
        )
        profile = (worker.get("description") or {}).get("runtime_profile") or {}
        selected_runtime_identity = runtime_identity or profile.get("runtime_profile_identity")

        temporary_root = self.session.config.state_path.parent
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="elh-fabric-target-", dir=temporary_root) as directory:
            manifest = build_manifest(source_root)
            archive = Path(directory) / "execution-bundle.zip"
            build_bundle_archive(source_root, archive)
            authorization_identity = _identity(
                {
                    "schema_version": "elh.fabric-tool-authorization.v0.1",
                    "worker_identity": target.worker_identity,
                    "argv": remote_argv,
                    "artifact_manifest_identity": manifest["manifest_identity"],
                    "policy": {
                        "allowed": decision.allowed,
                        "risk": decision.risk,
                        "reason": decision.reason,
                        "requires_approval": decision.requires_approval,
                        "approval": "GRANTED",
                    },
                }
            )
            plan = validate_job_plan(
                {
                    "schema_version": "mncs-fabric.job-plan.v0.1",
                    "job_id": "elh-fabric-target-tool",
                    "candidate_identity": authorization_identity,
                    "evaluator_identity": None,
                    "artifact_manifest_identity": manifest["manifest_identity"],
                    "argv": remote_argv,
                    "working_directory": ".",
                    "timeout_seconds": self.registry.policy_config.command_timeout_seconds,
                    "output_limit_bytes": max(
                        4096,
                        min(2 * 1024 * 1024, self.registry.policy_config.max_tool_output_chars * 4),
                    ),
                    "environment": {"PYTHONHASHSEED": "0"},
                    "required_capabilities": list(capabilities),
                    "result_paths": [],
                    "network_policy": "DECLARED_OFFLINE",
                }
            )
            execution_target = ExecutionTargetReference(
                worker_identity=str(target.worker_identity),
                required_capabilities=capabilities,
                tool_capability_identity=selected_tool_identity,
                runtime_identity=selected_runtime_identity,
                consumer_context_identity=context.context_identity,
                consumer_authorization_identity=authorization_identity,
            )
            fabric_result = client.execute_target(
                execution_target,
                plan,
                manifest,
                consumer_context=context,
                consumer_authorization_identity=authorization_identity,
                execution_bundle_archive=archive,
                request_id=request_id,
            )

        record = fabric_result.get("record") or {}
        success = (
            fabric_result.get("disposition") in {"EXECUTED", "DUPLICATE_IDEMPOTENT"}
            and record.get("outcome") == "PASS"
        )
        output = self._format_result(fabric_result, record)
        return FabricTargetToolResult(
            ToolExecution("run_command", arguments, output, success, decision),
            target,
            authorization_identity,
            dict(fabric_result),
        )

    def _source_root(self, value: Path | None) -> Path:
        root = self.registry.guard.resolve(value or self.registry.workspace, must_exist=True)
        if not root.is_dir():
            raise ValueError("Fabric target source root must be a workspace directory")
        return root

    def _remote_argv(self, argv: list[str], source_root: Path) -> list[str]:
        executable = Path(argv[0]).name
        if executable not in {"python", "python3"}:
            raise ValueError(
                "the portable Fabric target adapter currently supports only the worker-local "
                "Python runtime alias"
            )
        result = ["@python"]
        for index, argument in enumerate(argv[1:], start=1):
            path_segments = argument.replace("\\", "/").split("/")
            if ".." in path_segments:
                raise ValueError("parent traversal is not allowed in Fabric target arguments")
            candidate = Path(argument)
            if PureWindowsPath(argument).is_absolute():
                raise ValueError(
                    "controller Windows paths cannot be used as remote Fabric arguments"
                )
            if not candidate.is_absolute():
                if index == 1 and not argument.startswith("-"):
                    resolved = self.registry.guard.resolve(candidate, must_exist=True)
                    try:
                        result.append(resolved.relative_to(source_root).as_posix())
                    except ValueError as exc:
                        raise ValueError(
                            "Python entry point is outside the selected execution bundle"
                        ) from exc
                    continue
                result.append(argument)
                continue
            resolved = self.registry.guard.resolve(candidate, must_exist=False)
            try:
                result.append(resolved.relative_to(source_root).as_posix())
            except ValueError as exc:
                raise ValueError(
                    "absolute command argument is outside the selected execution bundle"
                ) from exc
        return result

    @staticmethod
    def _tool_identity(observation: dict[str, Any], capability_name: str) -> str | None:
        matching = [
            str(item["capability_identity"])
            for item in observation.get("capabilities", [])
            if isinstance(item, dict)
            and item.get("kind") == "tool"
            and item.get("name") == capability_name
            and isinstance(item.get("capability_identity"), str)
        ]
        return matching[0] if len(matching) == 1 else None

    def _format_result(self, result: dict[str, Any], record: dict[str, Any]) -> str:
        if record:
            stdout = (record.get("stdout") or {}).get("captured_utf8", "")
            stderr = (record.get("stderr") or {}).get("captured_utf8", "")
            text = f"exit_code={record.get('exit_code')}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        else:
            text = json.dumps(
                {
                    "disposition": result.get("disposition"),
                    "reason": result.get("reason"),
                    "target_admission_identity": result.get("target_admission_identity"),
                },
                sort_keys=True,
            )
        limit = self.registry.policy_config.max_tool_output_chars
        return text if len(text) <= limit else text[:limit] + "\n... truncated"

    @staticmethod
    def _not_dispatched(
        target: SessionTarget,
        arguments: dict[str, Any],
        decision: PolicyDecision,
        output: str,
    ) -> FabricTargetToolResult:
        return FabricTargetToolResult(
            ToolExecution("run_command", arguments, output, False, decision),
            target,
            None,
            None,
        )
