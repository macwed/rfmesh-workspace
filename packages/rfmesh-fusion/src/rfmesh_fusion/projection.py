"""WGS-84 <-> local ENU flat-Earth projection helpers.

Every downstream fusion module (Stansfield, MLE, GDOP, residuals, the
``Fuser`` final-step Geodetic round-trip) does its geometry in the local
East-North tangent plane and only crosses back to WGS-84 once at the end.
This module is that single, well-tested boundary.

WHY FLAT-EARTH
--------------
The fusion pipeline does not need a full geodetic solve. A bearings-only
cross-fix is dominated by the per-bearing sigma (degrees of arrival error)
times the range to the emitter; at the rfmesh operational scale
(single-region deployment, all nodes and candidate emitters inside a
~50 km box centred on the chosen ENU origin), the curvature error from
treating the local tangent plane as flat is well under 1 m -- two orders
of magnitude below the per-bearing geometry error. Trading that 1 m of
correctness for a scipy/pyproj dependency and the corresponding mypy /
pyproject churn would be a bad bargain. See ADR-004 D1 and
``packages/rfmesh-fusion/docs/MODULE_PLAN.md`` Section 3.

THE APPROXIMATION
-----------------
With ``lat0`` the origin latitude in radians, the conversion is:

    dy_north = (lat_deg - lat0_deg)                * R_EARTH_M * pi/180
    dx_east  = (lon_deg - lon0_deg) * cos(lat0)    * R_EARTH_M * pi/180

i.e. the equirectangular projection with the east-axis scale frozen at
the origin's parallel. The inverse simply divides through.

VALID DOMAIN
------------
Valid for points within ~50 km of the origin. Beyond that the
flat-Earth error accumulates faster than the fusion budget can absorb,
so the helpers refuse to project rather than silently degrade
(Invariant 4 -- no silent fallbacks). A point further than
``MAX_DISTANCE_M`` from the origin raises ``ValueError`` in both
``to_enu`` and ``from_enu``.

LIMITATIONS OF ``choose_enu_origin``
------------------------------------
The longitude mean is arithmetic, not circular. A set of positions
straddling the +/-180 deg antimeridian (e.g. -179 deg and +179 deg)
yields a centroid near 0 deg, which is wrong. For rfmesh deployments --
confined to a single country-scale region -- this is not a real risk;
the limitation is documented rather than fixed so the helper stays
dependency-free and its behaviour stays obvious.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Final

from rfmesh_contracts.geospatial import (  # type: ignore[import-untyped, unused-ignore]
    GeodeticPosition,
)

# WGS-84 equatorial radius (semi-major axis), metres.
# Reference: WGS-84 -- NIMA TR8350.2 4th edition (2000).
R_EARTH_M: Final[float] = 6_378_137.0

# Flat-Earth valid domain: beyond this distance the equirectangular
# approximation's error grows past what the fusion budget can absorb,
# so we refuse rather than degrade silently.
MAX_DISTANCE_M: Final[float] = 50_000.0

_DEG_TO_RAD: Final[float] = math.pi / 180.0
_RAD_TO_DEG: Final[float] = 180.0 / math.pi


def to_enu(point: GeodeticPosition, origin: GeodeticPosition) -> tuple[float, float]:
    """Project ``point`` from WGS-84 onto the local ENU plane at ``origin``.

    Returns ``(east_m, north_m)`` scalars. The Up axis is intentionally
    not returned: fusion geometry is 2-D on the horizontal plane and the
    HAE difference does not enter the bearing-cross-fix maths. Callers
    that need altitude work with ``point.hae_m`` directly.

    Raises ``ValueError`` if ``point`` is more than ``MAX_DISTANCE_M``
    (50 km) from ``origin``.
    """
    lat0_rad = origin.lat_deg * _DEG_TO_RAD
    deg_to_m_north = R_EARTH_M * _DEG_TO_RAD
    deg_to_m_east = deg_to_m_north * math.cos(lat0_rad)

    north_m = (point.lat_deg - origin.lat_deg) * deg_to_m_north
    east_m = (point.lon_deg - origin.lon_deg) * deg_to_m_east

    distance_m = math.hypot(east_m, north_m)
    if distance_m > MAX_DISTANCE_M:
        msg = (
            f"to_enu: point ({point.lat_deg:.6f}, {point.lon_deg:.6f}) is "
            f"{distance_m:.1f} m from origin "
            f"({origin.lat_deg:.6f}, {origin.lon_deg:.6f}); the flat-Earth "
            f"projection is only valid within {MAX_DISTANCE_M:.0f} m."
        )
        raise ValueError(msg)

    return east_m, north_m


def from_enu(
    east_m: float,
    north_m: float,
    origin: GeodeticPosition,
    sigma_m: float = 0.0,
) -> GeodeticPosition:
    """Invert ``to_enu``: take ENU ``(east_m, north_m)`` to a ``GeodeticPosition``.

    ``sigma_m`` is the isotropic 1-sigma uncertainty the caller wants
    attached to the result. The projection itself does not invent an
    uncertainty -- the production call site (``fuser.py`` final step)
    passes the scalar derived from ``EllipseENU.semi_major_m``; bench
    and test callers can leave it at the default 0.0.

    The returned ``GeodeticPosition`` has ``hae_m = 0.0``: this helper
    only handles the horizontal plane, so synthesising an altitude here
    would be a lie. Callers that need height re-attach it themselves.

    Raises ``ValueError`` if the ENU offset's magnitude exceeds
    ``MAX_DISTANCE_M`` (50 km).
    """
    distance_m = math.hypot(east_m, north_m)
    if distance_m > MAX_DISTANCE_M:
        msg = (
            f"from_enu: ENU offset ({east_m:.1f}, {north_m:.1f}) m has "
            f"magnitude {distance_m:.1f} m, beyond the flat-Earth valid "
            f"domain of {MAX_DISTANCE_M:.0f} m."
        )
        raise ValueError(msg)

    lat0_rad = origin.lat_deg * _DEG_TO_RAD
    m_to_deg_north = _RAD_TO_DEG / R_EARTH_M
    m_to_deg_east = m_to_deg_north / math.cos(lat0_rad)

    lat_deg = origin.lat_deg + north_m * m_to_deg_north
    lon_deg = origin.lon_deg + east_m * m_to_deg_east

    return GeodeticPosition(
        lat_deg=lat_deg,
        lon_deg=lon_deg,
        hae_m=0.0,
        sigma_m=sigma_m,
    )


def choose_enu_origin(positions: Iterable[GeodeticPosition]) -> GeodeticPosition:
    """Return the naive mean-lat / mean-lon position of ``positions``.

    The result is the canonical ENU origin for a batch of fusion inputs:
    each operation picks one origin (typically the centroid of the nodes
    that contributed to the fix) and does all least-squares geometry
    there. The returned ``GeodeticPosition`` has ``hae_m = 0.0`` and
    ``sigma_m = 0.0`` because the origin is a *frame choice*, not a
    measured point -- attaching either an altitude or an uncertainty
    would be misleading.

    Raises ``ValueError`` if ``positions`` is empty.

    Antimeridian limitation: see the module docstring. For rfmesh
    deployments this is not a real concern.
    """
    materialised = list(positions)
    if not materialised:
        msg = "choose_enu_origin requires at least one GeodeticPosition."
        raise ValueError(msg)

    n = len(materialised)
    mean_lat_deg = sum(p.lat_deg for p in materialised) / n
    mean_lon_deg = sum(p.lon_deg for p in materialised) / n

    return GeodeticPosition(
        lat_deg=mean_lat_deg,
        lon_deg=mean_lon_deg,
        hae_m=0.0,
        sigma_m=0.0,
    )
