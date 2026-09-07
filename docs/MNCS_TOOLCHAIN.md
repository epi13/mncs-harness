# MNCS toolchain pin and executor distribution

The harness executes shipped frozen MNCS artifacts; it never compiles at
runtime. Both sides of that statement are pinned here.

Four distinct identities (never conflate them):

1. **Source toolchain revision** (compiler+executor source):
   `epi13/mncs-language`
   `8d79250d54d4e4241c0bb0ae6f5c632345848a6d`
   (full immutable SHA — validated 2026-09-06; all corpus cases PASS on
   both backends with this revision's compiler and executor).
2. **Executor release:** `toolchain/mncs-executor-8d79250`
   (`mncs-executor-linux-x86_64`)
3. **Executor digest:**
   `sha256:f7d60825cb58acaff46bed78639d3cffed7da52e9944e2c9ff16dd67795d0210`
   (release bytes; reproducible — a local `cargo build --release -p mncs-cli`
   at the pinned revision produces this exact digest)
4. **Kernel artifact identities:** `src/mncs_harness/_mncs_artifacts/MANIFEST.json`
   (per-artifact `identity` + `bytes_sha256`; drift-gated in CI)

`scripts/check_toolchain_identity.py` proves 1–4 agree; CI runs it.

## Executor resolution order (product runtime)

1. `MNCS_EXECUTOR` environment variable (explicit file path).
2. `mncs-executor` found on `PATH` (the documented distributed name —
   where `scripts/fetch_mncs_executor.py` installs it by default).
3. A sibling `mncs-language` checkout build
   (`../mncs-language/target/{debug,release}/mncs`, developers only).
4. Otherwise fail closed with `MncsRuntimeError` pointing at
   `scripts/fetch_mncs_executor.py`.

The runtime never downloads anything by itself: fetching an executor is an
explicit operator/CI action, always digest-verified.

## Fetching (explicit)

```bash
python3 scripts/fetch_mncs_executor.py --dest ~/.local/bin/mncs-executor
```

Verifies the SHA-256 digest above before marking the file executable.
Ensure `~/.local/bin` is on `PATH` so step 2 resolves it, or export
`MNCS_EXECUTOR` explicitly (what CI does). CI and the family boundary use
this; developers with a checkout can also
`cargo build --release -p mncs-cli` inside mncs-language instead (the
sibling-checkout fallback in step 3 finds that build).

## Building artifacts

```bash
python3 scripts/build_mncs_artifacts.py        # rebuild + ship
python3 scripts/build_mncs_artifacts.py --verify  # rebuild + compare (CI drift gate)
```

## Backend selection

- Shipped per kernel: `wasm` (portable-WASM, default, ~5ms/call) and
  `bytecode` (research-bytecode, ~117ms/call in the debug executor).
- `MNCS_HARNESS_BACKEND=bytecode` selects the bytecode artifacts.
  CI proves both; evidence parity is required.

## Upgrading the pin

1. Check out the new mncs-language revision and rebuild artifacts.
2. Re-run the full corpus evidence; all must PASS on both backends.
3. Publish a new executor release; update the digest here and in
   `scripts/fetch_mncs_executor.py`.
4. CI drift gate (`--verify`) will fail until shipped artifacts match.
