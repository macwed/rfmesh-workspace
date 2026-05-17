"""Tests for the post-fit residuals + ``is_outlier`` flag (WS-CD-006).

Structure mirrors ``test_mle.py``: deterministic noise-free recovery
gates first (so a regression points at exactly one broken property),
then the wrap / ordering / threshold / defensive-input gates.

Every assertion in this file pins one of:

* the residual sign convention (``measured - predicted``, matching
  ``mle.py``);
* the predicted-azimuth convention (forward node->emitter, east-offset
  first, matching ``geometry.bearing_to_unit_vector`` and
  ``conftest.azimuth_node_to_emitter_deg``);
* the wrap-to-(-180, +180] correctness across the 0/360 seam;
* the ADR-005 §D3 outlier threshold (strict ``>``, per-bearing sigma);
* the ``INTERFACES.md`` §3 ordering invariant (residuals_deg index
  matches contributing_node_ids index matches input bearings index).
"""

from __future__ import annotations

import math

import pytest
from _helpers import (  # type: ignore[import-not-found, unused-ignore]
    MakeBearing,
    MakePosition,
    azimuth_node_to_emitter_deg,
)
from rfmesh_contracts.geospatial import (  # type: ignore[import-untyped, unused-ignore]
    GeodeticPosition,
)
from rfmesh_contracts.messages import (  # type: ignore[import-untyped, unused-ignore]
    BearingReport,
)
from rfmesh_fusion.exceptions import FusionError
from rfmesh_fusion.residuals import ResidualsResult, compute_residuals

# Tolerances. The "perfect" cases are float64 arithmetic on a single
# ``atan2`` round-trip; the round-off floor is well under 1e-12 deg.
# Pinning ``1e-9`` deg leaves headroom for any future refactor that
# adds an extra trig step without making the test flaky.
_TOL_PERFECT_DEG = 1e-9

# The ADR-005 §D3 outlier multiplier. Re-derived at the test edge --
# importing ``_OUTLIER_SIGMA_MULTIPLIER`` from the implementation would
# defeat the point of the threshold tests (they would pass any value
# the implementation set). The pinning here is what the ADR mandates.
_ADR_OUTLIER_MULT = 3.0

# Expected lengths for the scenarios below. Named so PLR2004 does not
# flag the literals and a reviewer sees what each "3" / "2" stands for.
_N_BEARINGS_TRIPLET = 3
_N_BEARINGS_PAIR = 2


# ---------------------------------------------------------------------------
# Helpers local to the residuals tests.
# ---------------------------------------------------------------------------


def _placeholder_node_position(make_position: MakePosition) -> GeodeticPosition:
    """A plausible ``GeodeticPosition`` to fill ``BearingReport.node_position``.

    ``compute_residuals`` does not consume the geodetic field; the ENU
    coordinates the function uses are passed separately. The placeholder
    only has to satisfy the contract validators. Mirrors the pattern
    in ``test_mle.py``.
    """
    return make_position(52.0, 21.0, sigma_m=5.0)


def _make_bearing_at(
    make_bearing: MakeBearing,
    make_position: MakePosition,
    node_enu: tuple[float, float],
    emitter_enu: tuple[float, float],
    *,
    sigma_deg: float = 1.0,
    azimuth_offset_deg: float = 0.0,
    node_id: str = "test-node",
) -> BearingReport:
    """``BearingReport`` whose azimuth is ``truth + azimuth_offset_deg``.

    With ``azimuth_offset_deg == 0`` the bearing is analytically
    consistent with the emitter at ``emitter_enu`` -- the residual at
    that emitter must be zero (modulo float64 round-off). Non-zero
    offsets are how the threshold tests inject a known disagreement.
    """
    perfect_az_deg = azimuth_node_to_emitter_deg(node_enu, emitter_enu)
    noisy_az_deg = (perfect_az_deg + azimuth_offset_deg) % 360.0
    return make_bearing(
        azimuth_deg=noisy_az_deg,
        sigma_deg=sigma_deg,
        node_position=_placeholder_node_position(make_position),
        node_id=node_id,
    )


