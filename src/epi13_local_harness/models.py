from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Risk = Literal["low", "medium", "high", "blocked"]
TargetKind = Literal["controller", "fabric-worker", "unresolved"]


@dataclass(frozen=True)
class SessionTarget:
    kind: TargetKind
    worker_identity: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"controller", "fabric-worker", "unresolved"}:
            raise ValueError("session target kind is invalid")
        if self.kind == "fabric-worker":
            worker = self.worker_identity
            if (
                not isinstance(worker, str)
                or not worker
                or len(worker) > 256
                or any(
                    ord(character) < 32 or 127 <= ord(character) <= 159
                    for character in worker
                )
            ):
                raise ValueError("Fabric worker targets require a bounded worker identity")
        elif self.worker_identity is not None:
            raise ValueError("controller and unresolved targets cannot carry a worker identity")

    @property
    def label(self) -> str:
        if self.kind == "fabric-worker":
            return f"fabric-worker:{self.worker_identity}"
        return self.kind


@dataclass(frozen=True)
class SessionTargets:
    """Independent inference placement, workspace authority, and tool target."""

    inference: SessionTarget = field(default_factory=lambda: SessionTarget("controller"))
    workspace: SessionTarget = field(default_factory=lambda: SessionTarget("controller"))
    tools: SessionTarget = field(default_factory=lambda: SessionTarget("controller"))

    @classmethod
    def remote_inference(cls, worker_identity: str) -> "SessionTargets":
        return cls(inference=SessionTarget("fabric-worker", worker_identity))

    @classmethod
    def unresolved_inference(cls) -> "SessionTargets":
        return cls(inference=SessionTarget("unresolved"))

    def as_metadata(self) -> dict[str, str]:
        return {
            "inference_target": self.inference.label,
            "workspace_target": self.workspace.label,
            "tool_execution_target": self.tools.label,
        }


@dataclass(frozen=True)
class ModelConfig:
    role: str
    name: str
    keep_alive: str | int
    num_ctx: int
    think: bool | str
    temperature: float
    top_p: float
    top_k: int
    tools: tuple[str, ...]
    provider: str = "fabric"
    execution_device: str = "auto"
    accelerator_backend: str | None = None
    offload: str = "auto"
    precision: str = "auto"
    model_storage_bytes: int = 0
    estimated_workspace_bytes: int = 0
    minimum_host_memory_bytes: int | None = None
    gpu_reserve_bytes: int = 268_435_456
    maximum_vram_bytes: int | None = None
    minimum_accelerator_working_bytes: int | None = None
    runtime_supports_sequential_cpu_offload: bool | None = None
    required_capabilities: tuple[str, ...] = ()
    resource_max_age_seconds: float = 300.0


@dataclass(frozen=True)
class OllamaConfig:
    base_url: str
    timeout_seconds: int
    max_tool_steps: int


@dataclass(frozen=True)
class RouterConfig:
    mode: str = "deterministic"
    backend: str = "deterministic"
    model: str = ""
    revision: str = ""
    device: str = "cpu"
    minimum_score: float = 0.60
    minimum_margin: float = 0.12
    enable_semantic_routing: bool = False
    fallback: str = "deterministic"
    ambiguity_lane: str = "review"
    cache_directory: Path = Path("~/.cache/huggingface/hub")
    local_files_only: bool = False


@dataclass(frozen=True)
class LaneConfig:
    name: str
    description: str
    worker_role: str
    enabled: bool = True
    requires_image: bool = False
    backend: str = "ollama"
    model: str = ""
    keep_alive: str | int = "0"
    num_ctx: int = 8192
    think: bool = False
    temperature: float = 1.0
    top_p: float = 0.95
    top_k: int = 64
    tools: tuple[str, ...] = ()
    escalation: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoutingConfig:
    code_specialist_enabled: bool
    escalate_on_verifier_failure: bool
    escalate_on_tool_error: bool
    max_attempts: int
    simple_word_limit: int
    complex_word_limit: int
    semantic_enabled: bool = False
    semantic_backend: str = "deterministic"
    semantic_model: str = ""
    semantic_revision: str = ""
    semantic_device: str = "cpu"
    minimum_score: float = 0.60
    minimum_margin: float = 0.12
    fallback: str = "deterministic"
    ambiguity_lane: str = "review"


@dataclass(frozen=True)
class PolicyConfig:
    approval_mode: str
    max_file_bytes: int
    max_tool_output_chars: int
    command_timeout_seconds: int
    allow_hidden_paths: bool
    allowed_executables: tuple[str, ...]


