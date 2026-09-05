"""Enforcement choke points: normal interfaces cannot bypass Atlas (harness#57).

Case 20 of the hostile matrix plus the mandatory-path pins: consequential
tools and Fabric dispatch fail closed without a granted Atlas-bound leg,
while read-only tools keep working.
"""

import sys
from pathlib import Path as _TestPath

sys.path.insert(0, str(_TestPath(__file__).resolve().parent))

import tempfile
from pathlib import Path

from atlas_fixtures import issue, load

from epi13_local_harness.config import load_config
from epi13_local_harness.fabric import FabricConfig, FabricSession
from epi13_local_harness.fabric_target_tools import FabricTargetToolExecutor
from epi13_local_harness.tools import ToolRegistry


def registry():
    temporary = tempfile.TemporaryDirectory()
    workspace = Path(temporary.name)
    policy = load_config(Path("/missing/config.toml")).policy
    return temporary, ToolRegistry(workspace, policy, auto_approve=True, interactive=False)


def test_20_consequential_tool_without_binding_is_blocked():
    handle, registry_ = registry()
    try:
        result = registry_.execute("run_command", {"argv": ["echo", "hi"]})
        assert not result.success
        assert result.output.startswith("ATLAS_REFUSED"), result.output
        written = registry_.execute("write_file", {"path": "x.txt", "content": "hi"})
        assert not written.success
        assert written.output.startswith("ATLAS_REFUSED"), written.output
    finally:
        handle.cleanup()


def test_read_only_tools_work_without_binding():
    handle, registry_ = registry()
    try:
        result = registry_.execute("system_info", {})
        assert result.success, result.output
    finally:
        handle.cleanup()


def test_bound_granted_leg_allows_consequential_tools():
    handle, registry_ = registry()
    try:
        requirement = load()
        acceptance = registry_.bind_atlas_requirement(requirement, "cpu")
        assert acceptance.verdict == "GRANTED", acceptance.reason
        result = registry_.execute("run_command", {"argv": ["python", "-m", "compileall", "."]})
        assert result.success, result.output
        written = registry_.execute(
            "write_file", {"path": "bound.txt", "content": "authority works"}
        )
        assert written.success, written.output
    finally:
        handle.cleanup()


def test_bound_leg_missing_tool_needs_is_blocked():
    handle, registry_ = registry()
    try:
        requirement = load(legs=[{"name": "narrow", "needs": ["tests.execute"]}])
        acceptance = registry_.bind_atlas_requirement(requirement, "narrow")
        assert acceptance.verdict == "GRANTED", acceptance.reason
        result = registry_.execute("run_command", {"argv": ["echo", "hi"]})
        assert not result.success
        assert "does not cover" in result.output, result.output
    finally:
        handle.cleanup()


def test_bound_denied_leg_blocks_consequential_tools():
    handle, registry_ = registry()
    try:
        requirement = load()
        acceptance = registry_.bind_atlas_requirement(requirement, "fetch")
        assert acceptance.verdict == "REFUSED", acceptance.reason
        result = registry_.execute("run_command", {"argv": ["echo", "hi"]})
        assert not result.success
        assert result.output.startswith("ATLAS_REFUSED"), result.output
    finally:
        handle.cleanup()


def test_fabric_dispatch_without_requirement_is_refused():
    handle, registry_ = registry()
    try:
        session = FabricSession(FabricConfig())
        executor = FabricTargetToolExecutor(session, registry_)
        result = executor.execute("worker-01", ["python", "-c", "pass"])
        assert result.fabric_result is None
        assert "ATLAS_REFUSED" in result.execution.output, result.execution.output
    finally:
        handle.cleanup()


def test_fabric_dispatch_to_unauthorized_worker_is_refused():
    handle, registry_ = registry()
    try:
        session = FabricSession(FabricConfig())
        executor = FabricTargetToolExecutor(session, registry_)
        requirement = load(
            atlas_decisions=[issue("worker.dispatch", "granted", execution_target="worker-01")],
            legs=[
                {
                    "name": "gpu",
                    "needs": ["worker.dispatch"],
                    "target": "worker-01",
                }
            ],
        )
        result = executor.execute(
            "worker-02",
            ["python", "-c", "pass"],
            requirement=requirement,
            requirement_leg="gpu",
        )
        assert result.fabric_result is None
        assert "not Atlas-authorized" in result.execution.output, result.execution.output
    finally:
        handle.cleanup()


def test_fabric_dispatch_leg_without_dispatch_cover_is_refused():
    handle, registry_ = registry()
    try:
        session = FabricSession(FabricConfig())
        executor = FabricTargetToolExecutor(session, registry_)
        requirement = load(legs=[{"name": "narrow", "needs": ["tests.execute"]}])
        result = executor.execute(
            "worker-01",
            ["python", "-c", "pass"],
            requirement=requirement,
            requirement_leg="narrow",
        )
        assert result.fabric_result is None
        assert "ATLAS_REFUSED" in result.execution.output, result.execution.output
    finally:
        handle.cleanup()
