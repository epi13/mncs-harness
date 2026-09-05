# MNCS agent execution contract (harness)

This repository routes model/tool work; it does not set semantics. The
authority boundary (see `docs/IDENTITY.md`) is load-bearing:

- **MNCS Control MCP** constrains where remote/operator actions may occur.
- **MNCS Harness** decides how work is routed, governed, approved, accepted.
- **MNCS Fabric** executes approved work on enrolled workers.
- **MNCS Commons** remembers durable structured knowledge and coordination.
- **MNCS Forge** evaluates development workflows, evidence, and gaps.

This file adopts the ecosystem agent contract bound in mncs-actions
(`AGENTS.md` there) with the language mirror in mncs-language. It is
enforced by `tests/test_agent_contract.py`: every path named below must
exist. Contract drift is a defect.

## 1. MNCS-language is the implementation default

Decision logic that MNCS can express belongs in MNCS source, not in
`src/epi13_local_harness/` Python. Host code here is routing, governance,
approval, and transport. Before adding capability to the harness, check
mncs-language (`library/std`, `library/core`); a missing MNCS capability is
a language-pressure event routed to mncs-language, never a reason to grow a
harness-local substitute.

## 2. Pressure routing

Unresolved needs become development-pressure evidence with an explicit owner
(same authority vocabulary as the mncs-actions contract: pressure semantics
MNCDS, rights mncs-rights-provenance, coordination MNCS-Commons, language
capability mncs-language, assurance mncs-forge-mcp, transport the routing
repository). Fix upstream, re-run that repository's suite, then resume here.

## 3. Routing and acceptance must be evidence-honest

- A request sent to Fabric is not execution. Acceptance decisions must trace
  to execution receipts identifying host, target, and native versus emulated
  execution. A remote request that silently fell back locally is a failure.
- Deterministic verifiers decide pass/fail; model output never promotes
  itself. `PASS`, `FAIL`, and `UNKNOWN` stay distinct.
- Scheduling is capability-based (`os.*`, `arch.*`, `execution.*`,
  `accelerator.*`); worker names are deployment details.

## 4. Badges derive from evidence

This repository currently carries no MNCS conformance badge in `README.md`;
do not add a decorative one. When a badge is added it must render the
current evidence-driven verdict (mncs-actions family workflow), never a
hand-edited claim, and must not overstate compile versus execution or
emulated versus physical proof.
