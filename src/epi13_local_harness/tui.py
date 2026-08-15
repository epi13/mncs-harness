from __future__ import annotations

import argparse
import json
import shlex
import shutil
import sys
from pathlib import Path
from typing import Sequence

from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
)
from textual.worker import get_current_worker

from .agent import LocalAgent
from .commons import CommonsStatus
from .commons_operator import CommonsOperatorService
from .config import default_config_path, load_config
from .fabric import FabricStatus
from .metrics import MetricsStore
from .models import AgentResult, HarnessConfig, RoutePlan, RoutingOverride
from .ollama import OllamaClient, OllamaError
from .router import plan_route
from .semantic_router import RouterRuntimeStatus, router_status


def role_options(config: HarnessConfig) -> list[tuple[str, str]]:
    """Return model choices suitable for a Textual Select widget."""
    options = [("Automatic routing", "")]
    options.extend(
        (f"{role} — {model.name}", role) for role, model in config.models.items()
    )
    return options


def worker_options(status: FabricStatus) -> list[tuple[str, str]]:
    """Return every known worker, including unavailable registry members."""

    return [
        (
            f"{worker.get('worker_id')} — {worker.get('availability', 'UNKNOWN')}",
            str(worker.get("worker_id")),
        )
        for worker in sorted(status.workers, key=lambda item: str(item.get("worker_id")))
        if worker.get("source") != "local" and worker.get("worker_id")
    ]


def worker_model_options(
    status: FabricStatus, worker_id: str | None = None
) -> list[tuple[str, str]]:
    """Return the union of worker inventories, preserving loaded placement labels."""

    placements: dict[str, list[str]] = {}
    loaded: set[tuple[str, str]] = set()
    for worker in status.workers:
        current_worker = str(worker.get("worker_id") or "")
        if worker_id and current_worker != worker_id:
            continue
        for model in worker.get("model_inventory") or ():
            if not isinstance(model, dict):
                continue
            name = str(model.get("name") or model.get("model") or "")
            if not name:
                continue
            placements.setdefault(name, []).append(current_worker)
            if model.get("loaded"):
                loaded.add((current_worker, name))
    options: list[tuple[str, str]] = []
    for name, workers in sorted(placements.items()):
        marker = "●" if any((worker, name) in loaded for worker in workers) else "○"
        options.append((f"{marker} {name} — {', '.join(sorted(workers))}", name))
    return options


def parse_image_paths(raw: str, workspace: Path) -> list[Path]:
    """Parse a shell-like image list, resolving relative paths inside the workspace."""
    if not raw.strip():
        return []
    tokens = shlex.split(raw.replace(",", " "))
    images: list[Path] = []
    for token in tokens:
        candidate = Path(token).expanduser()
        if not candidate.is_absolute():
            candidate = workspace / candidate
        resolved = candidate.resolve()
        if not resolved.is_file():
            raise ValueError(f"Image does not exist: {token}")
        images.append(resolved)
    return images


def route_summary(plan: RoutePlan) -> str:
    """Build a concise plain-text route summary for tests and accessibility."""
    parts = [f"route={' -> '.join(plan.all_roles)}"]
    parts.append(f"mode={plan.routing_override.mode}")
    if plan.lane:
        parts.append(f"lane={plan.lane}")
    if plan.semantic:
        parts.extend(
            [
                f"score={plan.semantic.selected_score:.3f}",
                f"margin={plan.semantic.margin:.3f}",
                f"backend={plan.semantic.backend}",
            ]
        )
        if plan.semantic.latency_ms is not None:
            parts.append(f"router_ms={plan.semantic.latency_ms:.1f}")
    if plan.reasons:
        parts.append("reasons=" + "; ".join(plan.reasons))
    return " | ".join(parts)


def router_status_summary(status: RouterRuntimeStatus) -> str:
    """Return an honest one-line semantic-router state description."""
    summary = (
        f"state={status.state} | enabled={status.enabled} | "
        f"backend={status.backend} | model={status.model or '(none)'} | "
        f"revision={status.revision or '(none)'} | cached={status.cached} | "
        f"active={status.active}"
    )
    if status.detail:
        summary += f" | detail={status.detail}"
    return summary


