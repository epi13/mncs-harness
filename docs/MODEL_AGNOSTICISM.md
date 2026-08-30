# Model identity is data, not policy

Harness selects implementations from Fabric inventory. A model tag is an opaque
identity. Brand, family, or substrings such as `qwen`, `granite`, `gemma`,
`coder`, or `devstral` never confer capability.

## Ownership

| Layer | Owns |
| --- | --- |
| Fabric | workers, resources, installed/loaded models, provider-reported claims, freshness |
| Harness | task requirements, policy preferences, observed-capability evidence, ranking |
| Operator | exact pins and optional role-preference tags |

Exact pins remain first-class: `--worker` / `--model-name` fail closed unless
`--allow-fallback` is explicit.

Configured `[models.<role>.name]` values are **operator preferences**. They are
used when present in CURRENT inventory. They are not architectural requirements.
The system remains functional when none of the historical tags exist.

## Three concepts

1. **Provider-reported capabilities** — claims such as `completion` or `tools`.
2. **MNCS-observed capabilities** — evidence from `elh verify-models`.
3. **Policy preferences** — prefer small/resident/large, mutation fail-closed.

Unknown is represented as unknown. Missing evidence is not treated as PASS.

## Compatibility mechanism

`provider-claim-compat` is an explicit compatibility layer used when a mutation
role has no MNCS-observed tool evidence yet. It may rank candidates using
provider-reported `tools` or, if no claims exist, size policy only. The reason
string says so. It is **compatibility machinery**, not proof.

Removal condition: once `elh verify-models --persist` has CURRENT evidence for
the relevant workers, observed PASS/FAIL outranks the compatibility path.

## Verification

```bash
elh verify-models --json
elh verify-models --worker fabric-worker-01 --tier 0 --tier 1 --persist
```

Tiers: 0 reachability, 1 protocol, 2 claimed capabilities, 3 Fabric receipt.

A tiny chat model that fails a code-edit probe is not invalid; that capability
is simply not demonstrated.

## Family audit (this change)

| Location | Classification | Action |
| --- | --- | --- |
| Harness `_CODE_HINTS` | harmful coupling | removed |
| Harness role preference tags | operator preference | labeled, no longer required |
| Live E2E granite/gemma defaults | harmful coupling | discovery unless explicit pin |
| Worker provisioning default model set | operator preset | requires `--model` or `--use-configured-preferences` |
| Semantic-router LFM2 default | remnant | removed from bundled config |
| Semantic-router module | compatibility remnant | remains only for old configs/tests |
| Fabric capability observations | correct architecture | unchanged |
| Tests using gemma/qwen names | fixtures / pins | kept where they are explicit pins |
| Historical development-evidence JSON | historical record | unchanged |

## Live E2E

`tests/test_live_distributed_inference.py` discovers models from Fabric unless
`MNCS_E2E_LINUX_MODEL` / `MNCS_E2E_WINDOWS_MODEL` are set as explicit pins.
There is no silent Granite/Gemma default.
