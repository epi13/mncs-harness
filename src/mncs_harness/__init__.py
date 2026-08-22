"""Canonical import alias for MNCS Harness.

The implementation package remains ``epi13_local_harness`` in this release so
existing deployments, editable installs, and tests keep working. New code
should import ``mncs_harness``.
"""

from epi13_local_harness import (
    ACTOR_PROVENANCE_SCHEMA,
    PROJECT_ID,
    PROJECT_NAME,
    __version__,
    build_actor_provenance,
)

__all__ = [
    "ACTOR_PROVENANCE_SCHEMA",
    "PROJECT_ID",
    "PROJECT_NAME",
    "__version__",
    "build_actor_provenance",
]
