# Distributed capability and session foundation

The harness now turns its existing bounded worker-local Ollama inventory probe into
Fabric-owned capability truth:

```text
enrolled worker -> loopback Ollama /api/tags -> bounded Fabric job
                -> normalized model/runtime entries
                -> FabricClient.ingest_capability_observation()
                -> Fabric freshness/liveness evaluation
                -> harness routing, status, capability graph, and TUI
```

Embedded MNCS Fabric `0.2.0a15` or newer in the `0.2.x` line provides
`mncs-fabric.worker-capability-observation.v0.1`. Fabric validates and retains these
generic facts but does not import Ollama, classify a model, authorize a tool, or pick
the semantically best model. The harness owns deterministic exact/fallback selection
and chooses only a specific currently available worker that reported the selected
model.

The persistent Fabric consumer service negotiates execution and capability
observation support from the connected controller's public status projection.
When those features are advertised, service mode dispatches the bounded probe
through `FabricClient.connect()` and ingests the resulting observation through
the same consumer boundary. If they are not advertised, service mode reports
capability inventory as unavailable and does not run worker-local probes.
Explicit `embedded` mode retains the complete compatibility path; explicit
`transitional` mode keeps persistent Fabric as fleet authority while using
embedded execution compatibility. Worker-initiated rendezvous remains a
separate planned Fabric feature.

Inventory states are fail-closed. `CURRENT` may be routed; `STALE`, `UNKNOWN`, and
`UNAVAILABLE` may be displayed with retained evidence but are not treated as model
presence. A failed fresh probe publishes an empty unavailable observation so a prior
successful scan cannot silently remain usable. `MODEL_NOT_INSTALLED`,
`WORKER_UNAVAILABLE`, `FABRIC_UNAVAILABLE`, and no-known-placement outcomes remain
distinguishable in model-selection metadata.

Older/custom mock Fabric clients without the capability API use the prior private
in-memory inventory compatibility path. Fabric-disabled and local-Ollama-only modes
remain independent of the optional package. Released installations should use the
new dependency floor; the fallback exists for tests and staged upgrades, not as a
claim that stale observations are current.

## Independent session targets

Each attempt carries typed inference, workspace, and tool-execution targets. Defaults
are controller-local workspace and controller-local tools. Remote inference changes
only the inference target:

```text
inference = fabric-worker:worker-01-windows
workspace = controller
tools     = controller
```

The provider receives the selected worker identity, while the existing `ToolRegistry`
continues to resolve paths and execute approved tools on the controller. No remote
filesystem, shell, SSH, workspace, or MCP authority follows from model placement.
General remote-tool dispatch is intentionally not implemented.

The harness-side capability graph is a deterministic view assembled from configured
controller workspace/tools plus current Fabric capability/resource observations. The
Fabric TUI table shows inventory state and counts by observed capability kind. It does
not infer skills from model names or create a graph database.

## Forge inspection note

MNCS Forge project inspection was attempted for planning and evidence review. The
available Forge instance was configured for the separate
`machine-native-complexity-standard` checkout, not either repository in this change,
and exposed no safe arbitrary-repository verifier/candidate scope. Repository-native
static analysis, unit tests, pytest, Ruff, compile checks, and Git review therefore
remain the authoritative evidence for this iteration. A useful future Forge addition
would be an explicit multi-repository workspace descriptor plus cross-repository
contract/version verification without broad arbitrary-command authority.
