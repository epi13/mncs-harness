# MNCS Harness identity and compatibility

The canonical public identity of this project is:

| Surface | Canonical value |
|---|---|
| Human-facing name | MNCS Harness |
| Repository | `epi13/mncs-harness` |
| Python distribution | `mncs-harness` |
| Preferred import | `mncs_harness` |
| Preferred CLI | `mncs-harness`, `mncs-harness-tui`, `mncs-harness-fabric` |
| Preferred config | `$HOME/.config/mncs-harness/config.toml` |
| Preferred state | `$HOME/.local/state/mncs-harness/` |

MNCS Harness is part of the MNCS **project family / operator implementation
ecosystem**. It is **not** a normative requirement of the Machine-Native
Complexity Standard.

## Intentionally deferred internal package rename

The implementation package remains `epi13_local_harness` in this release.

Renaming every import, protocol schema identifier, and installed service in one
step would break existing operator units, Control adapters, tests, and Fabric
consumer identities without improving architecture. New code should import
`mncs_harness`, which re-exports the current implementation.

## Compatibility surfaces

These names remain supported and are documented as compatibility, not as the
active project identity:

- repository checkout directory `epi13-local-harness`
- Python package `epi13_local_harness`
- CLI entry points `elh`, `elh-tui`, `elh-fabric`, `epi13-harness`
- environment variable `EPI13_HARNESS_CONFIG`
- config directory `$HOME/.config/epi13-local-harness/`
- state directory `$HOME/.local/state/epi13-local-harness/`
- historical controller/consumer identity `epi13-local-harness`
- historical schema identifiers such as `epi13-local-harness.model-inventory.v0.1`

`MNCS_HARNESS_CONFIG` is the preferred override. If it is unset, the loader
still honors `EPI13_HARNESS_CONFIG`. If neither override is set and the new
config file does not exist, an existing legacy config file is used.

New `mncs-harness init` writes the canonical config location.

## Default model profile

Bundled model tags are a **reference profile**, not a laboratory inventory.
Operators replace them with whatever resident models their enrolled workers
actually provide.
