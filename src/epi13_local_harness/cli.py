from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .agent import LocalAgent
from .config import bundled_evals_path, default_config_path, initialize_config, load_config
from .evals import evaluate_routes, load_cases
from .metrics import MetricsStore
from .ollama import OllamaClient, OllamaError
from .router import plan_route
from .verifiers import Verifier


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="elh",
        description="Route local AI tasks through policy-aware Ollama model tiers.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=_path, help="Path to a TOML configuration file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a user configuration")
    init_parser.add_argument("--force", action="store_true", help="Replace an existing config")
    init_parser.add_argument("--path", type=_path, help="Destination path")

    subparsers.add_parser("doctor", help="Check Ollama, model tags, and optional tools")
    subparsers.add_parser("models", help="Show configured and locally installed models")

    pull_parser = subparsers.add_parser("pull", help="Pull one or all configured Ollama models")
    pull_parser.add_argument("--role", action="append", help="Configured role to pull")

    route_parser = subparsers.add_parser("route", help="Preview deterministic routing")
    route_parser.add_argument("task", nargs="?", help="Task text; reads stdin when omitted")
    route_parser.add_argument("--image", action="append", type=_path, default=[])
    route_parser.add_argument("--model", help="Force a configured model role")

    ask_parser = subparsers.add_parser("ask", help="Run one routed agent task")
    ask_parser.add_argument("task", nargs="?", help="Task text; reads stdin when omitted")
    ask_parser.add_argument("--workspace", type=_path, default=Path.cwd())
    ask_parser.add_argument("--image", action="append", type=_path, default=[])
    ask_parser.add_argument("--model", help="Force a configured model role")
    ask_parser.add_argument(
        "--yes",
        action="store_true",
        help="Auto-approve policy-allowed writes and commands; blocked actions remain blocked",
    )
    ask_parser.add_argument("--verbose", action="store_true")

    chat_parser = subparsers.add_parser("chat", help="Start a simple routed terminal session")
    chat_parser.add_argument("--workspace", type=_path, default=Path.cwd())
    chat_parser.add_argument("--model", help="Force a configured model role")
    chat_parser.add_argument("--yes", action="store_true")

    verify_parser = subparsers.add_parser("verify", help="Run deterministic file verifiers")
    verify_parser.add_argument("paths", nargs="*", type=_path, default=[Path.cwd()])
    verify_parser.add_argument("--workspace", type=_path, default=Path.cwd())

    eval_parser = subparsers.add_parser("eval", help="Evaluate deterministic routing cases")
    eval_parser.add_argument("--file", type=_path, help="JSONL evaluation file")

    metrics_parser = subparsers.add_parser("metrics", help="Show recent model attempts")
    metrics_parser.add_argument("--limit", type=int, default=20)

    return parser


def _task_text(value: str | None) -> str:
    if value:
        return value
    if sys.stdin.isatty():
        raise ValueError("Provide a task argument or pipe task text on stdin")
    text = sys.stdin.read().strip()
    if not text:
        raise ValueError("Task text is empty")
    return text


def _validate_images(images: list[Path]) -> list[Path]:
    result: list[Path] = []
    for image in images:
        resolved = image.resolve()
        if not resolved.is_file():
            raise ValueError(f"Image does not exist: {image}")
        result.append(resolved)
    return result


def _plan_payload(plan) -> dict[str, object]:
    return {
        "primary_role": plan.primary_role,
        "escalation_roles": plan.escalation_roles,
        "reasons": plan.reasons,
        "profile": {
            "word_count": plan.profile.word_count,
            "has_code": plan.profile.has_code,
            "asks_for_edit": plan.profile.asks_for_edit,
            "asks_for_execution": plan.profile.asks_for_execution,
            "asks_for_explanation": plan.profile.asks_for_explanation,
            "is_high_risk": plan.profile.is_high_risk,
            "is_complex": plan.profile.is_complex,
            "has_image": plan.profile.has_image,
            "file_reference_count": plan.profile.file_reference_count,
        },
    }


def cmd_init(args: argparse.Namespace) -> int:
    destination = initialize_config(args.path, args.force)
    print(destination)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print(f"Python: {sys.version.split()[0]}")
    config_path = args.config or default_config_path()
    config_source = "(user)" if config_path.exists() else "(bundled defaults)"
    print(f"Config: {config_path} {config_source}")
    print(f"Metrics: {config.metrics.path}")
    failures = 0
    client = OllamaClient(config.ollama)
    try:
        version = client.version()
        installed = client.model_names()
        print(f"Ollama: {version} at {config.ollama.base_url}")
        for role, model in config.models.items():
            present = model.name in installed
            print(f"  {role:8} {model.name:18} {'installed' if present else 'missing'}")
            failures += int(not present)
    except OllamaError as exc:
        print(f"Ollama: ERROR: {exc}")
        failures += 1

    for executable in ("git", "bash", "shellcheck", "ruff", "pytest"):
        location = shutil.which(executable)
        print(f"Tool {executable:10} {location or 'not installed (optional)'}")
    try:
        store = MetricsStore(config.metrics.path, config.metrics.store_prompt_text)
        store.recent(1)
        print("Metrics database: writable")
    except OSError as exc:
        print(f"Metrics database: ERROR: {exc}")
        failures += 1
    return 1 if failures else 0


