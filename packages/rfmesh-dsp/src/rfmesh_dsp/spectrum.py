"""Spectrum analysis primitives: FFT, PSD (Welch), spectrogram (STFT).

Pure functions only. Inputs are complex64 IQ arrays; outputs are numpy arrays.

Salvaged from ``github.com/macwed/rf-mesh`` per SALVAGE_AUDIT Part 3
(verdict: TAKE). The only edits from the source are the import path
(``rfmesh.dsp.constants`` -> ``rfmesh_dsp.constants``) and the swap from
``loguru.logger`` to ``logging`` (loguru is not a workspace dependency).
"""

from __future__ import annotations

import logging

import numpy as np
import numpy.typing as npt
from scipy.signal import get_window, spectrogram, welch  # type: ignore[import-untyped]

from rfmesh_dsp.constants import EPSILON

logger = logging.getLogger(__name__)


def compute_fft(
    iq: npt.NDArray[np.complex64],
    fs: float,
    *,
    window: str = "hann",
    nfft: int | None = None,
    center_freq_hz: float = 0.0,
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.complex128]]:
    """Compute windowed FFT of complex IQ samples.

    Args:
        iq: Complex IQ samples.
        fs: Sample rate in Hz.
        window: scipy.signal.get_window name (default 'hann').
        nfft: FFT size. Defaults to len(iq). Zero-padded if larger.
        center_freq_hz: Center frequency of the IQ stream. Used to shift
            the output frequency axis to absolute frequencies. Default 0 = baseband.

    Returns:
        (freqs, fft_complex):
            - freqs: Frequency bins in Hz, fftshifted, length nfft.
            - fft_complex: FFT result, fftshifted, length nfft, dtype complex128.

    Raises:
        ValueError: If iq is empty or nfft < len(iq).
    """
    n = iq.size
    if n == 0:
        msg = "iq must contain at least one sample"
        raise ValueError(msg)
    nfft_eff = n if nfft is None else nfft
    if nfft_eff < n:
        msg = f"nfft ({nfft_eff}) must be >= len(iq) ({n})"
        raise ValueError(msg)

    win = get_window(window, n).astype(np.float64)
    windowed = iq.astype(np.complex128) * win
    if nfft_eff > n:
        padded = np.zeros(nfft_eff, dtype=np.complex128)
        padded[:n] = windowed
    else:
        padded = windowed

    fft_complex = np.fft.fftshift(np.fft.fft(padded)).astype(np.complex128)
    freqs = (
        np.fft.fftshift(np.fft.fftfreq(nfft_eff, d=1.0 / fs)).astype(np.float64) + center_freq_hz
    )
    logger.debug("compute_fft: n=%d, nfft=%d, fs=%.0f", n, nfft_eff, fs)
    return freqs, fft_complex


def compute_psd(
    iq: npt.NDArray[np.complex64],
    fs: float,
    *,
    nperseg: int = 4096,
    noverlap: int | None = None,
    window: str = "hann",
    center_freq_hz: float = 0.0,
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Compute Power Spectral Density via Welch's method.

    Args:
        iq: Complex IQ samples.
        fs: Sample rate in Hz.
        nperseg: Length of each Welch segment.
        noverlap: Number of overlapping samples. Default = nperseg // 2.
        window: scipy.signal.get_window name.
        center_freq_hz: Center frequency for absolute frequency axis.

    Returns:
        (freqs, psd_db):
            - freqs: Frequency bins in Hz, fftshifted, length nperseg.
            - psd_db: PSD in dB (10*log10(V^2/Hz)), fftshifted, length nperseg.

    Raises:
        ValueError: If nperseg > len(iq).
    """
    n = iq.size
    if nperseg > n:
        msg = f"nperseg ({nperseg}) must be <= len(iq) ({n})"
        raise ValueError(msg)

    freqs, pxx = welch(
        iq,
        fs=fs,
        window=window,
        nperseg=nperseg,
        noverlap=noverlap,
        return_onesided=False,
        scaling="density",
        detrend=False,
    )
    freqs_shifted = np.fft.fftshift(freqs).astype(np.float64) + center_freq_hz
    pxx_shifted = np.fft.fftshift(pxx).astype(np.float64)
    psd_db = (10.0 * np.log10(pxx_shifted + EPSILON)).astype(np.float64)
    logger.debug("compute_psd: nperseg=%d, fs=%.0f", nperseg, fs)
    return freqs_shifted, psd_db


def compute_spectrogram(
    iq: npt.NDArray[np.complex64],
    fs: float,
    *,
    nperseg: int = 1024,
    noverlap: int | None = None,
    window: str = "hann",
    center_freq_hz: float = 0.0,
) -> tuple[
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
]:
    """Compute Short-Time Fourier Transform spectrogram.

    Args:
        iq: Complex IQ samples.
        fs: Sample rate in Hz.
        nperseg: Window length per segment.
        noverlap: Overlap between segments. Default = nperseg // 2.
        window: scipy.signal.get_window name.
        center_freq_hz: Center frequency for absolute frequency axis.

    Returns:
        (times, freqs, sxx_db):
            - times: Time bins in seconds, length n_segments.
            - freqs: Frequency bins in Hz, fftshifted, length nperseg.
            - sxx_db: 2-D array of shape (nperseg, n_segments) with power in dB.

    Raises:
        ValueError: If nperseg > len(iq).
    """
    n = iq.size
    if nperseg > n:
        msg = f"nperseg ({nperseg}) must be <= len(iq) ({n})"
        raise ValueError(msg)

    freqs, times, sxx = spectrogram(
        iq,
        fs=fs,
        window=window,
        nperseg=nperseg,
        noverlap=noverlap,
        return_onesided=False,
        scaling="density",
        mode="psd",
        detrend=False,
    )
    freqs_shifted = np.fft.fftshift(freqs).astype(np.float64) + center_freq_hz
    sxx_shifted = np.fft.fftshift(sxx, axes=0).astype(np.float64)
    sxx_db = (10.0 * np.log10(sxx_shifted + EPSILON)).astype(np.float64)
    logger.debug("compute_spectrogram: nperseg=%d, n_segments=%d", nperseg, times.size)
    return times.astype(np.float64), freqs_shifted, sxx_db


def find_spectral_peak(
    iq: npt.NDArray[np.complex64],
    fs: float,
    *,
    center_freq_hz: float = 0.0,
    nfft: int | None = None,
    window: str = "hann",
) -> tuple[float, float]:
    """Find the dominant frequency and its power in IQ samples.

    Args:
        iq: Complex IQ samples.
        fs: Sample rate in Hz.
        center_freq_hz: Center frequency for absolute output.
        nfft: FFT size for analysis (default: len(iq)).
        window: Window name; must match the FFT window for amplitude calibration.

    Returns:
        (peak_freq_hz, peak_power_dbfs): Power is normalized so that a unit-
        amplitude complex tone (A=1.0) lands at 0 dBFS up to scallop loss.
    """
    freqs, fft_complex = compute_fft(
        iq, fs, window=window, nfft=nfft, center_freq_hz=center_freq_hz
    )
    win = get_window(window, iq.size).astype(np.float64)
    norm = float(np.sum(win))
    magnitude = np.abs(fft_complex) / norm
    idx = int(np.argmax(magnitude))
    peak_power_dbfs = 20.0 * float(np.log10(magnitude[idx] + EPSILON))
    return float(freqs[idx]), peak_power_dbfs
