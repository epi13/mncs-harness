from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .commons import WRITE_TOOLS, CommonsError, CommonsSession
from .models import PolicyConfig, PolicyDecision, ToolExecution
from .policy import (
    CommandPolicy,
    WorkspaceGuard,
    approval_granted,
    file_write_decision,
)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], ToolExecution]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(
        self,
        workspace: Path,
        policy_config: PolicyConfig,
        *,
        auto_approve: bool = False,
        interactive: bool = True,
        commons: CommonsSession | None = None,
    ):
        self.guard = WorkspaceGuard(workspace, policy_config.allow_hidden_paths)
        self.policy_config = policy_config
        self.command_policy = CommandPolicy(policy_config, self.guard)
        self.auto_approve = auto_approve
        self.interactive = interactive
        self.commons = commons
        self.modified_paths: list[Path] = []
        self._tools = self._build_tools()

    @property
    def workspace(self) -> Path:
        return self.guard.workspace

    def _build_tools(self) -> dict[str, ToolDefinition]:
        tools = {
            "read_file": ToolDefinition(
                "read_file",
                "Read a UTF-8 text file inside the workspace. Use line ranges for large files.",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer", "minimum": 1},
                        "end_line": {"type": "integer", "minimum": 1},
                    },
                    "required": ["path"],
                },
                self._read_file,
            ),
            "list_directory": ToolDefinition(
                "list_directory",
                "List files and directories inside the workspace without following escapes.",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "default": "."},
                        "max_entries": {"type": "integer", "minimum": 1, "maximum": 500},
                    },
                },
                self._list_directory,
            ),
            "search_text": ToolDefinition(
                "search_text",
                "Search UTF-8 workspace files for a literal string or regular expression.",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "path": {"type": "string", "default": "."},
                        "regex": {"type": "boolean", "default": False},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
                    },
                    "required": ["query"],
                },
                self._search_text,
            ),
            "write_file": ToolDefinition(
                "write_file",
                "Create or replace a UTF-8 text file inside the workspace. Requires approval.",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
                self._write_file,
            ),
            "run_command": ToolDefinition(
                "run_command",
                "Run one allowlisted command in the workspace without a shell. Requires approval.",
                {
                    "type": "object",
                    "properties": {
                        "argv": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        }
                    },
                    "required": ["argv"],
                },
                self._run_command,
            ),
            "git_diff": ToolDefinition(
                "git_diff",
                "Read the current Git working tree diff. This tool never changes Git state.",
                {
                    "type": "object",
                    "properties": {
                        "staged": {"type": "boolean", "default": False},
                        "path": {"type": "string"},
                    },
                },
                self._git_diff,
            ),
            "system_info": ToolDefinition(
                "system_info",
                "Return basic local operating-system, CPU, memory, disk, and Python information.",
                {"type": "object", "properties": {}},
                self._system_info,
            ),
        }
        if self.commons is not None and self.commons.ready:
            for schema in self.commons.schemas():
                function = schema["function"]
                name = str(function["name"])
                if name in tools:
                    raise ValueError(f"Commons tool name collides with controller tool: {name}")
                tools[name] = ToolDefinition(
                    name,
                    str(function.get("description", "")),
                    dict(function["parameters"]),
                    lambda arguments, tool_name=name: self._commons_call(tool_name, arguments),
                )
        return tools

    def available_schemas(self, names: tuple[str, ...]) -> list[dict[str, Any]]:
        return [self._tools[name].schema() for name in names if name in self._tools]

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolExecution:
        tool = self._tools.get(name)
        if not tool:
            decision = PolicyDecision(False, "blocked", f"Unknown tool: {name}")
            return ToolExecution(name, arguments, decision.reason, False, decision)
        try:
            return tool.handler(arguments)
        except Exception as exc:  # tool boundaries must return errors to the model
            decision = PolicyDecision(False, "blocked", f"Tool failed safely: {exc}")
            return ToolExecution(name, arguments, str(exc), False, decision)

    def _truncate(self, text: str) -> str:
        limit = self.policy_config.max_tool_output_chars
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n... truncated {len(text) - limit} characters"

    def _commons_call(self, name: str, args: dict[str, Any]) -> ToolExecution:
        assert self.commons is not None
        if name in WRITE_TOOLS:
            decision = PolicyDecision(
                self.commons.config.allow_model_publication,
                "medium" if self.commons.config.allow_model_publication else "blocked",
                (
                    "Persistent Commons publication requires explicit knowledge-write approval"
                    if self.commons.config.allow_model_publication
                    else "Commons model publication is disabled by controller policy"
                ),
                requires_approval=True,
            )
            if not decision.allowed:
                return ToolExecution(name, args, "COMMONS_TOOL_DENIED: " + decision.reason, False, decision)
            if not approval_granted(decision, self.auto_approve, self.interactive):
                return ToolExecution(
                    name,
                    args,
                    "COMMONS_TOOL_DENIED: publication was not approved",
                    False,
                    decision,
                )
            allow_write = True
        else:
            decision = PolicyDecision(True, "low", "Read-only controller-local Commons access")
            allow_write = False
        try:
            payload, success = self.commons.call(name, args, allow_write=allow_write)
            return ToolExecution(
                name,
                args,
                self._truncate(json.dumps(payload, sort_keys=True, ensure_ascii=False)),
                success,
                decision,
            )
        except CommonsError as exc:
            failure = PolicyDecision(False, "blocked", f"{exc.code}: {exc.detail}")
            return ToolExecution(name, args, str(exc), False, failure)

    def _read_file(self, args: dict[str, Any]) -> ToolExecution:
        path = self.guard.resolve(str(args["path"]), must_exist=True)
        if not path.is_file():
            raise ValueError(f"Not a regular file: {args['path']}")
        size = path.stat().st_size
        if size > self.policy_config.max_file_bytes:
            raise ValueError(
                f"File is {size} bytes; limit is {self.policy_config.max_file_bytes} bytes"
            )
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        start = max(1, int(args.get("start_line", 1)))
        end = min(len(lines), int(args.get("end_line", len(lines))))
        selected = "\n".join(
            f"{number}: {lines[number - 1]}" for number in range(start, end + 1)
        )
        decision = PolicyDecision(True, "low", "Read-only workspace access")
        return ToolExecution("read_file", args, self._truncate(selected), True, decision)

    def _list_directory(self, args: dict[str, Any]) -> ToolExecution:
        path = self.guard.resolve(str(args.get("path", ".")), must_exist=True)
        if not path.is_dir():
            raise ValueError(f"Not a directory: {args.get('path', '.')}")
        max_entries = min(500, max(1, int(args.get("max_entries", 200))))
        entries: list[str] = []
        for child in sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if not self.policy_config.allow_hidden_paths and child.name.startswith("."):
                continue
            suffix = "/" if child.is_dir() else ""
            entries.append(f"{self.guard.relative(child)}{suffix}")
            if len(entries) >= max_entries:
                entries.append("... entry limit reached")
                break
        decision = PolicyDecision(True, "low", "Read-only workspace access")
        return ToolExecution("list_directory", args, "\n".join(entries), True, decision)

    def _search_text(self, args: dict[str, Any]) -> ToolExecution:
        root = self.guard.resolve(str(args.get("path", ".")), must_exist=True)
        query = str(args["query"])
        use_regex = bool(args.get("regex", False))
        max_results = min(200, max(1, int(args.get("max_results", 50))))
        pattern = re.compile(query) if use_regex else None
        files = [root] if root.is_file() else root.rglob("*")
        results: list[str] = []
        for candidate in files:
            if len(results) >= max_results:
                break
            if not candidate.is_file() or candidate.is_symlink():
                continue
            relative = candidate.relative_to(self.workspace)
            if not self.policy_config.allow_hidden_paths and any(
                part.startswith(".") for part in relative.parts
            ):
                continue
            try:
                if candidate.stat().st_size > self.policy_config.max_file_bytes:
                    continue
                for line_number, line in enumerate(
                    candidate.read_text(encoding="utf-8").splitlines(), start=1
                ):
                    matched = bool(pattern.search(line)) if pattern else query in line
                    if matched:
                        results.append(f"{relative}:{line_number}: {line.strip()}")
                        if len(results) >= max_results:
                            break
            except (UnicodeDecodeError, OSError):
                continue
        decision = PolicyDecision(True, "low", "Read-only workspace search")
        output = "\n".join(results) if results else "No matches found."
        return ToolExecution("search_text", args, self._truncate(output), True, decision)

    def _write_file(self, args: dict[str, Any]) -> ToolExecution:
        path = self.guard.resolve(str(args["path"]), must_exist=False)
        content = str(args["content"])
        encoded = content.encode("utf-8")
        if len(encoded) > self.policy_config.max_file_bytes:
            raise ValueError(
                f"Requested content is {len(encoded)} bytes; limit is {self.policy_config.max_file_bytes}"
            )
        decision = file_write_decision(path, self.policy_config)
        if not approval_granted(decision, self.auto_approve, self.interactive):
            return ToolExecution(
                "write_file", args, "Write denied or not approved.", False, decision
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if path not in self.modified_paths:
            self.modified_paths.append(path)
        return ToolExecution(
            "write_file",
            args,
            f"Wrote {len(encoded)} bytes to {self.guard.relative(path)}",
            True,
            decision,
            [path],
        )

    def _run_command(self, args: dict[str, Any]) -> ToolExecution:
        argv, decision = self.command_policy.evaluate(args["argv"])
        if not decision.allowed:
            return ToolExecution("run_command", args, decision.reason, False, decision)
        if not approval_granted(decision, self.auto_approve, self.interactive):
            return ToolExecution(
                "run_command", args, "Command denied or not approved.", False, decision
            )
        completed = subprocess.run(
            argv,
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=self.policy_config.command_timeout_seconds,
            shell=False,
            env={**os.environ, "PAGER": "cat", "GIT_PAGER": "cat"},
            check=False,
        )
        output = (
            f"exit_code={completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
        return ToolExecution(
            "run_command",
            args,
            self._truncate(output),
            completed.returncode == 0,
            decision,
        )

    def _git_diff(self, args: dict[str, Any]) -> ToolExecution:
        argv = ["git", "diff", "--no-ext-diff", "--"]
        if bool(args.get("staged", False)):
            argv.insert(2, "--cached")
        requested_path = args.get("path")
        if requested_path:
            path = self.guard.resolve(str(requested_path), must_exist=False)
            argv.append(self.guard.relative(path))
        completed = subprocess.run(
            argv,
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
            check=False,
        )
        decision = PolicyDecision(True, "low", "Read-only Git diff")
        output = completed.stdout or completed.stderr or "No diff."
        return ToolExecution(
            "git_diff", args, self._truncate(output), completed.returncode == 0, decision
        )

    def _system_info(self, args: dict[str, Any]) -> ToolExecution:
        memory = "unknown"
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    memory = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
        disk = shutil.disk_usage(self.workspace)
        payload = {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "machine": platform.machine(),
            "processor": platform.processor() or "unknown",
            "cpu_count": os.cpu_count(),
            "memory_total": memory,
            "workspace": str(self.workspace),
            "workspace_disk_free_bytes": disk.free,
        }
        decision = PolicyDecision(True, "low", "Read-only system inspection")
        return ToolExecution("system_info", args, json.dumps(payload, indent=2), True, decision)
