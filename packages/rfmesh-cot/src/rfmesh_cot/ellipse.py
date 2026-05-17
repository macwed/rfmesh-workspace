"""Pure-math projection of an ENU confidence ellipse to a closed (lat, lon) polygon.

The only non-trivial math in the package. Lives in its own module so it can
be exercised against closed-form cases (a circle at the equator, a rotated
ellipse, an ellipse at high latitude) without standing up any of the network
machinery in ``publisher.py``.

PROJECTION MATH
---------------
The fusion workstream gives us a 95 % confidence ellipse in the local ENU
plane (East-North-Up, metres, right-handed; see ``INTERFACES.md`` §2). Its
centre is the fix position, expressed in WGS-84 lat/lon. The ellipse has a
semi-major axis ``a``, a semi-minor axis ``b``, and an orientation
``theta`` measured from local East toward North (ENU-plane mathematical
positive), i.e. CCW from the +E axis.

For each polygon vertex parameter ``phi`` in ``[0, 2*pi)`` we compute:

    east_m  = a * cos(phi) * cos(theta) - b * sin(phi) * sin(theta)
    north_m = a * cos(phi) * sin(theta) + b * sin(phi) * cos(theta)

That is the standard rotated-ellipse parametric form: ``a cos phi`` /
``b sin phi`` in the ellipse-aligned frame, rotated by ``theta`` into the
ENU frame.

We then project ENU offsets back to geodetic with the small-region
equirectangular approximation centred on the fix position
``(center_lat_deg, center_lon_deg)``:

    d_lat_deg = (north_m / R_meridional) * (180 / pi)
    d_lon_deg = (east_m  / R_transverse) * (180 / pi)

where ``R_meridional`` is the WGS-84 meridional radius of curvature at
``center_lat_deg`` and ``R_transverse = N(lat) * cos(lat)`` is the local
east-west scale factor (``N`` being the prime-vertical radius of
curvature). Using the latitude-dependent curvatures, rather than a flat
sphere, gives sub-metre accuracy out to a few tens of km from the centre
and stays honest at high latitudes (where flat-Earth east-west would
underestimate the east-west scale).

ACCURACY ENVELOPE
-----------------
With proper meridional/transverse curvatures the approximation is correct
to well under 1 m of **tangential** error at <= 5 km from the centre,
even at 70 deg latitude. Tangential = "along the polygon edge"; radial
distance from the fix position remains effectively exact (limited only
by float64 rounding) because each polygon vertex is built from its own
(east_m, north_m) offset relative to the fix. Above ~50 km the
tangential error grows quadratically with chord length to a worst-case
~190 m at 50 km, which is well outside the fixes the rfmesh BoTH3
scenarios produce (~1-5 km ranges); the simpler form is preferred
over a full geodesic projection (which would pull pyproj as a
dependency). Tangential error: rf-dsp-council NOTE 3.

THE POLYGON IS CLOSED
---------------------
The last vertex equals the first by construction (``phi=0`` and
``phi=2*pi`` map to the same point). ATAK renders <link> as a polyline,
so the explicit closure is what makes it a polygon. Tests assert this.
"""

from __future__ import annotations

import math

from rfmesh_contracts import EllipseENU

# WGS-84 ellipsoid parameters (frozen physical constants -- defining them
# locally avoids pulling a geodesy dep just for two numbers).
_WGS84_A_M: float = 6_378_137.0  # equatorial radius (semi-major)
_WGS84_F: float = 1.0 / 298.257_223_563  # flattening
_WGS84_E2: float = _WGS84_F * (2.0 - _WGS84_F)  # eccentricity squared

#: Minimum vertex count for a valid polygon (3 = triangle).
_MIN_POLYGON_VERTICES: int = 3


def _meridional_radius_m(lat_deg: float) -> float:
    """WGS-84 meridional radius of curvature M(lat), metres.

    The radius of curvature in the *north-south* direction at the given
    latitude. M(lat) = a (1 - e^2) / (1 - e^2 sin^2 lat)^(3/2).
    """
    lat_rad = math.radians(lat_deg)
    sin_lat = math.sin(lat_rad)
    denom: float = (1.0 - _WGS84_E2 * sin_lat * sin_lat) ** 1.5
    return float(_WGS84_A_M * (1.0 - _WGS84_E2) / denom)