def router_status_renderable(status: RouterRuntimeStatus) -> Panel:
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    table.add_row("State", status.state)
    table.add_row("Enabled", str(status.enabled))
    table.add_row("Mode", status.mode)
    table.add_row("Backend", status.backend)
    table.add_row("Model", status.model or "(not configured)")
    table.add_row("Revision", status.revision or "(not configured)")
    table.add_row("Device", status.device)
    table.add_row("Cached", str(status.cached))
    table.add_row("Active", str(status.active))
    table.add_row("Local only", str(status.local_files_only))
    table.add_row("Cache", str(status.cache_directory))
    if status.missing_dependencies:
        table.add_row("Missing", ", ".join(status.missing_dependencies))
    if status.detail:
        table.add_row("Detail", status.detail)
    border = "green" if status.state == "active" else "yellow"
    return Panel(table, title="Semantic routing backend", border_style=border)


def fabric_status_summary(status: FabricStatus) -> str:
    if not status.enabled:
        return "state=disabled"
    model_count = sum(
        int(worker.get("model_count") or len(worker.get("model_names") or ()))
        for worker in status.workers
        if worker.get("source") == "remote"
    )
    summary = (
        f"mode={status.controller_mode} | controller={status.controller_state} "
        f"authority={status.fleet_authority} | fleet={status.fleet_state} "
        f"| execution={status.execution_transport} | inventory={status.inventory_transport} "
        f"workers={status.available_workers}/{len(status.workers)} "
        f"| accelerators={status.accelerator_count} "
        f"| worker-models={model_count} "
        f"| offload-capable={status.offload_capable_count}"
    )
    if status.detail:
        summary += f" | detail={status.detail}"
    return summary


def commons_status_summary(status: CommonsStatus) -> str:
    if not status.enabled:
        return "state=disabled"
    summary = (
        f"state={'ready' if status.ready else 'unavailable'} | code={status.code} "
        f"| profile={status.profile or 'unknown'} "
        f"| store={'healthy' if status.store_healthy else 'unavailable'}"
    )
    if status.record_count is not None:
        summary += f" | records={status.record_count}"
    if status.detail:
        summary += f" | detail={status.detail}"
    return summary


def fabric_status_renderable(status: FabricStatus) -> Panel:
    table = Table(expand=True)
    table.add_column("Worker", style="cyan")
    table.add_column("Availability")
    table.add_column("Source")
    table.add_column("CPU/RAM")
    table.add_column("Accelerators")
    table.add_column("Models")
    table.add_column("Capabilities")
    for worker in status.workers:
        snapshot = worker.get("resource_snapshot") or {}
        cpu = snapshot.get("cpu_logical_count")
        ram = snapshot.get("host_memory_available_bytes")
        ram_text = "UNKNOWN" if ram is None else f"{ram / (1024 ** 3):.1f} GiB"
        accelerators = snapshot.get("accelerators") or []
        accelerator_text = str(len(accelerators))
        if any(item.get("execution_probe") == "PASS" for item in accelerators):
            accelerator_text += " (probe PASS)"
        inventory_status = str(worker.get("model_inventory_status") or "UNKNOWN")
        if worker.get("model_inventory_error"):
            model_text = "scan failed"
        elif "model_names" in worker:
            model_text = str(worker.get("model_count") or len(worker.get("model_names") or ()))
        else:
            model_text = inventory_status
        observation = worker.get("capability_observation")
        capability_entries = (
            observation.get("capabilities", []) if isinstance(observation, dict) else []
        )
        capability_text = str(worker.get("capability_inventory_status") or "UNKNOWN")
        if capability_entries:
            kinds: dict[str, int] = {}
            for capability in capability_entries:
                if isinstance(capability, dict):
                    kind = str(capability.get("kind") or "other")
                    kinds[kind] = kinds.get(kind, 0) + 1
            capability_text += " " + ", ".join(
                f"{kind}:{count}" for kind, count in sorted(kinds.items())
            )
        table.add_row(
            str(worker.get("worker_id", "unknown")),
            str(worker.get("availability", worker.get("state", "UNKNOWN"))),
            str(worker.get("source", "unknown")),
            f"{cpu or 'UNKNOWN'} / {ram_text}",
            accelerator_text,
            model_text,
            capability_text,
        )
    if not status.workers:
        table.add_row("—", "—", "—", "—", "—", "—", "—")
    if status.detail:
        table.caption = status.detail
    if status.last_inference:
        last = status.last_inference
        table.caption = (table.caption + " | " if table.caption else "") + (
            "last: "
            f"worker={last.get('worker') or 'none'} "
            f"placement={last.get('placement') or 'unknown'} "
            f"disposition={last.get('disposition') or 'unknown'}"
        )
    border = "green" if status.state == "available" else "yellow"
    if status.state == "unavailable":
        border = "red"
    return Panel(
        Group(
            Text(
                f"controller={status.controller_id} | {fabric_status_summary(status)}"
            ),
            table,
        ),
        title="MNCS Fabric",
        border_style=border,
    )


