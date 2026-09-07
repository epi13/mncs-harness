"""Test-only Atlas issuance fixtures (signed the way Atlas signs).

The keypair below is a FIXED TEST-ONLY issuer: it exists so the hostile
matrix can distinguish authentic test issuance (signed by a trusted
test key) from forgery (valid digest, no trusted signature). It must
never appear in any production trust roots file.
"""

import hashlib
import json
import time

from mncs_harness.atlas_binding import ExecutionRequirement, build_requirement

DECISION = "mncs.atlas-capability-decision/1"
PARTICIPANT = "e2e-agent"
SCOPE = "repo(mncs-language)"
SOURCE = "sha256:" + "a" * 64
ARTIFACT = "sha256:" + "b" * 64

TEST_ISSUER_KEY_ID = "test-key-1"
TEST_ISSUER_PRIVATE_HEX = "19270d571dc31474c6ddbc1d94095fa5b57da85482b502902563fbc693354d7b"
TEST_ISSUER_PUBLIC_HEX = "28d5aacb5f1d4041c42ef016bac24f803d8f607dd569010b94d7a09a4289522f"
TRUSTED_TEST_ISSUERS = {TEST_ISSUER_KEY_ID: bytes.fromhex(TEST_ISSUER_PUBLIC_HEX)}


def _sign_issuer(payload):
    """Attach a test-issuer signature exactly the way Atlas issuance does:
    signature over canonical bytes minus digest/signature, digest last so
    it covers the signature too."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(TEST_ISSUER_PRIVATE_HEX))
    issued = {k: v for k, v in payload.items() if k not in ("decision_digest", "issuer_signature")}
    issued["issuer"] = {"key_id": TEST_ISSUER_KEY_ID, "algorithm": "ed25519"}
    signed = json.dumps(issued, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    issued["issuer_signature"] = private.sign(signed.encode("utf-8")).hex()
    if "decision_digest_alg" in issued:
        body = {k: v for k, v in issued.items() if k != "decision_digest"}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        issued["decision_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return issued


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
    return _sign_issuer(payload)


def attest(name, participant=PARTICIPANT, scope=SCOPE, issued_at=None, **extra):
    """Mint a test-issuer evidence attestation for conditional promotion."""
    attestation = {
        "schema_version": "mncs.evidence-attestation/1",
        "name": name,
        "participant": participant,
        "scope": scope,
        "subject": extra.pop("subject", ""),
        "source_subsystem": extra.pop("source_subsystem", "test-subsystem"),
        "issued_at": issued_at if issued_at is not None else int(time.time()),
        "issuer": {"key_id": TEST_ISSUER_KEY_ID, "algorithm": "ed25519"},
    }
    attestation.update(extra)
    return _sign_issuer(attestation)


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
    now_secs = overrides.pop("now_secs", None)
    return ExecutionRequirement.from_dict(
        payload(**overrides),
        trusted_issuers=TRUSTED_TEST_ISSUERS,
        now_secs=now_secs,
    )
