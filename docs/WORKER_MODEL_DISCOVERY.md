# Dynamic worker model discovery

Fabric is the authority for what models are actually installed across the
fleet. Harness consumes Fabric capability inventory; it does not keep a
second list of product-specific model names.

Inventory freshness is explicit:

| State | Meaning | Consumer reaction |
|---|---|---|
| `CURRENT` | observation is inside the Fabric freshness bound | route normally |
| `STALE` | last-known inventory is still usable, but may omit newly installed models | route from last-known; refresh to discover additions |
| `UNKNOWN` | no trusted observation | refresh before routing |
| `UNAVAILABLE` | worker or provider cannot answer | do not route |

A newly installed model does not require restarting Harness, Control, or the
persistent Fabric controller. The standard path is `fleet.refresh` / Harness
`refresh()`, which re-probes worker-local Ollama and ingests the observation.
Exact pins that miss a model refresh once before failing closed.

An `ollama` `/api/version` of `0.0.0` is recorded as untrusted packaging, not
as a truthful runtime version.

MNCS Harness can also live-scan every Ollama model currently installed on an explicitly managed Windows worker. The inventory is not a fixed project list: if an operator installs, removes, or retags a model on the worker, the next scan reports the new worker-local state.

```bash
elh-fabric scan-models-windows \
  --ssh-host 192.0.2.10 \
  --ssh-user operator \
  --ssh-key ~/.ssh/windows-fabric \
  --expected-hostname WORKER01
```

The command queries the worker's loopback-only Ollama endpoint:

```text
http://127.0.0.1:11434/api/tags
```

This is intentionally different from looking for `ollama.exe` on the non-interactive SSH `PATH`. A Windows OpenSSH session can have a different PATH from the interactive desktop user even while Ollama is installed and its service is running. Model discovery therefore depends on the live Ollama API rather than the CLI executable location.

The JSON result contains every distinct model tag reported by Ollama plus available metadata such as stored size, digest, modification time, model family, parameter size, format, and quantization level.

Example concepts:

```json
{
  "model_count": 2,
  "model_names": [
    "custom-coder:7b",
    "gemma4:e4b"
  ],
  "source": "worker-local-ollama-api"
}
```

## Discovery versus routing

Discovery is deliberately broader than the configured routing roles. Installing a new model does **not** automatically classify it as a coder, reviewer, chat model, vision model, or safe escalation target. Those are semantic/runtime policy decisions and remain explicit configuration.

This separation lets an operator experiment freely with model installations while preventing the harness from guessing capabilities from a model tag alone.

## Network boundary

The Ollama service remains on worker loopback. The controller does not expose the worker's Ollama port over the LAN and does not transfer model blobs. The current Windows inventory command uses the already explicit operator SSH maintenance channel only to execute the loopback inventory request. Fabric mTLS remains the inference transport.

## Installing arbitrary models

Models may be installed directly on the worker at any time. For example:

```text
ollama pull some-model:tag
```

Afterward, rerun `elh-fabric scan-models-windows ...` and the new tag should appear without changing the scanner or rebuilding the project.

The existing `elh-fabric install-models-windows --model <tag>` command can also provision explicit tags. Model discovery is independent of the default accelerator-role model set.
