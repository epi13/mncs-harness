"""Atlas-bound execution requirements: hostile/bypass matrix (epi13/mncs-harness#57).

Every row is machine-readable: BindingError at load, or an Acceptance
verdict of GRANTED / UNKNOWN / REFUSED. No exception substitutes for a
verdict once the envelope loads.
"""

import sys
from pathlib import Path as _TestPath

sys.path.insert(0, str(_TestPath(__file__).resolve().parent))

import hashlib
import itertools
import json

import pytest
from atlas_fixtures import SCOPE, issue, load, payload

from epi13_local_harness.atlas_binding import (
    LEGACY_PROOF_ORIGIN,
    Acceptance,
    BindingError,
    ExecutionRequirement,
    requirement_identity,
)

SCHEMA = "mncs.execution-requirement/0.2"


# 1. No Atlas binding -------------------------------------------------------
def test_01_no_binding_is_unloadable():
    with pytest.raises(BindingError):
        load(atlas_decisions=[])


# 2. Fabricated Atlas-shaped grant ------------------------------------------
def test_02_fabricated_grant_without_digest_is_unloadable():
    forged = issue("tests.execute", "granted")
    del forged["decision_digest"]
    del forged["decision_digest_alg"]
    with pytest.raises(BindingError):
        load(atlas_decisions=[forged])


# 3. Tampered decision after issuance ----------------------------------------
def test_03_tampered_status_breaks_digest():
    tampered = issue("tests.execute", "denied")
    tampered["status"] = "granted"  # digest still covers "denied"
    with pytest.raises(BindingError):
        load(atlas_decisions=[tampered])


# 4-5. Conflicting duplicates, both orders -----------------------------------
def test_04_conflicting_duplicates_refuse_granted_first():
    payload = load(
        atlas_decisions=[
            issue("tests.execute", "granted"),
            issue("tests.execute", "denied"),
        ],
        legs=[{"name": "cpu", "needs": ["tests.execute"]}],
    )
    assert payload.accept("cpu").verdict == "REFUSED"


def test_05_conflicting_duplicates_refuse_denied_first():
    payload = load(
        atlas_decisions=[
            issue("tests.execute", "denied"),
            issue("tests.execute", "granted"),
        ],
        legs=[{"name": "cpu", "needs": ["tests.execute"]}],
    )
    assert payload.accept("cpu").verdict == "REFUSED"


def test_05b_duplicate_permutations_are_deterministic():
    trio = [
        issue("tests.execute", "granted"),
        issue("tests.execute", "conditional", missing=["execution.target"]),
        issue("tests.execute", "denied"),
    ]
    verdicts = set()
    for order in itertools.permutations(trio):
        payload = load(
            atlas_decisions=list(order),
            legs=[{"name": "cpu", "needs": ["tests.execute"]}],
        )
        verdicts.add(payload.accept("cpu").verdict)
    assert verdicts == {"REFUSED"}


# 6-8. Wrong session context --------------------------------------------------
def test_06_decision_from_another_session_is_unloadable():
    foreign = issue("tests.execute", "granted")
    body = {k: v for k, v in foreign.items() if k != "decision_digest"}
    body["session"] = {"participant": "other-agent", "scope": SCOPE}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    foreign["session"] = body["session"]
    foreign["decision_digest"] = hashlib.sha256(canonical.encode()).hexdigest()
    with pytest.raises(BindingError):
        load(atlas_decisions=[foreign])


def test_07_wrong_participant_is_unloadable():
    with pytest.raises(BindingError):
        load(session_participant="intruder-9")


def test_08_wrong_scope_is_unloadable():
    with pytest.raises(BindingError):
        load(session_scope="repo(other)")


# 9. Wrong artifact digest ----------------------------------------------------
def test_09_artifact_mismatch_refuses():
    acceptance = load().accept("cpu", observed_artifact="sha256:" + "c" * 64)
    assert acceptance.verdict == "REFUSED"
    assert "mismatch" in acceptance.reason


# 10. Wrong execution target ---------------------------------------------------
def test_10_target_mismatch_refuses():
    payload = load(
        atlas_decisions=[
            issue(
                "worker.dispatch",
                "granted",
                execution_target="mncs:target:cuda-0.1",
            )
        ],
        legs=[
            {
                "name": "gpu",
                "needs": ["worker.dispatch"],
                "target": "mncs:target:cpu-0.1",
            }
        ],
    )
    acceptance = payload.accept("gpu")
    assert acceptance.verdict == "REFUSED"
    assert "target mismatch" in acceptance.reason


# 10b. Multiple authorized targets are placement options ---------------------------
def test_10b_authorized_target_options():
    payload = load(
        atlas_decisions=[
            issue("worker.dispatch", "granted", execution_target="worker-01"),
            issue("worker.dispatch", "granted", execution_target="worker-02"),
        ],
        legs=[
            {"name": "first", "needs": ["worker.dispatch"], "target": "worker-01"},
            {"name": "second", "needs": ["worker.dispatch"], "target": "worker-02"},
            {"name": "outside", "needs": ["worker.dispatch"], "target": "worker-09"},
        ],
    )
    assert payload.accept("first").verdict == "GRANTED"
    assert payload.accept("second").verdict == "GRANTED"
    assert payload.accept("outside").verdict == "REFUSED"


