"""Canonical import alias for MNCS Harness.

The implementation package remains ``epi13_local_harness`` in this release so
existing deployments, editable installs, and tests keep working. New code
should import ``mncs_harness``.
"""

from epi13_local_harness import PROJECT_ID, PROJECT_NAME, __version__

__all__ = ["PROJECT_ID", "PROJECT_NAME", "__version__"]
