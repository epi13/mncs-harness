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

## 0.5 — local network harness

- discover trusted Ollama nodes;
- capability and architecture advertisements;
- route by model, RAM, CPU, accelerator, and current load;
- content-addressed task bundles;
- signed verifier responses;
- heterogeneous slow/fast cluster testing;
- resilient job recovery without granting remote nodes host authority.

## Relationship to MNCS and Forge

This harness can become a practical local proving ground for the broader idea that
models propose work while a network of small, composable verifiers establishes what
is actually true. Its metrics and tool boundary should remain separable enough to be
reused by Forge without coupling the repository to MNCS internals prematurely.
