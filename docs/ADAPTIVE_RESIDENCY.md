# Adaptive residency, locality, and session affinity

## Purpose

MNCS Harness already separates semantic model choice from physical placement and can
observe installed/loaded worker models, keep one preferred resident generation model
per worker, route to an exact worker/model pair, and reconcile a worker back to its
declared steady state after a transient model displaces the preferred resident.

The next useful step is to treat model residency as a measured working-set problem
rather than a single preferred-model flag.

A useful external reference is Picchio (`benmaster82/picchio`), a narrow GPT-OSS MoE
runtime that keeps dense state resident while streaming only routed experts, uses an
LRU/hot-store strategy, reuses persistent inference state, and documents both successful
and failed prefetch experiments. Picchio is reference material and prior art for systems
behavior; it is not a dependency and this project does not import or reimplement its
inference runtime.

The reusable idea is broader than MoE inference:

> Total available capability can exceed simultaneously resident capability when
> activation is sparse and movement, reuse, and eviction are explicit and measurable.

For MNCS Harness, the sparse unit is normally a model, provider session, tool,
capability, or bounded subtask rather than a transformer expert.

## Architectural analogy

| Picchio-scale concept | Harness/Fabric-scale analogue |
| --- | --- |
| token routing | task/subtask routing |
| expert | model/agent/capability |
| expert cache | resident model working set |
| hot expert store | learned high-value resident models |
| cache miss | model cold start |
| eviction | model unload/displacement |
| expert prefetch | speculative model warming |
| persistent KV state | session-affine provider state |
| parallel expert reads | parallel independent task placement |
| storage/RAM hierarchy | installed/warm/active capability hierarchy |

The analogy is intentionally coarse. MNCS Harness must not move transformer layers or
experts itself. Provider runtimes remain responsible for quantization, KV caches,
CPU/GPU split, expert movement, and accelerator-specific execution.

## Keep the current authority boundary

Adaptive residency must not weaken the existing division of responsibility:

- **Harness owns semantics:** task profiling, role/model suitability, tool policy,
  verification, escalation, session affinity, working-set policy, and any learned
  routing score.
- **Fabric owns bounded placement facts and transport:** worker identity, liveness,
  resource/capability observations, execution placement, and evidence.
- **Provider runtimes own inference internals:** model loading, cache implementation,
  KV state, quantization, GPU/CPU placement, and model-specific streaming/offload.

A warm model is an optimization signal, never a proof that the model is semantically
appropriate. An exact operator pin continues to fail closed unless fallback is
explicitly allowed.

## From one resident model to a bounded working set

The current one-preferred-model-per-worker policy is a good safe baseline. A future
working-set layer may represent multiple installed models with factual state such as:

```text
model
  installed
  loaded
  provider
  estimated_memory_bytes
  observed_load_ms
  last_used_at
  recent_use_count
  recent_task_family_count
  observed_tokens_per_second
  verifier_pass_rate
  escalation_rate
  eviction_count
  warm_state
```

Harness may derive a residency score from those observations. A starting policy should
remain deterministic and inspectable, for example:

```text
expected_utility
  = semantic_eligibility
  * expected_reuse
  * avoided_cold_start_cost
  * measured_success_value
  / memory_pressure_cost
```

No learned policy should be introduced until the deterministic metrics exist and replay
against recorded traces is possible.

## Session affinity

Routing each inference turn independently can destroy useful provider state. Once a
model has been selected for a multi-turn agent task, Harness should be able to keep an
opaque provider session attached to the same worker/model while controller-owned tools
execute elsewhere.

A future session placement may record:

```text
SessionPlacement
  session_id
  inference_worker
  model
  provider
  provider_session_ref
  workspace_target
  tool_target
  created_at
  last_used_at
  sticky_until
  stickiness_reason
```

The provider session reference is opaque. It grants no filesystem, shell, MCP, or
workspace authority. Tool requests still return through Harness policy and approval.

Stickiness should end on explicit session close, worker/model loss, provider failure,
resource pressure, a semantic escalation that requires another model, or a bounded idle
limit. Harness should record why affinity was retained or broken.

## Residency-aware route cost

Semantic suitability remains the first gate. Among semantically acceptable placements,
Harness may compare expected total cost rather than generation latency alone:

```text
route_cost
  = inference_cost
  + model_load_cost
  + network_cost
  + expected_eviction_cost
  + expected_restore_cost
  + tool_locality_cost
  + session_state_loss_cost
```

This makes a warm but slightly slower model preferable when reloading a faster model
would thrash a constrained accelerator, while still allowing a clearly better model to
win when correctness or task difficulty requires it.

All cost terms must distinguish measured observations from estimates. Unknown values
must remain unknown rather than being silently treated as zero.

## Speculative warming and prefetch discipline

Picchio's documented prefetch work is especially useful as a warning: speculative work
can reduce performance when it competes with the actual working set or causes duplicate
movement.

