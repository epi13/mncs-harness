"""Pin the harness agent contract to real repository paths."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONTRACT = REPO / "AGENTS.md"


def contract_text() -> str:
    assert CONTRACT.is_file(), "AGENTS.md (agent execution contract) is missing"
    return CONTRACT.read_text(encoding="utf-8")


def test_contract_names_existing_paths():
    text = contract_text()
    for ref in ("docs/IDENTITY.md", "src/epi13_local_harness/"):
        assert ref in text, f"contract must mention {ref}"
        assert (REPO / ref).exists(), f"contract names missing {ref}"


def test_contract_states_authority_boundary():
    text = contract_text()
    for party in (
        "MNCS Control MCP",
        "MNCS Harness",
        "MNCS Fabric",
        "MNCS Commons",
        "MNCS Forge",
    ):
        assert party in text, f"contract lost authority party {party!r}"


def test_contract_routes_pressure_and_forbids_fallback():
    text = contract_text()
    assert "mncs-language" in text
    assert "development-pressure" in text
    assert "silently fell back locally" in text
