"""Tests for the bearing-geometry primitives (WS-CD-002).

Pins the project-wide azimuth convention (0 deg = north, CW positive) at
the cardinal directions, exercises ray-ray intersection on
perpendicular / parallel / general configurations, and exercises the
weighted-centroid helper that backs the ``fallback_centroid`` path.
"""

from __future__ import annotations

import math

import pytest
from rfmesh_fusion.geometry import (
    bearing_to_unit_vector,
    ray_ray_crossing,
    weighted_centroid_of_crossings,
)

# Tolerances used by multiple tests, named so ruff PLR2004 does not have
# to flag the same magic constant repeatedly.
_TOL_UNIT_VECTOR = 1e-12
_TOL_PERPENDICULAR_CROSSING_M = 1e-9
_TOL_GENERAL_CROSSING_M = 1e-6
_TOL_CENTROID = 1e-12
_DOMINANT_WEIGHT_RATIO = 1000.0
_DOMINANT_TOLERANCE_FRACTION = 0.001  # 0.1 %


def test_bearing_to_unit_vector_cardinals_north() -> None:
    """Azimuth 0 deg points along +north, i.e. ``(east, north) = (0, 1)``."""
    east, north = bearing_to_unit_vector(0.0)
    assert east == pytest.approx(0.0, abs=_TOL_UNIT_VECTOR)
    assert north == pytest.approx(1.0, abs=_TOL_UNIT_VECTOR)


def test_bearing_to_unit_vector_cardinals_east() -> None:
    """Azimuth 90 deg points along +east, i.e. ``(east, north) = (1, 0)``."""
    east, north = bearing_to_unit_vector(90.0)
    assert east == pytest.approx(1.0, abs=_TOL_UNIT_VECTOR)
    assert north == pytest.approx(0.0, abs=_TOL_UNIT_VECTOR)


def test_bearing_to_unit_vector_cardinals_south() -> None:
    """Azimuth 180 deg points along -north, i.e. ``(east, north) = (0, -1)``."""
    east, north = bearing_to_unit_vector(180.0)
    assert east == pytest.approx(0.0, abs=_TOL_UNIT_VECTOR)
    assert north == pytest.approx(-1.0, abs=_TOL_UNIT_VECTOR)


def test_bearing_to_unit_vector_cardinals_west() -> None:
    """Azimuth 270 deg points along -east, i.e. ``(east, north) = (-1, 0)``."""
    east, north = bearing_to_unit_vector(270.0)
    assert east == pytest.approx(-1.0, abs=_TOL_UNIT_VECTOR)
    assert north == pytest.approx(0.0, abs=_TOL_UNIT_VECTOR)


@pytest.mark.parametrize(
    ("azimuth_deg", "expected_east", "expected_north"),
    [
        (45.0, math.sqrt(2.0) / 2.0, math.sqrt(2.0) / 2.0),
        (135.0, math.sqrt(2.0) / 2.0, -math.sqrt(2.0) / 2.0),
        (225.0, -math.sqrt(2.0) / 2.0, -math.sqrt(2.0) / 2.0),
        (315.0, -math.sqrt(2.0) / 2.0, math.sqrt(2.0) / 2.0),
    ],
)
def test_bearing_to_unit_vector_intermediate(
    azimuth_deg: float,
    expected_east: float,
    expected_north: float,
) -> None:
    """Diagonal azimuths land on the expected ``(+/-sqrt(2)/2, +/-sqrt(2)/2)``.

    Catches a sin/cos swap (which would put east at cos and north at
    sin) and sign errors (which would flip a quadrant).
    """
    east, north = bearing_to_unit_vector(azimuth_deg)
    assert east == pytest.approx(expected_east, abs=_TOL_UNIT_VECTOR)
    assert north == pytest.approx(expected_north, abs=_TOL_UNIT_VECTOR)


@pytest.mark.parametrize("azimuth_deg", [360.0, 720.0, -360.0])
def test_bearing_to_unit_vector_wraps_360(azimuth_deg: float) -> None:
    """Azimuths outside ``[0, 360)`` wrap by trig periodicity.

    ``math.radians`` composed with ``math.sin`` / ``math.cos`` is exactly
    periodic, so the function does not need to normalise its input. The
    contract validator on ``BearingReport.azimuth_deg`` rejects
    out-of-range values upstream; this test exists so the function
    stays robust if a unit test ever bypasses that.
    """
    east, north = bearing_to_unit_vector(azimuth_deg)
    expected_east, expected_north = bearing_to_unit_vector(0.0)
    assert east == pytest.approx(expected_east, abs=_TOL_UNIT_VECTOR)
    assert north == pytest.approx(expected_north, abs=_TOL_UNIT_VECTOR)


