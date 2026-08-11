# Controller-light routing

`controller-light` keeps orchestration on the controller and response-generation inference on Fabric workers.

```bash
elh-fabric controller-light
```

The profile makes four deliberate changes to the active harness configuration:

1. enables the pinned LiquidAI semantic router on CPU;
2. routes every generation role through Fabric;
3. places the small Fabric provider-call bundle on a remote worker as a normal CPU job; and
4. disables automatic fallback to the controller's local Ollama runtime.

Worker-local Ollama, not the Fabric Python runner, owns model loading, GPU residency, quantization, and CPU/GPU split. If the semantic-router dependencies are unavailable, deterministic routing remains the bounded fallback. The profile never silently replaces routing with a large local generation model.

## Two-stage routing

The runtime separates role selection from model selection:

```text
prompt
  -> local semantic router / deterministic fallback
  -> role: e2b | e4b | coder | reviewer
  -> live Fabric worker Ollama inventory
  -> installed model for that role
  -> remote Fabric provider-call bundle
  -> worker-local Ollama
  -> provider-selected GPU/CPU execution
```

The semantic router does not generate the answer. It chooses a lane/role. A generation model still performs the task, but under the controller-light profile that model is invoked through the remote worker rather than being loaded on the controller.

## Fabric placement versus provider placement

The Fabric execution bundle is only a small HTTP client. It calls worker-local Ollama on loopback. Requiring that Python process itself to pass CUDA placement would conflate two different authority boundaries and could cause a remote-capable request to fall back locally merely because the worker Python runtime lacks fresh CUDA execution evidence.

Controller-light therefore requests CPU placement for the provider-call bundle. Ollama remains responsible for how the selected model is loaded and executed on the worker's hardware.

`cuda_ready_count` remains useful evidence about the worker Python runtime, but it does not gate worker-local Ollama inference. A future provider-specific observation can record Ollama's actual GPU/CPU model split without pretending the Python CUDA probe proves Ollama placement.

## Live worker inventory

At Fabric session startup, the harness sends a bounded Python execution bundle to each available remote worker. That bundle queries only the worker-local Ollama endpoint:

```text
http://127.0.0.1:11434/api/tags
```

The returned inventory is normalized into provider-neutral model/runtime entries,
ingested as a Fabric worker capability observation, and then exposed by
`FabricClient.workers()` as `model_names` and `model_inventory`. Status also reports
`ollama_inventory_ready_count` so current model availability can be distinguished
from Python CUDA evidence.

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

The harness evaluates only `CURRENT` inventories, resolves an exact or configured
compatible fallback model, and targets a specific available worker that actually
reported that model. Stale, unknown, unavailable, or failed observations are never
promoted to presence.

## Fabric version

This feature requires `mncs-fabric >= 0.2.0a11`, which supplies the public capability
observation API and request-scoped bundle-cache correction. Custom/legacy mock clients
without that API retain the old session-local inventory behavior for compatibility.
