"""MNCS Harness canonical implementation package.

The public project identity is MNCS Harness / ``mncs-harness`` and this
module path (``mncs_harness``) is its single canonical implementation.
The former ``epi13_local_harness`` package was retired during the
MNCS-language conversion campaign; see ``docs/IDENTITY.md``.
"""

from .actor_provenance import ACTOR_PROVENANCE_SCHEMA, build_actor_provenance

__version__ = "0.6.9"
PROJECT_NAME = "MNCS Harness"
PROJECT_ID = "mncs-harness"
LEGACY_PROJECT_ID = "epi13-local-harness"

__all__ = [
    "ACTOR_PROVENANCE_SCHEMA",
    "LEGACY_PROJECT_ID",
    "PROJECT_ID",
    "PROJECT_NAME",
    "build_actor_provenance",
    "__version__",
]
