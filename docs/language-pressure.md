# MNCS language-pressure ledger (harness conversion campaign)

This document is a first-class deliverable of the mncs-harness
MNCS-conversion campaign. Each entry records real harness behavior that
pressured mncs-language, what was proven, what was worked around, and what
the follow-up language campaign should investigate. Nothing here was fixed
by changing the language: workarounds live host-side and are measured.

Authoritative MNCS surface built by this campaign:

- `mncs/harness_routing.mncs` — primary-role classification + coder chain
- `mncs/harness_policy.mncs` — command / file-write policy kernel
- `mncs/harness_verdict.mncs` — status-lattice evidence combination
- `mncs/harness_atlas.mncs` — Atlas verdict fold + dispatch gate
- `corpora/harness-*.json` — 57 executable cases
- `development-evidence/mncs-execution/` — 114 backend-case executions
  (portable-WASM + research-bytecode), all `PASS`
- `src/mncs_harness/mncs_logic.py` — evidence-pinned host projection
- `tests/test_mncs_logic.py` — corpus + live-path agreement

Reproducers for the four compiler-proven entries live under
`development-evidence/language-pressure/HARNESS-PRESSURE-*/`.

Conventions: severity is `BLOCKER` / `MAJOR` / `MODERATE` / `ERGONOMIC` /
`DOCUMENTATION`. Every entry distinguishes **(A)** "MNCS cannot currently
express this" from **(B)** "the harness assumed a Python-specific model".

---

## HARNESS-PRESSURE-001 — no string/text type

- **Area:** language/types
- **Severity:** BLOCKER (for in-language task understanding)
- **Harness requirement:** `router.profile_task` classifies free-form task
  text: substring term tables (`CODE_TERMS`, `HIGH_RISK_TERMS`, …), regex
  file-pattern scans, word counts, code-fence detection. This is the entry
  point of every `mncs-harness route` / `ask` invocation.
- **Desired MNCS expression:** `fn profile_task(text: string) -> TaskFlags`
  with bounded substring/regex matching over a text value.
- **Current behavior:** `string` is not a type. A `text: string` parameter
  fails elaboration with `MNE105`: *"source profile supports bool, byte,
  8/16/32/64-bit integers, declared finite/record types, bounded sequences,
  and Profile 0.8 integer vec<T, N>/mask<N>"*. Verdict: **(A)**.
- **Reproduction:** `development-evidence/language-pressure/
  HARNESS-PRESSURE-001/` (`repro.mncs`, diagnostic capture).
- **Workaround:** the host tokenizes and encodes six booleans;
  `mncs/harness_routing.mncs::classify_route` decides on the encoded flags.
  The six-flag ABI is documented on the function and pinned by 12 corpus
  cases. **Cost:** the most security-sensitive classifier input (raw text,
  including adversarial prompt content) never crosses into verified code;
  the bool-encoding step is unverified Python. Any future string support
  must be bounded (max length, no ambient allocation) to preserve the
  verification story.
- **Proposed direction:** bounded text views exist for bytes
  (`[byte; up_to 64]`, `std/text_view.v1`); investigate a bounded `string`
  scalar or text-view parameter type with total substring/match primitives
  before any heap-string or regex-engine proposal.

## HARNESS-PRESSURE-002 — no floating-point type

- **Area:** language/types
- **Severity:** MAJOR
- **Harness requirement:** semantic-router lane scores (`0.35 + …`),
  `minimum_score` / `minimum_margin` thresholds, model sampling parameters
  (`temperature`, `top_p`), resource byte-ratio arithmetic.
- **Desired MNCS expression:** `fn score_blend(base: f32, bonus: f32)
  -> (result: f32)` and ordered float comparison for threshold gates.
- **Current behavior:** `f32` is not a type; same `MNE105` refusal as
  strings. Verdict: **(A)**.
- **Reproduction:** `development-evidence/language-pressure/
  HARNESS-PRESSURE-002/`.
- **Workaround:** the semantic router stays host-side (it is already
  compatibility-only per the agent contract); thresholds are evaluated in
  Python. The deterministic router kernel avoids scores entirely by
  construction. **Cost:** any future score-based routing cannot move into
  MNCS without fixed-point conventions invented per call site.
- **Proposed direction:** decide whether MNCS wants fixed-point/scaled-int
  score conventions in stdlib first (portable, total), or a real float
  type with explicit rounding/NaN semantics. The harness is a willing
  consumer of either for lane scoring.

## HARNESS-PRESSURE-003 — literal-only iteration bounds

- **Area:** language/control
- **Severity:** MODERATE
- **Harness requirement:** scans over runtime-sized inputs: word lists,
  referenced-file lists, worker inventories, check envelopes.
