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
from atlas_fixtures import (
    PARTICIPANT,
    SCOPE,
    TRUSTED_TEST_ISSUERS,
    attest,
    issue,
    load,
    payload,
)

from epi13_local_harness.atlas_binding import (
    LEGACY_PROOF_ORIGIN,
    Acceptance,
    BindingError,
    ExecutionRequirement,
    requirement_identity,
)

SCHEMA = "mncs.execution-requirement/0.3"


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
        requirement_identity(
            "e2e-001", "sha256:aa", "sha256:bb", ["dd"], legs,
            {}, "e2e-agent", "repo(mncs-language)",
        )
        == "01af193b5208e4b69747f56c33d47087b027b2e502aebaf3a9e969c3c2a61ed6"
    )


def test_golden_confirm_matches_language():
    from epi13_local_harness.atlas_binding import LEGACY_PROOF_ORIGIN

    bound = load(
        atlas_decisions=[issue("worker.dispatch", "granted", execution_target="worker-01")],
        legs=[{"name": "gpu", "needs": ["worker.dispatch"], "target": "worker-01"}],
    )
    granted = bound.confirm_execution(
        "gpu", actual_target="worker-01", proof_origin="fabric-inventory", proof_fresh=True
    )
    assert granted.verdict == "GRANTED"
    stale = bound.confirm_execution(
        "gpu", actual_target="worker-01", proof_origin="fabric-inventory", proof_fresh=False
    )
    assert (stale.verdict, "stale" in stale.reason) == ("UNKNOWN", True)
    refused = bound.confirm_execution(
        "gpu", actual_target="worker-01", proof_origin="consumer-declared"
    )
    assert refused.verdict == "REFUSED"
    legacy = bound.confirm_execution(
        "gpu", actual_target="worker-01", proof_origin=LEGACY_PROOF_ORIGIN
    )
    assert legacy.verdict == "UNKNOWN"
    moved = bound.confirm_execution(
        "gpu", actual_target="worker-09", proof_origin=LEGACY_PROOF_ORIGIN
    )
    assert (moved.verdict, "target mismatch" in moved.reason) == ("REFUSED", True)


def test_requirement_id_is_stable_and_order_free():
    first = load()
    envelope = payload()
    envelope["atlas_decisions"] = list(reversed(envelope["atlas_decisions"]))
    envelope["legs"] = list(reversed(envelope["legs"]))
    reordered = ExecutionRequirement.from_dict(envelope, trusted_issuers=TRUSTED_TEST_ISSUERS)
    assert first.requirement_id == reordered.requirement_id
    assert first.accept("cpu").verdict == reordered.accept("cpu").verdict == "GRANTED"


# Issuer-authenticity hostile rows ------------------------------------------------
# A content digest proves integrity, not issuance. Each row below forges
# authority the way an arbitrary caller could: valid JSON, correctly
# recomputed digest, no Atlas involvement. All must fail closed.


