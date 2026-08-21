# Experiment Roles and Record Provenance

Status: architecture proposal / non-normative

## Purpose

MNCS concept reconstruction experiments need multiple forms of model behavior before RAVEL and MNEL are mature enough to occupy those roles directly. Harness can provide those behaviors now without conflating a prompted role with a system identity.

## Temporary role vocabulary

Harness should support explicit experiment-facing role labels such as:

- `experimenter` / `builder` — create a candidate implementation;
- `experiment-investigator` — examine hypothesis quality, falsifiers, competing explanations and evidence gaps;
- `adaptive-experiment-critic` — recommend the next high-information intervention from retained outcomes;
- `reviewer` / `skeptic` — independently challenge or reproduce a result.

These are routing/policy roles only. They MUST NOT imply the model is RAVEL, MNEL, Forge, MNCS or any other authority-bearing family component.

## Provenance requirement

Every experiment-role observation should preserve enough identity to answer who actually produced it:

```text
producer.system = mncs-harness
worker_id = exact Fabric worker
model_id = exact resolved model
provider_id = exact provider/runtime
role = declared experiment role
harness_version = exact version
control_experiment_id = if present
tool_schema_identity = if available
prompt/source identity = if retained under policy
```

A role change must not rewrite producer identity.

## Role separation

Important studies should prefer role and model diversity where resources permit. A builder should not automatically certify its own work. A useful bootstrap topology is:

```text
builder model(s)
      -> investigator model
      -> adaptive critic model
      -> Forge/verifier evaluation
```

Forge remains the bounded evaluator. Harness routes models and tools; it does not promote their prose into truth.

## Family Record Spine

Harness participates in the proposed Family Record Spine by emitting or exposing actor/routing/tool provenance that can be referenced by a producer-neutral Concept Experiment envelope in Control/Commons.

Harness records should remain distinct from:

- Fabric execution receipts;
- MNCS Language compiler/semantic records;
- Forge evaluation records;
- future MNEL causal-attribution records;
- future RAVEL strategy/learning records;
- MNCDS development records;
- MNCS assurance/conformance records.

## Commons boundary

The preferred initial deployment remains controller-mediated Commons access. Remote models receive bounded Commons tools/results through Harness when authorized; they do not receive the Commons store path, operator socket or direct persistence authority.

When an experiment-role model contributes an observation, Commons publication is delivery of an inert record or reference. It does not make the observation accepted or verified.

## Baseline-control value

The temporary investigator and adaptive-critic roles should be retained as explicit baselines. When MNEL and RAVEL are later operational, the project can compare whether those systems improve attribution quality, transfer, next-intervention choice, efficiency or retained-failure use relative to strong general models given equivalent evidence.

## First implementation target

The experiment-role surface should be exercised with a small Concept Reconstruction Experiment such as the MNCS tri-state result lattice. Exact role/model/worker pins should be part of the experiment identity so later reruns can distinguish model changes from language/compiler/tooling changes.