def _equilateral_geometry(
    side_m: float,
    emitter_enu: tuple[float, float],
) -> list[tuple[float, float]]:
    """3 nodes at the vertices of an equilateral triangle around ``emitter_enu``.

    Same arrangement as ``test_mle.py``'s ``_equilateral_geometry``;
    circumradius ``side_m / sqrt(3)``, vertices at angles 90/210/330
    deg (math convention CCW from +east) around the emitter centre.
    The cross-test consistency matters because WS-CD-007 will run
    both modules on the same geometry and the test fixtures should
    not silently disagree on what an "equilateral 3-node" scenario
    means.
    """
    circumradius_m = side_m / math.sqrt(3.0)
    angles_rad = [math.radians(a) for a in (90.0, 210.0, 330.0)]
    return [
        (
            emitter_enu[0] + circumradius_m * math.cos(a),
            emitter_enu[1] + circumradius_m * math.sin(a),
        )
        for a in angles_rad
    ]


# ---------------------------------------------------------------------------
# 1. ResidualsResult is a frozen dataclass with three same-length tuples.
# ---------------------------------------------------------------------------


def test_residuals_result_dataclass_shape() -> None:
    """``ResidualsResult`` is a frozen 3-attribute container in the documented order.

    Asserts the public surface WS-CD-007's fuser will consume:
    ``residuals_deg: tuple[float, ...]``, ``is_outlier: tuple[bool, ...]``,
    ``contributing_node_ids: tuple[str, ...]``. All three same length.
    Frozen so a caller that stores a reference cannot mutate the
    answer behind another caller's back.
    """
    result = ResidualsResult(
        residuals_deg=(0.5, -1.2, 4.0),
        is_outlier=(False, False, True),
        contributing_node_ids=("alpha", "beta", "gamma"),
    )
    assert result.residuals_deg == (0.5, -1.2, 4.0)
    assert result.is_outlier == (False, False, True)
    assert result.contributing_node_ids == ("alpha", "beta", "gamma")
    assert (
        len(result.residuals_deg)
        == len(result.is_outlier)
        == len(result.contributing_node_ids)
        == _N_BEARINGS_TRIPLET
    )
    # Element types as documented (a ``bool`` is technically an ``int``
    # in Python; check the type strictly).
    assert all(isinstance(r, float) for r in result.residuals_deg)
    assert all(type(o) is bool for o in result.is_outlier)
    assert all(isinstance(nid, str) for nid in result.contributing_node_ids)
    # Frozen / slots check: assignment to a declared attribute raises.
    with pytest.raises((AttributeError, TypeError)):
        result.residuals_deg = (1.0,)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. Perfect-fit residuals are all ~zero, no outliers.
# ---------------------------------------------------------------------------


def test_perfect_fit_residuals_zero(
    make_bearing: MakeBearing,
    make_position: MakePosition,
) -> None:
    """Every bearing exactly aimed at the emitter -> every residual ~0.

    Three nodes at the vertices of an equilateral triangle of side
    4000 m centred on the emitter at ``(0, 0)``; each bearing's
    ``azimuth_deg`` is the *exact* node-to-emitter azimuth. The
    residual at the true emitter position must be zero (to within
    one ``atan2`` round-trip's worth of float64 round-off), and no
    bearing flagged as outlier.
    """
    emitter_enu = (0.0, 0.0)
    node_positions_enu = _equilateral_geometry(side_m=4000.0, emitter_enu=emitter_enu)
    bearings = [
        _make_bearing_at(make_bearing, make_position, p, emitter_enu, node_id=f"n{i}")
        for i, p in enumerate(node_positions_enu)
    ]

    result = compute_residuals(emitter_enu, bearings, node_positions_enu)

    assert len(result.residuals_deg) == _N_BEARINGS_TRIPLET
    for r in result.residuals_deg:
        assert abs(r) < _TOL_PERFECT_DEG, (
            f"Perfect-fit residual {r:.3e} deg exceeds {_TOL_PERFECT_DEG:.0e} -- "
            "either the sign convention is wrong or the predicted-azimuth "
            "formula does not match mle.py's _predicted_azimuths_and_jacobian."
        )
    assert result.is_outlier == (False, False, False)


