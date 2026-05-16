"""Acceptance criteria 1(a) and 1(b): two-ray ground and FIR multipath channels.

The tests probe the closed-form physics of each channel against the
implementation. A simulator that lies about path loss or that produces
wrong spectral ripple makes every downstream sigma-honesty claim a fiction
(``ARCHITECTURE.md`` Section 4 honesty constraint), so the assertions are
deliberately tight (within a few dB) and tied to textbook formulas.
"""

from __future__ import annotations

import math

import numpy as np
from rfmesh_sdr import MultipathFIRChannel, TwoRayGroundChannel

_SPEED_OF_LIGHT_M_PER_S = 299_792_458.0
_FRIIS_FOUR_PI = 4.0 * math.pi

# Two-ray assertion thresholds. The near-regime median ratio of two-ray to
# Friis power is theoretically 10*log10(2) ~= +3.01 dB (median of
# 10*log10(4*sin^2(x)) for uniform x); we accept anywhere in (-3, +6) dB.
_NEAR_REGIME_MEDIAN_MIN_DB = -3.0
_NEAR_REGIME_MEDIAN_MAX_DB = 6.0
# Far-field slope should be -40 dB/decade (1/d^4); accept within 3 dB.
_FAR_FIELD_SLOPE_TOLERANCE_DB_PER_DECADE = 3.0
# The deep null at d_bp/2 must be at least 10 dB below Friis.
_NULL_DEPTH_MAX_DB = -10.0
# Per-frequency tolerance for the closed-form |H(f)| check.
_HF_TOLERANCE_DB = 0.5


def _friis_amplitude(distance_m: float, frequency_hz: float) -> float:
    """Free-space (Friis) electric-field amplitude factor used as the reference."""
    wavelength_m = _SPEED_OF_LIGHT_M_PER_S / frequency_hz
    return wavelength_m / (_FRIIS_FOUR_PI * distance_m)


def _two_ray_amplitude(
    channel: TwoRayGroundChannel,
    distance_m: float,
    frequency_hz: float,
) -> float:
    """Apply the channel to a unit complex sample and return the magnitude."""
    sample = np.array([1.0 + 0.0j], dtype=np.complex128)
    rng = np.random.default_rng(0)
    out = channel.apply(sample, distance_m, frequency_hz, rng)
    return float(np.abs(out[0]))


def test_two_ray_constructive_destructive() -> None:
    """``TwoRayGroundChannel`` reproduces the textbook two-ray power profile.

    With ``h_tx = 2.0 m``, ``h_rx = 1.5 m``, ``f = 915 MHz`` the breakpoint
    is ``d_bp = 4*h_tx*h_rx/lambda ~= 36.6 m``. The closed-form behaviour
    (Rappaport, Goldsmith) is:

    * Near regime ``d << d_bp``: the response oscillates around Friis with
      ``+6 dB`` constructive peaks (where ``Delta_d = (2n+1)*lambda/2``) and
      deep nulls (where ``Delta_d = n*lambda``). The *median* power across
      many oscillations is ~``2 x Friis`` (``+3 dB``).
    * Far regime ``d >> d_bp``: power falls as ``1/d^4`` (-40 dB/decade).
    * The *last* deep null is at ``d_null = 2*h_tx*h_rx/lambda = d_bp/2``
      (this is the strict physics location -- the WS-A-003 ticket says
      "null at ``d ~= d_bp``", but the actual null in the canonical
      Gamma = -1 model is at ``d_bp/2``; ``d_bp`` itself is the LAST
      constructive peak).
    """
    h_tx = 2.0
    h_rx = 1.5
    f_hz = 915e6
    wavelength_m = _SPEED_OF_LIGHT_M_PER_S / f_hz
    d_bp = 4.0 * h_tx * h_rx / wavelength_m

    channel = TwoRayGroundChannel(height_tx_m=h_tx, height_rx_m=h_rx)

    # --- (i) Near regime: many oscillations within d < d_bp, median ~ Friis ---
    # Sweep d well inside d_bp at irrational spacing so the sample grid does
    # not land on a single null-or-peak harmonic.
    d_near = np.linspace(5.0, 0.9 * d_bp, 401)
    two_ray_power_db = np.empty_like(d_near)
    friis_power_db = np.empty_like(d_near)
    for i, d in enumerate(d_near):
        two_ray_power_db[i] = 20.0 * math.log10(_two_ray_amplitude(channel, float(d), f_hz))
        friis_power_db[i] = 20.0 * math.log10(_friis_amplitude(float(d), f_hz))
    median_ratio_db = float(np.median(two_ray_power_db - friis_power_db))
    # Theoretical median of 10*log10(4*sin^2(x)) over uniform x in [0, pi) is
    # 10*log10(2) = +3.01 dB; "within 3 dB of Friis" then means
    # |median_ratio - 3.01| < 3 dB or, equivalently, median in (0, +6) dB.
    assert _NEAR_REGIME_MEDIAN_MIN_DB <= median_ratio_db <= _NEAR_REGIME_MEDIAN_MAX_DB, (
        f"Two-ray median power vs Friis in the near regime is {median_ratio_db:.2f} dB; "
        "expected median around +3 dB."
    )

    # --- (ii) Far regime: slope of -40 dB/decade across one decade past 2*d_bp ---
    d_far_1 = 3.0 * d_bp
    d_far_2 = 30.0 * d_bp
    p1_db = 20.0 * math.log10(_two_ray_amplitude(channel, d_far_1, f_hz))
    p2_db = 20.0 * math.log10(_two_ray_amplitude(channel, d_far_2, f_hz))
    decades = math.log10(d_far_2 / d_far_1)
    slope_db_per_decade = (p2_db - p1_db) / decades
    assert abs(slope_db_per_decade - (-40.0)) < _FAR_FIELD_SLOPE_TOLERANCE_DB_PER_DECADE, (
        f"Two-ray far-field slope is {slope_db_per_decade:.2f} dB/decade; "
        "expected -40 dB/decade (1/d^4)."
    )

    # --- (iii) Deepest null: at d = 2*h_tx*h_rx/lambda = d_bp/2 ---
    # See the docstring: with Gamma = -1, the canonical nulls fall at
    # Delta_d = n*lambda, which maps to d = 2*h*h/(n*lambda). The first
    # (deepest, last) null sits at d_bp/2; d_bp itself is the +6 dB
    # constructive peak, not a null.
    d_null = 2.0 * h_tx * h_rx / wavelength_m
    null_power_db = 20.0 * math.log10(_two_ray_amplitude(channel, d_null, f_hz))
    friis_power_db_at_null = 20.0 * math.log10(_friis_amplitude(d_null, f_hz))
    null_depth_db = null_power_db - friis_power_db_at_null
    assert null_depth_db < _NULL_DEPTH_MAX_DB, (
        f"Two-ray null at d = {d_null:.2f} m (= d_bp/2) is "
        f"{null_depth_db:.1f} dB vs Friis; expected at least 10 dB below."
    )


