"""Test-only Atlas issuance fixtures (mint decisions the way Atlas does)."""

import hashlib
import json

from epi13_local_harness.atlas_binding import ExecutionRequirement, build_requirement

DECISION = "mncs.atlas-capability-decision/1"
PARTICIPANT = "e2e-agent"
SCOPE = "repo(mncs-language)"
SOURCE = "sha256:" + "a" * 64
ARTIFACT = "sha256:" + "b" * 64


def issue(capability, status, participant=PARTICIPANT, scope=SCOPE, **extra):
    payload = {
        "schema_version": DECISION,
        "capability": capability,
        "status": status,
        "verdict": {"granted": "PASS", "conditional": "UNKNOWN", "denied": "FAIL"}[status],
        "authority": "fabric",
        "decision_by": ["fabric"],
        "reason": "test issuance",
        "missing": extra.pop("missing", []),
        "evidence_required": [],
        "conformant_path": [],
        "scope": scope,
        "session": {"participant": participant, "scope": scope},
        "execution_target": extra.pop("execution_target", ""),
        "decision_digest_alg": "sha256:canonical-json-v1",
    }
    payload.update(extra)
    body = {key: value for key, value in payload.items() if key != "decision_digest"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload["decision_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def payload(**overrides):
    session_participant = overrides.pop("session_participant", PARTICIPANT)
    session_scope = overrides.pop("session_scope", SCOPE)
    envelope = build_requirement(
        task_id="e2e-001",
        source_identity=SOURCE,
        artifact_identity=ARTIFACT,
        atlas_decisions=overrides.pop(
            "atlas_decisions",
            [
                issue("tests.execute", "granted"),
                issue("worker.dispatch", "granted"),
                issue("repo.edit", "granted"),
                issue("evidence.attest", "granted"),
                issue("network.fetch", "denied", missing=["network.declared"]),
            ],
        ),
        legs=overrides.pop(
            "legs",
            [
                {
                    "name": "cpu",
                    "needs": [
                        "tests.execute",
                        "worker.dispatch",
                        "repo.edit",
                        "evidence.attest",
                    ],
                },
                {"name": "fetch", "needs": ["network.fetch"]},
            ],
        ),
        session_participant=session_participant,
        session_scope=session_scope,
    )
    envelope.update(overrides)
    return envelope


def load(**overrides):
    return ExecutionRequirement.from_dict(payload(**overrides))
