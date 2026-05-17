"""Tests for ``StansfieldMLEFuser`` -- the ``Fuser`` Protocol wiring.

This is the orchestration ticket; the upstream modules (stansfield,
mle, covariance, gdop, residuals, confidence) each have their own
test suites. The tests here pin the end-to-end behaviour that emerges
from wiring them together:

* Protocol conformance (runtime ``isinstance(fuser, Fuser)`` works).
* The min-bearings refusal and the fallback-centroid LOW labelling.
* The trench-demo geometry produces MEDIUM (ADR-009 narrative).
* A synthetic dense mesh produces HIGH when geometry permits.
* The outlier-flag downgrade (HIGH -> MEDIUM, not LOW).
* MLE convergence failure falls back to Stansfield, not silently.
* Emitter-class consensus rules.
* The ``contributing_nodes`` <-> ``residuals_deg`` ordering invariant
  per INTERFACES.md §3.
* ``fix_id`` is a fresh UUID per call.
* ``t_unix_ns`` is integer arithmetic.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from rfmesh_contracts.config import FusionConfig  # type: ignore[import-untyped, unused-ignore]
from rfmesh_contracts.enums import (  # type: ignore[import-untyped, unused-ignore]
    Capability,
    ConfidenceLevel,
    EmitterClass,
)
from rfmesh_contracts.geospatial import (  # type: ignore[import-untyped, unused-ignore]
    GeodeticPosition,
)
from rfmesh_contracts.messages import (  # type: ignore[import-untyped, unused-ignore]
    BearingReport,
    FixEvent,
)
from rfmesh_contracts.protocols import Fuser  # type: ignore[import-untyped, unused-ignore]
from rfmesh_fusion.exceptions import MLEConvergenceError
from rfmesh_fusion.fuser import StansfieldMLEFuser
from rfmesh_fusion.projection import from_enu

# ---------------------------------------------------------------------------
# Shared fixtures and helpers.
# ---------------------------------------------------------------------------

# A plausible UTC ns timestamp. ``BearingReport.t_unix_ns`` requires
# ``> 0``; this constant lets every test use the same value when
# ordering is not the subject under test.
_T0_NS: int = 1_778_976_000_000_000_000

# Default config -- all defaults from the contract except listen_url
# which has no default.
_DEFAULT_FUSION_URL: str = "udp://0.0.0.0:9000"

# Trench-demo Beat D CRLB-predicted numbers (from
# docs/demo/crlb_analysis.py output, also documented in
# docs/demo/trench-demo-geometry.md §2.2). Each bound is generous
# (~5%) because the CRLB script and the implementation share the
# Jacobian + chi-square scaling, so agreement is to within float64
# round-off; the band is for forward compatibility, not flakiness.
_BEAT_D_SEMI_MAJOR_MIN_M: float = 350.0
_BEAT_D_SEMI_MAJOR_MAX_M: float = 370.0
_BEAT_D_SEMI_MINOR_MIN_M: float = 120.0
_BEAT_D_SEMI_MINOR_MAX_M: float = 135.0
_BEAT_D_GDOP_MIN: float = 0.95
_BEAT_D_GDOP_MAX: float = 1.10

# Outlier-test thresholds. The first node's residual-over-sigma ratio
# must exceed 3.0 (the ADR-005 §D3 outlier multiplier) for the
# is_outlier flag to fire on it. Pin both -- the threshold ratio AND
# the actual sigma used in the dense-mesh geometry helper.
_ADR_OUTLIER_RATIO: float = 3.0
_HIGH_BAND_GEOMETRY_SIGMA_DEG: float = 0.5

# Number of bearings the time-window test should retain after filtering.
_TIME_WINDOW_TEST_RETAINED: int = 3


@pytest.fixture
def default_config() -> FusionConfig:
    """A ``FusionConfig`` with contract defaults (min_bearings=2, gdop_warn=6.0)."""
    return FusionConfig(listen_url=_DEFAULT_FUSION_URL)  # type: ignore[arg-type]


@pytest.fixture
def fuser(default_config: FusionConfig) -> StansfieldMLEFuser:
    """A fresh ``StansfieldMLEFuser`` constructed with the default config."""
    return StansfieldMLEFuser(default_config)


@pytest.fixture
def origin_geo() -> GeodeticPosition:
    """ENU origin for the trench-demo geometry.

    Matches ``docs/demo/trench-demo-geometry.md`` §1 (Marche-les-Dames-
    area placeholder). The CRLB math is origin-independent; the demo
    document is the canonical reference for the absolute lat/lon.
    """
    return GeodeticPosition(lat_deg=50.330, lon_deg=5.0, hae_m=200.0, sigma_m=0.0)


def _bearing_from_enu_truth(
    *,
    node_enu: tuple[float, float],
    emitter_enu: tuple[float, float],
    sigma_deg: float,
    node_id: str,
    method: Capability,
    origin_geo: GeodeticPosition,
    t_unix_ns: int = _T0_NS,
    azimuth_offset_deg: float = 0.0,
    emitter_class: EmitterClass | None = None,
    classification_confidence: float | None = None,
) -> BearingReport:
    """Construct a ``BearingReport`` whose azimuth points from node to emitter.

    ``azimuth_offset_deg`` injects a known disagreement; with the
    default ``0.0`` the bearing is analytically consistent with the
    emitter at ``emitter_enu``.
    """
    de = emitter_enu[0] - node_enu[0]
    dn = emitter_enu[1] - node_enu[1]
    truth_az_deg = math.degrees(math.atan2(de, dn)) % 360.0
    az_deg = (truth_az_deg + azimuth_offset_deg) % 360.0
    node_geo = from_enu(
        node_enu[0],
        node_enu[1],
        origin=origin_geo,
        sigma_m=5.0,
    )
    return BearingReport(
        node_id=node_id,
        t_unix_ns=t_unix_ns,
        node_position=node_geo,
        azimuth_deg=az_deg,
        azimuth_sigma_deg=sigma_deg,
        method=method,
        emitter_class=emitter_class,
        classification_confidence=classification_confidence,
    )


def _trench_demo_beat_d_bearings(origin_geo: GeodeticPosition) -> list[BearingReport]:
    """The trench-demo Beat D geometry: 3 L1 nodes + 1 L2 node.

    Per ``docs/demo/trench-demo-geometry.md`` §1:
    * Node A = (-1800, +1800), L1, sigma 5 deg
    * Node B = (+1800, +1800), L1, sigma 5 deg
    * Node C = (0,     +900),  L1, sigma 5 deg
    * Node D = (+2000, +3500), L2, sigma 1.5 deg
    Emitter at (0, +3000). All bearings point at the truth (no noise).
    """
    emitter = (0.0, 3000.0)
    spec = [
        ((-1800.0, 1800.0), 5.0, "A", Capability.L1_RSSI),
        ((1800.0, 1800.0), 5.0, "B", Capability.L1_RSSI),
        ((0.0, 900.0), 5.0, "C", Capability.L1_RSSI),
        ((2000.0, 3500.0), 1.5, "D", Capability.L2_MUSIC),
    ]
    return [
        _bearing_from_enu_truth(
            node_enu=p,
            emitter_enu=emitter,
            sigma_deg=s,
            node_id=nid,
            method=m,
            origin_geo=origin_geo,
        )
        for (p, s, nid, m) in spec
    ]


def _high_band_geometry(
    origin_geo: GeodeticPosition,
    *,
    azimuth_offset_for_first: float = 0.0,
) -> list[BearingReport]:
    """A 6-node L2-class mesh tight enough to reach the HIGH band.

    Six L2-class nodes (sigma 0.5 deg, KrakenSDR-class precision) on
    a 1500-m-radius ring centred at ``(0, -500)`` ENU, with the
    emitter at ``(0, 0)``. Centroid-to-emitter distance = 500 m;
    CRLB semi-major ~18 m, ratio ~3.6 %. Geometry is intentionally
    favourable -- the test demonstrates that HIGH is reachable when
    deployment density and per-bearing precision both cooperate (the
    pitch in ADR-009 §"Positive" consequences). At trench-demo sigma=5°
    L1 / sigma=1.5° L2, HIGH is *not* reachable with 4 nodes; ADR-009
    pins that. This helper deliberately uses a *denser* and *more
    precise* mesh than the demo to confirm the HIGH band exists at
    all in the implementation.

    ``azimuth_offset_for_first`` injects a known disagreement on the
    first bearing -- the outlier-downgrade test uses this to force
    ``is_outlier_any = True`` while keeping the geometry-derived HIGH
    gates passing.
    """
    emitter = (0.0, 0.0)
    ring_center = (0.0, -500.0)
    radius_m = 1500.0
    n_nodes = 6
    bearings: list[BearingReport] = []
    for i in range(n_nodes):
        angle_rad = 2.0 * math.pi * i / n_nodes
        node_xy = (
            ring_center[0] + radius_m * math.cos(angle_rad),
            ring_center[1] + radius_m * math.sin(angle_rad),
        )
        offset = azimuth_offset_for_first if i == 0 else 0.0
        bearings.append(
            _bearing_from_enu_truth(
                node_enu=node_xy,
                emitter_enu=emitter,
                sigma_deg=0.5,
                node_id=f"N{i}",
                method=Capability.L2_MUSIC,
                origin_geo=origin_geo,
                azimuth_offset_deg=offset,
            )
        )
    return bearings


# ---------------------------------------------------------------------------
# 1. Protocol conformance.
# ---------------------------------------------------------------------------


def test_fuser_satisfies_protocol(fuser: StansfieldMLEFuser) -> None:
    """``isinstance(fuser, Fuser)`` is True (the Protocol is runtime_checkable).

    Cheapest end-to-end conformance gate: if a required method is
    missing or renamed, this assertion fails. Do *not* paper over a
    failure by removing the ``@runtime_checkable`` decoration on
    ``Fuser`` -- that would be a contract change (Invariant B1).
    """
    assert isinstance(fuser, Fuser)


# ---------------------------------------------------------------------------
# 2. Refusal cases -- the Protocol's "cannot solve" signal.
# ---------------------------------------------------------------------------


def test_fuser_below_min_bearings_returns_none(
    fuser: StansfieldMLEFuser,
    default_config: FusionConfig,
    origin_geo: GeodeticPosition,
) -> None:
    """Single-bearing input returns ``None`` -- ``min_bearings_for_fix = 2`` by default."""
    single = [
        _bearing_from_enu_truth(
            node_enu=(0.0, 0.0),
            emitter_enu=(100.0, 100.0),
            sigma_deg=5.0,
            node_id="lonely",
            method=Capability.L1_RSSI,
            origin_geo=origin_geo,
        )
    ]
    result = fuser.fuse(single, default_config)
    assert result is None


def test_fuser_empty_input_returns_none(
    fuser: StansfieldMLEFuser,
    default_config: FusionConfig,
) -> None:
    """Empty iterable returns ``None``."""
    assert fuser.fuse([], default_config) is None


def test_fuser_accepts_arbitrary_iterable(
    fuser: StansfieldMLEFuser,
    default_config: FusionConfig,
    origin_geo: GeodeticPosition,
) -> None:
    """``fuse`` works with a generator (any ``Iterable[BearingReport]``)."""

    def _gen() -> Iterator[BearingReport]:
        yield from _trench_demo_beat_d_bearings(origin_geo)

    result = fuser.fuse(_gen(), default_config)
    assert result is not None
    assert isinstance(result, FixEvent)


# ---------------------------------------------------------------------------
# 3. The load-bearing trench-demo Beat D test -- ADR-009 narrative.
# ---------------------------------------------------------------------------


def test_fuser_trench_demo_beat_d_high_band_NOT_reached(  # noqa: N802 -- emphatic NOT
    fuser: StansfieldMLEFuser,
    default_config: FusionConfig,
    origin_geo: GeodeticPosition,
) -> None:
    """At Beat D of the trench-demo geometry, confidence is MEDIUM (not HIGH).

    This pins ADR-009 at the implementation level. The CRLB analysis
    in ``docs/demo/crlb_analysis.py`` gives semi-major ~359 m at
    range ~1118 m (centroid-to-emitter); ratio ~32 % >> 5 %. With
    perfect bearings (no outliers) the HIGH gate fails on the
    ellipse-vs-range ratio alone, so the band is MEDIUM.

    If this test fails with ``HIGH``, the confidence policy is wrong
    OR the CRLB numbers in the demo document are wrong (re-run
    ``uv run python docs/demo/crlb_analysis.py``; if numbers diverge,
    write a scratchpad and stop -- ADR-009 is the binding narrative).
    """
    bearings = _trench_demo_beat_d_bearings(origin_geo)
    fix = fuser.fuse(bearings, default_config)
    assert fix is not None
    assert fix.confidence_level == ConfidenceLevel.MEDIUM, (
        f"Beat D should produce MEDIUM per ADR-009 (the demo lives in "
        f"MEDIUM throughout the four beats). Got {fix.confidence_level} "
        f"with semi_major={fix.confidence_ellipse_95.semi_major_m:.1f} m, "
        f"GDOP={fix.gdop:.3f}, method={fix.method!r}. Re-run "
        f"docs/demo/crlb_analysis.py and check whether the numbers "
        f"agree -- if they diverge, the policy is wrong; if they agree, "
        f"the ADR-009 narrative is broken."
    )
    # The CRLB-predicted numbers per docs/demo/trench-demo-geometry.md
    # §2.2 Beat D: semi_major=359, semi_minor=125, GDOP=1.02.
    # Tolerances generous -- the CRLB script and the implementation
    # share the same Jacobian + chi-square scaling, so agreement is
    # to within float64 round-off in practice.
    assert fix.confidence_ellipse_95.semi_major_m > _BEAT_D_SEMI_MAJOR_MIN_M
    assert fix.confidence_ellipse_95.semi_major_m < _BEAT_D_SEMI_MAJOR_MAX_M
    assert fix.confidence_ellipse_95.semi_minor_m > _BEAT_D_SEMI_MINOR_MIN_M
    assert fix.confidence_ellipse_95.semi_minor_m < _BEAT_D_SEMI_MINOR_MAX_M
    assert _BEAT_D_GDOP_MIN < fix.gdop < _BEAT_D_GDOP_MAX
    assert fix.method == "stansfield+mle"


# ---------------------------------------------------------------------------
# 4. HIGH is reachable when geometry permits.
# ---------------------------------------------------------------------------


def test_fuser_synthetic_perfect_high_band(
    fuser: StansfieldMLEFuser,
    default_config: FusionConfig,
    origin_geo: GeodeticPosition,
) -> None:
    """Dense 6-node L2 mesh at sigma 0.5 deg reaches HIGH on perfect bearings.

    Counterexample to "the demo cannot reach HIGH so the policy is
    miscalibrated" -- HIGH is reachable, just not at the trench-demo's
    sparser geometry. The dense-mesh story ADR-009 §"Positive"
    consequences calls out is what this test demonstrates: scale via
    deployment density + L2-class precision and the band transition
    becomes a real, observable event.
    """
    bearings = _high_band_geometry(origin_geo)
    fix = fuser.fuse(bearings, default_config)
    assert fix is not None
    semi_over_range = fix.confidence_ellipse_95.semi_major_m / 500.0
    assert fix.confidence_level == ConfidenceLevel.HIGH, (
        f"Dense L2 mesh should reach HIGH. Got {fix.confidence_level} "
        f"with semi_major={fix.confidence_ellipse_95.semi_major_m:.1f} m, "
        f"GDOP={fix.gdop:.3f}, semi/range={semi_over_range:.4f}."
    )


# ---------------------------------------------------------------------------
# 5. The ADR-005 §D3 outlier downgrade -- HIGH -> MEDIUM, not LOW.
# ---------------------------------------------------------------------------


def test_fuser_outlier_downgrades_high_to_medium(
    fuser: StansfieldMLEFuser,
    default_config: FusionConfig,
    origin_geo: GeodeticPosition,
) -> None:
    """Inject a 10-sigma bearing offset on a HIGH-reachable geometry -> MEDIUM.

    Pins ADR-005 §D3: the outlier downgrade is HIGH->MEDIUM, never
    HIGH->LOW. The geometry is the same dense L2 mesh as the HIGH
    test above; the only difference is one bearing's azimuth offset.

    A 10-sigma offset (with sigma=0.5°, that is 5° of bearing
    disagreement) is large enough that the MLE cannot smooth it
    across the other 5 bearings -- the post-fit residual on the
    offending bearing stays well above 3-sigma, so ``is_outlier``
    fires. Smaller offsets would let MLE absorb the disagreement
    into the other bearings' residuals, defeating the test.
    """
    # 10 * sigma_deg(0.5) = 5.0 degrees of azimuth disagreement on
    # node N0. With the other 5 nodes pointing at truth, MLE cannot
    # smooth this away.
    bearings = _high_band_geometry(origin_geo, azimuth_offset_for_first=5.0)
    fix = fuser.fuse(bearings, default_config)
    assert fix is not None
    # Compute the per-bearing |residual| / sigma. The offending
    # bearing should be the outlier (the others were truth-points).
    residual_ratio_n0 = abs(fix.residuals_deg[0]) / _HIGH_BAND_GEOMETRY_SIGMA_DEG
    assert residual_ratio_n0 > _ADR_OUTLIER_RATIO, (
        f"Outlier-injection test expected residual_ratio_n0 > "
        f"{_ADR_OUTLIER_RATIO} to fire the is_outlier flag. Got "
        f"residuals_deg={fix.residuals_deg}, ratio_n0={residual_ratio_n0:.2f}. "
        f"If MLE smoothed the disagreement across nodes, increase "
        f"azimuth_offset_for_first."
    )
    # ADR-005 §D3: the downgrade is HIGH -> MEDIUM, NOT to LOW.
    assert fix.confidence_level == ConfidenceLevel.MEDIUM, (
        f"Outlier downgrade should be MEDIUM (ADR-005 §D3). Got "
        f"{fix.confidence_level}. residuals_deg={fix.residuals_deg}, "
        f"semi_major={fix.confidence_ellipse_95.semi_major_m:.1f}, "
        f"GDOP={fix.gdop:.3f}."
    )


# ---------------------------------------------------------------------------
# 6. Fallback-centroid path -- ADR-007 D3.
# ---------------------------------------------------------------------------


def test_fuser_fallback_centroid_marks_low_method(
    fuser: StansfieldMLEFuser,
    default_config: FusionConfig,
    origin_geo: GeodeticPosition,
) -> None:
    """Coincident-nodes geometry -> ``DegenerateGeometryError`` -> fallback path.

    All three nodes at the same ENU position triggers Stansfield's
    ``_COINCIDENT_NODES_THRESHOLD_M`` check. The Fuser should catch
    the ``DegenerateGeometryError`` and route to the fallback-centroid
    path: method="fallback_centroid", confidence=LOW.
    """
    common_xy = (0.0, 0.0)
    emitter = (1000.0, 1000.0)
    # Three bearings from coincident nodes -- different azimuths so
    # the ray-ray crossings are well-defined.
    bearings = [
        _bearing_from_enu_truth(
            node_enu=common_xy,
            emitter_enu=emitter,
            sigma_deg=5.0,
            node_id="A",
            method=Capability.L1_RSSI,
            origin_geo=origin_geo,
        ),
        # Inject artificial azimuth disagreement so the ray-ray
        # crossings exist (otherwise all three bearings point the
        # same direction from the same point and there are no
        # pairwise crossings).
        _bearing_from_enu_truth(
            node_enu=common_xy,
            emitter_enu=emitter,
            sigma_deg=5.0,
            node_id="B",
            method=Capability.L1_RSSI,
            origin_geo=origin_geo,
            azimuth_offset_deg=30.0,
        ),
        _bearing_from_enu_truth(
            node_enu=common_xy,
            emitter_enu=emitter,
            sigma_deg=5.0,
            node_id="C",
            method=Capability.L1_RSSI,
            origin_geo=origin_geo,
            azimuth_offset_deg=-30.0,
        ),
    ]

    fix = fuser.fuse(bearings, default_config)
    # The coincident-nodes geometry should trigger the fallback path.
    # If somehow the fallback also fails (no ray-ray crossings), the
    # fuser returns None per the Protocol contract -- but this geometry
    # should produce crossings, so we expect a FixEvent.
    assert fix is not None, "Fallback path should produce a FixEvent on this geometry"
    assert fix.method == "fallback_centroid"
    assert fix.confidence_level == ConfidenceLevel.LOW


# ---------------------------------------------------------------------------
# 7. MLE convergence failure -- patch path to force the failure.
# ---------------------------------------------------------------------------


def test_fuser_mle_convergence_failure_falls_back_to_stansfield(
    fuser: StansfieldMLEFuser,
    default_config: FusionConfig,
    origin_geo: GeodeticPosition,
) -> None:
    """Patched ``solve_mle`` raises -> method="stansfield" (not "stansfield+mle").

    Uses ``unittest.mock.patch`` because constructing an organic
    geometry that defeats Gauss-Newton with analytic Jacobian is hard
    and not the subject of this test -- the subject is the
    orchestrator's fallback behaviour.
    """
    bearings = _trench_demo_beat_d_bearings(origin_geo)

    with patch("rfmesh_fusion.fuser.solve_mle") as mock_mle:
        mock_mle.side_effect = MLEConvergenceError("simulated divergence")
        fix = fuser.fuse(bearings, default_config)

    assert fix is not None
    assert fix.method == "stansfield", (
        f"MLE divergence should fall back to Stansfield seed. Got method={fix.method!r}."
    )
    # The covariance / ellipse should still be honest -- computed
    # from the Stansfield seed position, not a placeholder.
    assert fix.confidence_ellipse_95.semi_major_m > 0.0
    assert fix.confidence_ellipse_95.semi_minor_m > 0.0


# ---------------------------------------------------------------------------
# 8. Consensus emitter_class rules.
# ---------------------------------------------------------------------------


def test_fuser_consensus_emitter_class_all_none(
    fuser: StansfieldMLEFuser,
    default_config: FusionConfig,
    origin_geo: GeodeticPosition,
) -> None:
    """No node classified -> ``FixEvent.emitter_class is None`` (no consensus to take)."""
    bearings = _trench_demo_beat_d_bearings(origin_geo)
    # None of the helper-built bearings carry emitter_class, but be
    # explicit:
    for b in bearings:
        assert b.emitter_class is None
    fix = fuser.fuse(bearings, default_config)
    assert fix is not None
    assert fix.emitter_class is None


def test_fuser_consensus_emitter_class_majority(
    fuser: StansfieldMLEFuser,
    default_config: FusionConfig,
    origin_geo: GeodeticPosition,
) -> None:
    """2 ELRS + 1 CROSSFIRE -> consensus ELRS (majority rule)."""
    emitter = (0.0, 3000.0)
    spec = [
        ((-1800.0, 1800.0), "A", EmitterClass.ELRS, 0.9),
        ((1800.0, 1800.0), "B", EmitterClass.ELRS, 0.85),
        ((0.0, 900.0), "C", EmitterClass.CROSSFIRE, 0.7),
    ]
    bearings = [
        _bearing_from_enu_truth(
            node_enu=p,
            emitter_enu=emitter,
            sigma_deg=5.0,
            node_id=nid,
            method=Capability.L1_RSSI,
            origin_geo=origin_geo,
            emitter_class=cls,
            classification_confidence=conf,
        )
        for (p, nid, cls, conf) in spec
    ]
    fix = fuser.fuse(bearings, default_config)
    assert fix is not None
    assert fix.emitter_class == EmitterClass.ELRS


def test_fuser_consensus_emitter_class_three_way_split(
    fuser: StansfieldMLEFuser,
    default_config: FusionConfig,
    origin_geo: GeodeticPosition,
) -> None:
    """1 ELRS + 1 CROSSFIRE + 1 DRONEID -> ``UNKNOWN`` (no strict majority)."""
    emitter = (0.0, 3000.0)
    spec = [
        ((-1800.0, 1800.0), "A", EmitterClass.ELRS, 0.6),
        ((1800.0, 1800.0), "B", EmitterClass.CROSSFIRE, 0.6),
        ((0.0, 900.0), "C", EmitterClass.DRONEID, 0.6),
    ]
    bearings = [
        _bearing_from_enu_truth(
            node_enu=p,
            emitter_enu=emitter,
            sigma_deg=5.0,
            node_id=nid,
            method=Capability.L1_RSSI,
            origin_geo=origin_geo,
            emitter_class=cls,
            classification_confidence=conf,
        )
        for (p, nid, cls, conf) in spec
    ]
    fix = fuser.fuse(bearings, default_config)
    assert fix is not None
    assert fix.emitter_class == EmitterClass.UNKNOWN


def test_fuser_consensus_emitter_class_partial_classification(
    fuser: StansfieldMLEFuser,
    default_config: FusionConfig,
    origin_geo: GeodeticPosition,
) -> None:
    """Only some nodes classified -> consensus across the classifying subset.

    The fuser ignores ``None`` ``emitter_class`` values (those nodes
    did not classify). The consensus is taken over the classifying
    subset only.
    """
    emitter = (0.0, 3000.0)
    bearings = [
        _bearing_from_enu_truth(
            node_enu=(-1800.0, 1800.0),
            emitter_enu=emitter,
            sigma_deg=5.0,
            node_id="A",
            method=Capability.L1_RSSI,
            origin_geo=origin_geo,
            emitter_class=EmitterClass.ELRS,
            classification_confidence=0.9,
        ),
        _bearing_from_enu_truth(
            node_enu=(1800.0, 1800.0),
            emitter_enu=emitter,
            sigma_deg=5.0,
            node_id="B",
            method=Capability.L1_RSSI,
            origin_geo=origin_geo,
            # B did not classify.
        ),
        _bearing_from_enu_truth(
            node_enu=(0.0, 900.0),
            emitter_enu=emitter,
            sigma_deg=5.0,
            node_id="C",
            method=Capability.L1_RSSI,
            origin_geo=origin_geo,
            emitter_class=EmitterClass.ELRS,
            classification_confidence=0.85,
        ),
    ]
    fix = fuser.fuse(bearings, default_config)
    assert fix is not None
    # 2 of 2 classifiers said ELRS -> strict majority.
    assert fix.emitter_class == EmitterClass.ELRS


# ---------------------------------------------------------------------------
# 9. Ordering invariant per INTERFACES.md §3.
# ---------------------------------------------------------------------------


def test_fuser_contributing_nodes_order_matches_residuals(
    fuser: StansfieldMLEFuser,
    default_config: FusionConfig,
    origin_geo: GeodeticPosition,
) -> None:
    """``FixEvent.contributing_nodes[i]`` corresponds to ``residuals_deg[i]``.

    The ordering invariant is load-bearing for downstream consumers
    (dashboard, CoT). The fuser preserves input order end-to-end via
    ``residuals.compute_residuals.contributing_node_ids``.
    """
    bearings = _trench_demo_beat_d_bearings(origin_geo)
    fix = fuser.fuse(bearings, default_config)
    assert fix is not None
    # Same length.
    assert len(fix.contributing_nodes) == len(fix.residuals_deg)
    assert len(fix.contributing_nodes) == len(bearings)
    # Same order as input (the helper's spec was A, B, C, D).
    assert fix.contributing_nodes == ("A", "B", "C", "D")


# ---------------------------------------------------------------------------
# 10. fix_id is a fresh UUID per call.
# ---------------------------------------------------------------------------


def test_fuser_fix_id_unique(
    fuser: StansfieldMLEFuser,
    default_config: FusionConfig,
    origin_geo: GeodeticPosition,
) -> None:
    """Calling ``fuse()`` twice with the same inputs returns different ``fix_id``."""
    bearings = _trench_demo_beat_d_bearings(origin_geo)
    fix1 = fuser.fuse(bearings, default_config)
    fix2 = fuser.fuse(bearings, default_config)
    assert fix1 is not None
    assert fix2 is not None
    assert fix1.fix_id != fix2.fix_id


# ---------------------------------------------------------------------------
# 11. t_unix_ns is the midpoint, integer-only arithmetic.
# ---------------------------------------------------------------------------


def test_fuser_t_unix_ns_is_midpoint_integer(
    fuser: StansfieldMLEFuser,
    default_config: FusionConfig,
    origin_geo: GeodeticPosition,
) -> None:
    """3 bearings at t=100, 200, 300 ns -> ``t_unix_ns == 200``.

    Pins integer-only arithmetic end-to-end (no float64 conversion
    that would silently round in the high-magnitude regime that real
    timestamps live in). The contract validator requires
    ``t_unix_ns > 0``, so the test inputs are >0 too.
    """
    # Realistic ns-magnitude timestamps with 100ns spacing -- so the
    # batch_window_ms filter (default 100ms = 100_000_000 ns) keeps
    # all three.
    t_base = _T0_NS
    timestamps = [t_base + 100, t_base + 200, t_base + 300]
    expected_midpoint = (timestamps[0] + timestamps[1] + timestamps[2]) // 3
    emitter = (0.0, 3000.0)
    spec = [
        ((-1800.0, 1800.0), "A", timestamps[0]),
        ((1800.0, 1800.0), "B", timestamps[1]),
        ((0.0, 900.0), "C", timestamps[2]),
    ]
    bearings = [
        _bearing_from_enu_truth(
            node_enu=p,
            emitter_enu=emitter,
            sigma_deg=5.0,
            node_id=nid,
            method=Capability.L1_RSSI,
            origin_geo=origin_geo,
            t_unix_ns=ts,
        )
        for (p, nid, ts) in spec
    ]
    fix = fuser.fuse(bearings, default_config)
    assert fix is not None
    assert isinstance(fix.t_unix_ns, int)
    assert fix.t_unix_ns == expected_midpoint


# ---------------------------------------------------------------------------
# 12. Per-call config override.
# ---------------------------------------------------------------------------


def test_fuser_per_call_config_overrides_constructor_default(
    origin_geo: GeodeticPosition,
) -> None:
    """A per-call config with min_bearings=3 rejects a 2-bearing input.

    Demonstrates that the constructor's default config does not
    "stick" -- callers may pass a different config per call. Two
    different configs, one fuser instance, two different outcomes.
    """
    default_cfg = FusionConfig(listen_url=_DEFAULT_FUSION_URL)  # type: ignore[arg-type]
    strict_cfg = FusionConfig(  # type: ignore[arg-type]
        listen_url=_DEFAULT_FUSION_URL,
        min_bearings_for_fix=3,
    )
    fuser_instance = StansfieldMLEFuser(default_cfg)

    emitter = (1000.0, 1000.0)
    two_bearings = [
        _bearing_from_enu_truth(
            node_enu=(-1000.0, 0.0),
            emitter_enu=emitter,
            sigma_deg=5.0,
            node_id="A",
            method=Capability.L1_RSSI,
            origin_geo=origin_geo,
        ),
        _bearing_from_enu_truth(
            node_enu=(1000.0, 0.0),
            emitter_enu=emitter,
            sigma_deg=5.0,
            node_id="B",
            method=Capability.L1_RSSI,
            origin_geo=origin_geo,
        ),
    ]
    # Default config: 2 bearings is enough.
    assert fuser_instance.fuse(two_bearings, default_cfg) is not None
    # Strict config: 2 bearings is not enough.
    assert fuser_instance.fuse(two_bearings, strict_cfg) is None


# ---------------------------------------------------------------------------
# 13. Time-window filtering drops stale bearings.
# ---------------------------------------------------------------------------


def test_fuser_drops_bearings_outside_batch_window(
    fuser: StansfieldMLEFuser,
    default_config: FusionConfig,
    origin_geo: GeodeticPosition,
) -> None:
    """A bearing far outside ``batch_window_ms`` is dropped before solve.

    Default ``batch_window_ms = 100 ms = 1e8 ns``. A single stale
    bearing at t = median + 10 s is well outside the half-window
    (50 ms) and is dropped. The remaining 3 bearings still produce
    a valid fix.
    """
    base_ns = _T0_NS
    stale_ns = base_ns + 10 * 1_000_000_000  # +10 s
    emitter = (0.0, 3000.0)
    spec = [
        ((-1800.0, 1800.0), "A", base_ns),
        ((1800.0, 1800.0), "B", base_ns + 1_000_000),  # +1 ms
        ((0.0, 900.0), "C", base_ns + 2_000_000),  # +2 ms
        ((2000.0, 3500.0), "D_stale", stale_ns),  # +10 s -- dropped
    ]
    bearings = [
        _bearing_from_enu_truth(
            node_enu=p,
            emitter_enu=emitter,
            sigma_deg=5.0,
            node_id=nid,
            method=Capability.L1_RSSI,
            origin_geo=origin_geo,
            t_unix_ns=ts,
        )
        for (p, nid, ts) in spec
    ]
    fix = fuser.fuse(bearings, default_config)
    assert fix is not None
    # D_stale was filtered out -- contributing_nodes has only A, B, C.
    assert set(fix.contributing_nodes) == {"A", "B", "C"}
    assert len(fix.contributing_nodes) == _TIME_WINDOW_TEST_RETAINED
