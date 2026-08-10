"""Small inference-provider boundary shared by local and Fabric-backed paths."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

from .fabric import FabricExecutionError, FabricSession, FabricUnavailable
from .models import ModelConfig
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
    ):
        self.session = session
        self.local_provider = local_provider
        self.fallback_to_local = fallback_to_local
        self.last_metadata: dict[str, Any] = {}

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

    def chat(
        self,
        model: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        images: list[Path] | None = None,
    ) -> dict[str, Any]:
        effective_model, size_source = self._effective_model(model)
        try:
            response, metadata = self.session.chat(
                effective_model,
                messages,
                tools=tools,
                images=images,
            )
            metadata = dict(metadata)
            metadata["model_storage_bytes"] = effective_model.model_storage_bytes
            metadata["model_storage_source"] = size_source
            self.last_metadata = metadata
            return response
        except (FabricExecutionError, FabricUnavailable, ImportError, OSError, ValueError) as exc:
            if not self.fallback_to_local:
                raise ProviderError(f"Fabric provider failed: {exc}") from exc
            response = self.local_provider.chat(model, messages, tools=tools, images=images)
            local_metadata = dict(getattr(self.local_provider, "last_metadata", {}))
            local_metadata.update(
                {
                    "fabric_enabled": True,
                    "fabric_fallback": True,
                    "fabric_fallback_reason": str(exc),
                    "model_storage_bytes": effective_model.model_storage_bytes,
                    "model_storage_source": size_source,
                }
            )
            self.last_metadata = local_metadata
            return response
