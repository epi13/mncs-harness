from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Sequence

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
from .verifiers import Verifier


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _add_routing_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--role",
        help="Force a configured role; with --worker and --model-name, keep exact pins",
    )
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
    subparsers.add_parser(
        "install-cli",
        help="Install relocatable elh wrappers that do not embed a host Python shebang",
    )

    doctor_parser = subparsers.add_parser(
        "doctor", help="Check router, controller, Fabric, Commons, models, and tools"
    )
    doctor_parser.add_argument("--json", action="store_true")
    readiness_parser = subparsers.add_parser(
        "experiment-readiness",
        help="Inspect whether this stack may start experiments without repairing it",
    )
    readiness_parser.add_argument("--json", action="store_true")
    readiness_parser.add_argument(
        "--profile",
        default="base-inference",
        choices=(
            "base-inference",
            "code-analysis",
            "multi-agent",
            "sustained-experiment",
            "MNEL",
            "RAVEL",
        ),
    )
    models_parser = subparsers.add_parser(
        "models", help="Show controller-local and per-worker model state"
    )
    models_parser.add_argument("--worker", help="Limit output to one Fabric worker")
    models_parser.add_argument("--json", action="store_true")

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

    submit_parser = subparsers.add_parser(
        "submit",
        help="Accept exact-pin inference as detached persistent Fabric work and return immediately",
    )
    submit_parser.add_argument("task", nargs="?", help="Task text; reads stdin when omitted")
    _add_routing_arguments(submit_parser)
    submit_parser.add_argument("--idempotency-key")
    submit_parser.add_argument("--json", action="store_true")

    work_parser = subparsers.add_parser("work", help="Inspect detached persistent Fabric inference work")
    work_sub = work_parser.add_subparsers(dest="work_command", required=True)
    work_status = work_sub.add_parser("status")
    work_status.add_argument("work_id")
    work_status.add_argument("--json", action="store_true")
    work_result = work_sub.add_parser("result")
    work_result.add_argument("work_id")
    work_result.add_argument("--json", action="store_true")

    chat_parser = subparsers.add_parser("chat", help="Start a routed terminal session")
    chat_parser.add_argument("--workspace", type=_path, default=Path.cwd())
    _add_routing_arguments(chat_parser)
    chat_parser.add_argument("--yes", action="store_true")

    verify_parser = subparsers.add_parser("verify", help="Run deterministic file verifiers")
    verify_parser.add_argument("paths", nargs="*", type=_path, default=[Path.cwd()])
    verify_parser.add_argument("--workspace", type=_path, default=Path.cwd())
    verify_models = subparsers.add_parser(
        "verify-models",
        help="Run generic verification probes against Fabric-discovered models",
    )
    verify_models.add_argument("--worker", help="Limit to one worker identity")
    verify_models.add_argument("--model-name", help="Limit to one opaque model tag")
    verify_models.add_argument("--tier", action="append", type=int, choices=(0, 1, 2, 3))
    verify_models.add_argument("--persist", action="store_true", help="Append evidence records")
    verify_models.add_argument("--json", action="store_true")

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
    residency_warm.add_argument("--model")
    residency_warm.add_argument("--json", action="store_true")
    residency_release = residency_sub.add_parser(
        "release", help="Release one exact worker-local model and verify provider state"
    )
    residency_release.add_argument("worker")
    residency_release.add_argument("model")
    residency_release.add_argument("--json", action="store_true")

    commons_parser = subparsers.add_parser(
        "commons", help="Browse controller-local Commons without a model"
    )
    commons_sub = commons_parser.add_subparsers(dest="commons_command", required=True)
    for name in ("status", "work", "opportunities"):
        command = commons_sub.add_parser(name)
        command.add_argument("--json", action="store_true")
        if name in {"work", "opportunities"}:
            command.add_argument("--limit", type=int, default=100)
    commons_work_status = commons_sub.add_parser("work-status")
    commons_work_status.add_argument("work_id")
    commons_work_status.add_argument("--json", action="store_true")
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


def _fleet(
    config,
    *,
    refresh: bool = False,
    refresh_inventory: bool = True,
) -> tuple[InventoryAwareFabricSession, FleetService]:
    session = InventoryAwareFabricSession(
        config.fabric, residency_config=config.model_residency
    )
    session.initialize(refresh_inventory=refresh_inventory)
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


