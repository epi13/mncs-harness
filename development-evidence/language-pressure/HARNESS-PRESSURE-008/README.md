# HARNESS-PRESSURE-008 — no cross-repository module resolution (tooling/modules, MODERATE)

Full analysis: `docs/language-pressure.md` (HARNESS-PRESSURE-008).

## Harness requirement

`use mncs.core.status.v1` from this repo fails with MNE173; the CLI resolves `use` relative to the importing file only.

## Reproduction

```bash
cargo run -p mncs-cli -- source-study repro.mncs --node-id harness-pressure-008
```

Run from an `mncs-language` checkout. Expect failure with diagnostic `MNE173`
(see `expected.md`).

## Workaround in this repository

harness_verdict.mncs mirrors the status lattice; agreement pinned by tests/test_mncs_logic.py.
