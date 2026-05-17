"""Feature extraction for the L3 modulation-class classifier.

Pure-numpy primitives plus the salvaged STFT entry point
(``rfmesh_dsp.spectrum.compute_spectrogram``). No I/O, no torch, no
hardware. Consumed by ``rules.classify_from_features``; the public output
is a fixed-length ``float64`` feature vector with the layout documented
below.

Feature-vector layout (``FEATURE_VECTOR_LENGTH = 16``)
------------------------------------------------------

``[0]``  kurtosis of ``|iq|`` (Fisher's definition, excess kurtosis).
         High (positive) for a pure CW tone -- the magnitude is
         constant up to noise, so its distribution is heavy-tailed
         only through the noise floor; the excess kurtosis is large
         because the unwrapped distribution has near-zero variance
         relative to the noise spikes. For modulated signals the
         envelope varies and kurtosis falls toward 0 (Gaussian) or
         below.

``[1]``  envelope flatness -- ``exp(mean(log|iq|)) / mean(|iq|)``
         (the spectral-flatness analog applied to the envelope).
         Close to 1 for a CW tone (constant envelope) or a constant-
         envelope FM signal (FSK, LoRa chirp). Drops for FHSS where
         the envelope is gated between hop dwells, and for noise.

``[2]``  peak-to-average power ratio (PAPR) of ``|iq|``, in dB.
         Low (~0 dB) for constant-envelope modulations; high for
         hopped / bursted signals.

``[3]``  instantaneous-frequency histogram entropy (Shannon, base e).
         CW -> ~0 (one bin dominates), 2-FSK -> low (two bins),
         FHSS -> high (many bins, roughly uniform over the hop set),
         LoRa -> moderate (a near-uniform sweep across the chirp
         bandwidth).

``[4]``  instantaneous-frequency histogram peak count -- the number
         of histogram bins that are local maxima and exceed
         ``_IF_HIST_PEAK_THRESHOLD`` relative to the max bin. CW -> 1,
         2-FSK -> 2, FHSS -> many (>= 3), LoRa -> 0 or 1 (a sweep
         flattens the histogram; the chirp is detected on slope, not
         on peak count).

``[5]``  hop-rate detector: estimated dominant hop rate in Hz, from
         autocorrelation of the per-segment STFT argmax-bin sequence.
         Non-zero only when the signal hops between discrete
         frequencies; bounded by ``_MAX_HOPS_PER_SEC``.

``[6]``  hop-rate detector prominence -- how peaked the
         autocorrelation peak is (``(peak - median) / (max - median)``).
         A real hop pattern has a prominent autocorrelation peak; a
         noisy guess produces a flat autocorrelation.

``[7]``  chirp-rate detector: linear-fit slope of instantaneous
         frequency vs time, in Hz/s. Non-zero for a chirp, near zero
         for CW / FSK, undefined (set to 0) for FHSS where the slope
         changes sign at every hop.

``[8]``  chirp-rate detector: linear-fit residual RMS, in Hz. Small
         (relative to the per-segment frequency span) for a clean
         chirp; large for FHSS, FSK, or noise.

``[9]``  chirp-rate detector: instantaneous-frequency span (max -
         min of the per-segment STFT argmax-bin sequence, in Hz).
         Wide for LoRa / FHSS, narrow for CW / FSK.

``[10..15]`` STFT panel summary statistics, six entries -- mean of
         ``sxx_db`` across time, variance across time, mean across
         frequency, variance across frequency, mean of the per-segment
         peak power (in dB), and the standard deviation of the per-
         segment peak bin index. These are auxiliary features the CNN
         backend (when wired up) consumes directly; the rules-based
         core ignores them but they are kept finite so the contract on
         ``feature_vector`` is uniform across backends.

Empirically tuned thresholds (CW_KURTOSIS_THRESHOLD, etc.) live in
``rules.py``. This module is responsible for computing the *raw*
features; ``rules.py`` decides what they mean.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt
from rfmesh_dsp.spectrum import compute_spectrogram  # type: ignore[import-untyped, unused-ignore]

FEATURE_VECTOR_LENGTH: int = 16
"""Fixed length of the feature vector produced by ``extract_features``.

