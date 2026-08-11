"""Small inference-provider boundary shared by local and Fabric-backed paths."""

from __future__ import annotations

import inspect
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

from .fabric import FabricExecutionError, FabricSession, FabricUnavailable
from .models import ModelConfig, SessionTargets
from .ollama import OllamaClient


class ProviderError(RuntimeError):
    """A provider could not produce a response."""


class InferenceProvider(Protocol):
    last_metadata: dict[str, Any]

    def chat(
        self,
        model: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        images: list[Path] | None = None,
    ) -> dict[str, Any]: ...


class LocalOllamaProvider:
    """Compatibility adapter retaining the existing Ollama response shape."""

    def __init__(self, client: OllamaClient):
        self.client = client
        self.last_metadata: dict[str, Any] = {
            "provider": "ollama",
            "backend": "ollama",
            "fabric_enabled": False,
            "execution_source": "local",
            **SessionTargets().as_metadata(),
        }

    def model_size(self, name: str) -> int | None:
        return self.client.model_size(name)

    def chat(
        self,
        model: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        images: list[Path] | None = None,
    ) -> dict[str, Any]:
        return self.client.chat(model, messages, tools=tools, images=images)


class FabricOllamaProvider:
    """Invoke worker-local Ollama through a Fabric execution bundle."""

    def __init__(
        self,
        session: FabricSession,
        local_provider: InferenceProvider,
        fallback_to_local: bool,
        inference_worker_id: str | None = None,
        placement_error: str | None = None,
        session_targets: SessionTargets | None = None,
    ):
        self.session = session
        self.local_provider = local_provider
        self.fallback_to_local = fallback_to_local
        self.inference_worker_id = inference_worker_id
        self.placement_error = placement_error
        self.session_targets = session_targets or SessionTargets.unresolved_inference()
        self.last_metadata: dict[str, Any] = {}

    def _session_supports_worker_id(self) -> bool:
        chat = getattr(self.session, "chat", None)
        if not callable(chat):
            return False
        try:
            parameters = inspect.signature(chat).parameters.values()
        except (TypeError, ValueError):
            return False
        return any(
            parameter.name == "worker_id" or parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )

    def _effective_model(self, model: ModelConfig) -> tuple[ModelConfig, str]:
        if model.model_storage_bytes > 0:
            return model, "configured"
        resolver = getattr(self.local_provider, "model_size", None)
        if resolver is None:
            return model, "unknown"
        try:
            size = resolver(model.name)
        except Exception:
            return model, "unknown"
        if isinstance(size, int) and size > 0:
            return replace(model, model_storage_bytes=size), "ollama-tags"
        return model, "unknown"

    def _expanded_failure_reason(self, exc: BaseException) -> str:
        reason = str(exc)
        if reason not in {"INTEGRITY_FAILURE", "COMPLETED"}:
            return reason
        try:
            detail = self.session.status().detail
        except Exception:
            detail = None
        if detail and reason not in detail:
            return f"{reason}; Fabric status: {detail}"
        if detail:
            return f"{reason}; {detail}"
        return reason

    def _failure_metadata(
        self,
        *,
        reason: str,
        model_storage_bytes: int,
        model_storage_source: str,
    ) -> dict[str, Any]:
        last = getattr(self.session, "last_inference", None) or {}
        return {
            "provider": "ollama-via-mncs-fabric",
            "backend": "ollama",
            "fabric_enabled": True,
            "fabric_failure": True,
            "fabric_fallback": False,
            "fabric_failure_reason": reason,
            "fabric_worker": last.get("worker"),
            "execution_source": "remote",
            "placement_mode": last.get("placement"),
            "placement_reason": last.get("reason"),
            "fabric_request_identity": last.get("request_identity"),
            "model_storage_bytes": model_storage_bytes,
            "model_storage_source": model_storage_source,
            **self.session_targets.as_metadata(),
        }

    def chat(
        self,
        model: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        images: list[Path] | None = None,
    ) -> dict[str, Any]:
        effective_model, size_source = self._effective_model(model)
        try:
            if self.placement_error:
                raise FabricUnavailable(self.placement_error)
            arguments = {"tools": tools, "images": images}
            if self.inference_worker_id is not None and self._session_supports_worker_id():
                arguments["worker_id"] = self.inference_worker_id
            response, metadata = self.session.chat(effective_model, messages, **arguments)
            metadata = dict(metadata)
            metadata["model_storage_bytes"] = effective_model.model_storage_bytes
            metadata["model_storage_source"] = size_source
            metadata.update(self.session_targets.as_metadata())
            self.last_metadata = metadata
            return response
        except (FabricExecutionError, FabricUnavailable, ImportError, OSError, ValueError) as exc:
            reason = self._expanded_failure_reason(exc)
            failure = self._failure_metadata(
                reason=reason,
                model_storage_bytes=effective_model.model_storage_bytes,
                model_storage_source=size_source,
            )
            self.last_metadata = failure
            if not self.fallback_to_local:
                raise ProviderError(f"Fabric provider failed: {reason}") from exc
            response = self.local_provider.chat(model, messages, tools=tools, images=images)
            local_metadata = dict(getattr(self.local_provider, "last_metadata", {}))
            local_metadata.update(SessionTargets().as_metadata())
            local_metadata.update(
                {
                    "fabric_enabled": True,
                    "fabric_failure": True,
                    "fabric_fallback": True,
                    "fabric_fallback_reason": reason,
                    "fabric_worker": failure.get("fabric_worker"),
                    "fabric_request_identity": failure.get("fabric_request_identity"),
                    "model_storage_bytes": effective_model.model_storage_bytes,
                    "model_storage_source": size_source,
                }
            )
            self.last_metadata = local_metadata
            return response
