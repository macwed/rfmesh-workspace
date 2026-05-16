"""L2 Capon (a.k.a. MVDR-spectrum) BearingEstimator.

Implements the L2 capability the contracts label ``Capability.L2_MVDR_NULL``:
a phase-coherent direction-finding estimator that consumes a multi-channel
IQ block, inverts the sample covariance with diagonal loading, scans the
Capon pseudospectrum over array-local azimuth, and emits a
``BearingReport`` whose ``azimuth_sigma_deg`` is calibrated for honesty
against ground-truth Monte Carlo (see ``tests/test_l2_mvdr_sigma_honesty.py``).

NAMING NOTE
-----------
Capon / MVDR are two names for the same DoA spectrum:
``P_capon(theta) = 1 / Re( a(theta)^H * R^{-1} * a(theta) )``. The frozen
contract enum has ``L2_MVDR_NULL`` for the null-steering capability; this
estimator is structurally the *spectrum* variant of the same R^{-1}-based
maths and reports ``Capability.L2_MVDR_NULL`` for now (per the ticket,
the enum name is the only one available pre-contract-change). Pure
null-steering (synthesise weights toward known interferers) is a separate
utility that does not satisfy ``BearingEstimator`` and is out of scope.

DIAGONAL LOADING IS REQUIRED, NOT OPTIONAL
------------------------------------------
The sample covariance from a finite block has eigenvalues that include
"noise" eigenvalues which are close to the true noise floor but not
exactly equal: the smallest eigenvalue can be orders of magnitude smaller
than the others, making R ill-conditioned. Inverting that small
eigenvalue magnifies its contribution and the Capon spectrum becomes
noise-driven trash. Loading
``R_loaded = R + epsilon * I, epsilon = diagonal_loading_factor * trace(R) / N``
bounds the smallest eigenvalue from below by ``epsilon``, swamping numerical
noise without significantly perturbing the peak location at any
moderate-to-good SNR. The default factor ``1e-3`` adds 0.1 % of the average
channel power and is comfortably sufficient for N = 2..8 arrays at SNR
>= 0 dB.

RUN-TIME SIGMA PATH (binding)
-----------------------------
1. ``R = sample_covariance(block)``.
2. Optional ``R = forward_backward_smooth(R)`` (default off; v0.1 single
   emitter does not need de-correlation).
3. ``R_peak_loaded = R + eps_peak * I`` with
   ``eps_peak = diagonal_loading_factor * trace(R) / N`` (default
   loading 1e-3).
4. Reject if ``cond(R_peak_loaded) > 1e8``: loading has not stabilised
   the inverse.
5. ``R_peak_inv = inv(R_peak_loaded)``.
6. Scan: for each azimuth on a uniform ``scan_step_deg`` grid over
   ``[0, 360)``, compute the Capon spectrum ``P(theta_k)`` from
   ``R_peak_inv``.
7. Locate ``argmax(P)``; reject if ``max(P) < 3 * median(P)`` (no clear
   peak).
8. Fit a quadratic ``y = c2 x^2 + c1 x + c0`` to ``1/P`` over the
   ``_PARABOLA_FIT_N_POINTS`` (7) samples around the argmax (peak side
   only). ``1/P`` is minimum at the true DoA so the curvature
   ``c2 > 0``.
9. Sub-grid vertex ``x_v = -c1 / (2 c2)``; the recovered azimuth is
   ``(peak_az + x_v) mod 360``.
10. **Sigma path** -- re-invert R with a much smaller loading
    ``eps_sigma = _SIGMA_LOADING_FACTOR * trace(R) / N`` and re-evaluate
    the same 7-point Capon spectrum. Fit ``y = c2_s x^2 + c1_s x + c0_s``
    over the lightly-loaded ``1/P`` values; the Fisher-information
    variance is ``Var(x_v) = c0_s / (2 T c2_s)``. Multiply by the
    empirically calibrated ``_CAPON_SIGMA_K`` (tuned at SNR = 20 dB so
    median claimed sigma matches empirical std within +/- 5 %; the same
    constant satisfies the +/-20% honesty band at SNR in {10, 20, 30}
    dB on the canonical UCA-4 scenario).
11. Return a ``BearingReport`` with ``method=Capability.L2_MVDR_NULL``;
    on any guard failure, return ``None`` (Invariant 4 surface).

The Capon peak is theoretically broader than MUSIC at the same SNR
(MUSIC has super-resolution; Capon does not). The honesty test
confirms each estimator reports *its own* sigma honestly, which is the
load-bearing property fusion weighting relies on
(``INTERFACES.md`` Section 3 on ``BearingReport.azimuth_sigma_deg``).

WHY SPLIT LOADING FOR PEAK VS SIGMA
-----------------------------------
The polyfit-cov approach used in L1 (per-heading IQ blocks supply honest
per-point noise; the residual variance from a 7-point fit scales
correctly with SNR) does not transfer to Capon: all 7 scan samples come
from the *same* covariance R, so the within-realisation residuals are
dominated by the higher-order non-quadratic shape of ``1/P`` rather than
by statistical noise. The polyfit cov is essentially SNR-blind and
under-reports sigma by 3-4 orders of magnitude.

The Fisher-information variance ``c0 / (2 T c2)`` is the standard
asymptotic CRB-like estimator for Capon: it is the inverse curvature
of ``-log P_capon`` at the peak (since for a parabola fit to ``1/P``,
``d^2 log P / dtheta^2 |peak = -2 c2 / c0``), scaled by ``1/T`` for
the snapshot count. It scales correctly with SNR.

However, at high SNR with the default loading 1e-3, the diagonal load
itself dominates the noise floor of ``R^{-1}`` and inflates ``c0``
(=``1/P_peak``) so the Fisher estimate is biased high. Re-evaluating
the local curvature on R with a much smaller loading (1e-6 of
``trace/N``) removes that bias while still guaranteeing invertibility.
The cost is one extra ``inv`` of an N x N matrix per estimate, which
is negligible for N = 2..8. See ``.claude/scratchpad/ws-b-2026-05-16.md``
for the empirical justification (200--400 trial Monte Carlo at SNR
in {10, 20, 30} dB).

FAILURE MODES (Invariant 4 surface)
-----------------------------------
``estimate`` returns ``None`` rather than fabricate a confident bearing
when:

* ``set_timestamp`` was not called before this ``estimate``.
* The bound receiver reports ``is_calibrated == False``.
* The block has fewer than 2 channels or fewer than ``4 * n_channels``
  samples (covariance too underdetermined to be stable).
* The condition number of ``R_loaded`` exceeds ``1e8`` (loading failed).
* The Capon spectrum has no clear peak (``max(P) < 3 * median(P)``).
* The parabolic fit fails (rank-deficient cov, wrong curvature sign,
  out-of-window vertex, non-positive ``Var(x_v)``).
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt
from rfmesh_contracts import (  # type: ignore[import-untyped, unused-ignore]
    ArrayConfig,
    ArrayGeometry,
    BearingReport,
    Capability,
    CoherentIQBlock,
    CoherentReceiver,
    GeodeticPosition,
)

from rfmesh_dsp.array_covariance import forward_backward_smooth, sample_covariance
from rfmesh_dsp.array_manifold import steering_matrix

# Number of 1/P samples included in the local quadratic fit. Symmetric
# around the argmax (3 each side). With 7 samples and 3 polynomial
# parameters the fit has 4 residual degrees of freedom, matching the L1
# estimator's choice for the same statistical reason: enough degrees of
# freedom to make the polyfit covariance well-conditioned without
# extending the fit window past the parabolic-approximation regime of the
# Capon peak.
_PARABOLA_FIT_N_POINTS = 7
# Minimum channel count for the covariance to admit a meaningful inverse;
# 1 channel produces a scalar and a 2x2 sample R from 2 channels is the
# smallest case where R_inv is even defined for phase DF.
_MIN_CHANNELS = 2
# Lower bound on the snapshot count per channel for a stable sample
# covariance. With T < 4 * N the matrix is heavily rank-deficient and no
# amount of loading rescues a Capon peak from it.
_MIN_SAMPLES_PER_CHANNEL_FACTOR = 4
# Condition-number ceiling for R_loaded. Beyond this the diagonal loading
# has failed to stabilise the inverse and the Capon spectrum is no longer
# trustworthy.
_MAX_CONDITION_NUMBER = 1e8
# Peak-prominence gate: max(P_capon) must exceed ``_PEAK_PROMINENCE_RATIO``
# times median(P_capon) for the peak to count. Coarse, deliberately --
# Capon's broad peak is much taller than its median floor even at modest
# SNR, so the gate rejects only genuinely flat spectra.
_PEAK_PROMINENCE_RATIO = 3.0
# Slack on the vertex-inside-window check: a vertex fractionally past the
# half-window edge is still trusted (numerical noise), beyond it is not.
_VERTEX_BOUND_SLACK_DEG = 1e-6
# Diagonal-loading scale used only for the sigma curvature evaluation
# (see the module docstring "WHY SPLIT LOADING FOR PEAK VS SIGMA"). Small
# enough that at high SNR the loading does not dominate the noise floor
# of R_inv (which would bias the curvature down and the sigma up), large
# enough to guarantee invertibility even when R's smallest eigenvalue is
# numerically tiny.
_SIGMA_LOADING_FACTOR = 1e-6
# Empirical sigma calibration constant. Empirically validated against a
# 400-trial Monte Carlo at SNR in {10, 20, 30} dB on the canonical UCA-4
# / r=lambda/4 / 4096-sample scenario: K_capon = 1.0 keeps the median
# ratio (empirical_std / median_claimed_sigma) within [0.97, 0.98] at
# all three SNRs, well inside the +/- 20 % honesty band. Re-derive when
# the canonical scenario, the fit window, or the loading factors change;
# the Monte Carlo lives in ``tests/test_l2_mvdr_sigma_honesty.py``.
_CAPON_SIGMA_K = 1.0

_DEG_FULL_CIRCLE = 360.0
_DEG_HALF_CIRCLE = 180.0
_SPEED_OF_LIGHT_M_PER_S = 299_792_458.0
_TWO = 2


class L2MvdrEstimator:
    """L2 Capon-spectrum ``BearingEstimator`` (phase-coherent array).

    Conforms to ``rfmesh_contracts.BearingEstimator`` structurally
    (``method`` + ``estimate``) and adds the L2-specific ``set_timestamp``
    method that the node runtime calls to arm an emission window before
    each ``estimate(coherent_block)`` call. The estimator binds to the
    coherent receiver at construction so it can read ``is_calibrated``
    itself (no inputs from the runtime carry calibration state) -- the
    L2 DSP refuses to emit on uncalibrated coherent IQ
    (Invariant 4, surfaced explicitly).

    A single instance is reused across blocks. ``set_timestamp`` arms one
    emission; ``estimate`` finalises it and re-arms the instance so the
    next ``set_timestamp -> estimate`` cycle proceeds from a clean state.
    """

    #: Capability this estimator implements. Satisfies the
    #: ``BearingEstimator.method`` property on the Protocol side.
    method: Capability = Capability.L2_MVDR_NULL

    def __init__(
        self,
        *,
        node_id: str,
        node_position: GeodeticPosition,
        receiver: CoherentReceiver,
        array_config: ArrayConfig,
        operating_frequency_hz: float,
        diagonal_loading_factor: float = 1e-3,
        use_forward_backward: bool = False,
        scan_step_deg: float = 0.5,
    ) -> None:
        """Bind node identity, array geometry, and Capon parameters.

        Args:
            node_id: Stable ``NodeConfig.node_id`` of the producing node;
                carried verbatim onto every emitted ``BearingReport``.
            node_position: Position of the node; carried onto every
                emitted ``BearingReport``.
            receiver: The phase-coherent receiver the runtime reads
                blocks from. Bound here so the estimator can consult
                ``is_calibrated`` directly -- the contract for L2 DSP
                explicitly forbids emitting on uncalibrated streams.
            array_config: The contract ``ArrayConfig`` describing the
                array geometry; used to derive element positions in the
                array-local frame and pre-compute the scan steering
                matrix.
            operating_frequency_hz: RF carrier frequency, Hz; sets the
                wavelength for the steering vectors.
            diagonal_loading_factor: Loading scale ``epsilon = factor *
                trace(R) / N`` applied to ``R`` before inversion. Default
                ``1e-3`` is the empirically validated stable choice for
                N = 2..8 arrays at SNR >= 0 dB.
            use_forward_backward: If True, apply ``(R + J R* J) / 2``
                before loading. Default False -- single-emitter v0.1
                does not need coherent-source de-correlation.
            scan_step_deg: Angular resolution of the Capon scan, degrees.
                Default 0.5 deg -- 720 samples over [0, 360), matching
                the ``raw_pseudospectrum`` convention in INTERFACES.md
                section 3.
        """
        if operating_frequency_hz <= 0.0:
            msg = f"operating_frequency_hz must be > 0 (got {operating_frequency_hz})."
            raise ValueError(msg)
        if diagonal_loading_factor <= 0.0:
            msg = f"diagonal_loading_factor must be > 0 (got {diagonal_loading_factor})."
            raise ValueError(msg)
        if scan_step_deg <= 0.0 or scan_step_deg >= _DEG_FULL_CIRCLE:
            msg = f"scan_step_deg must be in (0, 360) (got {scan_step_deg})."
            raise ValueError(msg)

        self._node_id = node_id
        self._node_position = node_position
        self._receiver = receiver
        self._array_config = array_config
        self._operating_frequency_hz = float(operating_frequency_hz)
        self._loading_factor = float(diagonal_loading_factor)
        self._use_forward_backward = bool(use_forward_backward)
        self._scan_step_deg = float(scan_step_deg)

        wavelength_m = _SPEED_OF_LIGHT_M_PER_S / self._operating_frequency_hz
        self._wavelength_m = wavelength_m

        element_positions_m = _array_config_to_positions_m(array_config)
        self._element_positions_m = element_positions_m

        scan_azimuths_deg = np.arange(0.0, _DEG_FULL_CIRCLE, self._scan_step_deg, dtype=np.float64)
        self._scan_azimuths_deg = scan_azimuths_deg
        # Pre-compute the (N, K) steering matrix once; the scan grid is
        # static for the estimator's lifetime so this saves an
        # ``np.exp`` per ``estimate`` call.
        self._scan_steering = steering_matrix(
            geometry=array_config.geometry,
            element_positions_m=element_positions_m,
            azimuths_rad=np.deg2rad(scan_azimuths_deg),
            wavelength_m=wavelength_m,
        )

        self._pending_t_unix_ns: int | None = None

    def set_timestamp(self, t_unix_ns: int) -> None:
        """Arm the next ``estimate`` with the acquisition timestamp.

        Per ``INTERFACES.md`` Section 3 the ``BearingReport.t_unix_ns``
        is the acquisition timestamp; for L2 the natural value is the
        start of the coherent block the runtime is about to ``read``.
        Resets any state left from a prior cycle that did not consume
        its timestamp.
        """
        if t_unix_ns <= 0:
            msg = f"set_timestamp: t_unix_ns must be > 0 (got {t_unix_ns})."
            raise ValueError(msg)
        self._pending_t_unix_ns = t_unix_ns

    def estimate(self, samples: CoherentIQBlock) -> BearingReport | None:
        """Compute the Capon spectrum on ``samples`` and emit a ``BearingReport`` or ``None``.

        ``samples`` is a 2-D ``(n_channels, n_samples)`` complex64 block
        (see ``CoherentIQBlock`` in INTERFACES.md). The pending timestamp
        is consumed and cleared at the end of the call regardless of
        outcome so a failed estimate does not leak state into the next
        cycle.
        """
        t = self._pending_t_unix_ns
        self._pending_t_unix_ns = None
        if t is None:
            return None
        if not self._receiver.is_calibrated:
            return None

        block = np.asarray(samples)
        if block.ndim != _TWO:
            msg = f"estimate: samples must be 2-D (n_channels, n_samples); got ndim={block.ndim}."
            raise ValueError(msg)
        n_channels, n_samples = block.shape
        if n_channels < _MIN_CHANNELS:
            return None
        if n_samples < _MIN_SAMPLES_PER_CHANNEL_FACTOR * n_channels:
            return None
        if n_channels != self._element_positions_m.shape[0]:
            msg = (
                "estimate: samples have "
                f"{n_channels} channels but array_config describes "
                f"{self._element_positions_m.shape[0]}."
            )
            raise ValueError(msg)

        fit_result = self._scan_and_fit(block)
        if fit_result is None:
            return None
        azimuth_deg, sigma_az_deg, snr_db = fit_result

        return BearingReport(  # type: ignore[no-any-return, unused-ignore]
            node_id=self._node_id,
            t_unix_ns=t,
            node_position=self._node_position,
            azimuth_deg=azimuth_deg,
            azimuth_sigma_deg=sigma_az_deg,
            method=Capability.L2_MVDR_NULL,
            snr_db=snr_db,
        )

    def compute_pseudospectrum(self, samples: CoherentIQBlock) -> np.ndarray:
        """Return the raw Capon pseudospectrum over the scan grid.

        Exposed for the golden-file test and diagnostic plots only;
        does not consume the pending timestamp and does not check
        ``is_calibrated`` -- caller's responsibility. Uses the peak-side
        loading (``diagonal_loading_factor``) so the returned spectrum
        is what the runtime would peak-find on. Returns an empty array
        if the loaded covariance fails the cond-number gate.
        """
        block = np.asarray(samples)
        if block.ndim != _TWO:
            msg = (
                "compute_pseudospectrum: samples must be 2-D "
                f"(n_channels, n_samples); got ndim={block.ndim}."
            )
            raise ValueError(msg)
        cov = self._build_covariance(block)
        r_inv = self._invert_with_loading(cov, self._loading_factor, gate_condition=True)
        if r_inv is None:
            return np.empty(0, dtype=np.float64)
        return self._capon_spectrum(r_inv)

    @property
    def scan_azimuths_deg(self) -> np.ndarray:
        """Read-only view of the scan azimuth grid (degrees, [0, 360))."""
        return self._scan_azimuths_deg

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_covariance(self, block: np.ndarray) -> np.ndarray:
        """Sample R from the block and optionally apply forward-backward smoothing.

        The unloaded R is the substrate for both the peak inverse and the
        sigma inverse (each applies its own loading on top). Computed once
        per ``estimate`` to avoid two passes over the IQ block.
        """
        cov = sample_covariance(block)
        if self._use_forward_backward:
            cov = forward_backward_smooth(cov)
        return cov

    def _invert_with_loading(
        self,
        cov: np.ndarray,
        loading_factor: float,
        *,
        gate_condition: bool,
    ) -> np.ndarray | None:
        """Diagonally load ``cov`` and invert; optionally gate on cond number.

        ``loading_factor`` is scaled to ``epsilon = factor * trace(R) / N``
        so the loading tracks the magnitude of R rather than being an
        absolute number. Using only the real part of the trace defends
        against tiny imaginary residuals from finite-T rounding;
        ``trace(R)`` is real-valued for any Hermitian R.

        With ``gate_condition`` True the cond-number ceiling
        ``_MAX_CONDITION_NUMBER`` is applied (the peak-detection path);
        with False the gate is skipped (the sigma path, which uses a
        much lighter loading that may push cond slightly higher but is
        still bounded by the eigenvalue structure of R itself).
        """
        n_channels = cov.shape[0]
        trace_r = float(np.real(np.trace(cov)))
        epsilon = loading_factor * trace_r / float(n_channels)
        loaded = cov + epsilon * np.eye(n_channels, dtype=np.complex128)
        if gate_condition:
            cond = float(np.linalg.cond(loaded))
            if not math.isfinite(cond) or cond > _MAX_CONDITION_NUMBER:
                return None
        try:
            r_inv: np.ndarray = np.linalg.inv(loaded)
        except np.linalg.LinAlgError:
            return None
        return r_inv

    def _capon_spectrum(self, r_inv: np.ndarray) -> np.ndarray:
        """Compute the Capon pseudospectrum over the pre-computed scan grid.

        ``D_k = a_k^H R_inv a_k``, ``P_k = 1 / Re(D_k)``. Sum is over the
        two array-channel axes; the result is a 1-D vector of length
        ``len(scan_azimuths_deg)``. Where ``Re(D_k) <= 0`` (numerical
        artefacts of an ill-conditioned R_inv) we substitute the column
        with the spectrum's minimum positive value so the array remains
        a valid spectrum; the caller will reject such cases via the
        prominence gate, but the explicit fill keeps the array
        downstream-safe.
        """
        steering = self._scan_steering  # (N, K)
        # einsum 'ik,ij,jk->k': sum_{i,j} A.conj()[i,k] * R_inv[i,j] * A[j,k]
        # = sum_i A.conj()[i,k] * (R_inv @ A)[i,k] = (A^H R_inv A)_{k,k}.
        denom = np.einsum("ik,ij,jk->k", steering.conj(), r_inv, steering)
        denom_real = np.real(denom)
        positive = denom_real > 0.0
        if not np.any(positive):
            # Defensive: should not be reachable under the cond-number
            # gate, but keeps the return type consistent.
            full: np.ndarray = np.full_like(denom_real, np.finfo(np.float64).tiny)
            return full
        spectrum: np.ndarray = np.empty_like(denom_real)
        spectrum[positive] = 1.0 / denom_real[positive]
        if not np.all(positive):
            spectrum[~positive] = float(np.min(spectrum[positive]))
        return spectrum

    def _scan_and_fit(self, block: np.ndarray) -> tuple[float, float, float] | None:
        """Run the full Capon pipeline on ``block``.

        Returns ``(azimuth_deg, sigma_az_deg, snr_db)`` on success, or
        ``None`` if any of the guards trips. Sub-stage helpers
        (``_locate_peak``, ``_fit_peak_vertex``, ``_estimate_sigma``)
        keep each method below the workspace return-count budget; the
        logic is unchanged from a single linear pipeline.

        Two inversions are performed on the same sample R: a heavily
        loaded one for peak detection (cond-number gated) and a lightly
        loaded one for the Fisher-information curvature used in the
        sigma estimate. The split is documented in the module docstring
        under "WHY SPLIT LOADING FOR PEAK VS SIGMA".
        """
        cov = self._build_covariance(block)
        n_samples = block.shape[1]
        peak_info = self._locate_peak(cov)
        if peak_info is None:
            return None
        spectrum_peak, peak_idx, peak_p, median_p = peak_info
        vertex_info = self._fit_peak_vertex(spectrum_peak, peak_idx)
        if vertex_info is None:
            return None
        peak_az_deg, vertex_x, x_offsets, idxs = vertex_info
        sigma_az_deg = self._estimate_sigma(cov, idxs, x_offsets, n_samples)
        if sigma_az_deg is None:
            return None
        # SNR proxy: 10 log10(peak / median) of the Capon spectrum, which
        # tracks emitter-to-noise power at this node and is the natural
        # diagnostic to surface on ``BearingReport.snr_db``.
        snr_db = 10.0 * math.log10(peak_p / median_p)
        azimuth_deg = (peak_az_deg + vertex_x) % _DEG_FULL_CIRCLE
        return azimuth_deg, sigma_az_deg, snr_db

    def _locate_peak(
        self,
        cov: np.ndarray,
    ) -> tuple[np.ndarray, int, float, float] | None:
        """Find the heavily-loaded Capon peak.

        Returns ``(spectrum, peak_idx, peak_p, median_p)`` on success or
        ``None`` if the loaded inverse fails the cond-number gate or the
        peak prominence is below the rejection ratio.
        """
        r_inv_peak = self._invert_with_loading(cov, self._loading_factor, gate_condition=True)
        if r_inv_peak is None:
            return None
        spectrum = self._capon_spectrum(r_inv_peak)
        peak_idx = int(np.argmax(spectrum))
        median_p = float(np.median(spectrum))
        peak_p = float(spectrum[peak_idx])
        if peak_p < _PEAK_PROMINENCE_RATIO * median_p:
            return None
        return spectrum, peak_idx, peak_p, median_p

    def _fit_peak_vertex(
        self,
        spectrum: np.ndarray,
        peak_idx: int,
    ) -> tuple[float, float, np.ndarray, np.ndarray] | None:
        """Fit the parabola at the heavily-loaded peak; return sub-grid vertex.

        Returns ``(peak_az_deg, vertex_x, x_offsets, idxs)`` -- the
        peak grid azimuth, the sub-grid offset of the parabola vertex,
        the signed-offset abscissae used by the fit, and the grid
        indices spanning the fit window. Returns ``None`` if the fit is
        ill-conditioned, the curvature has the wrong sign, or the vertex
        falls outside the fit window.
        """
        n_scan = spectrum.size
        half = _PARABOLA_FIT_N_POINTS // 2
        idxs = (np.arange(peak_idx - half, peak_idx + half + 1) % n_scan).astype(np.intp)
        peak_az_deg = float(self._scan_azimuths_deg[peak_idx])
        # Wrap the fit-window azimuths to signed offsets in (-180, 180]
        # so the fit works across the 0/360 seam (mirrors the L1 trick).
        raw_offsets = self._scan_azimuths_deg[idxs] - peak_az_deg + _DEG_HALF_CIRCLE
        x_offsets = (raw_offsets % _DEG_FULL_CIRCLE) - _DEG_HALF_CIRCLE
        denom_peak = spectrum[idxs]
        if np.any(denom_peak <= 0.0):
            return None
        y_peak = 1.0 / denom_peak
        fit_peak = _fit_parabola(x_offsets, y_peak)
        if fit_peak is None:
            return None
        c2_p, c1_p, _c0_p = fit_peak
        if c2_p <= 0.0:
            return None
        vertex_x = -c1_p / (2.0 * c2_p)
        if abs(vertex_x) > half * self._scan_step_deg + _VERTEX_BOUND_SLACK_DEG:
            return None
        return peak_az_deg, vertex_x, x_offsets, idxs

    def _estimate_sigma(
        self,
        cov: np.ndarray,
        idxs: np.ndarray,
        x_offsets: np.ndarray,
        n_samples: int,
    ) -> float | None:
        """Compute the Fisher-information sigma from the lightly-loaded fit.

        The sigma path re-evaluates the Capon spectrum on the same 7-point
        window using a much smaller diagonal loading; the curvature is then
        unbiased by the peak-side loading at high SNR. See the module
        docstring "WHY SPLIT LOADING FOR PEAK VS SIGMA" for the rationale.
        """
        r_inv_sigma = self._invert_with_loading(cov, _SIGMA_LOADING_FACTOR, gate_condition=False)
        if r_inv_sigma is None:
            return None
        spectrum_sigma = self._capon_spectrum_at(r_inv_sigma, idxs)
        if np.any(spectrum_sigma <= 0.0):
            return None
        fit_sigma = _fit_parabola(x_offsets, 1.0 / spectrum_sigma)
        if fit_sigma is None:
            return None
        c2_s, _c1_s, c0_s = fit_sigma
        if c2_s <= 0.0 or c0_s <= 0.0:
            return None
        # Fisher-information variance: c0_s / (2 T c2_s). The asymptotic
        # MVDR CRB-like estimator (see module docstring).
        var_vertex_x = c0_s / (2.0 * float(n_samples) * c2_s)
        if not math.isfinite(var_vertex_x) or var_vertex_x <= 0.0:
            return None
        return _CAPON_SIGMA_K * math.sqrt(var_vertex_x)

    def _capon_spectrum_at(self, r_inv: np.ndarray, indices: np.ndarray) -> np.ndarray:
        """Evaluate the Capon spectrum at the given grid indices.

        Cheaper than the full ``_capon_spectrum`` when only a small
        neighbourhood is needed (the sigma-side fit window).
        """
        sub = self._scan_steering[:, indices]
        denom = np.einsum("ik,ij,jk->k", sub.conj(), r_inv, sub)
        denom_real = np.real(denom)
        # Defensive: ill-conditioned R_inv may produce non-positive denominators;
        # callers (the sigma fit) reject the whole window in that case.
        with np.errstate(divide="ignore", invalid="ignore"):
            spectrum = np.where(denom_real > 0.0, 1.0 / denom_real, 0.0)
        return spectrum


def _array_config_to_positions_m(array_config: ArrayConfig) -> np.ndarray:
    """Derive ``(N, 2) float64`` array-local element positions from ``ArrayConfig``.

    Matches the simulator's ``ArraySpec`` convention byte-for-byte so the
    DSP estimator's scanned steering vectors align with the IQ the
    simulator produces:

    * ``ULA``: elements on the y-axis, channel 0 at the origin, channel
      ``i`` at ``(0, i * element_spacing_m)``.
    * ``UCA``: elements on a circle of radius ``element_spacing_m`` (the
      contract's parametric ``element_spacing_m`` doubles as the UCA
      radius -- see ``INTERFACES.md`` section 4 ``ArrayConfig``),
      channel ``i`` at angle ``alpha_i = 2 pi i / N`` from +x.
    * ``CUSTOM``: positions taken from ``element_positions_m`` verbatim.
    """
    geometry = array_config.geometry
    n = array_config.n_elements
    if geometry is ArrayGeometry.ULA:
        spacing = array_config.element_spacing_m
        # ArrayConfig._geometry_consistency guarantees spacing is set for
        # ULA/UCA; the explicit check satisfies mypy strict here.
        if spacing is None:
            msg = "ArrayConfig(geometry=ULA) requires element_spacing_m."
            raise ValueError(msg)
        positions = np.zeros((n, _TWO), dtype=np.float64)
        positions[:, 1] = np.arange(n, dtype=np.float64) * spacing
        return positions
    if geometry is ArrayGeometry.UCA:
        radius = array_config.element_spacing_m
        if radius is None:
            msg = "ArrayConfig(geometry=UCA) requires element_spacing_m (UCA radius)."
            raise ValueError(msg)
        alphas = 2.0 * math.pi * np.arange(n, dtype=np.float64) / float(n)
        positions = np.empty((n, _TWO), dtype=np.float64)
        positions[:, 0] = radius * np.cos(alphas)
        positions[:, 1] = radius * np.sin(alphas)
        return positions
    # CUSTOM
    if array_config.element_positions_m is None:
        msg = "ArrayConfig(geometry=CUSTOM) requires element_positions_m."
        raise ValueError(msg)
    positions = np.asarray(array_config.element_positions_m, dtype=np.float64)
    if positions.shape != (n, _TWO):
        msg = f"ArrayConfig.element_positions_m has shape {positions.shape}; expected ({n}, 2)."
        raise ValueError(msg)
    return positions


def _fit_parabola(
    x: npt.NDArray[np.float64],
    y: npt.NDArray[np.float64],
) -> tuple[float, float, float] | None:
    """Quadratic least-squares fit; returns ``(c2, c1, c0)`` or ``None``.

    Ordered to match ``np.polyfit``'s highest-degree-first convention.
    The Fisher-information sigma path needs the coefficients only, not
    the polyfit residual covariance (see the module docstring "WHY SPLIT
    LOADING FOR PEAK VS SIGMA" for why the polyfit cov is SNR-blind for
    Capon).
    """
    try:
        coeffs = np.polyfit(x, y, 2)
    except (np.linalg.LinAlgError, ValueError):
        return None
    if not np.all(np.isfinite(coeffs)):
        return None
    return float(coeffs[0]), float(coeffs[1]), float(coeffs[2])
