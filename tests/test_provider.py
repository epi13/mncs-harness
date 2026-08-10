from __future__ import annotations

import unittest
from dataclasses import replace

from epi13_local_harness.config import load_config
from epi13_local_harness.provider import FabricOllamaProvider


class _CapturingSession:
    def __init__(self) -> None:
        self.model = None

    def chat(self, model, messages, tools=None, images=None):
        self.model = model
        return (
            {"message": {"role": "assistant", "content": "ok"}},
            {"provider": "ollama-via-mncs-fabric", "placement_mode": "full-accelerator"},
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


if __name__ == "__main__":
    unittest.main()