def cmd_models(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    client = OllamaClient(config.ollama)
    try:
        installed = client.model_names()
    except OllamaError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for role, model in config.models.items():
        print(
            f"{role:8} {model.name:18} ctx={model.num_ctx:<6} keep={str(model.keep_alive):<4} "
            f"{'installed' if model.name in installed else 'missing'}"
        )
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    roles = args.role or list(config.models)
    unknown = [role for role in roles if role not in config.models]
    if unknown:
        print(f"Unknown roles: {', '.join(unknown)}", file=sys.stderr)
        return 2
    if not shutil.which("ollama"):
        print("The ollama executable is not on PATH", file=sys.stderr)
        return 1
    for role in roles:
        model = config.models[role].name
        print(f"Pulling {role}: {model}")
        completed = subprocess.run(["ollama", "pull", model], check=False)
        if completed.returncode:
            return completed.returncode
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    task = _task_text(args.task)
    images = _validate_images(args.image)
    plan = plan_route(task, config, images, args.model)
    print(json.dumps(_plan_payload(plan), indent=2))
    return 0


def _print_attempts(result, verbose: bool) -> None:
    print(f"Route: {' -> '.join(result.route.all_roles)}", file=sys.stderr)
    for attempt in result.attempts:
        status = "passed" if attempt.verification.passed else "failed"
        print(
            f"Attempt {attempt.role} ({attempt.model}): {status}; "
            f"tools={len(attempt.tool_executions)}",
            file=sys.stderr,
        )
        if verbose:
            for check in attempt.verification.checks:
                print(f"  CHECK {check}", file=sys.stderr)
            for failure in attempt.verification.failures:
                print(f"  FAIL  {failure}", file=sys.stderr)


def cmd_ask(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    task = _task_text(args.task)
    workspace = args.workspace.resolve()
    if not workspace.is_dir():
        raise ValueError(f"Workspace is not a directory: {workspace}")
    images = _validate_images(args.image)
    result = LocalAgent(config).run(
        task,
        workspace=workspace,
        images=images,
        forced_role=args.model,
        auto_approve=args.yes,
    )
    _print_attempts(result, args.verbose)
    print(result.final_content)
    return 0 if result.successful else 1


def cmd_chat(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    workspace = args.workspace.resolve()
    agent = LocalAgent(config)
    print("Local harness chat. Each message is routed independently. Type /quit to exit.")
    while True:
        try:
            task = input("elh> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if task in {"/quit", "/exit"}:
            return 0
        if not task:
            continue
        result = agent.run(
            task,
            workspace=workspace,
            forced_role=args.model,
            auto_approve=args.yes,
        )
        _print_attempts(result, False)
        print(result.final_content)


def cmd_verify(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    workspace = args.workspace.resolve()
    paths = [(path if path.is_absolute() else workspace / path).resolve() for path in args.paths]
    result = Verifier(workspace, config.verification).verify(paths)
    for check in result.checks:
        print(f"PASS {check}")
    for failure in result.failures:
        print(f"FAIL {failure}")
    return 0 if result.passed else 1


def cmd_eval(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    cases = load_cases(args.file or bundled_evals_path())
    passed, failures = evaluate_routes(cases, config)
    print(f"Routing evaluations: {passed}/{len(cases)} passed")
    for failure in failures:
        print(f"FAIL {failure}")
    return 0 if not failures else 1


def cmd_metrics(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    rows = MetricsStore(config.metrics.path, config.metrics.store_prompt_text).recent(args.limit)
    if not rows:
        print("No model attempts recorded.")
        return 0
    for row in rows:
        duration = row.get("eval_duration_ns") or 0
        tokens = row.get("eval_count") or 0
        rate = (tokens / (duration / 1_000_000_000)) if duration else 0
        print(
            f"{row['created_at']} {row['role']:8} {row['model']:18} "
            f"{'pass' if row['passed'] else 'fail'} tools={row['tool_call_count']} "
            f"tokens={tokens} tok/s={rate:.2f} task={row['task_sha256'][:10]}"
        )
        if row.get("error"):
            print(f"  error: {row['error']}")
    return 0


COMMANDS = {
    "init": cmd_init,
    "doctor": cmd_doctor,
    "models": cmd_models,
    "pull": cmd_pull,
    "route": cmd_route,
    "ask": cmd_ask,
    "chat": cmd_chat,
    "verify": cmd_verify,
    "eval": cmd_eval,
    "metrics": cmd_metrics,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (ValueError, FileExistsError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