- **Desired MNCS expression:** `iterate i over xs` for caller-provided
  bounded sequences (exists in Profile 0.7), or `up_to n` with a
  runtime bound.
- **Current behavior:** `iterate … up_to n` with a non-literal `n` is a
  parse error (`MNP094`, *"expected a literal iteration bound"*). Sequence
  traversal over `[T; N]` / `[T; up_to N]` values does accept runtime
  lengths, so the capability exists in one spelling but not the other.
  Verdict: mostly **(A)** with a real **(B)** residue — the harness
  reframed envelopes as fixed `combine8` / `fold4` trees instead of
  sequence folds, trading clarity for profile comfort.
- **Reproduction:** `development-evidence/language-pressure/
  HARNESS-PRESSURE-003/`.
- **Workaround:** fixed-arity fold trees (`combine4`/`combine8`,
  `fold_pair`/`fold4`); the host chunks open-ended lists into fixed
  envelopes. **Cost:** O(n) listing behavior becomes host-side chunking
  code plus one MNCS call per chunk; envelope widths are magic numbers.
- **Proposed direction:** Profile 0.7 sequence traversal already covers
  the honest need — a follow-up should rewrite the harness kernels in
  sequence/generic form (Profile 0.10) and record whether anything real
  still requires runtime `up_to` bounds.

## HARNESS-PRESSURE-004 — no realizable I/O effects

- **Area:** stdlib/capability, runtime/host
- **Severity:** BLOCKER (for full conversion; accepted as host boundary)
- **Harness requirement:** everything the harness is *for*: workspace file
  reads/writes, subprocess execution with stdin/stdout/stderr/exit codes,
  Ollama HTTP, Fabric transport, Commons MCP, SQLite metrics, TUI output.
- **Desired MNCS expression:** declared capabilities with host-realized
  effects per `spec/effects-and-capabilities.md` (e.g.
  `effect observe authorized_by retry_authority` already parses).
- **Current behavior:** effect *declarations* elaborate, but there is no
  stdlib surface (`fs`, `process`, `net`, `clock`, `tty`) and no host
  effect runner behind `experiment execute`; backends return logical
  values only. All I/O in this campaign stayed Python. Verdict: **(A)**.
- **Reproduction:** absence finding — cite `spec/effects-and-capabilities.md`
  against `library/` (no I/O modules) and the `mncs execute-backend`
  contract (logical values in/out).
- **Workaround:** strict split — MNCS owns pure decision kernels, Python
  owns every effect. The split is enforced by `mncs_logic` docstrings and
  `tests/test_mncs_logic.py`. **Cost:** the security boundary
  (workspace confinement, approval) is implemented in the unverified host;
  MNCS can bless decisions but cannot confine effects.
- **Proposed direction:** do not boil the ocean. The smallest useful step
  is one host-realized read-only capability (e.g. bounded file read into
  `[byte; up_to 64]` views, composing with the existing JSON scanner),
  so a real harness read path can cross the boundary under test.

## HARNESS-PRESSURE-005 — no clock, duration, or entropy source

- **Area:** stdlib/capability
- **Severity:** MAJOR
- **Harness requirement:** residency freshness (`max_age_seconds`,
  `runtime_probe_max_age_seconds`), metrics timestamps, command timeouts,
  experiment scheduling, provider-observed state ages.
- **Desired MNCS expression:** monotonic-clock reads and duration
  comparison as explicit capabilities; domain-separated randomness beyond
  the deterministic LCG (`core/random.v1` disclaims independence).
- **Current behavior:** no clock or entropy capability exists; all
  freshness math (`_observation_freshness`, `_resource_decision`) is host
  Python on floats. Verdict: **(A)**.
- **Workaround:** host computes ages/booleans, passes results in.
  **Cost:** timeout and freshness policy — reliability-critical logic —
  is unverified; tests pin behavior only at the Python level.
- **Proposed direction:** a `clock.read MonotonicClock`-shaped capability
  returning an opaque instant with total `elapsed`/`expired` comparisons
  would let the residency kernel move into MNCS while keeping wall-clock
  trust at the host boundary.

## HARNESS-PRESSURE-006 — no cryptographic primitives

- **Area:** stdlib/crypto, runtime/host
- **Severity:** MAJOR
- **Harness requirement:** Atlas Ed25519 issuance verification, SHA-256
  canonical-JSON digests, trust-root key handling (`atlas_binding.py`).
- **Desired MNCS expression:** digest comparison already has an opaque
  vocabulary (`core/identity.v1`, `core/lineage.v1`); signature
  verification and canonical encoding do not.
- **Current behavior:** all crypto executes via Python `cryptography`;
  the language owns the *contract* (`mncs-model::authority`) while the
  harness carries a golden-vector-pinned Python projection. Verdict:
  **(A)** for execution, with the contract/model side healthy.
