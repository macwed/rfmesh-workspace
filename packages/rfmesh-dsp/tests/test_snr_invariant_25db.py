r"""Regression test for the 25 dB SNR-invariant error (INHERITED_CONTEXT.md Section 5.1).

CONTEXT
-------
A proposed test invariant in the prior project was
``in_band_snr >= peak_snr - 5``. For a CW signal the correct relationship
is much wider -- peak SNR concentrates all signal power into one FFT bin
(resolution ``fs / nfft``) while in-band SNR spreads the same signal
across a wider integration bandwidth (``BW``). The mistaken 5-dB invariant
would have masked a real DSP regression.

This test pins the salvaged spectral primitives
(``find_spectral_peak``, ``compute_rssi_in_band``, ``compute_snr_db``)
against their analytical expectations under known-input CW+AWGN, and
documents the correct peak-vs-in-band closed form. The relationship is
derived here rather than asserted as a magic constant, so a future
maintainer who changes ``nfft`` or ``BW`` updates the derivation in
lockstep.

CLOSED-FORM DERIVATION (CW + complex AWGN; on-bin Hann window)
--------------------------------------------------------------
Let
* ``A``       -- complex CW amplitude, signal power ``A**2``.
* ``sigma_n`` -- AWGN total complex std, noise variance ``sigma_n**2``.
* ``SNR``     = ``A**2 / sigma_n**2`` (true linear SNR).
* ``fs``      -- sample rate, Hz.
* ``nfft``    -- FFT length used by ``find_spectral_peak``.
* ``BW``      -- integration band width for ``compute_rssi_in_band``.
* ``BW_n``    -- a quiet noise band (no signal in it).

(1) ``find_spectral_peak`` returns
        peak_power_dbfs = 20 * log10(A)
    when ``f_0 = k * fs / nfft`` (no scallop loss).

(2) ``compute_rssi_in_band(signal band)`` returns
        in_band_signal_dbfs = 10 * log10(A**2 + sigma_n**2 * BW / fs).

(3) ``compute_rssi_in_band(noise band)`` returns
        in_band_noise_dbfs = 10 * log10(sigma_n**2 * BW_n / fs).

(4) ``compute_snr_db(signal band, noise band)`` returns
        snr_db = (2) - (3)
               = 10 * log10((A**2 + sigma_n**2 * BW / fs)
                            / (sigma_n**2 * BW_n / fs)).

(5) The "peak SNR" -- peak bin amplitude vs per-FFT-bin noise amplitude
    -- depends on which FFT (rectangular or Hann) is used for the noise
    reference. For Hann:
        per_bin_noise_dbfs_hann = 10 * log10(sigma_n**2 * 1.5 / nfft)
        peak_snr_dbfs_hann      = (1) - per_bin_noise_dbfs_hann
                                = SNR_dB + 10*log10(nfft / 1.5).

    The 1.5 / nfft factor is the Hann processing gain
    (``sum(w**2) / sum(w)**2``); rectangular would give 1 / nfft. The
    salvaged ``find_spectral_peak`` defaults to Hann.

(6) The PEAK-VS-IN-BAND DIFFERENCE (the regression anchor):
        peak_snr_dbfs - snr_db
            = 10*log10(nfft / 1.5) - 10*log10(fs / BW_n)
            = 10*log10(nfft * BW_n / (1.5 * fs)).
    This depends on the ratio of bandwidths, NOT on SNR. The wrong
    invariant ``in_band_snr >= peak_snr - 5`` ignores this ratio and is
    off by 10*log10(nfft * BW_n / (1.5 * fs)) - 5 dB -- for the
    parameters here, ~14 dB; for narrower bands, the gap grows toward
    the 25-dB regime the inherited context warned about.

NOTE on ``compute_noise_floor_dbfs``: deliberately NOT in this test.
Its median-of-PSD estimator carries a separate chi-square-median bias
that is a different test concern. Here we use ``compute_rssi_in_band``
on an explicit quiet band for the noise reference, which is unbiased.
"""

from __future__ import annotations

import math

import numpy as np
from rfmesh_dsp import compute_rssi_in_band, compute_snr_db, find_spectral_peak

