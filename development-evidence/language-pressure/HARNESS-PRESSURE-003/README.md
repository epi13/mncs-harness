# HARNESS-PRESSURE-003 — literal-only iteration bounds (language/control, MODERATE)

Full analysis: `docs/language-pressure.md` (HARNESS-PRESSURE-003).

## Harness requirement

`iterate up_to n` with runtime `n` fails with MNP094 plus a 14-error parse cascade.

## Reproduction

```bash
cargo run -p mncs-cli -- source-study repro.mncs --node-id harness-pressure-003
```

Run from an `mncs-language` checkout. Expect failure with diagnostic `MNP094 (+ cascade)`
(see `expected.md`).

## Workaround in this repository

fixed-arity fold trees (combine8/fold4); the host chunks open-ended lists.
