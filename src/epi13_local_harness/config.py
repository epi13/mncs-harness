from __future__ import annotations

import os
import shutil
import tomllib
from importlib.resources import files
from pathlib import Path
from typing import Any

from .models import (
    CommonsConfig,
    ControllerConfig,
    FabricConfig,
    FabricWorkerConfig,
    HarnessConfig,
    LaneConfig,
    MetricsConfig,
    ModelConfig,
    ModelResidencyConfig,
    OllamaConfig,
    PolicyConfig,
    ResidentWorkerConfig,
    RouterConfig,
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


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path | None = None) -> HarnessConfig:
    selected = (path or default_config_path()).expanduser()
    raw = tomllib.loads(bundled_config_path().read_text(encoding="utf-8"))
    if selected.exists():
        with selected.open("rb") as handle:
            raw = _merge_dicts(raw, tomllib.load(handle))

    ollama_raw = dict(raw.get("ollama", {}))
    routing_raw = dict(raw.get("routing", {}))
    router_raw = dict(raw.get("router", {}))
    policy_raw = dict(raw.get("policy", {}))
    verification_raw = dict(raw.get("verification", {}))
    metrics_raw = dict(raw.get("metrics", {}))
    fabric_raw = dict(raw.get("fabric", {}))
    commons_raw = dict(raw.get("commons", {}))
    residency_raw = dict(raw.get("model_residency", {}))
    controller_raw = dict(raw.get("controller", {}))
    models_raw = dict(raw.get("models", {}))
    lanes_raw = dict(raw.get("lanes", {}))

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
            provider=str(item.get("provider", "fabric")),
            execution_device=str(item.get("execution_device", "auto")),
            accelerator_backend=(
                str(item["accelerator_backend"])
                if item.get("accelerator_backend") is not None
                else None
            ),
            offload=str(item.get("offload", "auto")),
            precision=str(item.get("precision", "auto")),
            model_storage_bytes=int(item.get("model_storage_bytes", 0)),
            estimated_workspace_bytes=int(item.get("estimated_workspace_bytes", 0)),
            minimum_host_memory_bytes=(
                int(item["minimum_host_memory_bytes"])
                if item.get("minimum_host_memory_bytes") is not None
                else None
            ),
            gpu_reserve_bytes=int(item.get("gpu_reserve_bytes", 268_435_456)),
            maximum_vram_bytes=(
                int(item["maximum_vram_bytes"])
                if item.get("maximum_vram_bytes") is not None
                else None
            ),
            minimum_accelerator_working_bytes=(
                int(item["minimum_accelerator_working_bytes"])
                if item.get("minimum_accelerator_working_bytes") is not None
                else None
            ),
            runtime_supports_sequential_cpu_offload=(
                bool(item["runtime_supports_sequential_cpu_offload"])
                if item.get("runtime_supports_sequential_cpu_offload") is not None
                else None
            ),
            required_capabilities=tuple(
                str(value) for value in item.get("required_capabilities", [])
            ),
            resource_max_age_seconds=float(item.get("resource_max_age_seconds", 300.0)),
        )

    required_roles = {"e2b", "e4b", "reviewer"}
    missing = required_roles.difference(models)
    if missing:
        raise ValueError(f"Missing required model roles: {', '.join(sorted(missing))}")

    lanes: dict[str, LaneConfig] = {}
    for lane_name, item in lanes_raw.items():
        lanes[str(lane_name)] = LaneConfig(
            name=str(lane_name),
            description=str(item.get("description", str(lane_name))),
            worker_role=str(_required(item, "worker_role", f"lanes.{lane_name}")),
            enabled=bool(item.get("enabled", True)),
            requires_image=bool(item.get("requires_image", False)),
            backend=str(item.get("backend", "ollama")),
            model=str(item.get("model", "")),
            keep_alive=item.get("keep_alive", "0"),
            num_ctx=int(item.get("num_ctx", 8192)),
            think=bool(item.get("think", False)),
            temperature=float(item.get("temperature", 1.0)),
            top_p=float(item.get("top_p", 0.95)),
            top_k=int(item.get("top_k", 64)),
            tools=tuple(str(value) for value in item.get("tools", [])),
            escalation=tuple(str(value) for value in item.get("escalation", ())),
        )

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
            semantic_enabled=bool(router_raw.get("enable_semantic_routing", False)),
            semantic_backend=str(router_raw.get("backend", "deterministic")),
            semantic_model=str(router_raw.get("model", "")),
            semantic_revision=str(router_raw.get("revision", "")),
            semantic_device=str(router_raw.get("device", "cpu")),
            minimum_score=float(router_raw.get("minimum_score", 0.60)),
            minimum_margin=float(router_raw.get("minimum_margin", 0.12)),
            fallback=str(router_raw.get("fallback", "deterministic")),
            ambiguity_lane=str(router_raw.get("ambiguity_lane", "review")),
        ),
        router=RouterConfig(
            mode=str(router_raw.get("mode", "deterministic")),
            backend=str(router_raw.get("backend", "deterministic")),
            model=str(router_raw.get("model", "")),
            revision=str(router_raw.get("revision", "")),
            device=str(router_raw.get("device", "cpu")),
            minimum_score=float(router_raw.get("minimum_score", 0.60)),
            minimum_margin=float(router_raw.get("minimum_margin", 0.12)),
            enable_semantic_routing=bool(router_raw.get("enable_semantic_routing", False)),
            fallback=str(router_raw.get("fallback", "deterministic")),
            ambiguity_lane=str(router_raw.get("ambiguity_lane", "review")),
            cache_directory=Path(str(router_raw.get("cache_directory", "~/.cache/epi13-local-harness/router"))).expanduser(),
            local_files_only=bool(router_raw.get("local_files_only", False)),
        ),
        lanes=lanes,
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
        fabric=_parse_fabric_config(fabric_raw),
        commons=_parse_commons_config(commons_raw),
        model_residency=_parse_model_residency_config(residency_raw),
        controller=_parse_controller_config(controller_raw),
    )