# ---------------------------------------------------------------------------
# 3. A single offset bearing is correctly flagged (positive offset).
# ---------------------------------------------------------------------------


def test_one_bearing_offset_flagged_outlier(
    make_bearing: MakeBearing,
    make_position: MakePosition,
) -> None:
    """One bearing +5 deg off truth (sigma 1 deg) -> flagged, others not.

    The sign of the residual is also checked: with ``measured -
    predicted`` and ``measured = truth + 5``, the wrapped residual is
    ``+5`` deg, not ``-5``. A sign-flipped implementation would still
    flag the outlier (the threshold is on ``|r|``) but would fail the
    sign assertion -- which is the operator's "which way did the
    bearing point" diagnostic.
    """
    emitter_enu = (0.0, 0.0)
    node_positions_enu = _equilateral_geometry(side_m=4000.0, emitter_enu=emitter_enu)
    sigma_deg = 1.0
    offset_deg = 5.0  # sigma multiple = 5 > 3 -> outlier

    bearings = [
        _make_bearing_at(
            make_bearing,
            make_position,
            node_positions_enu[0],
            emitter_enu,
            sigma_deg=sigma_deg,
            azimuth_offset_deg=offset_deg,
            node_id="n0",
        ),
        _make_bearing_at(
            make_bearing,
            make_position,
            node_positions_enu[1],
            emitter_enu,
            sigma_deg=sigma_deg,
            node_id="n1",
        ),
        _make_bearing_at(
            make_bearing,
            make_position,
            node_positions_enu[2],
            emitter_enu,
            sigma_deg=sigma_deg,
            node_id="n2",
        ),
    ]

    result = compute_residuals(emitter_enu, bearings, node_positions_enu)

    assert result.is_outlier == (True, False, False)
    # Sign check: positive offset -> positive residual.
    assert result.residuals_deg[0] == pytest.approx(offset_deg, abs=1e-9)
    assert abs(result.residuals_deg[1]) < _TOL_PERFECT_DEG
    assert abs(result.residuals_deg[2]) < _TOL_PERFECT_DEG


# ---------------------------------------------------------------------------
# 4. Wrap-around correctness across the 0/360 seam (load-bearing test).
# ---------------------------------------------------------------------------