def _prime_vertical_radius_m(lat_deg: float) -> float:
    """WGS-84 prime-vertical radius of curvature N(lat), metres.

    The radius of curvature in the *east-west* direction (perpendicular
    to the meridian) at the given latitude. N(lat) = a / sqrt(1 - e^2
    sin^2 lat). The local east-west *scale* is N(lat) * cos(lat).
    """
    lat_rad = math.radians(lat_deg)
    sin_lat = math.sin(lat_rad)
    return _WGS84_A_M / math.sqrt(1.0 - _WGS84_E2 * sin_lat * sin_lat)


def ellipse_to_polygon_vertices(
    ellipse: EllipseENU,
    center_lat_deg: float,
    center_lon_deg: float,
    n_vertices: int = 72,
) -> tuple[tuple[float, float], ...]:
    """Project a 2-D ENU ellipse into a closed sequence of (lat, lon) polygon vertices.

    Parameters
    ----------
    ellipse:
        The 95 % confidence ellipse in the local East-North plane. Centre
        is implicit (the fix position). ``orientation_deg`` is measured
        from local East toward North, ENU-plane mathematical positive,
        range [-180, +180] deg.
    center_lat_deg, center_lon_deg:
        WGS-84 decimal degrees of the fix position. The ellipse polygon
        is centred *here*, not at the local ENU origin -- ATAK renders a
        polygon at absolute geodetic coordinates.
    n_vertices:
        Number of vertices the polygon will have. The default 72 means
        one vertex per 5 deg of ellipse-parameter angle: smooth enough
        that ATAK does not show facet edges. The returned tuple's
        length is ``n_vertices + 1`` because the last vertex equals
        the first (explicit closure for renderers that draw polylines).

    Returns
    -------
    A tuple of ``(lat_deg, lon_deg)`` pairs, length ``n_vertices + 1``,
    with the last pair equal to the first.

    Raises
    ------
    ValueError:
        If ``n_vertices`` is less than 3 (not a polygon).
    """
    if n_vertices < _MIN_POLYGON_VERTICES:
        msg = f"n_vertices must be >= {_MIN_POLYGON_VERTICES} to form a polygon; got {n_vertices}"
        raise ValueError(msg)

    a_m = ellipse.semi_major_m
    b_m = ellipse.semi_minor_m
    theta_rad = math.radians(ellipse.orientation_deg)
    cos_t = math.cos(theta_rad)
    sin_t = math.sin(theta_rad)

    # Latitude-dependent metres-per-degree scales.
    r_meridional_m = _meridional_radius_m(center_lat_deg)
    r_transverse_m = _prime_vertical_radius_m(center_lat_deg) * math.cos(
        math.radians(center_lat_deg)
    )
    # Guard for the pathological pole-on-fix case (would explode the
    # east-west degrees-per-metre to absurd values). The rfmesh demo
    # is mid-latitude (~50 N) so this is belt-and-braces, not a real
    # path; we surface it as a ValueError rather than silently
    # producing meaningless longitudes. The 1.0 m threshold catches
    # everything within ~1e-7 deg of the pole.
    if r_transverse_m < 1.0:
        msg = (
            "ellipse_to_polygon_vertices: degenerate east-west scale "
            f"at center_lat_deg={center_lat_deg} (singular at the poles)."
        )
        raise ValueError(msg)

    deg_per_m_lat = math.degrees(1.0 / r_meridional_m)
    deg_per_m_lon = math.degrees(1.0 / r_transverse_m)

    vertices: list[tuple[float, float]] = []
    for i in range(n_vertices):
        phi = 2.0 * math.pi * i / n_vertices
        cos_p = math.cos(phi)
        sin_p = math.sin(phi)
        # Rotated-ellipse parametric form in ENU.
        east_m = a_m * cos_p * cos_t - b_m * sin_p * sin_t
        north_m = a_m * cos_p * sin_t + b_m * sin_p * cos_t
        # Equirectangular reprojection to lat/lon.
        d_lat_deg = north_m * deg_per_m_lat
        d_lon_deg = east_m * deg_per_m_lon
        vertices.append((center_lat_deg + d_lat_deg, center_lon_deg + d_lon_deg))
    # Explicit closure: the polyline must return to the first vertex.
    vertices.append(vertices[0])
    return tuple(vertices)
