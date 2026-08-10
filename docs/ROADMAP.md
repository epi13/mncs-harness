# Roadmap

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

- inventory worker-local models and provider/runtime capabilities from authenticated
  Fabric worker observations;
- represent inference location, workspace location, and tool-execution location as
  separate task properties rather than assuming they are the same host;
- allow a model placed on a remote worker to request controller-owned tools against
  the controller workspace through the existing policy/approval boundary;
- introduce explicit execution-target routing for policy-approved tools that must run
  on another enrolled worker, without granting the model arbitrary SSH or host shell
  authority;
- model a capability graph spanning models, tools, MCP endpoints, hardware,
  workspaces, and current resource state;
- keep model suitability and semantic routing in the harness while Fabric advertises
  provider-neutral facts, placement evidence, identity, transport, and liveness;
- treat shell/Bash/PowerShell as guarded tool families: argv-only command execution by
  default, with script execution subject to policy inspection and explicit approval;
- support controller-hosted MNCS MCPs for remote models through tool-schema proxying,
  reserving worker-local MCPs for resources inherently attached to a worker; and
- add evaluation cases proving remote inference can safely operate on a different
  workspace/execution target and that policy remains authoritative.

## 0.3 — repository intelligence

- project-type detection;
- language-specific verifier plugins;
- test discovery with cost estimates;
- context packing and symbol-level retrieval;
- Git worktree sessions for isolated changes;
- benchmark corpus drawn from real local tasks.

## 0.4 — adaptive routing

- use observed latency, token rate, verifier pass rate, and escalation frequency;
- per-task-family routing thresholds;
- bandit-style exploration with strict safety overrides;
- independent reviewer sampling for uncertain changes;
- prevent model self-confidence from becoming the acceptance criterion.

## 0.5 — distributed agent scheduling

Build on the 0.25 capability/session boundary rather than coupling orchestration to
physical worker placement.

- discover and refresh trusted Fabric workers and their model/runtime inventories;
- route by model capability, RAM, CPU, accelerator, current load, tool needs, and
  execution target;
- content-addressed task bundles and explicit remote workspace/session references;
- signed or Fabric-bound verifier evidence;
- heterogeneous slow/fast cluster testing;
- resilient job recovery without granting remote nodes host authority;
- decompose clearly independent work into bounded sub-tasks that may execute on
  different workers;
- represent task dependencies as an explicit DAG before attempting general-purpose
  autonomous splitting;
- combine sub-results through a reducer/reviewer stage and verify the observable
  result independently; and
- keep semantic decomposition, model choice, reduction, verification, and escalation
  in the harness while Fabric executes and records bounded work.

## Relationship to MNCS and Forge

This harness can become a practical local proving ground for the broader idea that
models propose work while a network of small, composable verifiers establishes what
is actually true. Its metrics and tool boundary should remain separable enough to be
reused by Forge without coupling the repository to MNCS internals prematurely.