def test_residual_wraps_180_360_seam(
    make_bearing: MakeBearing,
    make_position: MakePosition,
) -> None:
    """Predicted 358 deg, measured 2 deg -> residual +4 deg (not -356).

    The single most likely place a residual implementation goes wrong:
    naive subtraction of two ``[0, 360)`` azimuths near the seam
    produces a ~356 deg "residual" that flags every otherwise-fine
    bearing as a 356-sigma outlier. The wrap-to-(-180, +180] is
    load-bearing; this test pins both directions.

    Geometry: emitter due-north of node 1 (predicted azimuth from
    node 1 = 0 deg = 360 deg modulo) is the natural seam-crossing
    construction. We place a single node at ``(0, -1000)`` so the
    emitter at ``(0, 0)`` is at predicted azimuth exactly 0.0 deg
    (true north), then inject the measured azimuth at ``2.0`` deg
    (positive case) and ``358.0`` deg (negative case) to span the
    seam in both directions.

    A subtler form of the same seam: predicted = 358 deg, measured =
    2 deg (and the symmetric pair). The asymmetric construction is
    achieved by rotating the node-emitter geometry slightly so the
    predicted azimuth lands at 358 deg.
    """
    sigma_deg = 1.0

    # --- Construction A: predicted = 0 deg, measured = 2 deg ---
    # Node directly south of emitter; predicted azimuth from node to
    # emitter is 0 deg (true north).
    emitter_enu = (0.0, 0.0)
    node_a_enu = (0.0, -1000.0)
    # Inject measured = 2 deg directly (bypass the helper to avoid
    # picking the "perfect" azimuth and adding offset; we are
    # constructing a known seam scenario).
    bearing_a_plus = make_bearing(
        azimuth_deg=2.0,
        sigma_deg=sigma_deg,
        node_position=_placeholder_node_position(make_position),
        node_id="a+",
    )
    bearing_a_minus = make_bearing(
        azimuth_deg=358.0,
        sigma_deg=sigma_deg,
        node_position=_placeholder_node_position(make_position),
        node_id="a-",
    )

    # Predicted = 0; measured = 2 -> raw diff +2 (no wrap needed; the
    # test still locks the no-wrap branch).
    result_plus = compute_residuals(emitter_enu, [bearing_a_plus], [node_a_enu])
    assert result_plus.residuals_deg[0] == pytest.approx(2.0, abs=1e-9), (
        f"Predicted 0 deg, measured 2 deg -> expected +2.0 deg residual, "
        f"got {result_plus.residuals_deg[0]:.6e}."
    )

    # Predicted = 0; measured = 358 -> raw diff +358 -> wrap to -2.
    # This is the seam-crossing case in the negative direction.
    result_minus = compute_residuals(emitter_enu, [bearing_a_minus], [node_a_enu])
    assert result_minus.residuals_deg[0] == pytest.approx(-2.0, abs=1e-9), (
        f"Predicted 0 deg, measured 358 deg -> expected -2.0 deg residual "
        f"(NOT +358), got {result_minus.residuals_deg[0]:.6e}. The wrap "
        "is failing -- check ((diff + 180) % 360) - 180 or atan2(sin, cos)."
    )

    # --- Construction B: predicted = 358 deg, measured = 2 deg ---
    # Rotate the geometry: place the node such that the emitter is at
    # a *negative* (counter-clockwise) 2-deg azimuth from the node,
    # i.e. predicted = 358 deg.
    # node at angle (180 + 2) deg from emitter, distance 1000 m, so the
    # azimuth from node back to emitter is (2 deg) mod 360 wait --
    # easier: pick node_b directly and compute predicted to confirm.
    # We want predicted_az_from_node_to_emitter = 358 deg, i.e.
    # the node is at (east, north) = emitter + 1000 * (sin(178), cos(178))
    # so that emitter - node points back along az = 358 deg.
    az_predicted_deg = 358.0
    # node = emitter - 1000 * (sin(az_predicted), cos(az_predicted))
    # then (emitter - node) = +1000 * (sin(az_predicted), cos(az_predicted))
    # and atan2(east, north) of that gives exactly az_predicted_deg.
    az_predicted_rad = math.radians(az_predicted_deg)
    node_b_enu = (
        emitter_enu[0] - 1000.0 * math.sin(az_predicted_rad),
        emitter_enu[1] - 1000.0 * math.cos(az_predicted_rad),
    )
    bearing_b_plus = make_bearing(
        azimuth_deg=2.0,
        sigma_deg=sigma_deg,
        node_position=_placeholder_node_position(make_position),
        node_id="b+",
    )
    bearing_b_minus = make_bearing(
        azimuth_deg=358.0,
        sigma_deg=sigma_deg,
        node_position=_placeholder_node_position(make_position),
        node_id="b-",
    )

    # Predicted = 358; measured = 2 -> raw diff -356 -> wrap to +4.
    result_b_plus = compute_residuals(emitter_enu, [bearing_b_plus], [node_b_enu])
    assert result_b_plus.residuals_deg[0] == pytest.approx(4.0, abs=1e-9), (
        f"Predicted 358 deg, measured 2 deg -> expected +4.0 deg "
        f"residual (NOT -356), got {result_b_plus.residuals_deg[0]:.6e}."
    )

    # Predicted = 358; measured = 358 -> raw diff 0 -> residual 0.
    # (The symmetric "+4 the other way" case: predicted 2, measured
    # 358 -> raw diff +356 -> wrap to -4. We can confirm it by
    # rotating the other way.)
    result_b_zero = compute_residuals(emitter_enu, [bearing_b_minus], [node_b_enu])
    assert abs(result_b_zero.residuals_deg[0]) < _TOL_PERFECT_DEG, (
        f"Predicted 358 deg, measured 358 deg -> expected ~0 deg "
        f"residual, got {result_b_zero.residuals_deg[0]:.6e}."
    )

    # --- Symmetric seam-crossing in the other direction ---
    # Predicted = 2, measured = 358 -> wrap to -4 (NOT +356).
    az_predicted_deg_c = 2.0
    az_predicted_rad_c = math.radians(az_predicted_deg_c)
    node_c_enu = (
        emitter_enu[0] - 1000.0 * math.sin(az_predicted_rad_c),
        emitter_enu[1] - 1000.0 * math.cos(az_predicted_rad_c),
    )
    bearing_c = make_bearing(
        azimuth_deg=358.0,
        sigma_deg=sigma_deg,
        node_position=_placeholder_node_position(make_position),
        node_id="c",
    )
    result_c = compute_residuals(emitter_enu, [bearing_c], [node_c_enu])
    assert result_c.residuals_deg[0] == pytest.approx(-4.0, abs=1e-9), (
        f"Predicted 2 deg, measured 358 deg -> expected -4.0 deg "
        f"residual (NOT +356), got {result_c.residuals_deg[0]:.6e}."
    )


