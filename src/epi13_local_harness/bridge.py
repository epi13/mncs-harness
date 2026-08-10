from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .agent import LocalAgent
from .config import default_config_path, load_config
from .metrics import MetricsStore
from .router import plan_route


class BridgeServer:
    def __init__(self, repository_path: Path, config_path: Path | None = None) -> None:
        self.repository_path = repository_path
        self.config_path = config_path or default_config_path()
        self.config = load_config(self.config_path)

    def _encode(self, request_id: str | None, method: str, result: Any) -> str:
        payload = {
            "protocolVersion": 1,
            "requestId": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "method": method,
            "result": result,
        }
        return json.dumps(payload, ensure_ascii=True) + "\n"

    def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        repository_path = Path(str(params.get("repositoryPath", self.repository_path))).expanduser()
        config_path = Path(str(params.get("configPath", self.config_path))).expanduser()
        self.repository_path = repository_path
        self.config_path = config_path
        self.config = load_config(config_path)
        return {
            "ok": True,
            "repositoryPath": str(repository_path),
            "configPath": str(config_path),
            "protocolVersion": 1,
        }

    def health_check(self, params: dict[str, Any]) -> dict[str, Any]:
        fabric = LocalAgent(self.config).fabric_status()
        return {
            "ok": True,
            "python": sys.version.split()[0],
            "repositoryPath": str(self.repository_path),
            "configPath": str(self.config_path),
            "configLoaded": True,
            "fabric": {
                "enabled": fabric.enabled,
                "state": fabric.state,
                "controller_id": fabric.controller_id,
                "workers": [dict(worker) for worker in fabric.workers],
                "detail": fabric.detail,
            },
        }

    def models_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "id": model_name,
                "name": model.name,
                "family": model.role,
                "version": "0.1.0",
                "workerRole": model.role,
                "supportsImage": False,
                "supportsTools": bool(model.tools),
                "provider": model.provider,
                "detail": f"Configured {model.role} lane",
            }
            for model_name, model in self.config.models.items()
        ]

    def lanes_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "name": lane.name,
                "description": lane.description,
                "worker_role": lane.worker_role,
                "enabled": lane.enabled,
                "requires_image": lane.requires_image,
                "model": lane.model or self.config.models[lane.worker_role].name,
                "backend": lane.backend,
                "escalation": list(lane.escalation),
            }
            for lane in self.config.lanes.values()
        ]

    def route_preview(self, params: dict[str, Any]) -> dict[str, Any]:
        task = str(params.get("task", "")).strip()
        if not task:
            raise ValueError("route/preview requires a non-empty task")
        plan = plan_route(task, self.config)
        return {
            "primary_role": plan.primary_role,
            "escalation_roles": list(plan.escalation_roles),
            "reasons": list(plan.reasons),
            "profile": {
                "word_count": plan.profile.word_count,
                "has_code": plan.profile.has_code,
                "asks_for_edit": plan.profile.asks_for_edit,
                "asks_for_execution": plan.profile.asks_for_execution,
                "asks_for_explanation": plan.profile.asks_for_explanation,
                "is_high_risk": plan.profile.is_high_risk,
                "is_complex": plan.profile.is_complex,
                "has_image": plan.profile.has_image,
                "file_reference_count": plan.profile.file_reference_count,
            },
        }

    def chat_start(self, params: dict[str, Any]) -> dict[str, Any]:
        messages = params.get("messages") or []
        lane = str(params.get("lane", "auto"))
        task = ""
        for message in messages:
            if isinstance(message, dict) and str(message.get("role", "")).lower() == "user":
                content = message.get("content")
                if isinstance(content, str):
                    task = content.strip()
                    break
        if not task:
            raise ValueError("chat/start requires a user message")
        result = LocalAgent(self.config).run(
            task,
            workspace=self.repository_path,
            forced_role=lane if lane != "auto" else None,
            auto_approve=False,
        )
        return {
            "route": {
                "primary_role": result.route.primary_role,
                "escalation_roles": list(result.route.escalation_roles),
                "reasons": list(result.route.reasons),
            },
            "final_content": result.final_content,
            "successful": result.successful,
            "attempts": [
                {
                    "role": attempt.role,
                    "model": attempt.model,
                    "content": attempt.content,
                    "thinking": attempt.thinking,
                    "verification": {
                        "passed": attempt.verification.passed,
                        "checks": list(attempt.verification.checks),
                        "failures": list(attempt.verification.failures),
                    },
                    "error": attempt.error,
                    "metrics": dict(attempt.metrics),
                }
                for attempt in result.attempts
            ],
        }

    def metrics_recent(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        limit = max(1, int(params.get("limit", 20)))
        store = MetricsStore(self.config.metrics.path, self.config.metrics.store_prompt_text)
        return list(store.recent(limit))

    def shutdown(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}


class BridgeRuntime:
    def __init__(self, repository_path: Path, config_path: Path | None = None) -> None:
        self.server = BridgeServer(repository_path=repository_path, config_path=config_path)

    def dispatch(self, method: str, params: dict[str, Any] | None, request_id: str | None) -> str:
        handler = {
            "initialize": self.server.initialize,
            "health/check": self.server.health_check,
            "models/list": self.server.models_list,
            "lanes/list": self.server.lanes_list,
            "route/preview": self.server.route_preview,
            "chat/start": self.server.chat_start,
            "metrics/recent": self.server.metrics_recent,
            "shutdown": self.server.shutdown,
        }.get(method)
        if handler is None:
            raise ValueError(f"Unsupported method: {method}")
        result = handler(params or {})
        return self.server._encode(request_id, method, result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="epi13_local_harness.bridge")
    parser.add_argument("--stdio", action="store_true", help="Run the JSON-line stdio bridge")
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=default_config_path())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.stdio:
        return 0

    runtime = BridgeRuntime(repository_path=args.repository.expanduser(), config_path=args.config.expanduser())
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            envelope = json.loads(line)
            method = str(envelope.get("method", ""))
            request_id = envelope.get("requestId")
            params = envelope.get("params") or {}
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            output = runtime.dispatch(method, params, request_id)
            sys.stdout.write(output)
            sys.stdout.flush()
        except Exception as exc:
            error_payload = {
                "protocolVersion": 1,
                "requestId": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "method": "error",
                "result": {"ok": False, "error": str(exc)},
            }
            sys.stdout.write(json.dumps(error_payload, ensure_ascii=True) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
