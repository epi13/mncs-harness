"""MNCS Harness implementation package.

The public project identity is MNCS Harness / ``mncs-harness``. This module
path remains ``epi13_local_harness`` as a compatibility surface for existing
imports, services, and tests. Prefer ``import mncs_harness`` in new code.
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
