# Operational Fabric GPU routing

The harness can keep lightweight chat local while sending heavier model roles to an
explicitly enrolled MNCS Fabric worker. The harness does not scan the LAN, bootstrap
workers over SSH, expose Ollama on the network, or copy TLS private-key contents into
its configuration.

## Prerequisites

- `mncs-fabric>=0.2.0a8` is installed in the harness environment;
- the remote Fabric worker is already enrolled and running as a bounded mTLS service;
- the worker-local Ollama service is listening on loopback (`127.0.0.1:11434` by default);
- the model tags used by the routed roles are installed on that worker; and
- the controller has the CA, controller client certificate/key, and worker trust-state
  files for that enrollment.

For sibling development checkouts:

```bash
python -m pip install -e ../mncs-fabric
python -m pip install -e .
```

## Configure one GPU worker

`elh-fabric configure-remote` performs a bounded edit of the normal user configuration
and creates `<config.toml>.pre-fabric` before the first edit.

```bash
elh-fabric configure-remote \
  --worker-id windows-gpu \
  --host 192.0.2.10 \
  --port 7443 \
  --ca-file /operator/path/ca.pem \
  --client-certificate /operator/path/controller.pem \
  --client-key /operator/path/controller.key \
  --trust-state /operator/path/controller-trust.jsonl
```

Only the paths are stored in the harness TOML. Certificate/key contents are not copied.
The command validates that all four trust files exist before changing the config.

By default the managed routing policy becomes:

```text
e2b       -> local Ollama

e4b       -> Fabric, CUDA accelerator required
coder     -> Fabric, CUDA accelerator required
reviewer  -> Fabric, CUDA accelerator required
```

The heavier roles use `offload = "auto"` and preserve local fallback. Fabric therefore
admits only a worker with fresh executable CUDA evidence for those roles; if no eligible
worker is available, the provider returns to the existing local Ollama path rather than
silently treating an unverified accelerator as usable.

Use repeated `--accelerator-role` or `--local-role` arguments to choose a different role
split. A 512 MiB GPU reserve is kept by default and can be changed with
`--gpu-reserve-mib`.

## Model-size admission

When `model_storage_bytes` is not explicitly configured, the Fabric-backed Ollama
provider now reads the matching local Ollama tag's `size` metadata and passes that value
into the Fabric placement request. This prevents a large model from being admitted as
if its size were zero merely because the user did not hand-enter a byte count.

This is an admission estimate, not a claim about actual remote VRAM residency. Fabric
continues to own worker eligibility and placement evidence; Ollama owns the actual model
runtime.

## Inspect the connection

```bash
elh-fabric show
elh-fabric status
```

`status` initializes the configured Fabric session and refreshes the remote worker. It
reports worker availability, accelerator observations, and current Fabric diagnostics.

Then launch the normal TUI:

```bash
elh-tui --workspace .
```

No special TUI command or environment variable is required. `LocalAgent` reads the
normal user configuration, initializes Fabric at startup, and selects the Fabric provider
for the configured model roles.

The TUI **Fabric** panel displays the worker and the last placement. The attempts table
shows whether an inference used the Fabric provider or the local fallback.

## Disable without deleting enrollment paths

```bash
elh-fabric disable
```

This changes only `[fabric].enabled` to `false`; worker/trust configuration remains in
the user TOML for later re-enablement.

## Security boundary

Remote inference does not move local tool execution to the worker. Model-requested tool
calls return to the local harness, where the existing workspace and command policy still
controls execution. Fabric transports the bounded inference request and evidence; it does
not grant the remote worker direct authority over the local workspace.
