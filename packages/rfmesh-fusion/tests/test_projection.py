"""Tests for the WGS-84 <-> ENU projection helpers (WS-CD-001).

These tests pin the seven acceptance criteria of WS-CD-001:

1. Origin -> ``(0.0, 0.0)`` exactly.
2. Roundtrip ``from_enu(to_enu(p, o), o) ~= p`` within 1 m / 1e-6 deg on a
   grid up to 10 km from the origin.
3. A purely-northward 1000 m offset in geodetic space projects to
   ``(0.0, 1000.0)`` within 0.5 m.
4. A purely-eastward 1000 m offset at latitude 60 deg projects to
   ``(1000.0, 0.0)`` within 0.5 m -- pins the ``cos(lat0)`` factor.
5. ``choose_enu_origin`` of three positions returns their arithmetic
   centroid with ``hae_m = 0`` and ``sigma_m = 0``.
6. ``choose_enu_origin`` of one position returns that position with
   ``hae_m`` and ``sigma_m`` reset to 0.
7. ``from_enu`` passes ``sigma_m`` straight through to the result --
   the projection does not invent an uncertainty.
"""

from __future__ import annotations

import math

import pytest
from rfmesh_contracts.geospatial import (  # type: ignore[import-untyped, unused-ignore]
    GeodeticPosition,
)
from rfmesh_fusion.projection import (
    MAX_DISTANCE_M,
    R_EARTH_M,
    choose_enu_origin,
    from_enu,
    to_enu,
)

from .conftest import MakePosition

# Tolerances and fixed displacements used by the acceptance-criteria tests.
# Named to satisfy ruff's PLR2004 (no magic numbers in comparisons) and to
# keep the per-test intent close to the per-test docstring.
_DISPLACEMENT_M = 1000.0  # acceptance-criteria 3 & 4
_TOLERANCE_M_FIXED_DISPLACEMENT = 0.5  # acceptance-criteria 3 & 4
_TOLERANCE_M_ROUNDTRIP = 1.0  # acceptance-criterion 2
_TOLERANCE_DEG_ROUNDTRIP = 1e-6  # acceptance-criterion 2
_SIGMA_PASSTHROUGH_M = 5.0  # acceptance-criterion 7
_HIGH_LATITUDE_DEG = 60.0  # acceptance-criterion 4: cos(lat) factor
_HIGH_LON_DEG = 10.0


def test_origin_roundtrips_to_zero(origin: GeodeticPosition) -> None:
    """``to_enu(origin, origin)`` is exactly ``(0.0, 0.0)``."""
    east_m, north_m = to_enu(origin, origin)
    assert east_m == 0.0
    assert north_m == 0.0


@pytest.mark.parametrize("dlat_deg", [-0.09, -0.045, 0.0, 0.045, 0.09])
@pytest.mark.parametrize("dlon_deg", [-0.09, -0.045, 0.0, 0.045, 0.09])
def test_roundtrip_within_1m_at_10km(
    origin: GeodeticPosition,
    make_position: MakePosition,
    dlat_deg: float,
    dlon_deg: float,
) -> None:
    """A point within 10 km of the origin roundtrips through ENU to within 1 m.

    The grid spans +/-0.09 deg in lat/lon. At the 52 deg N origin this is
    roughly +/-10 km north-south and +/-6.15 km east-west (the east axis
    is squeezed by the ``cos(lat0)`` factor); the worst-case corner sits
    ~11.7 km from the origin -- comfortably inside the 50 km flat-Earth
    valid domain. The lat/lon tolerance of 1e-6 deg (~0.11 m at lat 52)
    doubles as the 1 m bound on the geodetic-distance reconstruction.
    """
    point = make_position(origin.lat_deg + dlat_deg, origin.lon_deg + dlon_deg)

    east_m, north_m = to_enu(point, origin)
    roundtripped = from_enu(east_m, north_m, origin)

    assert roundtripped.lat_deg == pytest.approx(point.lat_deg, abs=_TOLERANCE_DEG_ROUNDTRIP)
    assert roundtripped.lon_deg == pytest.approx(point.lon_deg, abs=_TOLERANCE_DEG_ROUNDTRIP)

    # Equivalent metric check: the roundtripped point should re-project
    # to (east_m, north_m) almost exactly -- the inverse is closed-form,
    # so any drift is pure floating-point noise.
    east_back, north_back = to_enu(roundtripped, origin)
    residual_m = math.hypot(east_back - east_m, north_back - north_m)
    assert residual_m < _TOLERANCE_M_ROUNDTRIP


