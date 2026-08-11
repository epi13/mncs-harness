"""Deterministic inspectable view of current distributed capability facts."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .fabric import FabricStatus


def build_capability_graph(
    status: FabricStatus,
    *,
    workspace: Path | None = None,
    controller_tools: Iterable[str] = (),
) -> dict[str, object]:
    controller: dict[str, object] = {}
    if workspace is not None:
        controller["workspace"] = str(workspace.expanduser().resolve())
    tools = sorted(set(str(name) for name in controller_tools if str(name)))
    if tools:
        controller["tools"] = tools

    workers: list[dict[str, object]] = []
    for source in sorted(status.workers, key=lambda item: str(item.get("worker_id", ""))):
        worker: dict[str, object] = {
            "worker_identity": str(source.get("worker_id", "unknown")),
            "availability": str(source.get("availability", "UNKNOWN")),
            "capability_inventory_status": str(
                source.get("capability_inventory_status")
                or source.get("model_inventory_status")
                or "UNKNOWN"
            ),
        }
        observation = source.get("capability_observation")
        capabilities: list[dict[str, object]] = []
        if (
            worker["availability"] == "AVAILABLE"
            and worker["capability_inventory_status"] == "CURRENT"
            and isinstance(observation, dict)
            and isinstance(observation.get("capabilities"), list)
        ):
            for entry in observation["capabilities"]:
                if not isinstance(entry, dict):
                    continue
                capabilities.append(
                    {
                        "kind": str(entry.get("kind", "other")),
                        "namespace": str(entry.get("namespace", "unknown")),
                        "name": str(entry.get("name", "unknown")),
                        "capability_identity": entry.get("capability_identity"),
                    }
                )
        elif isinstance(source.get("model_inventory"), list):
            capabilities.extend(
                {
                    "kind": "model",
                    "namespace": "legacy-worker-inventory",
                    "name": str(model.get("name") or model.get("model")),
                }
                for model in source["model_inventory"]
                if isinstance(model, dict) and (model.get("name") or model.get("model"))
            )
        if capabilities:
            worker["capabilities"] = sorted(
                capabilities,
                key=lambda item: (str(item["kind"]), str(item["namespace"]), str(item["name"])),
            )
        snapshot = source.get("resource_snapshot")
        if isinstance(snapshot, dict):
            hardware: dict[str, object] = {}
            if snapshot.get("cpu_logical_count") is not None:
                hardware["cpu_logical_count"] = snapshot["cpu_logical_count"]
            accelerators = snapshot.get("accelerators")
            if isinstance(accelerators, list) and accelerators:
                hardware["accelerators"] = [
                    {
                        key: accelerator[key]
                        for key in ("backend", "name", "execution_probe")
                        if key in accelerator
                    }
                    for accelerator in accelerators
                    if isinstance(accelerator, dict)
                ]
            if hardware:
                worker["hardware"] = hardware
        workers.append(worker)
    graph: dict[str, object] = {"workers": workers}
    if controller:
        graph["controller"] = controller
    return graph
