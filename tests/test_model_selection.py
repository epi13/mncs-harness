import unittest

from epi13_local_harness.model_evidence import CapabilityEvidence
from epi13_local_harness.model_selection import select_installed_model

INVENTORY = [
    {"name": "alpha-general:24b", "size": 15_177_374_099, "capabilities": ["completion"]},
    {"name": "beta-chat:4b", "size": 9_608_350_718, "capabilities": ["completion"]},
    {"name": "gamma-large:20b", "size": 13_793_441_244, "capabilities": ["completion"]},
    {"name": "delta-mid:8b", "size": 4_942_891_653, "capabilities": ["completion", "tools"]},
    {"name": "epsilon-tiny:3.8b", "size": 2_491_876_774, "capabilities": ["completion"]},
    {"name": "zeta-tools:8b", "size": 5_225_388_164, "capabilities": ["completion", "tools"]},
]


def _evidence(model: str, capability: str, outcome: str) -> CapabilityEvidence:
    return CapabilityEvidence(
        subject_worker="any",
        subject_model=model,
        capability=capability,
        outcome=outcome,
        tier=2,
        freshness="CURRENT",
        recorded_at="2026-08-14T00:00:00Z",
        validator_identity="test",
    )


class ModelSelectionTests(unittest.TestCase):
    def test_exact_configured_model_wins(self) -> None:
        selection = select_installed_model("coder", "zeta-tools:8b", INVENTORY)
        assert selection is not None
        self.assertEqual(selection.selected_model, "zeta-tools:8b")
        self.assertIn("operator preference is installed", selection.reason)

    def test_chat_role_uses_smallest_installed_model_when_configured_tag_is_missing(self) -> None:
        selection = select_installed_model("e2b", "missing-small:1b", INVENTORY)
        assert selection is not None
        self.assertEqual(selection.selected_model, "epsilon-tiny:3.8b")
        self.assertNotIn("qwen", selection.reason)
        self.assertNotIn("gemma", selection.reason)

    def test_reviewer_prefers_largest_eligible_model_without_name_heuristics(self) -> None:
        selection = select_installed_model("reviewer", "missing-reviewer:12b", INVENTORY)
        assert selection is not None
        self.assertEqual(selection.selected_model, "zeta-tools:8b")
        self.assertIn("provider-reported tools", selection.reason)

    def test_coder_uses_provider_tools_claim_not_brand_hints(self) -> None:
        inventory = [item for item in INVENTORY if item["name"] != "zeta-tools:8b"]
        selection = select_installed_model("coder", "missing:coder", inventory)
        assert selection is not None
        self.assertEqual(selection.selected_model, "delta-mid:8b")
        self.assertNotIn("code-hinted", selection.reason)
        self.assertNotIn("granite", selection.reason)

    def test_unknown_model_tag_is_accepted_as_opaque_identity(self) -> None:
        inventory = [{"name": "totally-unknown-model:7b", "size": 3, "capabilities": ["completion", "tools"]}]
        selection = select_installed_model("coder", "absent:tag", inventory)
        assert selection is not None
        self.assertEqual(selection.selected_model, "totally-unknown-model:7b")
        self.assertIn("opaque implementation identity", selection.reason)

    def test_misleading_coder_name_is_not_capability_proof(self) -> None:
        inventory = [
            {"name": "excellent-coder:latest", "size": 20, "capabilities": ["completion"]},
            {"name": "quiet-tool-model:2b", "size": 2, "capabilities": ["completion", "tools"]},
        ]
        selection = select_installed_model("coder", "missing:coder", inventory)
        assert selection is not None
        self.assertEqual(selection.selected_model, "quiet-tool-model:2b")
        self.assertEqual(
            {item["model"] for item in selection.rejected},
            {"excellent-coder:latest"},
        )

    def test_observed_failure_excludes_candidate(self) -> None:
        evidence = (_evidence("zeta-tools:8b", "tool_call", "FAIL"),)
        selection = select_installed_model("coder", "missing:coder", INVENTORY, evidence=evidence)
        assert selection is not None
        self.assertEqual(selection.selected_model, "delta-mid:8b")

    def test_observed_success_outranks_provider_claim(self) -> None:
        evidence = (_evidence("beta-chat:4b", "tool_call", "PASS"),)
        inventory = [
            {"name": "beta-chat:4b", "size": 4, "capabilities": ["completion"]},
            {"name": "claimed-tools:8b", "size": 8, "capabilities": ["completion", "tools"]},
        ]
        selection = select_installed_model("coder", "missing:coder", inventory, evidence=evidence)
        assert selection is not None
        self.assertEqual(selection.selected_model, "beta-chat:4b")
        self.assertIn("observed tool_call=PASS", selection.reason)

    def test_empty_inventory_does_not_invent_a_model(self) -> None:
        self.assertIsNone(select_installed_model("reviewer", "missing:model", []))

    def test_unfamiliar_family_metadata_is_ignored(self) -> None:
        inventory = [
            {
                "name": "example/new-model:7b",
                "size": 7,
                "capabilities": ["completion", "tools"],
                "details": {"family": "completely-unknown-family"},
            }
        ]
        selection = select_installed_model("e4b", "other:1b", inventory)
        assert selection is not None
        self.assertEqual(selection.selected_model, "example/new-model:7b")


if __name__ == "__main__":
    unittest.main()