def cmd_init(args: argparse.Namespace) -> int:
    from .portable_cli import install_portable_cli

    destination = initialize_config(args.path, args.force)
    print(destination)
    for path in install_portable_cli():
        print(path)
    return 0


def cmd_install_cli(args: argparse.Namespace) -> int:
    del args
    from .portable_cli import install_portable_cli

    for path in install_portable_cli():
        print(path)
    return 0


def _bounded_probe(
    name: str,
    fn: Callable[[], Any],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Run one doctor probe with a real wall-clock bound.

    Thread-pool timeouts cannot interrupt a CPython thread blocked in a
    socket recv that holds progress in another library. SIGALRM can.
    """

    if os.name != "posix":
        try:
            detail = fn()
        except Exception as exc:
            return {"name": name, "status": "ERROR", "detail": str(exc)}
        return {"name": name, "status": "PASS", "detail": detail}

    timed_out = False

    def _alarm(_signum: int, _frame: object) -> None:
        nonlocal timed_out
        timed_out = True
        raise TimeoutError(name)

    previous = signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, float(timeout_seconds))
    try:
        detail = fn()
        return {"name": name, "status": "PASS", "detail": detail}
    except TimeoutError:
        return {
            "name": name,
            "status": "TIMEOUT",
            "detail": f"{name} probe exceeded {timeout_seconds:.0f}s",
        }
    except Exception as exc:
        if timed_out:
            return {
                "name": name,
                "status": "TIMEOUT",
                "detail": f"{name} probe exceeded {timeout_seconds:.0f}s",
            }
        return {"name": name, "status": "ERROR", "detail": str(exc)}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous)


def doctor_outcome(
    subsystems: list[dict[str, Any]],
    route_availability: list[dict[str, Any]],
) -> str:
    """Summarize doctor health without treating optional route gaps as transport failure."""

    core_failed = [
        item
        for item in subsystems
        if item.get("status") != "PASS" and not str(item.get("name", "")).startswith("Worker ")
    ]
    worker_failed = [
        item
        for item in subsystems
        if str(item.get("name", "")).startswith("Worker ") and item.get("status") != "PASS"
    ]
    fabric_roles = [item for item in route_availability if item.get("provider") == "fabric"]
    unavailable_routes = [item for item in fabric_roles if not item.get("available")]
    if any(item.get("status") == "ERROR" for item in core_failed):
        return "ERROR"
    if any(item.get("status") == "TIMEOUT" for item in core_failed):
        return "UNKNOWN"
    if worker_failed or unavailable_routes:
        return "DEGRADED"
    if core_failed:
        return "UNKNOWN"
    return "PASS"


def _doctor_progress(message: str, *, json_output: bool) -> None:
    print(message, file=sys.stderr if json_output else sys.stdout, flush=True)


def cmd_doctor(args: argparse.Namespace) -> int:
    json_output = bool(args.json)
    _doctor_progress("elh doctor: probing subsystems", json_output=json_output)
    config = load_config(args.config)
    config_path = args.config or default_config_path()
    subsystems: list[dict[str, Any]] = []

    def commons_probe() -> dict[str, Any]:
        return _commons(config).status()

    def fabric_probe() -> dict[str, Any]:
        session, fleet = _fleet(config, refresh_inventory=False)
        status = session.status()
        snapshot = fleet.snapshot(status)
        return {
            "state": status.state,
            "execution_transport": status.execution_transport,
            "detail": status.detail,
            "workers": snapshot["fabric"]["workers"],
            "controller": snapshot["controller"],
            "role_availability": fleet.role_availability(snapshot, status=status),
        }

    def ollama_probe() -> dict[str, Any]:
        url = config.ollama.base_url.rstrip("/") + "/api/tags"
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            raise RuntimeError("controller Ollama returned a malformed tags document")
        return {"model_count": len(models)}

    def cli_probe() -> dict[str, Any]:
        from .portable_cli import WRAPPERS, install_portable_cli

        bin_dir = Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin")
        missing = [
            name
            for name in ("mncs-harness", "mncs-harness-tui", "mncs-harness-fabric")
            if not (bin_dir / name).exists()
        ]
        repaired: list[str] = []
        if missing:
            repaired = [str(path.name) for path in install_portable_cli(bin_dir)]
        present = {
            name: (bin_dir / name).exists()
            for name in WRAPPERS
        }
        if not present.get("mncs-harness"):
            raise RuntimeError("canonical mncs-harness launcher is still missing after repair")
        return {
            "canonical": "mncs-harness",
            "compatibility": ["elh", "epi13-harness"],
            "present": present,
            "repaired": repaired,
        }

    commons_result = _bounded_probe("Commons", commons_probe, timeout_seconds=8)
    fabric_result = _bounded_probe("Fabric", fabric_probe, timeout_seconds=35)
    ollama_result = _bounded_probe("Ollama", ollama_probe, timeout_seconds=6)
    cli_result = _bounded_probe("CLI", cli_probe, timeout_seconds=4)
    subsystems.extend((commons_result, fabric_result, ollama_result, cli_result))

    commons_status = commons_result.get("detail") if commons_result["status"] == "PASS" else {}
    if not isinstance(commons_status, dict):
        commons_status = {"code": commons_result["status"], "detail": commons_result["detail"]}
    fabric_detail = fabric_result.get("detail") if fabric_result["status"] == "PASS" else {}
    snapshot = {
        "controller": fabric_detail.get("controller", {}) if isinstance(fabric_detail, dict) else {},
        "fabric": {"workers": fabric_detail.get("workers", []) if isinstance(fabric_detail, dict) else []},
    }
    for worker in snapshot["fabric"]["workers"]:
        worker_id = str(worker.get("worker_id") or "unknown-worker")
        availability = str(worker.get("availability") or "UNKNOWN")
        status = "PASS" if availability == "AVAILABLE" else "ERROR"
        subsystems.append(
            {
                "name": f"Worker {worker_id}",
                "status": status,
                "detail": {
                    "availability": availability,
                    "installed_model_count": worker.get("installed_model_count"),
                    "loaded_model_names": worker.get("loaded_model_names"),
                },
            }
        )

    route_availability = (
        list(fabric_detail.get("role_availability") or [])
        if isinstance(fabric_detail, dict)
        else []
    )

    tools = {
        executable: shutil.which(executable)
        for executable in ("git", "bash", "shellcheck", "ruff", "pytest")
    }
    try:
        store = MetricsStore(config.metrics.path, config.metrics.store_prompt_text)
        store.recent(1)
        metrics = "writable"
        subsystems.append({"name": "Metrics", "status": "PASS", "detail": metrics})
    except OSError as exc:
        metrics = f"ERROR: {exc}"
        subsystems.append({"name": "Metrics", "status": "ERROR", "detail": str(exc)})

    outcome = doctor_outcome(subsystems, route_availability)
    failed = outcome not in {"PASS", "DEGRADED"}

    def _public_detail(item: dict[str, Any]) -> Any:
        detail = item.get("detail")
        if item["name"] == "Fabric" and isinstance(detail, dict):
            return {
                "state": detail.get("state"),
                "execution_transport": detail.get("execution_transport"),
                "detail": detail.get("detail"),
            }
        return detail

    payload = {
        "outcome": outcome,
        "python": sys.version.split()[0],
        "config": str(config_path),
        "subsystems": [
            {"name": item["name"], "status": item["status"], "detail": _public_detail(item)}
            for item in subsystems
        ],
        "metrics": {"path": str(config.metrics.path), "status": metrics},
        "routing": {
            "mode": "deterministic",
            "semantic_router": "removed",
            "offline": True,
        },
        "commons": commons_status,
        "fleet": snapshot,
        "role_availability": route_availability,
        "tools": tools,
    }
    if json_output:
        _emit(payload, json_output=True)
    else:
        print(f"Python: {payload['python']}")
        print(f"Config: {config_path}")
        print("Routing: deterministic policy + current Fabric inventory (semantic router removed)")
        for item in subsystems:
            detail = item["detail"]
            if item["name"] == "Fabric" and isinstance(detail, dict):
                summary = f"{detail.get('state')} transport={detail.get('execution_transport')}"
            elif item["name"] == "Commons" and isinstance(detail, dict):
                summary = f"{detail.get('code')} records={detail.get('record_count')}"
            elif item["name"].startswith("Worker ") and isinstance(detail, dict):
                loaded = ",".join(detail.get("loaded_model_names") or []) or "none"
                summary = f"{detail.get('availability')} installed={detail.get('installed_model_count', 0)} loaded={loaded}"
            else:
                summary = detail if isinstance(detail, str) else json.dumps(detail, default=str)
            print(f"{item['name']:<22} {item['status']:<8} {summary}")
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
        print(f"Overall: {outcome}")
    return 1 if failed else 0


def cmd_experiment_readiness(args: argparse.Namespace) -> int:
    from .experiment_readiness import BLOCKED, UNKNOWN, inspect_live_config

    config = load_config(args.config)
    payload = inspect_live_config(config, profile=args.profile)
    if args.json:
        _emit(payload, json_output=True)
    else:
        print(f"Profile: {payload['profile']}")
        print(f"Profile status: {payload.get('profile_status') or payload['status']}")
        print(f"Claim boundary: {payload['claim_boundary']}")
        print(f"Schema: {payload.get('schema')}")
        print(f"Fabric classification: {payload.get('fabric_classification')}")
        print(f"Stack identity: {payload['experiment_stack']['experiment_stack_identity']}")
        print(f"Provenance: {payload['experiment_stack'].get('provenance_status')}")
        for name, layer in payload["layers"].items():
            print(f"{name:<22} {layer['status']:<8} {layer.get('evidence') or ''}")
        for warning in payload.get("optional_warnings") or []:
            print(f"optional warning      {warning.get('layer')}: {warning.get('status')}")
        print(f"Overall: {payload['status']}")
    return 1 if payload["status"] in {BLOCKED, UNKNOWN} else 0


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
            "available": True,
            "loaded": False,
            "resident": False,
            "fallback": requested.allow_fallback,
            "reason": "local generation is available; deterministic routing is authoritative",
        }
    print(json.dumps(payload, indent=2))
    return 0


def _print_attempts(result, verbose: bool) -> None:
    print(f"Route: {' -> '.join(result.route.all_roles)}", file=sys.stderr)
    print(f"Routing mode: {result.route.routing_override.mode}", file=sys.stderr)
    for attempt in result.attempts:
        status = "passed" if attempt.verification.passed else "failed"
        worker = attempt.session_targets.inference.worker_identity or "unresolved"
        print(
            f"Attempt {attempt.role} ({attempt.model}): {status}; "
            f"worker={worker}; tools={len(attempt.tool_executions)}",
            file=sys.stderr,
        )
        if attempt.error:
            print(f"  error={attempt.error}", file=sys.stderr)
        stages = attempt.metrics.get("inference_stages")
        if stages:
            print(f"  stages={' -> '.join(str(item) for item in stages)}", file=sys.stderr)
        residency = attempt.metrics.get("residency_reconciliation")
        if residency:
            print(f"  residency={residency}", file=sys.stderr)
        record = attempt.metrics.get("fabric_record_identity")
        request = attempt.metrics.get("fabric_request_identity")
        if record or request:
            print(f"  fabric request={request} record={record}", file=sys.stderr)
        commons = attempt.metrics.get("commons_evidence_receipt")
        if commons:
            print(f"  commons={commons}", file=sys.stderr)
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
    print("elh: starting", file=sys.stderr, flush=True)
    result = LocalAgent(config, refresh_inventory=False, warm_residency=False).run(
        task,
        workspace=workspace,
        images=images,
        routing_override=_routing_override(args),
        auto_approve=args.yes,
    )
    _print_attempts(result, args.verbose)
    print(result.final_content)
    return 0 if result.successful else 1


def cmd_submit(args: argparse.Namespace) -> int:
    override = _routing_override(args)
    if override.mode not in {"WORKER_MODEL", "WORKER_MODEL_ROLE"}:
        raise ValueError("elh submit requires exact --worker and --model-name pins")
    config = load_config(args.config)
    task = _task_text(args.task)
    agent = LocalAgent(config, refresh_inventory=False, warm_residency=False)
    role = override.role if override.role in config.models else (
        "coder" if "coder" in config.models else next(iter(config.models))
    )
    base = config.models[role]
    model, selection = agent.fabric_session.resolve_model(
        role,
        replace(base, name=str(override.model)),
        override,
    )
    if selection is None or not selection.available or not selection.worker_id:
        raise ValueError(selection.reason if selection else "exact pin could not be resolved")
    agent._configure_fabric_provenance(task, role, model)
    from .tools import ToolRegistry

    registry = ToolRegistry(
        Path.cwd(),
        config.policy,
        auto_approve=True,
        interactive=False,
        commons=agent.commons_session,
    )
    tools = registry.available_schemas(tuple(dict.fromkeys((*model.tools, *agent.commons_session.tool_names))))
    accepted = agent.fabric_session.submit_chat(
        model,
        [{"role": "user", "content": task}],
        worker_id=selection.worker_id,
        idempotency_key=args.idempotency_key,
        tools=tools or None,
    )
    payload = {
        "outcome": "ACCEPTED",
        "stage": "accepted",
        "work_id": accepted.get("work_id"),
        "worker": selection.worker_id,
        "model": model.name,
        "routing_mode": override.mode,
        "execution_transport": accepted.get("execution_transport"),
        "authority": "persistent-fabric",
        "commons_authority": "none",
    }
    if args.json:
        _emit(payload, json_output=True)
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_work(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    session = InventoryAwareFabricSession(config.fabric, residency_config=config.model_residency)
    session.initialize(refresh_inventory=False)
    if args.work_command == "status":
        payload = session.work_status(args.work_id)
    else:
        payload = session.work_result(args.work_id)
    payload["commons_authority"] = "none"
    if args.json:
        _emit(payload, json_output=True)
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    workspace = args.workspace.resolve()
    agent = LocalAgent(config, refresh_inventory=False, warm_residency=False)
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


def cmd_verify_models(args: argparse.Namespace) -> int:
    from .model_verification import discover_candidates, verify_candidate

    config = load_config(args.config)
    session, _fleet_view = _fleet(config, refresh_inventory=False)
    status = session.status()
    workers = [
        worker
        for worker in status.workers
        if not args.worker or worker.get("worker_id") == args.worker
    ]
    found = discover_candidates(workers)
    if args.model_name:
        found = [item for item in found if item[1].get("name") == args.model_name]
    tiers = set(args.tier or [0, 1])
    probes = {
        "reachability": lambda _name, _item: (
            "PASS",
            "implementation identity is present in CURRENT Fabric inventory",
        ),
        "marker_response": lambda _name, _item: (
            "UNKNOWN",
            "marker probe requires an explicit live inference session",
        ),
        "tool_call": lambda _name, _item: (
            "UNKNOWN",
            "tool probe requires an explicit live inference session",
        ),
        "file_read": lambda _name, _item: (
            "UNKNOWN",
            "vision/file probe requires an explicit live inference session",
        ),
        "fabric_receipt": lambda _name, _item: (
            "PASS" if status.state == "available" else "UNKNOWN",
            f"fabric state={status.state}",
        ),
    }
    payload = []
    for worker_id, item in found:
        result = verify_candidate(
            worker_id,
            item,
            probes=probes,
            tiers=tiers,
            persist=bool(args.persist),
        )
        payload.append(
            {
                "worker": result.worker_id,
                "model": result.model,
                "summary": result.summary,
                "records": [record.public() for record in result.records],
            }
        )
    _emit({"candidates": payload, "count": len(payload)}, json_output=args.json)
    return 0 if found else 1


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
    elif args.residency_command == "warm":
        if args.model:
            results = fleet.residency.prepare_experiment(
                "operator-explicit-warm",
                [{"worker_id": args.worker, "model": args.model, "role": "operator"}],
            )
        else:
            results = fleet.residency.reconcile(force_worker=args.worker)
        payload = {
            "outcome": "PASS",
            "worker": args.worker,
            "results": list(results),
        }
        outcomes = {item.get("outcome") for item in payload["results"]}
        if outcomes != {"PASS"}:
            payload["outcome"] = "UNKNOWN"
        session.refresh_model_inventory()
        payload["fleet"] = fleet.snapshot()["fabric"]
    else:
        payload = {
            "outcome": "PASS",
            "worker": args.worker,
            "model": args.model,
            "results": list(
                fleet.residency.release_experiment(
                    "operator-explicit-release",
                    [{
                        "worker_id": args.worker,
                        "model": args.model,
                        "provider": "ollama",
                        "managed": True,
                    }],
                )
            ),
        }
        if {item.get("outcome") for item in payload["results"]} != {"PASS"}:
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
    elif command == "opportunities":
        payload = service.opportunities(limit=args.limit)
    elif command == "work-status":
        payload = service.work_status(args.work_id)
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
    "install-cli": cmd_install_cli,
    "doctor": cmd_doctor,
    "experiment-readiness": cmd_experiment_readiness,
    "models": cmd_models,
    "pull": cmd_pull,
    "route": cmd_route,
    "ask": cmd_ask,
    "submit": cmd_submit,
    "work": cmd_work,
    "chat": cmd_chat,
    "verify": cmd_verify,
    "verify-models": cmd_verify_models,
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
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
