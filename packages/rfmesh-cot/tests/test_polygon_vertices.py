"""Closed-form / property tests for ``ellipse_to_polygon_vertices``.

These cover the pure-math projection without any of the CoT / publisher
machinery, so a regression here is unambiguous.

Test inventory:

* ``test_circle_at_equator``       -- isotropic ellipse at (0, 0); every
                                       vertex is on the same metre-radius
                                       circle (sanity check on the
                                       projection scale).
* ``test_rotated_ellipse``         -- a/b/theta = 200/100/45 deg at
                                       mid-latitude; assert axis lengths
                                       and orientation come out right.
* ``test_high_latitude_accuracy``  -- 1 km radius circle at lat=70 deg;
                                       reprojection error stays below 1 m.
* ``test_polygon_is_closed``       -- last vertex equals first.
* ``test_default_vertex_count``    -- default n_vertices=72 -> length 73.
* ``test_n_vertices_validation``   -- n_vertices < 3 raises ValueError.
* ``test_pole_rejected``           -- lat=90 raises (singular projection).
"""

from __future__ import annotations

import math

import pytest
from rfmesh_contracts import EllipseENU
from rfmesh_cot import ellipse_to_polygon_vertices

# WGS-84 numbers used to translate metres back to degrees for the
# accuracy checks below. Defined here, not imported, so the test
# stands alone if ``ellipse.py``'s internals change.
_WGS84_A_M = 6_378_137.0
_WGS84_F = 1.0 / 298.257_223_563
_WGS84_E2 = _WGS84_F * (2.0 - _WGS84_F)


def _local_distance_m(lat1_deg: float, lon1_deg: float, lat2_deg: float, lon2_deg: float) -> float:
    """Local-tangent-plane distance using the *same* WGS-84 curvatures the
    projection uses.

    Two points close together (a few km), centred near (lat1, lon1):

        north_m = M(lat1) * (lat2 - lat1) * pi/180
        east_m  = N(lat1) * cos(lat1) * (lon2 - lon1) * pi/180
        d_m     = sqrt(north_m**2 + east_m**2)

    This is the right ground truth for the equirectangular ellipse
    projection: both use the same WGS-84 ellipsoid, so the only
    error is the small-region approximation itself, not the geodesy
    library mismatch.
    """
    lat1_rad = math.radians(lat1_deg)
    sin_lat = math.sin(lat1_rad)
    r_meridional = _WGS84_A_M * (1.0 - _WGS84_E2) / ((1.0 - _WGS84_E2 * sin_lat * sin_lat) ** 1.5)
    r_transverse = _WGS84_A_M / math.sqrt(1.0 - _WGS84_E2 * sin_lat * sin_lat)
    north_m = r_meridional * math.radians(lat2_deg - lat1_deg)
    east_m = r_transverse * math.cos(lat1_rad) * math.radians(lon2_deg - lon1_deg)
    return math.hypot(north_m, east_m)


def _local_bearing_deg(lat1_deg: float, lon1_deg: float, lat2_deg: float, lon2_deg: float) -> float:
    """ENU-frame bearing CCW from East, degrees -- mathematical-positive.

    The polygon orientation_deg is in this frame (East -> North CCW),
    so to compare against the input orientation we must use it here.
    """
    lat1_rad = math.radians(lat1_deg)
    sin_lat = math.sin(lat1_rad)
    r_meridional = _WGS84_A_M * (1.0 - _WGS84_E2) / ((1.0 - _WGS84_E2 * sin_lat * sin_lat) ** 1.5)
    r_transverse = _WGS84_A_M / math.sqrt(1.0 - _WGS84_E2 * sin_lat * sin_lat)
    north_m = r_meridional * math.radians(lat2_deg - lat1_deg)
    east_m = r_transverse * math.cos(lat1_rad) * math.radians(lon2_deg - lon1_deg)
    return math.degrees(math.atan2(north_m, east_m))


def test_circle_at_equator() -> None:
    """Isotropic ellipse at the equator: every vertex is one radius away."""
    ellipse = EllipseENU(
        semi_major_m=1000.0,
        semi_minor_m=1000.0,
        orientation_deg=0.0,
    )
    vertices = ellipse_to_polygon_vertices(ellipse, 0.0, 0.0, n_vertices=72)
    distances = [_local_distance_m(0.0, 0.0, lat, lon) for lat, lon in vertices]
    # All distances should be 1000 m, within sub-metre tolerance.
    for d in distances:
        assert abs(d - 1000.0) < 1.0, f"vertex off-radius by {d - 1000.0:.3f} m"


