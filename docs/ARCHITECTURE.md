# Architecture

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

### `agent.py`

Runs the route plan. Each model attempt receives a role-specific system prompt and
only the tools assigned to that role. Tool calls are executed by the harness, added
back to the conversation, and bounded by `max_tool_steps`.

The agent never imports or evaluates generated Python. Tool names are resolved
against a static registry.

### `tools.py`

Implements narrow filesystem, search, Git-diff, system-information, write, and
command tools. The registry records every decision and modified path for metrics and
verification.

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

### `verifiers.py`

Checks modified artifacts independently of model confidence. The initial verifier
set handles Python, shell, JSON, and TOML. Configured unit tests can be enabled.

### `metrics.py`

Stores task fingerprints, route decisions, attempt metadata, Ollama timing/token
counters, tool outcomes, and verification status. Prompt text is disabled by
default.

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
- add a remote-node Ollama client while preserving the same policy boundary.