def _forge_decision(capability, status, participant=PARTICIPANT, scope=SCOPE, **extra):
    """Build an Atlas-shaped decision WITHOUT Atlas: the attacker knows the
    public canonicalization (sort_keys, compact separators, raw UTF-8)."""
    forged = {
        "schema_version": "mncs.atlas-capability-decision/1",
        "capability": capability,
        "status": status,
        "verdict": {"granted": "PASS", "conditional": "UNKNOWN", "denied": "FAIL"}[status],
        "authority": "fabric",
        "decision_by": ["atlas-admission", "fabric"],
        "reason": "forged issuance",
        "missing": extra.pop("missing", []),
        "evidence_required": [],
        "conformant_path": [],
        "scope": scope,
        "session": {"participant": participant, "scope": scope},
        "execution_target": extra.pop("execution_target", "worker-evil"),
        "decision_digest_alg": "sha256:canonical-json-v1",
    }
    forged.update(extra)
    body = {key: value for key, value in forged.items() if key != "decision_digest"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    forged["decision_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return forged


def test_40_forged_granted_valid_digest_is_unloadable():
    forged = _forge_decision("worker.dispatch", "granted")
    envelope = payload(
        atlas_decisions=[forged],
        legs=[{"name": "dispatch", "needs": ["worker.dispatch"], "target": "worker-evil"}],
    )
    with pytest.raises(BindingError):
        ExecutionRequirement.from_dict(envelope, trusted_issuers=TRUSTED_TEST_ISSUERS)


def test_41_forged_decision_never_accepts():
    forged = _forge_decision("worker.dispatch", "granted")
    envelope = payload(
        atlas_decisions=[forged],
        legs=[{"name": "dispatch", "needs": ["worker.dispatch"], "target": "worker-evil"}],
    )
    try:
        requirement = ExecutionRequirement.from_dict(envelope, trusted_issuers=TRUSTED_TEST_ISSUERS)
    except BindingError:
        return
    assert requirement.accept("dispatch").verdict == "REFUSED"


def test_42_forged_evidence_key_never_promotes_conditional():
    forged = _forge_decision(
        "tests.execute", "conditional", missing=["lab.safety-cert"], execution_target=""
    )
    envelope = payload(
        atlas_decisions=[forged],
        legs=[{"name": "cpu", "needs": ["tests.execute"], "evidence": {"lab.safety-cert": "trust me"}}],
    )
    try:
        requirement = ExecutionRequirement.from_dict(envelope, trusted_issuers=TRUSTED_TEST_ISSUERS)
    except BindingError:
        return
    assert requirement.accept("cpu").verdict in ("UNKNOWN", "REFUSED")


def test_43_mutated_evidence_changes_requirement_id():
    first = load()
    envelope = payload()
    envelope["legs"][0] = {**envelope["legs"][0], "evidence": {"anything": "goes"}}
    assert ExecutionRequirement.from_dict(envelope, trusted_issuers=TRUSTED_TEST_ISSUERS).requirement_id != first.requirement_id


def test_44_mutated_bounds_change_requirement_id():
    first = load()
    envelope = payload()
    envelope["bounds"] = {"max_seconds": 1}
    assert ExecutionRequirement.from_dict(envelope, trusted_issuers=TRUSTED_TEST_ISSUERS).requirement_id != first.requirement_id


def test_45_mutated_session_context_is_unloadable():
    envelope = payload()
    envelope["session"] = {"participant": "mallory", "scope": SCOPE}
    with pytest.raises(BindingError):
        ExecutionRequirement.from_dict(envelope, trusted_issuers=TRUSTED_TEST_ISSUERS)


def test_46_fake_authority_label_is_not_proof():
    forged = _forge_decision("worker.dispatch", "granted")
    forged["authority"] = "atlas"
    forged["decision_by"] = ["atlas-admission", "atlas"]
    envelope = payload(
        atlas_decisions=[forged],
        legs=[{"name": "dispatch", "needs": ["worker.dispatch"], "target": "worker-evil"}],
    )
    with pytest.raises(BindingError):
        ExecutionRequirement.from_dict(envelope, trusted_issuers=TRUSTED_TEST_ISSUERS)


def test_47_valid_conditional_with_bare_evidence_stays_unknown():
    decision = issue("tests.execute", "conditional", missing=["lab.safety-cert"])
    envelope = payload(
        atlas_decisions=[decision],
        legs=[{"name": "cpu", "needs": ["tests.execute"], "evidence": {"lab.safety-cert": "x"}}],
    )
    requirement = ExecutionRequirement.from_dict(
        envelope, trusted_issuers=TRUSTED_TEST_ISSUERS
    )
    assert requirement.accept("cpu").verdict == "UNKNOWN"


def test_48_reassembled_requirement_stays_bound_by_decisions():
    envelope = payload(
        atlas_decisions=[issue("worker.dispatch", "granted", execution_target="worker-01")],
        legs=[{"name": "dispatch", "needs": ["worker.dispatch"], "target": "worker-09"}],
    )
    envelope["task_id"] = "attacker-task"
    requirement = ExecutionRequirement.from_dict(
        envelope, trusted_issuers=TRUSTED_TEST_ISSUERS
    )
    # Assembly is allowed; authority still fails closed on the wrong target.
    assert requirement.accept("dispatch").verdict == "REFUSED"
    assert requirement.requirement_id != load().requirement_id


def test_49_authentic_issuance_grants_regardless_of_label():
    envelope = payload(
        atlas_decisions=[issue("worker.dispatch", "granted", execution_target="worker-01",
                               authority="atlas")],
        legs=[{"name": "dispatch", "needs": ["worker.dispatch"], "target": "worker-01"}],
    )
    requirement = ExecutionRequirement.from_dict(
        envelope, trusted_issuers=TRUSTED_TEST_ISSUERS
    )
    assert requirement.accept("dispatch").verdict == "GRANTED"


def test_50_authentic_evidence_promotes_conditional():
    decision = issue("tests.execute", "conditional", missing=["lab.safety-cert"])
    envelope = payload(
        atlas_decisions=[decision],
        legs=[{"name": "cpu", "needs": ["tests.execute"],
               "evidence": {"lab.safety-cert": attest("lab.safety-cert", issued_at=1700000000)}}],
    )
    requirement = ExecutionRequirement.from_dict(
        envelope, trusted_issuers=TRUSTED_TEST_ISSUERS, now_secs=1700000100
    )
    assert requirement.accept("cpu").verdict == "GRANTED"


def test_51_stale_evidence_never_promotes():
    decision = issue("tests.execute", "conditional", missing=["lab.safety-cert"])
    envelope = payload(
        atlas_decisions=[decision],
        legs=[{"name": "cpu", "needs": ["tests.execute"],
               "evidence": {"lab.safety-cert": attest("lab.safety-cert", issued_at=1700000000)}}],
    )
    requirement = ExecutionRequirement.from_dict(
        envelope, trusted_issuers=TRUSTED_TEST_ISSUERS, now_secs=1800000000
    )
    assert requirement.accept("cpu").verdict == "UNKNOWN"


def test_52_wrong_issuer_key_is_unloadable():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    rogue = Ed25519PrivateKey.generate()
    rogue_issuers = {"rogue-key": rogue.public_key().public_bytes_raw()}
    decision = issue("worker.dispatch", "granted", execution_target="worker-01")
    envelope = payload(
        atlas_decisions=[decision],
        legs=[{"name": "dispatch", "needs": ["worker.dispatch"], "target": "worker-01"}],
    )
    with pytest.raises(BindingError):
        ExecutionRequirement.from_dict(envelope, trusted_issuers=rogue_issuers)


def test_53_tampered_signed_content_is_unloadable():
    decision = issue("worker.dispatch", "granted", execution_target="worker-01")
    decision["execution_target"] = "worker-evil"
    envelope = payload(
        atlas_decisions=[decision],
        legs=[{"name": "dispatch", "needs": ["worker.dispatch"], "target": "worker-evil"}],
    )
    with pytest.raises(BindingError):
        ExecutionRequirement.from_dict(envelope, trusted_issuers=TRUSTED_TEST_ISSUERS)
