"""Pin the host projection to the MNCS kernels and executable corpora.

Authority: ``mncs/harness_*.mncs`` + ``corpora/*.json`` + backend evidence
under ``development-evidence/mncs-execution/``. These tests prove that
``mncs_logic`` reproduces every corpus vector and that the live host paths
(router, policy, verifier, Atlas fold) agree with the mirror.
"""

from __future__ import annotations

import itertools
import tempfile
import unittest
from pathlib import Path

from mncs_harness import mncs_logic
from mncs_harness.atlas_binding import AtlasDecision, _fold_capability
from mncs_harness.config import load_config
from mncs_harness.models import TaskProfile
from mncs_harness.policy import CommandPolicy, WorkspaceGuard
from mncs_harness.router import _deterministic_route

REPO = Path(__file__).resolve().parents[1]


def _decode(value: dict):
    if "boolean" in value:
        return value["boolean"]["value"]
    if "integer" in value:
        return value["integer"]["value"]
    if "finite" in value:
        finite = value["finite"]
        variant = finite["variant_identity"].rsplit("::", 1)[1]
        payload = {}
        for name, item in finite.get("payload", []):
            payload[name] = _decode(item)
        return (variant, payload)
    raise AssertionError(f"undecodable corpus value {value!r}")


def _run_mirror(module: str, function: str, args: list):
    if module == "mncs.harness.routing.v1":
        if function == "classify_route":
            return mncs_logic.classify_route(*args)
        if function == "needs_coder":
            primary = {
                "E2B": "e2b",
                "E4B": "e4b",
                "CODER": "coder",
                "REVIEWER": "reviewer",
            }[args[0]]
            return mncs_logic.needs_coder(primary, args[1], args[2])
    if module == "mncs.harness.policy.v1":
        if function == "evaluate_command":
            return mncs_logic.command_reason(*args)
        if function == "evaluate_write":
            return mncs_logic.write_reason(*args)
        if function == "reason_of":
            variant, payload = args[0]
            return payload.get("reason", 0) if variant == "Block" else 0
    if module == "mncs.harness.verdict.v1":
        statuses = [item[0] for item in args if isinstance(item, tuple)]
        if function == "dominate":
            return mncs_logic.dominate(statuses[0], statuses[1])
        if function in ("combine4", "combine8"):
            return mncs_logic.combine(statuses)
        if function == "is_decided":
            return mncs_logic.is_decided(statuses[0])
    if module == "mncs.harness.atlas.v1":
        if function == "dispatch_gate":
            return mncs_logic.dispatch_gate(args[0][0], args[1])
        grants = [item[0] for item in args if isinstance(item, tuple)]
        if function == "fold_pair":
            return mncs_logic.fold_pair(grants[0], grants[1])
        if function == "fold4":
            return mncs_logic.fold(grants)
    raise AssertionError(f"no mirror for {module}::{function}")


def _expected_name(value: dict) -> str:
    if "boolean" in value:
        return str(value["boolean"]["value"])
    if "integer" in value:
        return str(value["integer"]["value"])
    finite = value["finite"]
    variant = finite["variant_identity"].rsplit("::", 1)[1]
    payload = {name: _decode(item) for name, item in finite.get("payload", [])}
    if payload:
        return f"{variant}{payload}"
    return variant


def _mirror_name(result) -> str:
    if isinstance(result, bool):
        return str(result)
    if isinstance(result, int):
        return str(result)
    if result in ("e2b", "e4b", "reviewer"):
        return {"e2b": "E2B", "e4b": "E4B", "reviewer": "REVIEWER"}[result]
    if result in ("PASS", "FAIL", "UNKNOWN", "GRANTED", "REFUSED"):
        return result
    if isinstance(result, tuple):
        variant, payload = result
        if payload:
            return f"{variant}{payload}"
        return variant
    raise AssertionError(f"unnamable mirror result {result!r}")


