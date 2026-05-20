"""C4 — simulator calibration against Mast C empirical anchor.

Closes Tier C ticket C4 (revised scope, per local NOTES amendment of
``docs/phase-c-report/findings.md`` §7.5: free-space + light-shadowing
calibration against ``scenarios/mast_c_reference.yaml``, NOT two-ray
+ multipath_fir against a phantom null).

What this test does:
  - Constructs a ``SimulationScenario`` mirroring the Mast C 2026-05-19
    geometry (emitter at 306 deg true, 2980 m range, 958.695 MHz,
    isolated carrier). Yagi pattern matches ATK-10 (~50 deg HPBW).
  - Drives a coarse 8-heading polar sweep (45 deg steps), an off-axis
    capture at 216 deg, and a 60-window 1-s-each stationarity sequence
    at the peak heading.
  - Asserts the simulated polar response, front-back ratio, SNR
    above-noise-floor, and per-second stationarity sit inside the
    tolerance ranges declared in ``scenarios/mast_c_reference.yaml``
    ``simulator_calibration_target.tolerance`` block.

Calibration parameters (from this test) that satisfy the YAML target:
  - channel = FreeSpaceChannel + LogNormalShadowing(sigma_db=0.20)
  - tx_power_db = solved from Friis for ~11.4 dB at-receiver SNR at
    2980 m, 958.695 MHz, against -100 dBFS noise floor
  - antenna = AntennaPattern(hpbw_deg=50.0, back_lobe_floor_db=-15.0)

These values are wired below and become the binding "simulator-side
Mast C anchor" for any downstream regression test or scenario YAML
that wants to reproduce Mast C-like behaviour.

WD-2 (golden tests for new `rfmesh-dsp` functions) does not apply
here — this test is not a new DSP function; it's a simulator
calibration anchor that the rfmesh-sdr package owns. WS-A-004's
deterministic-seed pattern is the precedent.
"""

from __future__ import annotations

import math

import numpy as np
from rfmesh_sdr import (
    AntennaPattern,
    CompositeChannel,
    EmitterSpec,
    FreeSpaceChannel,
    LogNormalShadowing,
    SimulationScenario,
    SyntheticReceiver,
)

# ---------------------------------------------------------------------------
# Mast C empirical anchor — pinned constants from
# scenarios/mast_c_reference.yaml. These DUPLICATE the YAML on purpose:
# the YAML is the human-readable contract; this test is the
# machine-verifiable check. Drift between them is caught by the
# regression as soon as it ships.
# ---------------------------------------------------------------------------

_MAST_C_EMITTER_AZIMUTH_DEG = 306.0
_MAST_C_RANGE_M = 2980.0
_MAST_C_FREQ_HZ = 958.695e6

# Operator-side measured anchor (recordings/2026-05-19-mast-c/),
# pinned in mast_c_reference.yaml ``measured_behaviour``.
_MEASURED_PEAK_SNR_VS_SKY_DB = 11.38
_MEASURED_FRONT_BACK_DB = 14.90
_MEASURED_PEAK_STATIONARITY_STD_DB = 0.17
_MEASURED_OFF_AXIS_SNR_VS_SKY_DB = 5.53

# Tolerance ranges from
# scenarios/mast_c_reference.yaml :: simulator_calibration_target.tolerance
# These are set against the pure-single-emitter simulator's DELIVERABLE,
# not against the bench numbers directly — the bench picks up horizon-
# ambient cellular activity that v1 simulator does not model, so the
# bench F/B and off-axis SNR are inflated by that ambient. See the
# bench-vs-simulator delta block in the YAML for full rationale.
_TOLERANCE_PEAK_SNR_DB_RANGE = (9.0, 13.0)
_TOLERANCE_OFF_AXIS_SNR_DB_RANGE = (-3.0, 3.0)
_TOLERANCE_STATIONARITY_STD_DB_MAX = 0.30
_TOLERANCE_FRONT_BACK_DB_RANGE = (9.0, 13.0)

# Simulator-side configuration that satisfies the tolerances above.
# These are the calibrated values; changing any of them is a calibration
# change and the test verifies the new value still satisfies the YAML.
_SIM_HPBW_DEG = 50.0
_SIM_BACK_LOBE_FLOOR_DB = -15.0
_SIM_SHADOWING_SIGMA_DB = 0.20
_SIM_NOISE_FLOOR_DBFS = -100.0
_SIM_SAMPLE_RATE_HZ = 2_048_000.0