_FS_HZ = 2_048_000.0
_NFFT = 16384  # find_spectral_peak FFT length
_PSD_NPERSEG = 4096  # Welch segment for compute_rssi_in_band
_SIGNAL_FREQ_HZ = 50_000.0  # 400 * fs/nfft -- exact bin centre
_BW_HZ = 16384.0  # signal-band integration width
_NOISE_BAND_LOW = 200_000.0
_NOISE_BAND_HIGH = 200_000.0 + _BW_HZ  # noise-band width matches signal-band width
_A = 1.0  # CW complex amplitude
_SIGMA_N = 0.1  # AWGN std -> noise variance 0.01
_TRUE_SNR_DB = 10.0 * math.log10(_A**2 / _SIGMA_N**2)
_TOLERANCE_DB = 0.5
# Hann processing gain: sum(w**2) / sum(w)**2 for a length-N Hann is
# (3N/8) / (N/2)**2 = 1.5 / N. The per-FFT-bin noise variance under a
# Hann window is therefore 1.5x what a rectangular FFT would yield.
_HANN_PROCESSING_GAIN = 1.5
# Floor on peak_snr - in_band_snr that proves the wrong 5-dB invariant
# would not hold for the chosen parameters. Closed-form (6) yields
# ~19 dB, well above this floor.
_WRONG_INVARIANT_FLOOR_DB = 10.0


def _make_iq() -> np.ndarray:
    """Generate the canonical CW + complex AWGN test signal."""
    rng = np.random.default_rng(123)
    t = np.arange(_NFFT) / _FS_HZ
    signal = _A * np.exp(1j * 2.0 * math.pi * _SIGNAL_FREQ_HZ * t)
    noise_re = rng.standard_normal(_NFFT) * _SIGMA_N / math.sqrt(2.0)
    noise_im = rng.standard_normal(_NFFT) * _SIGMA_N / math.sqrt(2.0)
    iq: np.ndarray = (signal + noise_re + 1j * noise_im).astype(np.complex64)
    return iq


