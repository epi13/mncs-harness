from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from epi13_local_harness.agent import LocalAgent
from epi13_local_harness.config import load_config
from epi13_local_harness.models import MetricsConfig


class FakeOllamaClient:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def chat(self, model, messages, tools=None, images=None):
        count = self.counts.get(model.name, 0)
        self.counts[model.name] = count + 1
        common = {
            "total_duration": 100,
            "load_duration": 10,
            "prompt_eval_count": 20,
            "prompt_eval_duration": 30,
            "eval_count": 10,
            "eval_duration": 40,
        }
        if model.name == "gemma4:e4b":
            if count == 0:
                return {
                    **common,
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "write_file",
                                    "arguments": {
                                        "path": "example.py",
                                        "content": "def broken(:\n",
                                    },
                                },
                            }
                        ],
                    },
                }
            return {**common, "message": {"role": "assistant", "content": "Initial edit done."}}

        if model.name == "qwen3:8b":
            if count == 0:
                return {
                    **common,
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "write_file",
                                    "arguments": {
                                        "path": "example.py",
                                        "content": "def working():\n    return 1\n",
                                    },
                                },
                            }
                        ],
                    },
                }
            return {**common, "message": {"role": "assistant", "content": "Fixed and verified."}}

        return {**common, "message": {"role": "assistant", "content": "Reviewed."}}


class AgentTests(unittest.TestCase):
    def test_verifier_failure_escalates_to_coder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            base = load_config(Path("/missing/config.toml"))
            config = replace(
                base,
                metrics=MetricsConfig(workspace / "metrics.sqlite3", False),
            )
            agent = LocalAgent(config)
            agent.client = FakeOllamaClient()
            result = agent.run(
                "Fix example.py and run the tests.",
                workspace=workspace,
                auto_approve=True,
            )
            self.assertEqual([attempt.role for attempt in result.attempts], ["e4b", "coder"])
            self.assertFalse(result.attempts[0].verification.passed)
            self.assertTrue(result.attempts[1].verification.passed)
            self.assertEqual(result.final_content, "Fixed and verified.")
            self.assertIn(
                "def working", (workspace / "example.py").read_text(encoding="utf-8")
            )


if __name__ == "__main__":
    unittest.main()
