from __future__ import annotations

import unittest
from dataclasses import replace

from epi13_local_harness.config import load_config
from epi13_local_harness.fabric import FabricExecutionError, FabricStatus
from epi13_local_harness.provider import FabricOllamaProvider, ProviderError


class _CapturingSession:
    def __init__(self) -> None:
        self.model = None

    def chat(self, model, messages, tools=None, images=None):
        self.model = model
        return (
            {"message": {"role": "assistant", "content": "ok"}},
            {"provider": "ollama-via-mncs-fabric", "placement_mode": "full-accelerator"},
        )


class _FailingSession:
    last_inference = {
        "worker": "collamore02-windows",
        "placement": "cpu",
        "reason": "INTEGRITY_FAILURE",
        "request_identity": "request-123",
    }

    def chat(self, model, messages, tools=None, images=None):
        raise FabricExecutionError("INTEGRITY_FAILURE")

    def status(self):
        return FabricStatus(
            True,
            "available",
            "fixture-controller",
            detail=(
                "collamore02-windows: model inventory failed: INTEGRITY_FAILURE: "
                "artifact identity mismatch: inventory.py"
            ),
        )


class _SizedLocalProvider:
    last_metadata = {"provider": "ollama"}

    def model_size(self, name: str) -> int | None:
        return 6_000_000_000 if name else None

    def chat(self, model, messages, tools=None, images=None):
        return {"message": {"role": "assistant", "content": "local"}}


class ProviderTests(unittest.TestCase):
    def test_fabric_provider_derives_model_storage_size_before_placement(self) -> None:
        session = _CapturingSession()
        provider = FabricOllamaProvider(session, _SizedLocalProvider(), True)
        model = replace(load_config(None).models["coder"], model_storage_bytes=0)
        response = provider.chat(model, [{"role": "user", "content": "hello"}])
        self.assertEqual(response["message"]["content"], "ok")
        self.assertIsNotNone(session.model)
        self.assertEqual(session.model.model_storage_bytes, 6_000_000_000)
        self.assertEqual(provider.last_metadata["model_storage_source"], "ollama-tags")
        self.assertEqual(provider.last_metadata["model_storage_bytes"], 6_000_000_000)

    def test_failed_fabric_attempt_keeps_remote_provider_and_worker_metadata(self) -> None:
        provider = FabricOllamaProvider(_FailingSession(), _SizedLocalProvider(), False)
        model = replace(load_config(None).models["coder"], model_storage_bytes=1)
        with self.assertRaisesRegex(ProviderError, "artifact identity mismatch"):
            provider.chat(model, [{"role": "user", "content": "hello"}])
        self.assertEqual(provider.last_metadata["provider"], "ollama-via-mncs-fabric")
        self.assertEqual(provider.last_metadata["execution_source"], "remote")
        self.assertEqual(provider.last_metadata["fabric_worker"], "collamore02-windows")
        self.assertEqual(provider.last_metadata["placement_mode"], "cpu")
        self.assertFalse(provider.last_metadata["fabric_fallback"])
        self.assertIn("INTEGRITY_FAILURE", provider.last_metadata["fabric_failure_reason"])

    def test_local_fallback_reports_controller_inference_and_tool_targets(self) -> None:
        provider = FabricOllamaProvider(_FailingSession(), _SizedLocalProvider(), True)
        model = replace(load_config(None).models["coder"], model_storage_bytes=1)
        response = provider.chat(model, [{"role": "user", "content": "hello"}])
        self.assertEqual(response["message"]["content"], "local")
        self.assertTrue(provider.last_metadata["fabric_fallback"])
        self.assertEqual(provider.last_metadata["inference_target"], "controller")
        self.assertEqual(provider.last_metadata["workspace_target"], "controller")
        self.assertEqual(provider.last_metadata["tool_execution_target"], "controller")


if __name__ == "__main__":
    unittest.main()
