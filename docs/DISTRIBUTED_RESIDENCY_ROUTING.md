# Distributed residency and manual routing

Local Harness 0.6 consumes the Fabric operator registry, observes each worker's
installed and loaded Ollama models, selects at most one preferred resident
generation model per worker, and keeps semantic selection and operator policy
above Fabric.

```toml
[controller]
generation_policy = "router-only"

[fabric]
enabled = true
registry_path = "~/.local/state/mncs-fabric/workers.json"

[model_residency]
enabled = true
warm_on_startup = true
prefer_resident_for_auto_routing = true
keep_alive = -1
warm_timeout_seconds = 300
maximum_model_memory_fraction = 0.5

[model_residency.workers.collamore02-windows]
model = "gemma4:e4b"
```

An explicit assignment wins. Otherwise Harness chooses only among its configured
role preferences that a current worker inventory actually reports and requires
bounded model-size and host-memory facts before warming. Ollama `/api/ps` is the
loaded-state observation; `/api/generate` with an empty prompt and configured
`keep_alive` is the provider-specific warm operation. Fabric merely transports
the bounded loopback probe/job and records evidence.

Inventory refresh also asks Ollama `/api/show` for each installed model's
capabilities. Those capabilities are identity-bound worker observations, not
attestation or a semantic quality guarantee. They are used to avoid sending
unsupported request options to a pinned model: for example, a role configured
with `think = true` is sent with thinking disabled when the selected model does
not advertise the `thinking` capability. Unknown capability data preserves the
configured option, while an exact worker/model pin still fails closed if the
worker or model is unavailable.

Harness also re-observes and reconciles residency after a completed remote
attempt. This matters on constrained accelerators where a semantically selected
transient model can evict the preferred resident model; the node is returned to
its declared steady state before the operator receives the result. A failed
reconciliation is recorded as `UNKNOWN` operational evidence and does not rewrite
the inference outcome.

Automatic routing uses warm state only after semantic suitability, current
capability availability, and operator policy. Manual routes are typed:

- `--role ROLE`
- `--model-name TAG`
- `--worker ID`
- `--worker ID --model-name TAG`

Exact pins fail closed. `--allow-fallback` is the only way to permit automatic
fallback. Legacy `--model ROLE` remains an alias for `--role`.

Operator inspection uses one fleet view:

```bash
elh fabric workers --json
elh fabric refresh
elh models [--worker ID]
elh residency status
elh residency warm ID
elh route --worker ID --model-name TAG "task"
```

`elh pull` refuses Fabric-backed roles so a remote preference cannot accidentally
pull a heavyweight generation model onto the controller. In `router-only` mode,
provider failure cannot silently become controller-local generation.

Commons operator access uses the existing controller MCP session:

```bash
elh commons status
elh commons work
elh commons query --kind WorkRequest
elh commons get DIGEST
elh commons conversation DIGEST
elh commons evidence DIGEST
```

The TUI exposes the same per-worker inventory, resident state, route override,
and Commons browser. Commons content is always labelled untrusted and rendered as
data; it is never converted into a tool invocation.

Compatibility floor: `mncs-fabric>=0.2.0a13,<0.3` and
`mncs-commons[mcp]>=0.5.0.dev1,<0.6`. These package versions are separate from
Fabric's wire protocol and Commons' record, exchange, and node profiles.
