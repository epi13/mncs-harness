from __future__ import annotations

import unittest

from epi13_local_harness.model_selection import select_installed_model


INVENTORY = [
    {"name": "devstral-small-2:24b", "size": 15_177_374_099},
    {"name": "gemma4:e4b", "size": 9_608_350_718},
    {"name": "gpt-oss:20b", "size": 13_793_441_244},
    {"name": "granite3.3:8b", "size": 4_942_891_653},
    {"name": "phi4-mini:3.8b", "size": 2_491_876_774},
    {"name": "qwen3:8b", "size": 5_225_388_164},
]


class ModelSelectionTests(unittest.TestCase):
    def test_exact_configured_model_wins(self) -> None:
        selection = select_installed_model("coder", "qwen3:8b", INVENTORY)
        assert selection is not None
        self.assertEqual(selection.selected_model, "qwen3:8b")
        self.assertIn("configured model is installed", selection.reason)

    def test_chat_role_uses_smallest_installed_model_when_configured_tag_is_missing(self) -> None:
        selection = select_installed_model("e2b", "gemma4:e2b", INVENTORY)
        assert selection is not None
        self.assertEqual(selection.selected_model, "phi4-mini:3.8b")

    def test_reviewer_avoids_code_specialist_when_large_general_model_exists(self) -> None:
        selection = select_installed_model("reviewer", "gemma4:12b", INVENTORY)
        assert selection is not None
        self.assertEqual(selection.selected_model, "gpt-oss:20b")

    def test_coder_fallback_prefers_code_hinted_model_within_bound(self) -> None:
        inventory = [item for item in INVENTORY if item["name"] != "qwen3:8b"]
        selection = select_installed_model("coder", "missing:coder", inventory)
        assert selection is not None
        self.assertEqual(selection.selected_model, "granite3.3:8b")

    def test_empty_inventory_does_not_invent_a_model(self) -> None:
        self.assertIsNone(select_installed_model("reviewer", "missing:model", []))


if __name__ == "__main__":
    unittest.main()
