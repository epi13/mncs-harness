from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Risk = Literal["low", "medium", "high", "blocked"]


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
    cache_directory: Path = Path("~/.cache/epi13-local-harness/router")
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
class SemanticRouteResult:
    selected_lane: str
    selected_score: float
    runner_up_lane: str | None
    runner_up_score: float | None
    margin: float
    all_scores: dict[str, float]
    backend: str
    reason: str | None = None


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


@dataclass
class AgentResult:
    route: RoutePlan
    attempts: list[ModelAttempt]
    final_content: str

    @property
    def successful(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].verification.passed