- **Workaround:** `mncs/harness_atlas.mncs` owns the verdict *fold*
  (denial wins, UNKNOWN never promotes, target-gate demotion) — the part
  that is pure logic — while issuance math stays host-side. **Cost:**
  the highest-assurance path in the system still trusts 700+ lines of
  Python for signature math; the MNCS fold covers ordering, not issuance.
- **Proposed direction:** host-boundary crypto syscalls with
  language-visible effects (verify-only, no keygen in-language) so the
  accept/confirm path can be end-to-end MNCS with auditable crypto
  boundaries.

## HARNESS-PRESSURE-007 — no concurrency, async, or cancellation

- **Area:** language/effects, runtime
- **Severity:** BLOCKER (for agent-runtime conversion)
- **Harness requirement:** multi-turn tool-calling loop (`agent.py`),
  detached inference, periodic inventory refresh, concurrent worker
  fan-out, cancellation on operator abort.
- **Desired MNCS expression:** bounded task spawn/join with explicit
  cancellation and effect-closure over spawned tasks.
- **Current behavior:** the only repetition form is bounded `iterate`
  with literal bounds; there are no tasks, channels, or cancellation
  tokens. Verdict: **(A)**.
- **Workaround:** the entire runtime stays Python; MNCS contributes only
  per-step decisions (route/policy/verdict). **Cost:** the subsystem with
  the most failure modes (partial failure, timeout races, orphaned
  workers) is also the least verified.
- **Proposed direction:** out of scope for the next language tranche to
  *complete*, but the stateful-trace execution preview (RFC 0045,
  `init → transition* → finish` with trace provenance) is the natural
  seed: the harness tool loop wants exactly that shape with
  cancellation added.

## HARNESS-PRESSURE-008 — no cross-repository module resolution

- **Area:** tooling/modules
- **Severity:** MODERATE
- **Harness requirement:** reuse `mncs.core.status.v1` (`dominate`,
  `combine`, `dominate4`, `is_decided`) in `mncs/harness_verdict.mncs`
  instead of copying the lattice.
- **Desired MNCS expression:** `use mncs.core.status.v1;` resolving to
  the mncs-language checkout's `library/` by package identity.
- **Current behavior:** the research CLI resolves `use` names relative
  to the importing file's directory; from this repository the import
  fails closed with `MNE173`: *"imported module 'mncs.core.status.v1' is
  unavailable to the resolver"*. Verdict: **(A)** tooling, not language
  semantics (elaboration-time linking itself works — Profile 0.9).
- **Reproduction:** `development-evidence/language-pressure/
  HARNESS-PRESSURE-008/`.
- **Workaround:** a bounded three-function mirror of the lattice inside
  `harness_verdict.mncs`, with agreement against the stdlib pinned by
  `tests/test_mncs_logic.py` (the 3×3 dominate table plus combine widths
  match `library/core/status.mncs` semantics exactly). **Cost:** two
  authorities for one lattice until a registry exists; drift risk is
  contained by tests but real.
- **Proposed direction:** a package/workspace resolver (language-service
  hosts already resolve against resident workspace documents) or a
  vendoring contract with identity-pinned imports, so consumers bind the
  stdlib by identity across repository boundaries as the library README
  already promises.

## HARNESS-PRESSURE-009 — validation-only structured data, no emission or TOML

- **Area:** stdlib/encoding
- **Severity:** MODERATE (MAJOR for config/metrics paths)
- **Harness requirement:** TOML config load+merge (`config.py`), JSON
  envelope construction (Atlas requirements, Fabric messages, metrics
  rows), SQLite persistence (`metrics.py`).
- **Desired MNCS expression:** total JSON/TOML decode *and* canonical
  encode over bounded views.
- **Current behavior:** `std/json.v1` scans syntax, `std/json_stream.v1`
  streams structure, `std/json_cursor.v1` / `std/json_projection.v1`
  project keys — all recognition, no DOM, no emission, no TOML, no SQLite.
  Verdict: **(A)** partial capability (recognition exists and is good).
- **Workaround:** all serialization stays host-side; MNCS sees only
  pre-decoded flags/codes. **Cost:** schema evolution (new config keys,
  new envelope fields) is unverified stringly-typed Python; the JSON
  scanner cannot protect paths that never call it.
- **Proposed direction:** bounded canonical-JSON emission for the same
  `TextView`/view substrate the scanner uses would unlock one real path
  (e.g. metrics-row or requirement-envelope emission) as the next
  conversion slice.

## HARNESS-PRESSURE-010 — 64-element ceiling and fixed envelopes

