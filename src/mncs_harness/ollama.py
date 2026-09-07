from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .models import ModelConfig, OllamaConfig


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, config: OllamaConfig):
        self.config = config

    def _request(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        url = f"{self.config.base_url}{endpoint}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout or self.config.timeout_seconds
            ) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OllamaError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise OllamaError(
                f"Could not connect to Ollama at {self.config.base_url}: {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise OllamaError("Ollama request timed out") from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise OllamaError(f"Ollama returned invalid JSON: {body[:500]}") from exc

    def version(self) -> str:
        return str(self._request("GET", "/api/version").get("version", "unknown"))

    def tags(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/api/tags").get("models", []))

    def running(self) -> list[dict[str, Any]]:
        value = self._request("GET", "/api/ps")
        models = value.get("models", [])
        if not isinstance(models, list):
            raise OllamaError("Ollama returned a malformed running-model inventory")
        return [dict(model) for model in models if isinstance(model, dict)]

    def model_names(self) -> set[str]:
        names: set[str] = set()
        for model in self.tags():
            for key in ("name", "model"):
                value = model.get(key)
                if value:
                    names.add(str(value))
        return names

    def model_size(self, name: str) -> int | None:
        """Return Ollama's stored model size when the configured tag is present."""
        for model in self.tags():
            aliases = {str(model.get(key)) for key in ("name", "model") if model.get(key)}
            if name not in aliases:
                continue
            size = model.get("size")
            if isinstance(size, int) and not isinstance(size, bool) and size > 0:
                return size
            return None
        return None

    def set_residency(self, name: str, keep_alive: str | int) -> dict[str, Any]:
        if not name or len(name) > 256 or any(ord(character) < 32 for character in name):
            raise OllamaError("Ollama model name is invalid")
        return self._request(
            "POST",
            "/api/generate",
            {"model": name, "prompt": "", "stream": False, "keep_alive": keep_alive},
        )

    @staticmethod
    def encode_images(paths: list[Path] | None) -> list[str]:
        encoded: list[str] = []
        for path in paths or []:
            encoded.append(base64.b64encode(path.read_bytes()).decode("ascii"))
        return encoded

    def chat(
        self,
        model: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        images: list[Path] | None = None,
    ) -> dict[str, Any]:
        prepared = [dict(message) for message in messages]
        if images:
            for message in reversed(prepared):
                if message.get("role") == "user":
                    message["images"] = self.encode_images(images)
                    break
        payload: dict[str, Any] = {
            "model": model.name,
            "messages": prepared,
            "stream": False,
            "keep_alive": model.keep_alive,
            "think": model.think,
            "options": {
                "num_ctx": model.num_ctx,
                "temperature": model.temperature,
                "top_p": model.top_p,
                "top_k": model.top_k,
            },
        }
        if tools:
            payload["tools"] = tools
        return self._request("POST", "/api/chat", payload)
