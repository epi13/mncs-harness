# Security model

## Trust boundary

All model output is untrusted. A model may request an action, but only harness code
can authorize and execute it.

The current alpha is designed for supervised local development. It is not a hardened
container boundary, operating-system sandbox, or replacement for backups and Git.

## Enforced controls

### Workspace confinement

Filesystem paths are canonicalized relative to the selected workspace. Paths that
resolve outside it, including symlink escapes, are rejected. Hidden paths are
excluded by default.

### No shell interpolation

Commands run through `subprocess.run` with an argument list and `shell=False`.
Pipelines, redirection, command substitution, and command strings passed to `bash -c`,
`sh -c`, or `python -c` are blocked.

### Executable policy

The default policy blocks:

- `sudo`, `su`, and other privilege changes;
- deletion, disk formatting, partitioning, mounting, ownership, and mode changes;
- process termination and shutdown;
- system-service and package-manager commands;
- network clients and remote shells;
- container engines;
- dangerous Git operations such as hard reset, force push, and clean.

The remaining executable allowlist is still subject to command-specific checks.

### Approval

Writes and commands require interactive approval. `--yes` approves only actions that
already pass policy. It does not turn blocked actions into allowed actions.

### Tiered tools

The E2B role is read-only by default. Larger roles receive write and command tools,
but not broader policy authority.

### Verification

Modified source and configuration files are checked by deterministic parsers or
syntax tools. Verification failure can escalate to another model, but cannot weaken
policy.

### Optional Fabric boundary

Fabric receives only the bounded inference bundle intentionally constructed by
the provider. It does not receive the local workspace tool authority. A remote
model response returns to `LocalAgent`, where the existing policy registry
validates and executes any tool call locally. Remote workers are explicit,
enrolled endpoints; the harness does not scan, disable TLS validation, bypass
certificate trust, expose unauthenticated Ollama, or let model text select
arbitrary hosts.

Fabric placement evidence is resource admission evidence, not semantic
correctness, CUDA proof by itself, attestation, or permission to run tools.
`UNKNOWN` is preserved for stale or missing resource/runtime observations.

## Known limitations

- An allowlisted compiler or test program can execute project-controlled build logic.
  Use a disposable workspace or container for untrusted repositories.
- The process runs with the permissions of the user who launches it.
- Resource limits are currently timeout- and size-based, not cgroup-based.
- There is no seccomp, Landlock, bubblewrap, container, or virtual-machine isolation.
- `--yes` is appropriate only for repositories and tasks the user already trusts.
- Model and tool outputs may contain sensitive repository data; prompt storage is off,
  but tool outputs are present in active model context.

## Recommended operating practice

- commit or stash work before agent edits;
- use a dedicated repository workspace, never `$HOME` as the workspace root;
- leave `allow_hidden_paths = false`;
- keep prompt storage disabled;
- inspect `git diff` after every write session;
- use a container or disposable VM for unknown code;
- do not run the harness as root;
- add narrow tools instead of widening `run_command`.

## Future hardening

The next security layer should put every executing tool inside a constrained runner,
preferably with:

- an unprivileged user namespace;
- a read-only host filesystem and a writable workspace mount;
- network disabled by default;
- CPU, memory, process, and wall-time limits;
- explicit bind mounts for compiler caches;
- immutable verifier binaries or remotely attested verifier nodes;
- signed audit records for tool decisions and results.
