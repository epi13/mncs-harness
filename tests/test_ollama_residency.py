from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from epi13_local_harness.config import load_config
from epi13_local_harness.ollama import OllamaClient


class OllamaResidencyTests(unittest.TestCase):
    def test_local_chat_preserves_zero_bounded_and_pinned_keep_alive(self) -> None:
        config = load_config(None)
        client = OllamaClient(config.ollama)
        for keep_alive in (0, "10m", -1):
            with self.subTest(keep_alive=keep_alive), patch.object(
                client,
                "_request",
                return_value={"message": {"role": "assistant", "content": "ok"}},
            ) as request:
                client.chat(
                    replace(config.models["coder"], keep_alive=keep_alive),
                    [{"role": "user", "content": "hello"}],
                )
                payload = request.call_args.args[2]
                self.assertEqual(payload["keep_alive"], keep_alive)

    def test_explicit_warm_and_release_use_provider_generate_lifecycle(self) -> None:
        config = load_config(None)
        client = OllamaClient(config.ollama)
        with patch.object(client, "_request", return_value={}) as request:
            client.set_residency("qwen3:8b", -1)
            client.set_residency("qwen3:8b", 0)
        warm, release = request.call_args_list
        self.assertEqual(warm.args[1], "/api/generate")
        self.assertEqual(warm.args[2]["keep_alive"], -1)
        self.assertEqual(release.args[2]["keep_alive"], 0)


if __name__ == "__main__":
    unittest.main()
