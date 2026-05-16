"""L2 MUSIC BearingEstimator.

Implements the L2 capability: subspace direction finding on a phase-coherent
multi-channel IQ block. ``estimate(samples)`` consumes a
``CoherentIQBlock`` of shape ``(N, T)``, eigendecomposes the sample
covariance ``R``, scans the MUSIC pseudospectrum
``P(theta) = 1 / || E_n^H a(theta) ||^2`` over azimuth, parabolic-fits the
*denominator* (which is locally degree-2 in theta about the peak), and
emits a ``BearingReport`` with ``method = Capability.L2_MUSIC`` and a
fit-driven 1-sigma azimuth uncertainty.

THE SWEEP-AWARENESS GAP IN ``BearingEstimator``
-----------------------------------------------
Unlike L1, MUSIC does NOT sweep: one ``read_coherent(T)`` call yields one
``R`` yields one bearing. But the frozen contract
``BearingEstimator.estimate(samples) -> BearingReport | None`` has no
timestamp channel. Resolving this *without* a contract change
(Invariant 1):

* The class satisfies the Protocol's ``method`` and ``estimate`` surface
  exactly. ``isinstance(estimator, BearingEstimator)`` is True.
* It additionally exposes an L2-specific ``set_timestamp(t_unix_ns)``
  hook the node runtime calls immediately before each ``estimate``.
* If a Protocol-only consumer (unaware of L2) calls ``estimate`` without
  ``set_timestamp`` first, ``estimate`` raises ``RuntimeError`` -- it
  does NOT silently fabricate a ``time.time_ns()`` value (Invariant 4).

CALIBRATION TRUST CONTRACT (Invariant 4 surface)
------------------------------------------------
The estimator's constructor binds to a ``CoherentReceiver`` instance and
reads ``is_calibrated`` on every ``estimate`` call. An uncalibrated
coherent stream causes ``estimate`` to return ``None``; the runtime never
needs to be trusted to plumb a "calibrated?" flag (the honesty guarantee
sits at the source, not at the consumer).

SIGMA RECIPE -- Stoica-Nehorai asymptotic MUSIC variance
--------------------------------------------------------
The L1 recipe (parabolic fit, polyfit-cov, chi-square bias factor) does
NOT generalise to MUSIC. The MUSIC denom is so close to *exactly*
quadratic locally that polyfit's residual variance reports the
deviation from a parabola, not the estimator's actual uncertainty --
``polyfit(cov=True)`` was empirically off by ~1500x at SNR=20 dB on
the canonical UCA-4 scenario.

The honest sigma comes from the standard MUSIC asymptotic-variance
result for one source (Stoica & Nehorai, *MUSIC, Maximum Likelihood,
and Cramer-Rao Bound*, IEEE Trans. ASSP, 1989):

    Var(theta_hat) [rad^2] = sigma_n^2 / (2T * lambda_s * Re{d^H P_n d})

with:

* ``sigma_n^2`` -- the mean of the smallest ``N - n_sources`` eigenvalues
  of the sample covariance R (the noise-subspace floor).
* ``T`` -- snapshot count (``samples.shape[1]``).
* ``lambda_s`` -- the largest eigenvalue of R (which equals
  ``a^H R a = signal_power + sigma_n^2`` for the source-aligned unit-
  norm steering vector; equivalent to ``1 / (a^H R^-1 a)`` in this
  one-source case).
* ``P_n = E_n E_n^H`` -- the noise-subspace projector.
* ``d = d a / d theta`` -- the steering-vector derivative at the
  estimated angle.

The recipe in this module:

1. ``polyfit(x_deg, denom, 2)`` over the 7-point window centred on the
   coarse argmax recovers the sub-grid vertex ``x_v = -c1 / (2 c2)`` and
   the curvature ``c2``. With ``x`` in degrees, ``denom`` near the peak
   is ``c2 * (theta - theta*)^2``, and the second derivative
   ``d^2(denom)/d(theta_deg)^2 = 2 c2``. From the chain rule:
   ``c2 = Re{d_deg^H P_n d_deg}``, i.e. ``c2`` IS the geometric factor
   the Stoica-Nehorai formula needs, in (1/deg^2) units. The rad/deg
   factors cancel exactly when the formula is converted into deg^2:

       Var(theta_hat) [deg^2] = sigma_n^2 / (2T * lambda_s * c2)

   so the code reads c2 off the fit, reads ``sigma_n^2`` and
   ``lambda_s`` off the eigenvalues, and combines them directly. No
   unit conversion; no fit-covariance.

2. ``_SIGMA_HONESTY_FACTOR`` is an *empirical* multiplicative correction
   on the variance, calibrated against the honesty Monte-Carlo
   (``tests/test_l2_music_sigma_honesty.py``) so the median claimed
   sigma matches the empirical std of recovered azimuths within +/-5 %
   at SNR=20 dB and within +/-20 % across SNR in {10, 20, 30} dB.
   Calibrated 2026-05-16 on the canonical UCA-4 / r=lambda/4 / 915 MHz /
   4096-sample scenario; see "Calibration procedure" below if the
   honesty band ever breaks.

   Calibration procedure (re-run only when the simulator's noise/
   calibration model or the manifold convention changes):

   - Set ``_SIGMA_HONESTY_FACTOR = 1.0``.
   - Run the sigma-honesty test at SNR=20 dB; record
     ``ratio = sigma_emp / sigma_claimed``.
   - Set ``_SIGMA_HONESTY_FACTOR = ratio**2`` (variance scale, not
     sigma scale).
   - Re-run at SNR={10, 20, 30} dB; every band must hold within +/-20 %.
   - If no single factor brings all three SNRs within +/-20 %, STOP per
     the ticket and write a scratchpad note. Do NOT widen tolerance.

FAILURE MODES (Invariant 4 surface)
-----------------------------------
``estimate`` returns ``None`` (never a fabricated bearing) when:

* The bound receiver is not calibrated.
* The block has fewer than ``n_sources + 1`` channels (the noise subspace
  would be empty).
* The block has fewer than ``4 * N`` samples (covariance estimate too
  noisy; soft minimum documented in the ticket).
* The largest eigenvalue is not at least 3 dB above the mean of the
  smallest ``N - n_sources`` eigenvalues (no usable signal).
* The parabolic fit fails (rank-deficient, non-finite coefficients, or
  curvature has the wrong sign -- ``c2 <= 0``).
* The fitted vertex lies outside the half-window around the argmax (the
  fit extrapolated; the peak is poorly modelled as a parabola).
* The Stoica-Nehorai variance comes out non-positive or non-finite.

A ``None`` return is *not* an error; it is the honest "no usable bearing
this block".
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
    CoherentReceiver,
    GeodeticPosition,
)

from rfmesh_dsp.array_covariance import forward_backward_smooth, sample_covariance
from rfmesh_dsp.array_manifold import steering_matrix

# Speed of light, m/s. Inlined rather than imported from scipy.constants
# to keep the module's import footprint tight (Invariant 5 friendliness).
_SPEED_OF_LIGHT_M_PER_S = 299_792_458.0
# 7-point window around the coarse argmax for the parabolic fit on the
# MUSIC denominator. The fit's role is to (a) recover the sub-grid vertex
# x_v = -c1/(2 c2) and (b) extract c2 = d^H P_n d (1/deg^2) for the
# Stoica-Nehorai sigma formula. A wider window starts to bend away from
# the local quadratic; a narrower window has too few points for a stable
# c2 against discretisation noise.
_PARABOLA_FIT_N_POINTS = 7
# Eigenvalue-ratio gate (3 dB): the largest eigenvalue of R must exceed
# the mean of the smallest ``N - n_sources`` eigenvalues by this many dB
# for MUSIC to declare a usable signal. Below this the noise subspace is
# not meaningfully separated from the signal and any peak is multipath
# /coincidence -- return None (Invariant 4).
_SIGNAL_EIGENVALUE_RATIO_DB = 3.0
# Soft minimum on snapshots-per-channel for the covariance estimate to be
# stable. With T < 4N the sample covariance is noisy enough that the
# eigendecomposition's signal/noise subspace split is unreliable.
_MIN_SAMPLES_PER_CHANNEL = 4
# Full circle in degrees. Named so it is obvious next to azimuth-wrap code.
_DEG_FULL_CIRCLE = 360.0
_DEG_HALF_CIRCLE = 180.0
# Expected dimensionality for a coherent IQ block: (n_channels, n_samples).
_COHERENT_BLOCK_NDIM = 2
# Slack on the vertex-inside-window check (same role as in L1): a vertex
# fractionally past the half-window edge is still trusted (numerical
# noise), beyond it the fit extrapolated and we refuse.
_VERTEX_BOUND_SLACK_DEG = 1e-6
# Empirical variance scale (see module docstring "SIGMA RECIPE" step 2).
# Calibrated 2026-05-16 against test_l2_music_sigma_honesty.py at SNR=20
# dB on the canonical UCA-4, r/lambda=0.25, 4096-sample scenario:
# raw_ratio = sigma_emp / sigma_claimed = 1.0614 -> factor = ratio^2 =
# 1.127. With this factor the median-claimed-sigma vs empirical-sigma
# ratios at SNR = {10, 20, 30} dB are {1.015, 1.000, 1.057} -- comfortably
# inside the +/-20 % band; SNR=20 dB is exactly centred (the calibration
# point) and the others are within +/-5.7 %.
_SIGMA_HONESTY_FACTOR = 1.127


class L2MusicEstimator:
    """L2 MUSIC subspace BearingEstimator (phase-coherent multi-channel).

    Conforms to ``rfmesh_contracts.BearingEstimator`` structurally
    (``method`` + ``estimate``) and adds the L2-specific ``set_timestamp``
    hook the node runtime calls immediately before each ``estimate``.

    A single instance is reused across coherent reads. ``set_timestamp``
    must be called *every* cycle (no implicit "now" fallback). ``estimate``
    re-arms the instance on every call so the next set_timestamp/estimate
    cycle proceeds from clean state, whether or not a bearing was emitted.
    """

    #: Capability this estimator implements. Class attribute satisfies the
    #: ``BearingEstimator.method`` property on the Protocol side.
    method: Capability = Capability.L2_MUSIC

    def __init__(
        self,
        *,
        node_id: str,
        node_position: GeodeticPosition,
        receiver: CoherentReceiver,
        array_config: ArrayConfig,
        operating_frequency_hz: float,
        n_sources: int = 1,
        use_forward_backward: bool = False,
        scan_step_deg: float = 0.5,
    ) -> None:
        """Bind node identity, receiver, and array geometry; precompute the scan manifold.

        Args:
            node_id: Stable ``NodeConfig.node_id`` of the producing node;
                carried verbatim onto every emitted ``BearingReport``.
            node_position: Position of the node at the time of capture;
                carried onto every emitted ``BearingReport``.
            receiver: The ``CoherentReceiver`` this estimator consumes. The
                estimator reads ``receiver.is_calibrated`` on every
                ``estimate`` call and refuses (returns ``None``) on an
                uncalibrated stream. The constructor does NOT call
                ``open`` / ``configure`` / ``calibrate`` -- the node
                runtime owns the receiver's lifecycle.
            array_config: Frozen array description. Element positions in
                the array-local frame are derived here, once, from
                ``geometry`` + ``n_elements`` + ``element_spacing_m`` (for
                ULA/UCA) or ``element_positions_m`` (for CUSTOM).
            operating_frequency_hz: Carrier frequency, Hz. Used to
                compute the wavelength for the steering manifold.
            n_sources: Assumed number of signal sources. v0.1 hardcodes 1;
                the constructor parameter exists so a future ticket can
                raise it (with an AIC / MDL detector) without an API
                change. Must satisfy ``1 <= n_sources < n_elements``.
            use_forward_backward: When True, ``estimate`` applies
                forward-backward smoothing to the sample covariance
                before eigendecomposition. Default False; FB smoothing is
                valuable in coherent-source multipath but costs the array
                aperture's full rank and is left opt-in per scenario.
            scan_step_deg: Coarse azimuth-scan step, degrees. Default
                0.5 deg -> 720 grid points across [0, 360). Smaller steps
                grow the precomputed steering matrix linearly; 0.5 deg is
                a good compromise between sub-grid-fit reach and memory.

        Raises:
            ValueError: If ``operating_frequency_hz`` <= 0,
                ``scan_step_deg`` <= 0, ``n_sources`` < 1 or
                >= ``array_config.n_elements``, or the array geometry's
                element-position derivation fails.
        """
        if operating_frequency_hz <= 0.0:
            msg = f"operating_frequency_hz must be > 0 (got {operating_frequency_hz})."
            raise ValueError(msg)
        if scan_step_deg <= 0.0:
            msg = f"scan_step_deg must be > 0 (got {scan_step_deg})."
            raise ValueError(msg)
        if n_sources < 1:
            msg = f"n_sources must be >= 1 (got {n_sources})."
            raise ValueError(msg)
        if n_sources >= array_config.n_elements:
            msg = (
                "n_sources must be < array_config.n_elements (otherwise the "
                f"noise subspace is empty); got n_sources={n_sources}, "
                f"n_elements={array_config.n_elements}."
            )
            raise ValueError(msg)

        self._node_id = node_id
        self._node_position = node_position
        self._receiver = receiver
        self._n_sources = int(n_sources)
        self._n_elements = int(array_config.n_elements)
        self._use_forward_backward = bool(use_forward_backward)
        self._scan_step_deg = float(scan_step_deg)
        self._wavelength_m = _SPEED_OF_LIGHT_M_PER_S / float(operating_frequency_hz)

        self._geometry = array_config.geometry
        self._element_positions_m = _element_positions_from_config(array_config)

        # Precompute the scan grid and the steering matrix once. The
        # steering matrix is (N, K) complex128; for N=4, K=720 that is
        # 23 kB -- cheap to carry per-estimator.
        self._scan_azimuths_deg = np.arange(0.0, _DEG_FULL_CIRCLE, self._scan_step_deg)
        self._scan_azimuths_rad = np.deg2rad(self._scan_azimuths_deg)
        self._steering_matrix = steering_matrix(
            geometry=self._geometry,
            element_positions_m=self._element_positions_m,
            azimuths_rad=self._scan_azimuths_rad,
            wavelength_m=self._wavelength_m,
        )

        # Per-cycle state. set_timestamp() fills _pending_t_unix_ns;
        # estimate() consumes it and clears it back to None so the next
        # cycle MUST call set_timestamp again (no "now" fallback).
        self._pending_t_unix_ns: int | None = None

    def set_timestamp(self, t_unix_ns: int) -> None:
        """Record the timestamp the next ``estimate`` call will stamp on its report.

        Called by the node runtime immediately before every coherent
        ``estimate`` cycle, with the moment-of-capture timestamp (the
        integer-ns ``BearingReport.t_unix_ns`` convention). The estimator
        consumes and clears this value inside ``estimate``; a missed
        ``set_timestamp`` for the next cycle is a hard error rather than
        a silent fall-through to "now".

        Args:
            t_unix_ns: Acquisition timestamp in integer nanoseconds since
                Unix epoch UTC, > 0.

        Raises:
            ValueError: If ``t_unix_ns <= 0``.
        """
        if t_unix_ns <= 0:
            msg = f"set_timestamp: t_unix_ns must be > 0 (got {t_unix_ns})."
            raise ValueError(msg)
        self._pending_t_unix_ns = t_unix_ns

    def estimate(self, samples: np.ndarray) -> BearingReport | None:
        """Produce a ``BearingReport`` from one coherent IQ block, or ``None``.

        Args:
            samples: ``(n_channels, n_samples)`` coherent IQ block, any
                complex dtype (typically ``complex64`` from
                ``CoherentReceiver.read_coherent``).

        Returns:
            A ``BearingReport`` with ``method = Capability.L2_MUSIC`` on
            success; ``None`` on any of the documented failure-mode
            guards (see module docstring).

        Raises:
            RuntimeError: If ``set_timestamp`` was not called this cycle.
                The estimator deliberately does not fabricate a "now"
                fallback (Invariant 4).
            ValueError: If ``samples`` is not a 2-D complex ndarray.
        """
        if self._pending_t_unix_ns is None:
            msg = (
                "L2MusicEstimator.estimate() called before set_timestamp(); "
                "the node runtime must stamp every cycle (no implicit 'now')."
            )
            raise RuntimeError(msg)
        # Consume the pending timestamp regardless of which branch we exit
        # by, so the next cycle's set_timestamp/estimate handshake starts
        # from clean state.
        t_unix_ns = self._pending_t_unix_ns
        self._pending_t_unix_ns = None

        gated = self._gated_pseudospectrum(samples)
        if gated is None:
            return None
        denom, snr_db, signal_eigval, noise_floor, n_snapshots = gated

        fit = self._fit_peak(denom, signal_eigval, noise_floor, n_snapshots)
        if fit is None:
            return None
        azimuth_deg, sigma_az_deg = fit

        return BearingReport(  # type: ignore[no-any-return, unused-ignore]
            node_id=self._node_id,
            t_unix_ns=t_unix_ns,
            node_position=self._node_position,
            azimuth_deg=azimuth_deg,
            azimuth_sigma_deg=sigma_az_deg,
            method=Capability.L2_MUSIC,
            snr_db=snr_db,
        )

    def _gated_pseudospectrum(
        self,
        samples: np.ndarray,
    ) -> tuple[npt.NDArray[np.float64], float, float, float, int] | None:
        """Validate, eigendecompose, gate; return MUSIC pieces or ``None``.

        Bundles the Invariant-4 surface for ``estimate``:

        * receiver-not-calibrated => None
        * wrong channel count, too few samples, non-finite eigenvalues => None
        * lambda_max / mean(lambda_noise) below the 3 dB threshold => None

        Returns ``(denom, snr_db, signal_eigval, noise_floor, n_snapshots)``
        on success. ``snr_db`` (the diagnostic carried on
        ``BearingReport.snr_db``) is
        ``10 * log10((lambda_max - noise_floor) / noise_floor)``; the
        other three feed the Stoica-Nehorai sigma formula in
        ``_fit_peak``. The bearing's *weight* is that sigma; ``snr_db``
        is just for the dashboard.
        """
        if not self._receiver.is_calibrated:
            return None
        block = _validate_coherent_samples(samples, self._n_elements)
        if block is None:
            return None
        if block.shape[1] < _MIN_SAMPLES_PER_CHANNEL * self._n_elements:
            return None
        pieces = self._music_pieces(block)
        if pieces is None:
            return None
        denom, signal_eigval, noise_floor = pieces
        ratio_db = 10.0 * math.log10(signal_eigval / noise_floor)
        if ratio_db < _SIGNAL_EIGENVALUE_RATIO_DB:
            return None
        snr_db = 10.0 * math.log10(
            max(signal_eigval - noise_floor, np.finfo(np.float64).tiny) / noise_floor
        )
        return denom, snr_db, signal_eigval, noise_floor, int(block.shape[1])

    def pseudospectrum(self, samples: np.ndarray) -> npt.NDArray[np.float64]:
        """Return the MUSIC pseudospectrum ``P(theta_k)`` over the scan grid.

        Diagnostic / debug-payload surface. Computes the sample covariance,
        (optionally) forward-backward smooths it, eigendecomposes, builds
        the noise subspace and evaluates
        ``P(theta_k) = 1 / || E_n^H a(theta_k) ||^2`` for every theta on
        ``self._scan_azimuths_deg``. Does NOT apply the calibration or
        eigenvalue-ratio gates that ``estimate`` does -- those gates apply
        to *bearing emission*, not inspection. Used by the node runtime
        to populate ``BearingReport.raw_pseudospectrum`` and by the
        golden-file regression test to compare against the committed
        snapshot.

        Args:
            samples: ``(n_channels, n_samples)`` coherent IQ block.

        Returns:
            ``(K,) float64`` array of ``P(theta_k)`` values.

        Raises:
            ValueError: If ``samples`` is not a 2-D complex ndarray of
                the expected channel count.
        """
        block = _validate_coherent_samples(samples, self._n_elements)
        if block is None:
            # _validate_coherent_samples returns None on channel-count
            # mismatch (a soft failure for ``estimate``); for the debug
            # surface a mismatch is a programmer error so we surface it.
            msg = (
                "L2MusicEstimator.pseudospectrum: samples channel count "
                f"{samples.shape[0]} does not match the bound array's "
                f"n_elements={self._n_elements}."
            )
            raise ValueError(msg)
        pieces = self._music_pieces(block)
        if pieces is None:
            # Hermitian eigendecomposition produced non-finite eigenvalues:
            # surfacing as an error rather than a NaN array keeps callers
            # from silently propagating garbage.
            msg = (
                "L2MusicEstimator.pseudospectrum: eigendecomposition produced "
                "non-finite eigenvalues."
            )
            raise ValueError(msg)
        denom, _signal_eigval, _noise_floor = pieces
        return 1.0 / denom

    def _music_pieces(
        self,
        block: npt.NDArray[np.complex128],
    ) -> tuple[npt.NDArray[np.float64], float, float] | None:
        """Compute the MUSIC denominator and eigenvalue summary statistics.

        Returns ``(denom, signal_eigval, noise_floor)`` where:

        * ``denom`` is the (K,) float64 vector of
          ``|| E_n^H a(theta_k) ||^2`` over the precomputed scan grid;
          ``P(theta_k) = 1 / denom`` is the MUSIC pseudospectrum.
        * ``signal_eigval`` is the largest eigenvalue of ``R``.
        * ``noise_floor`` is the mean of the smallest ``N - n_sources``
          eigenvalues -- the empirical noise-subspace floor used by the
          eigenvalue-ratio gate in ``estimate``.

        Returns ``None`` on basic numerical failure (non-finite or
        non-positive eigenvalues, non-finite denom). The SNR-ratio gate
        is applied by ``estimate``, not here, so ``pseudospectrum`` can
        return the array unconditionally for inspection.
        """
        cov = sample_covariance(block)
        if self._use_forward_backward:
            cov = forward_backward_smooth(cov)
        # eigh returns eigenvalues in ascending order with orthogonal
        # eigenvector columns; both are real-valued for a Hermitian input
        # (the imaginary part is zeroed numerically).
        eigvals, eigvecs = np.linalg.eigh(cov)
        # Descending order so signal eigvals are at index [0, n_sources).
        order = np.argsort(eigvals)[::-1]
        sorted_eigvals = eigvals[order].astype(np.float64, copy=False)
        sorted_eigvecs = eigvecs[:, order]
        signal_eigval = float(sorted_eigvals[0])
        noise_eigvals = sorted_eigvals[self._n_sources :]
        noise_floor = float(np.mean(noise_eigvals))
        if (
            not math.isfinite(signal_eigval)
            or not math.isfinite(noise_floor)
            or signal_eigval <= 0.0
            or noise_floor <= 0.0
        ):
            return None

        noise_subspace = sorted_eigvecs[:, self._n_sources :]
        # projection shape: (N - n_sources, K).
        projection = noise_subspace.conj().T @ self._steering_matrix
        denom = np.sum(np.abs(projection) ** 2, axis=0).astype(np.float64)
        if not np.all(np.isfinite(denom)) or not np.all(denom > 0.0):
            return None
        return denom, signal_eigval, noise_floor

    def _fit_peak(
        self,
        denom: npt.NDArray[np.float64],
        signal_eigval: float,
        noise_floor: float,
        n_snapshots: int,
    ) -> tuple[float, float] | None:
        """Locate the MUSIC peak (argmin of ``denom``) and produce ``(az, sigma)``.

        Uses the parabolic fit on ``denom`` for the sub-grid vertex and
        the Stoica-Nehorai analytic variance for sigma. See the module
        docstring "SIGMA RECIPE" section for the derivation; here, in
        operational terms, ``c2`` from the polyfit is the
        ``d^H P_n d`` term in 1/deg^2 units, and the sigma formula
        reduces to ``Var_deg = sigma_n^2 / (2 T lambda_s c2)``.

        Returns ``(azimuth_deg, sigma_az_deg)`` on success, or ``None``
        if any of the guards (fit success, curvature sign,
        vertex-in-window, positive variance) trips.
        """
        # MUSIC peak in P(theta) <=> MUSIC trough in denom(theta).
        peak_idx = int(np.argmin(denom))
        peak_azimuth_deg = float(self._scan_azimuths_deg[peak_idx])

        half = _PARABOLA_FIT_N_POINTS // 2
        idxs = (np.arange(peak_idx - half, peak_idx + half + 1) % denom.size).astype(np.intp)
        x = np.arange(-half, half + 1, dtype=np.float64) * self._scan_step_deg
        y = denom[idxs]
        if not np.all(np.isfinite(y)):
            return None

        fit = _fit_parabola(x, y)
        if fit is None:
            return None
        c2, c1, _c0 = fit
        # Curvature must be POSITIVE: denom is *minimised* at the peak of
        # P, so its parabola opens upward. A non-positive c2 means we are
        # looking at a saddle (multipath fluke) and must refuse.
        if c2 <= 0.0:
            return None

        vertex_x = -c1 / (2.0 * c2)
        if abs(vertex_x) > half * self._scan_step_deg + _VERTEX_BOUND_SLACK_DEG:
            return None

        # Stoica-Nehorai 1989 asymptotic MUSIC variance (one source). In
        # (theta in degrees) units, with ``c2`` from a polyfit on
        # ``denom`` vs ``x_deg``: c2 = d_deg^H P_n d_deg = Re{...}, and
        # the rad/deg conversion factors cancel between the formula's
        # explicit Re{d^H P_n d}_rad^-2 term and the deg^2 output unit.
        var_vertex_x = noise_floor / (2.0 * float(n_snapshots) * signal_eigval * c2)
        # Empirical variance scale -- see "SIGMA RECIPE" step 2 in the
        # module docstring. The asymptotic formula is exact only in the
        # large-T, high-SNR limit; the small SNR/T-dependent residual
        # bias is absorbed by a single empirical factor calibrated
        # against the honesty Monte-Carlo at SNR=20 dB.
        var_vertex_x *= _SIGMA_HONESTY_FACTOR
        if not math.isfinite(var_vertex_x) or var_vertex_x <= 0.0:
            return None

        return (
            (peak_azimuth_deg + vertex_x) % _DEG_FULL_CIRCLE,
            math.sqrt(var_vertex_x),
        )


def _element_positions_from_config(array_config: ArrayConfig) -> npt.NDArray[np.float64]:
    """Materialise ``(N, 2) float64`` element positions from an ``ArrayConfig``.

    Matches the conventions of ``rfmesh_sdr.ArraySpec`` and the manifold
    in ``rfmesh_dsp.array_manifold``:

    * ULA: elements along the y-axis at ``(0, i * spacing_m)`` for
      ``i = 0 .. N-1``. Channel 0 at the origin.
    * UCA: elements on a circle of radius ``element_spacing_m`` around
      the origin, channel 0 at ``(r, 0)``, with element ``i`` at angle
      ``2 pi i / N``. (The contract's ``element_spacing_m`` is the UCA
      *radius* -- see ``ArrayConfig.element_spacing_m`` docstring.)
    * CUSTOM: ``element_positions_m`` is used verbatim (length checked
      against ``n_elements`` by the contract's validator).
    """
    n = array_config.n_elements
    if array_config.geometry is ArrayGeometry.ULA:
        spacing = array_config.element_spacing_m
        if spacing is None:  # pragma: no cover -- contract validator enforces this
            msg = "ArrayConfig(geometry=ULA) requires element_spacing_m to be set."
            raise ValueError(msg)
        positions = np.zeros((n, 2), dtype=np.float64)
        positions[:, 1] = np.arange(n, dtype=np.float64) * float(spacing)
        return positions
    if array_config.geometry is ArrayGeometry.UCA:
        radius = array_config.element_spacing_m
        if radius is None:  # pragma: no cover -- contract validator enforces this
            msg = "ArrayConfig(geometry=UCA) requires element_spacing_m (radius) to be set."
            raise ValueError(msg)
        alphas = 2.0 * math.pi * np.arange(n, dtype=np.float64) / n
        positions = np.empty((n, 2), dtype=np.float64)
        positions[:, 0] = float(radius) * np.cos(alphas)
        positions[:, 1] = float(radius) * np.sin(alphas)
        return positions
    # CUSTOM
    if array_config.element_positions_m is None:  # pragma: no cover -- contract enforces
        msg = "ArrayConfig(geometry=CUSTOM) requires element_positions_m."
        raise ValueError(msg)
    return np.asarray(array_config.element_positions_m, dtype=np.float64)


def _validate_coherent_samples(
    samples: np.ndarray,
    expected_n_channels: int,
) -> npt.NDArray[np.complex128] | None:
    """Coerce ``samples`` to a 2-D complex128 array; return None on a soft mismatch.

    Soft failures (wrong channel count) return ``None`` so the estimator
    refuses the block without surfacing a runtime error to a node runtime
    that may legitimately be wiring different array shapes to different
    estimators. Hard failures (wrong type, wrong dimensionality) raise --
    those are programming bugs, not honest "no bearing this block" cases.
    """
    if not isinstance(samples, np.ndarray):
        msg = (
            "L2MusicEstimator.estimate: samples must be a numpy.ndarray of "
            f"shape (n_channels, n_samples); got {type(samples).__name__}."
        )
        raise ValueError(msg)
    if samples.ndim != _COHERENT_BLOCK_NDIM:
        msg = (
            "L2MusicEstimator.estimate: samples must be 2-D (n_channels, "
            f"n_samples); got ndim={samples.ndim}."
        )
        raise ValueError(msg)
    if not np.issubdtype(samples.dtype, np.complexfloating):
        msg = f"L2MusicEstimator.estimate: samples must have a complex dtype; got {samples.dtype}."
        raise ValueError(msg)
    if samples.shape[0] != expected_n_channels:
        # n_channels mismatch is a soft failure: the estimator is bound to
        # one array geometry and a block from a different shape simply
        # cannot be processed. None is the honest answer.
        return None
    return samples.astype(np.complex128, copy=False)


def _fit_parabola(
    x: npt.NDArray[np.float64],
    y: npt.NDArray[np.float64],
) -> tuple[float, float, float] | None:
    """Quadratic least-squares fit; returns ``(c2, c1, c0)`` or ``None``.

    Coefficients are ordered highest-degree first to match
    ``np.polyfit``'s convention. Unlike L1's helper this does NOT
    propagate ``cov=True`` -- MUSIC's sigma is the Stoica-Nehorai
    asymptotic variance, not the fit residuals (see module docstring
    "SIGMA RECIPE").
    """
    try:
        coeffs = np.polyfit(x, y, 2)
    except (np.linalg.LinAlgError, ValueError):
        return None
    if not np.all(np.isfinite(coeffs)):
        return None
    return float(coeffs[0]), float(coeffs[1]), float(coeffs[2])
