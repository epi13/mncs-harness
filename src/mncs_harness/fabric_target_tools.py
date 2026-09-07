"""Harness-policy-gated bounded tool execution on one exact Fabric worker."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Sequence

from .atlas_binding import (
    FABRIC_DISPATCH_CAPABILITY_NEEDS,
    LEGACY_PROOF_ORIGIN,
    OPERATOR_PROOF_ORIGINS,
    ExecutionRequirement,
)
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
        requirement: ExecutionRequirement | None = None,
        requirement_leg: str = "",
        expected_participant: str = "",
        expected_scope: str = "",
    ) -> FabricTargetToolResult:
        """Execute one approved argv workload against one consumer-selected worker.

        The Atlas-bound requirement is mandatory and checked before command
        policy: the bound leg must be GRANTED, must cover worker.dispatch,
        and Atlas must authorize exactly this worker identity. Placement
        outside carried authority is never dispatched. When the caller
        supplies expected participant/scope (operator-authenticated
        context, never the envelope), the requirement session must match:
        a valid requirement issued for another session cannot be replayed
        here. A preflight proof check refuses before any Fabric call when
        the implementation cannot produce fresh operator-grade proof, so
        consequential work never executes first and annotates UNKNOWN
        afterward.
        """

        target = SessionTarget("fabric-worker", worker_identity)
        arguments = {"argv": [str(value) for value in argv]}
        refusal = self._atlas_dispatch_gate(
            worker_identity,
            requirement,
            requirement_leg,
            expected_participant=expected_participant,
            expected_scope=expected_scope,
        )
        if refusal is not None:
            return self._not_dispatched(target, arguments, refusal, refusal.reason)
        normalized, decision = self.registry.command_policy.evaluate(arguments["argv"])
        if not decision.allowed:
            return self._not_dispatched(target, arguments, decision, decision.reason)
        if not approval_granted(decision, self.registry.auto_approve, self.registry.interactive):
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
                requirement=requirement,
                requirement_leg=requirement_leg,
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

    def _atlas_dispatch_gate(
        self,
        worker_identity: str,
        requirement: ExecutionRequirement | None,
        requirement_leg: str,
        *,
        expected_participant: str = "",
        expected_scope: str = "",
    ) -> PolicyDecision | None:
        """Refuse dispatch outside carried Atlas authority (None = covered).

        Beyond the invoke-time gate (GRANTED leg, dispatch coverage,
        authorized worker), two preflight checks run before any Fabric
        call: the requirement session must match operator-supplied
        expected identity (replay into another participant/scope fails
        here, not after effects), and the Fabric implementation must be
        able to produce fresh operator-grade proof (consequential work
        never executes first and annotates UNKNOWN afterward).
        """
        if requirement is None or not requirement_leg:
            return PolicyDecision(
                False,
                "blocked",
                "ATLAS_REFUSED: fabric dispatch needs an Atlas-bound "
                "requirement leg; none was carried",
            )
        acceptance = requirement.accept(requirement_leg)
        if acceptance.verdict != "GRANTED":
            return PolicyDecision(
                False,
                "blocked",
                f"ATLAS_REFUSED: fabric dispatch blocked by leg "
                f"{requirement_leg}: {acceptance.reason}",
            )
        leg = next((item for item in requirement.legs if item.name == requirement_leg), None)
        uncovered = [
            need
            for need in FABRIC_DISPATCH_CAPABILITY_NEEDS
            if leg is None or need not in leg.needs
        ]
        if uncovered:
            return PolicyDecision(
                False,
                "blocked",
                f"ATLAS_REFUSED: leg {requirement_leg} does not cover dispatch needs {uncovered}",
            )
        authorized = sorted(
            {
                decision.execution_target
                for decision in requirement.decisions
                if decision.capability == "worker.dispatch" and decision.execution_target
            }
        )
        if worker_identity not in authorized:
            return PolicyDecision(
                False,
                "blocked",
                f"ATLAS_REFUSED: worker {worker_identity} is not Atlas-authorized "
                f"(authorized: {authorized})",
            )
        if expected_participant and requirement.session_participant != expected_participant:
            return PolicyDecision(
                False,
                "blocked",
                "ATLAS_REFUSED: requirement session participant "
                f"{requirement.session_participant!r} is not the operator-expected "
                f"{expected_participant!r}: replay into another participant?",
            )
        if expected_scope and requirement.session_scope != expected_scope:
            return PolicyDecision(
                False,
                "blocked",
                "ATLAS_REFUSED: requirement session scope "
                f"{requirement.session_scope!r} is not the operator-expected "
                f"{expected_scope!r}: replay into another scope?",
            )
        client = self.session.client
        if client is None:
            return PolicyDecision(
                False,
                "blocked",
                "ATLAS_REFUSED: no Fabric client for preflight proof check",
            )
        origin = self._inventory_proof_origin(client, worker_identity)
        if origin not in OPERATOR_PROOF_ORIGINS:
            if origin == LEGACY_PROOF_ORIGIN:
                reason = (
                    "legacy Fabric implementation predates observation provenance; "
                    "consequential Atlas-bound execution requires confirmable authority"
                )
            else:
                reason = (
                    f"proof origin {origin!r} is not operator authority; "
                    "exact-target admission needs operator-grade proof"
                )
            return PolicyDecision(
                False,
                "blocked",
                f"ATLAS_PREFLIGHT_REFUSED: {reason}; refusing before any Fabric call",
            )
        if not self._inventory_is_fresh(client, worker_identity):
            return PolicyDecision(
                False,
                "blocked",
                "ATLAS_PREFLIGHT_REFUSED: stale target evidence at dispatch time; "
                "refusing before any Fabric call",
            )
        return None

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
        requirement: ExecutionRequirement | None = None,
        requirement_leg: str = "",
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
        with tempfile.TemporaryDirectory(
            prefix="elh-fabric-target-", dir=temporary_root
        ) as directory:
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
        if requirement is not None and requirement_leg:
            # The gate already proved authorized == declared; now prove
            # actual == declared on fresh operator proof. The proof class
            # comes from Fabric's own observation provenance
            # (worker-observed / operator-asserted vs consumer-declared)
            # and freshness from Fabric's own inventory judgment -- never
            # from consumer context. The actual target comes from Fabric's
            # own placement evidence, never from the declared request: a
            # Fabric that placed elsewhere must not confirm. An execution
            # Fabric performed outside confirmed authority never reports
            # success; an unconfirmed one (UNKNOWN) keeps Fabric's own
            # result but is annotated as unconfirmed.
            evidence = fabric_result.get("target_execution_evidence") or {}
            confirmation = requirement.confirm_execution(
                requirement_leg,
                actual_target=str(evidence.get("worker_identity") or ""),
                proof_origin=self._inventory_proof_origin(client, target.worker_identity),
                proof_fresh=self._inventory_is_fresh(client, target.worker_identity),
            )
            if confirmation.verdict == "REFUSED":
                success = False
                output = f"ATLAS_CONFIRM_REFUSED: {confirmation.reason}\n{output}"
            elif confirmation.verdict == "UNKNOWN":
                output = f"ATLAS_CONFIRM_UNKNOWN: {confirmation.reason}\n{output}"
        return FabricTargetToolResult(
            ToolExecution("run_command", arguments, output, success, decision),
            target,
            authorization_identity,
            dict(fabric_result),
        )

    @staticmethod
    def _inventory_proof_origin(client: Any, worker_identity: str) -> str:
        """Map Fabric observation provenance to a proof channel.

        A Fabric implementation that predates observation provenance
        reports the legacy channel: its observations predate the classes,
        so a classless observation there is unproven-but-consistent
        (UNKNOWN), never a manufactured refusal. On a provenance-capable
        implementation a classless or consumer-declared observation stays
        fail-closed, matching Fabric's own admission doctrine.
        """
        try:
            from mncs_fabric.capabilities import TRUSTED_ADMISSION_CLASSES
        except ImportError:  # min-supported Fabric predates provenance classes
            return LEGACY_PROOF_ORIGIN
        try:
            observation = client.latest_capability_observation(worker_identity) or {}
        except Exception:
            return "consumer-declared"
        if not isinstance(observation, dict):
            return "consumer-declared"
        klass = observation.get("observation_class", "consumer-declared")
        return "fabric-inventory" if klass in TRUSTED_ADMISSION_CLASSES else "consumer-declared"

    @staticmethod
    def _inventory_is_fresh(client: Any, worker_identity: str) -> bool:
        """Defer to Fabric's own CURRENT/STALE/UNKNOWN inventory judgment."""
        try:
            inventory = client.capability_inventory(worker_identity)
        except Exception:
            return False
        return bool(isinstance(inventory, dict) and inventory.get("fresh"))

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
            windows_candidate = PureWindowsPath(argument)
            if not candidate.is_absolute() and (windows_candidate.drive or windows_candidate.root):
                raise ValueError(
                    "controller Windows paths (rooted, drive-relative, UNC, or device forms) "
                    "cannot be used as remote Fabric arguments"
                )
            if not candidate.is_absolute():
                if index == 1 and not argument.startswith("-"):
                    resolved = self.registry.guard.resolve(source_root / candidate, must_exist=True)
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