def test_ray_ray_crossing_perpendicular() -> None:
    """Two perpendicular rays meeting at the origin recover ``(0, 0)``.

    Ray A: starts at ``(-1, 0)``, heading east -> unit ``(1, 0)``.
    Ray B: starts at ``(0, -1)``, heading north -> unit ``(0, 1)``.
    """
    crossing = ray_ray_crossing((-1.0, 0.0), (1.0, 0.0), (0.0, -1.0), (0.0, 1.0))
    assert crossing is not None
    east, north = crossing
    assert east == pytest.approx(0.0, abs=_TOL_PERPENDICULAR_CROSSING_M)
    assert north == pytest.approx(0.0, abs=_TOL_PERPENDICULAR_CROSSING_M)


def test_ray_ray_crossing_parallel_returns_none() -> None:
    """Two rays sharing a unit direction return ``None``.

    Parallel rays either never meet (different lines) or coincide
    (same line) -- in neither case is there a unique honest crossing.
    """
    crossing = ray_ray_crossing((0.0, 0.0), (1.0, 0.0), (0.0, 10.0), (1.0, 0.0))
    assert crossing is None


def test_ray_ray_crossing_antiparallel_returns_none() -> None:
    """Two rays with opposite unit directions on the same line return ``None``.

    Anti-parallel rays could be interpreted as "meeting somewhere along
    the line" but the function cannot tell forward-meeting from
    backward-meeting and refuses to guess.
    """
    crossing = ray_ray_crossing((0.0, 0.0), (1.0, 0.0), (10.0, 0.0), (-1.0, 0.0))
    assert crossing is None


def test_ray_ray_crossing_general_geometry() -> None:
    """Non-trivial configuration recovers a hand-computed intersection.

    Ray A: starts at ``(0, 0)``, azimuth 60 deg.
    Ray B: starts at ``(1000, 500)``, azimuth 210 deg.

    Solving ``p_A + t * u_A = p_B + s * u_B`` in closed form gives
    ``(east, north) = (1500 - 250*sqrt(3), 500*sqrt(3) - 250)``.
    """
    u_a = bearing_to_unit_vector(60.0)
    u_b = bearing_to_unit_vector(210.0)
    p_a = (0.0, 0.0)
    p_b = (1000.0, 500.0)

    crossing = ray_ray_crossing(p_a, u_a, p_b, u_b)
    assert crossing is not None
    east, north = crossing

    expected_east = 1500.0 - 250.0 * math.sqrt(3.0)
    expected_north = 500.0 * math.sqrt(3.0) - 250.0
    assert east == pytest.approx(expected_east, abs=_TOL_GENERAL_CROSSING_M)
    assert north == pytest.approx(expected_north, abs=_TOL_GENERAL_CROSSING_M)


def test_weighted_centroid_equal_weights() -> None:
    """Three crossings, equal weights, recovers the arithmetic mean."""
    crossings = [(0.0, 0.0), (3.0, 0.0), (0.0, 6.0)]
    weights = [1.0, 1.0, 1.0]

    east, north = weighted_centroid_of_crossings(crossings, weights)
    assert east == pytest.approx(1.0, abs=_TOL_CENTROID)
    assert north == pytest.approx(2.0, abs=_TOL_CENTROID)


def test_weighted_centroid_one_dominant_weight() -> None:
    """A weight 1000x larger than its peers pulls the centroid within 0.1 %.

    Pins that the weights actually scale each point's contribution, not
    merely act as an inclusion flag. The two non-dominant crossings sit
    a few metres from the dominant one (the operational case of two
    nearly-coincident bearing crossings plus one slightly off); a
    1000:1 weight ratio then keeps the centroid well inside 0.1 % of
    the dominant magnitude.
    """
    dominant_point = (100.0, 200.0)
    # Outliers near the dominant point so the 1000:1 weight ratio
    # bounds the relative deviation below 0.1 %. With max outlier
    # distance D from the dominant and total weight W + 2, the
    # displacement is at most 2 * D / (W + 2). For 0.1 % of the
    # ``magnitude_m`` (~ 224), we need D < ~112; here D ~ 7, well
    # inside.
    crossings = [(95.0, 195.0), dominant_point, (105.0, 205.0)]
    weights = [1.0, _DOMINANT_WEIGHT_RATIO, 1.0]

    east, north = weighted_centroid_of_crossings(crossings, weights)

    distance_m = math.hypot(east - dominant_point[0], north - dominant_point[1])
    magnitude_m = math.hypot(*dominant_point)
    assert distance_m / magnitude_m < _DOMINANT_TOLERANCE_FRACTION


def test_weighted_centroid_rejects_empty() -> None:
    """Empty inputs raise ``ValueError`` (Invariant 4 -- no silent fallback)."""
    with pytest.raises(ValueError, match="at least one"):
        weighted_centroid_of_crossings([], [])


def test_weighted_centroid_rejects_mismatched_lengths() -> None:
    """``crossings`` and ``weights`` of different length raise ``ValueError``."""
    with pytest.raises(ValueError, match="does not match"):
        weighted_centroid_of_crossings([(0.0, 0.0), (1.0, 1.0)], [1.0])