def test_north_displacement_matches_great_circle(
    origin: GeodeticPosition,
    make_position: MakePosition,
) -> None:
    """1000 m due north in geodetic space lands at ENU ``(0, 1000)`` within 0.5 m.

    The latitude offset is computed from the same R_EARTH_M the module
    uses, so the closed-form inverse should reconstruct exactly 1000 m;
    the 0.5 m budget is for floating-point drift only.
    """
    dlat_deg = _DISPLACEMENT_M / R_EARTH_M * 180.0 / math.pi
    point = make_position(origin.lat_deg + dlat_deg, origin.lon_deg)

    east_m, north_m = to_enu(point, origin)

    assert abs(east_m) < _TOLERANCE_M_FIXED_DISPLACEMENT
    assert north_m == pytest.approx(_DISPLACEMENT_M, abs=_TOLERANCE_M_FIXED_DISPLACEMENT)


def test_east_displacement_scales_with_cos_lat(
    make_position: MakePosition,
) -> None:
    """1000 m due east at lat 60 deg lands at ENU ``(1000, 0)`` within 0.5 m.

    Validates the ``cos(lat0)`` factor explicitly: without it the
    projected east_m would be ~2000 m (lat 60 -> cos = 0.5). The
    longitude offset is computed from R_EARTH_M and cos(lat0), so the
    closed-form should reconstruct 1000 m to floating-point precision.
    """
    origin_60 = make_position(_HIGH_LATITUDE_DEG, _HIGH_LON_DEG)
    cos_lat0 = math.cos(math.radians(_HIGH_LATITUDE_DEG))
    dlon_deg = _DISPLACEMENT_M / (cos_lat0 * R_EARTH_M) * 180.0 / math.pi
    point = make_position(origin_60.lat_deg, origin_60.lon_deg + dlon_deg)

    east_m, north_m = to_enu(point, origin_60)

    assert east_m == pytest.approx(_DISPLACEMENT_M, abs=_TOLERANCE_M_FIXED_DISPLACEMENT)
    assert abs(north_m) < _TOLERANCE_M_FIXED_DISPLACEMENT


def test_choose_enu_origin_returns_centroid(make_position: MakePosition) -> None:
    """Three positions in a triangle -> centroid; hae_m / sigma_m reset to 0."""
    positions = [
        make_position(52.0, 21.0, hae_m=120.0, sigma_m=4.0),
        make_position(52.1, 21.2, hae_m=130.0, sigma_m=5.0),
        make_position(52.2, 21.1, hae_m=140.0, sigma_m=6.0),
    ]

    centroid = choose_enu_origin(positions)

    assert centroid.lat_deg == pytest.approx(52.1)
    assert centroid.lon_deg == pytest.approx(21.1)
    assert centroid.hae_m == 0.0
    assert centroid.sigma_m == 0.0


def test_choose_enu_origin_single_node(make_position: MakePosition) -> None:
    """One position in -> same lat/lon out, hae_m and sigma_m reset to 0.

    The origin is a frame choice, not a measured point: keeping the
    input's HAE or uncertainty on the centroid would let downstream
    code mistake a frame anchor for a measurement.
    """
    only = make_position(52.5, 21.5, hae_m=100.0, sigma_m=3.0)

    centroid = choose_enu_origin([only])

    assert centroid.lat_deg == only.lat_deg
    assert centroid.lon_deg == only.lon_deg
    assert centroid.hae_m == 0.0
    assert centroid.sigma_m == 0.0


def test_from_enu_sigma_passthrough(origin: GeodeticPosition) -> None:
    """``sigma_m`` flows through from caller to result unchanged.

    The projection helpers do not synthesise uncertainty: the production
    call site (``fuser.py``) derives ``sigma_m`` from the ellipse and
    passes it in explicitly, and tests need that contract to be honest.
    """
    result = from_enu(100.0, 200.0, origin, sigma_m=_SIGMA_PASSTHROUGH_M)

    assert result.sigma_m == _SIGMA_PASSTHROUGH_M


def test_to_enu_refuses_far_point(
    origin: GeodeticPosition,
    make_position: MakePosition,
) -> None:
    """A point well beyond the 50 km valid domain raises ``ValueError``.

    Not one of the seven WS-CD-001 acceptance items, but pins the
    no-silent-fallback contract (Invariant 4): the module promises to
    refuse rather than emit a meaningless projection.
    """
    # ~111 km north of the origin -- safely outside the 50 km bound.
    far_point = make_position(origin.lat_deg + 1.0, origin.lon_deg)
    with pytest.raises(ValueError, match="flat-Earth"):
        to_enu(far_point, origin)


def test_from_enu_refuses_far_offset(origin: GeodeticPosition) -> None:
    """An ENU offset outside the valid domain raises ``ValueError``."""
    with pytest.raises(ValueError, match="flat-Earth"):
        from_enu(0.0, MAX_DISTANCE_M + 1.0, origin)


def test_choose_enu_origin_rejects_empty() -> None:
    """No positions in -> ``ValueError``; the caller must hand us at least one."""
    with pytest.raises(ValueError, match="at least one"):
        choose_enu_origin([])
