from __future__ import annotations

import shlex
from pathlib import Path

from .models import PolicyConfig, PolicyDecision

BLOCKED_EXECUTABLES = {
    "sudo", "su", "doas", "rm", "rmdir", "mv", "cp", "dd", "mkfs", "fdisk",
    "parted", "mount", "umount", "chmod", "chown", "kill", "pkill", "killall",
    "reboot", "shutdown", "poweroff", "systemctl", "service", "dnf", "yum", "rpm",
    "apt", "apt-get", "pacman", "zypper", "curl", "wget", "ssh", "scp", "rsync",
    "nc", "ncat", "socat", "podman", "docker", "flatpak",
}

DANGEROUS_GIT_PATTERNS = {
    ("reset", "--hard"),
    ("clean", "-f"),
    ("clean", "-fd"),
    ("clean", "-fdx"),
    ("push", "--force"),
    ("push", "-f"),
    ("checkout", "--"),
    ("restore", "--source"),
}

SAFE_GIT_SUBCOMMANDS = {
    "status", "diff", "log", "show", "grep", "rev-parse", "ls-files", "branch",
    "describe", "remote", "config",
}

SAFE_PYTHON_MODULES = {"pytest", "unittest", "compileall", "py_compile"}


class WorkspaceGuard:
    def __init__(self, workspace: Path, allow_hidden_paths: bool = False):
        self.workspace = workspace.expanduser().resolve()
        self.allow_hidden_paths = allow_hidden_paths

    def resolve(self, requested: str | Path, *, must_exist: bool = False) -> Path:
        candidate = Path(requested).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        try:
            resolved = candidate.resolve(strict=must_exist)
        except FileNotFoundError:
            raise ValueError(f"Path does not exist: {requested}") from None

        if resolved != self.workspace and self.workspace not in resolved.parents:
            raise ValueError(f"Path escapes workspace: {requested}")

        if not self.allow_hidden_paths:
            relative = resolved.relative_to(self.workspace)
            if any(part.startswith(".") and part not in {".", ".."} for part in relative.parts):
                raise ValueError(f"Hidden paths are disabled: {requested}")
        return resolved

    def relative(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.workspace))


class CommandPolicy:
    def __init__(self, config: PolicyConfig, guard: WorkspaceGuard):
        self.config = config
        self.guard = guard
        self.allowed = set(config.allowed_executables)

    def parse(self, command: str | list[str]) -> list[str]:
        if isinstance(command, str):
            try:
                argv = shlex.split(command)
            except ValueError as exc:
                raise ValueError(f"Could not parse command: {exc}") from exc
        else:
            argv = [str(value) for value in command]
        if not argv:
            raise ValueError("Command cannot be empty")
        return argv

    def evaluate(self, command: str | list[str]) -> tuple[list[str], PolicyDecision]:
        try:
            argv = self.parse(command)
        except ValueError as exc:
            return [], PolicyDecision(False, "blocked", str(exc))

        executable = Path(argv[0]).name
        if executable in BLOCKED_EXECUTABLES:
            return argv, PolicyDecision(
                False, "blocked", f"Executable {executable!r} is blocked by policy"
            )
        if executable not in self.allowed:
            return argv, PolicyDecision(
                False, "blocked", f"Executable {executable!r} is not allowlisted"
            )

        joined = " ".join(argv).lower()
        if any(token in joined for token in ("&&", "||", ";", "`", "$(", ">", "<")):
            return argv, PolicyDecision(
                False, "blocked", "Shell operators and redirection are not supported"
            )

        if executable in {"bash", "sh"}:
            if "-c" in argv or "--command" in argv:
                return argv, PolicyDecision(
                    False, "blocked", "Shell command strings are blocked; use an explicit tool"
                )
            if "-n" not in argv:
                return argv, PolicyDecision(
                    False, "blocked", "Shells may only be used for syntax checking with -n"
                )

        if executable in {"python", "python3"}:
            if "-c" in argv:
                return argv, PolicyDecision(False, "blocked", "python -c is blocked")
            if "-m" in argv:
                index = argv.index("-m")
                module = argv[index + 1] if index + 1 < len(argv) else ""
                if module not in SAFE_PYTHON_MODULES:
                    return argv, PolicyDecision(
                        False, "blocked", f"Python module {module!r} is not allowlisted"
                    )
            elif len(argv) > 1:
                script = argv[1]
                try:
                    self.guard.resolve(script, must_exist=True)
                except ValueError as exc:
                    return argv, PolicyDecision(False, "blocked", str(exc))

        if executable == "git":
            subcommand = next((arg for arg in argv[1:] if not arg.startswith("-")), "")
            if subcommand not in SAFE_GIT_SUBCOMMANDS:
                return argv, PolicyDecision(
                    False, "blocked", f"Git subcommand {subcommand!r} is not allowlisted"
                )
            for first, second in DANGEROUS_GIT_PATTERNS:
                if first in argv and second in argv:
                    return argv, PolicyDecision(False, "blocked", "Dangerous Git operation")

        for argument in argv[1:]:
            if argument.startswith("/"):
                try:
                    self.guard.resolve(argument, must_exist=False)
                except ValueError:
                    return argv, PolicyDecision(
                        False, "blocked", f"Absolute path is outside workspace: {argument}"
                    )
            if argument.startswith("~"):
                return argv, PolicyDecision(
                    False, "blocked", "Home-relative command paths are not supported"
                )

        return argv, PolicyDecision(
            True,
            "medium",
            "Command is allowlisted and workspace-scoped",
            requires_approval=True,
        )


def file_write_decision(path: Path, config: PolicyConfig) -> PolicyDecision:
    if path.name in {".git", ".env"} or ".git" in path.parts:
        return PolicyDecision(False, "blocked", "Writing Git internals or .env is blocked")
    return PolicyDecision(
        True,
        "medium",
        "Workspace file write requires approval",
        requires_approval=True,
    )


def approval_granted(decision: PolicyDecision, auto_approve: bool, interactive: bool) -> bool:
    if not decision.allowed:
        return False
    if not decision.requires_approval:
        return True
    if auto_approve:
        return True
    if not interactive:
        return False
    answer = input(f"Approve {decision.risk}-risk action? {decision.reason} [y/N] ").strip().lower()
    return answer in {"y", "yes"}
