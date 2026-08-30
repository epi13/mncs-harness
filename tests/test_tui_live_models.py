from __future__ import annotations

import unittest

from epi13_local_harness.fabric import FabricStatus
from epi13_local_harness.tui import (
    _common_fabric_inventory,
    fabric_model_inventory_table,
    fabric_status_summary,
    worker_model_options,
)


class TuiLiveModelTests(unittest.TestCase):
    def _status(self) -> FabricStatus:
        return FabricStatus(
            enabled=True,
            state="available",
            controller_id="mncs-harness",
            workers=(
                {
                    "worker_id": "worker-01-windows",
                    "source": "remote",
                    "availability": "AVAILABLE",
                    "model_count": 3,
                    "model_names": [
                        "gemma4:e4b",
                        "gpt-oss:20b",
                        "new-model:latest",
                    ],
                    "model_inventory": [
                        {
                            "name": "gemma4:e4b",
                            "size": 9_608_350_718,
                            "details": {"family": "gemma", "parameter_size": "4B"},
                        },
                        {"name": "gpt-oss:20b", "size": 13_793_441_244},
                        {"name": "new-model:latest", "size": 4_000_000_000},
                    ],
                },
            ),
        )

    def test_common_inventory_keeps_unconfigured_worker_models(self) -> None:
        inventory = _common_fabric_inventory(self._status())
        names = [str(item.get("name")) for item in inventory]
        self.assertEqual(
            names,
            ["gemma4:e4b", "gpt-oss:20b", "new-model:latest"],
        )

    def test_fabric_summary_reports_live_model_count(self) -> None:
        summary = fabric_status_summary(self._status())
        self.assertIn("worker-models=3", summary)

    def test_inventory_table_renders_every_reported_model(self) -> None:
        table = fabric_model_inventory_table(self._status())
        self.assertEqual(len(table.rows), 3)

    def test_operator_model_options_use_union_and_mark_loaded_state(self) -> None:
        first = dict(self._status().workers[0])
        first["model_inventory"][0]["loaded"] = True
        second = {
            "worker_id": "arm-worker",
            "source": "remote",
            "availability": "AVAILABLE",
            "model_inventory": [{"name": "tiny:1b", "size": 1_000_000_000}],
        }
        status = FabricStatus(True, "available", "controller", workers=(first, second))
        options = worker_model_options(status)
        self.assertEqual({value for _label, value in options}, {
            "gemma4:e4b", "gpt-oss:20b", "new-model:latest", "tiny:1b"
        })
        self.assertTrue(next(label for label, value in options if value == "gemma4:e4b").startswith("●"))
        arm_only = worker_model_options(status, "arm-worker")
        self.assertEqual(arm_only, [("○ tiny:1b — arm-worker", "tiny:1b")])


if __name__ == "__main__":
    unittest.main()