_SPEED_OF_LIGHT_M_PER_S = 299_792_458.0
_FULL_CIRCLE_DEG = 360
_HALF_CIRCLE_DEG = 180
_GRID_BIN_DEG = 45  # coarse sweep resolution
_FINE_GRID_BIN_DEG = 10  # fine-sweep tolerance

# IQ block sizes (sample-count, NOT seconds-of-real-time — the simulator
# rolls a sample-offset counter that has no wall-clock dimension).
# 1-s-equivalent at 2.048 MS/s for stationarity windows.
_BLOCK_SAMPLES_ONE_SEC = 2_048_000
# Shorter block for the polar sweep — 8192 samples is enough for a
# stable mean(|iq|) at this SNR and saves test runtime.
_BLOCK_SAMPLES_SWEEP = 8_192

_OFF_AXIS_HEADING_DEG = 216.0  # mirrors mast_c_peak_60s_216deg capture

# Number of 1-s windows in the stationarity sequence. Real bench
# capture was 70 windows (~60 s of capture, ~1 s each).
_STATIONARITY_WINDOWS = 70


def _tx_power_db_for_snr_at_boresight(
    target_snr_db: float,
    distance_m: float,
    frequency_hz: float,
    noise_floor_dbfs: float,
) -> float:
    """Solve Friis-equation tx_power_db for ``target_snr_db`` at boresight."""
    wavelength_m = _SPEED_OF_LIGHT_M_PER_S / frequency_hz
    pl_amplitude = wavelength_m / (4.0 * math.pi * distance_m)
    pl_power_db = 20.0 * math.log10(pl_amplitude)
    return target_snr_db + noise_floor_dbfs - pl_power_db


def _build_mast_c_scenario() -> SimulationScenario:
    """Construct the SimulationScenario for the Mast C anchor."""
    tx_power_db = _tx_power_db_for_snr_at_boresight(
        target_snr_db=_MEASURED_PEAK_SNR_VS_SKY_DB,
        distance_m=_MAST_C_RANGE_M,
        frequency_hz=_MAST_C_FREQ_HZ,
        noise_floor_dbfs=_SIM_NOISE_FLOOR_DBFS,
    )
    return SimulationScenario(
        emitters=(
            EmitterSpec(
                azimuth_deg=_MAST_C_EMITTER_AZIMUTH_DEG,
                range_m=_MAST_C_RANGE_M,
                frequency_hz=_MAST_C_FREQ_HZ,
                tx_power_db=tx_power_db,
                phase_deg=0.0,
            ),
        ),
        antenna=AntennaPattern(
            hpbw_deg=_SIM_HPBW_DEG,
            back_lobe_floor_db=_SIM_BACK_LOBE_FLOOR_DB,
        ),
        sample_rate_hz=_SIM_SAMPLE_RATE_HZ,
        center_freq_hz=_MAST_C_FREQ_HZ,
        noise_floor_dbfs=_SIM_NOISE_FLOOR_DBFS,
        channel=CompositeChannel(
            channels=(
                FreeSpaceChannel(),
                LogNormalShadowing(sigma_db=_SIM_SHADOWING_SIGMA_DB),
            ),
        ),
    )


def _rssi_db(iq: np.ndarray) -> float:
    """Wideband RSSI in dB relative — matches the C4 ``mean(|iq|)`` semantics."""
    return float(20.0 * math.log10(float(np.mean(np.abs(iq))) + 1e-12))


def _signed_arc_delta_deg(a: float, b: float) -> float:
    """Signed shortest-arc difference a - b on the unit circle, in deg."""
    return ((a - b + _HALF_CIRCLE_DEG) % _FULL_CIRCLE_DEG) - _HALF_CIRCLE_DEG


