from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from epi13_local_harness.config import load_config
from epi13_local_harness.fabric import FabricStatus
from epi13_local_harness.models import RoutePlan, TaskProfile
from epi13_local_harness.semantic_router import RouterRuntimeStatus
from epi13_local_harness.tui import (
    HarnessTui,
    fabric_status_summary,
    parse_image_paths,
    role_options,
    route_summary,
    router_status_summary,
)


class TuiHelperTests(unittest.TestCase):
    def test_role_options_start_with_automatic_route(self) -> None:
        config = load_config(None)
        options = role_options(config)
        self.assertEqual(options[0], ("Automatic routing", ""))
        self.assertEqual({value for _, value in options[1:]}, set(config.models))

    def test_parse_image_paths_resolves_relative_and_quoted_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            first = workspace / "one.png"
            second = workspace / "two words.jpg"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            result = parse_image_paths('one.png, "two words.jpg"', workspace)
            self.assertEqual(result, [first.resolve(), second.resolve()])

    def test_parse_image_paths_rejects_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "Image does not exist"):
                parse_image_paths("missing.png", Path(temp_dir))

    def test_route_summary_contains_chain_and_reasons(self) -> None:
        plan = RoutePlan(
            primary_role="e4b",
            escalation_roles=("coder", "reviewer"),
            reasons=("code detected", "edit requested"),
            profile=TaskProfile(
                text="fix it",
                word_count=2,
                has_code=True,
                asks_for_edit=True,
                asks_for_execution=False,
                asks_for_explanation=False,
                is_high_risk=False,
                is_complex=False,
                has_image=False,
                file_reference_count=0,
            ),
        )
        summary = route_summary(plan)
        self.assertIn("e4b -> coder -> reviewer", summary)
        self.assertIn("code detected", summary)

    def test_router_summary_distinguishes_cached_from_active(self) -> None:
        status = RouterRuntimeStatus(
            enabled=True,
            mode="hybrid",
            backend="transformers",
            model="LiquidAI/router",
            revision="a" * 40,
            device="cpu",
            local_files_only=True,
            cache_directory=Path("/tmp/router"),
            missing_dependencies=(),
            cached=True,
            active=False,
            state="cached",
        )
        summary = router_status_summary(status)
        self.assertIn("state=cached", summary)
        self.assertIn("active=False", summary)

    def test_fabric_summary_distinguishes_disabled_state(self) -> None:
        summary = fabric_status_summary(
            FabricStatus(False, "disabled", "fixture-controller")
        )
        self.assertEqual(summary, "state=disabled")


class TuiAppTests(unittest.IsolatedAsyncioTestCase):
    async def test_app_mounts_without_contacting_ollama(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = HarnessTui(load_config(None), Path(temp_dir))
            async with app.run_test(size=(120, 40)):
                workspace = app.query_one("#workspace")
                model = app.query_one("#model")
                auto_approve = app.query_one("#auto-approve")
                self.assertEqual(workspace.value, str(Path(temp_dir).resolve()))
                self.assertEqual(model.selection, "")
                self.assertFalse(auto_approve.value)

    async def test_prompt_reader_does_not_collide_with_textual_task_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = HarnessTui(load_config(None), Path(temp_dir))
            async with app.run_test(size=(120, 40)):
                prompt = app.query_one("#prompt")
                prompt.value = "hello from the TUI"
                self.assertEqual(app._prompt_text(), "hello from the TUI")
                app.action_preview_route()
                self.assertNotEqual(app.query_one("#status").renderable, "Error")


if __name__ == "__main__":
    unittest.main()
