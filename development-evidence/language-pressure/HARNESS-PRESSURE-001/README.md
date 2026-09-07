# HARNESS-PRESSURE-001 — no string/text type (language/types, BLOCKER)

Full analysis: `docs/language-pressure.md` (HARNESS-PRESSURE-001).

## Harness requirement

router.profile_task classifies free-form task text; `text: string` fails with MNE105.

## Reproduction

```bash
cargo run -p mncs-cli -- source-study repro.mncs --node-id harness-pressure-001
```

Run from an `mncs-language` checkout. Expect failure with diagnostic `MNE105`
(see `expected.md`).

## Workaround in this repository

mncs/harness_routing.mncs decides on six host-encoded booleans instead.
