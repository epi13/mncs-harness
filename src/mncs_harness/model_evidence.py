"""MNCS-observed model capability evidence.

Provider claims are not proof. This store records bounded verification outcomes
against opaque implementation identities (worker + model tag).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

DEFAULT_EVIDENCE_PATH = Path.home() / ".local" / "state" / "mncs-harness" / "model-evidence.jsonl"
_MAX_RECORD_BYTES = 8192


@dataclass(frozen=True)
class CapabilityEvidence:
    subject_worker: str
    subject_model: str
    capability: str
    outcome: str
    tier: int
    freshness: str
    recorded_at: str
    validator_identity: str
    failure_class: str | None = None
    execution_receipt: str | None = None
    fixture_identity: str | None = None
    detail: str | None = None

    def public(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def evidence_path() -> Path:
    override = os.environ.get("MNCS_HARNESS_MODEL_EVIDENCE")
    return Path(override).expanduser() if override else DEFAULT_EVIDENCE_PATH


def load_evidence(path: Path | None = None) -> tuple[CapabilityEvidence, ...]:
    selected = path or evidence_path()
    if not selected.is_file():
        return ()
    records: list[CapabilityEvidence] = []
    try:
        text = selected.read_text(encoding="utf-8")
    except OSError:
        return ()
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        try:
            records.append(
                CapabilityEvidence(
                    subject_worker=str(raw["subject_worker"]),
                    subject_model=str(raw["subject_model"]),
                    capability=str(raw["capability"]),
                    outcome=str(raw["outcome"]),
                    tier=int(raw.get("tier", 0)),
                    freshness=str(raw.get("freshness", "CURRENT")),
                    recorded_at=str(raw.get("recorded_at") or utc_now()),
                    validator_identity=str(raw.get("validator_identity", "mncs-harness/model-verify")),
                    failure_class=raw.get("failure_class"),
                    execution_receipt=raw.get("execution_receipt"),
                    fixture_identity=raw.get("fixture_identity"),
                    detail=raw.get("detail"),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(records)


def append_evidence(record: CapabilityEvidence, path: Path | None = None) -> None:
    selected = path or evidence_path()
    selected.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    encoded = json.dumps(record.public(), ensure_ascii=False, sort_keys=True)
    if len(encoded.encode("utf-8")) > _MAX_RECORD_BYTES:
        raise ValueError("capability evidence record exceeds the bounded size")
    with selected.open("a", encoding="utf-8") as handle:
        handle.write(encoded + "\n")


def latest_outcome(
    records: Iterable[CapabilityEvidence],
    *,
    model: str,
    capability: str,
    worker: str | None = None,
) -> CapabilityEvidence | None:
    matching = [
        item
        for item in records
        if item.subject_model == model
        and item.capability == capability
        and (worker is None or item.subject_worker == worker)
    ]
    if not matching:
        return None
    return matching[-1]
