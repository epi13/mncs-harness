# Experiment readiness

Experiments may begin only when every **required** layer for the selected
profile is `READY`. Optional developer capabilities become `optional_warnings`
and do not change `profile_status`.

```text
elh experiment-readiness --json
elh experiment-readiness --profile base-inference
```

The command inspects. It does not refresh the fleet, restart workers, publish
records, or repair launchers.

Schema: `mncs.experiment-readiness.v1`.

## Authority

Harness owns orchestration-stack readiness (workers, models, routing, Commons
consumer, Fabric compatibility, experiment-stack provenance). Control projects
that contract and adds Control/sandbox-specific evidence.

## Worker invariant

A worker is experiment-eligible only when Fabric management authority says so:

```text
availability == AVAILABLE
AND management_state in {READY, BUSY}
AND health certification == CERTIFIED
AND certification is bound to the current inventory identity
AND desired-state conformance has no blocking failures
AND conformance is bound to the current desired_state_identity
AND no unresolved update/restart/rollback transaction exists
AND worker service/build is compatible
AND capability inventory is CURRENT for a strict epoch
```

`AVAILABLE` is not `READY`. `STALE` is not `UNAVAILABLE`, and `STALE` is not
fresh experiment certification.

## Model invariant

A model is experiment-eligible only when it exists on an experiment-eligible
worker, has a known identity, satisfies the required role when one is declared,
is not excluded by observed failure, and is permitted by routing.

An available worker with zero models is not a READY model layer.

Prefer `provider + tag + digest + worker` over a tag alone.

## Profiles

| Profile | Required layers |
| --- | --- |
| `base-inference` | Harness, Fabric controller, fleet, workers, models, Commons consumer, artifact write |
| `code-analysis` | base-inference + Joern (sandbox-callable) + Forge (callable) |
| `multi-agent` | base-inference + routing |
| `MNEL` | multi-agent + Commons operator publication + reference-studies |
| `RAVEL` | base-inference + reference-studies + Forge |

Joern unavailable is an optional warning for `base-inference`. It blocks
`code-analysis`. The historical RAVEL 0.5 canonical-artifact limitation blocks
the RAVEL profile only.

## Fabric compatibility

```text
MIN_SUPPORTED_FABRIC          0.2.0a17 / 6285f7d3f49994e926aa0468a6cc2b644f9a3e85
EXPERIMENT_CERTIFIED_FABRIC   0.2.0a30 / 02fea5b5571e3b43a532d904f56468f99c75e482
                                      sha256:188d6b6a64d215871147c157b60a5d066776505b1c7d5d6d52434de45db9c940
FABRIC_MAIN_CANARY            main
```

Exact certification requires the certified source commit or an artifact digest.
A matching version string without an immutable identity is
`COMPATIBLE_VERSION_ONLY`. `main` is a forward-compatibility canary only.

## Provenance

`mncs.experiment-stack.v1` records runtime build identities. Sibling checkout
HEADs are supplemental diagnostics. Missing required runtime identity is
`PROVENANCE_INCOMPLETE` and blocks a strict epoch freeze.

## Claim boundary

A readiness smoke run is `infrastructure validation`. It is not a research
result and must not be cited as MNCS scientific evidence.

## Local fallback

Experiment-mode runs must not silently execute on the controller host. If
`fallback_to_local` is used, the readiness report records that it is explicit.