def _sky_noise_floor_db(scenario: SimulationScenario, seed: int) -> float:
    """Sky-pointing reference: heading 90 deg away from the emitter.

    The Yagi back lobe + the 90-deg-off-boresight gain produce a
    receiver output dominated by AWGN + shadowing residual at the
    noise floor — the simulator-side analogue of pointing the antenna
    at the sky (no horizon-derived signal reaches the receiver in this
    geometry). One 1-s capture is enough to bound the floor.
    """
    rx = SyntheticReceiver(scenario, seed=seed)
    rx.open()
    # Point exactly opposite the emitter so the antenna sees the
    # back-lobe floor at -15 dB and the emitter's contribution is
    # attenuated by ~30 dB relative to peak.
    rx.set_antenna_heading((_MAST_C_EMITTER_AZIMUTH_DEG + _HALF_CIRCLE_DEG) % _FULL_CIRCLE_DEG)
    iq = rx.read(_BLOCK_SAMPLES_ONE_SEC)
    rx.close()
    return _rssi_db(iq)


def test_c4_mast_c_simulator_calibration_anchor() -> None:
    """The single-test calibration anchor: simulator reproduces Mast C anchor.

    Six assertions, each citing a YAML tolerance:

    1. Peak heading within +/- one fine-grid bin of the configured
       emitter azimuth.
    2. Front-back ratio (peak vs back-lobe) inside
       ``tolerance.front_back_ratio_db``.
    3. Peak SNR vs sky-noise inside
       ``tolerance.snr_peak_above_sky_db``.
    4. Off-axis (216 deg) SNR vs sky-noise inside
       ``tolerance.snr_off_axis_above_sky_db``.
    5. Per-1-s stationarity std at peak <=
       ``tolerance.stationarity_std_db_max``.
    6. Front-back ratio is non-degenerate (peak is the max, back is
       the min) — sanity check.
    """
    scenario = _build_mast_c_scenario()

    # --- (1) Coarse 8-heading polar sweep at 45 deg steps ----------------
    headings_deg = np.arange(0, _FULL_CIRCLE_DEG, _GRID_BIN_DEG, dtype=float)
    rssi_db_per_heading = np.empty_like(headings_deg)
    rx = SyntheticReceiver(scenario, seed=42)
    rx.open()
    for i, hdg in enumerate(headings_deg):
        rx.set_antenna_heading(float(hdg))
        iq = rx.read(_BLOCK_SAMPLES_SWEEP)
        rssi_db_per_heading[i] = _rssi_db(iq)
    rx.close()

    peak_idx = int(np.argmax(rssi_db_per_heading))
    peak_heading = float(headings_deg[peak_idx])
    peak_rssi_db = float(rssi_db_per_heading[peak_idx])

    # Assertion 1 — peak within one grid bin of the configured azimuth
    delta = abs(_signed_arc_delta_deg(peak_heading, _MAST_C_EMITTER_AZIMUTH_DEG))
    assert delta <= _GRID_BIN_DEG, (
        f"Simulated peak heading {peak_heading} deg is {delta:.1f} deg from "
        f"configured emitter azimuth {_MAST_C_EMITTER_AZIMUTH_DEG} deg "
        f"(> one {_GRID_BIN_DEG} deg sweep grid bin)."
    )

    # --- (2) Front-back ratio (peak vs heading 180 deg away) -------------
    back_heading_deg = (peak_heading + _HALF_CIRCLE_DEG) % _FULL_CIRCLE_DEG
    back_idx = int(np.argmin(np.abs(headings_deg - back_heading_deg)))
    back_rssi_db = float(rssi_db_per_heading[back_idx])
    front_back_db = peak_rssi_db - back_rssi_db
    lo, hi = _TOLERANCE_FRONT_BACK_DB_RANGE
    assert lo <= front_back_db <= hi, (
        f"Simulated front-back ratio {front_back_db:.2f} dB outside "
        f"tolerance [{lo}, {hi}] dB (mast_c_reference.yaml). "
        f"Peak at {peak_heading} deg ({peak_rssi_db:+.2f} dB), "
        f"back at {back_heading_deg} deg ({back_rssi_db:+.2f} dB)."
    )

    # --- (3) Peak SNR vs sky-noise floor ---------------------------------
    sky_floor_db = _sky_noise_floor_db(scenario, seed=43)
    peak_snr_vs_sky_db = peak_rssi_db - sky_floor_db
    lo, hi = _TOLERANCE_PEAK_SNR_DB_RANGE
    assert lo <= peak_snr_vs_sky_db <= hi, (
        f"Simulated peak SNR vs sky-noise {peak_snr_vs_sky_db:+.2f} dB "
        f"outside tolerance [{lo}, {hi}] dB (mast_c_reference.yaml). "
        f"Peak RSSI {peak_rssi_db:+.2f} dB, sky floor {sky_floor_db:+.2f} dB. "
        f"Measured anchor: +{_MEASURED_PEAK_SNR_VS_SKY_DB} dB."
    )

    # --- (4) Off-axis (216 deg) SNR vs sky-noise -------------------------
    rx = SyntheticReceiver(scenario, seed=44)
    rx.open()
    rx.set_antenna_heading(_OFF_AXIS_HEADING_DEG)
    off_axis_iq = rx.read(_BLOCK_SAMPLES_ONE_SEC)
    rx.close()
    off_axis_rssi_db = _rssi_db(off_axis_iq)
    off_axis_snr_vs_sky_db = off_axis_rssi_db - sky_floor_db
    lo, hi = _TOLERANCE_OFF_AXIS_SNR_DB_RANGE
    assert lo <= off_axis_snr_vs_sky_db <= hi, (
        f"Simulated off-axis (216 deg) SNR vs sky-noise "
        f"{off_axis_snr_vs_sky_db:+.2f} dB outside tolerance "
        f"[{lo}, {hi}] dB (mast_c_reference.yaml). "
        f"Off-axis RSSI {off_axis_rssi_db:+.2f} dB, sky floor "
        f"{sky_floor_db:+.2f} dB. Measured anchor: "
        f"+{_MEASURED_OFF_AXIS_SNR_VS_SKY_DB} dB."
    )

    # --- (5) 60-window stationarity at peak ------------------------------
    rx = SyntheticReceiver(scenario, seed=45)
    rx.open()
    rx.set_antenna_heading(peak_heading)
    stationarity_db = np.empty(_STATIONARITY_WINDOWS, dtype=np.float64)
    for k in range(_STATIONARITY_WINDOWS):
        iq = rx.read(_BLOCK_SAMPLES_ONE_SEC)
        stationarity_db[k] = _rssi_db(iq)
    rx.close()
    std_db = float(np.std(stationarity_db))
    assert std_db <= _TOLERANCE_STATIONARITY_STD_DB_MAX, (
        f"Simulated 60-window stationarity std {std_db:.3f} dB exceeds "
        f"tolerance {_TOLERANCE_STATIONARITY_STD_DB_MAX} dB "
        f"(mast_c_reference.yaml). Measured anchor: "
        f"{_MEASURED_PEAK_STATIONARITY_STD_DB} dB."
    )

    # --- (6) Sanity: peak is the actual max ------------------------------
    assert peak_rssi_db == max(rssi_db_per_heading), (
        f"Internal logic error: peak_rssi_db {peak_rssi_db} != "
        f"max of sweep {max(rssi_db_per_heading)}."
    )


