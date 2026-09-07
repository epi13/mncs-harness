from __future__ import annotations

import shlex
from pathlib import Path

from . import mncs_exec
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

        # Host boundary: lexing, allowlists, and path resolution produce
        # plain observations. The allow/block ordering itself EXECUTES the
        # MNCS kernel (mncs/harness_policy.mncs via mncs_exec).
        executable = Path(argv[0]).name
        joined = " ".join(argv).lower()

        executable_blocked = executable in BLOCKED_EXECUTABLES
        allowlisted = executable in self.allowed
        has_shell_operator = any(
            token in joined for token in ("&&", "||", ";", "`", "$(", ">", "<")
        )

        shell_ok, shell_detail = True, ""
        if executable in {"bash", "sh"}:
            if "-c" in argv or "--command" in argv:
                shell_ok = False
                shell_detail = "Shell command strings are blocked; use an explicit tool"
            elif "-n" not in argv:
                shell_ok = False
                shell_detail = "Shells may only be used for syntax checking with -n"

        python_ok, python_detail = True, ""
        if executable in {"python", "python3"}:
            if "-c" in argv:
                python_ok, python_detail = False, "python -c is blocked"
            elif "-m" in argv:
                index = argv.index("-m")
                module = argv[index + 1] if index + 1 < len(argv) else ""
                if module not in SAFE_PYTHON_MODULES:
                    python_ok = False
                    python_detail = f"Python module {module!r} is not allowlisted"
            elif len(argv) > 1:
                try:
                    self.guard.resolve(argv[1], must_exist=True)
                except ValueError as exc:
                    python_ok, python_detail = False, str(exc)

        git_ok, git_detail = True, ""
        if executable == "git":
            subcommand = next((arg for arg in argv[1:] if not arg.startswith("-")), "")
            if subcommand not in SAFE_GIT_SUBCOMMANDS:
                git_ok = False
                git_detail = f"Git subcommand {subcommand!r} is not allowlisted"
            elif any(first in argv and second in argv for first, second in DANGEROUS_GIT_PATTERNS):
                git_ok, git_detail = False, "Dangerous Git operation"

        path_ok, path_detail = True, ""
        for argument in argv[1:]:
            if argument.startswith("/"):
                try:
                    self.guard.resolve(argument, must_exist=False)
                except ValueError:
                    path_ok = False
                    path_detail = f"Absolute path is outside workspace: {argument}"
                    break
            if argument.startswith("~"):
                path_ok, path_detail = False, "Home-relative command paths are not supported"
                break

        reason = mncs_exec.command_reason(
            executable_blocked,
            allowlisted,
            has_shell_operator,
            shell_ok,
            python_ok,
            git_ok,
            path_ok,
        )
        details = {
            1: f"Executable {executable!r} is blocked by policy",
            2: f"Executable {executable!r} is not allowlisted",
            3: "Shell operators and redirection are not supported",
            4: shell_detail,
            5: python_detail,
            6: git_detail,
            7: path_detail,
        }
        if reason != 0:  # 0 is the kernel's Allow code (mncs/harness_policy.mncs)
            return argv, PolicyDecision(False, "blocked", details[reason])
        return argv, PolicyDecision(
            True,
            "medium",
            "Command is allowlisted and workspace-scoped",
            requires_approval=True,
        )


def file_write_decision(path: Path, config: PolicyConfig) -> PolicyDecision:
    protected = path.name in {".git", ".env"} or ".git" in path.parts
    if not mncs_exec.write_allowed(protected):  # executes mncs/harness_policy.mncs
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
