# MNCS Harness

MNCS Harness is a policy-aware operator harness for routing work across
configured models, executing narrowly scoped tools, and accepting or escalating
results for a particular deployment.

It is part of the MNCS **project family**. It is **not** a normative requirement
of the [Machine-Native Complexity Standard](https://github.com/epi13/machine-native-complexity-standard).

The authority boundary is:

- **MNCS Control MCP** constrains where remote/operator actions may occur.
- **MNCS Harness** decides how model/tool work is routed, governed, approved, and accepted.
- **MNCS Fabric** executes approved work on enrolled workers.
- **MNCS Commons** remembers durable structured knowledge and coordination state.
- **MNCS Forge** evaluates development workflows, evidence, experiments, and gaps.

See [docs/IDENTITY.md](docs/IDENTITY.md) for the rename from
`epi13-local-harness` and the compatibility surfaces that remain.

```text
request
  -> deterministic safety and modality preflight
  -> optional configured semantic-router lanes
  -> a small configured model for read-only work
  -> a configured ordinary-work model
  -> an optional coding specialist
  -> a larger configured model for ambiguity, review, and difficult multi-step work
  -> deterministic verifiers decide whether the result passes
```

Bundled model tags are a **reference profile**. A representative controller
routes an approved workload to an enrolled worker that has a configured resident
model. Operators replace the profile with their own fleet.

The models propose actions. The harness owns permissions, executes tools,
records metrics, and decides whether a result is acceptable.

## Current status

MNCS Harness is a Fabric consumer/router. In the intended
`fabric.controller_mode = "service"` configuration it connects to an already
running persistent Fabric controller and reads its fleet. It does not start
Fabric, register workers, load the endpoint registry, or own worker presence.

When the connected controller advertises controller-managed execution and
observations, service mode can use the persistent consumer path. Otherwise
generation and worker-local inventory remain unavailable unless the operator
explicitly selects `transitional` compatibility. `embedded` remains available
for isolated tests and deployments.

This repository contains a functional alpha rather than only an architecture
sketch. It includes deterministic routing, an optional semantic prompt router, a
dependency-free Ollama HTTP client, a multi-turn tool-calling loop, workspace
confinement, interactive approval, a Textual TUI, verification helpers, SQLite
metrics, and unit tests.

## Requirements

- Linux or another POSIX-like system, with Windows worker support through Fabric
- Python 3.11 or newer
- an Ollama endpoint, typically `http://127.0.0.1:11434` on the worker
- enough disk space for the selected models

## Install

```bash
git clone https://github.com/epi13/mncs-harness.git
cd mncs-harness
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

For the complete distributed development setup from sibling checkouts:

```bash
python -m pip install -e '../mncs-fabric'
python -m pip install -e '../MNCS-Commons'
python -m pip install -e '.[distributed]'
```

Create a user configuration:

```bash
mncs-harness init
mncs-harness install-cli
```

`elh` remains a compatibility command. `install-cli` rewrites the virtualenv
launchers so they exec the interpreter next to the script. That keeps the CLI
working inside MNCS Control, where the workspace is mounted at `/workspace` and
the host-absolute shebang from `pip install` is not visible.

The default file is written to:

```text
$HOME/.config/mncs-harness/config.toml
```

If that file does not exist, an existing
`$HOME/.config/epi13-local-harness/config.toml` is still loaded.

## Deterministic routing

The harness is offline by default. Routing uses explicit operator pins, task
characteristics, configured roles, and the current Fabric worker/model
inventory. Use `--worker` and `--model-name` together for an exact route.
`--allow-fallback` is required before a failed manual route may use the
configured fallback policy.

## Terminal interface

```bash
cd /path/to/mncs-harness
source .venv/bin/activate
mncs-harness-tui --workspace .
```

`elh-tui` remains supported. Auto-approval is disabled by default. See
[Terminal UI](docs/TUI.md) for controls.

## Pull the reference profile

```bash
mncs-harness pull --role e2b
mncs-harness pull --role e4b
mncs-harness pull --role coder
mncs-harness pull --role reviewer
```

Bundled role tags are operator preferences, not architectural requirements.
Automatic placement uses Fabric inventory, provider claims, observed evidence,
and policy. See [docs/MODEL_AGNOSTICISM.md](docs/MODEL_AGNOSTICISM.md).

## Confirm the environment

```bash
mncs-harness doctor
mncs-harness models
```

## Preview routing without running a worker model

```bash
mncs-harness route "Explain what systemctl status ollama means"
mncs-harness route "Repair the Python tests in this repository"
```

## Ask the local agent

```bash
mncs-harness ask "Inspect this repository and explain its package structure" --workspace .
mncs-harness ask "Add input validation to the parser and run the tests" --workspace .
```

The harness prompts before model-requested writes or commands. `--yes`
automatically approves actions that already pass workspace and command policy;
it does not bypass blocked actions.

## Development

```bash
python -m unittest discover -s tests -v
python -m pip install -e '.[dev]'
ruff check .
pytest
```

## License

Apache License 2.0.
