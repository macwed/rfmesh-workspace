"""Tests for the closed-form Stansfield seed (WS-CD-002).

The recovery tests use perfect (noise-free) bearings: a true emitter
position is chosen, the geometrically exact azimuth from each node to
that emitter is computed, those azimuths feed
``stansfield_seed``, and the solver must recover the truth to within
floating-point round-off. Noise is exercised in WS-CD-008's
honest-ellipse Monte Carlo, not here.

Per-bearing weights use representative ``sigma_deg`` values; the
recovery tests are by construction insensitive to absolute sigma
(uniform sigma cancels), so a small or large value would work
identically.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import pytest
from _helpers import MakeBearing, MakePosition  # type: ignore[import-not-found, unused-ignore]
from rfmesh_contracts.geospatial import (  # type: ignore[import-untyped, unused-ignore]
    GeodeticPosition,
)
from rfmesh_contracts.messages import (  # type: ignore[import-untyped, unused-ignore]
    BearingReport,
)
from rfmesh_fusion.exceptions import DegenerateGeometryError
from rfmesh_fusion.geometry import bearing_to_unit_vector, ray_ray_crossing
from rfmesh_fusion.stansfield import stansfield_seed

# Recovery tolerances per ticket acceptance criteria.
_TOL_EQUILATERAL_M = 1e-6
_TOL_SQUARE_M = 1e-6
_TOL_TWO_BEARINGS_M = 1e-9
_TOL_NEARLY_COLLINEAR_M = 1e-3

# Per-bearing sigmas the recovery tests use. Uniform sigma cancels out
# of the weighted LS, so the absolute value is immaterial to recovery;
# we set a representative ``1 deg`` (a clean L2_MUSIC sigma) for the
# "everything equal" tests and a wider value for the "favour the
# precise one" test, plus an extremely tight one for the dominant
# bearing.
_SIGMA_TIGHT_DEG = 0.01
_SIGMA_NOMINAL_DEG = 1.0
_SIGMA_WIDE_DEG = 10.0


def _azimuth_node_to_emitter(
    node_enu: tuple[float, float],
    emitter_enu: tuple[float, float],
) -> float:
    """Return the geographic azimuth from ``node_enu`` to ``emitter_enu``.

    Degrees, true north = 0, CW positive, wrapped to ``[0, 360)``.
    Mirrors ``geometry.bearing_to_unit_vector`` exactly: azimuth =
    ``atan2(east, north)``.
    """
    de = emitter_enu[0] - node_enu[0]
    dn = emitter_enu[1] - node_enu[1]
    az_deg = math.degrees(math.atan2(de, dn))
    return az_deg % 360.0


def _make_perfect_bearing(
    make_bearing: MakeBearing,
    make_position: MakePosition,
    node_enu: tuple[float, float],
    emitter_enu: tuple[float, float],
    *,
    sigma_deg: float,
    origin: GeodeticPosition,
) -> BearingReport:
    """Build a ``BearingReport`` with the exact azimuth to the emitter.

    The ``node_position`` field is filled with a placeholder
    ``GeodeticPosition`` (constructed from ``origin`` plus the ENU
    offset interpreted as small geodetic deltas). The Stansfield solver
    only consumes ``azimuth_deg`` and ``azimuth_sigma_deg`` from the
    report; the ENU coordinates the solver actually uses are passed
    separately via ``node_positions_enu``. The geodetic
    ``node_position`` is kept self-consistent only enough to satisfy
    the contract validators.
    """
    azimuth_deg = _azimuth_node_to_emitter(node_enu, emitter_enu)
    # The geodetic offset does not need to match node_enu exactly; we
    # just need a valid GeodeticPosition. Re-using ``origin`` is fine.
    node_position = make_position(
        origin.lat_deg,
        origin.lon_deg,
        sigma_m=5.0,
    )
    return make_bearing(
        azimuth_deg=azimuth_deg,
        sigma_deg=sigma_deg,
        node_position=node_position,
    )


# Helper alias for type-hinting the local ``make_bearing_at`` factory the
# tests build once they have ``origin`` in hand.
MakeBearingAt = Callable[[tuple[float, float], tuple[float, float], float], BearingReport]


@pytest.fixture
def make_bearing_at(
    make_bearing: MakeBearing,
    make_position: MakePosition,
    origin: GeodeticPosition,
) -> MakeBearingAt:
    """Return a closure that builds a perfect bearing from ``node`` to ``emitter``.

    Saves four function arguments at every test call site.
    """

    def _factory(
        node_enu: tuple[float, float],
        emitter_enu: tuple[float, float],
        sigma_deg: float,
    ) -> BearingReport:
        return _make_perfect_bearing(
            make_bearing,
            make_position,
            node_enu,
            emitter_enu,
            sigma_deg=sigma_deg,
            origin=origin,
        )

    return _factory


def test_stansfield_recovers_truth_equilateral_3_nodes(
    make_bearing_at: MakeBearingAt,
) -> None:
    """Equilateral 3-node geometry recovers the central emitter exactly.

    Nodes at the vertices of an equilateral triangle of side 4000 m
    centred on the emitter at ``(0, 0)``. The circumradius is
    ``s / sqrt(3) ~= 2309.4 m``. Perfect bearings, uniform sigma --
    recovery is limited only by float64 round-off.
    """
    side_m = 4000.0
    circumradius_m = side_m / math.sqrt(3.0)
    emitter_enu = (0.0, 0.0)

    # Nodes at 90 deg, 210 deg, 330 deg measured CCW from +east in the
    # math convention -- arbitrary but symmetric about the emitter.
    angles_rad = [math.radians(a) for a in (90.0, 210.0, 330.0)]
    node_positions_enu = [
        (circumradius_m * math.cos(a), circumradius_m * math.sin(a)) for a in angles_rad
    ]

    bearings = [make_bearing_at(p, emitter_enu, _SIGMA_NOMINAL_DEG) for p in node_positions_enu]

    east, north = stansfield_seed(bearings, node_positions_enu)
    assert east == pytest.approx(emitter_enu[0], abs=_TOL_EQUILATERAL_M)
    assert north == pytest.approx(emitter_enu[1], abs=_TOL_EQUILATERAL_M)


def test_stansfield_recovers_truth_square_4_nodes(
    make_bearing_at: MakeBearingAt,
) -> None:
    """4-node square geometry recovers an off-centre emitter exactly.

    Nodes at the corners of a 5000 m square centred on ENU origin;
    emitter at ``(1500, 800)``. Perfect bearings, uniform sigma.
    """
    half_side = 2500.0
    emitter_enu = (1500.0, 800.0)
    node_positions_enu: list[tuple[float, float]] = [
        (half_side, half_side),
        (-half_side, half_side),
        (-half_side, -half_side),
        (half_side, -half_side),
    ]

    bearings = [make_bearing_at(p, emitter_enu, _SIGMA_NOMINAL_DEG) for p in node_positions_enu]

    east, north = stansfield_seed(bearings, node_positions_enu)
    assert east == pytest.approx(emitter_enu[0], abs=_TOL_SQUARE_M)
    assert north == pytest.approx(emitter_enu[1], abs=_TOL_SQUARE_M)


def test_stansfield_two_bearings_exact(make_bearing_at: MakeBearingAt) -> None:
    """Minimum case: 2 perfect bearings -> exact ray-ray crossing.

    Two bearings define a unique intersection; with no noise the LS
    solution must be that exact crossing.
    """
    emitter_enu = (250.0, 400.0)
    node_positions_enu: list[tuple[float, float]] = [
        (-1000.0, 0.0),
        (0.0, -1000.0),
    ]

    bearings = [make_bearing_at(p, emitter_enu, _SIGMA_NOMINAL_DEG) for p in node_positions_enu]

    east, north = stansfield_seed(bearings, node_positions_enu)
    assert east == pytest.approx(emitter_enu[0], abs=_TOL_TWO_BEARINGS_M)
    assert north == pytest.approx(emitter_enu[1], abs=_TOL_TWO_BEARINGS_M)


def test_stansfield_weights_favour_precise_bearing(
    make_bearing_at: MakeBearingAt,
    make_bearing: MakeBearing,
    make_position: MakePosition,
    origin: GeodeticPosition,
) -> None:
    """A 0.01-deg-sigma bearing dominates two 10-deg-sigma perturbed bearings.

    One node has a tight sigma and a perfect bearing to the true
    emitter. Two other nodes have wide sigmas and bearings pointing at
    deliberately-perturbed fake targets a few hundred metres off truth
    -- their pairwise crossing sits far from the precise bearing's ray.

    The weighted LS solution must land closer to the true emitter than
    to the unweighted centroid of the three pairwise ray crossings --
    i.e. the ``1 / sigma^2`` weighting actually enters the solve.
    """
    true_emitter_enu = (0.0, 0.0)

    # Precise node directly south of truth; perfect bearing -> precise
    # ray is the east = 0 axis pointing north.
    precise_node_enu = (0.0, -1000.0)
    precise_bearing = make_bearing_at(precise_node_enu, true_emitter_enu, _SIGMA_TIGHT_DEG)

    # Two wide-sigma nodes far to the east and west on the east axis.
    # Each points at a fake target hundreds of metres off truth; the
    # two wide bearings' pairwise crossing then sits far east of the
    # precise ray.
    wide_node_a_enu = (-2000.0, 0.0)
    wide_node_b_enu = (2000.0, 0.0)
    fake_target_a = (200.0, 100.0)
    fake_target_b = (-400.0, 200.0)

    bearing_a = make_bearing(
        azimuth_deg=_azimuth_node_to_emitter(wide_node_a_enu, fake_target_a),
        sigma_deg=_SIGMA_WIDE_DEG,
        node_position=make_position(origin.lat_deg, origin.lon_deg, sigma_m=5.0),
    )
    bearing_b = make_bearing(
        azimuth_deg=_azimuth_node_to_emitter(wide_node_b_enu, fake_target_b),
        sigma_deg=_SIGMA_WIDE_DEG,
        node_position=make_position(origin.lat_deg, origin.lon_deg, sigma_m=5.0),
    )

    bearings = [precise_bearing, bearing_a, bearing_b]
    node_positions_enu = [precise_node_enu, wide_node_a_enu, wide_node_b_enu]

    east, north = stansfield_seed(bearings, node_positions_enu)

    # Centroid of the three pairwise ray-ray crossings -- the
    # "unweighted centroid of the three rays" the ticket names. With
    # bearings A and B perturbed to point at fake targets far from
    # truth, their pairwise crossing sits far east of truth, dragging
    # this centroid off the precise ray.
    azimuths_deg = [b.azimuth_deg for b in bearings]
    unit_vectors = [bearing_to_unit_vector(a) for a in azimuths_deg]
    pairwise_crossings: list[tuple[float, float]] = []
    for i in range(len(bearings)):
        for j in range(i + 1, len(bearings)):
            crossing = ray_ray_crossing(
                node_positions_enu[i],
                unit_vectors[i],
                node_positions_enu[j],
                unit_vectors[j],
            )
            assert crossing is not None  # the geometry above is non-degenerate
            pairwise_crossings.append(crossing)
    centroid_east = sum(c[0] for c in pairwise_crossings) / len(pairwise_crossings)
    centroid_north = sum(c[1] for c in pairwise_crossings) / len(pairwise_crossings)

    weighted_distance_m = math.hypot(east - true_emitter_enu[0], north - true_emitter_enu[1])
    centroid_distance_m = math.hypot(
        centroid_east - true_emitter_enu[0],
        centroid_north - true_emitter_enu[1],
    )

    # The weighted solution must be meaningfully closer to truth than
    # the unweighted centroid of pairwise crossings -- if the weights
    # are not entering the LS, this assertion fails.
    assert weighted_distance_m < centroid_distance_m


def test_stansfield_nearly_collinear_geometry_still_solves(
    make_bearing_at: MakeBearingAt,
) -> None:
    """3 nodes in a row at ``y = 0`` recover an off-axis emitter.

    Spacing 2000 m along east; emitter at ``(1000, 3000)``. The normal
    matrix is poorly conditioned (small north-axis baseline relative
    to range), but not yet singular -- the solver returns the truth
    within 1 mm.
    """
    emitter_enu = (1000.0, 3000.0)
    node_positions_enu: list[tuple[float, float]] = [
        (-2000.0, 0.0),
        (0.0, 0.0),
        (2000.0, 0.0),
    ]

    bearings = [make_bearing_at(p, emitter_enu, _SIGMA_NOMINAL_DEG) for p in node_positions_enu]

    east, north = stansfield_seed(bearings, node_positions_enu)
    assert east == pytest.approx(emitter_enu[0], abs=_TOL_NEARLY_COLLINEAR_M)
    assert north == pytest.approx(emitter_enu[1], abs=_TOL_NEARLY_COLLINEAR_M)


def test_stansfield_raises_on_parallel_rays(
    make_bearing: MakeBearing,
    make_position: MakePosition,
    origin: GeodeticPosition,
) -> None:
    """All bearings azimuth 90 deg -> rank-1 normal matrix -> raises.

    The error message must mention ``singular`` or ``degenerate`` or
    ``rank`` so a future operator log line is self-explanatory.
    """
    placeholder_position = make_position(origin.lat_deg, origin.lon_deg, sigma_m=5.0)
    bearings = [
        make_bearing(
            azimuth_deg=90.0,
            sigma_deg=_SIGMA_NOMINAL_DEG,
            node_position=placeholder_position,
        )
        for _ in range(3)
    ]
    node_positions_enu = [(0.0, 0.0), (1000.0, 0.0), (0.0, 1000.0)]

    with pytest.raises(DegenerateGeometryError, match=r"singular|degenerate|rank"):
        stansfield_seed(bearings, node_positions_enu)


def test_stansfield_raises_on_coincident_nodes(
    make_bearing_at: MakeBearingAt,
) -> None:
    """All node positions coincident -> raises ``DegenerateGeometryError``.

    Three different azimuths from the same point have no parallax
    baseline. The LS would mathematically return that common point as
    the "emitter"; the solver refuses that geometrically meaningless
    answer instead.
    """
    common_node_enu = (250.0, 350.0)
    emitter_enu = (1500.0, 1500.0)
    bearings = [
        make_bearing_at(common_node_enu, emitter_enu, _SIGMA_NOMINAL_DEG),
        # Use different placeholder emitters so the bearings are not
        # all identical (which would also be parallel-rays). The
        # solver must catch coincident nodes regardless of the
        # bearings actually being non-parallel.
        make_bearing_at(common_node_enu, (1500.0, -1500.0), _SIGMA_NOMINAL_DEG),
        make_bearing_at(common_node_enu, (-1500.0, 1500.0), _SIGMA_NOMINAL_DEG),
    ]
    node_positions_enu = [common_node_enu, common_node_enu, common_node_enu]

    with pytest.raises(DegenerateGeometryError, match=r"coincident|rank"):
        stansfield_seed(bearings, node_positions_enu)


def test_stansfield_raises_on_below_minimum(make_bearing_at: MakeBearingAt) -> None:
    """Single bearing -> raises ``DegenerateGeometryError``.

    One bearing is a ray, not a fix. The solver refuses rather than
    inventing a position.
    """
    bearings = [make_bearing_at((0.0, 0.0), (1000.0, 1000.0), _SIGMA_NOMINAL_DEG)]
    node_positions_enu = [(0.0, 0.0)]

    with pytest.raises(DegenerateGeometryError, match=r"at least|minimum|2"):
        stansfield_seed(bearings, node_positions_enu)
