# HARNESS-PRESSURE-014 — 'capability' is unusable as a parameter name (language/surface, ERGONOMIC)

Full analysis: `docs/language-pressure.md` (HARNESS-PRESSURE-014).

## Harness behavior

The eligibility kernel naturally names its subject `capability: Capability`; the parameter grammar rejects it (MNP024) and recovery misfires into MNP042 'expected capability name' plus 9 further cascade errors.

## Reproduction

```bash
cargo run -p mncs-cli -- source-study repro.mncs --node-id harness-pressure-014
```

Run from an `mncs-language` checkout. Expect elaboration failure with `MNP024 (+ MNP042-led cascade)`
(see `expected.md`).

## Workaround in this repository

renamed the parameter to `gate` (mncs/harness_eligibility.mncs).
