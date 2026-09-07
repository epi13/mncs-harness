# Expected diagnostics for HARNESS-PRESSURE-003

Sealed against mncs-language `source-study` output (2026-09-06).
Re-run: `cargo run -p mncs-cli -- source-study repro.mncs --node-id <id>`
from an mncs-language checkout.

- `MNP094 :: expected a literal iteration bound`
- `MNP095 :: expected 'carrying' after iteration bound`
- `MNP097 :: expected ':' after carried state`
- `MNP098 :: expected carried state type`
- `MNP099 :: expected '=' before initial carried state`
- `MNP064 :: expected expression`
- `MNP100 :: expected '{' before iteration body`
- `MNP061 :: expected let, if, fail, return, or bounded iteration (x8 cascade)`
