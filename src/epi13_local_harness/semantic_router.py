from __future__ import annotations

import importlib.util
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .models import HarnessConfig, LaneConfig, SemanticRouteResult, TaskProfile

_FULL_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_REQUIRED_MODULES = ("torch", "transformers", "huggingface_hub")
_BACKENDS: dict[tuple[str, str, str, str, bool], "LfmPromptRouter"] = {}
_LAST_ERRORS: dict[tuple[str, str, str, str, bool], str] = {}
_BACKEND_LOCK = threading.Lock()


class SemanticRouterError(RuntimeError):
    """Raised when a configured semantic-routing backend cannot be used safely."""


@dataclass(frozen=True)
class RouterRuntimeStatus:
    enabled: bool
    mode: str
    backend: str
    model: str
    revision: str
    device: str
    local_files_only: bool
    cache_directory: Path
    missing_dependencies: tuple[str, ...]
    cached: bool
    active: bool
    state: str
    detail: str = ""


def _cache_key(config: HarnessConfig) -> tuple[str, str, str, str, bool]:
    router = config.router
    return (
        router.model,
        router.revision,
        router.device,
        str(router.cache_directory),
        router.local_files_only,
    )


def _missing_dependencies() -> tuple[str, ...]:
    return tuple(name for name in _REQUIRED_MODULES if importlib.util.find_spec(name) is None)


def _require_pinned_revision(revision: str) -> None:
    if not _FULL_COMMIT_RE.fullmatch(revision):
        raise SemanticRouterError(
            "The Transformers router requires a full 40-character commit hash; "
            f"configured revision is {revision!r}."
        )


def _eligible_lanes(
    config: HarnessConfig,
    profile: TaskProfile,
) -> list[LaneConfig]:
    return [
        lane
        for lane in config.lanes.values()
        if lane.enabled and (not lane.requires_image or profile.has_image)
    ]


def _load_transformers_classes() -> tuple[Any, Any]:
    try:
        from transformers import AutoModel, AutoTokenizer
    except ImportError as exc:
        raise SemanticRouterError(
            "Semantic-router dependencies are missing. Install with "
            "`python -m pip install -e '.[router]'`."
        ) from exc
    return AutoModel, AutoTokenizer


