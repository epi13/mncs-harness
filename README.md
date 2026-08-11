# epi13-local-harness

A local, policy-aware AI harness for routing work across multiple local models,
executing narrowly scoped tools, and escalating only when deterministic checks say
that a smaller model was not enough.

The default cascade is intentionally sized for Alexander's Fedora workstation:

```text
request
  -> deterministic safety and modality preflight
  -> optional LiquidAI encoder routing across configured lanes
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
- an optional pinned LiquidAI semantic prompt router;
- a dependency-free Ollama HTTP client for worker models;
- a multi-turn tool-calling loop;
- workspace confinement and command policy checks;
- interactive approval for writes and command execution;
- a Textual terminal UI for chat, routing previews, diagnostics, and metrics;
- Python, shell, JSON, and TOML verification;
- SQLite run, router, and tool-call metrics;
- model health checks and configured-model pulling;
- an evaluation runner for routing experiments;
- unit tests and GitHub Actions CI.

Version 0.5.0 also supports an optional controller-local MNCS Commons service.
Fabric-placed models receive approved Commons tool schemas, but every requested
operation returns to the harness policy layer and executes against the controller
store. The worker receives only the bounded result on the next Fabric inference.

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

For the complete distributed development setup from sibling checkouts:

```bash
python -m pip install -e '../mncs-fabric'
python -m pip install -e '../MNCS-Commons[mcp]'
python -m pip install -e '.[distributed]'
```

Create a user configuration:

```bash
elh init
```

The default file is written to:

```text
~/.config/epi13-local-harness/config.toml
```

## Optional LiquidAI semantic router

Install the router dependencies separately so deterministic-only installations do not
need PyTorch or Transformers:

```bash
python -m pip install -e '.[router]'
```

Download the pinned router snapshot:

```bash
hf download LiquidAI/LFM2.5-Encoder-350M-Prompt-Router \
  --revision 35ca4a0469f180f1cf05a630df8842fa17ac18e3
```

Check the configured, cached, and active state:

```bash
elh router status
elh router prepare
```

After the smoke test succeeds, enable semantic routing in the user configuration:

```toml
[router]
enable_semantic_routing = true
local_files_only = true
```

The full model revision remains pinned because the official checkpoint requires custom
Transformers code. The router only selects a worker lane; deterministic policy remains
authoritative. See [Semantic prompt router](docs/SEMANTIC_ROUTER.md).

## Terminal interface

Launch the full-screen terminal interface from any workspace:

```bash
cd ~/epi13-local-harness
source .venv/bin/activate
elh-tui --workspace .
```

The TUI provides:

- automatic or forced model-role selection;
- separate semantic-router and Ollama-worker status;
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

`doctor` distinguishes semantic-router state from Ollama worker tags, checks optional
verification tools, and confirms that the metrics location is writable.

## Preview routing without running a worker model

```bash
elh route "Explain what systemctl status ollama means"
elh route "Repair the Python tests in this repository"
elh route "Delete and reinstall the system service as root"
```

Deterministic preflight always runs first. When enabled and available, the semantic
router scores eligible lane descriptions. Any router failure falls back to the
inspectable deterministic route.

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

Attach an image for a multimodal worker:

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
to `evals/tasks.jsonl` and use the results to tune routing thresholds.

## Inspect metrics

```bash
elh metrics --limit 20
```

Each attempt records model role, model tag, routing rationale, token counts, model
load time, generation time, verifier status, and escalation source. Semantic runs also
record backend, revision, lane, score, margin, and router latency. Prompts and file
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
- semantic routing never authorizes tools or overrides policy;
- model output is never treated as authorization.

See [docs/SECURITY.md](docs/SECURITY.md) before widening the tool surface.

## Architecture documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Routing and escalation](docs/ROUTING.md)
- [Semantic prompt router](docs/SEMANTIC_ROUTER.md)
- [Security model](docs/SECURITY.md)
- [Terminal UI](docs/TUI.md)
- [MNCS Fabric integration](docs/FABRIC.md)
- [Distributed capability/session foundation](docs/DISTRIBUTED_CAPABILITY_FOUNDATION.md)
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

The normal test suite mocks the semantic backend and does not download model files.

## License

Apache License 2.0.
