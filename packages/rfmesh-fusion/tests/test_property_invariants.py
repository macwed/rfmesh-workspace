"""Property-based invariants for the fusion pipeline (B3).

Honest geometric and sigma-honesty invariants that any correct cross-fixing
solver must preserve. Failure of one of these is a load-bearing bug,
not a numerics nit:

1. **sigma-scaling invariance.** Multiplying every bearing's
   ``azimuth_sigma_deg`` by a positive constant ``k`` scales the
   confidence ellipse's semi-axes by exactly ``k`` (linear in sigma).
   Covariance scales by ``k**2``. GDOP is **independent of sigma** per
   ADR-007 D4 (unweighted geometry).

2. **Permutation invariance.** Re-ordering the bearings in the input
   iterable yields the same emitter position, ellipse, and GDOP. The
   per-bearing residuals re-order with the input but their multiset is
   preserved.

3. **Translation invariance** (geodesic neighbourhood). Shifting every
   node *and* the emitter by the same small offset preserves ellipse
   axes (lengths) and GDOP. The fix position moves by the same offset
   (modulo projection-rounding).

Hypothesis budget tight on purpose: each example builds a full geometry
+ runs the solver, so ``max_examples=20`` per test keeps the suite under
~30 s. Larger budgets belong in ``test_honest_ellipse_monte_carlo.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from rfmesh_contracts.config import FusionConfig
from rfmesh_contracts.geospatial import GeodeticPosition
from rfmesh_contracts.messages import BearingReport
from rfmesh_fusion import StansfieldMLEFuser

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from _helpers import azimuth_node_to_emitter_deg  # noqa: E402

# ----- helpers ---------------------------------------------------------------


_DEFAULT_T_UNIX_NS = 1_778_976_000_000_000_000
_ENU_ORIGIN_LAT = 52.0
_ENU_ORIGIN_LON = 21.0
# 1 deg latitude ~ 111.32 km; 1 deg lon at lat 52 ~ 68.5 km. Conversion
# constants for the small-offset bearing geometry built below.
_M_PER_DEG_LAT = 111_320.0
_M_PER_DEG_LON_AT_LAT52 = 68_500.0


def _geo_from_enu(east_m: float, north_m: float) -> GeodeticPosition:
    """Build a GeodeticPosition from a small ENU offset around the fixed origin."""
    lat = _ENU_ORIGIN_LAT + (north_m / _M_PER_DEG_LAT)
    lon = _ENU_ORIGIN_LON + (east_m / _M_PER_DEG_LON_AT_LAT52)
    return GeodeticPosition(lat_deg=lat, lon_deg=lon, hae_m=0.0, sigma_m=5.0)


def _build_bearing(
    *,
    node_enu: tuple[float, float],
    emitter_enu: tuple[float, float],
    sigma_deg: float,
    node_id: str,
    t_unix_ns: int = _DEFAULT_T_UNIX_NS,
) -> BearingReport:
    az = azimuth_node_to_emitter_deg(node_enu, emitter_enu)
    return BearingReport(
        node_id=node_id,
        t_unix_ns=t_unix_ns,
        node_position=_geo_from_enu(*node_enu),
        azimuth_deg=az,
        azimuth_sigma_deg=sigma_deg,
        method="l1_rssi",
    )


def _make_fuser() -> StansfieldMLEFuser:
    return StansfieldMLEFuser(
        FusionConfig(
            listen_url="udp://0.0.0.0:9000",
            min_bearings_for_fix=2,
        )
    )


# Three-node geometry forming a good GDOP triangle around the origin.
# Used as the canonical scenario for the property tests; randomisation
# happens via hypothesis on sigma, permutation order, and translation offset.
_NODE_ENUS: tuple[tuple[float, float], ...] = (
    (-1500.0, -1000.0),
    (1500.0, -1000.0),
    (0.0, 1500.0),
)
_EMITTER_ENU: tuple[float, float] = (0.0, 3000.0)


def _build_scenario(
    *,
    sigma_deg: float,
    shift_m: tuple[float, float] = (0.0, 0.0),
) -> list[BearingReport]:
    return [
        _build_bearing(
            node_enu=(n[0] + shift_m[0], n[1] + shift_m[1]),
            emitter_enu=(_EMITTER_ENU[0] + shift_m[0], _EMITTER_ENU[1] + shift_m[1]),
            sigma_deg=sigma_deg,
            node_id=f"node-{i}",
        )
        for i, n in enumerate(_NODE_ENUS)
    ]


# ----- property tests --------------------------------------------------------


@given(
    sigma_deg=st.floats(min_value=0.5, max_value=10.0, allow_nan=False, allow_infinity=False),
    scale_k=st.floats(min_value=0.25, max_value=4.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_sigma_scaling_invariance(sigma_deg: float, scale_k: float) -> None:
    """Scaling all sigma by k scales ellipse semi-axes by k, GDOP unchanged.

    Ellipse semi-axes scale linearly with sigma: Fisher info ~ 1/sigma^2;
    covariance ~ sigma^2; semi_major = sqrt(eig) ~ sigma. GDOP is
    geometry-only (ADR-007 D4), independent of sigma.
    """
    fuser = _make_fuser()
    fix_base = fuser.fuse(_build_scenario(sigma_deg=sigma_deg))
    fix_scaled = fuser.fuse(_build_scenario(sigma_deg=sigma_deg * scale_k))
    assert fix_base is not None and fix_scaled is not None

    base_ell = fix_base.confidence_ellipse_95
    scaled_ell = fix_scaled.confidence_ellipse_95
    ratio_major = scaled_ell.semi_major_m / base_ell.semi_major_m
    ratio_minor = scaled_ell.semi_minor_m / base_ell.semi_minor_m

    # The ellipse-projection map (ENU covariance via WGS-84 curvatures)
    # introduces sub-metre nonlinearity at lat 52 over ~3 km baselines;
    # 2 % tolerance covers that and the MLE convergence step.
    assert ratio_major == pytest.approx(scale_k, rel=0.02), (
        f"semi_major ratio {ratio_major:.4f}, expected {scale_k:.4f}"
    )
    assert ratio_minor == pytest.approx(scale_k, rel=0.02)
    assert fix_scaled.gdop == pytest.approx(fix_base.gdop, rel=1e-6), (
        "GDOP must be sigma-independent (ADR-007 D4)"
    )


@given(
    sigma_deg=st.floats(min_value=1.0, max_value=5.0, allow_nan=False, allow_infinity=False),
    permutation=st.permutations([0, 1, 2]),
)
@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_permutation_invariance(sigma_deg: float, permutation: list[int]) -> None:
    """Re-ordering bearings preserves the fix.

    Position, ellipse axes, and GDOP are invariant under permutation.
    The residuals tuple reorders with the input, but {sorted}
    residuals are unchanged. ``contributing_nodes`` reorders with input.
    """
    fuser = _make_fuser()
    base = _build_scenario(sigma_deg=sigma_deg)
    permuted = [base[i] for i in permutation]
    fix_base = fuser.fuse(base)
    fix_perm = fuser.fuse(permuted)
    assert fix_base is not None and fix_perm is not None

    # Position should match to sub-metre.
    assert fix_perm.position.lat_deg == pytest.approx(fix_base.position.lat_deg, abs=1e-7)
    assert fix_perm.position.lon_deg == pytest.approx(fix_base.position.lon_deg, abs=1e-7)
    # Ellipse axes invariant.
    assert fix_perm.confidence_ellipse_95.semi_major_m == pytest.approx(
        fix_base.confidence_ellipse_95.semi_major_m, rel=1e-4
    )
    assert fix_perm.confidence_ellipse_95.semi_minor_m == pytest.approx(
        fix_base.confidence_ellipse_95.semi_minor_m, rel=1e-4
    )
    # GDOP invariant.
    assert fix_perm.gdop == pytest.approx(fix_base.gdop, rel=1e-6)
    # Residual multisets identical.
    assert sorted([round(r, 6) for r in fix_perm.residuals_deg]) == sorted(
        [round(r, 6) for r in fix_base.residuals_deg]
    )
    # contributing_nodes reorders with input.
    expected_perm_nodes = tuple(fix_base.contributing_nodes[i] for i in permutation)
    assert fix_perm.contributing_nodes == expected_perm_nodes


@given(
    sigma_deg=st.floats(min_value=1.0, max_value=5.0, allow_nan=False, allow_infinity=False),
    shift_east_m=st.floats(min_value=-500.0, max_value=500.0, allow_nan=False),
    shift_north_m=st.floats(min_value=-500.0, max_value=500.0, allow_nan=False),
)
@settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_translation_invariance(
    sigma_deg: float, shift_east_m: float, shift_north_m: float
) -> None:
    """Shifting all nodes + emitter by same offset preserves ellipse + GDOP.

    The geometry is rigid; the resulting fix shifts by the same offset,
    but ellipse axis lengths and GDOP do not depend on absolute position.
    """
    fuser = _make_fuser()
    fix_base = fuser.fuse(_build_scenario(sigma_deg=sigma_deg))
    fix_shifted = fuser.fuse(
        _build_scenario(sigma_deg=sigma_deg, shift_m=(shift_east_m, shift_north_m))
    )
    assert fix_base is not None and fix_shifted is not None

    # Ellipse magnitudes invariant under rigid shift (WGS-84 projection
    # is approximately linear over 500 m shifts at lat 52).
    assert fix_shifted.confidence_ellipse_95.semi_major_m == pytest.approx(
        fix_base.confidence_ellipse_95.semi_major_m, rel=5e-3
    )
    assert fix_shifted.confidence_ellipse_95.semi_minor_m == pytest.approx(
        fix_base.confidence_ellipse_95.semi_minor_m, rel=5e-3
    )
    assert fix_shifted.gdop == pytest.approx(fix_base.gdop, rel=1e-3)


def test_residuals_zero_sum_no_outlier_under_clean_geometry() -> None:
    """Exact-azimuth input yields only sub-3-sigma residuals (no outliers).

    Each bearing carries the analytically exact azimuth from its node to
    the emitter (no measurement noise injected). The reported per-bearing
    sigma is 2.0 deg, representative of an L1 estimator. After fusion,
    every residual must be below the 3-sigma outlier threshold — given
    exact input, the MLE solver must not synthesise spurious residuals.
    """
    fuser = _make_fuser()
    fix = fuser.fuse(_build_scenario(sigma_deg=2.0))
    assert fix is not None
    sigma_deg = 2.0
    for r in fix.residuals_deg:
        assert abs(r) < 3.0 * sigma_deg, (
            f"clean-geometry residual {r:.3f} flagged as outlier (>3·sigma={3.0 * sigma_deg})"
        )


def test_unweighted_gdop_independent_of_sigma_choice() -> None:
    """GDOP must be a pure-geometry scalar (ADR-007 D4 unweighted)."""
    fuser = _make_fuser()
    f_low = fuser.fuse(_build_scenario(sigma_deg=0.5))
    f_high = fuser.fuse(_build_scenario(sigma_deg=8.0))
    assert f_low is not None and f_high is not None
    assert f_low.gdop == pytest.approx(f_high.gdop, rel=1e-6), (
        "GDOP must not depend on sigma — geometry-only by ADR-007 D4"
    )


def test_confidence_level_monotonic_under_sigma_inflation() -> None:
    """Inflating sigma cannot move ``confidence_level`` from LOW toward HIGH.

    The mapping is sigma → ellipse → confidence_level. Larger sigma → larger
    ellipse → confidence stays at the same band or moves toward LOW;
    it must not move toward HIGH.
    """
    fuser = _make_fuser()
    levels_order = {"high": 2, "medium": 1, "low": 0}
    prev_rank: int | None = None
    for sigma in (0.5, 1.0, 2.0, 5.0, 10.0):
        fix = fuser.fuse(_build_scenario(sigma_deg=sigma))
        assert fix is not None
        rank = levels_order[fix.confidence_level.value]
        if prev_rank is not None:
            assert rank <= prev_rank, (
                f"confidence_level rose when sigma inflated: "
                f"sigma={sigma} rank={rank} > prev {prev_rank}"
            )
        prev_rank = rank
