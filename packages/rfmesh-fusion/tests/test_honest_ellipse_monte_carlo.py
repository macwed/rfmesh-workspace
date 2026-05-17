"""Honest-ellipse Monte Carlo -- the sprint-1 demo-honesty acceptance gate.

This is the load-bearing test. The whole pipeline (L1 / L2 sigma honesty
-> Stansfield seed -> MLE refinement -> Fisher-info covariance -> 95%
chi-square ellipse -> ConfidenceLevel band) is trustworthy iff the
``FixEvent.confidence_ellipse_95`` it produces really is the 95%
confidence contour -- i.e. the true emitter falls inside it in 95 +/- 3
% of Monte Carlo trials.

For each scenario:

1. Pin a ground-truth emitter ENU position and N node ENU positions.
2. Convert node ENUs to geodetic at a known origin (so the fuser can
   project them back through ``choose_enu_origin``).
3. For each of ``N_TRIALS = 1000`` trials:
   - For each node, compute the true azimuth to the emitter.
   - Draw Gaussian noise ``~N(0, sigma_i)`` in degrees, declare the
     same sigma on the ``BearingReport`` (honest sigma).
   - Build N ``BearingReport`` s, call ``StansfieldMLEFuser.fuse``.
   - Project the fix back into the trial's ENU frame; rotate the
     offset (true_emitter - fix_position) into the ellipse-local
     frame; test ``(u/semi_major)^2 + (v/semi_minor)^2 <= 1``.
4. Compute inclusion rate, mean position bias, empirical 2x2 covariance,
   and ConfidenceLevel band counts across trials.

Pass criteria (all per-scenario):

* Inclusion rate in ``[0.92, 0.98]`` (3 % statistical tolerance around
  the declared 95 % level).
* Mean position bias <= ``0.5 * semi_minor_m`` (claimed first-trial
  value) -- MLE-converged positions are unbiased to below ellipse
  resolution.
* Empirical-vs-claimed covariance Frobenius ratio in ``[1/1.5, 1.5]``
  -- the full covariance is honest, not just its contour.
* Band distribution matches ADR-009 narrative (MEDIUM-dominant for
  trench-demo Beat C/D; HIGH-dominant for the high-SNR mesh).

If any of those fail, the gate fails. **Do not widen the band** --
diagnose per the WS-CD-008 ticket's "Stop conditions".

References
----------
* ``docs/tickets/WS-CD-008-honest-ellipse-monte-carlo.md`` -- the
  ticket this test satisfies.
* ``docs/adr/ADR-005-fusion-confidence-policy.md`` Validation gates --
  this IS the gate the ADR anticipates.
* ``docs/adr/ADR-009-confidence-band-math-correction-and-demo-narrative.md``
  -- the 1-sigma vs 95% scale correction; the test makes the wrong
  scale impossible to slip through silently.
* ``docs/demo/crlb_analysis.py`` -- the analytic reference the empirical
  covariance is cross-validated against.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

import numpy as np
import pytest
from rfmesh_contracts.config import FusionConfig  # type: ignore[import-untyped, unused-ignore]
from rfmesh_contracts.enums import (  # type: ignore[import-untyped, unused-ignore]
    Capability,
    ConfidenceLevel,
)
from rfmesh_contracts.geospatial import (  # type: ignore[import-untyped, unused-ignore]
    GeodeticPosition,
)
from rfmesh_contracts.messages import (  # type: ignore[import-untyped, unused-ignore]
    BearingReport,
)
from rfmesh_fusion.fuser import StansfieldMLEFuser
from rfmesh_fusion.projection import from_enu, to_enu

# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

# Canonical RNG seed. Documented for reproducibility -- every Monte Carlo
# scenario uses ``np.random.default_rng(seed=_SEED)`` so a re-run
# produces byte-identical pass/fail outcomes.
_SEED: Final[int] = 42

# Number of trials per scenario. 1000 trials gives a Monte Carlo
# standard error on the inclusion rate of ``sqrt(0.95 * 0.05 / 1000)``
# ~ 0.0069 -- well inside the 0.03 pass band.
_N_TRIALS: Final[int] = 1000

# Inclusion-rate pass band. +/-3 % around the declared 95 % level.
# Asymmetry is *intentional*: under-conservative (<0.92) is a worse
# failure mode than over-conservative (>0.98), but both signal a real
# bug -- see the ticket's "Stop conditions".
_INCLUSION_MIN: Final[float] = 0.92
_INCLUSION_MAX: Final[float] = 0.98

# Bias check: mean-of-fix offset must be below half a semi-minor axis.
# This is the bias *of the estimator*, not per-trial scatter (which is
# itself ~semi-minor by construction). MLE is asymptotically unbiased;
# at 1000 trials the MC noise floor of the bias estimate is well below
# this gate.
_BIAS_FRACTION_OF_SEMI_MINOR: Final[float] = 0.5

# Empirical-vs-claimed covariance: Frobenius ratio band. 1.5x is wider
# than the analytic-vs-analytic band because empirical covariance has
# its own MC noise floor of ``~sqrt(2/N_TRIALS)`` ~ 4.5 % at N = 1000.
_COVARIANCE_FROBENIUS_RATIO_MAX: Final[float] = 1.5
_COVARIANCE_FROBENIUS_RATIO_MIN: Final[float] = 1.0 / _COVARIANCE_FROBENIUS_RATIO_MAX

# ENU origin for trench-demo scenarios. Matches
# ``docs/demo/trench-demo-geometry.md`` Section 1 and ``test_fuser.py``.
_TRENCH_DEMO_LAT_DEG: Final[float] = 50.330
_TRENCH_DEMO_LON_DEG: Final[float] = 5.000
_TRENCH_DEMO_HAE_M: Final[float] = 200.0

# ENU origin for the synthetic isotropic / high-SNR scenarios. Central
# Poland -- matches ``conftest.origin``; nothing in the test depends
# on the absolute lat/lon (the projection is origin-symmetric).
_SYNTHETIC_LAT_DEG: Final[float] = 52.0
_SYNTHETIC_LON_DEG: Final[float] = 21.0

# A plausible UTC ns timestamp. Required > 0 by the contract validator;
# the absolute value is immaterial to the MC (every bearing in a trial
# shares the same timestamp so they batch into one fix).
_T0_NS: Final[int] = 1_778_976_000_000_000_000

# Surveyed node-position sigma (smartphone GNSS class). Plays no role
# in the MC except as a contract-valid value on ``GeodeticPosition``.
_NODE_SIGMA_M: Final[float] = 5.0

# Default fusion endpoint URL for ``FusionConfig`` -- the contract
# requires *some* value, the MC does not use the network.
_DEFAULT_FUSION_URL: Final[str] = "udp://0.0.0.0:9000"

# Band-distribution thresholds. Pinned per ADR-009 narrative -- the
# trench-demo lives in MEDIUM, the high-SNR mesh crosses to HIGH.
_BAND_DOMINANT_THRESHOLD: Final[float] = 0.5  # 50%: "dominant" means majority.
_BAND_HIGH_CAP_FOR_MEDIUM_SCENARIO: Final[float] = 0.3  # < 30 % HIGH in MEDIUM scenarios.


# ---------------------------------------------------------------------------
# Scenario types and builders.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeSpec:
    """One node in a Monte Carlo scenario.

    ``enu`` is the node's ENU position; ``sigma_deg`` is the per-bearing
    1-sigma uncertainty declared on every BearingReport from this node
    (the same value the Gaussian noise is drawn from, so the honesty
    contract is satisfied by construction).
    """

    enu: tuple[float, float]
    sigma_deg: float
    node_id: str
    method: Capability


@dataclass(frozen=True)
class Scenario:
    """A pinned Monte Carlo scenario.

    ``emitter_enu`` is the ground-truth emitter position in the same
    ENU frame as every ``NodeSpec.enu``. ``origin_lat_deg`` /
    ``origin_lon_deg`` are the geodetic anchor for projecting node
    positions into ``GeodeticPosition`` on each bearing.

    ``band_assertion`` is a callable that takes the band distribution
    counters and returns ``None`` on pass or a string explaining the
    failure. Some scenarios pin MEDIUM-dominant (per ADR-009), some
    pin HIGH-dominant, and the isotropic ring has no strict band
    requirement (geometry favourable, sigma moderate -- informative
    only).
    """

    name: str
    emitter_enu: tuple[float, float]
    nodes: tuple[NodeSpec, ...]
    origin_lat_deg: float
    origin_lon_deg: float
    band_assertion: Callable[[dict[ConfidenceLevel, int]], str | None]


def _trench_demo_beat_c() -> Scenario:
    """Three L1 nodes at sigma=5deg, emitter 3 km north. ADR-009: MEDIUM-dominant."""
    return Scenario(
        name="trench-demo Beat C (3 L1, sigma=5 deg)",
        emitter_enu=(0.0, 3000.0),
        nodes=(
            NodeSpec((-1800.0, 1800.0), 5.0, "A", Capability.L1_RSSI),
            NodeSpec((1800.0, 1800.0), 5.0, "B", Capability.L1_RSSI),
            NodeSpec((0.0, 900.0), 5.0, "C", Capability.L1_RSSI),
        ),
        origin_lat_deg=_TRENCH_DEMO_LAT_DEG,
        origin_lon_deg=_TRENCH_DEMO_LON_DEG,
        band_assertion=_assert_medium_dominant,
    )


def _trench_demo_beat_d() -> Scenario:
    """3 L1 at sigma=5deg + 1 L2 at sigma=1.5deg. ADR-009: still MEDIUM-dominant."""
    return Scenario(
        name="trench-demo Beat D (3 L1 + L2)",
        emitter_enu=(0.0, 3000.0),
        nodes=(
            NodeSpec((-1800.0, 1800.0), 5.0, "A", Capability.L1_RSSI),
            NodeSpec((1800.0, 1800.0), 5.0, "B", Capability.L1_RSSI),
            NodeSpec((0.0, 900.0), 5.0, "C", Capability.L1_RSSI),
            NodeSpec((2000.0, 3500.0), 1.5, "D", Capability.L2_MUSIC),
        ),
        origin_lat_deg=_TRENCH_DEMO_LAT_DEG,
        origin_lon_deg=_TRENCH_DEMO_LON_DEG,
        band_assertion=_assert_medium_dominant,
    )


def _isotropic_4node_ring() -> Scenario:
    """Four nodes on a 2 km ring offset 1 km from the emitter, sigma=3deg.

    The ring centre is offset *south* of the emitter so the centroid-
    to-emitter range (the ADR-005 D2 denominator) is well above the
    ``_MIN_RANGE_M_FOR_HIGH = 10 m`` degeneracy floor. A perfect
    centroid-on-emitter ring would collapse range_m to zero and force
    every fix to LOW -- exactly the ADR-005 "Negative" bullet edge
    case, useful to *test*, but not what this scenario exists to
    demonstrate.

    With ring centre at ``(0, -1000)`` and radius 2 km, centroid-to-
    emitter range is 1 km; per-bearing sigma=3deg at ~2 km gives
    semi-major ~95 m, semi/range ~9 %. Band lands in MEDIUM uniformly
    -- geometry favourable but sigma too coarse for HIGH. This is the
    "perfect geometry, mediocre per-bearing" sanity case (near-1.0
    GDOP).
    """
    radius_m = 2000.0
    ring_center = (0.0, -1000.0)
    nodes = tuple(
        NodeSpec(
            enu=(
                ring_center[0] + radius_m * math.cos(2.0 * math.pi * i / 4.0),
                ring_center[1] + radius_m * math.sin(2.0 * math.pi * i / 4.0),
            ),
            sigma_deg=3.0,
            node_id=f"R{i}",
            method=Capability.L1_RSSI,
        )
        for i in range(4)
    )
    return Scenario(
        name="Isotropic 4-node ring (sigma=3 deg)",
        emitter_enu=(0.0, 0.0),
        nodes=nodes,
        origin_lat_deg=_SYNTHETIC_LAT_DEG,
        origin_lon_deg=_SYNTHETIC_LON_DEG,
        # Geometry favourable, sigma moderate. No strict band assertion
        # -- the test still records the distribution for the briefing-
        # book table; a runaway band (e.g. all LOW) would indicate a
        # bug, but the precise mix of HIGH/MEDIUM here is sensitive
        # to the centroid-to-emitter range. Always-pass.
        band_assertion=_assert_any_band,
    )


def _high_snr_5node_mesh() -> Scenario:
    """Five high-SNR nodes (sigma=1deg) on a wide forward arc; emitter 2 km north.

    Geometry tuned (empirically during development) so that
    ``semi_major / range`` sits decisively below the 5 % HIGH-band
    threshold across all 1000 trials. The wide forward arc gives a
    near-1.0 GDOP and a closer emitter range (2 km) tightens the
    ratio. This is the "competition-compliant" geometry case ADR-009
    Section "Demo behaviour" invites the jury to imagine: denser,
    higher-sigma-honesty deployments cross into HIGH.

    At sigma=1deg, 5 nodes, range ~2.7 km centroid-to-emitter, semi-
    major ~113 m, so semi/range ~4.2 % < 5 %. HIGH band reachable.
    """
    nodes = (
        NodeSpec((-3000.0, 0.0), 1.0, "H0", Capability.L2_MUSIC),
        NodeSpec((-1500.0, -1000.0), 1.0, "H1", Capability.L2_MUSIC),
        NodeSpec((0.0, -1500.0), 1.0, "H2", Capability.L2_MUSIC),
        NodeSpec((1500.0, -1000.0), 1.0, "H3", Capability.L2_MUSIC),
        NodeSpec((3000.0, 0.0), 1.0, "H4", Capability.L2_MUSIC),
    )
    # Centroid ENU = (0, -700); emitter at (0, +2000) gives range ~2700 m.
    return Scenario(
        name="High-SNR 5-node mesh (sigma=1 deg)",
        emitter_enu=(0.0, 2000.0),
        nodes=nodes,
        origin_lat_deg=_SYNTHETIC_LAT_DEG,
        origin_lon_deg=_SYNTHETIC_LON_DEG,
        band_assertion=_assert_high_dominant,
    )


# ---------------------------------------------------------------------------
# Band-distribution assertions.
# ---------------------------------------------------------------------------


def _assert_medium_dominant(counts: dict[ConfidenceLevel, int]) -> str | None:
    """ADR-009 narrative: trench-demo lives in MEDIUM (>= 50%); HIGH < 30%.

    The dominant-MEDIUM gate plus the HIGH cap together pin the ADR-009
    narrative at the band-distribution level. If the demo geometry
    produces a high count of HIGH labels the policy or the chi-square
    scaling is wrong; scratchpad per the ticket's "Stop conditions".
    """
    total = sum(counts.values())
    if total == 0:
        return "no trials counted"
    medium_frac = counts[ConfidenceLevel.MEDIUM] / total
    high_frac = counts[ConfidenceLevel.HIGH] / total
    if medium_frac < _BAND_DOMINANT_THRESHOLD:
        return f"MEDIUM should be dominant (>= 50%); got {100.0 * medium_frac:.1f}%"
    if high_frac >= _BAND_HIGH_CAP_FOR_MEDIUM_SCENARIO:
        return f"HIGH should be < 30% per ADR-009 demo narrative; got {100.0 * high_frac:.1f}%"
    return None


def _assert_high_dominant(counts: dict[ConfidenceLevel, int]) -> str | None:
    """Sharp geometry + low sigma produces HIGH >= 50%."""
    total = sum(counts.values())
    if total == 0:
        return "no trials counted"
    high_frac = counts[ConfidenceLevel.HIGH] / total
    if high_frac < _BAND_DOMINANT_THRESHOLD:
        return f"HIGH should be dominant (>= 50%) for sharp geometry; got {100.0 * high_frac:.1f}%"
    return None


def _assert_any_band(counts: dict[ConfidenceLevel, int]) -> str | None:
    """No strict band assertion -- record the distribution for the briefing book."""
    total = sum(counts.values())
    if total == 0:
        return "no trials counted"
    return None


# ---------------------------------------------------------------------------
# Bearing construction + inclusion geometry.
# ---------------------------------------------------------------------------


def _azimuth_from_node_to_emitter_deg(
    node_enu: tuple[float, float],
    emitter_enu: tuple[float, float],
) -> float:
    """True azimuth from node to emitter -- same convention as ``geometry``.

    Degrees, true north = 0, CW positive, wrapped to ``[0, 360)``.
    Matches ``rfmesh_fusion.geometry.bearing_to_unit_vector`` and the
    conftest helper.
    """
    de = emitter_enu[0] - node_enu[0]
    dn = emitter_enu[1] - node_enu[1]
    return math.degrees(math.atan2(de, dn)) % 360.0


def _build_bearing(
    *,
    node_spec: NodeSpec,
    emitter_enu: tuple[float, float],
    noise_deg: float,
    origin_geo: GeodeticPosition,
) -> BearingReport:
    """Construct a noisy ``BearingReport`` with honest declared sigma.

    ``noise_deg`` is the actual Gaussian draw to add to the true
    azimuth. The declared ``azimuth_sigma_deg`` is ``node_spec.
    sigma_deg`` -- the same value the noise was drawn from, so the
    honesty contract (Invariant B2) is satisfied by construction.
    """
    truth_az_deg = _azimuth_from_node_to_emitter_deg(node_spec.enu, emitter_enu)
    noisy_az_deg = (truth_az_deg + noise_deg) % 360.0
    node_geo = from_enu(
        node_spec.enu[0],
        node_spec.enu[1],
        origin=origin_geo,
        sigma_m=_NODE_SIGMA_M,
    )
    return BearingReport(
        node_id=node_spec.node_id,
        t_unix_ns=_T0_NS,
        node_position=node_geo,
        azimuth_deg=noisy_az_deg,
        azimuth_sigma_deg=node_spec.sigma_deg,
        method=node_spec.method,
    )


def _point_inside_ellipse(
    *,
    offset_enu: tuple[float, float],
    semi_major_m: float,
    semi_minor_m: float,
    orientation_deg: float,
) -> bool:
    """Return True iff ``offset_enu`` lies inside the rotated ellipse.

    The ellipse's ``orientation_deg`` is the angle of the semi-major
    axis from local East toward North (``INTERFACES.md`` Section 2).
    With ``theta = radians(orientation_deg)``:

    * semi-major direction = ``(cos theta, sin theta)`` in (E, N).
    * semi-minor direction = ``(-sin theta, cos theta)``.

    Project the offset onto those axes and apply the standard ellipse
    inequality. Tolerance is intentionally exact -- any tweak there
    would obscure honesty failures.
    """
    de, dn = offset_enu
    theta_rad = math.radians(orientation_deg)
    cos_t = math.cos(theta_rad)
    sin_t = math.sin(theta_rad)
    u = de * cos_t + dn * sin_t
    v = -de * sin_t + dn * cos_t
    return (u / semi_major_m) ** 2 + (v / semi_minor_m) ** 2 <= 1.0


# ---------------------------------------------------------------------------
# Per-trial bookkeeping.
# ---------------------------------------------------------------------------


@dataclass
class _TrialResults:
    """Accumulates inclusion, position offsets, and band counts across trials."""

    inclusions: int
    total: int
    offsets_east_m: list[float]
    offsets_north_m: list[float]
    band_counts: dict[ConfidenceLevel, int]
    first_claimed_cov: tuple[float, float, float] | None
    first_semi_minor_m: float | None

    @classmethod
    def fresh(cls) -> _TrialResults:
        return cls(
            inclusions=0,
            total=0,
            offsets_east_m=[],
            offsets_north_m=[],
            band_counts={
                ConfidenceLevel.HIGH: 0,
                ConfidenceLevel.MEDIUM: 0,
                ConfidenceLevel.LOW: 0,
            },
            first_claimed_cov=None,
            first_semi_minor_m=None,
        )


def _run_one_trial(
    *,
    fuser: StansfieldMLEFuser,
    config: FusionConfig,
    scenario: Scenario,
    origin_geo: GeodeticPosition,
    rng: np.random.Generator,
    results: _TrialResults,
) -> None:
    """Run one MC trial and update ``results`` in place.

    Failure of ``fuser.fuse`` to return a ``FixEvent`` is silently
    skipped from the inclusion count *only if it returns None* -- that
    is the Protocol's "cannot solve" signal. For the geometries pinned
    in this test, ``None`` should never occur; if it does, the trial
    count diverges from 1000 and the assertions will see a smaller
    denominator (the test reports that distinctly).
    """
    bearings = []
    for node in scenario.nodes:
        noise_deg = float(rng.normal(loc=0.0, scale=node.sigma_deg))
        bearings.append(
            _build_bearing(
                node_spec=node,
                emitter_enu=scenario.emitter_enu,
                noise_deg=noise_deg,
                origin_geo=origin_geo,
            )
        )

    fix = fuser.fuse(bearings, config)
    if fix is None:
        # Refusal-to-fix is a non-event in inclusion accounting; the
        # outer assertion checks ``results.total == _N_TRIALS``.
        return

    # Project the fused emitter geodetic back into the trial's ENU
    # frame for a direct comparison against the truth ENU position.
    fix_east_m, fix_north_m = to_enu(fix.position, origin=origin_geo)
    offset = (
        scenario.emitter_enu[0] - fix_east_m,
        scenario.emitter_enu[1] - fix_north_m,
    )
    results.offsets_east_m.append(fix_east_m - scenario.emitter_enu[0])
    results.offsets_north_m.append(fix_north_m - scenario.emitter_enu[1])

    inside = _point_inside_ellipse(
        offset_enu=offset,
        semi_major_m=fix.confidence_ellipse_95.semi_major_m,
        semi_minor_m=fix.confidence_ellipse_95.semi_minor_m,
        orientation_deg=fix.confidence_ellipse_95.orientation_deg,
    )
    if inside:
        results.inclusions += 1
    results.total += 1
    results.band_counts[fix.confidence_level] += 1

    if results.first_claimed_cov is None:
        results.first_claimed_cov = fix.covariance_m2
        results.first_semi_minor_m = fix.confidence_ellipse_95.semi_minor_m


# ---------------------------------------------------------------------------
# The parametrized test.
# ---------------------------------------------------------------------------


_ALL_SCENARIOS: Final[tuple[Scenario, ...]] = (
    _trench_demo_beat_c(),
    _trench_demo_beat_d(),
    _isotropic_4node_ring(),
    _high_snr_5node_mesh(),
)


@pytest.mark.slow
@pytest.mark.parametrize(
    "scenario",
    _ALL_SCENARIOS,
    ids=[s.name for s in _ALL_SCENARIOS],
)
def test_honest_ellipse_monte_carlo(scenario: Scenario) -> None:
    """The 95% ellipse contains the true emitter in 95 +/- 3 % of trials.

    Per-scenario assertions (all must hold):

    * Inclusion rate in ``[0.92, 0.98]``.
    * Mean position bias <= ``0.5 * semi_minor_m``.
    * Empirical-vs-claimed covariance Frobenius ratio in ``[1/1.5,
      1.5]``.
    * Band distribution matches the scenario's ADR-009 expectation
      (MEDIUM-dominant for trench-demo, HIGH-dominant for high-SNR).

    Failure of any of these means the honesty payload is broken; do
    NOT widen the bands. Diagnose per the ticket's "Stop conditions".
    """
    rng = np.random.default_rng(seed=_SEED)
    config = FusionConfig(listen_url=_DEFAULT_FUSION_URL)  # type: ignore[arg-type]
    fuser = StansfieldMLEFuser(config)
    origin_geo = GeodeticPosition(
        lat_deg=scenario.origin_lat_deg,
        lon_deg=scenario.origin_lon_deg,
        hae_m=_TRENCH_DEMO_HAE_M if scenario.origin_lat_deg == _TRENCH_DEMO_LAT_DEG else 0.0,
        sigma_m=0.0,
    )

    results = _TrialResults.fresh()
    for _ in range(_N_TRIALS):
        _run_one_trial(
            fuser=fuser,
            config=config,
            scenario=scenario,
            origin_geo=origin_geo,
            rng=rng,
            results=results,
        )

    # Every trial in these scenarios must produce a fix. A ``None`` is
    # a Protocol-level "cannot solve"; for our pinned geometries it
    # would indicate a regression in stansfield/MLE/covariance.
    assert results.total == _N_TRIALS, (
        f"{scenario.name}: only {results.total}/{_N_TRIALS} trials produced a fix; "
        "Stansfield/MLE/covariance pipeline regressed."
    )

    inclusion_rate = results.inclusions / results.total
    assert _INCLUSION_MIN <= inclusion_rate <= _INCLUSION_MAX, (
        f"{scenario.name}: 95% ellipse inclusion rate "
        f"{100.0 * inclusion_rate:.1f}% is outside the honesty band "
        f"[{100.0 * _INCLUSION_MIN:.0f}%, {100.0 * _INCLUSION_MAX:.0f}%]. "
        f"The honesty payload is broken; do NOT widen the band. "
        f"See WS-CD-008 ticket's 'Stop conditions' for diagnosis."
    )

    # Mean position bias: norm of the mean of per-trial offsets must
    # be small relative to the ellipse's resolution. MLE is
    # asymptotically unbiased.
    mean_east = float(np.mean(results.offsets_east_m))
    mean_north = float(np.mean(results.offsets_north_m))
    bias_m = math.hypot(mean_east, mean_north)
    assert results.first_semi_minor_m is not None
    bias_threshold_m = _BIAS_FRACTION_OF_SEMI_MINOR * results.first_semi_minor_m
    assert bias_m <= bias_threshold_m, (
        f"{scenario.name}: MLE bias {bias_m:.2f} m exceeds "
        f"{_BIAS_FRACTION_OF_SEMI_MINOR} * semi_minor "
        f"({bias_threshold_m:.2f} m). MLE not unbiased -- check the "
        f"Gauss-Newton convergence or the Jacobian sign."
    )

    # Empirical covariance vs the claimed 2x2 from trial 0 -- Frobenius
    # ratio must lie inside the 1.5x band. Builds the empirical
    # covariance with ``ddof=1`` (Bessel-corrected) for an unbiased
    # estimate, matching the convention the analytic Sigma represents.
    offsets = np.column_stack(
        (
            np.array(results.offsets_east_m, dtype=np.float64),
            np.array(results.offsets_north_m, dtype=np.float64),
        )
    )
    empirical_cov = np.cov(offsets, rowvar=False, ddof=1)
    assert results.first_claimed_cov is not None
    claimed = np.array(
        [
            [results.first_claimed_cov[0], results.first_claimed_cov[1]],
            [results.first_claimed_cov[1], results.first_claimed_cov[2]],
        ],
        dtype=np.float64,
    )
    emp_frob = float(np.linalg.norm(empirical_cov, ord="fro"))
    claimed_frob = float(np.linalg.norm(claimed, ord="fro"))
    ratio = emp_frob / claimed_frob if claimed_frob > 0.0 else float("inf")
    assert _COVARIANCE_FROBENIUS_RATIO_MIN <= ratio <= _COVARIANCE_FROBENIUS_RATIO_MAX, (
        f"{scenario.name}: empirical/claimed covariance Frobenius ratio "
        f"{ratio:.3f} is outside [{_COVARIANCE_FROBENIUS_RATIO_MIN:.3f}, "
        f"{_COVARIANCE_FROBENIUS_RATIO_MAX:.3f}]. The CLAIMED covariance is "
        f"either too small (ratio > 1.5, ellipse will be under-conservative) "
        f"or too large (ratio < 0.667). Check sigma unit conversion + "
        f"chi-square constant."
    )

    # Band distribution -- scenario-specific assertion (None = pass).
    band_failure = scenario.band_assertion(results.band_counts)
    assert band_failure is None, (
        f"{scenario.name}: band distribution check failed: {band_failure}. "
        f"Counts: HIGH={results.band_counts[ConfidenceLevel.HIGH]}, "
        f"MEDIUM={results.band_counts[ConfidenceLevel.MEDIUM]}, "
        f"LOW={results.band_counts[ConfidenceLevel.LOW]}."
    )