class LfmPromptRouter:
    """Lazy, pinned wrapper around LiquidAI's zero-shot prompt-router API."""

    def __init__(
        self,
        config: HarnessConfig,
        *,
        loader: Callable[[], tuple[Any, Any]] = _load_transformers_classes,
    ) -> None:
        self.config = config.router
        self._loader = loader
        self._tokenizer: Any | None = None
        self._model: Any | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None and self._tokenizer is not None

    def load(self) -> None:
        if self.loaded:
            return
        if self.config.backend != "transformers":
            raise SemanticRouterError(
                f"Unsupported semantic router backend: {self.config.backend!r}"
            )
        if not self.config.model:
            raise SemanticRouterError("No semantic router model is configured.")
        _require_pinned_revision(self.config.revision)

        missing = _missing_dependencies()
        if missing:
            raise SemanticRouterError(
                "Missing semantic-router dependencies: " + ", ".join(missing)
            )

        AutoModel, AutoTokenizer = self._loader()
        common = {
            "revision": self.config.revision,
            "trust_remote_code": True,
            "cache_dir": str(self.config.cache_directory),
            "local_files_only": self.config.local_files_only,
        }
        tokenizer = AutoTokenizer.from_pretrained(self.config.model, **common)
        model = AutoModel.from_pretrained(self.config.model, **common).eval()
        if self.config.device and self.config.device != "auto":
            model = model.to(self.config.device)
        if not callable(getattr(model, "route", None)):
            raise SemanticRouterError(
                "The loaded model does not expose the documented route() method."
            )
        self._tokenizer = tokenizer
        self._model = model

    def route(
        self,
        text: str,
        lanes: list[LaneConfig],
    ) -> SemanticRouteResult:
        if not lanes:
            raise SemanticRouterError("No eligible semantic-routing lanes are configured.")
        self.load()
        assert self._model is not None
        assert self._tokenizer is not None

        labels = [f"{lane.name}: {lane.description.strip()}" for lane in lanes]
        label_to_lane = dict(zip(labels, (lane.name for lane in lanes)))
        started = time.perf_counter()
        raw = self._model.route(text, labels, tokenizer=self._tokenizer)
        latency_ms = (time.perf_counter() - started) * 1000

        if not isinstance(raw, list) or not raw:
            raise SemanticRouterError("The semantic router returned no route scores.")

        scores: dict[str, float] = {}
        for item in raw:
            if not isinstance(item, dict):
                raise SemanticRouterError("The semantic router returned an invalid item.")
            label = str(item.get("route", ""))
            lane_name = label_to_lane.get(label)
            if lane_name is None:
                raise SemanticRouterError(
                    f"The semantic router returned an unknown route label: {label!r}"
                )
            try:
                score = float(item["score"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SemanticRouterError(
                    f"The semantic router returned an invalid score for {lane_name!r}."
                ) from exc
            if not 0.0 <= score <= 1.0:
                raise SemanticRouterError(
                    f"The semantic router score for {lane_name!r} is outside [0, 1]."
                )
            scores[lane_name] = score

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        selected_lane, selected_score = ranked[0]
        runner_up_lane, runner_up_score = (
            ranked[1] if len(ranked) > 1 else (None, None)
        )
        margin = (
            selected_score - runner_up_score
            if runner_up_score is not None
            else selected_score
        )

        reason = "semantic lane selected"
        if selected_score < self.config.minimum_score:
            selected_lane = self.config.ambiguity_lane
            reason = "semantic score below configured threshold"
        elif runner_up_score is not None and margin < self.config.minimum_margin:
            selected_lane = self.config.ambiguity_lane
            reason = "semantic score margin below configured threshold"

        return SemanticRouteResult(
            selected_lane=selected_lane,
            selected_score=selected_score,
            runner_up_lane=runner_up_lane,
            runner_up_score=runner_up_score,
            margin=margin,
            all_scores=scores,
            backend="transformers",
            reason=reason,
            revision=self.config.revision,
            latency_ms=latency_ms,
        )


def get_router_backend(config: HarnessConfig) -> LfmPromptRouter:
    key = _cache_key(config)
    with _BACKEND_LOCK:
        backend = _BACKENDS.get(key)
        if backend is None:
            backend = LfmPromptRouter(config)
            _BACKENDS[key] = backend
        return backend


def clear_router_cache() -> None:
    with _BACKEND_LOCK:
        _BACKENDS.clear()
        _LAST_ERRORS.clear()


def activate_router(config: HarnessConfig) -> bool:
    """Load an enabled semantic router on the caller's current thread.

    The Textual TUI routes prompts from worker threads. Pre-activating the model
    before Textual starts those workers avoids asking model-loading code to spawn
    or duplicate process resources from inside a threaded UI runtime. Failure is
    recorded for Doctor/status and deterministic routing remains available.
    """

    # Compatibility-only activation for callers that explicitly opt into the
    # removed backend. Normal CLI/TUI paths never call this function.
    if not config.router.enable_semantic_routing or config.router.backend != "transformers":
        return False
    key = _cache_key(config)
    try:
        get_router_backend(config).load()
    except Exception as exc:
        _LAST_ERRORS[key] = f"{type(exc).__name__}: {exc}"
        return False
    _LAST_ERRORS.pop(key, None)
    return True


def route_with_backend(
    text: str,
    config: HarnessConfig,
    profile: TaskProfile,
) -> tuple[SemanticRouteResult | None, str | None]:
    if not config.router.enable_semantic_routing:
        return None, None
    if config.router.backend != "transformers":
        return None, f"semantic backend {config.router.backend!r} is not implemented"
    if profile.is_high_risk:
        return None, "high-risk intent kept on deterministic reviewer route"
    lanes = _eligible_lanes(config, profile)
    if not lanes:
        return None, "no eligible semantic-routing lanes"
    key = _cache_key(config)
    try:
        result = get_router_backend(config).route(text, lanes)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        _LAST_ERRORS[key] = detail
        return None, detail
    _LAST_ERRORS.pop(key, None)
    return result, None


def _is_snapshot_cached(config: HarnessConfig) -> bool:
    if not config.router.model or not _FULL_COMMIT_RE.fullmatch(config.router.revision):
        return False
    if importlib.util.find_spec("huggingface_hub") is None:
        return False
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(
            repo_id=config.router.model,
            revision=config.router.revision,
            cache_dir=str(config.router.cache_directory),
            local_files_only=True,
        )
        return True
    except Exception:
        return False


def router_status(config: HarnessConfig) -> RouterRuntimeStatus:
    router = config.router
    # Do not inspect optional packages or Hugging Face caches here.  Doctor and
    # normal inference must remain useful on an offline, deterministic install.
    missing: tuple[str, ...] = ()
    active = False
    cached = False
    state = "disabled" if not router.enable_semantic_routing else "removed"
    detail = _LAST_ERRORS.get(_cache_key(config), "semantic router removed; deterministic policy and Fabric inventory are used")

    return RouterRuntimeStatus(
        enabled=router.enable_semantic_routing,
        mode=router.mode,
        backend=router.backend,
        model=router.model,
        revision=router.revision,
        device=router.device,
        local_files_only=router.local_files_only,
        cache_directory=router.cache_directory,
        missing_dependencies=missing,
        cached=cached,
        active=active,
        state=state,
        detail=detail,
    )


def prepare_router(config: HarnessConfig) -> SemanticRouteResult:
    if config.router.backend != "transformers":
        raise SemanticRouterError(
            f"Cannot prepare unsupported backend {config.router.backend!r}."
        )
    backend = get_router_backend(config)
    lanes = [
        lane
        for lane in config.lanes.values()
        if lane.enabled and not lane.requires_image
    ]
    if not lanes:
        raise SemanticRouterError("No text-only lanes are available for a smoke test.")
    return backend.route(
        "Fix the failing Python unit test and explain the change.",
        lanes,
    )