def _parse_commons_config(raw: dict[str, Any]) -> CommonsConfig:
    domain = str(raw.get("domain", "local"))
    startup = float(raw.get("startup_timeout_seconds", 10.0))
    call = float(raw.get("call_timeout_seconds", 30.0))
    maximum = int(raw.get("max_response_bytes", 1_048_576))
    if not domain or len(domain) > 256 or any(ord(char) < 32 for char in domain):
        raise ValueError("commons.domain must be bounded non-empty text")
    if not 0.1 <= startup <= 60 or not 0.1 <= call <= 300:
        raise ValueError("Commons MCP timeouts are outside bounded ranges")
    if not 1024 <= maximum <= 4 * 1024 * 1024:
        raise ValueError("commons.max_response_bytes must be between 1024 and 4194304")
    return CommonsConfig(
        enabled=bool(raw.get("enabled", False)),
        store_path=Path(
            str(raw.get("store_path", "~/.local/state/mncs-commons"))
        ).expanduser(),
        domain=domain,
        auto_initialize=bool(raw.get("auto_initialize", True)),
        allow_model_publication=bool(raw.get("allow_model_publication", False)),
        publish_fabric_evidence=bool(raw.get("publish_fabric_evidence", False)),
        startup_timeout_seconds=startup,
        call_timeout_seconds=call,
        max_response_bytes=maximum,
    )


