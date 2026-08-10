# Controller-light routing

`controller-light` keeps orchestration on the controller and response-generation inference on Fabric workers.

```bash
elh-fabric controller-light
```

The profile makes four deliberate changes to the active harness configuration:

1. enables the pinned LiquidAI semantic router on CPU;
2. routes every generation role through Fabric;
3. requests CUDA placement for generation roles; and
4. disables automatic fallback to the controller's local Ollama runtime.

If the semantic-router dependencies are unavailable, deterministic routing remains the bounded fallback. The profile never silently replaces routing with a large local generation model.

## Two-stage routing

The runtime separates two decisions:

```text
prompt
  -> local semantic router / deterministic fallback
  -> role: e2b | e4b | coder | reviewer
  -> live Fabric worker Ollama inventory
  -> installed model for that role
  -> Fabric placement / execution
```

The semantic router does not generate the answer. It chooses a lane/role. A generation model still performs the task, but under the controller-light profile that model runs through Fabric rather than being loaded on the controller.

## Live worker inventory

At Fabric session startup, the harness sends a bounded Python execution bundle to each available remote worker. That bundle queries only the worker-local Ollama endpoint:

```text
http://127.0.0.1:11434/api/tags
```

The returned inventory is attached to `elh-fabric status` as `model_names` and `model_inventory`.

This runtime inventory path uses Fabric mTLS. It does not require SSH credentials and it does not expose Ollama on the LAN.

## Model selection

For each role, the configured model tag remains the first choice when it is installed. If that tag is missing, the current deterministic inventory fallback is:

- `e2b`: smallest installed model;
- `coder`: code-hinted installed model, preferring the largest one at or below 10 GiB;
- `reviewer`: largest installed non-code-specialist model, then largest installed model;
- other/general roles: median-size installed model.

The selected tag and the reason are recorded in attempt metrics as `configured_model`, `selected_model`, `model_selection_reason`, and `model_selection_source=worker-inventory`.

This is availability-aware routing, not a capability proof. Model presence does not prove quality, tool-use reliability, GPU residency, or suitability for a role.

## Multiple workers

Until Fabric carries model availability as a first-class scheduling capability, automatic inventory fallback uses only models present on every currently available remote worker. This avoids selecting a model and then allowing Fabric to schedule the request onto a worker that does not have that model installed.

## Fabric version

This feature requires `mncs-fabric >= 0.2.0a9`. Fabric `0.2.0a8` exposes the execution-bundle archive API but has a transferred-bundle dispatch-binding bug that can prevent CUDA and model-inventory probes from executing.