- **Area:** language/bounds, ergonomics
- **Severity:** MODERATE
- **Harness requirement:** open-ended host data: worker inventories, file
  lists from `rglob`, check envelopes, lane tables.
- **Desired MNCS expression:** generic sequence folds (`summarize<N>`,
  Profile 0.10) over caller-sized data.
- **Current behavior:** `N` is bounded by `MAX_SEQUENCE_BOUND` (64); the
  campaign used fixed `combine8`/`fold4` trees rather than sequence folds
  to stay in Profile 0.6 comfort. Verdict: **(B)**-leaning — the language
  has the honest spelling (Profile 0.10 generics), the campaign did not
  use it.
- **Workaround:** host-side chunking into fixed envelopes.
  **Cost:** envelope widths as magic numbers; chunking code unverified.
- **Proposed direction:** rewrite the four kernels in Profile 0.10
  sequence/generic form as a narrow follow-up; if anything real resists
  (ABI limits on sequence-of-record, mask/vector element refusals), file
  it as a backend pressure with the attempt as evidence.

## HARNESS-PRESSURE-011 — exact-cost obligations stay UNKNOWN

- **Area:** compiler/evidence
- **Severity:** MODERATE
- **Harness requirement:** resource budgeting: VRAM reservations,
  `model_storage_bytes`, worker placement weights, timeout budgets.
- **Desired MNCS expression:** experiment results carrying usable cost
  evidence for bounded kernels.
- **Current behavior:** all four harness corpora return `PASS` with empty
  `unresolved_reasons`, yet exact instruction cost remains `UNKNOWN` by
  contract wherever arithmetic exists. The harness cannot distinguish
  "cheap kernel" from "expensive kernel" from language evidence.
  Verdict: **(A)** known contract position, observed (not discovered) here.
- **Workaround:** budgets stay operator-configured constants.
  **Cost:** none today; blocks future cost-aware placement moving into MNCS.
- **Proposed direction:** no action until HARNESS-PRESSURE-004/005 move;
  recorded so the next campaign does not mistake `PASS` for "free".

## HARNESS-PRESSURE-012 — match-arm spelling and cascade diagnostics

- **Area:** tooling/diagnostics, documentation
- **Severity:** DOCUMENTATION (with an ERGONOMIC tail)
- **Harness requirement:** writing correct MNCS by example during the
  campaign.
- **Observed behavior:** (1) checked-in fixtures disagree on payload-less
  match arms: `library/core/status.mncs` matches `PASS => …` bare, while
  `examples/source/profile06-payload-sums.mncs` matches `Defer { } => …`
  with braces. Both compile, so both are accepted — but no doc states the
  rule (payload-carrying enums appear to require `{ }` even on
  payload-less variants, payload-free enums accept bare names). The
  campaign guessed right on the first try and both styles appear in
  `mncs/` (`Role` matched bare, `PolicyVerdict.Allow` matched as
  `Allow { }`). (2) One mistake — a non-literal `up_to` bound — yields a
  15-diagnostic cascade (`MNP094` followed by 14 `MNP061/064/095…`
  parse-recovery errors), burying the single real error.
- **Workaround:** none needed for (1) once guessed; (2) costs every
  newcomer one confused read per mistake.
- **Proposed direction:** one paragraph in the profile docs pinning the
  arm-spelling rule, plus parse-recovery that suppresses cascading
  statement errors after a failed iteration header.

---

## What converted cleanly (no pressure)

For balance, the record should show where the language carried real
application weight without friction:

- Three-valued verdict lattices with exhaustive matching caught two
  draft bugs at authoring time (a missing `UNKNOWN` arm refused to
  elaborate — the exact guarantee the harness relies on).
- Payload-bearing enums (`PolicyVerdict.Block { reason }`) replaced a
  whole class of `(bool, str)` and `(bool, int)` Python tuples with
  checked shapes.
- `source-study` + `experiment run` + sealed result JSON gave the campaign
  a better evidence story than the Python suite had: 114 backend-case
  executions with identities, step counts, and expectation checks.
- First-try `completed` status on all four modules; failure diagnostics
  (`MNE105`, `MNE173`, `MNP094`) are specific and quotable.
- The `mncs:0.2:` identity prefix on corpus `finite` values works across
  profiles 0.6–0.10 without friction (though the versioning story could
  use one clarifying paragraph — see HARNESS-PRESSURE-012 scope).

## Deliberately host-side (not pressure)

These stayed Python by design, not for lack of trying: process spawning
and lifecycle, HTTP/Ollama, Fabric/Commons transports, SQLite, TUI event
loop, approval interaction, TOML parsing, Ed25519/SHA-256 math, wall-clock
time. Each maps to HARNESS-PRESSURE-004/005/006/007/009 above; they are
listed here so the next campaign does not re-litigate the boundary.
