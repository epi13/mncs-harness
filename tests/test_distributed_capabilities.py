from __future__ import annotations

import json
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from epi13_local_harness.agent import LocalAgent
from epi13_local_harness.capability_graph import build_capability_graph
from epi13_local_harness.config import load_config
from epi13_local_harness.fabric import FabricExecutionError, FabricStatus
from epi13_local_harness.fabric_inventory_session import (
    InventoryAwareFabricSession,
    _FreshDispatchClient,
)
from epi13_local_harness.model_selection import ModelSelection
from epi13_local_harness.models import (
    FabricConfig,
    MetricsConfig,
    SessionTarget,
    SessionTargets,
)


def _entry(name: str, size: int = 1) -> dict[str, object]:
    return {
        "kind": "model",
        "namespace": "ollama",
        "name": name,
        "attributes": {"size_bytes": size},
    }


def _worker(
    worker_id: str,
    entries: list[dict[str, object]],
    *,
    availability: str = "AVAILABLE",
    inventory_status: str = "CURRENT",
) -> dict[str, object]:
    observation = {
        "worker_identity": worker_id,
        "availability": "AVAILABLE",
        "capabilities": entries,
    }
    return {
        "worker_id": worker_id,
        "source": "remote",
        "availability": availability,
        "capability_inventory_status": inventory_status,
        "capability_observation": observation,
    }


def _response_observation(request_id: str) -> dict[str, object]:
    return {
        "apiVersion": "commons.mncs.dev/v0alpha1",
        "kind": "Observation",
        "metadata": {
            "recordId": "commons:observation:remote-response",
            "createdAt": "2026-08-10T00:01:00Z",
            "author": {"type": "agent", "id": "agent:remote-fabric-model"},
        },
        "subject": {"type": "work-request", "identity": request_id},
        "scope": {
            "context": {"worker": "fabric-worker"},
            "limitations": ["deterministic integration fixture"],
        },
        "statement": {
            "summary": "The remote-labelled agent responded through controller policy.",
            "details": "The response is an inert observation, not verification.",
        },
        "evidence": [],
        "reproduction": {
            "prerequisites": [],
            "procedure": [],
            "expected": ["controller-mediated publication"],
        },
        "dependencies": [],
        "affectedContracts": [],
        "provenance": {
            "producer": {"type": "agent", "id": "agent:remote-fabric-model"},
            "sourceRecords": [request_id],
        },
        "confidence": {"level": "low", "rationale": "deterministic fixture"},
        "security": {
            "sensitivity": "public",
            "executableAttachments": False,
            "instructionsAreUntrusted": True,
            "requiredExternalAuthority": True,
        },
        "lifecycle": {"initialState": "proposed", "reviewWhen": ["human review"]},
        "relationships": [{"type": "responds_to", "target": request_id}],
        "details": {"outcome": "UNKNOWN", "authority": "none-granted"},
    }
class _CapabilityClient:
    def __init__(self, workers: list[dict[str, object]]) -> None:
        self._workers = workers
        self.ingested: list[dict[str, object]] = []

    def workers(self):
        return [dict(worker) for worker in self._workers]

    def ingest_capability_observation(
        self,
        worker_id,
        capabilities,
        *,
        availability="AVAILABLE",
        observation_source,
        status_reason=None,
    ):
        observation = {
            "worker_identity": worker_id,
            "availability": availability,
            "capabilities": capabilities,
            "observation_source": observation_source,
            "status_reason": status_reason,
        }
        self.ingested.append(observation)
        for worker in self._workers:
            if worker["worker_id"] == worker_id:
                worker["capability_observation"] = observation
                worker["capability_inventory_status"] = (
                    "CURRENT" if availability == "AVAILABLE" else availability
                )
        return observation


