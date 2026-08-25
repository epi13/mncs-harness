from mncs_harness import ACTOR_PROVENANCE_SCHEMA, build_actor_provenance


def test_bootstrap_role_preserves_exact_harness_producer() -> None:
    value = build_actor_provenance(
        role="adaptive-experiment-critic",
        model_identity="model:qwen:fixture",
        provider_identity="provider:ollama",
        worker_identity="worker:fixture",
        route_identity="route:critic:fixture",
        tool_exposure=["read_file", "read_file", "search_text"],
        policy_profile="policy:read-only",
        prompt_digest="sha256:" + "a" * 64,
        session_identity="session:fixture",
        observed_at="2026-08-21T20:00:00Z",
    )
    assert value["producer"] == "mncs-harness"
    assert value["schema_version"] == ACTOR_PROVENANCE_SCHEMA
    assert value["role"] == "adaptive-experiment-critic"
    assert value["bootstrap_role"] is True
    assert value["tool_exposure"] == ["read_file", "search_text"]
    assert value["stable_id"].startswith("mncs-harness://actor-route/")
    assert value["identity_boundary"].startswith("Harness role only")


def test_actor_record_maps_to_rights_participant() -> None:
    from epi13_local_harness.actor_provenance import (
        build_actor_provenance,
        to_rights_participant,
    )

    record = build_actor_provenance(
        role="builder",
        model_identity="qwen3:8b",
        provider_identity="ollama-local",
        worker_identity="worker-01",
        route_identity="route-9",
        tool_exposure=["read", "write"],
        policy_profile="default",
        prompt_digest="sha256:" + "1" * 64,
        session_identity="session-3",
        observed_at="2026-08-24T10:00:00Z",
    )
    participant = to_rights_participant(record)
    assert participant["type"] == "model"
    assert participant["model"] == "qwen3:8b"
    assert participant["participant_ref"] == record["stable_id"]
    assert len(participant["digest"]) == 64


def test_mapping_rejects_incomplete_records() -> None:
    import pytest

    from epi13_local_harness.actor_provenance import to_rights_participant

    with pytest.raises(ValueError):
        to_rights_participant({"role": "builder"})
