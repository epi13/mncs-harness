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
workspace, verification, and escalation. Persistent Fabric owns worker
membership/presence and factual fleet state. In embedded compatibility mode
Fabric also admits placement, transports a bounded invocation bundle, and returns
worker/placement/record/receipt evidence.
The worker-local provider runtime remains responsible for model loading, CUDA,
quantization, KV cache, and actual layer movement.

## Configuration

Service-mode consumers require a Fabric public contract that advertises
`persistent_fleet_read`; the current package floor is `0.2.0a17` or newer in
the `0.2.x` line. Persistent execution and capability ingestion are selected
from public feature metadata rather than inferred from a package version. The
current contract retains provider-neutral worker capability observations,
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

Service mode primarily configures the persistent consumer endpoint:

```toml
[fabric]
enabled = true
controller_mode = "service"
controller_id = "epi13-local-harness"
service_socket = "~/.local/state/mncs-fabric/controller.sock"
service_timeout_seconds = 5.0
consumer_identity = "epi13-local-harness"
state_path = "~/.local/state/epi13-local-harness/fabric.jsonl"
fallback_to_local = true
refresh_on_startup = true
refresh_timeout_seconds = 5.0
provider_ollama_base_url = "http://127.0.0.1:11434"
provider_timeout_seconds = 600
job_timeout_overhead_seconds = 5

```

For explicit embedded or transitional compatibility, add the historical worker
tables and registry path under `controller_mode = "embedded"` or
`"transitional"`; those fields are rejected in service mode. Worker endpoint
and trust material belongs to Fabric in the persistent architecture.

```toml
[fabric]
controller_mode = "embedded"
registry_path = "~/.local/state/mncs-fabric/workers.json"

[fabric.workers.windows-gpu]
kind = "remote"
host = "operator-configured.example"
port = 9443
capabilities = ["python"]
ca_file = "/operator/path/ca.pem"
client_certificate = "/operator/path/client.pem"
client_key = "/operator/path/client.key"
trust_state = "/operator/path/trust.jsonl"
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

Startup constructs the configured Fabric consumer session. Service mode connects
and reads the shared fleet without registering, refreshing, or ingesting worker
state. When the connected controller's public service status advertises
controller-managed execution and capability observations, service mode uses
those features; otherwise inventory probes and residency warming remain explicit
unsupported states. Embedded mode performs the historical bounded refreshes.
The TUI's **Fabric** view and **Doctor** panel distinguish controller
connection, fleet availability, execution transport, capability inventory, and
generation availability. Worker-initiated rendezvous is a separate planned
Fabric feature and is never inferred from a package version.

Completed attempts show provider, worker, placement mode, precision, and Fabric
identities. They also record independent inference, workspace, and tool-execution
targets. The SQLite migration is additive and does not store prompt text by default.
Fabric's bounded execution record may retain the invocation output
needed for evidence; do not put secrets in model prompts or worker configuration.

When a model requests a tool, the remote model response returns to the local
harness. The local policy registry validates and executes the tool, and only the
next inference request may cross Fabric. Remote inference never grants a worker
access to the local workspace.

For an explicitly remote tool target, `FabricTargetToolExecutor` first applies the
same Harness command policy and approval decision. It currently accepts only Python
argv workloads through Fabric's worker-local `@python` alias. The consumer selects an
exact enrolled worker and an allowed workspace root; the adapter stages only that root
as an immutable content bundle, converts in-root absolute arguments to bundle-relative
paths, rejects parent traversal and controller Windows paths, and requires a relative
Python entry point to resolve inside the selected bundle. It then calls
`FabricClient.execute_target` with
the Harness consumer context and an identity-addressed record of the policy decision.

The adapter reads only public fleet and capability records. It does not receive worker
addresses, TLS material, registry paths, rendezvous sessions, or remote staging paths.
Fabric rechecks membership, presence, freshness, and runtime compatibility and returns
target admission and execution evidence. A denial, disconnect, or malformed result is
returned as a failed tool call; there is no controller-local or alternate-worker
fallback. The authorization identity is Harness provenance, not Fabric proof that a
semantic tool request was permitted. Automatic model-directed target choice and result
material import remain future policy work.

The immutable bundle and argv checks are an authority boundary, not an operating-system
sandbox. An approved Python program still runs with the Fabric worker service account's
ordinary filesystem permissions. Use only trusted workspace programs until worker-side
Landlock, bubblewrap, or an equivalent containment profile is implemented.

In `transitional` mode, persistent Fabric remains authoritative for membership,
presence, and fleet identity. Bounded execution and worker-local observations
come from the explicitly labeled embedded compatibility client and are reported
as `embedded-compatibility`; they do not overwrite persistent lifecycle facts.

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
# Persistent Fabric consumer boundary

Fabric is persistent infrastructure owned by `mncs-fabric-controller.service`.
Local Harness is an ordinary `FabricClient` consumer and never uses
`FabricAdminClient`. Service mode reads the shared consumer AF_UNIX socket and
closes only its own connection; Harness shutdown is not worker disconnect and
does not stop Fabric.

```toml
[fabric]
enabled = true
controller_mode = "service"
service_socket = "~/.local/state/mncs-fabric/controller.sock"
service_timeout_seconds = 5.0
consumer_identity = "epi13-local-harness"
```

`embedded` is an explicit compatibility mode that retains the historical
worker/registry setup. `transitional` remains an explicit compatibility mode for an
older Fabric contract and is clearly labeled in diagnostics/results.

Service mode reports the controller, fleet, execution, and inventory states
independently. A connected controller does not imply executable inference:
the static package contract is only a compatibility ceiling. Harness derives
inference dispatch, inventory, residency, and capability-ingestion support from the
live service projection and fails closed with `FABRIC_SERVICE_EXECUTION_UNSUPPORTED`
when a connected service does not advertise execution. A configured backend or
rendezvous worker can advertise those features without changing the Harness
routing/session boundary.
