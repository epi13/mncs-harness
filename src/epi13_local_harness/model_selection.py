"""Inventory-aware model selection for Fabric-backed roles.

The semantic router chooses a *role/lane*. This module chooses an installed
worker-local Ollama model for that role without assuming every worker has the
same fixed tag. Exact configured tags win; otherwise the fallback policy is
small and deterministic and reports why a model was selected.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

_CODE_HINTS = ("code", "coder", "devstral", "qwen", "granite")


@dataclass(frozen=True)
class ModelSelection:
    role: str
    configured_model: str
    selected_model: str
    stored_size_bytes: int
    reason: str


def _model_name(item: dict[str, Any]) -> str:
    return str(item.get("name") or item.get("model") or "").strip()


def _size(item: dict[str, Any]) -> int:
    value = item.get("size")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _dedupe_models(models: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for item in models:
        name = _model_name(item)
        if name and name not in by_name:
            by_name[name] = dict(item)
    return [by_name[name] for name in sorted(by_name)]


def _is_code_specialist(name: str) -> bool:
    lowered = name.casefold()
    return any(token in lowered for token in _CODE_HINTS)


def select_installed_model(
    role: str,
    configured_model: str,
    models: Iterable[dict[str, Any]],
) -> ModelSelection | None:
    """Resolve one role to an installed model using a deterministic policy.

    Policy order:
    1. exact configured tag if present;
    2. cheap/chat role -> smallest installed model;
    3. coder -> largest code-hinted model not exceeding 10 GiB when possible,
       otherwise the smallest code-hinted model, then the smallest model;
    4. reviewer -> largest non-code-specialist model, then largest model;
    5. general/tool role -> median-by-size installed model.

    Presence is not a capability proof. The returned reason is intentionally
    explicit so metrics can distinguish configured matches from inventory
    fallback choices.
    """

    available = _dedupe_models(models)
    if not available:
        return None

    exact = next((item for item in available if _model_name(item) == configured_model), None)
    if exact is not None:
        return ModelSelection(
            role=role,
            configured_model=configured_model,
            selected_model=configured_model,
            stored_size_bytes=_size(exact),
            reason="configured model is installed on the Fabric worker inventory",
        )

    sized = sorted(available, key=lambda item: (_size(item) or 2**63, _model_name(item)))

    if role == "e2b":
        chosen = sized[0]
        reason = "configured model missing; selected smallest installed model for cheap/chat role"
    elif role == "coder":
        hinted = [item for item in available if _is_code_specialist(_model_name(item))]
        if hinted:
            ten_gib = 10 * 1024 * 1024 * 1024
            bounded = [item for item in hinted if 0 < _size(item) <= ten_gib]
            if bounded:
                chosen = max(bounded, key=lambda item: (_size(item), _model_name(item)))
                reason = "configured model missing; selected largest code-hinted model within 10 GiB"
            else:
                chosen = min(hinted, key=lambda item: (_size(item) or 2**63, _model_name(item)))
                reason = "configured model missing; selected smallest code-hinted installed model"
        else:
            chosen = sized[0]
            reason = "configured model missing; no code-hinted model found, selected smallest installed model"
    elif role == "reviewer":
        general = [item for item in available if not _is_code_specialist(_model_name(item))]
        pool = general or available
        chosen = max(pool, key=lambda item: (_size(item), _model_name(item)))
        reason = (
            "configured model missing; selected largest installed general model for reviewer role"
            if general
            else "configured model missing; selected largest installed model for reviewer role"
        )
    else:
        by_size = sorted(available, key=lambda item: (_size(item), _model_name(item)))
        chosen = by_size[len(by_size) // 2]
        reason = "configured model missing; selected median-size installed model for general/tool role"

    return ModelSelection(
        role=role,
        configured_model=configured_model,
        selected_model=_model_name(chosen),
        stored_size_bytes=_size(chosen),
        reason=reason,
    )
