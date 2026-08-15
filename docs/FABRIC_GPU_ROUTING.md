# Operational Fabric GPU routing

The harness can keep lightweight chat local while sending heavier model roles to an
explicitly enrolled MNCS Fabric worker. The harness does not scan the LAN, expose
Ollama on the network, or use SSH as the inference transport.

## Recommended: persistent Windows commissioning

For a Windows GPU worker that should remain available between experiments, use the
persistent commissioning command instead of reusing short-lived physical-test PKI:

```bash
elh-fabric commission-windows \
  --ssh-host 192.0.2.10 \
  --ssh-user operator \
  --ssh-key ~/.ssh/windows-fabric \
  --expected-hostname WORKER01 \
  --worker-id windows-gpu \
  --windows-python python
```

The command requires an explicit SSH endpoint and existing strict host-key entry. It
uses public-key-only SSH/SCP for **provisioning only**, then:

1. creates or reuses a persistent local enrollment under
   `~/.local/state/mncs-harness/fabric-enrollment/<worker-id>`;
2. creates a dedicated Fabric CA, controller certificate/key, worker certificate/key,
   and append-only controller/worker trust ledgers;
3. stages the installed `mncs-fabric` package and worker trust material to the explicit
   Windows host under `C:/Users/<ssh-user>/mncs-fabric-worker` by default;
4. starts a detached, bounded Fabric worker listening on the requested Fabric port;
5. updates the normal MNCS Harness TOML with the matching controller identity and trust
   paths;
6. refreshes the worker through mTLS; and
7. dispatches a synchronized CUDA execution probe **through Fabric itself** before the
   worker is treated as CUDA-ready.

The CA private key and controller private key remain on the controller. Only the worker
certificate/key, CA certificate, worker trust ledger, and Fabric package are staged to
Windows. Certificate material is not committed to the repository.

Persistent certificates default to 365 days. Re-running commissioning reuses the same
enrollment. Rotation is explicit:

```bash
elh-fabric commission-windows ... --rotate-enrollment
```

Use a specific GPU Python environment when `python` is not the interpreter that has the
required Torch/CUDA runtime:

```bash
elh-fabric commission-windows ... \
  --windows-python 'C:/path/to/gpu-python/python.exe'
```

Commissioning also checks the worker-local Ollama `/api/tags` endpoint and reports any
routed model tags that are missing. It does not automatically download large models.

## Existing enrollment: configure one remote worker

If a worker has already been enrolled by another operator workflow,
`elh-fabric configure-remote` performs a bounded edit of the normal user configuration
and creates `<config.toml>.pre-fabric` before the first edit.

```bash
elh-fabric configure-remote \
  --controller-id epi13-local-harness \
  --worker-id windows-gpu \
  --host 192.0.2.10 \
  --port 7443 \
  --ca-file /operator/path/ca.pem \
  --client-certificate /operator/path/controller.pem \
  --client-key /operator/path/controller.key \
  --trust-state /operator/path/controller-trust.jsonl
```

`--controller-id` must match the logical controller identity enrolled by the worker. The
command validates all four local trust files before changing configuration.

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

Use repeated `--accelerator-role` or `--local-role` arguments with
`configure-remote` to choose a different role split. A 512 MiB GPU reserve is kept by
default and can be changed with `--gpu-reserve-mib`.

## Fresh CUDA execution evidence

A resource snapshot from `nvidia-smi` establishes GPU discovery and volatile memory, but
it is not CUDA execution proof. When a remote worker reports a CUDA accelerator, Local
Harness now dispatches a small offline runtime probe to that exact worker through Fabric.
The probe imports Torch in the worker's own Python runtime, performs real FP32/FP16/BF16
operations when supported, calls `torch.cuda.synchronize()`, and then ingests the result
through Fabric's runtime-observation API.

The default settings are:

```toml
[fabric]
runtime_probe_on_refresh = true
runtime_probe_timeout_seconds = 45.0
runtime_probe_max_age_seconds = 1800.0
```

The probe is refreshed at startup/status and again before accelerator inference when the
existing observation is missing or older than the configured bound. This avoids a stale
commissioning-time CUDA result becoming permanent placement authority.

This remains operator-controlled runtime evidence, not hardware attestation or semantic
correctness evidence.

## Model-size admission

When `model_storage_bytes` is not explicitly configured, the Fabric-backed Ollama
provider reads the matching local Ollama tag's `size` metadata and passes that value into
the Fabric placement request. This prevents a large model from being admitted as if its
size were zero merely because the user did not hand-enter a byte count.

This is an admission estimate, not a claim about actual remote VRAM residency. Fabric
continues to own worker eligibility and placement evidence; Ollama owns the actual model
runtime.

## Inspect the connection

```bash
elh-fabric show
elh-fabric status
```

`status` initializes the configured Fabric session, refreshes the remote worker, and
refreshes bounded CUDA execution evidence when needed. In addition to worker and
accelerator counts, it reports `cuda_ready_count`.

A healthy GPU worker should show all of the following concepts in the JSON result:

```text
state: available
worker availability: AVAILABLE
accelerator_count: 1 or more
cuda_ready_count: 1 or more
runtime_observation.runtime_execution_probe: PASS
```

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

SSH/SCP are only a commissioning/bootstrap channel for the explicitly named Windows
host. Candidate inference traffic uses direct mutually authenticated Fabric TLS, and
worker-local Ollama remains bound to loopback.