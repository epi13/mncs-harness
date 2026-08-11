# MNCS Fabric integration

Fabric is optional. With the bundled configuration, the harness remains local
Ollama-only because `[fabric].enabled = false`. Enabling Fabric makes configured
model roles use the Fabric provider when possible; `fallback_to_local` controls
whether a provider or placement failure may return to local Ollama.

```text
                 epi13-local-harness
                         |
                  task/semantic router
                         |
                      model role
                         |
                narrow inference provider
                    /                 \
                   /                   \
          local Ollama          Fabric-backed Ollama
                                      |
                              PlacementRequest
                                      |
                                MNCS Fabric
                           /       |        \
                        local    Fedora    Windows
                         CPU      worker     GPU
                                      |
                              worker-local runtime
                                      |
                                model response
                                      |
                            local tools/verifier
                                      |
                                  escalation
```

The harness decides the task, semantic lane, model role, tools, permissions,
workspace, verification, and escalation. Fabric registers configured workers,
refreshes worker/resource observations, admits eligible placement, transports a
bounded invocation bundle, and returns worker/placement/record/receipt evidence.
The worker-local provider runtime remains responsible for model loading, CUDA,
quantization, KV cache, and actual layer movement.

## Configuration

The Fabric-backed inference provider requires MNCS Fabric `0.2.0a13` or newer in
the `0.2.x` line. That version retains provider-neutral worker capability observations,
ensures each explicitly supplied request bundle is staged after placement, and
separates short control waits from job-bounded execution responses. Install from a
released package:

```bash
python -m pip install -e '.[fabric]'
```

For sibling development checkouts, install Fabric first and then the harness:

```bash
python -m pip install -e ../mncs-fabric
python -m pip install -e .
```

Add only operator-owned worker entries to the user configuration. Paths are
deliberately not populated in the bundled defaults:

```toml
[fabric]
enabled = true
controller_id = "epi13-local-harness"
state_path = "~/.local/state/epi13-local-harness/fabric.jsonl"
fallback_to_local = true
refresh_on_startup = true
refresh_timeout_seconds = 5.0
provider_ollama_base_url = "http://127.0.0.1:11434"
provider_timeout_seconds = 600
job_timeout_overhead_seconds = 5

[fabric.workers.local]
kind = "local"
state_path = "~/.local/state/epi13-local-harness/fabric-local.jsonl"
bundle_root = "~/.local/state/epi13-local-harness/fabric-worker-bundle"
capabilities = ["python"]

[fabric.workers.windows-gpu]
kind = "remote"
host = "operator-configured.example"
port = 9443
state_path = "~/.local/state/epi13-local-harness/fabric-windows.jsonl"
capabilities = ["python"]
ca_file = "/operator/path/ca.pem"
client_certificate = "/operator/path/client.pem"
client_key = "/operator/path/client.key"
trust_state = "/operator/path/trust.jsonl"
timeout_seconds = 5.0
connect_timeout_seconds = 5.0
control_timeout_seconds = 5.0
execution_timeout_overhead_seconds = 5.0
```

The provider timeout bounds worker-local Ollama. The Fabric job adds only
`job_timeout_overhead_seconds` for process completion. The network transport then
adds the worker's small execution-response overhead. Connection, refresh, TLS,
control, and idle waits remain governed by their short bounds; they are not widened
to the model timeout.

Remote workers must already be enrolled persistent/bounded Fabric services.
The TUI does not scan the LAN, start SSH sessions, launch arbitrary remote
processes, or expose a network Ollama listener. Missing certificates, keys, or
trust state produce an unavailable Fabric status; local fallback remains
possible when configured.

Model placement intent is configured per `[models.<role>]` entry and mapped to
Fabric's provider-neutral `PlacementRequest`:

```toml
[models.reviewer]
provider = "fabric"
execution_device = "accelerator"
accelerator_backend = "cuda"
offload = "sequential-cpu"
precision = "float16"
model_storage_bytes = 12000000000
estimated_workspace_bytes = 2000000000
minimum_host_memory_bytes = 16000000000
gpu_reserve_bytes = 1073741824
minimum_accelerator_working_bytes = 1000000000
runtime_supports_sequential_cpu_offload = true
required_capabilities = ["python"]
```

These are requirements, not hardware facts. Fabric may return `UNKNOWN` when
resource observations are stale, absent, or unverified. Sequential CPU offload
means the exact worker runtime has evidence for that capability and sufficient
resources; the provider runtime performs the layer movement. The harness never
reports that Fabric moved model layers.

## Startup, diagnostics, and fallback

Startup constructs the Fabric consumer session, registers explicit workers, and
performs bounded refreshes when enabled. The TUI's **Fabric** view and **Doctor**
panel distinguish disabled, unavailable/misconfigured, available-but-no-eligible
worker, stale/unknown capability or resource observations, and provider-runtime
failure. The bounded `/api/tags` result is normalized into Fabric's generic model and
runtime entries, ingested through `FabricClient`, and read back through
`FabricClient.workers()` for routing and status.

Completed attempts show provider, worker, placement mode, precision, and Fabric
identities. They also record independent inference, workspace, and tool-execution
targets. The SQLite migration is additive and does not store prompt text by default.
Fabric's bounded execution record may retain the invocation output
needed for evidence; do not put secrets in model prompts or worker configuration.

When a model requests a tool, the remote model response returns to the local
harness. The local policy registry validates and executes the tool, and only the
next inference request may cross Fabric. Remote inference never grants a worker
access to the local workspace.

See [DISTRIBUTED_CAPABILITY_FOUNDATION.md](DISTRIBUTED_CAPABILITY_FOUNDATION.md)
for inventory states, compatibility behavior, the capability graph, and target
separation.
See [COMMONS.md](COMMONS.md) for controller-local knowledge tools and evidence
translation.

## Tests and live smoke tests

The normal suite deliberately remains runnable without the optional Fabric
package. Fabric-only integration coverage is skipped when the dependency is not
installed, while disabled/unavailable behavior is still tested:

```bash
python -m unittest discover -s tests -v
```

With a sibling Fabric checkout installed, run the complete integration coverage:

```bash
PYTHONPATH=src:/path/to/mncs-fabric/src python -m pytest
```

The in-process test proves bounded bundle execution, loopback provider
invocation, placement admission, and returned Fabric evidence. A live test is
optional and must use already enrolled operator-configured workers and their
worker-local provider runtime. It must not change firewall, SSH trust, TLS,
drivers, or OS configuration. Report local/in-process and remote physical
evidence separately; no physical GPU or offload claim is made by the ordinary
suite.