def test_cw_peak_vs_in_band_snr() -> None:
    """Each salvaged spectral primitive matches its closed-form prediction within 0.5 dB.

    Verifies (1) through (6) of the module-docstring derivation. The
    relationship (6) is the actual regression anchor for the 25-dB story
    -- a future change that breaks compute_rssi_in_band or
    find_spectral_peak would also break (6) and fail loudly here.
    """
    iq = _make_iq()
    sig_band_low = _SIGNAL_FREQ_HZ - _BW_HZ / 2.0
    sig_band_high = _SIGNAL_FREQ_HZ + _BW_HZ / 2.0

    # (1) Peak amplitude at the FFT-bin resolution.
    peak_f, peak_power_dbfs = find_spectral_peak(iq, _FS_HZ, nfft=_NFFT)
    peak_power_pred = 20.0 * math.log10(_A)
    assert abs(peak_f - _SIGNAL_FREQ_HZ) < _FS_HZ / _NFFT, (
        f"peak frequency {peak_f} Hz not within one FFT bin of {_SIGNAL_FREQ_HZ} Hz."
    )
    assert abs(peak_power_dbfs - peak_power_pred) <= _TOLERANCE_DB, (
        f"peak_power_dbfs = {peak_power_dbfs:.3f}, predicted {peak_power_pred:.3f} "
        f"(closed-form (1), |diff| > {_TOLERANCE_DB} dB)."
    )

    # (2) In-band signal power = signal + noise across BW.
    in_band_signal_dbfs = compute_rssi_in_band(
        iq,
        _FS_HZ,
        band_low_hz=sig_band_low,
        band_high_hz=sig_band_high,
        nperseg=_PSD_NPERSEG,
    )
    in_band_signal_pred = 10.0 * math.log10(_A**2 + _SIGMA_N**2 * _BW_HZ / _FS_HZ)
    assert abs(in_band_signal_dbfs - in_band_signal_pred) <= _TOLERANCE_DB, (
        f"in_band_signal_dbfs = {in_band_signal_dbfs:.3f}, predicted "
        f"{in_band_signal_pred:.3f} (closed-form (2), |diff| > {_TOLERANCE_DB} dB)."
    )

    # (3) In-band noise power = noise only across BW_n.
    in_band_noise_dbfs = compute_rssi_in_band(
        iq,
        _FS_HZ,
        band_low_hz=_NOISE_BAND_LOW,
        band_high_hz=_NOISE_BAND_HIGH,
        nperseg=_PSD_NPERSEG,
    )
    bw_n = _NOISE_BAND_HIGH - _NOISE_BAND_LOW
    in_band_noise_pred = 10.0 * math.log10(_SIGMA_N**2 * bw_n / _FS_HZ)
    assert abs(in_band_noise_dbfs - in_band_noise_pred) <= _TOLERANCE_DB, (
        f"in_band_noise_dbfs = {in_band_noise_dbfs:.3f}, predicted "
        f"{in_band_noise_pred:.3f} (closed-form (3), |diff| > {_TOLERANCE_DB} dB)."
    )

    # (4) In-band SNR via compute_snr_db with explicit quiet noise band
    # (avoids the chi-square-median bias of compute_noise_floor_dbfs).
    snr_db = compute_snr_db(
        iq,
        _FS_HZ,
        signal_band_hz=(sig_band_low, sig_band_high),
        noise_band_hz=(_NOISE_BAND_LOW, _NOISE_BAND_HIGH),
        nperseg=_PSD_NPERSEG,
    )
    snr_db_pred = in_band_signal_pred - in_band_noise_pred
    assert abs(snr_db - snr_db_pred) <= _TOLERANCE_DB, (
        f"compute_snr_db = {snr_db:.3f}, predicted {snr_db_pred:.3f} "
        f"(closed-form (4), |diff| > {_TOLERANCE_DB} dB)."
    )

    # (5) Peak SNR -- peak bin amplitude vs Hann per-FFT-bin noise power.
    # Per-FFT-bin Hann noise variance = sigma_n**2 * 1.5 / nfft (derived
    # from sum(w**2)/sum(w)**2 for Hann; the salvaged find_spectral_peak
    # defaults to Hann).
    per_bin_noise_hann_dbfs = 10.0 * math.log10(_SIGMA_N**2 * _HANN_PROCESSING_GAIN / _NFFT)
    peak_snr_dbfs = peak_power_dbfs - per_bin_noise_hann_dbfs
    peak_snr_pred = _TRUE_SNR_DB + 10.0 * math.log10(_NFFT / _HANN_PROCESSING_GAIN)
    assert abs(peak_snr_dbfs - peak_snr_pred) <= _TOLERANCE_DB, (
        f"peak_snr_dbfs = {peak_snr_dbfs:.3f}, predicted {peak_snr_pred:.3f} "
        f"(closed-form (5), |diff| > {_TOLERANCE_DB} dB)."
    )

    # (6) The peak-vs-in-band relationship -- the regression anchor.
    diff_pred = 10.0 * math.log10(_NFFT * bw_n / (_HANN_PROCESSING_GAIN * _FS_HZ))
    diff_measured = peak_snr_dbfs - snr_db
    # The per-side tolerances combine: peak_snr (eq 5) has 0.5 dB band
    # and snr_db (eq 4) has 0.5 dB band, so the difference may drift by
    # up to 1 dB.
    assert abs(diff_measured - diff_pred) <= 2.0 * _TOLERANCE_DB, (
        f"peak_snr - in_band_snr = {diff_measured:.3f} dB, predicted "
        f"{diff_pred:.3f} dB (closed-form (6), |diff| > {2.0 * _TOLERANCE_DB} dB)."
    )

    # And the wrong invariant "in_band_snr >= peak_snr - 5" -- the bug
    # the prior project caught -- is off by >10 dB at these parameters.
    # An explicit assertion documents that future "simplifications" to a
    # constant invariant fail loudly here, not silently.
    assert diff_measured > _WRONG_INVARIANT_FLOOR_DB, (
        f"peak_snr - in_band_snr = {diff_measured:.3f} dB; the wrong "
        "invariant 'in_band_snr >= peak_snr - 5' would silently pass "
        "but is off by >5 dB. See INHERITED_CONTEXT.md Section 5.1."
    )