class DistributedCapabilityTests(unittest.TestCase):
    def _session(self, workers: list[dict[str, object]]) -> InventoryAwareFabricSession:
        config = replace(load_config(None).fabric, enabled=True)
        session = InventoryAwareFabricSession(config)
        session.client = _CapabilityClient(workers)
        session._state = "available"
        session.capability_api_available = True
        return session

    def test_exact_model_wins_on_a_specific_current_worker(self) -> None:
        session = self._session(
            [
                _worker("worker-a", [_entry("small:1b", 1)]),
                _worker("worker-b", [_entry("gemma4:e4b", 4)]),
            ]
        )
        model = load_config(None).models["e4b"]
        effective, selection = session.resolve_model("e4b", model)
        self.assertEqual(effective.name, "gemma4:e4b")
        self.assertIsNotNone(selection)
        self.assertEqual(selection.worker_id, "worker-b")
        self.assertTrue(selection.available)

    def test_compatible_fallback_selects_only_a_worker_that_reports_it(self) -> None:
        session = self._session(
            [
                _worker("worker-a", [_entry("general:3b", 3)]),
                _worker("worker-b", [_entry("devstral-small:8b", 8)]),
            ]
        )
        model = load_config(None).models["coder"]
        effective, selection = session.resolve_model("coder", model)
        self.assertEqual(effective.name, "devstral-small:8b")
        self.assertEqual(selection.worker_id, "worker-b")
        self.assertIn("code-hinted", selection.reason)

    def test_stale_unknown_and_unavailable_inventories_fail_closed(self) -> None:
        cases = (
            (_worker("stale", [_entry("gemma4:e4b")], inventory_status="STALE"), "STALE"),
            (_worker("unknown", [_entry("gemma4:e4b")], inventory_status="UNKNOWN"), "UNKNOWN"),
            (
                _worker("down", [_entry("gemma4:e4b")], availability="UNAVAILABLE"),
                "WORKER_UNAVAILABLE",
            ),
        )
        for worker, expected in cases:
            with self.subTest(expected=expected):
                session = self._session([worker])
                _effective, selection = session.resolve_model(
                    "e4b", load_config(None).models["e4b"]
                )
                self.assertFalse(selection.available)
                self.assertIsNone(selection.worker_id)
                self.assertEqual(selection.inventory_status, expected)

    def test_missing_model_and_fabric_unavailability_are_distinct(self) -> None:
        missing = self._session([_worker("worker", [])])
        _effective, selection = missing.resolve_model(
            "e4b", load_config(None).models["e4b"]
        )
        self.assertFalse(selection.available)
        self.assertEqual(selection.inventory_status, "MODEL_NOT_INSTALLED")

        unavailable = self._session([])
        unavailable.capability_api_available = False
        unavailable._state = "unavailable"
        _effective, selection = unavailable.resolve_model(
            "e4b", load_config(None).models["e4b"]
        )
        self.assertFalse(selection.available)
        self.assertEqual(selection.inventory_status, "FABRIC_UNAVAILABLE")

    def test_fabric_disabled_and_legacy_inventory_paths_remain_compatible(self) -> None:
        disabled_config = replace(load_config(None).fabric, enabled=False)
        disabled = InventoryAwareFabricSession(disabled_config)
        configured = load_config(None).models["e4b"]
        effective, selection = disabled.resolve_model("e4b", configured)
        self.assertEqual(effective.name, configured.name)
        self.assertEqual(effective.provider, configured.provider)
        self.assertIsNone(selection)

        legacy = self._session([_worker("legacy-worker", [])])
        legacy.capability_api_available = False
        legacy.model_inventories["legacy-worker"] = ({"name": "gemma4:e4b", "size": 4},)
        effective, selection = legacy.resolve_model("e4b", configured)
        self.assertEqual(effective.name, "gemma4:e4b")
        self.assertEqual(selection.worker_id, "legacy-worker")

    def test_failed_fresh_probe_publishes_unavailable_and_hides_prior_models(self) -> None:
        worker = _worker("worker", [_entry("old:model")])
        session = self._session([worker])
        calls = 0

        def probe(_worker_id: str):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ({"name": "gemma4:e4b", "size": 4},)
            raise RuntimeError("fresh observation failed")

        session._probe_model_inventory = probe  # type: ignore[method-assign]
        session._refresh_model_inventories()
        self.assertEqual(session.status().workers[0]["model_names"], ["gemma4:e4b"])
        session._refresh_model_inventories()
        failed = session.status().workers[0]
        self.assertNotIn("model_names", failed)
        self.assertEqual(failed["model_inventory_status"], "UNAVAILABLE")
        self.assertEqual(
            session.client.ingested[-1]["availability"],  # type: ignore[attr-defined]
            "UNAVAILABLE",
        )

    def test_capability_graph_reports_only_observed_and_configured_facts(self) -> None:
        status = self._session([_worker("worker", [_entry("gemma4:e4b")])]).status()
        graph = build_capability_graph(
            status,
            workspace=Path("."),
            controller_tools=("git_diff", "read_file"),
        )
        self.assertEqual(graph["controller"]["tools"], ["git_diff", "read_file"])
        self.assertEqual(graph["workers"][0]["capabilities"][0]["name"], "gemma4:e4b")
        self.assertNotIn("best_for", graph["workers"][0]["capabilities"][0])

        stale_status = self._session(
            [_worker("stale-worker", [_entry("old:model")], inventory_status="STALE")]
        ).status()
        stale_graph = build_capability_graph(stale_status)
        self.assertNotIn("capabilities", stale_graph["workers"][0])

    def test_remote_inference_targets_do_not_grant_workspace_or_tool_authority(self) -> None:
        targets = SessionTargets.remote_inference("collamore02-windows")
        self.assertEqual(targets.inference.label, "fabric-worker:collamore02-windows")
        self.assertEqual(targets.workspace.label, "controller")
        self.assertEqual(targets.tools.label, "controller")
        with self.assertRaises(ValueError):
            SessionTarget("fabric-worker", "bad\nworker")


