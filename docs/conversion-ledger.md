# MNCS conversion ledger

Source of truth: `docs/conversion-ledger.json` (validated in CI by
`scripts/check_conversion_ledger.py`). Every production module has at
least one entry; statuses are `MNCS-EXECUTED`, `HOST-EFFECT-BOUNDARY`,
`BLOCKED:<pressure-id>`, `MIGRATION-DEBT`, `TEST-BUILD-ONLY`.

## Totals

- `BLOCKED:HARNESS-PRESSURE-001`: 5
- `BLOCKED:HARNESS-PRESSURE-002`: 2
- `BLOCKED:HARNESS-PRESSURE-004`: 1
- `BLOCKED:HARNESS-PRESSURE-005`: 3
- `BLOCKED:HARNESS-PRESSURE-006`: 1
- `BLOCKED:HARNESS-PRESSURE-007`: 1
- `BLOCKED:HARNESS-PRESSURE-009`: 1
- `HOST-EFFECT-BOUNDARY`: 37
- `MNCS-EXECUTED`: 33
- `TEST-BUILD-ONLY`: 4

## Entries

- `__init__.identity` — **HOST-EFFECT-BOUNDARY** — Canonical identity constants.
- `__main__.main` — **HOST-EFFECT-BOUNDARY** — Interpreter entry point.
- `actor_provenance.provenance` — **HOST-EFFECT-BOUNDARY** — Actor provenance construction over string identities.
- `agent._exact_manual_route` — **MNCS-EXECUTED** — Exact-pin predicate executes mncs.harness.pins.v1::is_exact_pin via mncs_exec; progress branch only.
- `agent.per-step-decisions` — **MNCS-EXECUTED** — Per-step route/policy/verdict choices execute kernels via mncs_exec through tools/router.
- `agent.tool-loop/runtime` — **BLOCKED:HARNESS-PRESSURE-007** — Multi-turn loop, spawning, cancellation need concurrency.
- `atlas_binding._fold_capability` — **MNCS-EXECUTED** — Duplicate-decision fold executes mncs.harness.atlas.v1::fold4 via mncs_exec.fold; reasons/outstanding host-side.
- `atlas_binding.confirm_execution` — **MNCS-EXECUTED** — Final target comparison executes mncs.harness.atlas.v1::dispatch_gate via mncs_exec.dispatch_gate; freshness/signature host-side.
- `atlas_binding.freshness/session` — **BLOCKED:HARNESS-PRESSURE-005** — Freshness windows and session echo need a clock.
- `atlas_binding.signature/digest-verify` — **BLOCKED:HARNESS-PRESSURE-006** — Ed25519 issuance verification and SHA-256 canonical digests need crypto.
- `bridge.bridge` — **HOST-EFFECT-BOUNDARY** — JSON-RPC stdio transport to the extension host.
- `capability_graph.build_capability_graph` — **MNCS-EXECUTED** — Inventory-source selection executes mncs.harness.eligibility.v1::capability_source via mncs_exec; string matching/sorting/paths host-side.
- `cli.cli` — **HOST-EFFECT-BOUNDARY** — Subcommand dispatch, approval interaction, output rendering.
- `commons.publish_fabric_evidence-gate` — **MNCS-EXECUTED** — Ready/configured/record precedence executes mncs.harness.policy.v1::publication_gate via mncs_exec.publication_gate; error codes mapped host-side.
- `commons.transport/records` — **HOST-EFFECT-BOUNDARY** — MCP transport, JSON envelopes, SQLite records are I/O effects.
- `commons_operator.publication-policy` — **MNCS-EXECUTED** — Admission precedence executes mncs.harness.policy.v1::publication_gate via mncs_exec; Commons transport/translation host-side.
- `commons_operator.transports` — **HOST-EFFECT-BOUNDARY** — Controller/bridge transports are network effects.
- `config.load_config` — **BLOCKED:HARNESS-PRESSURE-009** — TOML load/merge and env interpolation need structured text.
- `e2e_gauntlet.gauntlet` — **TEST-BUILD-ONLY** — Scenario harness driving product paths in tests.
- `evals.evaluations` — **TEST-BUILD-ONLY** — Routing evaluation scenarios driving the product path.
- `experiment_readiness._overall` — **MNCS-EXECUTED** — Overall verdict executes mncs.harness.readiness.v1::fold4/::fold8 envelopes via mncs_exec.fold_readiness; warnings stay host-side.
- `experiment_readiness.evaluate_layers` — **HOST-EFFECT-BOUNDARY** — Layer probing builds stringly-typed dicts over transports.
- `experiment_stack.provenance_gaps` — **BLOCKED:HARNESS-PRESSURE-001** — Gap detection over string identities needs text.
- `experiment_stack.stack-record` — **HOST-EFFECT-BOUNDARY** — Git digests, canonical JSON, clock strings are process/crypto/time effects.
- `fabric.trust-store` — **HOST-EFFECT-BOUNDARY** — Trust-store file I/O and metadata are FS effects.
- `fabric_commission.commission` — **HOST-EFFECT-BOUNDARY** — Cloud commissioning over APIs; purpose labels are frozen wire strings.
- `fabric_commission_breakaway.breakaway` — **HOST-EFFECT-BOUNDARY** — Breakaway provisioning over transports.
- `fabric_compat.evaluate_experiment_fabric` — **MNCS-EXECUTED** — Classification + dispatch gate execute mncs.harness.fabric.v1::classify_fabric/::dispatch_allowed via mncs_exec; version parsing host-side.
- `fabric_compat.version/capability-encode` — **HOST-EFFECT-BOUNDARY** — PEP-440 parsing and capability-name tables are string operations.
- `fabric_controller_light.controller` — **HOST-EFFECT-BOUNDARY** — Controller transports, worker spawning, polling loops.
- `fabric_inventory.inventory` — **HOST-EFFECT-BOUNDARY** — Worker inventory file polling and JSON parsing.
- `fabric_inventory_session.placement-admission` — **MNCS-EXECUTED** — Pin admission executes mncs.harness.pins.v1::admit_placement via mncs_exec.admit_placement; reason prefixes host-side.
- `fabric_inventory_session.transport/session` — **HOST-EFFECT-BOUNDARY** — Transport, heartbeats, session state are network effects.
- `fabric_models.tables` — **HOST-EFFECT-BOUNDARY** — Model tag tables and inventory mapping.
- `fabric_profile.profiles/state` — **HOST-EFFECT-BOUNDARY** — Profile files, state paths, worker table I/O.
- `fabric_profile_breakaway.profiles` — **HOST-EFFECT-BOUNDARY** — Breakaway/inventory profile variants; same I/O shape.
- `fabric_profile_inventory.profiles` — **HOST-EFFECT-BOUNDARY** — Inventory-backed profile resolution over files.
- `fabric_target_tools.target-tools` — **HOST-EFFECT-BOUNDARY** — Worker-side tool shims over transports.
- `fabric_test_support.fixtures` — **TEST-BUILD-ONLY** — Test fixtures and fakes for distributed tests.
- `fleet.role_availability` — **HOST-EFFECT-BOUNDARY** — Row availability is string-identity set membership over live inventory plus delegation to fabric_session.resolve (external); no bounded numeric/threshold decision exists to move (strings blocked by HARNESS-PRESSURE-001).
- `metrics.metrics/events` — **BLOCKED:HARNESS-PRESSURE-005** — Timestamps, durations, and SQLite persistence need clock + storage.
- `mncs.atlas-fold` — **MNCS-EXECUTED** — mncs/harness_atlas.mncs generic fold_all<4> over fold4 envelope + fold_pair/dispatch_gate/tool_admission executed via mncs_exec.
- `mncs.capability_source` — **MNCS-EXECUTED** — mncs/harness_eligibility.mncs inventory-source selection executed via mncs_exec by capability_graph.
- `mncs.classify_route` — **MNCS-EXECUTED** — mncs/harness_routing.mncs executed via mncs_exec on every route.
- `mncs.eligibility` — **MNCS-EXECUTED** — mncs/harness_eligibility.mncs capability/resource/capability-source/residency-admit gates executed via mncs_exec.
- `mncs.evaluate_command` — **MNCS-EXECUTED** — mncs/harness_policy.mncs executed via mncs_exec; host encodes shell/python/git/path observations.
- `mncs.evaluate_write` — **MNCS-EXECUTED** — mncs/harness_policy.mncs evaluate_write executed via mncs_exec.
- `mncs.fabric-class` — **MNCS-EXECUTED** — mncs/harness_fabric.mncs precedence + dispatch gate executed via mncs_exec.
- `mncs.is_exact_pin` — **MNCS-EXECUTED** — mncs/harness_pins.mncs is_exact_pin executed via mncs_exec by router + agent.
- `mncs.needs_coder` — **MNCS-EXECUTED** — mncs/harness_routing.mncs needs_coder executed via mncs_exec.
- `mncs.pins` — **MNCS-EXECUTED** — mncs/harness_pins.mncs shape table + fail-closed admission + is_exact_pin executed via mncs_exec.
- `mncs.publication_gate` — **MNCS-EXECUTED** — mncs/harness_policy.mncs ready-first publication precedence executed via mncs_exec by agent + commons.
- `mncs.readiness-fold` — **MNCS-EXECUTED** — Generic fold_readiness<N> over concrete fold4/fold8 envelopes executed via mncs_exec.
- `mncs.verdict-fold` — **MNCS-EXECUTED** — mncs/harness_verdict.mncs generic combine_all<N> over combine4/combine8 envelopes executed; host chunks envelopes.
- `mncs_exec.primary_role/command_reason/combine/fold/*` — **MNCS-EXECUTED** — Canonical mncs_exec entrypoints; each one executes its kernel through mncs_runtime.call and fails closed. No Python fallback exists.
- `mncs_runtime.load/call/encode/decode` — **HOST-EFFECT-BOUNDARY** — Frozen artifact loading, executor subprocess, ABI codec: the adapter itself.
- `model_capabilities.claims/sizes` — **HOST-EFFECT-BOUNDARY** — Claim/size table lookups over string-keyed dicts.
- `model_evidence.load/save-evidence` — **HOST-EFFECT-BOUNDARY** — JSONL evidence store read/write is an FS effect.
- `model_selection._eligible` — **MNCS-EXECUTED** — Evidence lookup, claim tables, reason strings; verdict per needed capability executes mncs.harness.eligibility.v1::capability_eligible via mncs_exec.capability_eligible.
- `model_selection.inventory/claims` — **HOST-EFFECT-BOUNDARY** — Inventory fetch, claims, sizes are transport/dict operations.
- `model_verification.verification` — **HOST-EFFECT-BOUNDARY** — Live model verification over subprocess/HTTP transports.
- `models.RoutingOverride-content` — **BLOCKED:HARNESS-PRESSURE-001** — Mode/field string content (length, control chars) needs strings.
- `models.RoutingOverride.__post_init__` — **MNCS-EXECUTED** — Pin shape table executes mncs.harness.pins.v1::pin_fields_valid via mncs_exec.pin_fields_valid; string content checks host-side.
- `models.data-model` — **HOST-EFFECT-BOUNDARY** — Plain dataclasses transported across the host boundary.
- `ollama.client` — **HOST-EFFECT-BOUNDARY** — Ollama HTTP client is a network effect.
- `policy.CommandPolicy.evaluate` — **MNCS-EXECUTED** — Command ordering executes mncs.harness.policy.v1::evaluate_command via mncs_exec.command_reason; lexing/paths host-side.
- `policy.CommandPolicy.parse/evaluate-encode` — **HOST-EFFECT-BOUNDARY** — Shell lexing, allowlist tables, path resolution are string/FS effects.
- `policy.file_write_decision` — **MNCS-EXECUTED** — Write decision executes mncs.harness.policy.v1::evaluate_write via mncs_exec.write_allowed.
- `portable_cli.wrappers` — **HOST-EFFECT-BOUNDARY** — Entry-point shims delegating to cli modules.
- `prompts.system_prompt` — **BLOCKED:HARNESS-PRESSURE-001** — Prompt templates are string construction.
- `provider.dispatch` — **HOST-EFFECT-BOUNDARY** — Provider dispatch over HTTP/subprocess transports.
- `residency._resource_decision` — **MNCS-EXECUTED** — Integer budget/availability verdict executes mncs.harness.eligibility.v1::resource_gate via mncs_exec.resource_gate; facts contract unchanged.
- `residency.observation-freshness` — **BLOCKED:HARNESS-PRESSURE-005** — Age arithmetic and timeouts need a clock.
- `residency.probes/transports` — **HOST-EFFECT-BOUNDARY** — Probe subprocesses and Ollama HTTP are process/network effects.
- `residency.reconcile-conflict` — **MNCS-EXECUTED** — Implicit-eviction veto executes mncs.harness.eligibility.v1::residency_admit via mncs_exec; inventory/freshness probes host-side.
- `router._deterministic_route` — **MNCS-EXECUTED** — Primary classification + coder chain execute mncs.harness.routing.v1::classify_route/::needs_coder via mncs_exec.primary_role/needs_coder; availability mapping host-side.
- `router.profile_task` — **BLOCKED:HARNESS-PRESSURE-001** — Free-form task text tokenization, regex scans, word counts need strings.
- `router.semantic_route` — **BLOCKED:HARNESS-PRESSURE-002** — Lane scores, thresholds, temperature/top_p need floats.
- `runtime_identity.identity/provenance` — **HOST-EFFECT-BOUNDARY** — Git probing, path inspection, JSON emission are process/FS effects.
- `runtime_identity.identity_is_complete` — **BLOCKED:HARNESS-PRESSURE-001** — Completeness over string identities needs text + digests.
- `semantic_router.scoring` — **BLOCKED:HARNESS-PRESSURE-002** — Score arithmetic needs floats; retained as compatibility-only.
- `tests.mncs_oracle.corpus_cases` — **TEST-BUILD-ONLY** — Test-only agreement oracle + corpus loader; never imported from src/.
- `tools._atlas_gate` — **MNCS-EXECUTED** — Granted-AND-covered verdict executes mncs.harness.atlas.v1::tool_admission via mncs_exec.tool_admission; reason text host-side.
- `tools.handlers/dispatch` — **HOST-EFFECT-BOUNDARY** — Handler dispatch, truncation, subprocess tools are process effects.
- `tui.tui` — **BLOCKED:HARNESS-PRESSURE-004** — Terminal event loop and rendering are TTY effects.
- `verifiers.Verifier._run` — **HOST-EFFECT-BOUNDARY** — py_compile/bash/JSON/TOML/test subprocess execution is a process effect.
- `verifiers.Verifier.verify` — **MNCS-EXECUTED** — Verification acceptance folds through mncs.harness.verdict.v1::combine8/::combine4/::is_decided via mncs_exec.combine/verdict_decided; subprocess checks host-side.
- `windows_worker_launcher.launcher` — **HOST-EFFECT-BOUNDARY** — Windows worker launcher over process effects.
