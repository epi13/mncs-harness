from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .models import HarnessConfig
from .router import plan_route


@dataclass(frozen=True)
class EvalCase:
    task: str
    expected_role: str
    name: str


def load_cases(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                payload = json.loads(line)
                cases.append(
                    EvalCase(
                        task=str(payload["task"]),
                        expected_role=str(payload["expected_role"]),
                        name=str(payload.get("name", f"line-{line_number}")),
                    )
                )
            except (json.JSONDecodeError, KeyError) as exc:
                raise ValueError(f"Invalid evaluation case at {path}:{line_number}: {exc}") from exc
    return cases


def evaluate_routes(cases: list[EvalCase], config: HarnessConfig) -> tuple[int, list[str]]:
    failures: list[str] = []
    for case in cases:
        actual = plan_route(case.task, config).primary_role
        if actual != case.expected_role:
            failures.append(
                f"{case.name}: expected {case.expected_role}, got {actual}: {case.task}"
            )
    return len(cases) - len(failures), failures