def _parse_fabric_config(raw: dict[str, Any]) -> FabricConfig:
    controller_mode = str(raw.get("controller_mode", "service"))
    if controller_mode not in {"service", "embedded", "transitional"}:
        raise ValueError("fabric.controller_mode must be service, embedded, or transitional")
    workers_raw = raw.get("workers", {})
    if not isinstance(workers_raw, dict):
        raise ValueError("fabric.workers must be a TOML table")
    workers: list[FabricWorkerConfig] = []
    for worker_id, item in workers_raw.items():
        if not isinstance(item, dict):
            raise ValueError(f"fabric.workers.{worker_id} must be a TOML table")
        kind = str(item.get("kind", "remote"))
        if kind not in {"local", "remote"}:
            raise ValueError(f"fabric.workers.{worker_id}.kind must be local or remote")
        workers.append(
            FabricWorkerConfig(
                worker_id=str(worker_id),
                kind=kind,
                state_path=Path(
                    str(item.get("state_path", f"~/.local/state/{APP_NAME}/fabric-{worker_id}.jsonl"))
                ).expanduser(),
                bundle_root=(
                    Path(str(item["bundle_root"])).expanduser()
                    if item.get("bundle_root") is not None
                    else None
                ),
                host=str(item["host"]) if item.get("host") is not None else None,
                port=int(item["port"]) if item.get("port") is not None else None,
                capabilities=tuple(str(value) for value in item.get("capabilities", ["python"])),
                ca_file=(
                    Path(str(item["ca_file"])).expanduser()
                    if item.get("ca_file") is not None
                    else None
                ),
                client_certificate=(
                    Path(str(item["client_certificate"])).expanduser()
                    if item.get("client_certificate") is not None
                    else None
                ),
                client_key=(
                    Path(str(item["client_key"])).expanduser()
                    if item.get("client_key") is not None
                    else None
                ),
                trust_state=(
                    Path(str(item["trust_state"])).expanduser()
                    if item.get("trust_state") is not None
                    else None
                ),
                concurrency_limit=int(item.get("concurrency_limit", 1)),
                timeout_seconds=float(item.get("timeout_seconds", 5.0)),
                connect_timeout_seconds=(
                    float(item["connect_timeout_seconds"])
                    if item.get("connect_timeout_seconds") is not None
                    else None
                ),
                control_timeout_seconds=(
                    float(item["control_timeout_seconds"])
                    if item.get("control_timeout_seconds") is not None
                    else None
                ),
                execution_timeout_overhead_seconds=float(
                    item.get("execution_timeout_overhead_seconds", 5.0)
                ),
            )
        )
    for worker in workers:
        if (
            worker.timeout_seconds <= 0
            or (
                worker.connect_timeout_seconds is not None
                and worker.connect_timeout_seconds <= 0
            )
            or (
                worker.control_timeout_seconds is not None
                and worker.control_timeout_seconds <= 0
            )
            or not 0 < worker.execution_timeout_overhead_seconds <= 300
        ):
            raise ValueError(f"fabric worker {worker.worker_id} timeout bounds are invalid")
    registry_value = (
        Path(str(raw["registry_path"])).expanduser()
        if raw.get("registry_path") is not None
        else None
    )
    if controller_mode == "service" and (workers or registry_value is not None):
        raise ValueError(
            "fabric.controller_mode=service cannot contain fabric.workers or registry_path; "
            "use controller_mode=embedded or transitional for explicit compatibility"
        )
    service_timeout = float(raw.get("service_timeout_seconds", 5.0))
    if not 0.1 <= service_timeout <= 30:
        raise ValueError("fabric.service_timeout_seconds must be between 0.1 and 30")
    consumer_identity = str(raw.get("consumer_identity", "epi13-local-harness"))
    if not consumer_identity or len(consumer_identity) > 128 or "\x00" in consumer_identity:
        raise ValueError("fabric.consumer_identity must be bounded non-empty text")
    runtime_probe_timeout = float(raw.get("runtime_probe_timeout_seconds", 45.0))
    runtime_probe_max_age = float(raw.get("runtime_probe_max_age_seconds", 1800.0))
    if runtime_probe_timeout <= 0 or runtime_probe_timeout > 300:
        raise ValueError("fabric.runtime_probe_timeout_seconds must be between 0 and 300")
    if runtime_probe_max_age < 0 or runtime_probe_max_age > 3600:
        raise ValueError("fabric.runtime_probe_max_age_seconds must be between 0 and 3600")
    provider_timeout = int(raw.get("provider_timeout_seconds", 600))
    job_timeout_overhead = int(raw.get("job_timeout_overhead_seconds", 5))
    if (
        provider_timeout < 1
        or job_timeout_overhead < 1
        or job_timeout_overhead > 300
        or provider_timeout + job_timeout_overhead > 86_400
    ):
        raise ValueError("Fabric provider/job timeout bounds are invalid")
    return FabricConfig(
        enabled=bool(raw.get("enabled", False)),
        controller_mode=controller_mode,
        controller_id=str(raw.get("controller_id", "epi13-local-harness")),
        service_socket=Path(
            str(raw.get("service_socket", "~/.local/state/mncs-fabric/controller.sock"))
        ).expanduser(),
        service_timeout_seconds=service_timeout,
        consumer_identity=consumer_identity,
        state_path=Path(
            str(raw.get("state_path", "~/.local/state/epi13-local-harness/fabric.jsonl"))
        ).expanduser(),
        fallback_to_local=bool(raw.get("fallback_to_local", True)),
        refresh_on_startup=bool(raw.get("refresh_on_startup", True)),
        refresh_timeout_seconds=float(raw.get("refresh_timeout_seconds", 5.0)),
        runtime_probe_on_refresh=bool(raw.get("runtime_probe_on_refresh", True)),
        runtime_probe_timeout_seconds=runtime_probe_timeout,
        runtime_probe_max_age_seconds=runtime_probe_max_age,
        worker_bundle_root=Path(
            str(
                raw.get(
                    "worker_bundle_root",
                    "~/.local/state/epi13-local-harness/fabric-worker-bundle",
                )
            )
        ).expanduser(),
        provider_ollama_base_url=str(
            raw.get("provider_ollama_base_url", "http://127.0.0.1:11434")
        ).rstrip("/"),
        provider_timeout_seconds=provider_timeout,
        job_timeout_overhead_seconds=job_timeout_overhead,
        registry_path=registry_value,
        workers=tuple(sorted(workers, key=lambda item: item.worker_id)),
    )