# ---------------------------------------------------------------------------
# 5. Ordering invariant (INTERFACES.md §3).
# ---------------------------------------------------------------------------


def test_residual_ordering_matches_contributing_nodes(
    make_bearing: MakeBearing,
    make_position: MakePosition,
) -> None:
    """Output order matches input order; node_ids align with their residuals.

    Three distinct ``node_id`` values, three distinct azimuth offsets.
    The returned ``residuals_deg[k]`` must correspond to
    ``contributing_node_ids[k]`` must correspond to
    ``bearings[k].node_id``. This is ``INTERFACES.md`` §3's binding
    invariant on ``FixEvent.residuals_deg`` and
    ``FixEvent.contributing_nodes`` ordering -- if it does not hold
    here it cannot hold in the fuser, and the dashboard will mislabel
    every residual.
    """
    emitter_enu = (0.0, 0.0)
    node_positions_enu = _equilateral_geometry(side_m=4000.0, emitter_enu=emitter_enu)
    sigma_deg = 1.0

    # Three distinct offsets so a permutation would surface in the
    # residual values, not just the node_id ordering.
    offsets_deg = [4.0, -2.5, 1.0]
    node_ids = ["alpha", "beta", "gamma"]

    bearings = [
        _make_bearing_at(
            make_bearing,
            make_position,
            node_positions_enu[i],
            emitter_enu,
            sigma_deg=sigma_deg,
            azimuth_offset_deg=offsets_deg[i],
            node_id=node_ids[i],
        )
        for i in range(3)
    ]

    result = compute_residuals(emitter_enu, bearings, node_positions_enu)

    assert result.contributing_node_ids == tuple(node_ids), (
        f"Node-id order mismatch: input {node_ids}, output {result.contributing_node_ids}."
    )
    # Each residual matches the offset injected for that node_id.
    for i in range(3):
        assert result.residuals_deg[i] == pytest.approx(offsets_deg[i], abs=1e-9), (
            f"Residual at index {i} (node_id={node_ids[i]}) is "
            f"{result.residuals_deg[i]:.6e}; expected ~{offsets_deg[i]} deg. "
            "Output ordering does not match input ordering."
        )


# ---------------------------------------------------------------------------
# 6. Two-bearing minimum case: residuals are mathematically zero.
# ---------------------------------------------------------------------------


