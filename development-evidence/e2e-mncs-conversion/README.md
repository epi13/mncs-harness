# End-to-end proof: converted mncs_harness doing real work through MNCS

Chain proven on 2026-09-06 (branch `feat/mncs-conversion-pressure`):

```text
operator text input
  -> python3 -m mncs_harness.cli route ...      (canonical package, no legacy tree)
  -> router.profile_task                        (host: tokenize, regex, flags)
  -> mncs_logic.classify_route                  (evidence-pinned MNCS projection)
  -> mncs/harness_routing.mncs::classify_route  (authoritative MNCS kernel)
  -> mncs-portable-wasm-mvp + research-bytecode (real backend execution)
  -> observable route decision
```

## 1. Product path (host executes the kernel projection)

| Input | Observed `primary_role` | Evidence |
|---|---|---|
| `Explain what systemctl status ollama means` | `e2b` | `route-explanatory.json` |
| `Repair the Python tests in this repository` | `e4b` → `coder`, `reviewer` | `route-code-edit.json` |
| `Use sudo to reinstall the service as root` | `reviewer` | `route-high-risk.json` |
| Routing evaluations (`cli eval`, same gate as CI) | `6/6 passed` | `eval.json` |

The `route` subcommand runs fully offline against the bundled reference
profile; no worker model is invoked. The primary-role branch in
`router._deterministic_route` is computed by `mncs_logic.classify_route`,
and the coder-chain bit by `mncs_logic.needs_coder` — both pinned to the
MNCS kernels by `tests/test_mncs_logic.py` (64-vector flag cube).

## 2. Language path (toolchain executes the authoritative kernel)

Fresh `experiment run` of `mncs/harness_routing.mncs` over
`corpora/harness-routing-corpus.json` (12 cases):

- portable-WASM: `PASS`, 12/12 expectations met (`routing-wasm-result.json`)
- research-bytecode: `PASS`, 12/12 expectations met
  (`routing-bytecode-result.json`)
- `experiment compare` across backends: `same_source: true`,
  `same_semantics: true`, `same_ssa: true`, `bounded_behavior_agrees: true`
  (`experiment-compare.json`)

The CLI flag vectors above correspond to corpus cases `explanatory-code-
no-tools` (E2B), `code-edit` (E4B), and `high-risk-code` (REVIEWER): the
same truth table the backends executed is the one that routed the product
commands.

## 3. What this proves — and what it does not

Proves: the converted `mncs_harness` performs real routing work through
MNCS-authored, backend-executed decision logic, with byte-identical
expectations across two independent backends.

Does not prove: model invocation, tool execution, or Fabric placement
through MNCS — those remain Python host boundaries per
HARNESS-PRESSURE-004/006/007, exercised by the existing Python suite, not
by this evidence.
