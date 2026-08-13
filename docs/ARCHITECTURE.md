# Architecture

Local Harness is a Fabric consumer/router. `service` connects to the existing
consumer socket; `embedded` is explicit compatibility ownership for isolated
deployments; `transitional` combines persistent fleet authority with a separate
embedded execution path. The latter is temporary and every status projection
keeps fleet authority and execution/inventory transport distinct.

The unified fleet view combines controller policy, the Fabric registry, current
worker/resource/capability observations, installed/loaded provider models, and
Harness-owned resident assignments. Runtime routing, Doctor, Models, CLI, and TUI
consume that view rather than recomputing a controller-centric inventory.

## Design objective

The harness is a local orchestration layer, not another foundation model. Its job is
to apply the smallest reasonable model to a task, expose only the tools needed by
that tier, verify observable work, and escalate when evidence says the attempt was
not sufficient.

```text
CLI request
   |
   v
TaskProfiler -------- image / code / edit / execution / risk / complexity signals
   |
   v
DeterministicRouter
   |
   +---- E2B: read-only inspection and explanation
   |
   +---- E4B: primary workspace worker
   |
   +---- Qwen3 8B: optional independent coding specialist
   |
   `---- 12B: difficult, ambiguous, multimodal, or high-risk review
             |
             v
       inference provider boundary
          /               \\
         /                 \\
  local Ollama       MNCS Fabric placement
                              |
                       worker-local Ollama
             |
             v
      bounded tool-call loop
             |
             v
 filesystem guard + command policy + user approval
             |
             v
 deterministic verifiers
             |
      pass --+-- fail -> next configured role
             |
             v
      answer + SQLite metrics
```

## Distributed execution semantics

A distributed session must not assume that the machine performing inference is the
machine that owns the workspace or executes a requested tool. The harness therefore
treats three locations as independent routing dimensions:

- **inference location** — the worker/runtime that hosts the selected model;
- **workspace location** — the authority that owns the files or state being changed;
- **tool execution location** — the enrolled host on which an approved command or
  worker-local tool actually runs.

For example, a large model on a Windows GPU worker may reason about a repository on
the Fedora controller. Its filesystem or test request returns through the harness,
where the existing policy registry validates and executes the operation against the
Fedora workspace. The remote model receives only the bounded tool result on the next
inference turn; it does not receive a direct SSH session or ambient access to the
controller filesystem.

```text
Fedora controller/workspace                 Windows GPU worker
---------------------------                 ------------------
LocalAgent / policy
        |
        +------ Fabric inference ----------> model
        |                                      |
        |<--------- tool request --------------+
        |
 guarded local or targeted tool execution
        |
        +--------- bounded result ----------> model
