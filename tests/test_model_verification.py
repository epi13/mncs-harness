import unittest

from epi13_local_harness.model_verification import (
    discover_candidates,
    planned_probes,
    verify_candidate,
)


class ModelVerificationTests(unittest.TestCase):
    def test_unknown_model_is_verified_like_any_other_identity(self) -> None:
        item = {"name": "totally-unknown-model:7b", "capabilities": ["completion", "tools"]}
        results = []

        def probe(name: str, _item: dict) -> tuple[str, str | None]:
            results.append(name)
            return "PASS", "fixture"

        outcome = verify_candidate(
            "worker-z",
            item,
            probes={"reachability": probe, "marker_response": probe, "tool_call": probe},
            tiers={0, 1, 2},
        )
        self.assertEqual(outcome.model, "totally-unknown-model:7b")
        self.assertEqual(results, ["totally-unknown-model:7b"] * 3)
        self.assertTrue(all(record.outcome == "PASS" for record in outcome.records))

    def test_code_edit_failure_is_not_model_invalid(self) -> None:
        item = {"name": "tiny-chat:1b", "capabilities": ["completion"]}
        outcome = verify_candidate(
            "worker-a",
            item,
            probes={"reachability": lambda _n, _i: ("PASS", None)},
            tiers={0, 2},
        )
        capabilities = {record.capability for record in outcome.records}
        self.assertEqual(capabilities, {"reachability"})
        self.assertNotIn("tool_call", capabilities)

    def test_discover_candidates_skips_stale_and_unavailable(self) -> None:
        workers = [
            {
                "worker_id": "up",
                "availability": "AVAILABLE",
                "capability_inventory_status": "CURRENT",
                "model_inventory": [{"name": "example/new-model:7b"}],
            },
            {
                "worker_id": "stale",
                "availability": "AVAILABLE",
                "capability_inventory_status": "STALE",
                "model_inventory": [{"name": "stale-model:1b"}],
            },
            {
                "worker_id": "down",
                "availability": "UNAVAILABLE",
                "capability_inventory_status": "CURRENT",
                "model_inventory": [{"name": "down-model:1b"}],
            },
        ]
        found = discover_candidates(workers)
        self.assertEqual(found, [("up", {"name": "example/new-model:7b"})])

    def test_unconventional_probe_boundary_is_explicit_and_visible(self) -> None:
        item = {"name": "bridge-model:1b", "capabilities": ["completion"]}

        def unusual(_name: str, _item: dict) -> tuple[str, str | None]:
            return "UNKNOWN", "compatibility-bridge: no native contract yet"

        outcome = verify_candidate(
            "worker-a",
            item,
            probes={"reachability": unusual},
            tiers={0},
        )
        record = outcome.records[0]
        self.assertEqual(record.outcome, "UNKNOWN")
        self.assertEqual(record.failure_class, "compatibility-bridge: no native contract yet")
        self.assertEqual(record.validator_identity, "mncs-harness/model-verify/v1")

    def test_planned_probes_do_not_invent_tool_tests(self) -> None:
        item = {"name": "chat-only:1b", "capabilities": ["completion"]}
        self.assertEqual(planned_probes(item, tiers={0, 2}), ((0, "reachability"),))


if __name__ == "__main__":
    unittest.main()