def test_two_bearing_minimum_residual_vacuous(
    make_bearing: MakeBearing,
    make_position: MakePosition,
) -> None:
    """2 bearings, 2 unknowns -> residuals at the analytic intersection ~0.

    Mathematically: a system with N equations and N unknowns has a
    unique solution (when non-degenerate), and that solution makes
    every residual exactly zero -- there is no residual degree of
    freedom to spend on misfit. With 2 bearings and 2 position
    unknowns, the LS minimum *is* the intersection, and the residuals
    at the intersection are zero.

    This is what ADR-005 §D4 first bullet captures: "2 bearings
    exactly (the minimum): residual is mathematically zero". The
    test pins the property so a future change to ``compute_residuals``
    (e.g. computing a weighted-mean offset to "center" residuals)
    cannot silently break it.

    Geometry: two perpendicular bearings (north-pointing from south
    node, east-pointing from west node) cross at the origin.
    """
    emitter_enu = (0.0, 0.0)
    node_south = (0.0, -1000.0)  # azimuth to emitter = 0 deg (north)
    node_west = (-1000.0, 0.0)  # azimuth to emitter = 90 deg (east)
    node_positions_enu = [node_south, node_west]
    bearings = [
        _make_bearing_at(make_bearing, make_position, node_south, emitter_enu, node_id="s"),
        _make_bearing_at(make_bearing, make_position, node_west, emitter_enu, node_id="w"),
    ]

    result = compute_residuals(emitter_enu, bearings, node_positions_enu)

    assert len(result.residuals_deg) == _N_BEARINGS_PAIR
    for r in result.residuals_deg:
        assert abs(r) < _TOL_PERFECT_DEG, (
            f"2-bearing residual {r:.3e} deg exceeds {_TOL_PERFECT_DEG:.0e} -- "
            "the 2-eq / 2-unknown LS-minimum residual must be zero "
            "(ADR-005 §D4 first bullet)."
        )
    assert result.is_outlier == (False, False)


# ---------------------------------------------------------------------------
# 7. The is_outlier threshold is strictly ``>`` 3 (ADR-005 §D3).
# ---------------------------------------------------------------------------


def test_is_outlier_threshold_at_exactly_3(
    make_bearing: MakeBearing,
    make_position: MakePosition,
) -> None:
    """Residual = 3*sigma -> NOT outlier; 3.001*sigma -> outlier.

    ADR-005 §D3 says ``|r_i| / sigma_i > 3`` -- strictly greater. A
    residual of exactly 3*sigma is on the threshold; the strict
    inequality means it is *not* flagged. The 3.001*sigma value
    crosses the strict threshold. Both signs of the residual checked.
    """
    sigma_deg = 1.0
    emitter_enu = (0.0, 0.0)
    node_enu = (0.0, -1000.0)  # azimuth to emitter = 0 deg
    node_positions = [node_enu]

    on_threshold = _ADR_OUTLIER_MULT * sigma_deg
    just_over = _ADR_OUTLIER_MULT * sigma_deg + 0.001

    # Positive direction.
    bearing_on_pos = make_bearing(
        azimuth_deg=on_threshold,
        sigma_deg=sigma_deg,
        node_position=_placeholder_node_position(make_position),
        node_id="on+",
    )
    bearing_over_pos = make_bearing(
        azimuth_deg=just_over,
        sigma_deg=sigma_deg,
        node_position=_placeholder_node_position(make_position),
        node_id="over+",
    )
    # Negative direction (residual sign negative; |r| still on/over
    # threshold). Measured = 360 - x maps to a -x residual after wrap.
    bearing_on_neg = make_bearing(
        azimuth_deg=(360.0 - on_threshold) % 360.0,
        sigma_deg=sigma_deg,
        node_position=_placeholder_node_position(make_position),
        node_id="on-",
    )
    bearing_over_neg = make_bearing(
        azimuth_deg=(360.0 - just_over) % 360.0,
        sigma_deg=sigma_deg,
        node_position=_placeholder_node_position(make_position),
        node_id="over-",
    )

    result_on_pos = compute_residuals(emitter_enu, [bearing_on_pos], node_positions)
    result_over_pos = compute_residuals(emitter_enu, [bearing_over_pos], node_positions)
    result_on_neg = compute_residuals(emitter_enu, [bearing_on_neg], node_positions)
    result_over_neg = compute_residuals(emitter_enu, [bearing_over_neg], node_positions)

    assert result_on_pos.is_outlier == (False,), (
        f"Residual = exactly 3*sigma = {on_threshold} (positive) must NOT "
        "be flagged per ADR-005 §D3 (strictly >). "
        f"Got is_outlier={result_on_pos.is_outlier}."
    )
    assert result_over_pos.is_outlier == (True,), (
        f"Residual = 3.001*sigma = {just_over} (positive) MUST be flagged "
        f"per ADR-005 §D3. Got is_outlier={result_over_pos.is_outlier}."
    )
    assert result_on_neg.is_outlier == (False,), (
        f"Residual = -3*sigma = -{on_threshold} (negative) must NOT be "
        "flagged (|r| comparison, but still strict >). "
        f"Got is_outlier={result_on_neg.is_outlier}."
    )
    assert result_over_neg.is_outlier == (True,), (
        f"Residual = -3.001*sigma = -{just_over} (negative) MUST be flagged. "
        f"Got is_outlier={result_over_neg.is_outlier}."
    )


