# Terminal user interface

The Textual-based terminal UI exposes the existing policy-aware harness without
implementing a second agent. Routing, Ollama calls, tools, workspace confinement,
verification, escalation, and metrics all remain in the Python harness.

## Start it

```bash
cd ~/epi13-local-harness
source .venv/bin/activate
python -m pip install -e .
elh-tui --workspace .
```

## Controls

- **Model role:** leave this on `Automatic routing`, or force one configured role.
- **Workspace:** all model file and command tools remain confined to this directory.
- **Images:** enter one or more paths separated by spaces or commas. Quote paths
  containing spaces.
- **Auto-approve:** disabled by default. When disabled, model-requested writes and
  commands are denied rather than falling back to terminal `input()` underneath the
  TUI. When enabled, only actions already allowed by deterministic policy are
  approved; blocked operations remain blocked.
- **Preview route:** inspect the role chain and semantic-routing details without
  invoking a worker model.
- **Doctor, Models, Metrics, Fabric:** inspect the local installation and optional
  Fabric worker/resource/capability freshness state from inside the TUI.

Keyboard shortcuts:

| Shortcut | Action |
|---|---|
| `Enter` | Submit the prompt while the prompt field is focused |
| `Ctrl+R` | Preview route |
| `Ctrl+D` | Run diagnostics |
| `Fabric` button | Show Fabric worker/resource status |
| `Ctrl+L` | Clear the conversation log |
| `Esc` | Request worker cancellation |
| `Ctrl+Q` | Quit |

## Safety behavior

The TUI never executes model output itself. It calls `LocalAgent` and disables the
legacy stdin approval prompt so Textual and `input()` cannot compete for terminal
input. The explicit auto-approval checkbox maps to the existing policy-controlled
`auto_approve` flag.

This is a supervised local interface. Cancellation prevents additional UI updates,
but the current synchronous Ollama request may finish in its background thread before
all resources are released.