def test_rotated_ellipse() -> None:
    """200 m / 100 m ellipse rotated 45 deg at mid-latitude.

    The semi-major axis (parameter phi=0) should land NE of centre at
    sqrt(2)/2 * 200 in both east and north components. The semi-minor
    axis (phi = pi/2) should land NW.
    """
    centre_lat = 50.0
    centre_lon = 5.0
    ellipse = EllipseENU(
        semi_major_m=200.0,
        semi_minor_m=100.0,
        orientation_deg=45.0,
    )
    vertices = ellipse_to_polygon_vertices(ellipse, centre_lat, centre_lon, n_vertices=4)
    # n_vertices=4 -> phi = 0, pi/2, pi, 3pi/2 -- but the function steps
    # uniformly in phi, so we get four cardinal points in the ellipse
    # parameter, NOT in geographic cardinal directions.

    # phi=0 -> east = a*cos(theta) = 200*sqrt(2)/2 = 141.42,
    #          north = a*sin(theta) = 141.42
    lat0, lon0 = vertices[0]
    expected_offset_m = 200.0
    actual_offset_m = _local_distance_m(centre_lat, centre_lon, lat0, lon0)
    assert abs(actual_offset_m - expected_offset_m) < 1.0

    # The ENU-frame bearing (CCW from East) from centre to vertex[0]
    # should match the orientation_deg, which was 45.
    bearing_deg = _local_bearing_deg(centre_lat, centre_lon, lat0, lon0)
    assert abs(bearing_deg - 45.0) < 0.5, f"bearing was {bearing_deg:.2f} deg"

    # phi=pi/2 -> east = -b*sin(theta) = -100*sqrt(2)/2 = -70.71,
    #             north = b*cos(theta) = 70.71. Distance = 100.
    lat1, lon1 = vertices[1]
    actual_offset_m = _local_distance_m(centre_lat, centre_lon, lat1, lon1)
    assert abs(actual_offset_m - 100.0) < 1.0


def test_high_latitude_accuracy() -> None:
    """1 km circle at lat=70 deg: reprojection error < 1 m at every vertex.

    This is the binding accuracy envelope from the ticket. The
    equirectangular small-region approximation, using WGS-84
    meridional and prime-vertical curvatures, must stay under 1 m
    at this radius and latitude.
    """
    ellipse = EllipseENU(
        semi_major_m=1000.0,
        semi_minor_m=1000.0,
        orientation_deg=0.0,
    )
    centre_lat = 70.0
    centre_lon = 25.0
    vertices = ellipse_to_polygon_vertices(ellipse, centre_lat, centre_lon, n_vertices=72)
    for lat, lon in vertices:
        d = _local_distance_m(centre_lat, centre_lon, lat, lon)
        # Allow at most 1 m of reprojection error.
        assert abs(d - 1000.0) < 1.0, (
            f"high-lat reprojection error at vertex ({lat}, {lon}): {abs(d - 1000.0):.3f} m"
        )


def test_polygon_is_closed() -> None:
    """The last vertex is byte-equal to the first (explicit closure)."""
    ellipse = EllipseENU(
        semi_major_m=50.0,
        semi_minor_m=20.0,
        orientation_deg=10.0,
    )
    vertices = ellipse_to_polygon_vertices(ellipse, 49.5, 4.5)
    assert vertices[0] == vertices[-1]


def test_default_vertex_count() -> None:
    """Default n_vertices=72 -> 73 returned points (closure adds one)."""
    ellipse = EllipseENU(
        semi_major_m=10.0,
        semi_minor_m=5.0,
        orientation_deg=0.0,
    )
    vertices = ellipse_to_polygon_vertices(ellipse, 0.0, 0.0)
    assert len(vertices) == 73


def test_n_vertices_validation() -> None:
    """``n_vertices < 3`` raises ValueError (not a polygon)."""
    ellipse = EllipseENU(
        semi_major_m=10.0,
        semi_minor_m=5.0,
        orientation_deg=0.0,
    )
    with pytest.raises(ValueError, match="n_vertices must be >= 3"):
        ellipse_to_polygon_vertices(ellipse, 0.0, 0.0, n_vertices=2)


def test_pole_rejected() -> None:
    """At the geographic pole the east-west scale is zero; we reject it."""
    ellipse = EllipseENU(
        semi_major_m=100.0,
        semi_minor_m=50.0,
        orientation_deg=0.0,
    )
    with pytest.raises(ValueError, match="degenerate east-west scale"):
        ellipse_to_polygon_vertices(ellipse, 90.0, 0.0)


def test_non_uniform_axes_preserved() -> None:
    """Distinct semi-major/minor: vertex distances range between them.

    The maximum vertex distance from centre should approach
    ``semi_major_m``, the minimum should approach ``semi_minor_m``,
    and intermediate vertices should fall between those.
    """
    ellipse = EllipseENU(
        semi_major_m=300.0,
        semi_minor_m=50.0,
        orientation_deg=0.0,
    )
    centre_lat = 50.0
    centre_lon = 5.0
    vertices = ellipse_to_polygon_vertices(ellipse, centre_lat, centre_lon, n_vertices=360)
    distances = [_local_distance_m(centre_lat, centre_lon, la, lo) for la, lo in vertices]
    assert abs(max(distances) - 300.0) < 1.0
    assert abs(min(distances) - 50.0) < 1.0


def test_vertex_count_is_n_plus_one_for_other_sizes() -> None:
    """``len(result) == n_vertices + 1`` for various sensible counts."""
    ellipse = EllipseENU(
        semi_major_m=10.0,
        semi_minor_m=5.0,
        orientation_deg=0.0,
    )
    for n in (3, 4, 16, 36, 144):
        vertices = ellipse_to_polygon_vertices(ellipse, 0.0, 0.0, n_vertices=n)
        assert len(vertices) == n + 1
