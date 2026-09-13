from __future__ import annotations

import subprocess
from pathlib import Path

from mncs_harness.atlas_context import (
    CAPSULE_MAX_CHARS,
    AtlasContext,
    find_atlas_root,
    load_atlas_context,
)
from mncs_harness.cli import build_parser
from mncs_harness.prompts import system_prompt


def _atlas_fixture(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "mncs-demo"
    atlas = tmp_path / "mncs-atlas"
    workspace.mkdir()
    (atlas / "registry").mkdir(parents=True)
    (atlas / "registry" / "__main__.py").write_text("", encoding="utf-8")
    (atlas / "registry" / "compiled.json").write_text("{}", encoding="utf-8")
    return workspace, atlas


def test_find_atlas_root_accepts_explicit_local_checkout(tmp_path: Path) -> None:
    workspace, atlas = _atlas_fixture(tmp_path)

    assert find_atlas_root(workspace, atlas) == atlas.resolve()


def test_load_atlas_context_uses_local_cli_and_bounds_output(tmp_path: Path) -> None:
    workspace, atlas = _atlas_fixture(tmp_path)
    capsule = "MNCS FAMILY CONTEXT\nRegistry revision: test\nProject: mncs-demo"
    calls: list[dict[str, object]] = []

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"args": args, **kwargs})
        return subprocess.CompletedProcess(args[0], 0, capsule + "\n", "")  # type: ignore[arg-type]

    result = load_atlas_context(workspace, atlas_root=atlas, runner=runner)

    assert result == AtlasContext("available", capsule, atlas.resolve())
    assert calls[0]["cwd"] == atlas
    command = calls[0]["args"][0]  # type: ignore[index]
    assert command[1:4] == ["-m", "registry", "context"]  # type: ignore[index]
    assert command[4] == str(workspace.resolve())  # type: ignore[index]
    assert calls[0]["timeout"] == 2.0


def test_load_atlas_context_fails_open_for_missing_or_invalid_sources(tmp_path: Path) -> None:
    workspace, atlas = _atlas_fixture(tmp_path)
    assert load_atlas_context(workspace, atlas_root=tmp_path / "missing").status == "unavailable"

    malformed = subprocess.CompletedProcess(["atlas"], 0, "not a capsule", "")
    result = load_atlas_context(
        workspace,
        atlas_root=atlas,
        runner=lambda *args, **kwargs: malformed,
    )
    assert result.status == "stale"
    assert "invalid capsule" in result.detail

    oversized = subprocess.CompletedProcess(
        ["atlas"], 0, "MNCS FAMILY CONTEXT\n" + "x" * CAPSULE_MAX_CHARS, ""
    )
    result = load_atlas_context(
        workspace,
        atlas_root=atlas,
        runner=lambda *args, **kwargs: oversized,
    )
    assert result.status == "stale"
    assert "size bound" in result.detail

    def timed_out(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(kwargs.get("args", args[0]), 2.0)  # type: ignore[index]

    result = load_atlas_context(workspace, atlas_root=atlas, runner=timed_out)
    assert result.status == "stale"
    assert "timed out" in result.detail


def test_system_prompt_includes_only_the_supplied_capsule(tmp_path: Path) -> None:
    capsule = "MNCS FAMILY CONTEXT\nProject: mncs-demo\nOwner: source-migration"
    prompt = system_prompt("e4b", tmp_path, atlas_context="capsule:\n" + capsule)

    assert "capsule:\n" + capsule in prompt
    assert "mncs-demo" in prompt


def test_submit_accepts_an_explicit_workspace_for_preflight() -> None:
    args = build_parser().parse_args(
        [
            "submit",
            "inspect the project",
            "--workspace",
            "/tmp/mncs-demo",
            "--worker",
            "worker-01",
            "--model-name",
            "model:tag",
        ]
    )

    assert args.workspace == Path("/tmp/mncs-demo")
