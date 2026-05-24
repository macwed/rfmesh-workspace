"""rfmesh-fusion -- bearing cross-fix, GDOP, and confidence ellipses.

Workstream C+D. Implements ``rfmesh_contracts.Fuser`` via
``StansfieldMLEFuser`` (the wiring of all upstream modules). Pure: no
network, no file I/O, no subprocess, no SDR access (Invariant B5).
Must run fully in pytest on a CI runner with no hardware.

PUBLIC SURFACE
--------------
The ``__all__`` tuple below names everything a consumer outside
``rfmesh-fusion`` may rely on:

* ``StansfieldMLEFuser`` -- the ``Fuser`` Protocol implementation.
* Pure helpers from ``projection``, ``geometry``, ``stansfield``,
  ``mle``, ``covariance``, ``gdop``, ``residuals``, ``confidence`` --
  exposed so downstream tickets (CoT publisher, ops dashboard,
  ``apps/demo-replay``) can compose them without reaching into private
  module paths.
* The exception hierarchy (``FusionError`` + its three subclasses).

The orchestration internals of ``fuser.py`` (``_filter_to_time_window``,
``_solve_emitter_position``, ``_fallback_centroid``,
``_consensus_emitter_class``, ``_midpoint_t_unix_ns``,
``_placeholder_ellipse``, ``_ellipse_or_downgrade``) are *not* exported
-- they are private to the fuser and changing them is not a contract
event.
"""

from __future__ import annotations

from .confidence import compute_confidence_level
from .covariance import (
    CHI2_95_DF2,
    compute_covariance,
    covariance_to_ellipse,
)
from .exceptions import (
    DegenerateGeometryError,
    FusionError,
    MLEConvergenceError,
    SingularFisherInformationError,
)
from .fuser import StansfieldMLEFuser
from .gdop import compute_gdop
from .geometry import (
    bearing_to_unit_vector,
    ray_ray_crossing,
    weighted_centroid_of_crossings,
)
from .mle import MLEResult, solve_mle
from .posterior import combine_bearing_prior
from .projection import (
    MAX_DISTANCE_M,
    R_EARTH_M,
    choose_enu_origin,
    from_enu,
    to_enu,
)
from .residuals import ResidualsResult, compute_residuals
from .stansfield import stansfield_seed

__all__ = (
    "CHI2_95_DF2",
    "MAX_DISTANCE_M",
    "R_EARTH_M",
    "DegenerateGeometryError",
    "FusionError",
    "MLEConvergenceError",
    "MLEResult",
    "ResidualsResult",
    "SingularFisherInformationError",
    "StansfieldMLEFuser",
    "bearing_to_unit_vector",
    "choose_enu_origin",
    "combine_bearing_prior",
    "compute_confidence_level",
    "compute_covariance",
    "compute_gdop",
    "compute_residuals",
    "covariance_to_ellipse",
    "from_enu",
    "ray_ray_crossing",
    "solve_mle",
    "stansfield_seed",
    "to_enu",
    "weighted_centroid_of_crossings",
)