# ---------------------------------------------------------------------------
# 8. Negative residual still flagged on |r|.
# ---------------------------------------------------------------------------


def test_negative_residual_outlier(
    make_bearing: MakeBearing,
    make_position: MakePosition,
) -> None:
    """Residual = -5*sigma -> outlier (absolute-value comparison).

    A sign-checked threshold (``r > 3*sigma`` instead of
    ``|r| > 3*sigma``) would miss every "bearing offset
    counter-clockwise" outlier. This test pins the absolute-value
    semantics.
    """
    emitter_enu = (0.0, 0.0)
    node_positions_enu = _equilateral_geometry(side_m=4000.0, emitter_enu=emitter_enu)
    sigma_deg = 1.0
    offset_deg = -5.0  # negative -> negative residual; |r| = 5*sigma > 3

    bearings = [
        _make_bearing_at(
            make_bearing,
            make_position,
            node_positions_enu[0],
            emitter_enu,
            sigma_deg=sigma_deg,
            azimuth_offset_deg=offset_deg,
            node_id="n0",
        ),
        _make_bearing_at(
            make_bearing, make_position, node_positions_enu[1], emitter_enu, node_id="n1"
        ),
        _make_bearing_at(
            make_bearing, make_position, node_positions_enu[2], emitter_enu, node_id="n2"
        ),
    ]

    result = compute_residuals(emitter_enu, bearings, node_positions_enu)

    assert result.residuals_deg[0] == pytest.approx(offset_deg, abs=1e-9), (
        f"Negative offset -5 deg -> expected negative residual; got {result.residuals_deg[0]:.6e}."
    )
    assert result.is_outlier == (True, False, False), (
        "A negative-residual outlier must be flagged (|r| comparison, "
        f"not signed). Got is_outlier={result.is_outlier}."
    )


# ---------------------------------------------------------------------------
# 9. Defensive: a zero/negative sigma slipping past the contract validator.
# ---------------------------------------------------------------------------