```

Some tools may later target another enrolled Fabric worker because the resource is
inherently remote. That path remains an explicit, policy-approved execution target,
not an unrestricted shell. Model placement and tool placement are evaluated
separately.

Worker observations may contribute a provider-neutral capability graph covering
models, runtimes, tools, MCP endpoints, hardware, workspaces, and current resource
state. Fabric advertises and transports facts; the harness remains responsible for
semantic task decomposition, model suitability, tool permissions, verification,
reduction, and escalation.

The implemented `SessionTargets` value records these three locations independently on
every attempt. `capability_graph.py` assembles a deterministic inspection view from
current Fabric observations plus configured controller facts. Tools remain
controller-local by default. `FabricTargetToolExecutor` is the explicit remote path:
Harness selects one worker, applies its existing command policy and approval, converts
the chosen workspace material into an immutable bundle, and invokes Fabric's exact
target API with no fallback.

Controller-hosted MNCS MCPs should normally be exposed to remote models by tool
schema and proxied invocation through the harness. Worker-local MCPs are reserved for
capabilities inherently attached to that worker, such as local hardware or an
application instance. A remote model does not need a duplicate installation of every
controller MCP merely to call it.

MNCS Commons is the first implemented controller MCP. In the default `service` mode,
`CommonsSession` connects to the persistent Commons consumer socket, validates the
exact local-agent descriptor and read-only tool set, and registers those schemas with
`ToolRegistry`. It never opens the store directly and closing a harness client does
not stop Commons. Persistent publication has a dedicated opt-in policy, uses the
separate operator socket, and still requires ordinary approval. Fabric evidence
publication is a separate controller action and is conservative by default. Explicit
`stdio` mode remains a compatibility path. The capability graph therefore places
Commons under `controller.mcp`; it is never copied into a Fabric worker observation.

Shell access follows the same authority boundary. The preferred primitive remains a
guarded executable plus argv. Bash or PowerShell script tools may be added where they
provide real value, but script content must pass policy inspection and approval before
execution. Distributed operation must not convert a model placement decision into
host shell authority.

## Modules

### `router.py`

Builds a `TaskProfile` from explicit, inspectable features. The first implementation
uses deterministic rules because they are cheap, debuggable, and measurable. A
learned router can be added later without changing the agent or policy interfaces.

### `ollama.py`

A dependency-free HTTP client for Ollama's native API. It supports:

- server and model discovery;
- non-streaming chat;
- native tool schemas and tool calls;
- thinking controls;
- per-role context and sampling options;
- per-request `keep_alive` values;
- base64 image inputs.

### `provider.py` and `fabric.py`

The provider boundary preserves the Ollama-shaped chat response while allowing
configured roles to use a Fabric-backed bounded invocation. Fabric owns worker
state, resource eligibility, transport, and evidence. The local harness still
owns the tool loop, workspace, policy, verification, and escalation. See
[FABRIC.md](FABRIC.md) for configuration and the security boundary.

The distributed capability/session layer extends this boundary without changing its
authority model. The bounded worker-local model probe publishes generic entries into
Fabric's identity-bound capability observation API. The harness accepts only current
observations, selects a model and exact reporting worker, and separately records the
controller workspace and tool target. Inference and tool workers can differ while
workspace authority stays on the controller.

### `agent.py`

Runs the route plan. Each model attempt receives a role-specific system prompt and
only the tools assigned to that role. Tool calls are executed by the harness, added
back to the conversation, and bounded by `max_tool_steps`.

The agent never imports or evaluates generated Python. Tool names are resolved
against a static registry.

Future task planning may decompose clearly independent work into bounded sub-tasks
and represent dependencies as an explicit DAG. Fabric can execute those nodes on
eligible workers, but the harness owns decomposition semantics, reduction, review,
verification, and escalation.

### `tools.py`

Implements narrow filesystem, search, Git-diff, system-information, write, and
command tools. The registry records every decision and modified path for metrics and
verification.

Tool requests carry an attempt-level target that is controller-local by default.
The explicit remote Python adapter corresponds to an enrolled Fabric worker and a
fresh factual runtime observation, then reuses the same policy and approval checks.
It is bounded argv execution, not a general remote shell, and never falls back locally.

### `policy.py`

Separates model intent from execution authority. It provides:

- canonical workspace path resolution;
- symlink and `..` escape rejection;
- hidden-path controls;
- executable allowlisting;
- hard blocks for privilege, destructive, package-management, network, and service
  operations;
- special restrictions for Git, Python, Bash, and POSIX shell invocations;
- approval requirements for writes and commands.

Distributed execution must preserve these controls. A remote model or remote worker
never becomes an authorization source merely because Fabric admitted its placement.

### `verifiers.py`

Checks modified artifacts independently of model confidence. The initial verifier
set handles Python, shell, JSON, and TOML. Configured unit tests can be enabled.

### `metrics.py`

Stores task fingerprints, route decisions, attempt metadata, Ollama timing/token
counters, tool outcomes, and verification status. Prompt text is disabled by
default.

Distributed attempts record inference worker, workspace authority, and tool execution
target separately in attempt metadata and SQLite metrics. Provider/runtime and Fabric
evidence remain separate fields so performance and correctness are not conflated.

## Model residency

The defaults intentionally avoid holding the complete cascade in memory:

- E2B and E4B use a ten-minute `keep_alive` for interactive reuse;
- Qwen3 and 12B use `keep_alive = "0"` so Ollama can unload them immediately;
- each role uses a practical context size rather than the model's architectural
  maximum.

On a 32 GB CPU-first workstation, the operating system, context cache, and active
applications matter as much as model download size. The correct values should be
selected from measured local latency and memory behavior.

## Extension points

The core interfaces are deliberately small:

- add a role under `[models.<role>]`;
- add deterministic routing features in `profile_task`;
- add a tool definition to `ToolRegistry`;
- add a verifier keyed to file type or project metadata;
- add evaluation cases before changing routing thresholds;
- add model/runtime inventory and execution-target resolution while preserving the
  existing policy boundary;
- add controller-proxied or worker-local MCP capabilities without making MCP presence
  an authorization signal; and
- add task-DAG planning only after single-task distributed sessions are measurable and
  reliable.
# Fabric ownership

The persistent Fabric controller owns durable lifecycle, fleet membership,
worker presence, and worker facts. Local Harness owns semantic model choice,
residency preference, task decomposition, verification, escalation, and agent
policy. The split is factual fleet observation below and routing policy above.

Harness has three explicit Fabric connection modes:

* `service`: ordinary consumer reads from the existing controller service;
* `embedded`: isolated compatibility controller/client with configured workers;
* `transitional`: persistent fleet authority plus an explicitly bounded embedded
  direct-execution compatibility path.

There is no automatic service-to-embedded fallback. Missing service sockets are
reported as `FABRIC_CONTROLLER_UNAVAILABLE`, and unsupported service execution
as `FABRIC_SERVICE_EXECUTION_UNSUPPORTED`.
