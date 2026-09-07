#!/usr/bin/env python3
"""Validate docs/conversion-ledger.json and regenerate docs/conversion-ledger.md.

CI gate: fails when the JSON is malformed, uses an unknown status, leaves a
production module uncovered, references a missing kernel file or an unknown
pressure ID, or when the checked-in Markdown drifts from the JSON.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
JSON_PATH = REPO / "docs" / "conversion-ledger.json"
MD_PATH = REPO / "docs" / "conversion-ledger.md"
PRESSURE_PATH = REPO / "docs" / "language-pressure.md"

STATUSES = {
    "MNCS-EXECUTED",
    "HOST-EFFECT-BOUNDARY",
    "MIGRATION-DEBT",
    "TEST-BUILD-ONLY",
}


def fail(message: str) -> None:
    print(f"conversion-ledger: {message}", file=sys.stderr)
    raise SystemExit(1)


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
