# Roadmap

## Completed in 0.6.0

- Fabric-owned persistent worker registry consumption with explicit table
  compatibility and fail-closed duplicate precedence.
- Per-worker installed/loaded model observations and one bounded Harness-owned
  resident generation-model assignment per capable worker.
- Controller generation policy, warm-aware automatic routing, and typed role,
  model, worker, and exact worker/model operator overrides.
- Unified fleet view for runtime routing, CLI, Doctor, Models, and TUI.
- Direct Commons CLI and TUI browsing over the existing controller-local MCP seam.
- Policy-gated exact-worker Python tool execution through Fabric target admission,
  immutable bundles, deterministic retry, and bound evidence.

## Still future

- Distributed DAG scheduling, model migration, worker-local Commons, remote MCP,
  federation, and automatic multi-agent reduction remain out of scope.
- Adaptive multi-model working sets, provider-session affinity, eviction-aware route
  cost, speculative warming, and alternate provider experiments are staged future
  work; see [Adaptive residency, locality, and session affinity](ADAPTIVE_RESIDENCY.md).

## 0.1 — functional local alpha

- deterministic model routing;
- Gemma E2B/E4B/12B cascade;
- optional Qwen3 coding specialist;
- Ollama tool loop;
- workspace and command policy;
- interactive approvals;
- deterministic file verification;
- SQLite metrics;
- routing evaluations and CI.

## 0.2 — stronger execution boundary

- Landlock or bubblewrap runner on Fedora;
- per-tool resource budgets;
- network namespace disabled by default;
- structured audit export;
- file patch tool instead of whole-file replacement;
- explicit rollback checkpoints.

## 0.25 — distributed capability and session foundation

Pull forward the minimum network-facing abstractions needed to make the existing
agent loop useful across MNCS Fabric without turning Fabric into an agent runtime.

- [x] inventory worker-local models and provider/runtime capabilities from authenticated
  Fabric worker observations;
- [x] represent inference location, workspace location, and tool-execution location as
  separate task properties rather than assuming they are the same host;
- [x] allow a model placed on a remote worker to request controller-owned tools against
  the controller workspace through the existing policy/approval boundary;
- [x] introduce explicit execution-target routing for policy-approved tools that must run
  on another enrolled worker, without granting the model arbitrary SSH or host shell
  authority;
- [x] model a capability graph spanning observed models/runtimes/worker capabilities,
  configured controller tools/workspace, controller-local Commons MCP, and
  hardware/resource state;
- [x] keep model suitability and semantic routing in the harness while Fabric advertises
  provider-neutral facts, placement evidence, identity, transport, and liveness;
- [~] retain the existing guarded argv-only command execution; dedicated distributed
  Bash/PowerShell script tool families remain future work;
- [x] support controller-local MNCS Commons for remote models through validated
  tool-schema proxying while reserving worker-local MCPs for future resources
  inherently attached to a worker; and
- [x] add in-process evaluations proving inference and tools can use different workers,
  controller workspace authority remains separate, policy denial prevents dispatch,
  and identical retry does not execute twice.

The 0.5.0 integration additionally proves a multi-turn Commons WorkRequest-to-
Observation contribution, opaque Fabric consumer provenance, optional inert Fabric
evidence publication, and bounded long-running remote inference. General remote MCP
invocation, worker-local Commons, federation, and automatic scheduling remain future
work.

An operator-controlled physical run also placed `gemma4:e4b` on
`collamore02-windows`, mediated `commons_describe` through the Fedora controller, and
completed the second Fabric turn. Its sanitized evidence is in
`development-evidence/commons-fabric-collamore02-2026-08-10.json`; this is physical
integration evidence, not independent assurance.

## 0.3 — repository intelligence

- project-type detection;
- language-specific verifier plugins;
- test discovery with cost estimates;
- context packing and symbol-level retrieval;
- Git worktree sessions for isolated changes;
- benchmark corpus drawn from real local tasks.

