from __future__ import annotations

import os
import shutil
import tomllib
from importlib.resources import files
from pathlib import Path
from typing import Any

from .models import (
    HarnessConfig,
    MetricsConfig,
    ModelConfig,
    OllamaConfig,
    PolicyConfig,
    RoutingConfig,
    VerificationConfig,
)

APP_NAME = "epi13-local-harness"


def default_config_path() -> Path:
    override = os.environ.get("EPI13_HARNESS_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / APP_NAME / "config.toml"


def bundled_config_path() -> Path:
    return Path(str(files("epi13_local_harness").joinpath("default_config.toml")))


def bundled_evals_path() -> Path:
    return Path(str(files("epi13_local_harness").joinpath("default_evals.jsonl")))


def initialize_config(destination: Path | None = None, force: bool = False) -> Path:
    destination = (destination or default_config_path()).expanduser()
    if destination.exists() and not force:
        raise FileExistsError(f"Configuration already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(bundled_config_path(), destination)
    return destination


def _required(data: dict[str, Any], key: str, context: str) -> Any:
    if key not in data:
        raise ValueError(f"Missing required setting {context}.{key}")
    return data[key]


def load_config(path: Path | None = None) -> HarnessConfig:
    selected = (path or default_config_path()).expanduser()
    source = selected if selected.exists() else bundled_config_path()
    with source.open("rb") as handle:
        raw = tomllib.load(handle)

    ollama_raw = _required(raw, "ollama", "root")
    routing_raw = _required(raw, "routing", "root")
    policy_raw = _required(raw, "policy", "root")
    verification_raw = _required(raw, "verification", "root")
    metrics_raw = _required(raw, "metrics", "root")
    models_raw = _required(raw, "models", "root")

    models: dict[str, ModelConfig] = {}
    for role, item in models_raw.items():
        models[role] = ModelConfig(
            role=role,
            name=str(_required(item, "name", f"models.{role}")),
            keep_alive=item.get("keep_alive", "0"),
            num_ctx=int(item.get("num_ctx", 8192)),
            think=item.get("think", False),
            temperature=float(item.get("temperature", 1.0)),
            top_p=float(item.get("top_p", 0.95)),
            top_k=int(item.get("top_k", 64)),
            tools=tuple(str(value) for value in item.get("tools", [])),
        )

    required_roles = {"e2b", "e4b", "reviewer"}
    missing = required_roles.difference(models)
    if missing:
        raise ValueError(f"Missing required model roles: {', '.join(sorted(missing))}")

    return HarnessConfig(
        ollama=OllamaConfig(
            base_url=str(ollama_raw.get("base_url", "http://127.0.0.1:11434")).rstrip("/"),
            timeout_seconds=int(ollama_raw.get("timeout_seconds", 600)),
            max_tool_steps=int(ollama_raw.get("max_tool_steps", 8)),
        ),
        models=models,
        routing=RoutingConfig(
            code_specialist_enabled=bool(routing_raw.get("code_specialist_enabled", True)),
            escalate_on_verifier_failure=bool(
                routing_raw.get("escalate_on_verifier_failure", True)
            ),
            escalate_on_tool_error=bool(routing_raw.get("escalate_on_tool_error", True)),
            max_attempts=max(1, int(routing_raw.get("max_attempts", 3))),
            simple_word_limit=int(routing_raw.get("simple_word_limit", 80)),
            complex_word_limit=int(routing_raw.get("complex_word_limit", 220)),
        ),
        policy=PolicyConfig(
            approval_mode=str(policy_raw.get("approval_mode", "prompt")),
            max_file_bytes=int(policy_raw.get("max_file_bytes", 1_048_576)),
            max_tool_output_chars=int(policy_raw.get("max_tool_output_chars", 16_000)),
            command_timeout_seconds=int(policy_raw.get("command_timeout_seconds", 120)),
            allow_hidden_paths=bool(policy_raw.get("allow_hidden_paths", False)),
            allowed_executables=tuple(
                str(value) for value in policy_raw.get("allowed_executables", [])
            ),
        ),
        verification=VerificationConfig(
            run_unit_tests=bool(verification_raw.get("run_unit_tests", False)),
            unit_test_command=tuple(
                str(value)
                for value in verification_raw.get(
                    "unit_test_command", ["python", "-m", "unittest", "discover", "-s", "tests"]
                )
            ),
            use_shellcheck_when_available=bool(
                verification_raw.get("use_shellcheck_when_available", True)
            ),
        ),
        metrics=MetricsConfig(
            path=Path(str(metrics_raw.get("path", "~/.local/state/epi13-local-harness/metrics.sqlite3"))).expanduser(),
            store_prompt_text=bool(metrics_raw.get("store_prompt_text", False)),
        ),
    )