def route_renderable(plan: RoutePlan) -> Panel:
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    table.add_row("Route", " → ".join(plan.all_roles))
    table.add_row("Requested mode", plan.routing_override.mode)
    if plan.routing_override.worker:
        table.add_row("Requested worker", plan.routing_override.worker)
    if plan.routing_override.model:
        table.add_row("Requested model", plan.routing_override.model)
    table.add_row(
        "Fallback", "enabled" if plan.routing_override.allow_fallback else "disabled"
    )
    table.add_row("Lane", plan.lane or "deterministic / fallback")
    if plan.semantic:
        table.add_row("Router", plan.semantic.backend)
        if plan.semantic.revision:
            table.add_row("Revision", plan.semantic.revision)
        table.add_row("Score", f"{plan.semantic.selected_score:.3f}")
        table.add_row("Margin", f"{plan.semantic.margin:.3f}")
        if plan.semantic.latency_ms is not None:
            table.add_row("Latency", f"{plan.semantic.latency_ms:.1f} ms")
        if plan.semantic.runner_up_lane:
            table.add_row(
                "Runner-up",
                f"{plan.semantic.runner_up_lane} ({plan.semantic.runner_up_score:.3f})",
            )
    table.add_row("Reasons", "\n".join(f"• {reason}" for reason in plan.reasons))
    return Panel(table, title="Routing preview", border_style="cyan")


def result_renderables(result: AgentResult) -> list[object]:
    renderables: list[object] = [route_renderable(result.route)]
    attempts = Table(title="Attempts", expand=True)
    attempts.add_column("Role", style="cyan")
    attempts.add_column("Model")
    attempts.add_column("Provider")
    attempts.add_column("Placement")
    attempts.add_column("Status")
    attempts.add_column("Tools", justify="right")
    for attempt in result.attempts:
        status = (
            "[green]passed[/green]"
            if attempt.verification.passed
            else "[red]failed[/red]"
        )
        provider = attempt.metrics.get("provider") or "unknown"
        placement = (
            attempt.metrics.get("placement_mode")
            or attempt.metrics.get("execution_source")
            or "unknown"
        )
        attempts.add_row(
            attempt.role,
            attempt.model,
            str(provider),
            str(placement),
            status,
            str(len(attempt.tool_executions)),
        )
    renderables.append(attempts)
    renderables.append(
        Panel(
            Markdown(result.final_content or "_No final response._"),
            title="Assistant",
            border_style="green" if result.successful else "red",
        )
    )
    failures = [
        failure
        for attempt in result.attempts
        for failure in attempt.verification.failures
    ]
    if failures:
        renderables.append(
            Panel("\n".join(f"• {failure}" for failure in failures), title="Verification")
        )
    return renderables


def _common_fabric_inventory(status: FabricStatus) -> tuple[dict, ...]:
    workers = [
        worker
        for worker in status.workers
        if worker.get("source") == "remote" and worker.get("availability") == "AVAILABLE"
    ]
    if not workers or any("model_inventory" not in worker for worker in workers):
        return ()
    common_names: set[str] | None = None
    metadata: dict[str, dict] = {}
    for worker in workers:
        inventory = worker.get("model_inventory") or []
        names = {
            str(item.get("name") or item.get("model") or "")
            for item in inventory
            if isinstance(item, dict) and (item.get("name") or item.get("model"))
        }
        common_names = names if common_names is None else common_names & names
        for item in inventory:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("model") or "")
            if name and name not in metadata:
                metadata[name] = dict(item)
    return tuple(metadata[name] for name in sorted(common_names or ()))


