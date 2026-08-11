# Worker-local model provisioning

Large model blobs should not be copied from the Local Harness controller to a Fabric
worker over the LAN. The harness can instead stage a tiny Windows command file and run
`ollama pull` on the worker itself. Ollama then downloads the model directly from the
worker's configured registry/network connection.

For the default accelerator roles, the generated installer pulls:

```text
gemma4:e4b
qwen3:8b
gemma4:12b
```

`gemma4:e2b` is intentionally excluded because the default managed routing policy keeps
that lightweight role on the controller's local Ollama instance.

## Stage and run on the worker

```bash
elh-fabric install-models-windows \
  --ssh-host 192.0.2.10 \
  --ssh-user operator \
  --ssh-key ~/.ssh/windows-fabric \
  --expected-hostname WORKER01
```

The command:

1. verifies the explicitly named Windows host over public-key SSH;
2. creates `C:\Users\<ssh-user>\mncs-fabric-worker\install-models.cmd`;
3. copies only that small command file to the worker;
4. runs the command file on the worker; and
5. verifies the requested tags through the worker-local Ollama API.

The SSH session carries command/progress text only. Model blobs are downloaded by
`ollama pull` on the worker and are not transferred through Fabric or from the
controller's model store.

Use `--stage-only` when you want to inspect or launch the command file yourself from a
Windows terminal:

```bash
elh-fabric install-models-windows \
  --ssh-host 192.0.2.10 \
  --ssh-user operator \
  --ssh-key ~/.ssh/windows-fabric \
  --expected-hostname WORKER01 \
  --stage-only
```

Use repeated `--model` arguments to provision a different explicit model set:

```bash
elh-fabric install-models-windows ... \
  --model qwen3:8b \
  --model gemma4:12b
```

Model tags are restricted to Ollama-safe registry/tag characters before they are written
into the command file. Shell metacharacters and whitespace are rejected.

Ollama must already be installed and its local service must be reachable on the worker.
The installer intentionally does not expose Ollama on the LAN.

## Fabric API compatibility before commissioning

Persistent commissioning stages the `mncs_fabric` package that is actually imported by
the active Local Harness Python environment. Editable installs can therefore be stale
even when `pyproject.toml` declares a newer optional dependency.

Before touching the worker, `elh-fabric commission-windows` now verifies that the active
Fabric package exposes:

```text
FabricClient.execute(..., execution_bundle_archive=...)
FabricClient.ingest_capability_observation(...)
FabricClient.workers(... capability observation fields ...)
```

This is the consumer API required by the capability-backed runtime-probe path and is
available in MNCS Fabric `0.2.0a11` and newer supported `0.2.x` builds. An older
imported checkout now
fails immediately with the loaded version and module path instead of commissioning a
worker that can only fail later during the CUDA probe.
