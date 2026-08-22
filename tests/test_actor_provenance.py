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