def test_zero_sigma_input_handled(
    make_bearing: MakeBearing,
    make_position: MakePosition,
) -> None:
    """``azimuth_sigma_deg <= 0`` raises ``FusionError`` (Invariant B3).

    The contract validator enforces ``azimuth_sigma_deg > 0`` and
    rejects malformed reports at construction time -- the standard
    ``BearingReport(...)`` constructor cannot be used to build the
    malformed input. ``model_construct`` bypasses validators (this
    is Pydantic's documented escape hatch); the test uses it to
    simulate the worst-case "a programmatically-mutated bearing
    reached fusion" path. ``compute_residuals`` must refuse loudly
    rather than silently emit a ``+inf`` outlier ratio.

    Two cases: sigma = 0.0 (the literal zero-division), sigma = -1.0
    (a negative sigma is nonsensical but the validator forbids it
    for the same reason; defence-in-depth covers it identically).
    """
    emitter_enu = (0.0, 0.0)
    node_enu = (0.0, -1000.0)
    # A valid (positive) bearing first, so we can confirm the
    # baseline path returns successfully before injecting the
    # malformed one.
    good_bearing = _make_bearing_at(
        make_bearing, make_position, node_enu, emitter_enu, node_id="good"
    )
    baseline = compute_residuals(emitter_enu, [good_bearing], [node_enu])
    assert baseline.is_outlier == (False,)

    # model_construct skips validators. The Pydantic v2 form; passes
    # all required fields verbatim so the resulting object is
    # structurally a BearingReport even though its sigma is
    # contract-invalid.
    bad_sigma_zero = BearingReport.model_construct(
        schema_version=good_bearing.schema_version,
        node_id="bad-zero",
        t_unix_ns=good_bearing.t_unix_ns,
        node_position=good_bearing.node_position,
        azimuth_deg=good_bearing.azimuth_deg,
        azimuth_sigma_deg=0.0,  # <-- forbidden by validator; bypassed here
        method=good_bearing.method,
        snr_db=None,
        emitter_class=None,
        classification_confidence=None,
        raw_pseudospectrum=None,
    )

    with pytest.raises(FusionError, match=r"azimuth_sigma_deg|sigma|zero|positive"):
        compute_residuals(emitter_enu, [bad_sigma_zero], [node_enu])

    bad_sigma_neg = BearingReport.model_construct(
        schema_version=good_bearing.schema_version,
        node_id="bad-neg",
        t_unix_ns=good_bearing.t_unix_ns,
        node_position=good_bearing.node_position,
        azimuth_deg=good_bearing.azimuth_deg,
        azimuth_sigma_deg=-1.0,  # <-- forbidden by validator; bypassed here
        method=good_bearing.method,
        snr_db=None,
        emitter_class=None,
        classification_confidence=None,
        raw_pseudospectrum=None,
    )

    with pytest.raises(FusionError, match=r"azimuth_sigma_deg|sigma|positive"):
        compute_residuals(emitter_enu, [bad_sigma_neg], [node_enu])


# ---------------------------------------------------------------------------
# 10. Structural input error: length mismatch.
# ---------------------------------------------------------------------------


def test_length_mismatch_raises(
    make_bearing: MakeBearing,
    make_position: MakePosition,
) -> None:
    """``len(bearings) != len(node_positions_enu)`` -> ``FusionError``.

    The two sequences are *parallel*; a length mismatch is a
    structural caller bug, not a degenerate-geometry case. Refuse
    loudly with an informative message (Invariant B3).
    """
    emitter_enu = (0.0, 0.0)
    node_enu = (0.0, -1000.0)
    bearing = _make_bearing_at(make_bearing, make_position, node_enu, emitter_enu, node_id="solo")

    # 1 bearing, 2 positions.
    with pytest.raises(FusionError, match=r"len|match"):
        compute_residuals(emitter_enu, [bearing], [node_enu, (100.0, 100.0)])

    # 2 bearings, 1 position.
    with pytest.raises(FusionError, match=r"len|match"):
        compute_residuals(emitter_enu, [bearing, bearing], [node_enu])

    # 0 bearings, 1 position. ``compute_residuals`` does not enforce
    # a lower bound on the bearing count (the solver does, upstream);
    # the *mismatch* is still the structural error here.
    with pytest.raises(FusionError, match=r"len|match"):
        compute_residuals(emitter_enu, [], [node_enu])