Harness should therefore treat speculative warming as a late optimization with these
rules:

1. disabled by default until cold-start and eviction metrics are trustworthy;
2. confidence-gated after semantic routing, never before safety/policy filtering;
3. resource-aware, with explicit RAM/VRAM reserve and expected eviction cost;
4. bounded to one or a very small number of candidates;
5. cancelable when the route resolves differently;
6. measured against a no-prefetch baseline;
7. automatically suppressed when recent speculation caused thrash or negative value.

A useful metric is **wasted warm bytes/time**: model load work performed speculatively
that was not used before eviction or expiry.

## Eviction and reconciliation telemetry

The existing post-attempt resident reconciliation should grow into explicit cache-like
telemetry rather than becoming hidden provider behavior. Record at least:

- model loaded/unloaded observations before and after an attempt;
- cold-start duration when observable;
- transient model displacement;
- preferred/working-set model restoration duration;
- evictions caused by another Harness route versus external/provider activity;
- memory pressure at admission and after completion when available;
- whether the next task reused the loaded model;
- UNKNOWN when the provider cannot establish the transition.

This data can later support adaptive routing without making model self-confidence an
acceptance signal.

## Capability hierarchy

The fleet may be treated as an operational hierarchy without pretending it is literal
memory:

```text
active provider session
  -> loaded model on preferred worker
  -> loaded compatible model on another worker
  -> installed model on preferred worker
  -> installed model elsewhere
  -> locally available artifact/model package
  -> provision/download path
```

Each transition has a different latency, resource, trust, and availability cost.
Harness may choose among those states only after semantic suitability and operator
policy are satisfied.

## Picchio provider experiment

Picchio exposes an OpenAI-shaped chat endpoint and is therefore a useful future provider
experiment independent of whether any of its internal code is reused.

A bounded experiment should add a provider adapter behind the existing inference
boundary and answer these questions:

- Can GPT-OSS 20B provide useful high-tier reasoning on hardware where a conventional
  fully resident runtime is impractical?
- Is very slow GPT-OSS 120B useful for unattended evaluation/review tasks where latency
  matters less than capability?
- Can the provider report enough factual load/cache/session telemetry for Harness to
  make placement decisions without learning Picchio-specific semantics?
- Does persistent session reuse materially improve multi-turn agent work?
- How should tool-call compatibility gaps be surfaced without pretending full OpenAI
  API compatibility?

The adapter must advertise exact supported features. Missing tool-call structure,
concurrency limits, model-specific constraints, or absent endpoints must fail closed or
be represented as unsupported capability—not guessed.

## Explicit non-goals

This design does **not** propose:

- making Fabric an inference engine;
- sharding transformer experts/layers across Fabric workers;
- per-token network scheduling;
- copying Picchio source into MNCS Harness;
- assuming a warm model is the correct model;
- unconstrained speculative downloads or model warming;
- granting a remote inference worker authority over the controller workspace; or
- treating provider telemetry as correctness evidence.

If distributed model execution is explored later, it should live behind a dedicated
provider runtime that Fabric places as a bounded workload.

## Staged implementation

### Stage A — telemetry first

- retain current one-preferred-model policy;
- record cold-start, displacement, restoration, reuse, and eviction observations;
- add route-cost fields to metrics without changing routing behavior;
- add deterministic replay fixtures for residency decisions.

### Stage B — session affinity

- introduce an opaque Harness-owned `SessionPlacement`;
- keep multi-turn inference on the same eligible worker/model where useful;
- record affinity retention/break reasons;
- prove controller-owned tool execution remains independent of inference placement.

### Stage C — bounded working sets

- allow more than one ranked resident candidate per worker where hardware permits;
- choose deterministic residency based on measured reuse/load/memory cost;
- preserve explicit operator overrides and fail-closed exact pins.

### Stage D — adaptive policy

- add task-family reuse statistics and measured success/latency signals;
- replay learned/adaptive decisions against deterministic baselines;
- keep strict safety and semantic suitability gates above performance optimization.

### Stage E — speculative warming experiments

- add confidence- and resource-gated warming behind an experimental flag;
- measure useful versus wasted warming and eviction thrash;
- automatically fall back to no-prefetch behavior when value is negative.

### Stage F — alternate provider study

- implement a narrowly declared Picchio/GPT-OSS provider adapter;
- benchmark 20B/120B for review/evaluation workloads;
- compare session reuse, cold-start economics, correctness/verifier outcomes, and
  operational cost against existing Ollama providers.

## Evidence rule

Residency and placement optimization must remain measurable. Every adaptive decision
should be reconstructable from recorded task features, current capability observations,
resource state, model state, policy version, and cost inputs. Performance evidence may
justify a faster route; only existing verifiers and project-specific evaluation may
establish whether the resulting work is acceptable.
