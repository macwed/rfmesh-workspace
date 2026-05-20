"""L1 amplitude-sweep BearingEstimator.

Implements the L1 capability: a servo-rotated directional antenna's RSSI
peak-find, fitted to a quadratic in the neighbourhood of the peak, with a
fit-driven 1-sigma azimuth uncertainty that the fusion solver consumes as
inverse weight. Honesty of ``BearingReport.azimuth_sigma_deg`` is
load-bearing (see ``INTERFACES.md`` Section 3) -- this module's
``tests/test_sigma_honesty.py`` is the empirical guard.

THE SWEEP-AWARENESS GAP IN ``BearingEstimator``
-----------------------------------------------
The frozen contract ``BearingEstimator.estimate(samples) -> BearingReport |
None`` has no heading channel. An L1 estimator needs the antenna heading
*per IQ block*, otherwise it cannot map a block to a position on the
RSSI-vs-azimuth curve. Resolving this *without a contract change*
(Invariant 1):

* The class satisfies the Protocol's ``method`` and ``estimate`` surface
  exactly. ``isinstance(estimator, BearingEstimator)`` is True.
* It additionally exposes two L1-specific methods, ``begin_sweep`` and
  ``observe``, which the node runtime calls between ``begin_sweep`` and
  the final ``estimate``. A runtime that only knows the Protocol stays
  oblivious; an L1-aware runtime drives the sweep with the extra methods.

The node-runtime workstream reads this design note when wiring L1.

RUN-TIME SIGMA PATH (binding)
-----------------------------
1. Per heading, ``observe(h, samples)`` records ``compute_rssi_dbfs(samples)``.
2. ``estimate(samples)`` finalises the accumulated sweep. ``samples`` is
   not consumed -- the data was provided through ``observe``. Keeping the
   parameter satisfies the Protocol shape.
3. Locate the argmax of the RSSI array.
4. Fit a quadratic ``y = c2 x^2 + c1 x + c0`` to the
   ``_PARABOLA_FIT_N_POINTS`` (7) RSSI samples around the argmax, with x
   measured in degrees off the argmax heading (shortest-arc wrapped to
   [-180, 180) so the fit works across the 0/360 seam).
5. The parabola vertex is ``x_v = -c1 / (2 c2)``. The fractional peak
   azimuth is ``(peak_heading + x_v) mod 360``.
6. ``np.polyfit(..., cov=True)`` returns ``Cov(c2, c1, c0)`` scaled by the
   fit's residual variance (the chi-square / (N - 3) estimator of the
   per-heading RSSI noise; for a smooth main-lobe pattern under AWGN the
   residuals are approximately Gaussian, so this is the standard fit-
   driven sigma). Propagation via the Jacobian gives ``Var(x_v)``:

       d x_v / d c1 = -1 / (2 c2)
       d x_v / d c2 =  c1 / (2 c2^2)
       Var(x_v)    = (d x_v / d c1)^2 * Var(c1)
                   + (d x_v / d c2)^2 * Var(c2)
                   + 2 * (d x_v / d c1) * (d x_v / d c2) * Cov(c1, c2)

7. ``Var(x_v)`` is scaled by ``1 / median(chi-square_dof / dof)`` -- the
   known median-bias correction on the chi-square-distributed residual
   variance. polyfit's ``cov=True`` returns an *unbiased* estimator of
   variance, but its *median* is biased low by the chi-square dof factor.
   Dividing by ``median(chi-square_4) / 4 = 0.8392`` makes the median of
   ``Var(x_v)`` an unbiased estimator of the true variance, which is
   what Acceptance 3 checks via ``median(sigma_claimed) vs sigma_emp``.
   The square root is reported as ``BearingReport.azimuth_sigma_deg``.

REFERENCE SIGMA (sanity check, NOT the runtime path)
----------------------------------------------------
For a CW emitter against a smooth main-lobe antenna with HPBW W, a
Cramer-Rao-style scaling for the peak-position estimate is

    sigma_theta ~ k * HPBW / sqrt(2 * SNR_linear * N_eff)

with ``k`` a fit-shape constant of order 0.5-1.0 and ``N_eff`` the count
of samples that lie within the main lobe (gain >= -3 dB). The run-time
estimator does not use this -- it propagates the fit's own covariance --
but the constant ``k`` and the form of the formula are useful when a
calibration drift breaks the honesty test and the next debugging step is
"is the fit producing the right magnitude of sigma at all".

FAILURE MODES (Invariant 4 surface)
-----------------------------------
``estimate`` returns ``None`` rather than fabricate a confident bearing
when:

* ``begin_sweep`` was not called.
* Fewer than ``_PARABOLA_FIT_N_POINTS`` headings were observed.
* The peak prominence (peak RSSI minus median-floor RSSI) is below
  ``peak_prominence_db_min``.
* The fitted curvature ``c2`` is non-negative (the quadratic opens
  upward -- the "peak" is actually a saddle, e.g. multipath fluke).
* The fitted vertex lies outside the half-window around the argmax
  heading (the fit extrapolated, indicating the peak is poorly modelled
  as a parabola).
* The propagated ``Var(x_v)`` is non-positive or non-finite.

A ``None`` return is *not* an error; it is the honest "no usable bearing
this sweep". The node runtime simply does not forward it.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt
from rfmesh_contracts import (  # type: ignore[import-untyped, unused-ignore]
    BearingReport,
    Capability,
    GeodeticPosition,
    IQBlock,
)

from rfmesh_dsp.rssi import compute_rssi_dbfs

# Number of RSSI samples included in the local quadratic fit. Symmetric
# around the argmax (3 on each side). With 7 samples and 3 polynomial
# parameters the fit has 4 residual degrees of freedom, which keeps the
# chi-square bias on the residual-variance estimator small enough that the
# honesty test (median claimed sigma vs empirical spread, +/-20 %) is
# comfortably inside its band.
_PARABOLA_FIT_N_POINTS = 7
# Percent of strongest RSSI samples excluded before taking the median when
# computing the noise-floor reference for the prominence gate and the
# snr_db diagnostic. Mirrors compute_noise_floor_dbfs's algorithm, applied
# to per-heading RSSI rather than per-bin PSD.
_NOISE_FLOOR_EXCLUDE_STRONGEST_PCT = 10.0
# Median of chi-square_{dof} / dof for dof = _PARABOLA_FIT_N_POINTS - 3 = 4.
# polyfit(cov=True)'s residual-variance scale is chi-square / dof: unbiased
# in expectation but biased low in median by this factor. Dividing
# the propagated Var(x_v) by this factor makes the *median* claimed sigma
# an unbiased estimator of the true sigma -- which is what
# tests/test_sigma_honesty.py asserts.
# Value: scipy.stats.chi2.ppf(0.5, df=4) / 4 = 0.83918212...
_CHI2_MEDIAN_OVER_DOF = 0.8391821182013634
# Full circle in degrees. Named to keep ruff PLR2004 quiet and to make the
# unit obvious next to the heading-range guard.
_DEG_FULL_CIRCLE = 360.0
_DEG_HALF_CIRCLE = 180.0
# Slack on the vertex-inside-window check: a vertex fractionally past the
# half-window edge is still trusted (numerical noise), beyond it is not.
_VERTEX_BOUND_SLACK_DEG = 1e-6


class L1AmplitudeSweepEstimator:
    """L1 amplitude-comparison BearingEstimator (servo-swept directional antenna).

    Conforms to ``rfmesh_contracts.BearingEstimator`` structurally
    (``method`` + ``estimate``) and adds the L1-specific ``begin_sweep`` /
    ``observe`` surface that the node runtime drives the sweep with.

    A single instance is reused across sweeps. ``begin_sweep`` resets the
    accumulator; ``estimate`` finalises the current sweep and re-arms the
    instance so the next ``begin_sweep`` -> ``observe`` -> ``estimate``
    cycle proceeds from a clean state.
    """

    #: Capability this estimator implements. Class attribute satisfies the
    #: ``BearingEstimator.method`` property on the Protocol side.
    method: Capability = Capability.L1_RSSI

    def __init__(
        self,
        *,
        node_id: str,
        node_position: GeodeticPosition,
        sweep_step_deg: float = 1.0,
        sweep_dwell_samples: int,
        peak_prominence_db_min: float = 6.0,
    ) -> None:
        """Bind node identity and sweep parameters.

        Args:
            node_id: Stable ``NodeConfig.node_id`` of the producing node;
                carried verbatim onto every emitted ``BearingReport``.
            node_position: Position of the node at sweep start;
                carried onto every emitted ``BearingReport``.
            sweep_step_deg: Nominal angular step the runtime advances the
                servo by between observations. Only used as a sanity bound
                on the parabola-vertex offset (the vertex must lie inside
                half the fit window).
            sweep_dwell_samples: Number of IQ samples the runtime reads at
                each heading. Carried for diagnostic completeness and a
                guard in ``observe`` (an inconsistent block size is a
                runtime-wiring bug rather than something to silently
                tolerate).
            peak_prominence_db_min: Minimum required (peak RSSI - median
                noise floor) for ``estimate`` to emit a bearing. Below
                this threshold ``estimate`` returns ``None`` (Invariant 4).
        """
        if sweep_step_deg <= 0.0:
            msg = f"sweep_step_deg must be > 0 (got {sweep_step_deg})."
            raise ValueError(msg)
        if sweep_dwell_samples <= 0:
            msg = f"sweep_dwell_samples must be > 0 (got {sweep_dwell_samples})."
            raise ValueError(msg)

        self._node_id = node_id
        self._node_position = node_position
        self._sweep_step_deg = float(sweep_step_deg)
        self._sweep_dwell_samples = int(sweep_dwell_samples)
        self._peak_prominence_db_min = float(peak_prominence_db_min)

        self._sweep_t_unix_ns: int | None = None
        self._headings_deg: list[float] = []
        self._rssi_dbfs: list[float] = []
        # Last-refusal annotation — operator-facing diagnostic of why
        # `estimate()` returned None on the most recent sweep. Cleared
        # to None on every successful emission and at each begin_sweep.
        # The node runtime reads this through `last_refusal_reason` and
        # surfaces it via NodeStatus.status_detail (per E1, 2026-05-18).
        # Wire-level diagnostic via a contract enum is parked as G4
        # (needs SCHEMA_VERSION 1.2.0 bump).
        self._last_refusal_reason: str | None = None

    def begin_sweep(self, t_unix_ns: int) -> None:
        """Mark the start of a new sweep; record the bearing's "instant".

        Per ``INTERFACES.md`` Section 3 the ``BearingReport.t_unix_ns`` is the
        sweep-start time (a sweep takes some hundreds of ms; fusion's
        ``batch_window_ms`` defaults to 100 ms, so the start is the right
        instant to bin against). Resets any state left from a prior sweep.
        """
        if t_unix_ns <= 0:
            msg = f"begin_sweep: t_unix_ns must be > 0 (got {t_unix_ns})."
            raise ValueError(msg)
        self._sweep_t_unix_ns = t_unix_ns
        self._last_refusal_reason = None
        self._headings_deg = []
        self._rssi_dbfs = []

    def observe(self, heading_deg: float, samples: IQBlock) -> None:
        """Record one (heading, RSSI) measurement.

        Args:
            heading_deg: Geographic heading the antenna boresight pointed
                at during the dwell, degrees true, [0, 360).
            samples: 1-D ``complex64`` IQ block of length
                ``sweep_dwell_samples``.
        """
        if self._sweep_t_unix_ns is None:
            msg = "observe() called before begin_sweep(); call begin_sweep first."
            raise RuntimeError(msg)
        if not 0.0 <= heading_deg < _DEG_FULL_CIRCLE:
            msg = f"observe: heading_deg must be in [0, 360) (got {heading_deg})."
            raise ValueError(msg)
        iq = np.asarray(samples)
        if iq.ndim != 1:
            msg = f"observe: samples must be 1-D (got ndim={iq.ndim})."
            raise ValueError(msg)
        if iq.size != self._sweep_dwell_samples:
            msg = (
                "observe: samples length "
                f"{iq.size} does not match sweep_dwell_samples "
                f"{self._sweep_dwell_samples}."
            )
            raise ValueError(msg)
        rssi = compute_rssi_dbfs(iq.astype(np.complex64, copy=False))
        self._headings_deg.append(float(heading_deg))
        self._rssi_dbfs.append(float(rssi))

    def estimate(self, samples: IQBlock) -> BearingReport | None:
        """Finalise the accumulated sweep and return a ``BearingReport`` or ``None``.

        The ``samples`` parameter is required by the
        ``BearingEstimator.estimate`` Protocol signature but is not
        consumed by L1 -- per-heading data is provided through
        ``observe``. The sweep's accumulator is cleared at the end of
        every ``estimate`` call so the instance is ready for the next
        ``begin_sweep``, regardless of whether a bearing was emitted.
        """
        del samples  # The L1 finalisation reads from the per-heading accumulator.

        sweep_t = self._sweep_t_unix_ns
        headings = np.asarray(self._headings_deg, dtype=np.float64)
        rssi = np.asarray(self._rssi_dbfs, dtype=np.float64)
        # Re-arm before any early return so a failed estimate does not leak
        # state into the next sweep.
        self._sweep_t_unix_ns = None
        self._headings_deg = []
        self._rssi_dbfs = []

        if sweep_t is None or rssi.size < _PARABOLA_FIT_N_POINTS:
            self._last_refusal_reason = (
                f"sweep underpopulated: {rssi.size} < "
                f"{_PARABOLA_FIT_N_POINTS} observations (no begin_sweep?)"
            )
            return None

        fit_result = self._fit_peak(headings, rssi)
        if fit_result is None:
            return None  # _fit_peak already populated _last_refusal_reason
        azimuth_deg, sigma_az_deg, snr_db = fit_result

        # Successful emission — clear any stale refusal text.
        self._last_refusal_reason = None
        return BearingReport(  # type: ignore[no-any-return, unused-ignore]
            node_id=self._node_id,
            t_unix_ns=sweep_t,
            node_position=self._node_position,
            azimuth_deg=azimuth_deg,
            azimuth_sigma_deg=sigma_az_deg,
            method=Capability.L1_RSSI,
            snr_db=snr_db,
        )

    @property
    def last_refusal_reason(self) -> str | None:
        """Human-readable reason the most recent `estimate()` returned None.

        ``None`` when the last call emitted a bearing successfully, or
        when no `estimate()` call has happened yet. Operator-facing
        diagnostic; the node runtime forwards this to
        `NodeStatus.status_detail` so the dashboard's NodeStatusPanel
        renders it (E1 closes the operator-invisible-refusal gap).
        """
        return self._last_refusal_reason

    def _fit_peak(
        self,
        headings: npt.NDArray[np.float64],
        rssi: npt.NDArray[np.float64],
    ) -> tuple[float, float, float] | None:
        """Locate, fit, and propagate sigma for one sweep's RSSI samples.

        Returns ``(azimuth_deg, sigma_az_deg, snr_db)`` on success, or
        ``None`` if any of the guards (prominence, fit success, curvature
        sign, vertex-in-window, positive variance) trips. Extracted from
        ``estimate`` so the latter is one short read end-to-end and stays
        within the workspace return-count budget; the logic itself is
        unchanged.
        """
        median_floor_db = _median_floor_db(rssi, _NOISE_FLOOR_EXCLUDE_STRONGEST_PCT)
        peak_idx = int(np.argmax(rssi))
        peak_db = float(rssi[peak_idx])
        prominence_db = peak_db - median_floor_db
        if prominence_db < self._peak_prominence_db_min:
            self._last_refusal_reason = (
                f"L1 refused: prominence {prominence_db:.1f} dB < gate "
                f"{self._peak_prominence_db_min:.1f} dB (multipath dominance?)"
            )
            return None

        half = _PARABOLA_FIT_N_POINTS // 2
        idxs = (np.arange(peak_idx - half, peak_idx + half + 1) % rssi.size).astype(np.intp)
        peak_heading = float(headings[peak_idx])
        # Wrap each window heading to a signed offset in (-180, 180] degrees
        # from the peak heading so the fit works across the 0/360 seam.
        raw_offsets = headings[idxs] - peak_heading + _DEG_HALF_CIRCLE
        x = (raw_offsets % _DEG_FULL_CIRCLE) - _DEG_HALF_CIRCLE
        y = rssi[idxs]

        fit = _fit_parabola_with_cov(x, y)
        if fit is None:
            self._last_refusal_reason = "L1 refused: parabola fit failed (singular cov)"
            return None
        c2, c1, _c0, cov = fit
        # Curvature must be negative for a true peak; otherwise the
        # quadratic opens upward and the "peak" is a saddle (Invariant 4).
        if c2 >= 0.0:
            self._last_refusal_reason = (
                f"L1 refused: parabola opens upward (c2={c2:.3e}, "
                "peak is a saddle — multipath fluke?)"
            )
            return None

        vertex_x = -c1 / (2.0 * c2)
        # The parabola is only a faithful approximation inside the fit
        # window; an extrapolated vertex means the data did not actually
        # peak inside the window we picked, so refuse.
        if abs(vertex_x) > half * self._sweep_step_deg + _VERTEX_BOUND_SLACK_DEG:
            self._last_refusal_reason = (
                f"L1 refused: vertex offset {vertex_x:.1f} deg outside "
                f"fit window (peak not in observed sweep)"
            )
            return None

        d_dc1 = -1.0 / (2.0 * c2)
        d_dc2 = c1 / (2.0 * c2 * c2)
        # np.polyfit returns coefficients highest-degree first, so cov is
        # indexed in the same order: cov[0, 0] = Var(c2), cov[1, 1] =
        # Var(c1), cov[1, 0] = cov[0, 1] = Cov(c1, c2).
        var_vertex_x = (
            d_dc1 * d_dc1 * cov[1, 1] + d_dc2 * d_dc2 * cov[0, 0] + 2.0 * d_dc1 * d_dc2 * cov[1, 0]
        )
        # Median-bias correction on the chi-square residual-variance scaling
        # (see module docstring step 7). Makes median(claimed_sigma) over
        # many trials equal to the true bearing-recovery sigma.
        var_vertex_x /= _CHI2_MEDIAN_OVER_DOF
        if not math.isfinite(var_vertex_x) or var_vertex_x <= 0.0:
            self._last_refusal_reason = (
                f"L1 refused: non-finite variance {var_vertex_x} (numerically unrecoverable fit)"
            )
            return None

        return (
            (peak_heading + vertex_x) % _DEG_FULL_CIRCLE,
            math.sqrt(var_vertex_x),
            peak_db - median_floor_db,
        )


def _median_floor_db(rssi_db: npt.NDArray[np.float64], exclude_strongest_pct: float) -> float:
    """Median RSSI after excluding the top ``exclude_strongest_pct`` percent.

    Mirrors ``compute_noise_floor_dbfs``'s algorithm but operates on a
    per-heading RSSI vector instead of a per-bin PSD, returning a robust
    estimate of the "antenna-off-emitter" RSSI floor.
    """
    sorted_db = np.sort(rssi_db)
    keep_n = max(round(sorted_db.size * (1.0 - exclude_strongest_pct / 100.0)), 1)
    return float(np.median(sorted_db[:keep_n]))


def _fit_parabola_with_cov(
    x: npt.NDArray[np.float64],
    y: npt.NDArray[np.float64],
) -> tuple[float, float, float, npt.NDArray[np.float64]] | None:
    """Quadratic least-squares fit with covariance.

    Returns ``(c2, c1, c0, cov_matrix)`` ordered to match ``np.polyfit``'s
    highest-degree-first convention, or ``None`` if the fit is
    ill-conditioned. ``cov`` is the 3x3 covariance scaled by the residual
    chi-square divided by the residual degrees of freedom -- i.e. the
    standard fit-driven sigma propagation under Gaussian-residual
    assumptions.
    """
    try:
        coeffs, cov = np.polyfit(x, y, 2, cov=True)
    except (np.linalg.LinAlgError, ValueError):
        return None
    cov_arr = np.asarray(cov, dtype=np.float64)
    if cov_arr.shape != (3, 3) or not np.all(np.isfinite(cov_arr)):
        return None
    return float(coeffs[0]), float(coeffs[1]), float(coeffs[2]), cov_arr
