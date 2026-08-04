# epi13-local-harness

A local, policy-aware AI harness for routing work across multiple Ollama models,
executing narrowly scoped tools, and escalating only when deterministic checks say
that a smaller model was not enough.

The default cascade is intentionally sized for Alexander's Fedora workstation:

```text
request
  -> deterministic task profile
  -> Gemma 4 E2B for small read-only work
  -> Gemma 4 E4B for ordinary tool use and coding
  -> Qwen3 8B as an optional coding specialist
  -> Gemma 4 12B for ambiguity, review, and difficult multi-step work
  -> deterministic verifiers decide whether the result passes
```

The models propose actions. The harness owns permissions, executes tools, records
metrics, and decides whether a result is acceptable.

## Current status

This repository contains a functional alpha rather than only an architecture sketch.
It includes:

- deterministic routing and escalation plans;
- a dependency-free Ollama HTTP client;
- a multi-turn tool-calling loop;
- workspace confinement and command policy checks;
- interactive approval for writes and command execution;
- a Textual terminal UI for chat, routing previews, diagnostics, and metrics;
- Python, shell, JSON, and TOML verification;
- SQLite run and tool-call metrics;
- model health checks and configured-model pulling;
- an evaluation runner for routing experiments;
- unit tests and GitHub Actions CI.

## Requirements

- Fedora or another Linux distribution;
- Python 3.11 or newer;
- Ollama running on `http://127.0.0.1:11434`;
- enough disk space for the selected models.

Install and start Ollama first:

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama
```

## Install the harness

```bash
git clone https://github.com/epi13/epi13-local-harness.git
cd epi13-local-harness
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Create a user configuration:

```bash
elh init
```

The default file is written to:

```text
~/.config/epi13-local-harness/config.toml
```

## Terminal interface

Launch the full-screen terminal interface from any workspace:

```bash
cd ~/epi13-local-harness
source .venv/bin/activate
elh-tui --workspace .
```

The TUI provides:

- automatic or forced model-role selection;
- workspace and image inputs;
- route previews without invoking a worker model;
- local-model chat through the existing `LocalAgent`;
- Ollama diagnostics and model status;
- recent metrics;
- visible guarded auto-approval for policy-allowed writes and commands.

Auto-approval is disabled by default. With it disabled, writes and commands are denied
rather than opening a competing stdin prompt beneath the TUI. Enabling it does not
bypass blocked-command or workspace policy.

See [Terminal UI](docs/TUI.md) for controls and keyboard shortcuts.

## Pull the model cascade

```bash
elh pull --role e2b
elh pull --role e4b
elh pull --role coder
elh pull --role reviewer
```

The default tags are:

```text
gemma4:e2b
gemma4:e4b
qwen3:8b
gemma4:12b
```

All model names and runtime settings are configurable. The harness never assumes
that every model should remain loaded at once.

## Confirm the environment

```bash
elh doctor
elh models
```

`doctor` checks the Ollama server, installed model tags, optional verification tools,
and the writable metrics location.

## Preview routing without running a model

```bash
elh route "Explain what systemctl status ollama means"
elh route "Repair the Python tests in this repository"
elh route "Delete and reinstall the system service as root"
```

Routing is deterministic and inspectable. A model is not spent merely to decide
which other model should answer.

## Ask the local agent

Read-only task:

```bash
elh ask "Inspect this repository and explain its package structure" --workspace .
```

Tool-driven coding task:

```bash
elh ask "Add input validation to the parser and run the tests" --workspace .
```

The harness prompts before model-requested writes or commands. `--yes` automatically
approves actions that already pass the workspace and command policy; it does not
bypass blocked actions.

Force a particular configured role:

```bash
elh ask "Review this implementation" --model reviewer --workspace .
```

Attach an image for a multimodal Gemma model:

```bash
elh ask "Explain this terminal screenshot" --image screenshot.png
```

## Verify files directly

```bash
elh verify src tests
```

Known checks include:

- `.py`: `py_compile`;
- `.sh` and Bash scripts: `bash -n`, plus `shellcheck` when installed;
- `.json`: standard-library JSON parsing;
- `.toml`: standard-library TOML parsing.

## Run routing evaluations

```bash
elh eval
```

The included cases verify expected first-choice routes. Add project-specific examples
to `evals/tasks.jsonl` and use the results to tune the deterministic router before
considering learned routing.

## Inspect metrics

```bash
elh metrics --limit 20
```

Each attempt records model role, model tag, routing rationale, token counts, model
load time, generation time, verifier status, and escalation source. Prompts and file
contents are not stored by default; only a SHA-256 task fingerprint is recorded.

## Safety boundaries

The initial implementation deliberately does **not** provide an unrestricted shell.

- filesystem tools are confined to the selected workspace;
- symlink escapes are rejected;
- commands are passed as argument arrays with `shell=False`;
- `sudo`, privilege changes, destructive utilities, package managers, network clients,
  and dangerous Git operations are blocked;
- writes and execution require approval unless `--yes` is supplied;
- E2B receives read-only tools by default;
- model output is never treated as authorization.

See [docs/SECURITY.md](docs/SECURITY.md) before widening the tool surface.

## Architecture documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Routing and escalation](docs/ROUTING.md)
- [Security model](docs/SECURITY.md)
- [Terminal UI](docs/TUI.md)
- [Roadmap](docs/ROADMAP.md)

## Development

The test suite runs with:

```bash
python -m unittest discover -s tests -v
```

Optional developer checks:

```bash
python -m pip install -e '.[dev]'
ruff check .
pytest
```

## License

Apache License 2.0.
