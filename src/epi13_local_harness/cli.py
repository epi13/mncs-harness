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
from .commons import CommonsError, CommonsSession
from .commons_operator import CommonsOperatorService
from .config import bundled_evals_path, default_config_path, initialize_config, load_config
from .evals import evaluate_routes, load_cases
from .fabric_inventory_session import InventoryAwareFabricSession
from .fleet import FleetService
from .metrics import MetricsStore
from .models import RoutingOverride
from .router import plan_route
from .semantic_router import (
    SemanticRouterError,
    prepare_router,
    router_status,
)
from .verifiers import Verifier


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _add_routing_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--role", help="Force a configured semantic role")
    parser.add_argument(
        "--model",
        dest="legacy_role",
        help="Legacy alias for --role (configured role, not an exact model tag)",
    )
    parser.add_argument("--worker", help="Pin an exact current Fabric worker")
    parser.add_argument("--model-name", help="Pin an exact installed model tag")
    parser.add_argument(
        "--allow-fallback",
        action="store_true",
        help="Explicitly permit AUTO fallback when a manual worker/model route fails",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="elh",
        description="Route local AI tasks through policy-aware model tiers.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=_path, help="Path to a TOML configuration file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a user configuration")
    init_parser.add_argument("--force", action="store_true", help="Replace an existing config")
    init_parser.add_argument("--path", type=_path, help="Destination path")

    doctor_parser = subparsers.add_parser(
        "doctor", help="Check router, controller, Fabric, Commons, models, and tools"
    )
    doctor_parser.add_argument("--json", action="store_true")
    models_parser = subparsers.add_parser(
        "models", help="Show controller-local and per-worker model state"
    )
    models_parser.add_argument("--worker", help="Limit output to one Fabric worker")
    models_parser.add_argument("--json", action="store_true")

    router_parser = subparsers.add_parser(
        "router",
        help="Inspect or prepare the semantic prompt router",
    )
    router_parser.add_argument("action", choices=("status", "prepare"))

    pull_parser = subparsers.add_parser("pull", help="Pull one or all Ollama models")
    pull_parser.add_argument("--role", action="append", help="Configured role to pull")

    route_parser = subparsers.add_parser("route", help="Preview hybrid routing")
    route_parser.add_argument("task", nargs="?", help="Task text; reads stdin when omitted")
    route_parser.add_argument("--image", action="append", type=_path, default=[])
    _add_routing_arguments(route_parser)

    ask_parser = subparsers.add_parser("ask", help="Run one routed agent task")
    ask_parser.add_argument("task", nargs="?", help="Task text; reads stdin when omitted")
    ask_parser.add_argument("--workspace", type=_path, default=Path.cwd())
    ask_parser.add_argument("--image", action="append", type=_path, default=[])
    _add_routing_arguments(ask_parser)
    ask_parser.add_argument(
        "--yes",
        action="store_true",
        help="Auto-approve policy-allowed writes and commands; blocked actions remain blocked",
    )
    ask_parser.add_argument("--verbose", action="store_true")

    chat_parser = subparsers.add_parser("chat", help="Start a routed terminal session")
    chat_parser.add_argument("--workspace", type=_path, default=Path.cwd())
    _add_routing_arguments(chat_parser)
    chat_parser.add_argument("--yes", action="store_true")

    verify_parser = subparsers.add_parser("verify", help="Run deterministic file verifiers")
    verify_parser.add_argument("paths", nargs="*", type=_path, default=[Path.cwd()])
    verify_parser.add_argument("--workspace", type=_path, default=Path.cwd())

    eval_parser = subparsers.add_parser("eval", help="Evaluate routing cases")
    eval_parser.add_argument("--file", type=_path, help="JSONL evaluation file")

    metrics_parser = subparsers.add_parser("metrics", help="Show recent model attempts")
    metrics_parser.add_argument("--limit", type=int, default=20)

    fabric_parser = subparsers.add_parser("fabric", help="Inspect the registered Fabric fleet")
    fabric_sub = fabric_parser.add_subparsers(dest="fabric_command", required=True)
    fabric_workers = fabric_sub.add_parser("workers", help="List every known Fabric worker")
    fabric_workers.add_argument("--json", action="store_true")
    fabric_refresh = fabric_sub.add_parser("refresh", help="Refresh worker/model observations")
    fabric_refresh.add_argument("--json", action="store_true")
    fabric_registry = fabric_sub.add_parser("registry", help="Inspect or migrate worker registry")
    registry_sub = fabric_registry.add_subparsers(dest="registry_command", required=True)
    registry_list = registry_sub.add_parser("list")
    registry_list.add_argument("--path", type=_path)
    registry_list.add_argument("--json", action="store_true")
    registry_validate = registry_sub.add_parser("validate")
    registry_validate.add_argument("--path", type=_path)
    registry_validate.add_argument("--json", action="store_true")
    registry_import = registry_sub.add_parser(
        "import-config", help="Import complete explicit Harness worker tables"
    )
    registry_import.add_argument("--path", type=_path)
    registry_import.add_argument("--json", action="store_true")

    residency_parser = subparsers.add_parser(
        "residency", help="Inspect or reconcile resident worker models"
    )
    residency_sub = residency_parser.add_subparsers(dest="residency_command", required=True)
    residency_status = residency_sub.add_parser("status")
    residency_status.add_argument("--json", action="store_true")
    residency_warm = residency_sub.add_parser("warm")
    residency_warm.add_argument("worker")
    residency_warm.add_argument("--json", action="store_true")

    commons_parser = subparsers.add_parser(
        "commons", help="Browse controller-local Commons without a model"
    )
    commons_sub = commons_parser.add_subparsers(dest="commons_command", required=True)
    for name in ("status", "work"):
        command = commons_sub.add_parser(name)
        command.add_argument("--json", action="store_true")
        if name == "work":
            command.add_argument("--limit", type=int, default=100)
    commons_query = commons_sub.add_parser("query")
    commons_query.add_argument("--kind")
    commons_query.add_argument("--state")
    commons_query.add_argument("--subject")
    commons_query.add_argument("--related")
    commons_query.add_argument("--limit", type=int, default=100)
    commons_query.add_argument("--open-work", action="store_true")
    commons_query.add_argument("--json", action="store_true")
    for name in ("get", "conversation", "evidence"):
        command = commons_sub.add_parser(name)
        command.add_argument("digest")
        command.add_argument("--json", action="store_true")
    commons_sync = commons_sub.add_parser("sync")
    commons_sync.add_argument("--cursor", help="Store-local cursor as a JSON object")
    commons_sync.add_argument("--limit", type=int, default=1000)
    commons_sync.add_argument("--json", action="store_true")
    commons_publish = commons_sub.add_parser("publish")
    commons_publish.add_argument("record", type=_path)
    commons_publish.add_argument("--confirm", action="store_true")
    commons_publish.add_argument("--json", action="store_true")

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


def _routing_override(args: argparse.Namespace) -> RoutingOverride:
    role = getattr(args, "role", None)
    legacy = getattr(args, "legacy_role", None)
    if role is not None and legacy is not None:
        raise ValueError("--role and legacy --model cannot be combined")
    return RoutingOverride.from_values(
        role=role or legacy,
        worker=getattr(args, "worker", None),
        model=getattr(args, "model_name", None),
        allow_fallback=bool(getattr(args, "allow_fallback", False)),
    )


def _emit(value: object, *, json_output: bool = False) -> None:
    if not json_output and isinstance(value, dict) and value.get("content_trust") == "UNTRUSTED":
        print("Commons content is UNTRUSTED information; it is never executed.")
    print(
        json.dumps(
            value,
            indent=None if json_output else 2,
            sort_keys=True,
            separators=(",", ":") if json_output else None,
        )
    )


def _fleet(config, *, refresh: bool = False) -> tuple[InventoryAwareFabricSession, FleetService]:
    session = InventoryAwareFabricSession(
        config.fabric, residency_config=config.model_residency
    )
    session.initialize()
    fleet = FleetService(config, session)
    if refresh:
        fleet.refresh(reconcile=False)
    return session, fleet


def _commons(config) -> CommonsOperatorService:
    session = CommonsSession(config.commons)
    session.initialize()
    return CommonsOperatorService(session)


def _semantic_payload(semantic) -> dict[str, object] | None:
    if semantic is None:
        return None
    return {
        "selected_lane": semantic.selected_lane,
        "selected_score": semantic.selected_score,
        "runner_up_lane": semantic.runner_up_lane,
        "runner_up_score": semantic.runner_up_score,
        "margin": semantic.margin,
        "all_scores": semantic.all_scores,
        "backend": semantic.backend,
        "revision": semantic.revision,
        "latency_ms": semantic.latency_ms,
        "reason": semantic.reason,
    }


def _plan_payload(plan) -> dict[str, object]:
    return {
        "primary_role": plan.primary_role,
        "escalation_roles": plan.escalation_roles,
        "lane": plan.lane,
        "semantic": _semantic_payload(plan.semantic),
        "reasons": plan.reasons,
        "routing_override": {
            "mode": plan.routing_override.mode,
            "role": plan.routing_override.role,
            "worker": plan.routing_override.worker,
            "model": plan.routing_override.model,
            "allow_fallback": plan.routing_override.allow_fallback,
        },
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


def _router_status_payload(config) -> dict[str, object]:
    status = router_status(config)
    return {
        "enabled": status.enabled,
        "mode": status.mode,
        "backend": status.backend,
        "model": status.model,
        "revision": status.revision,
        "device": status.device,
        "local_files_only": status.local_files_only,
        "cache_directory": str(status.cache_directory),
        "missing_dependencies": status.missing_dependencies,
        "cached": status.cached,
        "active": status.active,
        "state": status.state,
        "detail": status.detail,
    }


def _print_router_status(config) -> None:
    status = router_status(config)
    print("Semantic router:")
    print(f"  state:      {status.state}")
    print(f"  enabled:    {status.enabled}")
    print(f"  mode:       {status.mode}")
    print(f"  backend:    {status.backend}")
    print(f"  model:      {status.model or '(not configured)'}")
    print(f"  revision:   {status.revision or '(not configured)'}")
    print(f"  device:     {status.device}")
    print(f"  cached:     {status.cached}")
    print(f"  active:     {status.active}")
    print(f"  cache:      {status.cache_directory}")
    if status.missing_dependencies:
        print("  missing:    " + ", ".join(status.missing_dependencies))
    if status.detail:
        print(f"  detail:     {status.detail}")


def cmd_init(args: argparse.Namespace) -> int:
    destination = initialize_config(args.path, args.force)
    print(destination)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    config_path = args.config or default_config_path()
    commons_service = _commons(config)
    commons_status = commons_service.status()
    session, fleet = _fleet(config)
    snapshot = fleet.snapshot()
    route_availability: list[dict[str, object]] = []
    failures = 0
    if commons_status["enabled"] and not commons_status["ready"]:
        failures += 1
    router = router_status(config)
    if router.enabled and router.state in {
        "unsupported",
        "unpinned",
        "missing-dependencies",
    }:
        failures += 1
    if router.enabled and router.local_files_only and router.state == "not-cached":
        failures += 1
    local_names = {
        str(item.get("name") or item.get("model"))
        for item in snapshot["controller"]["installed_models"]
    }
    for role, model in config.models.items():
        if model.provider == "fabric" and config.fabric.enabled:
            _effective, selection = session.resolve_model(role, model)
            available = bool(selection and selection.available)
            route_availability.append(
                {
                    "role": role,
                    "provider": "fabric",
                    "configured_model": model.name,
                    "resolved_model": selection.selected_model if selection else None,
                    "worker": selection.worker_id if selection else None,
                    "available": available,
                    "reason": selection.reason if selection else "no Fabric selection",
                }
            )
        else:
            available = model.name in local_names
            route_availability.append(
                {
                    "role": role,
                    "provider": "controller-ollama",
                    "configured_model": model.name,
                    "resolved_model": model.name,
                    "worker": "controller",
                    "available": available,
                    "reason": "controller-local installed inventory",
                }
            )
        failures += int(not available)

    tools = {
        executable: shutil.which(executable)
        for executable in ("git", "bash", "shellcheck", "ruff", "pytest")
    }
    try:
        store = MetricsStore(config.metrics.path, config.metrics.store_prompt_text)
        store.recent(1)
        metrics = "writable"
    except OSError as exc:
        metrics = f"ERROR: {exc}"
        failures += 1
    payload = {
        "outcome": "PASS" if not failures else "UNKNOWN",
        "python": sys.version.split()[0],
        "config": str(config_path),
        "metrics": {"path": str(config.metrics.path), "status": metrics},
        "router": _router_status_payload(config),
        "commons": commons_status,
        "fleet": snapshot,
        "role_availability": route_availability,
        "tools": tools,
    }
    if args.json:
        _emit(payload, json_output=True)
    else:
        print(f"Python: {payload['python']}")
        print(f"Config: {config_path}")
        _print_router_status(config)
        print(
            f"Commons: {commons_status['code']} profile={commons_status['profile']} "
            f"records={commons_status['record_count']}"
        )
        controller = snapshot["controller"]
        print(
            f"Controller: generation={controller['generation_policy']} "
            f"loaded={','.join(controller['loaded_generation_models']) or 'none'}"
        )
        for worker in snapshot["fabric"]["workers"]:
            print(
                f"Fabric {worker['worker_id']}: {worker.get('availability', 'UNKNOWN')} "
                f"version={worker.get('worker_service_version') or 'UNKNOWN'} "
                f"installed={worker.get('installed_model_count', 0)} "
                f"loaded={','.join(worker.get('loaded_model_names', [])) or 'none'}"
            )
        for item in route_availability:
            print(
                f"Role {item['role']:8} {item['provider']:18} "
                f"{item['resolved_model'] or item['configured_model']} "
                f"worker={item['worker'] or 'unresolved'} "
                f"{'available' if item['available'] else 'unavailable'}"
            )
        for executable, location in tools.items():
            print(f"Tool {executable:10} {location or 'not installed (optional)'}")
        print(f"Metrics database: {metrics}")
    return 1 if failures else 0


def cmd_models(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    _session, fleet = _fleet(config)
    snapshot = fleet.snapshot()
    if args.worker:
        workers = [
            worker
            for worker in snapshot["fabric"]["workers"]
            if worker.get("worker_id") == args.worker
        ]
        if not workers:
            raise ValueError(f"unknown Fabric worker: {args.worker}")
        payload = {"controller": None, "workers": workers}
    else:
        payload = {
            "controller": snapshot["controller"],
            "workers": snapshot["fabric"]["workers"],
            "role_policy": [
                {
                    "role": role,
                    "provider": model.provider,
                    "model": model.name,
                    "keep_alive": model.keep_alive,
                }
                for role, model in config.models.items()
            ],
        }
    if args.json:
        _emit(payload, json_output=True)
        return 0
    if payload["controller"] is not None:
        controller = payload["controller"]
        print(
            f"Controller ({controller['generation_policy']}): "
            f"loaded={','.join(controller['loaded_generation_models']) or 'none'}"
        )
        for model in controller["installed_models"]:
            print(f"  {'●' if model.get('loaded') else '○'} {model.get('name') or model.get('model')}")
    for worker in payload["workers"]:
        print(
            f"{worker['worker_id']} {worker.get('availability', 'UNKNOWN')} "
            f"version={worker.get('worker_service_version') or 'UNKNOWN'}"
        )
        for model in worker.get("model_inventory", []):
            print(f"  {'●' if model.get('loaded') else '○'} {model.get('name') or model.get('model')}")
    return 0


def cmd_router(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.action == "status":
        print(json.dumps(_router_status_payload(config), indent=2))
        return 0
    result = prepare_router(config)
    print(json.dumps(_semantic_payload(result), indent=2))
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    roles = args.role or list(config.models)
    unknown = [role for role in roles if role not in config.models]
    if unknown:
        print(f"Unknown roles: {', '.join(unknown)}", file=sys.stderr)
        return 2
    remote_roles = [role for role in roles if config.models[role].provider == "fabric"]
    if remote_roles:
        print(
            "Refusing to pull Fabric-backed roles on the controller: "
            + ", ".join(remote_roles)
            + ". Install models on the intended worker, then use `elh residency warm`.",
            file=sys.stderr,
        )
        return 2
    if config.controller.generation_policy == "router-only":
        print(
            "Controller generation policy is router-only; local generation pulls are denied.",
            file=sys.stderr,
        )
        return 2
    if not shutil.which("ollama"):
        print("The controller-local ollama executable is not on PATH", file=sys.stderr)
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
    requested = _routing_override(args)
    plan = plan_route(task, config, images, routing_override=requested)
    payload = _plan_payload(plan)
    model = config.models[plan.primary_role]
    if model.provider == "fabric":
        session, _fleet_service = _fleet(config)
        effective, selection = session.resolve_model(
            plan.primary_role, model, routing_override=requested
        )
        payload["resolved_route"] = {
            "provider": "fabric",
            "worker": selection.worker_id if selection else None,
            "model": effective.name,
            "available": selection.available if selection else False,
            "loaded": selection.loaded if selection else False,
            "resident": selection.resident if selection else False,
            "fallback": requested.allow_fallback,
            "reason": selection.reason if selection else "Fabric inventory unavailable",
        }
    else:
        payload["resolved_route"] = {
            "provider": "controller-ollama",
            "worker": "controller",
            "model": model.name,
            "available": config.controller.generation_policy != "router-only",
            "loaded": False,
            "resident": False,
            "fallback": requested.allow_fallback,
            "reason": f"controller policy is {config.controller.generation_policy}",
        }
    print(json.dumps(payload, indent=2))
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
        routing_override=_routing_override(args),
        auto_approve=args.yes,
    )
    _print_attempts(result, args.verbose)
    print(result.final_content)
    return 0 if result.successful else 1


def cmd_chat(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    workspace = args.workspace.resolve()
    agent = LocalAgent(config)
    routing_override = _routing_override(args)
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
            routing_override=routing_override,
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
        route_detail = (
            f" lane={row['semantic_lane']}"
            if row.get("semantic_lane")
            else ""
        )
        print(
            f"{row['created_at']} {row['role']:8} {row['model']:18} "
            f"{'pass' if row['passed'] else 'fail'} tools={row['tool_call_count']} "
            f"tokens={tokens} tok/s={rate:.2f}{route_detail} "
            f"task={row['task_sha256'][:10]}"
        )
        if row.get("error"):
            print(f"  error: {row['error']}")
    return 0


def _registry_path(config, value: Path | None) -> Path:
    configured = value or config.fabric.registry_path
    if configured is None:
        return Path("~/.local/state/mncs-fabric/workers.json").expanduser()
    return configured.expanduser()


def cmd_fabric(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.fabric_command in {"workers", "refresh"}:
        _session, fleet = _fleet(config, refresh=args.fabric_command == "refresh")
        payload = fleet.snapshot()["fabric"]
        _emit(payload, json_output=args.json)
        return 0

    from mncs_fabric import RegistryWorker, WorkerRegistry

    path = _registry_path(config, args.path)
    registry = WorkerRegistry(path, config.fabric.controller_id)
    if args.registry_command == "list":
        payload = {
            "outcome": "PASS",
            "path": str(path),
            "workers": [worker.public_dict() for worker in registry.load()],
        }
    elif args.registry_command == "validate":
        payload = {"path": str(path), **registry.validate()}
    elif args.registry_command == "import-config":
        results: list[dict[str, object]] = []
        existing = {worker.worker_id: worker for worker in registry.load()}
        for worker in config.fabric.workers:
            required = (
                worker.host,
                worker.port,
                worker.ca_file,
                worker.client_certificate,
                worker.client_key,
                worker.trust_state,
            )
            if any(value is None for value in required):
                results.append(
                    {
                        "worker_id": worker.worker_id,
                        "outcome": "UNKNOWN",
                        "code": "REGISTRY_IMPORT_INCOMPLETE",
                        "detail": "explicit worker has incomplete endpoint/trust references",
                    }
                )
                continue
            candidate = RegistryWorker(
                worker_id=worker.worker_id,
                host=str(worker.host),
                port=int(worker.port),
                capabilities=worker.capabilities,
                ca_file=str(worker.ca_file),
                client_certificate=str(worker.client_certificate),
                client_key=str(worker.client_key),
                trust_state=str(worker.trust_state),
                concurrency_limit=worker.concurrency_limit,
                timeout=worker.timeout_seconds,
                connect_timeout=worker.connect_timeout_seconds,
                control_timeout=worker.control_timeout_seconds,
                execution_timeout_overhead=worker.execution_timeout_overhead_seconds,
            )
            if worker.worker_id in existing:
                results.append(registry.update(candidate))
            else:
                results.append(registry.register(candidate))
        payload = {
            "path": str(path),
            "outcome": (
                "PASS" if results and all(item.get("outcome") == "PASS" for item in results)
                else "UNKNOWN"
            ),
            "results": results,
        }
    else:
        raise AssertionError("unreachable registry command")
    _emit(payload, json_output=args.json)
    return 0 if payload.get("outcome") == "PASS" else 1


def cmd_residency(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    session, fleet = _fleet(config, refresh=True)
    if args.residency_command == "status":
        payload = fleet.snapshot()["residency"]
    else:
        payload = {
            "outcome": "PASS",
            "worker": args.worker,
            "results": list(fleet.residency.reconcile(force_worker=args.worker)),
        }
        outcomes = {item.get("outcome") for item in payload["results"]}
        if outcomes != {"PASS"}:
            payload["outcome"] = "UNKNOWN"
        session.refresh_model_inventory()
        payload["fleet"] = fleet.snapshot()["fabric"]
    _emit(payload, json_output=args.json)
    return 0 if payload.get("outcome", "PASS") == "PASS" else 1


def cmd_commons(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    service = _commons(config)
    command = args.commons_command
    if command == "status":
        payload = service.status()
    elif command == "work":
        payload = service.work(limit=args.limit)
    elif command == "query":
        payload = service.query(
            kind=args.kind,
            state=args.state,
            subject=args.subject,
            related=args.related,
            limit=args.limit,
            open_work=True if args.open_work else None,
        )
    elif command == "get":
        payload = service.get(args.digest)
    elif command == "conversation":
        payload = service.conversation(args.digest)
    elif command == "evidence":
        payload = service.evidence(args.digest)
    elif command == "sync":
        cursor = json.loads(args.cursor) if args.cursor else None
        if cursor is not None and not isinstance(cursor, dict):
            raise ValueError("--cursor must decode to a JSON object")
        payload = service.sync(cursor=cursor, limit=args.limit)
    elif command == "publish":
        if not args.confirm:
            raise ValueError("operator publication requires --confirm")
        try:
            record = json.loads(args.record.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"record file is not valid JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise ValueError("record file must contain one JSON object")
        payload = service.publish(record)
    else:
        raise AssertionError("unreachable Commons command")
    _emit(payload, json_output=args.json)
    outcome = payload.get("outcome")
    if outcome is None:
        return 0 if payload.get("ready") else 1
    return 0 if outcome == "PASS" else 1


COMMANDS = {
    "init": cmd_init,
    "doctor": cmd_doctor,
    "models": cmd_models,
    "router": cmd_router,
    "pull": cmd_pull,
    "route": cmd_route,
    "ask": cmd_ask,
    "chat": cmd_chat,
    "verify": cmd_verify,
    "eval": cmd_eval,
    "metrics": cmd_metrics,
    "fabric": cmd_fabric,
    "residency": cmd_residency,
    "commons": cmd_commons,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (
        ValueError,
        FileExistsError,
        OSError,
        CommonsError,
        SemanticRouterError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