def role_models_table(
    config: HarnessConfig,
    local_installed: set[str],
    fabric_status: FabricStatus,
) -> Table:
    """Show configured role preferences separately from live availability."""

    table = Table(title="Role model policy", expand=True)
    table.add_column("Role", style="cyan")
    table.add_column("Provider")
    table.add_column("Preferred")
    table.add_column("Resolved")
    table.add_column("State")
    for role, model in config.models.items():
        if model.provider == "fabric":
            placements = [
                str(worker.get("worker_id"))
                for worker in fabric_status.workers
                if worker.get("availability") == "AVAILABLE"
                and any(
                    isinstance(item, dict)
                    and str(item.get("name") or item.get("model") or "") == model.name
                    for item in worker.get("model_inventory") or ()
                )
            ]
            if not placements:
                resolved = "—"
                state = "[yellow]worker inventory unavailable[/yellow]"
            else:
                resolved = model.name
                state = "[green]" + ", ".join(sorted(placements)) + "[/green]"
        else:
            resolved = model.name
            state = (
                "[green]installed locally[/green]"
                if model.name in local_installed
                else "[red]missing locally[/red]"
            )
        table.add_row(role, model.provider, model.name, resolved, state)
    return table


def fabric_model_inventory_table(status: FabricStatus) -> Table:
    """Render every model reported by every currently available Fabric worker."""

    table = Table(title="Live Fabric worker model inventory", expand=True)
    table.add_column("Worker", style="cyan")
    table.add_column("Model")
    table.add_column("Size", justify="right")
    table.add_column("Family")
    table.add_column("Params")
    table.add_column("Quant")
    table.add_column("State")
    rows = 0
    for worker in status.workers:
        if worker.get("source") != "remote":
            continue
        worker_id = str(worker.get("worker_id") or "unknown")
        inventory = worker.get("model_inventory")
        if isinstance(inventory, list):
            for item in sorted(
                (value for value in inventory if isinstance(value, dict)),
                key=lambda value: str(value.get("name") or value.get("model") or ""),
            ):
                name = str(item.get("name") or item.get("model") or "")
                size = item.get("size")
                size_text = (
                    f"{size / (1024 ** 3):.2f} GiB"
                    if isinstance(size, int) and not isinstance(size, bool) and size >= 0
                    else "—"
                )
                details = item.get("details") if isinstance(item.get("details"), dict) else {}
                table.add_row(
                    worker_id,
                    name or "(unnamed)",
                    size_text,
                    str(details.get("family") or "—"),
                    str(details.get("parameter_size") or "—"),
                    str(details.get("quantization_level") or "—"),
                    "● loaded" if item.get("loaded") else "○ installed",
                )
                rows += 1
        elif worker.get("model_inventory_error"):
            table.add_row(
                worker_id,
                "[red]inventory scan failed[/red]",
                "—",
                "—",
                "—",
                "—",
                "—",
            )
            rows += 1
    if rows == 0:
        table.add_row("—", "No live worker inventory available", "—", "—", "—", "—", "—")
    return table


def residency_table(config: HarnessConfig, status: FabricStatus) -> Table:
    """Render desired resident assignment separately from provider observations."""

    configured = {item.worker_id: item.model for item in config.model_residency.workers}
    table = Table(title="Resident model policy", expand=True)
    table.add_column("Worker", style="cyan")
    table.add_column("Preferred")
    table.add_column("Installed")
    table.add_column("Loaded")
    for worker in status.workers:
        worker_id = str(worker.get("worker_id") or "")
        if not worker_id or worker.get("source") == "local":
            continue
        preferred = configured.get(worker_id)
        inventory = [
            item for item in worker.get("model_inventory") or () if isinstance(item, dict)
        ]
        selected = next(
            (
                item
                for item in inventory
                if str(item.get("name") or item.get("model") or "") == preferred
            ),
            None,
        )
        table.add_row(
            worker_id,
            preferred or "automatic / unresolved",
            "yes" if selected is not None else "no" if preferred else "UNKNOWN",
            "yes" if selected and selected.get("loaded") else "no" if selected else "UNKNOWN",
        )
    if not table.rows:
        table.add_row("—", "No known workers", "UNKNOWN", "UNKNOWN")
    return table