class _AgentOllamaFixture(BaseHTTPRequestHandler):
    chat_calls = 0
    received_tool_result = False
    received_commons_result = False
    saw_commons_schema = False
    tool_sequence: tuple[str, ...] = ("write_file",)
    publication_record: dict[str, object] | None = None

    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps(
            {"models": [{"name": "gemma4:e4b", "size": 4, "digest": "sha256:" + "a" * 64}]}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        type(self).chat_calls += 1
        type(self).saw_commons_schema = type(self).saw_commons_schema or any(
            tool.get("function", {}).get("name") == "commons_describe"
            for tool in request.get("tools", [])
        )
        call_index = type(self).chat_calls - 1
        if call_index < len(type(self).tool_sequence):
            tool_name = type(self).tool_sequence[call_index]
            if tool_name == "write_file":
                arguments = {
                    "path": "controller-result.txt",
                    "content": "controller-owned",
                }
            elif tool_name == "commons_publish_record":
                arguments = {"record": type(self).publication_record}
            else:
                arguments = {}
            response = {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": arguments,
                            },
                        }
                    ],
                }
            }
        else:
            type(self).received_tool_result = any(
                message.get("role") == "tool" and "Wrote" in message.get("content", "")
                for message in request["messages"]
            )
            type(self).received_commons_result = any(
                message.get("role") == "tool"
                and "commons.mncs.dev/node/local-agent/v0alpha1"
                in message.get("content", "")
                for message in request["messages"]
            )
            response = {"message": {"role": "assistant", "content": "tool result received"}}
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