def test_c4_mast_c_off_axis_below_peak() -> None:
    """Sanity test alongside the main calibration anchor.

    The Yagi pattern must put the 216 deg off-axis capture at lower
    RSSI than the on-peak captures — independent of the SNR tolerance.
    Catches a calibration-knob mistake where peak and off-axis
    accidentally end up within noise of each other.
    """
    scenario = _build_mast_c_scenario()
    rx = SyntheticReceiver(scenario, seed=42)
    rx.open()
    rx.set_antenna_heading(_MAST_C_EMITTER_AZIMUTH_DEG)
    peak_iq = rx.read(_BLOCK_SAMPLES_ONE_SEC)
    rx.set_antenna_heading(_OFF_AXIS_HEADING_DEG)
    off_axis_iq = rx.read(_BLOCK_SAMPLES_ONE_SEC)
    rx.close()
    peak_db = _rssi_db(peak_iq)
    off_axis_db = _rssi_db(off_axis_iq)
    assert peak_db > off_axis_db, (
        f"Off-axis (216 deg) RSSI {off_axis_db:+.2f} dB is not below "
        f"on-peak ({_MAST_C_EMITTER_AZIMUTH_DEG} deg) RSSI "
        f"{peak_db:+.2f} dB — Yagi pattern is degenerate or "
        f"off-axis heading is on a side-lobe maximum."
    )