def _parse_model_residency_config(raw: dict[str, Any]) -> ModelResidencyConfig:
    workers_raw = raw.get("workers", {})
    if not isinstance(workers_raw, dict):
        raise ValueError("model_residency.workers must be a TOML table")
    workers: list[ResidentWorkerConfig] = []
    for worker_id, item in workers_raw.items():
        if not isinstance(item, dict):
            raise ValueError(f"model_residency.workers.{worker_id} must be a TOML table")
        model = item.get("model")
        workers.append(
            ResidentWorkerConfig(
                worker_id=str(worker_id),
                model=str(model) if model is not None else None,
            )
        )
    keep_alive = raw.get("keep_alive", -1)
    if not isinstance(keep_alive, (str, int)) or isinstance(keep_alive, bool):
        raise ValueError("model_residency.keep_alive must be a duration or integer")
    warm_timeout = float(raw.get("warm_timeout_seconds", 300.0))
    memory_fraction = float(raw.get("maximum_model_memory_fraction", 0.5))
    role_preference = tuple(
        str(value)
        for value in raw.get(
            "role_preference", ["e4b", "e2b", "coder", "reviewer"]
        )
    )
    if not 1 <= warm_timeout <= 3600:
        raise ValueError("model_residency.warm_timeout_seconds must be between 1 and 3600")
    if not 0.05 <= memory_fraction <= 0.9:
        raise ValueError(
            "model_residency.maximum_model_memory_fraction must be between 0.05 and 0.9"
        )
    if not role_preference or len(set(role_preference)) != len(role_preference):
        raise ValueError("model_residency.role_preference must be unique and non-empty")
    return ModelResidencyConfig(
        enabled=bool(raw.get("enabled", False)),
        warm_on_startup=bool(raw.get("warm_on_startup", False)),
        prefer_resident_for_auto_routing=bool(
            raw.get("prefer_resident_for_auto_routing", True)
        ),
        keep_alive=keep_alive,
        warm_timeout_seconds=warm_timeout,
        maximum_model_memory_fraction=memory_fraction,
        role_preference=role_preference,
        workers=tuple(sorted(workers, key=lambda item: item.worker_id)),
    )


def _parse_controller_config(raw: dict[str, Any]) -> ControllerConfig:
    policy = str(raw.get("generation_policy", "local-generation-allowed"))
    if policy not in {"router-only", "local-generation-allowed"}:
        raise ValueError("controller.generation_policy is invalid")
    return ControllerConfig(generation_policy=policy)
