from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from .models import HarnessConfig, RoutePlan, RoutingOverride, SemanticRouteResult, TaskProfile

CODE_TERMS = {
    "code", "coding", "function", "class", "method", "script", "python", "bash",
    "rust", "cargo", "compiler", "compile", "test", "tests", "pytest", "bug",
    "debug", "refactor", "repository", "repo", "patch", "implementation", "api",
    "json", "toml", "yaml", "sql", "git", "diff",
}
EDIT_TERMS = {
    "add", "change", "create", "edit", "fix", "implement", "modify", "patch",
    "refactor", "remove", "rename", "repair", "replace", "update", "write",
}
EXECUTION_TERMS = {
    "run", "execute", "build", "compile", "test", "install", "launch", "start",
    "stop", "restart", "pull", "commit",
}
EXPLANATION_TERMS = {
    "explain", "describe", "summarize", "inspect", "show", "tell", "what", "why",
    "how", "review", "analyze", "analyse",
}
HIGH_RISK_TERMS = {
    "sudo", "root", "delete", "wipe", "format", "partition", "mount", "firewall",
    "iptables", "nftables", "systemctl enable", "systemctl disable", "dnf install",
    "rpm -e", "chmod", "chown", "kill", "reboot", "shutdown", "credential",
    "password", "secret", "token", "private key",
}
COMPLEX_TERMS = {
    "architecture", "across the repository", "multi-file", "multiple files", "design",
    "race condition", "security review", "migration", "dependency graph", "distributed",
    "recursive", "concurrency", "performance", "benchmark", "ambiguous",
}
OCR_TERMS = {
    "ocr", "transcribe", "extract text", "read this document", "scanned", "invoice",
    "receipt", "table extraction", "handwriting", "document recognition",
}
FILE_PATTERN = re.compile(
    r"(?:^|\s)(?:[\w.-]+/)*[\w.-]+\."
    r"(?:py|sh|rs|c|h|cpp|hpp|js|ts|tsx|jsx|json|toml|yaml|yml|md|txt)\b",
    re.IGNORECASE,
)


def _contains_any(text: str, terms: set[str]) -> bool:
    return any(term in text for term in terms)


def profile_task(
    text: str,
    config: HarnessConfig,
    images: list[Path] | None = None,
) -> TaskProfile:
    normalized = " ".join(text.lower().split())
    words = re.findall(r"\b[\w.-]+\b", normalized)
    word_set = set(words)
    file_refs = FILE_PATTERN.findall(text)
    code_block = "```" in text or bool(
        re.search(r"\b(def|class|fn|function|SELECT)\b", text)
    )

    has_code = code_block or bool(word_set.intersection(CODE_TERMS)) or bool(file_refs)
    asks_for_edit = bool(word_set.intersection(EDIT_TERMS))
    asks_for_execution = bool(word_set.intersection(EXECUTION_TERMS))
    asks_for_explanation = bool(word_set.intersection(EXPLANATION_TERMS))
    is_high_risk = _contains_any(normalized, HIGH_RISK_TERMS)

    complexity_reasons: list[str] = []
    if len(words) >= config.routing.complex_word_limit:
        complexity_reasons.append("long request")
    if len(file_refs) >= 4:
        complexity_reasons.append("many referenced files")
    if _contains_any(normalized, COMPLEX_TERMS):
        complexity_reasons.append("complexity keyword")
    if normalized.count(" and ") >= 4:
        complexity_reasons.append("many requested operations")
    if is_high_risk:
        complexity_reasons.append("high-risk intent")

    reasons: list[str] = []
    if has_code:
        reasons.append("code or repository context detected")
    if asks_for_edit:
        reasons.append("workspace modification requested")
    if asks_for_execution:
        reasons.append("command or test execution requested")
    if asks_for_explanation and not asks_for_edit:
        reasons.append("primarily explanatory request")
    if images:
        reasons.append("multimodal input attached")
    reasons.extend(complexity_reasons)

    return TaskProfile(
        text=text,
        word_count=len(words),
        has_code=has_code,
        asks_for_edit=asks_for_edit,
        asks_for_execution=asks_for_execution,
        asks_for_explanation=asks_for_explanation,
        is_high_risk=is_high_risk,
        is_complex=bool(complexity_reasons),
        has_image=bool(images),
        file_reference_count=len(file_refs),
        reasons=tuple(reasons),
    )


