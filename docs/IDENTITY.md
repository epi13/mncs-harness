# MNCS Harness identity and compatibility

The canonical public identity of this project is:

| Surface | Canonical value |
|---|---|
| Human-facing name | MNCS Harness |
| Repository | `epi13/mncs-harness` |
| Python distribution | `mncs-harness` |
| Implementation package | `mncs_harness` (`src/mncs_harness/`) |
| MNCS decision kernels | `mncs/harness_*.mncs` with corpora under `corpora/` |
| Preferred CLI | `mncs-harness`, `mncs-harness-tui`, `mncs-harness-fabric` |
| Preferred config | `$HOME/.config/mncs-harness/config.toml` |
| Preferred state | `$HOME/.local/state/mncs-harness/` |
| VS Code extension | `mncs-harness-vscode`, commands under `mncs-harness.*` |

MNCS Harness is part of the MNCS **project family / operator implementation
ecosystem**. It is **not** a normative requirement of the Machine-Native
Complexity Standard.

## Completed package rename

The former implementation package `epi13_local_harness` was retired during
the MNCS-language conversion campaign: its modules moved to
`src/mncs_harness/`, all imports and entry points were rewritten, and the
legacy directory was deleted. There is exactly one implementation tree.
Historical documentation may mention the former name when explaining
migration, but no live code imports it.

## Compatibility surfaces

These names remain supported and are documented as compatibility, not as the
active project identity:

- repository checkout directory `epi13-local-harness` (operator-local only)
- CLI entry points `elh`, `elh-tui`, `elh-fabric`, `epi13-harness`
- environment variable `EPI13_HARNESS_CONFIG`
- config directory `$HOME/.config/epi13-local-harness/` (read fallback)
- state directory `$HOME/.local/state/epi13-local-harness/` (read fallback)
- historical controller/consumer identity `epi13-local-harness`
- persisted wire/state schema identifiers such as
  `epi13-local-harness.model-inventory.v0.1`,
  `epi13-local-harness.fabric-enrollment.v0.1`, and
  `epi13-local-harness.fabric-launcher.v0.2`
- frozen development-evidence records quoting the former identity

The schema identifiers are intentionally frozen: they label persisted state
files, trust-store metadata, and cross-process observations, so renaming
them would orphan existing deployments without changing any behavior.
`MNCS_HARNESS_CONFIG` is the preferred override. If it is unset, the loader
still honors `EPI13_HARNESS_CONFIG`. If neither override is set and the new
config file does not exist, an existing legacy config file is used.

New `mncs-harness init` writes the canonical config location, and new
Fabric worker profiles default to the canonical state directory.

## Default model profile

Bundled model tags are a **reference profile**, not a laboratory inventory.
Operators replace them with whatever resident models their enrolled workers
actually provide.
