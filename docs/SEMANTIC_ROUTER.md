# Semantic prompt router

The harness can use `LiquidAI/LFM2.5-Encoder-350M-Prompt-Router` as an optional,
CPU-oriented semantic dispatcher in front of the Ollama worker models.

The encoder only selects a configured lane. It never authorizes tools, approves
commands, changes workspace policy, or decides whether a result passes verification.

## Install the optional backend

From the repository virtual environment:

```bash
python -m pip install -e '.[router]'
```

The bundled configuration pins the LiquidAI repository to:

```text
35ca4a0469f180f1cf05a630df8842fa17ac18e3
```

A full 40-character commit hash is mandatory because the model requires
`trust_remote_code=True`. The harness refuses to load `main`, a tag, or an abbreviated
revision.

## Download and test the router

The default cache is the normal Hugging Face cache used by `hf download`:

```text
~/.cache/huggingface/hub
```

Download the pinned snapshot:

```bash
hf download LiquidAI/LFM2.5-Encoder-350M-Prompt-Router \
  --revision 35ca4a0469f180f1cf05a630df8842fa17ac18e3
```

Inspect status without loading the model:

```bash
elh router status
```

Load it and run an offline smoke test:

```bash
elh router prepare
```

`prepare` uses the configured `local_files_only` value. Set it to `true` after the
snapshot is cached when the router must never access the network.

## Enable hybrid routing

Edit `~/.config/epi13-local-harness/config.toml`:

```toml
[router]
mode = "hybrid"
backend = "transformers"
model = "LiquidAI/LFM2.5-Encoder-350M-Prompt-Router"
revision = "35ca4a0469f180f1cf05a630df8842fa17ac18e3"
device = "cpu"
enable_semantic_routing = true
local_files_only = true
minimum_score = 0.60
minimum_margin = 0.12
```

Then verify:

```bash
elh doctor
elh route "Fix the failing Python parser tests."
```

The route preview includes the actual encoder scores, selected lane, runner-up,
margin, revision, and routing latency.

## Runtime states

The CLI and TUI distinguish these states:

- `disabled`: configured but semantic routing is turned off;
- `unpinned`: the revision is not a full commit hash and cannot be loaded safely;
- `missing-dependencies`: one or more router extras are unavailable;
- `not-cached`: no pinned snapshot was found in the configured cache;
- `cached`: files are present but the encoder has not been loaded in this process;
- `active`: the encoder is loaded and available to score prompts;
- `unsupported`: the configured backend is not implemented.

Cached and active are deliberately different. A downloaded checkpoint is not reported
as active until the current harness process has successfully loaded it.

## Fallback behavior

Deterministic preflight always runs first. High-risk requests remain on the deterministic
reviewer path. If the semantic router cannot load, returns malformed data, has no eligible
lanes, or otherwise fails, the harness keeps the deterministic route and records a clear
fallback reason.

The previous fixed-score implementation remains available only when the backend is
explicitly configured as `heuristic`. Naming another backend no longer relabels heuristic
scores as model output.

## Metrics

The runs table stores semantic backend, pinned revision, selected lane, top score, margin,
latency, and routing reason. Prompt text remains disabled by default; only the existing
SHA-256 task fingerprint is stored unless prompt storage is explicitly enabled.