def _deterministic_route(profile: TaskProfile, config: HarnessConfig) -> RoutePlan:
    def available(preferred: str) -> str:
        if preferred in config.models:
            return preferred
        return next(iter(config.models))

    if profile.is_high_risk or profile.has_image or profile.is_complex:
        primary = available("reviewer")
        escalations: tuple[str, ...] = ()
        reasons = (
            *profile.reasons,
            "reviewer selected for risk, multimodality, or complexity",
        )
    elif profile.has_code and (profile.asks_for_edit or profile.asks_for_execution):
        primary = available("e4b")
        chain: list[str] = []
        if config.routing.code_specialist_enabled and "coder" in config.models:
            chain.append("coder")
        if "reviewer" in config.models:
            chain.append("reviewer")
        escalations = tuple(chain)
        reasons = (*profile.reasons, "E4B selected as primary tool-using worker")
    elif profile.asks_for_edit or profile.asks_for_execution:
        primary = available("e4b")
        escalations = ("reviewer",) if "reviewer" in config.models else ()
        reasons = (*profile.reasons, "E4B selected because the request needs tools")
    else:
        primary = available("e2b")
        escalations = tuple(role for role in ("e4b", "reviewer") if role in config.models)
        reasons = (*profile.reasons, "E2B selected for inexpensive read-only work")

    max_roles = max(1, config.routing.max_attempts)
    all_roles = (primary, *escalations)[:max_roles]
    return RoutePlan(
        primary_role=all_roles[0],
        escalation_roles=tuple(all_roles[1:]),
        reasons=tuple(dict.fromkeys(reasons)),
        profile=profile,
    )


def _compatibility_lane_route(
    text: str, config: HarnessConfig, profile: TaskProfile
) -> SemanticRouteResult | None:
    """Pure task-feature lane selection for deprecated configs.

    This is intentionally local and dependency-free. It exists only so older
    configuration files can be read while operators migrate; the normal route
    path does not enable it.
    """
    if not config.lanes:
        return None
    normalized = " ".join(text.lower().split())
    scores: dict[str, float] = {}
    for name, lane in config.lanes.items():
        if lane.enabled and (not lane.requires_image or profile.has_image):
            score = 0.35
            if name == "ocr" and (profile.has_image or _contains_any(normalized, OCR_TERMS)):
                score += 0.35
            if name == "coding" and profile.has_code and (profile.asks_for_edit or profile.asks_for_execution):
                score += 0.18
            if name == "review" and (profile.is_high_risk or profile.is_complex):
                score += 0.22
            if name == "chat" and profile.asks_for_explanation and not profile.asks_for_edit:
                score += 0.10
            scores[name] = min(1.0, score)
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    selected, selected_score = ranked[0]
    runner, runner_score = ranked[1] if len(ranked) > 1 else (None, None)
    margin = selected_score - runner_score if runner_score is not None else selected_score
    return SemanticRouteResult(
        selected_lane=selected,
        selected_score=selected_score,
        runner_up_lane=runner,
        runner_up_score=runner_score,
        margin=margin,
        all_scores=scores,
        backend="heuristic",
        reason="deterministic compatibility lane selected",
    )


def plan_route(
    text: str,
    config: HarnessConfig,
    images: list[Path] | None = None,
    forced_role: str | None = None,
    routing_override: RoutingOverride | None = None,
) -> RoutePlan:
    profile = profile_task(text, config, images)

    if forced_role is not None and routing_override is not None:
        raise ValueError("legacy forced_role cannot be combined with routing_override")
    requested = routing_override or RoutingOverride.from_values(role=forced_role)
    if requested.role is not None:
        forced_role = requested.role

    if forced_role:
        if forced_role not in config.models:
            raise ValueError(
                f"Unknown model role {forced_role!r}; choose from {', '.join(config.models)}"
            )
        return RoutePlan(
            primary_role=forced_role,
            escalation_roles=(),
            reasons=(f"model role forced to {forced_role}",),
            profile=profile,
            routing_override=requested,
        )

    # Every operator pin is authoritative. Exact worker/model requests must
    # not initialize compatibility routing or walk the escalation cascade.
    if requested.mode in {"MODEL", "WORKER", "WORKER_MODEL", "WORKER_MODEL_ROLE"}:
        plan = _deterministic_route(profile, config)
        return replace(plan, escalation_roles=(), routing_override=requested)
    if requested.mode != "AUTO":
        return replace(_deterministic_route(profile, config), routing_override=requested)

    plan = _deterministic_route(profile, config)
    # Legacy config compatibility only. The Transformer backend is ignored
    # during normal execution; only the explicit heuristic compatibility
    # path may still annotate a lane.
    semantic = None
    compatibility_reason: str | None = None
    if config.router.enable_semantic_routing and config.router.backend == "heuristic":
        semantic = _compatibility_lane_route(text, config, profile)
    elif config.router.enable_semantic_routing:
        compatibility_reason = (
            "semantic router is compatibility-only; "
            f"backend={config.router.backend} is ignored during normal execution"
        )
    if semantic and semantic.selected_lane in config.lanes:
        lane = config.lanes[semantic.selected_lane]
        if lane.enabled and lane.worker_role in config.models:
            return replace(
                plan,
                primary_role=lane.worker_role,
                lane=semantic.selected_lane,
                semantic=semantic,
                routing_override=requested,
            )
    if compatibility_reason:
        return replace(
            plan,
            reasons=(*plan.reasons, f"semantic router fallback: {compatibility_reason}"),
            routing_override=requested,
        )
    return replace(plan, routing_override=requested)
