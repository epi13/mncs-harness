#!/usr/bin/env python3
"""Bounded worker-local Ollama runtime probe.

This is a Fabric artifact, not a Harness policy. It reports executable
provenance, daemon/client version, model inventory, and one load attempt
without hard-coding model families.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import stat
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


def _run(argv: list[str], timeout: float = 8.0) -> dict[str, object]:
    try:
        completed = subprocess.run(
            argv, check=False, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"argv": argv, "error": str(exc)}
    return {
        "argv": argv,
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "")[:4000],
        "stderr": (completed.stderr or "")[:2000],
    }


def _http(url: str, payload: dict[str, object] | None = None, timeout: float = 20.0) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="GET" if data is None else "POST",
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
        return {"url": url, "ok": True, "body": json.loads(body)}
    except Exception as exc:  # probe must return evidence, not raise
        detail = str(exc)
        if isinstance(exc, urllib.error.HTTPError):
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
            except Exception:
                detail = str(exc)
        return {"url": url, "ok": False, "error": detail}


def main() -> int:
    executable = shutil.which("ollama")
    report: dict[str, object] = {
        "probe": "mncs-fabric.ollama-runtime.v0.1",
        "which": executable,
        "env": {key: os.environ.get(key) for key in ("OLLAMA_HOST", "OLLAMA_MODELS", "HOME") if os.environ.get(key)},
    }
    if executable:
        path = Path(executable)
        info = path.stat()
        report["executable"] = {
            "path": str(path.resolve()),
            "size": info.st_size,
            "mode": stat.filemode(info.st_mode),
            "uid": info.st_uid,
            "gid": info.st_gid,
        }
        report["cli_version"] = _run([executable, "--version"])
        report["rpm"] = _run(["rpm", "-q", "ollama", "--qf", "%{NAME}-%{VERSION}-%{RELEASE}\\n"])
    report["api_version"] = _http("http://127.0.0.1:11434/api/version")
    report["tags"] = _http("http://127.0.0.1:11434/api/tags")
    report["ps"] = _http("http://127.0.0.1:11434/api/ps")
    models = []
    tags = report["tags"]
    if isinstance(tags, dict) and tags.get("ok") and isinstance(tags.get("body"), dict):
        models = [item.get("name") for item in tags["body"].get("models", []) if isinstance(item, dict)]
    report["model_names"] = models
    requested = os.environ.get("MNCS_PROBE_MODEL")
    if len(sys.argv) > 1 and sys.argv[1]:
        requested = sys.argv[1]
    if not requested:
        requested = next((name for name in models if "granite" in name.lower()), None)
    if not requested:
        requested = next((name for name in models if name), None)
    if requested:
        report["generate"] = _http(
            "http://127.0.0.1:11434/api/generate",
            {
                "model": requested,
                "prompt": "Reply with exactly: MNCS_OLLAMA_PROBE_OK",
                "stream": False,
                "options": {"num_predict": 16, "temperature": 0},
            },
            timeout=90.0,
        )
        report["probed_model"] = requested
    print(json.dumps(report, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
