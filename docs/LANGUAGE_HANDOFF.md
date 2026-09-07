# Language handoff — mncs-harness closure pass

Machine-readable companion: `docs/language-handoff.json` (schema
`mncs.harness-language-handoff.v1`, CI-validated). This document is the
reasoning; the JSON is the contract.

## Where the harness stands

- 88 conversion-ledger entries: **33 MNCS-EXECUTED, 37 HOST-EFFECT-BOUNDARY,
  14 BLOCKED, 4 TEST-BUILD-ONLY, 0 MIGRATION-DEBT.**
- 8 kernels, 168 corpus cases (336 backend-case executions), 16 shipped
  artifacts, real MNCS execution on every converted product path, no Python
  fallback.
- Toolchain: mncs-language
  `8d79250d54d4e4241c0bb0ae6f5c632345848a6d`, executor release
  `toolchain/mncs-executor-8d79250`
  (`sha256:f7d6082…0210`), manifest
  `sha256:a0f31b5…e3b5`.

## Recommended language-campaign order

Not severity order — leverage order (surfaces unlocked × security value ×
host-authority reduction × prerequisites × tractability):

1. **HARNESS-PRESSURE-001** (bounded strings) → unlocks 5 BLOCKED entries
   outright, completes fleet + capgraph, and is a prerequisite for 009.
   Smallest useful slice: equality/ordering/length over bounded views —
   the harness never needs a full text type.
2. **HARNESS-PRESSURE-006** (verify-only crypto syscalls) → the
   highest-assurance path (Atlas issuance) still trusts 700+ lines of
   Python. Verify-only Ed25519 + SHA-256 over bounded views; no keygen
   in-language. Independent of 001 — could go first on security grounds.
3. **HARNESS-PRESSURE-004** (one read-only capability seed) → prerequisite
   for every effect migration; first slice confines one read path instead
   of blessing it. Deliberately small: bounded file read into byte views.
4. **HARNESS-PRESSURE-005** (clock read) → unlocks 3 BLOCKED entries and
   completes the residency kernel. Opaque instants + `elapsed`/`expired`
   comparisons keep wall-clock trust at the host boundary.
5. **HARNESS-PRESSURE-009** (canonical JSON emission) → unlocks config and
   metrics paths; builds on the 001 string slice and the existing scanner
   substrate. TOML decode second; SQLite out of scope.
6. **HARNESS-PRESSURE-002** (floats or scaled decimals) → unlocks only the
   compatibility-only scoring paths; core budgets already converted via
   scaled integers (`resource_gate` pattern), so this ranks below 005.
7. **HARNESS-PRESSURE-007** (bounded task/cancel seed) → the agent runtime
   is the biggest remaining Python subsystem; RFC 0045 trace shape is the
   seed. Requires 004 first. Long arc — start after the quick wins below.
8. **HARNESS-PRESSURE-008** (package resolver) → deletes the 3-function
   lattice mirror; no BLOCKED surface, cost contained by tests. Do when
   touching the resolver anyway.
9. **Quick wins, anytime, independent:** 013 (bool match arms), 014
   (`capability` identifier or truthful diagnostic), 012 (one docs
   paragraph + cascade suppression).
10. **Monitor only:** 011 (cost evidence; revisit with 004/005), 015
    (bytecode latency; check before any default-backend change).

Dependency chain: 001 → 009; 004 → 007; 004/005 → 011. Everything else is
independent.

## Closed in the closure pass (do not re-litigate)

- **003/010:** all three fold kernels now use generic `<N>` folds with
  monomorphic envelopes; host chunking is ABI transport, not a workaround.
- **Transitional Python:** removed. `tests/mncs_oracle.py` is test-only.

## Per-pressure detail

See `language-handoff.json`: blocked surfaces, workarounds, reproducers,
pinned diagnostics, desired capability, unlock value, acceptance and
integration tests. Verified on the pinned toolchain in the closure pass:

- Reproducers 001/002/003/008/013/014 re-run at
  `8d79250d…` — diagnostics MNE105 / MNP094 / MNE173 / MNP084 / MNP024
  all reproduce exactly.
- 004/005/006/007/009 absence re-checked against the pinned stdlib
  (`library/std`, `library/core`): no fs/process/net/clock/crypto/tty/
  TOML/emission surface.
