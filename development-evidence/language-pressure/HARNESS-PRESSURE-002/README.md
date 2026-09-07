# HARNESS-PRESSURE-002 — no floating-point type (language/types, MAJOR)

Full analysis: `docs/language-pressure.md` (HARNESS-PRESSURE-002).

## Harness requirement

lane scores, thresholds, and sampling params need floats; `f32` fails with MNE105.

## Reproduction

```bash
cargo run -p mncs-cli -- source-study repro.mncs --node-id harness-pressure-002
```

Run from an `mncs-language` checkout. Expect failure with diagnostic `MNE105`
(see `expected.md`).

## Workaround in this repository

score-based routing stays host-side; the deterministic kernel avoids scores by construction.