def test_fir_multipath_produces_iq_ripple() -> None:
    """``MultipathFIRChannel`` produces the closed-form ``|H(f)|`` ripple.

    With two taps ``[1.0+0j, 0.7*exp(j*pi/3)]`` at delays ``[0, 50]`` samples,
    the transfer function is

        H(f) = 1 + 0.7 * exp(j*(pi/3 - 2*pi*f*50/fs))

    A unit-amplitude CW tone at baseband offset ``f_test`` therefore yields
    a steady-state output whose mean magnitude equals ``|H(f_test)|`` within
    numerical noise. We check that at three test frequencies the measured
    magnitude matches the closed form within 0.5 dB.
    """
    taps = np.array([1.0 + 0.0j, 0.7 * np.exp(1j * math.pi / 3.0)], dtype=np.complex128)
    delays = np.array([0, 50], dtype=np.int64)
    channel = MultipathFIRChannel(taps=taps, delays=delays)

    fs = 2_048_000.0
    n = 8192
    rng = np.random.default_rng(0)

    # Three baseband offsets spanning positive and negative frequency.
    test_freqs_hz = [-300_000.0, 125_000.0, 450_000.0]

    # The mode='same' convolution introduces an edge transient of length
    # max_delay = 50 at each end. Skip generously so the steady-state mean
    # is unbiased.
    transient_skip = 100

    for f_test in test_freqs_hz:
        t = np.arange(n) / fs
        tone = np.exp(1j * 2.0 * math.pi * f_test * t).astype(np.complex128)
        # distance/frequency are unused by MultipathFIR; pass plausible values.
        output = channel.apply(tone, distance_m=1000.0, frequency_hz=915e6, rng=rng)

        steady = output[transient_skip : n - transient_skip]
        measured_magnitude = float(np.mean(np.abs(steady)))

        h_value = 1.0 + 0.7 * np.exp(1j * (math.pi / 3.0 - 2.0 * math.pi * f_test * 50.0 / fs))
        expected_magnitude = float(np.abs(h_value))

        ratio_db = 20.0 * math.log10(measured_magnitude / expected_magnitude)
        assert abs(ratio_db) < _HF_TOLERANCE_DB, (
            f"FIR multipath magnitude at f={f_test:+.0f} Hz: "
            f"measured {measured_magnitude:.5f}, expected {expected_magnitude:.5f} "
            f"(ratio {ratio_db:+.2f} dB)."
        )
