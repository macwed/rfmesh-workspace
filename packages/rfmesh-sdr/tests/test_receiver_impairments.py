"""Acceptance criteria 1(e), 1(f), 1(g): IQ imbalance, DC offset, ADC quantization.

Each impairment is checked against the textbook closed form. A simulator
that fakes these impairments turns every downstream calibration / sigma
claim into theatre, so we hold the assertions tight: image-rejection
ratio within 0.5 dB, DC bin magnitude within 0.5 dB, SQNR within 1 dB of
``6.02 * N + 1.76 dB``.
"""

from __future__ import annotations

import math

import numpy as np
from rfmesh_sdr import ADCQuantization, DCOffset, IQImbalance

_FS = 2_048_000.0
_N = 32_768
_NUMERIC_NOISE_FLOOR = 1e-10
# Tolerance bands for the textbook closed-form comparisons (per ticket).
_IRR_TOLERANCE_DB = 0.5
_DC_BIN_TOLERANCE_DB = 0.5
_SIGNAL_BIN_TOLERANCE_DB = 0.5
_ADC_SQNR_TOLERANCE_DB = 1.0


def _normalised_fft(x: np.ndarray) -> np.ndarray:
    """FFT normalised so the magnitude at a CW bin equals the tone amplitude."""
    return np.fft.fft(x) / x.size


def _bin_index(freq_hz: float) -> int:
    """Index of the FFT bin closest to ``freq_hz`` for the test (``fs``, ``n``)."""
    return round(freq_hz * _N / _FS) % _N


def test_iq_imbalance_produces_image() -> None:
    """A CW tone at ``+f0`` produces an image at ``-f0`` matching the IRR formula.

    Reference: any digital-communications textbook chapter on direct-
    conversion receivers (e.g. Razavi). Closed form
    ``IRR_dB ~= -20*log10(0.5*|epsilon|)`` with
    ``epsilon = 1 - g*exp(j*phi)``, where ``g = 10**(amplitude_db/20)``
    and ``phi = radians(phase_deg)``.
    """
    amplitude_db = 0.5
    phase_deg = 3.0
    impair = IQImbalance(amplitude_db=amplitude_db, phase_deg=phase_deg)

    f0 = 200_000.0
    t = np.arange(_N) / _FS
    tone = np.exp(1j * 2.0 * math.pi * f0 * t).astype(np.complex128)
    output = impair.apply(tone, np.random.default_rng(0))

    y = _normalised_fft(output)
    signal_bin = _bin_index(+f0)
    image_bin = _bin_index(-f0)

    signal_magnitude = float(np.abs(y[signal_bin]))
    image_magnitude = float(np.abs(y[image_bin]))
    measured_irr_db = 20.0 * math.log10(signal_magnitude / image_magnitude)

    # Closed-form IRR. The approximation assumes |signal coefficient| ~ 1;
    # the exact formula is 20*log10(|A|/|B|) with
    # A = (1 + g*exp(j*phi))/2, B = (1 - g*exp(j*phi))/2.
    g = 10.0 ** (amplitude_db / 20.0)
    phi = math.radians(phase_deg)
    epsilon = 1.0 - g * (math.cos(phi) + 1j * math.sin(phi))
    expected_irr_db = -20.0 * math.log10(0.5 * float(np.abs(epsilon)))

    assert abs(measured_irr_db - expected_irr_db) < _IRR_TOLERANCE_DB, (
        f"IQ imbalance image-rejection ratio: measured {measured_irr_db:.3f} dB, "
        f"expected {expected_irr_db:.3f} dB."
    )


