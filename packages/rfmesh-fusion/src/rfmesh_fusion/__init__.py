"""rfmesh-fusion -- bearing cross-fix, GDOP, and confidence ellipses.

Workstream C+D. Implements ``rfmesh_contracts.Fuser``. Pure: no network,
no file I/O, no subprocess, no SDR access (Invariant 5). Must run fully
in pytest on a CI runner with no hardware.
"""

from __future__ import annotations

from .projection import (
    MAX_DISTANCE_M,
    R_EARTH_M,
    choose_enu_origin,
    from_enu,
    to_enu,
)

__all__ = [
    "MAX_DISTANCE_M",
    "R_EARTH_M",
    "choose_enu_origin",
    "from_enu",
    "to_enu",
]
