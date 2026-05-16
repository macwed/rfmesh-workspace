"""rfmesh-fusion -- bearing cross-fix, GDOP, and confidence ellipses.

Workstream C+D. Implements ``rfmesh_contracts.Fuser``. Pure: no network,
no file I/O, no subprocess, no SDR access (Invariant 5). Must run fully
in pytest on a CI runner with no hardware.
"""

from __future__ import annotations

from .exceptions import DegenerateGeometryError, FusionError
from .geometry import (
    bearing_to_unit_vector,
    ray_ray_crossing,
    weighted_centroid_of_crossings,
)
from .projection import (
    MAX_DISTANCE_M,
    R_EARTH_M,
    choose_enu_origin,
    from_enu,
    to_enu,
)
from .stansfield import stansfield_seed

__all__ = [
    "MAX_DISTANCE_M",
    "R_EARTH_M",
    "DegenerateGeometryError",
    "FusionError",
    "bearing_to_unit_vector",
    "choose_enu_origin",
    "from_enu",
    "ray_ray_crossing",
    "stansfield_seed",
    "to_enu",
    "weighted_centroid_of_crossings",
]
