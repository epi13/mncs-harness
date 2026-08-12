from __future__ import annotations

import importlib.util
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from epi13_local_harness.agent import LocalAgent
from epi13_local_harness.config import load_config
from epi13_local_harness.fabric import FabricSession, FabricStatus, FabricUnavailable
from epi13_local_harness.models import FabricConfig, FabricWorkerConfig, MetricsConfig
from epi13_local_harness.provider import FabricOllamaProvider, ProviderError


class _OllamaFixture(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        length = int(self.headers["Content-Length"])
        json.loads(self.rfile.read(length))
        body = json.dumps(
            {
                "message": {"role": "assistant", "content": "fabric fixture response"},
                "eval_count": 2,
                "eval_duration": 1_000_000,
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


class _FailingSession:
    def chat(self, *args: object, **kwargs: object) -> object:
        raise FabricUnavailable("no eligible worker")


class _LocalProvider:
    last_metadata = {"provider": "ollama", "execution_source": "local"}

    def chat(self, *args: object, **kwargs: object) -> dict[str, object]:
        return {"message": {"role": "assistant", "content": "local fallback"}}


class _SuccessfulSession:
    def chat(self, *args: object, **kwargs: object) -> tuple[dict[str, object], dict[str, object]]:
        return (
            {"message": {"role": "assistant", "content": "Fabric reviewed the task."}},
            {
                "provider": "ollama-via-mncs-fabric",
                "fabric_enabled": True,
                "execution_source": "remote",
                "fabric_worker": "fixture-gpu",
                "placement_mode": "full-accelerator",
            },
        )


class FabricTests(unittest.TestCase):
    def test_disabled_fabric_is_explicit(self) -> None:
        config = load_config(Path("/missing/config.toml"))
        status = FabricSession(config.fabric).status()
        self.assertFalse(status.enabled)
        self.assertEqual(status.state, "disabled")

    def test_missing_remote_trust_paths_are_reported_without_startup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = FabricConfig(
                enabled=True,
                state_path=root / "fabric.jsonl",
                workers=(
                    FabricWorkerConfig(
                        worker_id="missing-remote",
                        kind="remote",
                        state_path=root / "worker.jsonl",
                        host="127.0.0.1",
                        port=9443,
                    ),
                ),
            )
            session = FabricSession(config)
            session.initialize()
            status = session.status()
            self.assertEqual(status.state, "unavailable")
            detail = status.detail or ""
            if importlib.util.find_spec("mncs_fabric"):
                self.assertIn("trust paths", detail)
            else:
                self.assertIn("mncs-fabric is not installed", detail)

    def test_fallback_provider_preserves_local_response_and_reason(self) -> None:
        provider = FabricOllamaProvider(_FailingSession(), _LocalProvider(), True)
        response = provider.chat(replace(load_config(Path("/missing/config.toml")).models["e2b"]), [])
        self.assertEqual(response["message"]["content"], "local fallback")
        self.assertTrue(provider.last_metadata["fabric_fallback"])
        self.assertIn("no eligible worker", provider.last_metadata["fabric_fallback_reason"])

    def test_fallback_policy_can_prohibit_local_retry(self) -> None:
        provider = FabricOllamaProvider(_FailingSession(), _LocalProvider(), False)
        with self.assertRaises(ProviderError):
            provider.chat(replace(load_config(Path("/missing/config.toml")).models["e2b"]), [])

    def test_status_counts_available_accelerator_and_unknown_workers(self) -> None:
        status = FabricStatus(
            True,
            "available",
            "fixture-controller",
            workers=(
                {
                    "worker_id": "gpu",
                    "availability": "AVAILABLE",
                    "capabilities": ["python", "placement:sequential-cpu-offload"],
                    "resource_snapshot": {"accelerators": [{"execution_probe": "UNKNOWN"}]},
                    "runtime_observation": {
                        "accelerator_backend": "cuda",
                        "runtime_execution_probe": "PASS",
                    },
                },
                {
                    "worker_id": "stale-cpu",
                    "availability": "UNKNOWN",
                    "capabilities": ["python"],
                    "resource_snapshot": {"accelerators": []},
                },
            ),
        )
        self.assertEqual(status.available_workers, 1)
        self.assertEqual(status.accelerator_count, 1)
        self.assertEqual(status.cuda_ready_count, 1)
        self.assertEqual(status.offload_capable_count, 1)

    def test_service_refresh_reads_cuda_facts_without_running_unsupported_probes(self) -> None:
        class ServiceClient:
            def workers(self):
                return [
                    {
                        "worker_id": "service-gpu",
                        "source": "remote",
                        "availability": "AVAILABLE",
                        "resource_snapshot": {"accelerators": [{"backend": "cuda"}]},
                    }
                ]

        config = replace(
            load_config(Path("/missing/config.toml")).fabric,
            enabled=True,
            controller_mode="service",
        )
        session = FabricSession(config)
        session.client = ServiceClient()
        session._state = "available"
        session._controller_state = "connected"
        session._fleet_state = "available"
        session._execution_transport = "unsupported"
        with patch.object(session, "_refresh_remote_workers", side_effect=AssertionError("probe attempted")), patch.object(
            session, "_ensure_cuda_runtime_observations", side_effect=AssertionError("probe attempted")
        ):
            status = session.refresh()
        self.assertEqual(status.fleet_state, "available")
        self.assertEqual(status.execution_transport, "unsupported")
        self.assertEqual(status.capability_inventory, "unavailable")
        self.assertEqual(status.fleet_authority, "persistent-controller")

    def test_agent_keeps_semantic_route_and_uses_fabric_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = load_config(Path("/missing/config.toml"))
            config = replace(
                base,
                fabric=FabricConfig(enabled=True),
                metrics=MetricsConfig(root / "metrics.sqlite3", False),
            )
            agent = LocalAgent(config)
            agent.fabric_session = _SuccessfulSession()
            result = agent.run(
                "Review this change for security risks.",
                workspace=root,
                forced_role="reviewer",
            )
            self.assertEqual(result.attempts[0].role, "reviewer")
            self.assertTrue(result.attempts[0].verification.passed)
            self.assertEqual(result.attempts[0].metrics["fabric_worker"], "fixture-gpu")
            self.assertEqual(result.attempts[0].metrics["placement_mode"], "full-accelerator")

    @unittest.skipUnless(
        importlib.util.find_spec("mncs_fabric"),
        "optional mncs-fabric dependency is not installed",
    )
    def test_in_process_fabric_invocation_returns_placement_evidence(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaFixture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                base = load_config(Path("/missing/config.toml"))
                model = replace(base.models["e2b"], provider="fabric", execution_device="cpu")
                config = FabricConfig(
                    enabled=True,
                    state_path=root / "fabric.jsonl",
                    provider_ollama_base_url=f"http://127.0.0.1:{server.server_port}",
                    workers=(
                        FabricWorkerConfig(
                            worker_id="fixture-local",
                            kind="local",
                            state_path=root / "worker.jsonl",
                            bundle_root=root / "bundle-root",
                        ),
                    ),
                )
                session = FabricSession(config)
                session.initialize()
                response, metadata = session.chat(model, [{"role": "user", "content": "hello"}])
                self.assertEqual(response["message"]["content"], "fabric fixture response")
                self.assertEqual(metadata["execution_source"], "local")
                self.assertEqual(metadata["placement_mode"], "cpu")
                self.assertTrue(metadata["fabric_receipt_identity"])
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