def test_dc_offset_appears_at_dc() -> None:
    """``DCOffset`` adds a tone at DC with the expected magnitude, untouched elsewhere."""
    i_volts = 0.02
    q_volts = -0.01
    impair = DCOffset(i_volts=i_volts, q_volts=q_volts)

    f0 = 100_000.0
    t = np.arange(_N) / _FS
    tone = np.exp(1j * 2.0 * math.pi * f0 * t).astype(np.complex128)
    output = impair.apply(tone, np.random.default_rng(0))

    y = _normalised_fft(output)
    dc_bin = 0
    signal_bin = _bin_index(f0)

    measured_dc_magnitude = float(np.abs(y[dc_bin]))
    expected_dc_magnitude = math.sqrt(i_volts * i_volts + q_volts * q_volts)
    ratio_db = 20.0 * math.log10(measured_dc_magnitude / expected_dc_magnitude)
    assert abs(ratio_db) < _DC_BIN_TOLERANCE_DB, (
        f"DC bin magnitude: measured {measured_dc_magnitude:.5f}, "
        f"expected {expected_dc_magnitude:.5f} (ratio {ratio_db:+.2f} dB)."
    )

    # The signal bin is unaffected (CW tone amplitude is 1).
    measured_signal_magnitude = float(np.abs(y[signal_bin]))
    signal_ratio_db = 20.0 * math.log10(measured_signal_magnitude)
    assert abs(signal_ratio_db) < _SIGNAL_BIN_TOLERANCE_DB, (
        f"Signal bin magnitude after DC injection: measured {measured_signal_magnitude:.5f}, "
        f"expected 1.0 (ratio {signal_ratio_db:+.2f} dB)."
    )

    # Other bins (not DC, not signal) are at numerical-noise level.
    mask = np.ones(_N, dtype=bool)
    mask[dc_bin] = False
    mask[signal_bin] = False
    other_max = float(np.max(np.abs(y[mask])))
    assert other_max < _NUMERIC_NOISE_FLOOR, (
        f"DC offset leaked energy into off-bin: max |Y| in other bins is {other_max:.3e}."
    )


def test_adc_quantization_floor() -> None:
    """8-bit ``ADCQuantization`` caps the achievable SNR at ``6.02*N + 1.76 dB``.

    Start with a near-full-scale complex tone and additive Gaussian noise
    at 80 dB SNR (i.e. far below what the ADC can resolve). After
    quantization the noise floor is dominated by quantisation noise, and
    the recovered SNR collapses to the theoretical SQNR for 8 bits.
    """
    bits = 8
    impair = ADCQuantization(bits=bits)

    signal_amp = 0.95  # leave a small margin so AWGN does not clip the ADC
    sigma_pre = signal_amp * (10.0 ** (-80.0 / 20.0))  # 80 dB below signal
    f0 = 200_000.0

    rng_pre = np.random.default_rng(0)
    t = np.arange(_N) / _FS
    tone = signal_amp * np.exp(1j * 2.0 * math.pi * f0 * t)
    noise_re = rng_pre.standard_normal(_N) * (sigma_pre / math.sqrt(2.0))
    noise_im = rng_pre.standard_normal(_N) * (sigma_pre / math.sqrt(2.0))
    clean = (tone + noise_re + 1j * noise_im).astype(np.complex128)

    quantized = impair.apply(clean, np.random.default_rng(1))

    y = _normalised_fft(quantized)
    signal_bin = _bin_index(f0)
    # Power per bin: |Y[k]|^2 (with this FFT normalisation, the signal bin
    # equals the tone amplitude). Total time-domain SNR is signal_power
    # over total noise power; Parseval ties the time-domain noise variance
    # to the sum of bin powers excluding signal and DC.
    signal_power = float(np.abs(y[signal_bin]) ** 2)
    mask = np.ones(_N, dtype=bool)
    mask[signal_bin] = False
    mask[0] = False  # exclude DC bin (any residual rounding bias lands there)
    noise_total_power = float(np.sum(np.abs(y[mask]) ** 2))
    snr_db = 10.0 * math.log10(signal_power / noise_total_power)

    expected_snr_db = 6.02 * bits + 1.76
    # 0.95 vs 1.0 full-scale costs ~0.4 dB; assert within 1 dB per the ticket.
    assert abs(snr_db - expected_snr_db) < _ADC_SQNR_TOLERANCE_DB, (
        f"8-bit ADC SNR: measured {snr_db:.2f} dB, expected ~{expected_snr_db:.2f} dB."
    )