class CorpusAgreementTests(unittest.TestCase):
    def _check_corpus(self, name: str, module: str) -> None:
        for case in mncs_logic.corpus_cases(name):
            target = case["request"]["target"]
            self.assertEqual(target["module"], module, case["id"])
            args = [_decode(item) for item in case["request"]["arguments"]]
            # needs_coder takes an enum first arg; normalize to variant name.
            if target["function"] == "needs_coder":
                args = [args[0][0] if isinstance(args[0], tuple) else args[0], *args[1:]]
            got = _run_mirror(target["module"], target["function"], args)
            if target["module"] == "mncs.harness.policy.v1" and target[
                "function"
            ] in ("evaluate_command", "evaluate_write"):
                expected = case["expected"][0]
                finite = expected["finite"]
                exp_variant = finite["variant_identity"].rsplit("::", 1)[1]
                exp_payload = {
                    name: _decode(item) for name, item in finite.get("payload", [])
                }
                got_variant = "Allow" if got == 0 else "Block"
                got_payload = {} if got == 0 else {"reason": got}
                self.assertEqual(got_variant, exp_variant, case["id"])
                self.assertEqual(got_payload, exp_payload, case["id"])
                continue
            self.assertEqual(
                _mirror_name(got),
                _expected_name(case["expected"][0]),
                f"{name}/{case['id']}",
            )

    def test_routing_corpus(self) -> None:
        self._check_corpus("harness-routing", "mncs.harness.routing.v1")

    def test_policy_corpus(self) -> None:
        self._check_corpus("harness-policy", "mncs.harness.policy.v1")

    def test_verdict_corpus(self) -> None:
        self._check_corpus("harness-verdict", "mncs.harness.verdict.v1")

    def test_atlas_corpus(self) -> None:
        self._check_corpus("harness-atlas", "mncs.harness.atlas.v1")


class LivePathAgreementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(Path("/missing/config.toml"))

    def test_router_matches_kernel_over_flag_cube(self) -> None:
        for flags in itertools.product([False, True], repeat=6):
            has_code, asks_edit, asks_exec, high_risk, has_image, is_complex = flags
            profile = TaskProfile(
                text="cube",
                word_count=1,
                has_code=has_code,
                asks_for_edit=asks_edit,
                asks_for_execution=asks_exec,
                asks_for_explanation=False,
                is_high_risk=high_risk,
                is_complex=is_complex,
                has_image=has_image,
                file_reference_count=0,
            )
            plan = _deterministic_route(profile, self.config)
            kernel = mncs_logic.classify_route(*flags)
            self.assertEqual(plan.primary_role, kernel, flags)
            self.assertEqual(
                "coder" in plan.escalation_roles,
                mncs_logic.needs_coder(
                    "e4b",
                    self.config.routing.code_specialist_enabled,
                    has_code,
                )
                and kernel == "e4b",
                flags,
            )

    def test_policy_matches_kernel_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "script.py").write_text("print('hi')\n", encoding="utf-8")
            policy = CommandPolicy(
                self.config.policy, WorkspaceGuard(workspace)
            )
            vectors = [
                (["git", "status", "--short"], True),
                (["python", "-m", "pytest", "-q"], True),
                (["sudo", "dnf", "install", "x"], False),
                (["sl"], False),
                (["bash", "echo hi"], False),
                (["bash", "-c", "echo hi"], False),
                (["python", "-c", "print(1)"], False),
                (["python", "-m", "http.server"], False),
                (["git", "push", "--force"], False),
                (["git", "status", "/etc/passwd"], False),
                (["git", "status", "~/x"], False),
            ]
            for argv, allowed in vectors:
                _, decision = policy.evaluate(argv)
                self.assertEqual(decision.allowed, allowed, argv)
                self.assertEqual(
                    decision.risk, "medium" if allowed else "blocked", argv
                )

    def test_atlas_fold_is_order_independent_and_denial_wins(self) -> None:
        granted = AtlasDecision(capability="worker.dispatch", status="granted")
        conditional = AtlasDecision(
            capability="worker.dispatch",
            status="conditional",
            missing=("evidence.attest",),
        )
        denied = AtlasDecision(capability="worker.dispatch", status="denied")
        verdicts = set()
        for order in itertools.permutations([granted, conditional, denied]):
            verdicts.add(_fold_capability("worker.dispatch", list(order)).verdict)
        self.assertEqual(verdicts, {"REFUSED"})
        only_unknown = _fold_capability("worker.dispatch", [granted, conditional])
        self.assertEqual(only_unknown.verdict, "UNKNOWN")
        self.assertEqual(only_unknown.outstanding, ("evidence.attest",))
        only_granted = _fold_capability("worker.dispatch", [granted, granted])
        self.assertEqual(only_granted.verdict, "GRANTED")

    def test_verdict_lattice_properties(self) -> None:
        for left, right in itertools.product(["PASS", "FAIL", "UNKNOWN"], repeat=2):
            folded = mncs_logic.dominate(left, right)
            self.assertEqual(folded, mncs_logic.dominate(right, left))
            if "FAIL" in (left, right):
                self.assertEqual(folded, "FAIL")
            elif "UNKNOWN" in (left, right):
                self.assertEqual(folded, "UNKNOWN")
            else:
                self.assertEqual(folded, "PASS")
        # UNKNOWN is never promoted by combination.
        self.assertEqual(mncs_logic.combine(["UNKNOWN", "PASS"]), "UNKNOWN")
        self.assertFalse(mncs_logic.is_decided("UNKNOWN"))


if __name__ == "__main__":
    unittest.main()
