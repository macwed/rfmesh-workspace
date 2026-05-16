"""RSSI estimation, noise-floor analysis, SNR, and clipping detection.

Pure functions over complex64 IQ arrays. PSD-based estimators delegate to
``rfmesh_dsp.spectrum.compute_psd`` -- Welch's method is not reimplemented here.

Salvaged from ``github.com/macwed/rf-mesh`` per SALVAGE_AUDIT Part 3
(verdict: TAKE). The only edits from the source are the import path
(``rfmesh.dsp.*`` -> ``rfmesh_dsp.*``) and the swap from
``loguru.logger`` to ``logging`` (loguru is not a workspace dependency).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from rfmesh_dsp.constants import EPSILON
from rfmesh_dsp.spectrum import compute_psd

logger = logging.getLogger(__name__)

# Upper bound (exclusive) on ``exclude_strongest_pct``. Named for ruff PLR2004.
_EXCLUDE_STRONGEST_PCT_MAX = 100.0


def compute_rssi_dbfs(iq: npt.NDArray[np.complex64]) -> float:
    """Compute total RSSI of IQ samples in dBFS.

    RSSI is computed as ``10 * log10(mean(|iq|**2))``, giving power relative
    to a unit-amplitude full-scale signal (A = 1.0 -> 0 dBFS).

    Args:
        iq: Complex IQ samples, normalized so |iq| <= 1 corresponds to ADC
            full scale.

    Returns:
        RSSI in dBFS as a Python float.

    Raises:
        ValueError: If ``iq`` is empty.
    """
    if iq.size == 0:
        msg = "iq must contain at least one sample"
        raise ValueError(msg)
    power = float(np.mean(np.abs(iq) ** 2))
    rssi_dbfs = 10.0 * float(np.log10(power + EPSILON))
    logger.debug("compute_rssi_dbfs: n=%d, rssi=%.2f dBFS", iq.size, rssi_dbfs)
    return rssi_dbfs


def compute_rssi_in_band(
    iq: npt.NDArray[np.complex64],
    fs: float,
    *,
    band_low_hz: float,
    band_high_hz: float,
    nperseg: int = 4096,
    window: str = "hann",
) -> float:
    """Compute RSSI integrated over a baseband frequency band (in dBFS).

    Uses Welch's PSD via :func:`rfmesh_dsp.spectrum.compute_psd` and integrates
    power within ``[band_low_hz, band_high_hz]``. Frequencies are baseband --
    relative to the IQ stream's center frequency.

    Args:
        iq: Complex IQ samples.
        fs: Sample rate in Hz.
        band_low_hz: Lower edge of integration band (baseband, may be negative).
        band_high_hz: Upper edge of integration band (baseband).
        nperseg: PSD segment length passed to Welch.
        window: Window name passed to Welch.

    Returns:
        RSSI in dBFS for the specified band.

    Raises:
        ValueError: If ``band_low_hz >= band_high_hz``, ``nperseg > len(iq)``,
            or the requested band is entirely outside ``[-fs/2, fs/2]``.
    """
    if band_low_hz >= band_high_hz:
        msg = f"band_low_hz ({band_low_hz}) must be < band_high_hz ({band_high_hz})"
        raise ValueError(msg)
    nyquist = fs / 2.0
    if band_high_hz <= -nyquist or band_low_hz >= nyquist:
        msg = (
            f"band [{band_low_hz}, {band_high_hz}] Hz lies entirely outside "
            f"[-fs/2, fs/2] = [{-nyquist}, {nyquist}] Hz"
        )
        raise ValueError(msg)

    freqs, psd_db = compute_psd(iq, fs, nperseg=nperseg, window=window, center_freq_hz=0.0)
    psd_linear = np.power(10.0, psd_db / 10.0)
    df = float(freqs[1] - freqs[0])
    mask = (freqs >= band_low_hz) & (freqs <= band_high_hz)
    if not bool(np.any(mask)):
        msg = f"band [{band_low_hz}, {band_high_hz}] Hz contains no PSD bins (df = {df:.3f} Hz)"
        raise ValueError(msg)
    power_linear = float(np.sum(psd_linear[mask]) * df)
    rssi_dbfs = 10.0 * float(np.log10(power_linear + EPSILON))
    logger.debug(
        "compute_rssi_in_band: band=[%.0f,%.0f] Hz, rssi=%.2f dBFS",
        band_low_hz,
        band_high_hz,
        rssi_dbfs,
    )
    return rssi_dbfs


def compute_noise_floor_dbfs(
    iq: npt.NDArray[np.complex64],
    fs: float,
    *,
    nperseg: int = 4096,
    window: str = "hann",
    exclude_strongest_pct: float = 10.0,
) -> float:
    """Estimate noise floor (in dBFS/Hz) by excluding strongest spectral bins.

    Computes the PSD, sorts bins ascending, drops the strongest
    ``exclude_strongest_pct`` percent, and returns the median of the remainder.
    Robust to one or several strong emitters.

    Args:
        iq: Complex IQ samples.
        fs: Sample rate in Hz.
        nperseg: PSD segment length.
        window: Window name.
        exclude_strongest_pct: Percent of strongest bins to discard before
            taking the median. Must be in ``[0, 100)``.

    Returns:
        Noise floor estimate in dBFS/Hz (per-Hz density).

    Raises:
        ValueError: If ``exclude_strongest_pct`` is not in ``[0, 100)`` or
            ``nperseg > len(iq)``.
    """
    if not 0.0 <= exclude_strongest_pct < _EXCLUDE_STRONGEST_PCT_MAX:
        msg = f"exclude_strongest_pct ({exclude_strongest_pct}) must be in [0, 100)"
        raise ValueError(msg)

    _, psd_db = compute_psd(iq, fs, nperseg=nperseg, window=window, center_freq_hz=0.0)
    psd_linear = np.power(10.0, psd_db / 10.0)
    sorted_lin = np.sort(psd_linear)
    keep_n = round(sorted_lin.size * (1.0 - exclude_strongest_pct / _EXCLUDE_STRONGEST_PCT_MAX))
    keep_n = max(keep_n, 1)
    kept = sorted_lin[:keep_n]
    median_lin = float(np.median(kept))
    noise_floor_db = 10.0 * float(np.log10(median_lin + EPSILON))
    logger.debug(
        "compute_noise_floor_dbfs: kept=%d/%d bins, noise_floor=%.2f dBFS/Hz",
        keep_n,
        sorted_lin.size,
        noise_floor_db,
    )
    return noise_floor_db


def compute_snr_db(
    iq: npt.NDArray[np.complex64],
    fs: float,
    *,
    signal_band_hz: tuple[float, float],
    noise_band_hz: tuple[float, float] | None = None,
    nperseg: int = 4096,
    window: str = "hann",
) -> float:
    """Compute SNR (in dB) between a signal band and a noise reference.

    If ``noise_band_hz`` is ``None``, the noise floor is estimated globally via
    :func:`compute_noise_floor_dbfs` and scaled to the signal-band bandwidth so
    both terms are comparable total-power values.

    Args:
        iq: Complex IQ samples.
        fs: Sample rate in Hz.
        signal_band_hz: ``(low, high)`` in Hz, baseband.
        noise_band_hz: Optional explicit noise reference band. If ``None``, a
            global noise-floor estimate is used.
        nperseg: PSD segment length.
        window: Window name.

    Returns:
        SNR in dB as a Python float.

    Raises:
        ValueError: If band specs are invalid.
    """
    sig_low, sig_high = signal_band_hz
    signal_dbfs = compute_rssi_in_band(
        iq,
        fs,
        band_low_hz=sig_low,
        band_high_hz=sig_high,
        nperseg=nperseg,
        window=window,
    )

    if noise_band_hz is not None:
        noise_low, noise_high = noise_band_hz
        noise_dbfs = compute_rssi_in_band(
            iq,
            fs,
            band_low_hz=noise_low,
            band_high_hz=noise_high,
            nperseg=nperseg,
            window=window,
        )
    else:
        noise_density_db = compute_noise_floor_dbfs(iq, fs, nperseg=nperseg, window=window)
        bandwidth = sig_high - sig_low
        noise_dbfs = noise_density_db + 10.0 * float(np.log10(bandwidth + EPSILON))

    snr_db = signal_dbfs - noise_dbfs
    logger.debug(
        "compute_snr_db: signal=%.2f dBFS, noise=%.2f dBFS, snr=%.2f dB",
        signal_dbfs,
        noise_dbfs,
        snr_db,
    )
    return snr_db


@dataclass(frozen=True)
class ClippingReport:
    """Report on ADC clipping detected in IQ samples."""

    total_samples: int
    clipped_i_count: int
    clipped_q_count: int
    clipped_pct: float
    max_magnitude: float
    is_clipping: bool


def detect_clipping(
    iq: npt.NDArray[np.complex64],
    *,
    threshold: float = 0.999,
    warning_pct: float = 0.01,
) -> ClippingReport:
    """Detect ADC saturation in normalized IQ samples.

    For RTL-SDR uint8 input normalized via ``(raw - 127.5) / 127.5``, clipping
    manifests as ``|I|`` or ``|Q|`` reaching +/-1.0 (raw 0 or 255).

    Args:
        iq: Complex IQ samples normalized to roughly ``[-1, 1]`` per channel.
        threshold: Magnitude threshold (per channel) above which a sample is
            considered clipped. Default 0.999.
        warning_pct: Percent of clipped samples above which ``is_clipping`` is
            set. Default 0.01 (i.e. 0.01%).

    Returns:
        :class:`ClippingReport` with detailed statistics.

    Raises:
        ValueError: If ``iq`` is empty.
    """
    if iq.size == 0:
        msg = "iq must contain at least one sample"
        raise ValueError(msg)

    clipped_i = np.abs(iq.real) >= threshold
    clipped_q = np.abs(iq.imag) >= threshold
    clipped_any = clipped_i | clipped_q
    clipped_pct = 100.0 * float(np.sum(clipped_any)) / iq.size
    max_magnitude = float(np.max(np.abs(iq)))
    is_clipping = clipped_pct > warning_pct
    return ClippingReport(
        total_samples=int(iq.size),
        clipped_i_count=int(np.sum(clipped_i)),
        clipped_q_count=int(np.sum(clipped_q)),
        clipped_pct=clipped_pct,
        max_magnitude=max_magnitude,
        is_clipping=is_clipping,
    )
