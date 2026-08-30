# Experiment model residency

Sustained experiments use an explicit, observable model-weight lifecycle. Harness
owns the policy, Fabric transports exact-target jobs and evidence, and the
worker-local provider owns the actual process and weights.

```text
Control experiment context/messages
             |
             | exact worker + model assignment
             v
Harness prepare -> Fabric exact dispatch -> provider warm -> current loaded observation
       |                                                    |
       +---- sync / detached / tool follow-up inference ----+
             (each request reinforces experiment keep_alive)
       |
Harness teardown -> Fabric exact dispatch -> provider release -> current absent observation
```

Model residency is not conversation state. A loaded model has weights available;
it does not recover prior messages, tool outputs, actor handoffs, or experiment
semantics. Control or the Harness caller remains authoritative for those values.

## Lifetimes

- `models.<role>.keep_alive` is request-level behavior. `0` unloads after the
  request, a bounded duration such as `"10m"` retains weights temporarily, and
  `-1` requests an indefinite provider pin.
- `model_residency.keep_alive` is the background preferred-model policy.
- `model_residency.experiment_keep_alive` is used by explicit experiment prepare
  and by every detached/tool-follow-up request in that experiment.
- Teardown sends a provider-specific release (`keep_alive = 0`) for the exact
  managed worker/model lease and verifies that it is absent afterward.

The default ordinary request lifetime is bounded (`"10m"`). The default explicit
experiment lifetime is pinned (`-1`) and released at experiment end.

## Admission and ownership

Preparation fails closed unless the worker is `AVAILABLE`, its provider inventory
is `CURRENT`, the exact model is installed, the provider advertises warm/observe/
release, and current resource facts satisfy the configured memory fraction and
available-memory checks. By default, Harness allows one distinct pinned model per
worker and refuses to warm a cold target while another model is loaded; this avoids
silent eviction and high-frequency model thrash.

A lease records whether the model was already loaded. Harness releases only a
lease it manages. Pre-existing loaded models are observed and reused but left
loaded at teardown. Repeating warm or release is safe: prepare reports reuse and
release reports already released. Concurrent Control experiments sharing the same
worker/model retain the model until the last active reference tears down.

## Evidence and failure semantics

Prepare and teardown evidence includes the exact worker/model/provider, policy
mode, keep-alive value, loaded/pre-existing state, current resource facts, remaining
loaded models, and Fabric request/record/receipt identities. Provider state and
Fabric capability observations are authoritative operational evidence; elapsed
request time alone is not proof of reuse.

Stale or missing observations, unavailable workers, resource pressure, unsupported
providers, conflicting loaded models, warm failures, and release failures are
explicit `FAIL` or `UNKNOWN` records. Cleanup uncertainty never becomes a claim
that weights were released.

## Operator commands

```bash
elh residency status --json
elh residency warm WORKER --model MODEL --json
elh residency release WORKER MODEL --json
elh experiment-readiness --profile sustained-experiment --json
```

`residency warm WORKER` without `--model` retains the older background reconcile
behavior. Exact experiment-style warm and release never fall back to another worker
or model.