class DistributedSessionIntegrationTests(unittest.TestCase):
    def test_remote_inference_controller_tool_loop_preserves_authority_boundary(self) -> None:
        try:
            from mncs_fabric.api import FabricClient
            from mncs_fabric.transport import InProcessTransport
            from mncs_fabric.worker import LocalWorker
        except ImportError:
            self.skipTest("optional mncs-fabric dependency is not installed")

        _AgentOllamaFixture.chat_calls = 0
        _AgentOllamaFixture.received_tool_result = False
        _AgentOllamaFixture.received_commons_result = False
        _AgentOllamaFixture.saw_commons_schema = False
        _AgentOllamaFixture.tool_sequence = ("write_file",)
        server = ThreadingHTTPServer(("127.0.0.1", 0), _AgentOllamaFixture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                remote_root = root / "remote-root"
                remote_root.mkdir()
                remote_cache = root / "remote-cache"
                worker = LocalWorker(
                    "fabric-worker",
                    remote_root,
                    root / "remote-worker.jsonl",
                    bundle_cache_root=remote_cache,
                )
                client = FabricClient("controller", root / "controller.jsonl")
                client.network.register_remote(
                    "fabric-worker",
                    worker.capabilities(),
                    InProcessTransport(worker),
                    resource_snapshot=worker.resource_snapshot(),
                )
                client.remote_configs["fabric-worker"] = object()  # type: ignore[assignment]
                client.refresh_worker("fabric-worker")

                fabric = FabricConfig(
                    enabled=True,
                    state_path=root / "session.jsonl",
                    fallback_to_local=False,
                    provider_ollama_base_url=f"http://127.0.0.1:{server.server_port}",
                    runtime_probe_on_refresh=False,
                )
                session = InventoryAwareFabricSession(fabric)
                session.client = _FreshDispatchClient(client)
                session._state = "available"
                session.capability_api_available = True
                session._probe_model_inventory = lambda _worker_id: (  # type: ignore[method-assign]
                    {
                        "name": "gemma4:e4b",
                        "size": 4,
                        "digest": "sha256:" + "a" * 64,
                    },
                )
                session._refresh_model_inventories()

                base = load_config(None)
                config = replace(
                    base,
                    fabric=fabric,
                    metrics=MetricsConfig(root / "metrics.sqlite3", False),
                )
                agent = LocalAgent(config)
                agent.fabric_session = session
                workspace = root / "controller-workspace"
                workspace.mkdir()
                result = agent.run(
                    "Write the bounded controller result.",
                    workspace=workspace,
                    forced_role="e4b",
                    auto_approve=True,
                    interactive_approval=False,
                )
                attempt = result.attempts[0]
                self.assertTrue(result.successful, result)
                self.assertEqual(attempt.model, "gemma4:e4b")
                self.assertEqual(attempt.session_targets.inference.label, "fabric-worker:fabric-worker")
                self.assertEqual(attempt.session_targets.workspace.label, "controller")
                self.assertEqual(attempt.session_targets.tools.label, "controller")
                self.assertEqual(
                    (workspace / "controller-result.txt").read_text(encoding="utf-8"),
                    "controller-owned",
                )
                self.assertFalse(any(remote_cache.rglob("controller-result.txt")))
                self.assertTrue(_AgentOllamaFixture.received_tool_result)
                dispatches = client.network.ledger.records(
                    record_type="protocol.controller-dispatch", limit=100
                )
                plans = [entry["record"]["payload"]["job_plan"] for entry in dispatches]
                self.assertTrue(plans)
                self.assertTrue(all(plan["argv"][0] == "@python" for plan in plans))
                self.assertNotIn(str(workspace), json.dumps(plans, sort_keys=True))
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_worker_disappearing_between_commons_tool_turns_fails_without_authority_fallback(
        self,
    ) -> None:
        try:
            import mcp  # noqa: F401
            import mncs_commons  # noqa: F401
        except ImportError:
            self.skipTest("Commons MCP optional dependencies are unavailable")
        class DisappearingSession:
            def __init__(self) -> None:
                self.calls = 0
                self.last_inference = None
                self.last_execution_record = None

            def refresh_model_inventory(self):
                return self.status()

            def status(self):
                return FabricStatus(
                    True,
                    "available",
                    "controller",
                    workers=(_worker("fabric-worker", [_entry("gemma4:e4b")]),),
                )

            def resolve_model(self, role, model):
                return model, ModelSelection(
                    role=role,
                    configured_model=model.name,
                    selected_model=model.name,
                    stored_size_bytes=4,
                    worker_id="fabric-worker",
                    inventory_status="CURRENT",
                    reason="deterministic disappearing-worker fixture",
                )

            def set_consumer_context(self, **_kwargs):
                return None

            def chat(self, _model, _messages, tools=None, images=None, *, worker_id=None):
                self.calls += 1
                if self.calls == 1:
                    self.last_inference = {
                        "worker": worker_id,
                        "request_identity": "request:first",
                    }
                    return (
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "type": "function",
                                        "function": {
                                            "name": "commons_describe",
                                            "arguments": {},
                                        },
                                    }
                                ],
                            }
                        },
                        {
                            "provider": "ollama-via-mncs-fabric",
                            "fabric_worker": worker_id,
                            "execution_source": "remote",
                        },
                    )
                raise FabricExecutionError("WORKER_UNAVAILABLE")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = load_config(None)
            fabric = replace(base.fabric, enabled=True, fallback_to_local=False)
            config = replace(
                base,
                fabric=fabric,
                commons=replace(
                    base.commons,
                    enabled=True,
                    store_path=root / "commons",
                    domain="controller:test",
                ),
                metrics=MetricsConfig(root / "metrics.sqlite3", False),
            )
            agent = LocalAgent(config)
            session = DisappearingSession()
            agent.fabric_session = session  # type: ignore[assignment]
            workspace = root / "workspace"
            workspace.mkdir()
            result = agent.run(
                "Describe Commons, but do not fall back if the worker disappears.",
                workspace=workspace,
                forced_role="e4b",
                interactive_approval=False,
            )
            attempt = result.attempts[0]
            self.assertFalse(result.successful)
            self.assertEqual(session.calls, 2)
            self.assertEqual(attempt.tool_executions[0].name, "commons_describe")
            self.assertTrue(attempt.tool_executions[0].success)
            self.assertIn("WORKER_UNAVAILABLE", attempt.error or "")
            self.assertEqual(
                attempt.session_targets.inference.label, "fabric-worker:fabric-worker"
            )
            self.assertEqual(attempt.session_targets.workspace.label, "controller")
            self.assertEqual(attempt.session_targets.tools.label, "controller")

    def test_remote_fabric_model_uses_controller_commons_and_publishes_inert_evidence(self) -> None:
        try:
            from mncs_commons.store import CommonsStore
            from mncs_fabric.api import FabricClient
            from mncs_fabric.transport import InProcessTransport
            from mncs_fabric.worker import LocalWorker
        except ImportError:
            self.skipTest("optional Commons/Fabric dependencies are not installed")

        _AgentOllamaFixture.chat_calls = 0
        _AgentOllamaFixture.received_tool_result = False
        _AgentOllamaFixture.received_commons_result = False
        _AgentOllamaFixture.saw_commons_schema = False
        _AgentOllamaFixture.tool_sequence = ("commons_describe",)
        server = ThreadingHTTPServer(("127.0.0.1", 0), _AgentOllamaFixture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                remote_root = root / "remote-root"
                remote_root.mkdir()
                remote_cache = root / "remote-cache"
                worker = LocalWorker(
                    "fabric-worker",
                    remote_root,
                    root / "remote-worker.jsonl",
                    bundle_cache_root=remote_cache,
                )
                client = FabricClient("controller", root / "controller.jsonl")
                client.network.register_remote(
                    "fabric-worker",
                    worker.capabilities(),
                    InProcessTransport(worker),
                    resource_snapshot=worker.resource_snapshot(),
                )
                client.remote_configs["fabric-worker"] = object()  # type: ignore[assignment]
                client.refresh_worker("fabric-worker")

                fabric = FabricConfig(
                    enabled=True,
                    state_path=root / "session.jsonl",
                    fallback_to_local=False,
                    provider_ollama_base_url=f"http://127.0.0.1:{server.server_port}",
                    runtime_probe_on_refresh=False,
                )
                session = InventoryAwareFabricSession(fabric)
                session.client = _FreshDispatchClient(client)
                session._state = "available"
                session.capability_api_available = True
                session._probe_model_inventory = lambda _worker_id: (  # type: ignore[method-assign]
                    {
                        "name": "gemma4:e4b",
                        "size": 4,
                        "digest": "sha256:" + "a" * 64,
                    },
                )
                session._refresh_model_inventories()

                commons_path = root / "controller-commons"
                base = load_config(None)
                config = replace(
                    base,
                    fabric=fabric,
                    commons=replace(
                        base.commons,
                        enabled=True,
                        store_path=commons_path,
                        domain="controller:test",
                        publish_fabric_evidence=True,
                    ),
                    metrics=MetricsConfig(root / "metrics.sqlite3", False),
                )
                agent = LocalAgent(config)
                agent.fabric_session = session
                workspace = root / "controller-workspace"
                workspace.mkdir()
                result = agent.run(
                    "Use commons_describe and report the controller knowledge profile.",
                    workspace=workspace,
                    forced_role="e4b",
                    auto_approve=True,
                    interactive_approval=False,
                )
                attempt = result.attempts[0]
                self.assertTrue(result.successful, result)
                self.assertEqual(
                    attempt.session_targets.inference.label, "fabric-worker:fabric-worker"
                )
                self.assertEqual(attempt.session_targets.workspace.label, "controller")
                self.assertEqual(attempt.session_targets.tools.label, "controller")
                self.assertEqual(attempt.metrics["commons_target"], "controller")
                self.assertTrue(_AgentOllamaFixture.saw_commons_schema)
                self.assertTrue(_AgentOllamaFixture.received_commons_result)
                self.assertEqual(_AgentOllamaFixture.chat_calls, 2)

                records = CommonsStore(commons_path).records()
                evidence = [
                    record
                    for record in records
                    if isinstance(record.get("details"), dict)
                    and "fabricExecution" in record["details"]
                ]
                self.assertEqual(len(evidence), 2, attempt.metrics)
                self.assertTrue(
                    all(record["details"]["sourceOutcome"] == "PASS" for record in evidence)
                )
                self.assertTrue(
                    all(
                        record["details"]["claimVerificationStatus"] == "UNKNOWN"
                        for record in evidence
                    )
                )
                self.assertIn(
                    attempt.metrics["commons_evidence_publication"], {"PUBLISHED"}
                )

                dispatches = client.network.ledger.records(
                    record_type="protocol.controller-dispatch", limit=100
                )
                inference_dispatches = [
                    row["record"]
                    for row in dispatches
                    if row["record"]["payload"]["job_plan"]["job_id"]
                    == "elh-fabric-inference"
                ]
                self.assertEqual(len(inference_dispatches), 2)
                self.assertTrue(
                    all("consumer_context" in row["payload"] for row in inference_dispatches)
                )
                self.assertTrue(
                    all(
                        row["payload"]["consumer_context"]["source_project"]
                        == "epi13-local-harness"
                        for row in inference_dispatches
                    )
                )
                self.assertNotIn(
                    "Use commons_describe", json.dumps(inference_dispatches, sort_keys=True)
                )

                store_bytes = str(commons_path).encode("utf-8")
                self.assertFalse(
                    any(
                        store_bytes in path.read_bytes()
                        for path in remote_cache.rglob("*")
                        if path.is_file()
                    )
                )
                self.assertFalse(any(remote_cache.rglob("records")))
                plans = [row["payload"]["job_plan"] for row in inference_dispatches]
                self.assertTrue(all(plan["argv"][0] == "@python" for plan in plans))
                self.assertTrue(
                    all(
                        plan["timeout_seconds"]
                        == fabric.provider_timeout_seconds
                        + fabric.job_timeout_overhead_seconds
                        for plan in plans
                    )
                )
                self.assertNotIn("ssh", json.dumps(plans, sort_keys=True).lower())
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_remote_agent_discovers_work_and_contributes_through_controller_commons(self) -> None:
        try:
            from mncs_commons.application import CommonsApplication
            from mncs_commons.bootstrap import _request
            from mncs_commons.store import CommonsStore
            from mncs_fabric.api import FabricClient
            from mncs_fabric.transport import InProcessTransport
            from mncs_fabric.worker import LocalWorker
        except ImportError:
            self.skipTest("optional Commons/Fabric dependencies are not installed")

        _AgentOllamaFixture.chat_calls = 0
        _AgentOllamaFixture.received_tool_result = False
        _AgentOllamaFixture.received_commons_result = False
        _AgentOllamaFixture.saw_commons_schema = False
        _AgentOllamaFixture.tool_sequence = ("commons_work_list", "commons_publish_record")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _AgentOllamaFixture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                commons_path = root / "controller-commons"
                store = CommonsStore(commons_path)
                store.init()
                request = _request(
                    "commons:request:remote-agent-test",
                    "Report one bounded controller-mediated observation.",
                    "controller:test",
                )
                receipt = CommonsApplication(store).publish(request, domain="controller:test")
                _AgentOllamaFixture.publication_record = _response_observation(
                    str(request["metadata"]["recordId"])
                )

                remote_root = root / "remote-root"
                remote_root.mkdir()
                remote_cache = root / "remote-cache"
                worker = LocalWorker(
                    "fabric-worker",
                    remote_root,
                    root / "remote-worker.jsonl",
                    bundle_cache_root=remote_cache,
                )
                client = FabricClient("controller", root / "controller.jsonl")
                client.network.register_remote(
                    "fabric-worker",
                    worker.capabilities(),
                    InProcessTransport(worker),
                    resource_snapshot=worker.resource_snapshot(),
                )
                client.remote_configs["fabric-worker"] = object()  # type: ignore[assignment]
                client.refresh_worker("fabric-worker")
                fabric = FabricConfig(
                    enabled=True,
                    state_path=root / "session.jsonl",
                    fallback_to_local=False,
                    provider_ollama_base_url=f"http://127.0.0.1:{server.server_port}",
                    runtime_probe_on_refresh=False,
                )
                session = InventoryAwareFabricSession(fabric)
                session.client = _FreshDispatchClient(client)
                session._state = "available"
                session.capability_api_available = True
                session._probe_model_inventory = lambda _worker_id: (  # type: ignore[method-assign]
                    {"name": "gemma4:e4b", "size": 4, "digest": "sha256:" + "a" * 64},
                )
                session._refresh_model_inventories()

                base = load_config(None)
                config = replace(
                    base,
                    fabric=fabric,
                    commons=replace(
                        base.commons,
                        enabled=True,
                        store_path=commons_path,
                        domain="controller:test",
                        allow_model_publication=True,
                    ),
                    metrics=MetricsConfig(root / "metrics.sqlite3", False),
                )
                agent = LocalAgent(config)
                agent.fabric_session = session
                workspace = root / "controller-workspace"
                workspace.mkdir()
                result = agent.run(
                    "Find the open Commons WorkRequest and publish the prepared bounded response.",
                    workspace=workspace,
                    forced_role="e4b",
                    auto_approve=True,
                    interactive_approval=False,
                )
                attempt = result.attempts[0]
                self.assertTrue(result.successful, result)
                self.assertEqual(
                    [execution.name for execution in attempt.tool_executions],
                    ["commons_work_list", "commons_publish_record"],
                )
                self.assertTrue(all(item.success for item in attempt.tool_executions))
                self.assertIn(
                    "commons:request:remote-agent-test", attempt.tool_executions[0].output
                )
                self.assertIn("INGESTED", attempt.tool_executions[1].output)
                conversation = CommonsApplication(CommonsStore(commons_path)).conversation(
                    str(receipt["contentDigest"]), max_nodes=10
                )
                self.assertEqual(len(conversation["records"]), 2)
                self.assertTrue(
                    any(edge["type"] == "responds_to" for edge in conversation["edges"])
                )
                self.assertEqual(_AgentOllamaFixture.chat_calls, 3)
                self.assertEqual(
                    attempt.session_targets.inference.label, "fabric-worker:fabric-worker"
                )
                self.assertEqual(attempt.session_targets.workspace.label, "controller")
                self.assertEqual(attempt.session_targets.tools.label, "controller")
                self.assertFalse(any(remote_cache.rglob("records")))
        finally:
            _AgentOllamaFixture.publication_record = None
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
