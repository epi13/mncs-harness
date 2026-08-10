"""Small inference-provider boundary shared by local and Fabric-backed paths."""

from __future__ import annotations

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

    def chat(
        self,
        model: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        images: list[Path] | None = None,
    ) -> dict[str, Any]:
        try:
            response, metadata = self.session.chat(model, messages, tools=tools, images=images)
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
                }
            )
            self.last_metadata = local_metadata
            return response
