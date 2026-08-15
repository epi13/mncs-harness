from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from epi13_local_harness.config import load_config
from epi13_local_harness.models import RoutingOverride
from epi13_local_harness.router import plan_route


class RouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(Path("/missing/config.toml"))

    def test_simple_explanation_uses_e2b(self) -> None:
        plan = plan_route("Explain what uname -a displays.", self.config)
        self.assertEqual(plan.primary_role, "e2b")
        self.assertEqual(plan.escalation_roles, ("e4b", "reviewer"))

    def test_code_edit_uses_e4b_then_coder(self) -> None:
        plan = plan_route("Fix parser.py and run the tests.", self.config)
        self.assertEqual(plan.primary_role, "e4b")
        self.assertEqual(plan.escalation_roles[0], "coder")

    def test_high_risk_request_starts_with_reviewer(self) -> None:
        plan = plan_route("Use sudo to reinstall the service as root.", self.config)
        self.assertEqual(plan.primary_role, "reviewer")
        self.assertTrue(plan.profile.is_high_risk)

    def test_image_starts_with_reviewer(self) -> None:
        plan = plan_route(
            "Explain this screenshot.",
            self.config,
            images=[Path("terminal.png")],
        )
        self.assertEqual(plan.primary_role, "reviewer")

    def test_forced_role_disables_escalation(self) -> None:
        plan = plan_route("Any task", self.config, forced_role="coder")
        self.assertEqual(plan.all_roles, ("coder",))

    def test_explicit_heuristic_backend_remains_available(self) -> None:
        config = replace(
            self.config,
            router=replace(
                self.config.router,
                enable_semantic_routing=True,
                backend="heuristic",
                mode="hybrid",
            ),
        )
        plan = plan_route(
            "Extract all visible text from this scanned invoice into JSON.",
            config,
            images=[Path("invoice.png")],
        )
        self.assertEqual(plan.lane, "ocr")
        self.assertEqual(plan.primary_role, "reviewer")
        self.assertEqual(plan.semantic.backend, "heuristic")

    def test_transformers_config_is_ignored_during_normal_execution(self) -> None:
        config = replace(
            self.config,
            router=replace(
                self.config.router,
                enable_semantic_routing=True,
                backend="transformers",
                mode="hybrid",
            ),
        )
        with patch(
            "epi13_local_harness.semantic_router.route_with_backend",
            side_effect=AssertionError("transformers backend must not run"),
        ):
            plan = plan_route("Fix parser.py.", config)
        self.assertEqual(plan.primary_role, "e4b")
        self.assertIsNone(plan.semantic)
        self.assertTrue(any("ignored during normal execution" in reason for reason in plan.reasons))

    def test_backend_error_preserves_deterministic_route(self) -> None:
        config = replace(
            self.config,
            router=replace(
                self.config.router,
                enable_semantic_routing=True,
                backend="transformers",
            ),
        )
        plan = plan_route("Explain what uname does.", config)
        self.assertEqual(plan.primary_role, "e2b")
        self.assertTrue(
            any("ignored during normal execution" in reason for reason in plan.reasons)
        )

    def test_exact_worker_model_pin_bypasses_semantic_compatibility_router(self) -> None:
        config = replace(
            self.config,
            router=replace(
                self.config.router,
                enable_semantic_routing=True,
                backend="transformers",
            ),
        )
        with patch(
            "epi13_local_harness.semantic_router.route_with_backend",
            side_effect=AssertionError("semantic router must not run for an exact pin"),
        ):
            plan = plan_route(
                "Explain this without network access",
                config,
                routing_override=RoutingOverride.from_values(
                    worker="worker-01-windows", model="gemma4:e4b"
                ),
            )
        self.assertIsNone(plan.semantic)
        self.assertEqual(plan.routing_override.mode, "WORKER_MODEL")
        self.assertEqual(plan.escalation_roles, ())


if __name__ == "__main__":
    unittest.main()