@dataclass(frozen=True)
class VerificationConfig:
    run_unit_tests: bool
    unit_test_command: tuple[str, ...]
    use_shellcheck_when_available: bool


@dataclass(frozen=True)
class MetricsConfig:
    path: Path
    store_prompt_text: bool


@dataclass(frozen=True)
class FabricWorkerConfig:
    worker_id: str
    kind: str
    state_path: Path
    bundle_root: Path | None = None
    host: str | None = None
    port: int | None = None
    capabilities: tuple[str, ...] = ("python",)
    ca_file: Path | None = None
    client_certificate: Path | None = None
    client_key: Path | None = None
    trust_state: Path | None = None
    concurrency_limit: int = 1
    timeout_seconds: float = 5.0
    connect_timeout_seconds: float | None = None
    control_timeout_seconds: float | None = None
    execution_timeout_overhead_seconds: float = 5.0


@dataclass(frozen=True)
class FabricConfig:
    enabled: bool = False
    controller_id: str = "epi13-local-harness"
    state_path: Path = Path("~/.local/state/epi13-local-harness/fabric.jsonl")
    fallback_to_local: bool = True
    refresh_on_startup: bool = True
    refresh_timeout_seconds: float = 5.0
    runtime_probe_on_refresh: bool = True
    runtime_probe_timeout_seconds: float = 45.0
    runtime_probe_max_age_seconds: float = 1800.0
    worker_bundle_root: Path = Path(
        "~/.local/state/epi13-local-harness/fabric-worker-bundle"
    )
    provider_ollama_base_url: str = "http://127.0.0.1:11434"
    provider_timeout_seconds: int = 600
    job_timeout_overhead_seconds: int = 5
    workers: tuple[FabricWorkerConfig, ...] = ()


@dataclass(frozen=True)
class CommonsConfig:
    enabled: bool = False
    store_path: Path = Path("~/.local/state/mncs-commons")
    domain: str = "local"
    auto_initialize: bool = True
    allow_model_publication: bool = False
    publish_fabric_evidence: bool = False
    startup_timeout_seconds: float = 10.0
    call_timeout_seconds: float = 30.0
    max_response_bytes: int = 1_048_576


@dataclass(frozen=True)
class SemanticRouteResult:
    selected_lane: str
    selected_score: float
    runner_up_lane: str | None
    runner_up_score: float | None
    margin: float
    all_scores: dict[str, float]
    backend: str
    reason: str | None = None
    revision: str | None = None
    latency_ms: float | None = None


@dataclass(frozen=True)
class HarnessConfig:
    ollama: OllamaConfig
    models: dict[str, ModelConfig]
    routing: RoutingConfig
    router: RouterConfig
    lanes: dict[str, LaneConfig]
    policy: PolicyConfig
    verification: VerificationConfig
    metrics: MetricsConfig
    fabric: FabricConfig = field(default_factory=FabricConfig)
    commons: CommonsConfig = field(default_factory=CommonsConfig)


@dataclass(frozen=True)
class TaskProfile:
    text: str
    word_count: int
    has_code: bool
    asks_for_edit: bool
    asks_for_execution: bool
    asks_for_explanation: bool
    is_high_risk: bool
    is_complex: bool
    has_image: bool
    file_reference_count: int
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoutePlan:
    primary_role: str
    escalation_roles: tuple[str, ...]
    reasons: tuple[str, ...]
    profile: TaskProfile
    lane: str | None = None
    semantic: SemanticRouteResult | None = None

    @property
    def all_roles(self) -> tuple[str, ...]:
        return (self.primary_role, *self.escalation_roles)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    risk: Risk
    reason: str
    requires_approval: bool = False


@dataclass
class ToolExecution:
    name: str
    arguments: dict[str, Any]
    output: str
    success: bool
    decision: PolicyDecision
    modified_paths: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    checks: tuple[str, ...]
    failures: tuple[str, ...]


@dataclass
class ModelAttempt:
    role: str
    model: str
    content: str
    thinking: str
    metrics: dict[str, Any]
    tool_executions: list[ToolExecution]
    verification: VerificationResult
    error: str | None = None
    session_targets: SessionTargets = field(default_factory=SessionTargets)


@dataclass
class AgentResult:
    route: RoutePlan
    attempts: list[ModelAttempt]
    final_content: str

    @property
    def successful(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].verification.passed
