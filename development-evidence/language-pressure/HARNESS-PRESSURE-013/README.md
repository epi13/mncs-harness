# HARNESS-PRESSURE-013 — boolean literals are not match patterns (language/surface, ERGONOMIC)

Full analysis: `docs/language-pressure.md` (HARNESS-PRESSURE-013).

## Harness behavior

Branching per-capability unknown-policy logic on plain bools reads naturally as `match flag { true => …, false => … }`; the parser rejects it with MNP084 plus a 3-error cascade.

## Reproduction

```bash
cargo run -p mncs-cli -- source-study repro.mncs --node-id harness-pressure-013
```

Run from an `mncs-language` checkout. Expect elaboration failure with `MNP084 (+ cascade)`
(see `expected.md`).

## Workaround in this repository

if/else chains and helper functions (see mncs/harness_eligibility.mncs tools_unknown/tools_compat).
