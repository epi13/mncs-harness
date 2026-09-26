"""Regression tests for the enforcement-boundary export-spelling gate.

The shipped artifacts use backend-specific export spellings
(research-bytecode keeps source names; portable-wasm-mvp mangles them).
The gate must accept either spelling per required export and fail closed
with the artifact named when neither is present.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "mncs_harness_check",
    Path(__file__).resolve().parents[1] / "scripts" / "mncs_harness_check.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)


def test_wasm_spelling_matches_shipped_atlas_artifact() -> None:
    assert (
        _mod._wasm_spelling("mncs.harness.atlas.v1", "fold_pair")
        == "mncs_mncs_harness_atlas_v1__fold__pair"
    )


def test_source_spelling_accepted_without_mangling() -> None:
    actual = {"fold_pair", "fold4", "dispatch_gate", "tool_admission"}
    assert (
        _mod._missing_exports(
            "mncs.harness.atlas.v1",
            "mncs-research-bytecode",
            {"fold_pair", "dispatch_gate"},
            actual,
        )
        == set()
    )


def test_wasm_spelling_accepted_for_mangled_exports() -> None:
    actual = {
        "mncs_mncs_harness_atlas_v1__dispatch__gate",
        "mncs_mncs_harness_atlas_v1__fold4",
        "mncs_mncs_harness_atlas_v1__fold__all____spec__f10cf230",
        "mncs_mncs_harness_atlas_v1__fold__pair",
        "mncs_mncs_harness_atlas_v1__tool__admission",
    }
    assert (
        _mod._missing_exports(
            "mncs.harness.atlas.v1",
            "mncs-portable-wasm-mvp",
            {"fold_pair", "fold4", "dispatch_gate", "tool_admission"},
            actual,
        )
        == set()
    )


def test_missing_export_fails_closed_with_name() -> None:
    assert _mod._missing_exports(
        "mncs.harness.atlas.v1",
        "mncs-portable-wasm-mvp",
        {"fold_pair", "dispatch_gate"},
        {"mncs_mncs_harness_atlas_v1__fold__pair"},
    ) == {"dispatch_gate"}
