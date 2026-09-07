#!/usr/bin/env python3
"""Validate docs/conversion-ledger.json and regenerate docs/conversion-ledger.md.

CI gate: fails when the JSON is malformed, uses an unknown status, leaves a
production module uncovered, references a missing kernel file or an unknown
pressure ID, retains any MIGRATION-DEBT, lets an MNCS-EXECUTED entry drift
from a real shipped kernel export, lets the language handoff drift from the
ledger and pressure doc, leaves transitional Python execution in src/, or
when the checked-in Markdown drifts from the JSON.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
JSON_PATH = REPO / "docs" / "conversion-ledger.json"
MD_PATH = REPO / "docs" / "conversion-ledger.md"
PRESSURE_PATH = REPO / "docs" / "language-pressure.md"
HANDOFF_PATH = REPO / "docs" / "language-handoff.json"
ARTIFACTS_DIR = REPO / "src" / "mncs_harness" / "_mncs_artifacts"

STATUSES = {
    "MNCS-EXECUTED",
    "HOST-EFFECT-BOUNDARY",
    "MIGRATION-DEBT",
    "TEST-BUILD-ONLY",
}


def fail(message: str) -> None:
    print(f"conversion-ledger: {message}", file=sys.stderr)
    raise SystemExit(1)


def _manifest_exports() -> set[str]:
    try:
        manifest = json.loads((ARTIFACTS_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail(f"unreadable artifact manifest: {exc}")
    exports: set[str] = set()
    for entry in manifest.get("artifacts", {}).values():
        exports.update(entry.get("exports", []))
    if not exports:
        fail("artifact manifest has no exports")
    return exports


def _check_executed_kernel_mapping(entries: list[dict]) -> None:
    """Every MNCS-EXECUTED entry must name a real shipped kernel export.

    Entries cite exports as ``::function`` (preferred) or must at least name
    the ``mncs_exec``/kernel path they execute through; anything else is a
    vacuous claim. ``::`` names must exist in the shipped MANIFEST exports.
    """
    exports = _manifest_exports()
    for entry in entries:
        if entry["status"] != "MNCS-EXECUTED":
            continue
        detail = entry["detail"]
        cited = set(re.findall(r"::([A-Za-z_][A-Za-z0-9_]*)", detail))
        unknown = sorted(name for name in cited if name not in exports)
        if unknown:
            fail(f"{entry['module']}.{entry['item']} cites unknown kernel exports {unknown}")
        if not cited and "mncs_exec" not in detail and "mncs/harness_" not in detail:
            fail(
                f"{entry['module']}.{entry['item']} names no kernel export, "
                "mncs_exec path, or mncs/harness_ source"
            )


def _check_no_transitional_python() -> None:
    """The transitional Python decision fallback must stay removed."""
    if (REPO / "src" / "mncs_harness" / "mncs_logic.py").exists():
        fail("src/mncs_harness/mncs_logic.py reappeared; the mirror lives in tests/ only")
    offenders = []
    for path in (REPO / "src" / "mncs_harness").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "TRANSITIONAL_PYTHON_DECISIONS" in text or "mncs_logic" in text or "mncs_oracle" in text:
            offenders.append(path.name)
    if offenders:
        fail(f"transitional Python decision surface in src/: {offenders}")


def _check_language_handoff(entries: list[dict], pressure_ids: set[str]) -> None:
    """The language handoff must agree with the ledger and the pressure doc."""
    try:
        handoff = json.loads(HANDOFF_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail(f"unreadable language handoff: {exc}")
    if handoff.get("schema") != "mncs.harness-language-handoff.v1":
        fail("unknown handoff schema")
    items = handoff.get("items", [])
    if not items:
        fail("handoff has no items")
    by_id = {}
    for item in items:
        for key in ("id", "status", "blocked_surfaces", "workaround", "diagnostic", "desired"):
            if key not in item or item[key] is None:
                fail(f"handoff item missing {key}: {item.get('id')}")
        if item["id"] not in pressure_ids:
            fail(f"handoff references unknown pressure {item['id']}")
        if item["status"] not in ("open", "open-unblocking", "closed"):
            fail(f"handoff item {item['id']} has unknown status {item['status']!r}")
        if item["status"] == "open" and not item["blocked_surfaces"]:
            fail(f"open handoff item {item['id']} lists no blocked harness surface")
        reproducer = item.get("reproducer")
        if reproducer and not (REPO / reproducer).exists():
            fail(f"handoff item {item['id']} claims missing reproducer {reproducer}")
        by_id[item["id"]] = item

    ledger_modules = {entry["module"] for entry in entries}
    blocked_pressures: set[str] = set()
    for entry in entries:
        if not entry["status"].startswith("BLOCKED:"):
            continue
        pressure = entry["status"].split("BLOCKED:", 1)[1]
        blocked_pressures.add(pressure)
        item = by_id.get(pressure)
        if item is None:
            fail(f"BLOCKED ledger entry {entry['module']}.{entry['item']} has no handoff item")
        if item["status"] != "open":
            fail(f"handoff item {pressure} must be open while ledger entries are BLOCKED on it")
    for pressure in blocked_pressures:
        surfaces = by_id[pressure]["blocked_surfaces"]
        for surface in surfaces:
            module = surface.split(".")[0]
            if module not in ledger_modules:
                fail(f"handoff surface {surface} names unknown ledger module {module}")
    order = handoff.get("recommended_order", [])
    if sorted(order) != sorted(item["id"] for item in items if item["status"] != "closed"):
        fail("handoff recommended_order must list exactly the non-closed items")
    toolchain = handoff.get("toolchain", {})
    for key in ("revision", "executor_release", "executor_digest", "artifact_manifest_digest"):
        if not toolchain.get(key):
            fail(f"handoff toolchain missing {key}")
    manifest_digest = "sha256:" + hashlib.sha256(
        (ARTIFACTS_DIR / "MANIFEST.json").read_bytes()
    ).hexdigest()
    if toolchain["artifact_manifest_digest"] != manifest_digest:
        fail("handoff manifest digest does not match shipped MANIFEST.json")


def main() -> None:
    try:
        ledger = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail(f"unreadable JSON: {exc}")
    if ledger.get("schema") != "mncs.harness-conversion-ledger.v1":
        fail("unknown schema")
    entries = ledger.get("entries", [])
    if not entries:
        fail("no entries")

    pressure_ids = set(re.findall(r"HARNESS-PRESSURE-\d{3}", PRESSURE_PATH.read_text(encoding="utf-8")))
    kernel_map = ledger.get("kernel_map", {})
    for module, path in kernel_map.items():
        if not (REPO / path).is_file():
            fail(f"kernel file missing: {path}")

    by_status: dict[str, int] = {}
    covered_modules: set[str] = set()
    for index, entry in enumerate(entries):
        for key in ("module", "item", "status", "detail"):
            if not entry.get(key):
                fail(f"entry {index} missing {key}")
        status = entry["status"]
        if status.startswith("BLOCKED:"):
            if status.split("BLOCKED:", 1)[1] not in pressure_ids:
                fail(f"entry {index} references unknown pressure {status}")
        elif status not in STATUSES:
            fail(f"entry {index} has unknown status {status!r}")
        by_status[status] = by_status.get(status, 0) + 1
        covered_modules.add(entry["module"])

    src_modules = {
        path.stem for path in (REPO / "src" / "mncs_harness").glob("*.py")
    } | {"mncs"}
    missing = sorted(src_modules - covered_modules)
    if missing:
        fail(f"modules without ledger entries: {missing}")

    if "MIGRATION-DEBT" in by_status:
        fail(f"{by_status['MIGRATION-DEBT']} MIGRATION-DEBT entries remain; closure requires zero")

    _check_executed_kernel_mapping(entries)
    _check_no_transitional_python()
    _check_language_handoff(entries, pressure_ids)

    lines = [
        "# MNCS conversion ledger",
        "",
        "Source of truth: `docs/conversion-ledger.json` (validated in CI by",
        "`scripts/check_conversion_ledger.py`). Every production module has at",
        "least one entry; statuses are `MNCS-EXECUTED`, `HOST-EFFECT-BOUNDARY`,",
        "`BLOCKED:<pressure-id>`, `MIGRATION-DEBT`, `TEST-BUILD-ONLY`.",
        "",
        "## Totals",
        "",
    ]
    for status in sorted(by_status):
        lines.append(f"- `{status}`: {by_status[status]}")
    lines += ["", "## Entries", ""]
    for entry in sorted(entries, key=lambda item: (item["module"], item["item"])):
        lines.append(
            f"- `{entry['module']}.{entry['item']}` — **{entry['status']}** — {entry['detail']}"
        )
    lines.append("")
    rendered = "\n".join(lines)
    if not MD_PATH.is_file() or MD_PATH.read_text(encoding="utf-8") != rendered:
        MD_PATH.write_text(rendered, encoding="utf-8")
        fail("conversion-ledger.md was stale; regenerated — commit the update")
    print(f"conversion-ledger OK: {len(entries)} entries across {len(covered_modules)} modules")


if __name__ == "__main__":
    main()