class HarnessTui(App[None]):
    """Interactive terminal interface for the policy-aware local harness."""

    TITLE = "MNCS Harness"
    SUB_TITLE = "Policy-aware local AI"
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+l", "clear_log", "Clear"),
        Binding("ctrl+r", "preview_route", "Route"),
        Binding("ctrl+d", "doctor", "Doctor"),
        Binding("escape", "cancel_work", "Cancel"),
    ]

    CSS = """
    Screen {
        layout: vertical;
        background: $surface;
    }

    #main {
        height: 1fr;
    }

    #sidebar {
        width: 36;
        min-width: 30;
        border-right: solid $primary;
        padding: 1;
    }

    #conversation {
        width: 1fr;
        padding: 0 1;
    }

    .field-label {
        margin-top: 1;
        color: $text-muted;
    }

    #controls {
        height: auto;
        margin-top: 1;
    }

    #controls Button {
        width: 1fr;
        margin: 0 1 1 0;
    }

    #log {
        height: 1fr;
        border: round $primary-background;
        padding: 0 1;
    }

    #composer {
        height: auto;
        min-height: 3;
        padding-top: 1;
    }

    #prompt {
        width: 1fr;
    }

    #send {
        width: 12;
        margin-left: 1;
    }

    #status {
        height: 1;
        padding: 0 1;
        background: $primary-background;
        color: $text;
    }

    Checkbox {
        margin-top: 1;
    }

    #route-active {
        color: $warning;
        margin-top: 1;
    }
    """

    def __init__(
        self,
        config: HarnessConfig,
        workspace: Path,
        *,
        config_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.initial_workspace = workspace.expanduser().resolve()
        self.config_path = config_path
        self.agent = LocalAgent(config)
        self.commons = CommonsOperatorService(self.agent.commons_session)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with VerticalScroll(id="sidebar"):
                yield Label("Workspace", classes="field-label")
                yield Input(value=str(self.initial_workspace), id="workspace")
                yield Label("Routing mode", classes="field-label")
                yield Select[str](
                    [
                        ("Automatic", "AUTO"),
                        ("Role", "ROLE"),
                        ("Exact model", "MODEL"),
                        ("Exact worker", "WORKER"),
                        ("Worker + model", "WORKER_MODEL"),
                    ],
                    value="AUTO",
                    allow_blank=False,
                    id="routing-mode",
                )
                yield Label("Role", classes="field-label")
                yield Select[str](
                    role_options(self.config),
                    allow_blank=False,
                    id="model",
                )
                initial_fabric = self.agent.fabric_status()
                yield Label("Worker", classes="field-label")
                yield Select[str](
                    worker_options(initial_fabric),
                    prompt="No known worker selected",
                    allow_blank=True,
                    id="worker",
                )
                yield Label("Exact model", classes="field-label")
                yield Select[str](
                    worker_model_options(initial_fabric),
                    prompt="No current model selected",
                    allow_blank=True,
                    id="model-name",
                )
                yield Checkbox(
                    "Allow explicit fallback",
                    value=False,
                    id="allow-fallback",
                )
                yield Static("Mode: AUTO", id="route-active")
                yield Label("Images", classes="field-label")
                yield Input(
                    placeholder='Optional paths, e.g. "receipt scan.png" screenshot.png',
                    id="images",
                )
                yield Checkbox(
                    "Auto-approve policy-allowed writes and commands",
                    value=False,
                    id="auto-approve",
                )
                with Container(id="controls"):
                    yield Button("Preview route", id="route")
                    yield Button("Doctor", id="doctor")
                    yield Button("Models", id="models")
                    yield Button("Metrics", id="metrics")
                    yield Button("Fabric", id="fabric")
                    yield Button("Commons", id="commons")
                    yield Button("Clear", id="clear")
            with Vertical(id="conversation"):
                yield RichLog(
                    id="log",
                    markup=True,
                    wrap=True,
                    highlight=True,
                    auto_scroll=True,
                )
                with Horizontal(id="composer"):
                    yield Input(
                        placeholder="Ask the local harness…",
                        id="prompt",
                    )
                    yield Button("Send", id="send", variant="primary")
        yield Static("Ready", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self._log(
            Panel(
                Markdown(
                    "**MNCS Harness TUI**\n\n"
                    "Each prompt is routed independently. Select a role to force it, "
                    "or leave **Automatic routing** selected. Exact worker/model pins "
                    "remain active across prompts until changed and fail closed unless "
                    "fallback is visibly enabled. The semantic encoder "
                    "router and Ollama workers are reported separately. Writes and "
                    "commands are denied unless auto-approval is deliberately enabled."
                ),
                border_style="cyan",
            )
        )
        self.query_one("#prompt", Input).focus()
        current_fabric = self.agent.fabric_status()
        self._log(
            Group(
                Panel(
                    Text(commons_status_summary(self.agent.commons_status())),
                    title="Commons (controller-owned; content untrusted)",
                    border_style="yellow",
                ),
                fabric_status_renderable(current_fabric),
                residency_table(self.config, current_fabric),
            )
        )

    def _log(self, renderable: object) -> None:
        self.query_one("#log", RichLog).write(renderable)

    def _status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    def _set_busy(self, busy: bool, status: str = "Ready") -> None:
        for button_id in (
            "send", "route", "doctor", "models", "metrics", "fabric", "commons"
        ):
            self.query_one(f"#{button_id}", Button).disabled = busy
        self._status(status)

    def _workspace(self) -> Path:
        raw = self.query_one("#workspace", Input).value.strip()
        workspace = Path(raw or ".").expanduser().resolve()
        if not workspace.is_dir():
            raise ValueError(f"Workspace is not a directory: {workspace}")
        return workspace

    def _selected_role(self) -> str | None:
        value = self.query_one("#model", Select).selection
        return str(value) if value else None

    @staticmethod
    def _selection(select: Select) -> str | None:
        value = select.selection
        return str(value) if value else None

    def _routing_override(self) -> RoutingOverride:
        mode = self._selection(self.query_one("#routing-mode", Select)) or "AUTO"
        role = self._selected_role() if mode == "ROLE" else None
        worker = (
            self._selection(self.query_one("#worker", Select))
            if mode in {"WORKER", "WORKER_MODEL"}
            else None
        )
        model = (
            self._selection(self.query_one("#model-name", Select))
            if mode in {"MODEL", "WORKER_MODEL"}
            else None
        )
        requested = RoutingOverride.from_values(
            role=role,
            worker=worker,
            model=model,
            allow_fallback=self.query_one("#allow-fallback", Checkbox).value,
        )
        if requested.mode != mode:
            raise ValueError(f"{mode} routing requires its selected fields")
        return requested

    def _update_routing_label(self) -> None:
        try:
            requested = self._routing_override()
            detail = [f"Mode: {requested.mode}"]
            if requested.role:
                detail.append(f"role={requested.role}")
            if requested.worker:
                detail.append(f"worker={requested.worker}")
            if requested.model:
                detail.append(f"model={requested.model}")
            detail.append(f"fallback={'enabled' if requested.allow_fallback else 'disabled'}")
            self.query_one("#route-active", Static).update(" | ".join(detail))
        except ValueError:
            self.query_one("#route-active", Static).update("Manual route incomplete")

    def _images(self, workspace: Path) -> list[Path]:
        return parse_image_paths(self.query_one("#images", Input).value, workspace)

    def _prompt_text(self) -> str:
        """Return the current prompt without colliding with Textual's internal _task state."""
        return self.query_one("#prompt", Input).value.strip()

    def _show_error(self, exc: BaseException) -> None:
        self._set_busy(False, "Error")
        self._log(Panel(str(exc), title=type(exc).__name__, border_style="red"))

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()

    def action_cancel_work(self) -> None:
        self.workers.cancel_group(self, "agent")
        self.workers.cancel_group(self, "inspection")
        self._set_busy(False, "Cancellation requested")

    def action_preview_route(self) -> None:
        task = self._prompt_text()
        if not task:
            self._show_error(ValueError("Enter a prompt before previewing its route."))
            return
        try:
            workspace = self._workspace()
            images = self._images(workspace)
            plan = plan_route(
                task,
                self.config,
                images,
                routing_override=self._routing_override(),
            )
            model = self.config.models[plan.primary_role]
            if model.provider == "fabric":
                effective, selection = self.agent.fabric_session.resolve_model(
                    plan.primary_role,
                    model,
                    routing_override=plan.routing_override,
                )
                resolution = Table.grid(padding=(0, 1))
                resolution.add_column(style="bold cyan")
                resolution.add_column()
                resolution.add_row(
                    "Resolved worker", selection.worker_id if selection else "unresolved"
                )
                resolution.add_row("Resolved model", effective.name)
                resolution.add_row(
                    "Available", str(selection.available if selection else False)
                )
                resolution.add_row("Loaded", str(selection.loaded if selection else False))
                resolution.add_row(
                    "Resident", str(selection.resident if selection else False)
                )
                resolution.add_row(
                    "Reason", selection.reason if selection else "inventory unavailable"
                )
                renderable: object = Group(
                    route_renderable(plan),
                    Panel(resolution, title="Resolved route", border_style="cyan"),
                )
            else:
                renderable = route_renderable(plan)
        except (OSError, ValueError) as exc:
            self._show_error(exc)
            return
        self._log(renderable)
        self._status(route_summary(plan))

    def action_doctor(self) -> None:
        self._set_busy(True, "Running diagnostics…")
        self.run_inspection("doctor")

    def action_models(self) -> None:
        self._set_busy(True, "Refreshing worker model inventory…")
        self.run_inspection("models")

    def action_metrics(self) -> None:
        self._set_busy(True, "Loading metrics…")
        self.run_inspection("metrics")

    def action_fabric(self) -> None:
        self._set_busy(True, "Refreshing Fabric status…")
        self.run_inspection("fabric")

    def action_commons(self) -> None:
        self._set_busy(True, "Loading controller-local Commons…")
        self.run_inspection("commons")

    def action_send(self) -> None:
        task = self._prompt_text()
        if not task:
            return
        try:
            workspace = self._workspace()
            images = self._images(workspace)
        except (OSError, ValueError) as exc:
            self._show_error(exc)
            return
        try:
            routing_override = self._routing_override()
        except ValueError as exc:
            self._show_error(exc)
            return
        auto_approve = self.query_one("#auto-approve", Checkbox).value
        self._log(Panel(Text(task), title="You", border_style="blue"))
        self.query_one("#prompt", Input).value = ""
        self._set_busy(True, "Routing and running provider…")
        self.run_agent_task(task, workspace, images, routing_override, auto_approve)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "worker":
            worker = self._selection(event.select)
            options = worker_model_options(self.agent.fabric_status(), worker)
            model_select = self.query_one("#model-name", Select)
            model_select.set_options(options)
            if options:
                model_select.value = options[0][1]
            else:
                model_select.clear()
        if event.select.id in {"routing-mode", "model", "worker", "model-name"}:
            self._update_routing_label()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "prompt":
            self.action_send()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "send": self.action_send,
            "route": self.action_preview_route,
            "doctor": self.action_doctor,
            "models": self.action_models,
            "metrics": self.action_metrics,
            "fabric": self.action_fabric,
            "commons": self.action_commons,
            "clear": self.action_clear_log,
        }
        action = actions.get(event.button.id or "")
        if action:
            action()

    @work(thread=True, exclusive=True, group="agent")
    def run_agent_task(
        self,
        task: str,
        workspace: Path,
        images: list[Path],
        routing_override: RoutingOverride,
        auto_approve: bool,
    ) -> None:
        try:
            result = self.agent.run(
                task,
                workspace=workspace,
                images=images,
                routing_override=routing_override,
                auto_approve=auto_approve,
                interactive_approval=False,
            )
        except Exception as exc:
            self.call_from_thread(self._show_error, exc)
            return
        worker = get_current_worker()
        if worker.is_cancelled:
            return
        self.call_from_thread(self._display_result, result)

    def _display_result(self, result: AgentResult) -> None:
        for renderable in result_renderables(result):
            self._log(renderable)
        final_attempt = result.attempts[-1] if result.attempts else None
        if final_attempt and final_attempt.metrics.get("eval_duration"):
            seconds = final_attempt.metrics["eval_duration"] / 1_000_000_000
            tokens = final_attempt.metrics.get("eval_count", 0)
            rate = tokens / seconds if seconds else 0
            detail = f" · {tokens} tokens · {rate:.1f} tok/s"
        else:
            detail = ""
        completion = "Completed" if result.successful else "Completed with failures"
        self._set_busy(False, completion + detail)
        self.query_one("#prompt", Input).focus()

    @work(thread=True, exclusive=True, group="inspection")
    def run_inspection(self, kind: str) -> None:
        try:
            renderable = self._inspection_renderable(kind)
        except Exception as exc:
            self.call_from_thread(self._show_error, exc)
            return
        worker = get_current_worker()
        if worker.is_cancelled:
            return
        self.call_from_thread(self._finish_inspection, renderable, kind)

    def _inspection_renderable(self, kind: str) -> object:
        if kind == "metrics":
            rows = MetricsStore(
                self.config.metrics.path,
                self.config.metrics.store_prompt_text,
            ).recent(20)
            table = Table(title="Recent model attempts", expand=True)
            for column in ("Time", "Role", "Model", "Lane", "Status", "Tools", "tok/s"):
                table.add_column(column)
            for row in rows:
                duration = row.get("eval_duration_ns") or 0
                tokens = row.get("eval_count") or 0
                rate = tokens / (duration / 1_000_000_000) if duration else 0
                table.add_row(
                    str(row.get("created_at", "")),
                    str(row.get("role", "")),
                    str(row.get("model", "")),
                    str(row.get("semantic_lane") or "deterministic"),
                    "pass" if row.get("passed") else "fail",
                    str(row.get("tool_call_count", 0)),
                    f"{rate:.1f}",
                )
            return table if rows else Panel("No model attempts recorded.", title="Metrics")

        semantic = router_status(self.config)
        semantic_panel = router_status_renderable(semantic)
        fabric_status = self.agent.refresh_fabric_inventory()
        if fabric_status is None:
            fabric_status = FabricStatus(False, "disabled", self.config.fabric.controller_id)
        fabric_panel = fabric_status_renderable(fabric_status)
        if kind == "commons":
            return Panel(
                Text(json.dumps({
                    "status": self.commons.status(),
                    "open_work": self.commons.work(limit=100),
                    "warning": "UNTRUSTED DATA — rendered as text; never executed",
                }, indent=2, sort_keys=True)),
                title="Controller-local Commons",
                border_style="yellow",
            )
        if kind == "fabric":
            return fabric_panel

        client = OllamaClient(self.config.ollama)
        local_installed = client.model_names()
        roles = role_models_table(self.config, local_installed, fabric_status)
        remote_models = fabric_model_inventory_table(fabric_status)
        residency = residency_table(self.config, fabric_status)
        if kind == "models":
            return Group(semantic_panel, fabric_panel, residency, roles, remote_models)

        version = client.version()
        diagnostics = Table.grid(padding=(0, 1))
        diagnostics.add_column(style="bold cyan")
        diagnostics.add_column()
        diagnostics.add_row("Python", sys.version.split()[0])
        diagnostics.add_row("Config", str(self.config_path or default_config_path()))
        diagnostics.add_row("Ollama", f"{version} at {self.config.ollama.base_url}")
        diagnostics.add_row("Metrics", str(self.config.metrics.path))
        diagnostics.add_row("Router", router_status_summary(semantic))
        diagnostics.add_row("Fabric", fabric_status_summary(fabric_status))
        diagnostics.add_row("Commons", commons_status_summary(self.agent.commons_status()))
        diagnostics.add_row(
            "Tools",
            ", ".join(
                f"{name}={'yes' if shutil.which(name) else 'no'}"
                for name in ("git", "bash", "shellcheck", "ruff", "pytest")
            ),
        )
        commons_panel = Panel(
            Text(commons_status_summary(self.agent.commons_status())),
            title="Commons (controller-owned; content untrusted)",
            border_style="yellow",
        )
        return Group(
            Panel(diagnostics, title="Doctor", border_style="cyan"),
            semantic_panel,
            commons_panel,
            fabric_panel,
            residency,
            roles,
            remote_models,
        )

    def _finish_inspection(self, renderable: object, kind: str) -> None:
        status = self.agent.fabric_status()
        worker_select = self.query_one("#worker", Select)
        prior_worker = self._selection(worker_select)
        workers = worker_options(status)
        worker_select.set_options(workers)
        worker_values = {value for _label, value in workers}
        if prior_worker in worker_values:
            worker_select.value = prior_worker
        elif workers:
            worker_select.value = workers[0][1]
        else:
            worker_select.clear()
        selected_worker = self._selection(worker_select)
        model_select = self.query_one("#model-name", Select)
        prior_model = self._selection(model_select)
        models = worker_model_options(status, selected_worker)
        model_select.set_options(models)
        model_values = {value for _label, value in models}
        if prior_model in model_values:
            model_select.value = prior_model
        elif models:
            model_select.value = models[0][1]
        else:
            model_select.clear()
        self._update_routing_label()
        self._log(renderable)
        self._set_busy(False, f"{kind.capitalize()} complete")


def run_tui(
    *,
    config_path: Path | None = None,
    workspace: Path | None = None,
) -> None:
    config = load_config(config_path)
    selected_workspace = (workspace or Path.cwd()).expanduser().resolve()
    if not selected_workspace.is_dir():
        raise ValueError(f"Workspace is not a directory: {selected_workspace}")
    HarnessTui(config, selected_workspace, config_path=config_path).run()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mncs-harness-tui",
        description="MNCS Harness TUI",
    )
    parser.add_argument("--config", type=Path, help="Path to a TOML configuration file")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        run_tui(config_path=args.config, workspace=args.workspace)
    except (OSError, ValueError, OllamaError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
