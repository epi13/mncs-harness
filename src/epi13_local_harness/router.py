from __future__ import annotations

import re
from pathlib import Path

from .models import HarnessConfig, RoutePlan, TaskProfile

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
FILE_PATTERN = re.compile(
    r"(?:^|\s)(?:[\w.-]+/)*[\w.-]+\.(?:py|sh|rs|c|h|cpp|hpp|js|ts|tsx|jsx|json|toml|yaml|yml|md|txt)\b",
    re.IGNORECASE,
)


def _contains_any(text: str, terms: set[str]) -> bool:
    return any(term in text for term in terms)


def profile_task(text: str, config: HarnessConfig, images: list[Path] | None = None) -> TaskProfile:
    normalized = " ".join(text.lower().split())
    words = re.findall(r"\b[\w.-]+\b", normalized)
    word_set = set(words)
    file_refs = FILE_PATTERN.findall(text)
    code_block = "```" in text or bool(re.search(r"\b(def|class|fn|function|SELECT)\b", text))

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


def plan_route(
    text: str,
    config: HarnessConfig,
    images: list[Path] | None = None,
    forced_role: str | None = None,
) -> RoutePlan:
    profile = profile_task(text, config, images)

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
        )

    if profile.is_high_risk or profile.has_image or profile.is_complex:
        primary = "reviewer"
        escalations: tuple[str, ...] = ()
        reasons = (*profile.reasons, "reviewer selected for risk, multimodality, or complexity")
    elif profile.has_code and (profile.asks_for_edit or profile.asks_for_execution):
        primary = "e4b"
        chain: list[str] = []
        if config.routing.code_specialist_enabled and "coder" in config.models:
            chain.append("coder")
        chain.append("reviewer")
        escalations = tuple(chain)
        reasons = (*profile.reasons, "E4B selected as primary tool-using worker")
    elif profile.asks_for_edit or profile.asks_for_execution:
        primary = "e4b"
        escalations = ("reviewer",)
        reasons = (*profile.reasons, "E4B selected because the request needs tools")
    else:
        primary = "e2b"
        escalations = ("e4b", "reviewer")
        reasons = (*profile.reasons, "E2B selected for inexpensive read-only work")

    max_roles = max(1, config.routing.max_attempts)
    all_roles = (primary, *escalations)[:max_roles]
    return RoutePlan(
        primary_role=all_roles[0],
        escalation_roles=tuple(all_roles[1:]),
        reasons=tuple(dict.fromkeys(reasons)),
        profile=profile,
    )
