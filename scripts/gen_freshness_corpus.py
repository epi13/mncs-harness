#!/usr/bin/env python3
"""Generate the bounded corpus for the freshness-window kernel.

Pure integer windows, so every expectation is byte-exact on both
backends: boundary ages (== budget) are fresh, budget+1 is stale,
future-dated stores read fresh for residency (skew floor) but never
supply for attestations (strict window).
Run from the repository root:

    python3 scripts/gen_freshness_corpus.py
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "corpora")

MODULE = "mncs.harness.freshness.v1"


def integer(value):
    return {"integer": {"value": value, "type": {"bits": 64, "signed": True}}}


def boolean(value):
    return {"boolean": {"value": value}}


def case(case_id, function, arguments, expected):
    return {
        "id": case_id,
        "request": {
            "schema_version": "0.1",
            "target": {"module": MODULE, "function": function},
            "arguments": [integer(arg) for arg in arguments],
            "step_budget": 4096,
        },
        "expected": [boolean(expected)],
    }


cases = [
    case("fresh-young", "observation_fresh", [1000, 1500, 1000], True),
    case("fresh-boundary", "observation_fresh", [1000, 2000, 1000], True),
    case("stale", "observation_fresh", [1000, 2001, 1000], False),
    case("future-skew", "observation_fresh", [3000, 2000, 100], True),
    case("zero-budget-hit", "observation_fresh", [500, 500, 0], True),
    case("window-ok", "attestation_window_ok", [100, 150, 100], True),
    case("window-boundary", "attestation_window_ok", [0, 100, 100], True),
    case("window-stale", "attestation_window_ok", [0, 101, 100], False),
    case("window-future", "attestation_window_ok", [200, 150, 100], False),
]

with open(os.path.join(OUT, "harness-freshness-corpus.json"), "w") as handle:
    json.dump(
        {"schema_version": "0.1", "name": "harness-freshness", "cases": cases},
        handle,
        indent=1,
    )
    handle.write("\n")

print(f"wrote {len(cases)} cases")
