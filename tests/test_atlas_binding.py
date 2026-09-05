"""Atlas-bound execution requirements: carry Atlas decisions, enforce them."""

import pytest

from epi13_local_harness.atlas_binding import (
    AtlasDecision,
    BindingError,
    ExecutionRequirement,
)

DECISION = "mncs.atlas-capability-decision/1"


def decision(capability, status, **extra):
    payload = {
        "schema_version": DECISION,
        "capability": capability,
        "status": status,
        "missing": extra.pop("missing", []),
        "session": {"participant": "e2e-agent", "scope": "repo(mncs-language)"},
    }
    payload.update(extra)
    return payload


def requirement(**overrides):
    payload = {
        "schema_version": "mncs.execution-requirement/0.1",
        "task_id": "e2e-001",
        "source_identity": "sha256:" + "a" * 64,
        "artifact_identity": "sha256:" + "b" * 64,
        "atlas_decisions": [
            decision("tests.execute", "granted"),
            decision("worker.dispatch", "granted"),
            decision("network.fetch", "denied", missing=["network.declared"]),
        ],
        "legs": [
            {"name": "cpu", "needs": ["tests.execute", "worker.dispatch"]},
            {"name": "fetch", "needs": ["network.fetch"]},
        ],
    }
    payload.update(overrides)
    return ExecutionRequirement.from_dict(payload)


def test_granted_leg_is_accepted():
    acceptance = requirement().accept("cpu")
    assert acceptance.verdict == "GRANTED", acceptance.reason
    assert "atlas granted" in acceptance.reason


def test_denied_capability_refuses_leg():
    acceptance = requirement().accept("fetch")
    assert acceptance.verdict == "REFUSED", acceptance.reason
    assert "atlas denied" in acceptance.reason


def test_unknown_leg_refuses():
    assert requirement().accept("nope").verdict == "REFUSED"


def test_missing_binding_is_unloadable():
    with pytest.raises(BindingError):
        requirement(atlas_decisions=[])


def test_conditional_without_evidence_is_unknown():
    payload = requirement(
        atlas_decisions=[
            decision(
                "tests.execute",
                "conditional",
                missing=["execution.target"],
            )
        ],
        legs=[{"name": "cpu", "needs": ["tests.execute"]}],
    )
    acceptance = payload.accept("cpu")
    assert acceptance.verdict == "UNKNOWN", acceptance.reason


def test_conditional_with_carried_evidence_is_granted():
    payload = requirement(
        atlas_decisions=[
            decision(
                "tests.execute",
                "conditional",
                missing=["execution.target"],
                execution_target="mncs:target:research-bytecode-0.1",
            )
        ],
        legs=[{"name": "cpu", "needs": ["tests.execute"]}],
    )
    acceptance = payload.accept("cpu")
    assert acceptance.verdict == "GRANTED", acceptance.reason


def test_artifact_mismatch_fails_closed():
    acceptance = requirement().accept("cpu", observed_artifact="sha256:" + "c" * 64)
    assert acceptance.verdict == "REFUSED", acceptance.reason
    assert "mismatch" in acceptance.reason


def test_unknown_status_is_rejected_at_load():
    with pytest.raises(BindingError):
        AtlasDecision.from_dict(
            {
                "schema_version": DECISION,
                "capability": "tests.execute",
                "status": "maybe",
            }
        )