# 11-12. Stale / consumer-declared proof ---------------------------------------
def _bound_gpu():
    return load(
        atlas_decisions=[issue("worker.dispatch", "granted", execution_target="worker-01")],
        legs=[
            {
                "name": "gpu",
                "needs": ["worker.dispatch"],
                "target": "worker-01",
            }
        ],
    )


def test_11_stale_target_evidence_is_unknown():
    acceptance = _bound_gpu().confirm_execution(
        "gpu", actual_target="worker-01", proof_origin="fabric-inventory", proof_fresh=False
    )
    assert acceptance.verdict == "UNKNOWN"
    assert "stale" in acceptance.reason


def test_12_consumer_declared_observation_refuses():
    acceptance = _bound_gpu().confirm_execution(
        "gpu", actual_target="worker-01", proof_origin="consumer-declared"
    )
    assert acceptance.verdict == "REFUSED"
    assert "not operator authority" in acceptance.reason


def test_12b_legacy_match_is_unknown_never_granted():
    acceptance = _bound_gpu().confirm_execution(
        "gpu", actual_target="worker-01", proof_origin=LEGACY_PROOF_ORIGIN
    )
    assert acceptance.verdict == "UNKNOWN"
    assert "predates observation provenance" in acceptance.reason


def test_12c_legacy_mismatch_still_refuses():
    acceptance = _bound_gpu().confirm_execution(
        "gpu", actual_target="worker-09", proof_origin=LEGACY_PROOF_ORIGIN
    )
    assert acceptance.verdict == "REFUSED"
    assert "target mismatch" in acceptance.reason


def test_12d_legacy_without_actual_evidence_is_unknown():
    acceptance = _bound_gpu().confirm_execution("gpu", proof_origin=LEGACY_PROOF_ORIGIN)
    assert acceptance.verdict == "UNKNOWN"
    assert "predates observation provenance" in acceptance.reason


# 13. Missing conditional evidence ----------------------------------------------
def test_13_conditional_without_evidence_is_unknown():
    payload = load(
        atlas_decisions=[issue("tests.execute", "conditional", missing=["execution.target"])],
        legs=[{"name": "cpu", "needs": ["tests.execute"]}],
    )
    assert payload.accept("cpu").verdict == "UNKNOWN"


# 14. Denied capability ----------------------------------------------------------
def test_14_denied_capability_refuses_leg():
    acceptance = load().accept("fetch")
    assert acceptance.verdict == "REFUSED"
    assert "atlas denied" in acceptance.reason


# 15. Leg needs undecided capability ----------------------------------------------
def test_15_undecided_need_refuses():
    payload = load(legs=[{"name": "sneaky", "needs": ["capability.grant"]}])
    acceptance = payload.accept("sneaky")
    assert acceptance.verdict == "REFUSED"
    assert "bypass" in acceptance.reason


# 16. Valid grant + matching execution ----------------------------------------------
def test_16_valid_grant_and_matching_execution():
    acceptance = _bound_gpu().confirm_execution(
        "gpu", actual_target="worker-01", proof_origin="fabric-inventory"
    )
    assert acceptance.verdict == "GRANTED", acceptance.reason


# 17. Valid conditional + complete evidence ----------------------------------------------
def test_17_conditional_with_bound_target_is_granted():
    payload = load(
        atlas_decisions=[
            issue(
                "tests.execute",
                "conditional",
                missing=["execution.target"],
                execution_target="mncs:target:research-bytecode-0.1",
            )
        ],
        legs=[
            {
                "name": "cpu",
                "needs": ["tests.execute"],
                "target": "mncs:target:research-bytecode-0.1",
            }
        ],
    )
    acceptance = payload.accept("cpu")
    assert acceptance.verdict == "GRANTED", acceptance.reason


# 18. Valid denial ------------------------------------------------------------------
def test_18_valid_denial_means_refused():
    acceptance = load().accept("fetch")
    assert isinstance(acceptance, Acceptance)
    assert acceptance.verdict == "REFUSED"


# 19. Replay in an unrelated context --------------------------------------------------
def test_19_replay_in_unrelated_scope_is_unloadable():
    with pytest.raises(BindingError):
        load(
            atlas_decisions=[issue("tests.execute", "granted")],
            session_scope="repo(unrelated)",
        )


# Cross-language golden vector --------------------------------------------------
def test_golden_requirement_identity_matches_language():
    legs = [{"name": "cpu", "needs": ["b", "a"]}]
    assert (
        requirement_identity("e2e-001", "sha256:aa", "sha256:bb", ["dd"], legs)
        == "2b56d74b42c0ed29cec46f392330e9e732524980fa39ef1a76fa0612a3ab54ae"
    )


def test_requirement_id_is_stable_and_order_free():
    first = load()
    envelope = payload()
    envelope["atlas_decisions"] = list(reversed(envelope["atlas_decisions"]))
    envelope["legs"] = list(reversed(envelope["legs"]))
    reordered = ExecutionRequirement.from_dict(envelope)
    assert first.requirement_id == reordered.requirement_id
    assert first.accept("cpu").verdict == reordered.accept("cpu").verdict == "GRANTED"