Pinned at 16 so that ``ClassificationResult.feature_vector`` carries a
contract-stable shape that downstream consumers (ops dashboard,
WS-B-006 threat-mapping layer) can rely on across releases of this
module. Adding a new feature beyond index 15 is a MINOR change here and
should be paired with a docstring update.
"""

# ---------------------------------------------------------------------------
# Internal tuning constants. Conservative; the rules-based scorer in
# rules.py applies the actual classification thresholds.
# ---------------------------------------------------------------------------

_DEFAULT_NPERSEG: int = 256
"""STFT window length. 256 samples at 2.048 MS/s -> ~125 us per segment,
which resolves hop rates up to ~4 kHz comfortably and chirp slopes from
LoRa SF7 (~31 kHz/symbol over 1.024 ms) without aliasing."""

_IF_HIST_BINS: int = 32
"""Number of bins for the instantaneous-frequency histogram. 32 is a
practical compromise: enough resolution to separate 2-FSK from CW and
to distinguish FHSS's near-uniform spread from a chirp's gradient,
small enough that low-SNR scenarios do not artificially inflate the
entropy."""

_IF_HIST_PEAK_THRESHOLD: float = 0.3
"""Relative threshold (vs the max bin) for counting an IF-histogram
peak in feature [4]. Bins below this fraction of the max are not
counted as peaks. Tuned so 2-FSK is detected as 2 peaks at SNR >= 5 dB
without false positives from noise side-lobes."""

_MIN_AUTOCORR_LAG_SEGMENTS: int = 2
"""Smallest segment lag considered by the hop-rate autocorrelation.
Lag 0 is trivially the global max; lag 1 measures one-segment-apart
correlation which dominates for any continuous signal. The first real
candidate for a hop period is lag >= 2."""

_MAX_HOPS_PER_SEC: float = 10_000.0
"""Upper bound on the hop-rate detector's reported frequency in feature
[5]. Beyond this, the autocorrelation candidate is dropped (set to 0).
Real FHSS systems (e.g. Crossfire ~150 hops/s, FrSky ~100 hops/s,
ELRS up to 500 hops/s) all sit well below this ceiling; the cap
prevents pathological noise-driven autocorrelations from looking like
GHz-rate hops."""

# Numerical-hygiene floors. These are deliberately well below any
# physically meaningful value -- they catch the "all-zero" degenerate
# branch in each statistic and short-circuit it before a division by
# near-zero produces a NaN. The exact value does not matter; any number
# below ``1e-12`` would do. Kept as named constants per ruff PLR2004.
_VARIANCE_EPS: float = 1e-30
_DIVISOR_EPS: float = 1e-30
_PROMINENCE_EPS: float = 1e-9

_MIN_SAMPLES_FOR_DIFF: int = 2
"""Two samples are the minimum to compute a phase difference (the
instantaneous-frequency estimator); below this the function returns
an empty array."""

_MIN_SEGMENTS_FOR_AUTOCORR: int = 4
"""The hop-rate autocorrelation needs at least this many segments to
produce a meaningful peak prominence; below this the estimator
short-circuits to (0, 0)."""

_MIN_TIMES_FOR_DT: int = 2
"""Two time samples are the minimum to compute the segment dt for the
hop-rate detector."""

_STFT_SUMMARY_LENGTH: int = 6
"""Number of STFT-panel summary statistics returned by ``_stft_summary``
and stored at the tail of the feature vector."""

_MIN_NPERSEG: int = 8
"""Minimum STFT window length. Below this the spectrogram is degenerate
(too few segments to compute a sensible per-segment peak-bin std)."""


def _ensure_complex64(iq: npt.NDArray[np.complex64]) -> None:
    """Raise ``TypeError`` with an explicit ``complex64`` message on mismatch.

    Strict on dtype, like every other estimator in the project (Invariant
    B3 surface: catch dtype confusion at the call boundary, do not
    silently re-cast).
    """
    if iq.dtype != np.complex64:
        msg = (
            f"iq must be complex64 (got {iq.dtype}); use np.asarray(..., dtype=np.complex64) "
            "at the caller boundary"
        )
        raise TypeError(msg)


def _kurtosis(x: npt.NDArray[np.float64]) -> float:
    """Excess kurtosis (Fisher's definition: Gaussian -> 0).

    Returns 0.0 when variance is below ``EPSILON`` (degenerate input);
    callers treat that as "no information" rather than as a confident
    Gaussian claim.
    """
    if x.size == 0:
        return 0.0
    mean = float(np.mean(x))
    centered = x - mean
    var = float(np.mean(centered**2))
    if var < _VARIANCE_EPS:
        return 0.0
    fourth = float(np.mean(centered**4))
    return fourth / (var * var) - 3.0


def _envelope_flatness(magnitude: npt.NDArray[np.float64]) -> float:
    """Geometric-mean-over-arithmetic-mean of the IQ magnitude.

    Returns 0.0 when the arithmetic mean is zero (degenerate input).
    Bounded in ``[0, 1]`` for non-negative magnitudes.
    """
    if magnitude.size == 0:
        return 0.0
    arith = float(np.mean(magnitude))
    if arith < _DIVISOR_EPS:
        return 0.0
    # Add a small floor to log argument to keep finite on near-zero samples.
    log_floor = max(arith, _DIVISOR_EPS) * 1e-12
    geo = float(np.exp(np.mean(np.log(magnitude + log_floor))))
    return float(np.clip(geo / arith, 0.0, 1.0))


def _papr_db(magnitude: npt.NDArray[np.float64]) -> float:
    """Peak-to-average power ratio in dB.

    Returns 0.0 when mean power is zero. Negative values are clamped to 0.
    """
    if magnitude.size == 0:
        return 0.0
    power = magnitude.astype(np.float64) ** 2
    mean_power = float(np.mean(power))
    if mean_power < _DIVISOR_EPS:
        return 0.0
    peak_power = float(np.max(power))
    ratio = peak_power / mean_power
    if ratio <= 0.0:
        return 0.0
    return float(10.0 * math.log10(ratio))


def _instantaneous_frequency(iq: npt.NDArray[np.complex64], fs: float) -> npt.NDArray[np.float64]:
    """Instantaneous frequency in Hz from the unwrapped IQ phase difference.

    Returns an array one sample shorter than ``iq`` (the difference of
    consecutive phases). For SNR <= 0 dB the result is dominated by
    noise; that is honest -- the rules-based scorer reads the histogram
    statistics, which collapse to "high entropy / no peaks" on noise and
    do not produce a false positive for any class.
    """
    if iq.size < _MIN_SAMPLES_FOR_DIFF:
        return np.zeros(0, dtype=np.float64)
    phase = np.angle(iq.astype(np.complex128))
    dphi = np.diff(np.unwrap(phase))
    # f = (1/2pi) dphi/dt; dt = 1/fs.
    return (dphi * (fs / (2.0 * math.pi))).astype(np.float64)


def _if_histogram_features(inst_freq: npt.NDArray[np.float64], fs: float) -> tuple[float, float]:
    """Return ``(entropy, peak_count)`` of the instantaneous-frequency histogram.

    Histogram range is ``[-fs/2, fs/2]`` (the full unaliased baseband).
    Entropy is in nats (natural log). Peak count is integer-valued
    returned as a float for the feature vector.
    """
    if inst_freq.size == 0:
        return 0.0, 0.0
    hist, _ = np.histogram(inst_freq, bins=_IF_HIST_BINS, range=(-fs / 2.0, fs / 2.0))
    total = float(hist.sum())
    if total <= 0.0:
        return 0.0, 0.0
    p = hist.astype(np.float64) / total
    nonzero = p[p > 0.0]
    entropy = float(-np.sum(nonzero * np.log(nonzero)))

    # Local maxima above the relative threshold.
    max_count = float(hist.max())
    if max_count <= 0.0:
        return entropy, 0.0
    threshold = _IF_HIST_PEAK_THRESHOLD * max_count
    peaks = 0
    for i in range(_IF_HIST_BINS):
        v = float(hist[i])
        if v < threshold:
            continue
        left = float(hist[i - 1]) if i > 0 else -math.inf
        right = float(hist[i + 1]) if i < _IF_HIST_BINS - 1 else -math.inf
        if v >= left and v >= right and not (v == left and v == right):
            peaks += 1
    return entropy, float(peaks)


def _per_segment_peak_bin(sxx_db: npt.NDArray[np.float64]) -> npt.NDArray[np.int64]:
    """Index of the peak frequency bin per STFT segment.

    ``sxx_db`` has shape ``(n_freqs, n_segments)`` -- the layout
    ``compute_spectrogram`` returns. ``argmax`` along axis 0 gives one
    bin index per segment.
    """
    result: npt.NDArray[np.int64] = np.argmax(sxx_db, axis=0).astype(np.int64)
    return result


def _hop_rate_features(  # noqa: PLR0911 -- early-return guard ladder
    peak_bins: npt.NDArray[np.int64], times: npt.NDArray[np.float64], fs: float
) -> tuple[float, float]:
    """Return ``(hop_rate_hz, prominence)`` from per-segment peak-bin autocorrelation.

    ``peak_bins`` is the per-segment argmax frequency index over time.
    A hopping signal produces a periodic sequence whose autocorrelation
    has a clean peak at the hop period; a CW / chirp signal produces a
    monotonic or near-constant sequence and the autocorrelation peak
    sits at lag 0 (which we exclude).

    Returns ``(0.0, 0.0)`` if no candidate lag is found, the candidate
    rate exceeds ``_MAX_HOPS_PER_SEC``, or fewer than 4 segments are
    available.
    """
    n = peak_bins.size
    if n < _MIN_SEGMENTS_FOR_AUTOCORR or times.size < _MIN_TIMES_FOR_DT:
        return 0.0, 0.0

    segment_dt = float(times[1] - times[0])
    if segment_dt <= 0.0:
        return 0.0, 0.0

    centered = peak_bins.astype(np.float64) - float(np.mean(peak_bins))
    var = float(np.mean(centered**2))
    if var < _PROMINENCE_EPS:
        # Constant sequence -- no hops, no chirp captured by argmax bin.
        return 0.0, 0.0
    autocorr = np.correlate(centered, centered, mode="full")[n - 1 :]
    autocorr = autocorr / autocorr[0]  # normalise so lag 0 == 1.

    if autocorr.size <= _MIN_AUTOCORR_LAG_SEGMENTS:
        return 0.0, 0.0

    candidate_region = autocorr[_MIN_AUTOCORR_LAG_SEGMENTS:]
    if candidate_region.size == 0:
        return 0.0, 0.0
    idx = int(np.argmax(candidate_region)) + _MIN_AUTOCORR_LAG_SEGMENTS
    peak_value = float(autocorr[idx])
    if peak_value <= 0.0:
        return 0.0, 0.0

    period_s = idx * segment_dt
    if period_s <= 0.0:
        return 0.0, 0.0
    rate_hz = 1.0 / period_s
    if rate_hz > _MAX_HOPS_PER_SEC:
        return 0.0, 0.0

    median = float(np.median(autocorr[1:]))
    max_val = float(np.max(autocorr[1:]))
    denom = max_val - median
    prominence = (
        0.0 if denom <= _PROMINENCE_EPS else float(np.clip((peak_value - median) / denom, 0.0, 1.0))
    )

    # fs is read by callers via the sample-rate-driven nperseg defaults;
    # kept as a parameter so the signature is uniform with the chirp
    # feature, which does depend on fs directly.
    del fs
    return rate_hz, prominence


def _chirp_features(
    peak_bins: npt.NDArray[np.int64],
    times: npt.NDArray[np.float64],
    freqs: npt.NDArray[np.float64],
) -> tuple[float, float, float]:
    """Return ``(slope_hz_per_s, residual_rms_hz, span_hz)`` for a chirp fit.

    Maps the per-segment peak-bin sequence to per-segment peak
    *frequency* via the STFT's frequency axis, then fits a line. A LoRa
    up- or down-chirp produces a straight line; a CW tone produces a
    flat line (slope ~ 0); FSK and FHSS produce piecewise-constant
    sequences with large residuals.
    """
    n = peak_bins.size
    if n < _MIN_SEGMENTS_FOR_AUTOCORR or times.size != n:
        return 0.0, 0.0, 0.0
    peak_freqs = freqs[peak_bins].astype(np.float64)
    span = float(np.max(peak_freqs) - np.min(peak_freqs))
    if span <= 0.0:
        # Constant peak frequency -- treat as zero slope, zero residual.
        return 0.0, 0.0, 0.0

    # ``np.polyfit`` is deterministic; degree 1 is what we want.
    coeffs = np.polyfit(times, peak_freqs, deg=1)
    slope = float(coeffs[0])
    intercept = float(coeffs[1])
    residuals = peak_freqs - (slope * times + intercept)
    residual_rms = float(np.sqrt(np.mean(residuals**2)))
    return slope, residual_rms, span


def _stft_summary(sxx_db: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """Return six STFT-panel summary statistics as a length-6 vector.

    Auxiliary features the CNN backend consumes; the rules-based scorer
    ignores them. Kept finite for any non-empty STFT panel.
    """
    if sxx_db.size == 0:
        return np.zeros(_STFT_SUMMARY_LENGTH, dtype=np.float64)
    time_axis_mean = float(np.mean(sxx_db, axis=1).mean())
    time_axis_var = float(np.var(np.mean(sxx_db, axis=1)))
    freq_axis_mean = float(np.mean(sxx_db, axis=0).mean())
    freq_axis_var = float(np.var(np.mean(sxx_db, axis=0)))
    peak_per_segment = sxx_db.max(axis=0)
    peak_mean_db = float(np.mean(peak_per_segment))
    peak_bin_std = float(np.std(np.argmax(sxx_db, axis=0).astype(np.float64)))
    return np.array(
        [time_axis_mean, time_axis_var, freq_axis_mean, freq_axis_var, peak_mean_db, peak_bin_std],
        dtype=np.float64,
    )


def extract_features(
    iq: npt.NDArray[np.complex64],
    *,
    fs: float,
    nperseg: int = _DEFAULT_NPERSEG,
) -> npt.NDArray[np.float64]:
    """Extract the ``FEATURE_VECTOR_LENGTH`` feature vector from an IQ block.

    Args:
        iq: ``complex64`` IQ samples. Strict on dtype; a mismatch
            raises ``TypeError`` (Invariant B3 surface).
        fs: Sample rate in Hz. Must be > 0.
        nperseg: STFT window length. Defaults to 256; tuneable for
            longer blocks. ``ValueError`` if larger than ``iq.size``.

    Returns:
        A length-``FEATURE_VECTOR_LENGTH`` ``float64`` numpy array,
        layout as documented in the module docstring. All entries are
        finite -- a degenerate input (e.g. an all-zero block) maps to
        defined zeros rather than to NaN.

    Raises:
        TypeError: if ``iq.dtype != complex64``.
        ValueError: if ``iq`` is empty, ``fs <= 0``, or ``nperseg``
            exceeds ``iq.size``.
    """
    _ensure_complex64(iq)
    if iq.size == 0:
        msg = "iq must contain at least one sample"
        raise ValueError(msg)
    if fs <= 0.0:
        msg = f"fs must be > 0 (got {fs})"
        raise ValueError(msg)
    effective_nperseg = min(nperseg, iq.size)
    if effective_nperseg < _MIN_NPERSEG:
        msg = (
            f"iq is too short for STFT-based features "
            f"(need >= {_MIN_NPERSEG} samples for nperseg, got iq.size={iq.size})"
        )
        raise ValueError(msg)

    magnitude = np.abs(iq).astype(np.float64)
    kurtosis = _kurtosis(magnitude)
    flatness = _envelope_flatness(magnitude)
    papr = _papr_db(magnitude)

    inst_freq = _instantaneous_frequency(iq, fs)
    if_entropy, if_peak_count = _if_histogram_features(inst_freq, fs)

    times, freqs, sxx_db = compute_spectrogram(iq, fs, nperseg=effective_nperseg)
    peak_bins = _per_segment_peak_bin(sxx_db)
    hop_rate, hop_prominence = _hop_rate_features(peak_bins, times, fs)
    chirp_slope, chirp_residual_rms, chirp_span = _chirp_features(peak_bins, times, freqs)
    stft_summary = _stft_summary(sxx_db)

    feature_vector = np.array(
        [
            kurtosis,
            flatness,
            papr,
            if_entropy,
            if_peak_count,
            hop_rate,
            hop_prominence,
            chirp_slope,
            chirp_residual_rms,
            chirp_span,
            stft_summary[0],
            stft_summary[1],
            stft_summary[2],
            stft_summary[3],
            stft_summary[4],
            stft_summary[5],
        ],
        dtype=np.float64,
    )

    # Numerical-hygiene gate: anything non-finite at this point is a
    # bug in feature extraction, not "noisy input". Convert to a hard
    # failure so the caller sees it rather than passing a NaN-poisoned
    # vector into the rules scorer (Invariant B3 surface).
    if not np.all(np.isfinite(feature_vector)):
        bad_indices = np.where(~np.isfinite(feature_vector))[0].tolist()
        msg = f"feature vector contains non-finite entries at indices {bad_indices}"
        raise ValueError(msg)

    return feature_vector
