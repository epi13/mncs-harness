from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from epi13_local_harness.config import load_config
from epi13_local_harness.router import profile_task
from epi13_local_harness.semantic_router import (
    LfmPromptRouter,
    SemanticRouterError,
    clear_router_cache,
    route_with_backend,
    router_status,
)

REVISION = "35ca4a0469f180f1cf05a630df8842fa17ac18e3"


class FakeTokenizer:
    calls: list[tuple[str, dict[str, object]]] = []

    @classmethod
    def from_pretrained(cls, model: str, **kwargs):
        cls.calls.append((model, kwargs))
        return cls()


class FakeLoadedModel:
    def __init__(self) -> None:
        self.device = "cpu"

    def eval(self):
        return self

    def to(self, device: str):
        self.device = device
        return self

    def route(self, text, routes, tokenizer):
        scores = []
        for route in routes:
            score = 0.82 if route.startswith("coding:") else 0.06
            scores.append({"route": route, "score": score})
        return sorted(scores, key=lambda item: item["score"], reverse=True)


class FakeAutoModel:
    calls: list[tuple[str, dict[str, object]]] = []

    @classmethod
    def from_pretrained(cls, model: str, **kwargs):
        cls.calls.append((model, kwargs))
        return FakeLoadedModel()


class SemanticRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_router_cache()
        FakeTokenizer.calls.clear()
        FakeAutoModel.calls.clear()

    def _config(self):
        config = load_config(Path("/missing/config.toml"))
        return replace(
            config,
            router=replace(
                config.router,
                enable_semantic_routing=True,
                backend="transformers",
                mode="hybrid",
                revision=REVISION,
                local_files_only=True,
            ),
        )

    def test_pinned_transformers_backend_scores_configured_lanes(self) -> None:
        config = self._config()
        profile = profile_task("Fix the failing Python unit test.", config)
        backend = LfmPromptRouter(
            config,
            loader=lambda: (FakeAutoModel, FakeTokenizer),
        )
        with patch(
            "epi13_local_harness.semantic_router._missing_dependencies",
            return_value=(),
        ):
            result = backend.route(
                "Fix the failing Python unit test.",
                list(config.lanes.values()),
            )

        self.assertEqual(result.selected_lane, "coding")
        self.assertEqual(result.backend, "transformers")
        self.assertEqual(result.revision, REVISION)
        self.assertGreater(result.selected_score, result.runner_up_score or 0)
        self.assertGreaterEqual(result.latency_ms or 0, 0)
        self.assertEqual(profile.has_code, True)
        self.assertEqual(FakeAutoModel.calls[0][1]["revision"], REVISION)
        self.assertTrue(FakeAutoModel.calls[0][1]["trust_remote_code"])

    def test_unpinned_revision_is_rejected_before_remote_code_load(self) -> None:
        base = self._config()
        config = replace(base, router=replace(base.router, revision="main"))
        backend = LfmPromptRouter(
            config,
            loader=lambda: (FakeAutoModel, FakeTokenizer),
        )
        with patch(
            "epi13_local_harness.semantic_router._missing_dependencies",
            return_value=(),
        ):
            with self.assertRaisesRegex(SemanticRouterError, "40-character"):
                backend.load()
        self.assertEqual(FakeAutoModel.calls, [])

    def test_backend_failure_returns_deterministic_fallback_reason(self) -> None:
        config = self._config()
        profile = profile_task("Explain this file.", config)
        with patch(
            "epi13_local_harness.semantic_router.get_router_backend",
            side_effect=SemanticRouterError("checkpoint unavailable"),
        ):
            result, reason = route_with_backend("Explain this file.", config, profile)
        self.assertIsNone(result)
        self.assertIn("checkpoint unavailable", reason or "")

    def test_status_distinguishes_disabled_from_active(self) -> None:
        config = load_config(Path("/missing/config.toml"))
        with tempfile.TemporaryDirectory() as directory:
            config = replace(
                config,
                router=replace(
                    config.router,
                    cache_directory=Path(directory),
                    enable_semantic_routing=False,
                ),
            )
            status = router_status(config)
        self.assertEqual(status.state, "disabled")
        self.assertFalse(status.active)


if __name__ == "__main__":
    unittest.main()