## 0.35 — adaptive residency and session affinity

Treat model loading as a bounded working-set problem while preserving semantic routing
above placement. The current one-preferred-model policy remains the safe baseline until
telemetry proves more complicated behavior is valuable.

- record model cold-start duration, displacement, restoration, reuse, eviction, and
  memory-pressure observations without changing routing behavior;
- add explicit route-cost metrics for load, network, eviction, restore, tool-locality,
  and session-state-loss costs while preserving UNKNOWN rather than treating missing
  values as zero;
- introduce an opaque Harness-owned session placement so multi-turn agent inference can
  remain sticky to a useful worker/model/provider session while workspace and tools stay
  independently authorized;
- record why session affinity is retained or broken and prove affinity grants no new
  filesystem, shell, MCP, or workspace authority;
- evolve from one preferred resident model to a bounded ranked working set only after
  deterministic replay fixtures and measured cache-thrash behavior exist;
- keep explicit operator pins fail-closed and make warm state an optimization signal,
  never semantic proof;
- add confidence- and resource-gated speculative warming only behind an experimental
  flag, with useful-versus-wasted warm metrics and automatic suppression after negative
  value or eviction thrash;
- evaluate alternate provider runtimes, including a narrowly declared Picchio/GPT-OSS
  adapter, behind the existing provider boundary rather than embedding model-runtime
  logic in Harness or Fabric; and
- keep transformer expert/layer movement, KV implementation, quantization, and GPU/CPU
  split inside provider runtimes.

See [Adaptive residency, locality, and session affinity](ADAPTIVE_RESIDENCY.md) for the
reference analogy, non-goals, staged implementation, and evidence rules.

## 0.4 — adaptive routing

- use observed latency, token rate, verifier pass rate, and escalation frequency;
- incorporate measured cold-start, resident reuse, eviction/restoration, session-affinity,
  and memory-pressure costs after the 0.35 telemetry baseline is trustworthy;
- per-task-family routing thresholds and per-task-family reuse statistics;
- bandit-style exploration with strict safety overrides;
- replay adaptive choices against deterministic routing and no-prefetch baselines;
- independent reviewer sampling for uncertain changes;
- prevent model self-confidence, warm state, or provider telemetry from becoming the
  acceptance criterion.

## 0.5 — distributed agent scheduling

Build on the 0.25 capability/session boundary rather than coupling orchestration to
physical worker placement.

- discover and refresh trusted Fabric workers and their model/runtime inventories;
- route by model capability, RAM, CPU, accelerator, current load, tool needs, execution
  target, bounded residency cost, and usable session affinity;
- content-addressed task bundles and explicit remote workspace/session references;
- signed or Fabric-bound verifier evidence;
- heterogeneous slow/fast cluster testing;
- resilient job recovery without granting remote nodes host authority;
- decompose clearly independent work into bounded sub-tasks that may execute on
  different workers;
- represent task dependencies as an explicit DAG before attempting general-purpose
  autonomous splitting;
- coalesce or parallelize independent work only when the expected placement/load cost is
  better than sequential reuse of an already useful resident model/session;
- combine sub-results through a reducer/reviewer stage and verify the observable
  result independently; and
- keep semantic decomposition, model choice, residency policy, reduction, verification,
  and escalation in the harness while Fabric executes and records bounded work.

## Relationship to MNCS and Forge

This harness can become a practical local proving ground for the broader idea that
models propose work while a network of small, composable verifiers establishes what
is actually true. Its metrics and tool boundary should remain separable enough to be
reused by Forge without coupling the repository to MNCS internals prematurely.
- [x] Consume Fabric-owned persistent controller/fleet state through the
  ordinary local service socket with explicit embedded/transitional compatibility.
- [x] Derive inference dispatch, worker-local inventory, residency warming, and
  capability ingestion from the persistent Fabric service's live feature projection;
  connected services that omit a feature remain explicitly unsupported.
