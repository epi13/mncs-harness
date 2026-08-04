from __future__ import annotations

import unittest
from pathlib import Path

from epi13_local_harness.config import load_config
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
            "Explain this screenshot.", self.config, images=[Path("terminal.png")]
        )
        self.assertEqual(plan.primary_role, "reviewer")

    def test_forced_role_disables_escalation(self) -> None:
        plan = plan_route("Any task", self.config, forced_role="coder")
        self.assertEqual(plan.all_roles, ("coder",))


if __name__ == "__main__":
    unittest.main()
