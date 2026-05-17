"""Shared pytest fixtures for the rfmesh-dsp tests.

Re-creates the WS-A ``peak_test_scenario`` and adds parametrised SNR
variants for the sigma-honesty Monte Carlo. Per the WS-B-001 ticket the
DSP test suite owns its own scenarios -- it does not import from the
WS-A test tree (each workstream owns only what is inside its package
directory).

Fixtures:

* ``peak_test_scenario`` -- WS-A-001 acceptance-criterion-1h geometry: CW
  emitter at azimuth 137 deg, range 1500 m, 915 MHz, ~20 dB SNR at
  boresight. Used by the basic peak-recovery test and by the golden
  generator.
* ``flat_scenario`` -- CW emitter ~30 dB *below* the noise floor at
  boresight, used to verify the L1 estimator returns ``None`` (no
  fabricated bearing) on an unusable sweep.
* ``snr_scenario_factory`` -- factory that builds a ``peak_test_scenario``
  variant at an arbitrary target SNR; used by the sigma-honesty Monte
  Carlo to sweep SNR in {10, 20, 30} dB.
* ``golden_dir`` -- session-scoped path to the committed ``.npz`` golden
  fixtures directory; consumed by array-manifold and array-covariance
  tests (WS-B-002).
"""

from __future__ import annotations

import math
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from rfmesh_sdr import (  # type: ignore[import-untyped, unused-ignore]
    AntennaPattern,
    ArraySpec,
    EmitterSpec,
    SimulationScenario,
)

# Per workspace convention (ADR-006: no __init__.py under tests/), pytest's
# rootdir-based discovery handles test collection; this conftest.py is loaded
# before any test module. Add the tests dir to sys.path so ``test_*.py``
# files can ``import golden_generator`` without per-file boilerplate.
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

# Centralised scenario constants. Kept in sync with the WS-A-001
# conftest (the test plan in WS-B-001 explicitly re-creates the WS-A
# fixture geometry).
_SPEED_OF_LIGHT_M_PER_S = 299_792_458.0
_CENTER_FREQ_HZ = 915e6
_SAMPLE_RATE_HZ = 2_048_000.0
_NOISE_FLOOR_DBFS = -100.0
_HPBW_DEG = 50.0
_PEAK_AZIMUTH_DEG = 137.0
_PEAK_RANGE_M = 1500.0
_TARGET_SNR_DB_AT_BORESIGHT = 20.0


def _tx_power_db_for_snr(
    target_snr_db: float,
    distance_m: float,
    frequency_hz: float,
    noise_floor_dbfs: float,
) -> float:
    """Solve for tx_power_db that yields ``target_snr_db`` at boresight under Friis.

    All dB:
        P_rx_dbfs = tx_power_db + path_loss_db + antenna_gain_db
        SNR_db    = P_rx_dbfs - noise_floor_dbfs
    With antenna_gain_db = 0 at boresight:
        tx_power_db = target_snr_db + noise_floor_dbfs - path_loss_db
    """
    wavelength_m = _SPEED_OF_LIGHT_M_PER_S / frequency_hz
    pl_amplitude = wavelength_m / (4.0 * math.pi * distance_m)
    pl_power_db = 20.0 * math.log10(pl_amplitude)
    return target_snr_db + noise_floor_dbfs - pl_power_db


def _build_peak_scenario(target_snr_db: float) -> SimulationScenario:
    """Build the canonical L1 peak scenario at a configurable target SNR."""
    tx_power_db = _tx_power_db_for_snr(
        target_snr_db=target_snr_db,
        distance_m=_PEAK_RANGE_M,
        frequency_hz=_CENTER_FREQ_HZ,
        noise_floor_dbfs=_NOISE_FLOOR_DBFS,
    )
    return SimulationScenario(
        emitters=(
            EmitterSpec(
                azimuth_deg=_PEAK_AZIMUTH_DEG,
                range_m=_PEAK_RANGE_M,
                frequency_hz=_CENTER_FREQ_HZ,
                tx_power_db=tx_power_db,
            ),
        ),
        antenna=AntennaPattern(hpbw_deg=_HPBW_DEG),
        sample_rate_hz=_SAMPLE_RATE_HZ,
        center_freq_hz=_CENTER_FREQ_HZ,
        noise_floor_dbfs=_NOISE_FLOOR_DBFS,
    )


@pytest.fixture
def peak_test_scenario() -> SimulationScenario:
    """WS-A acceptance-1h geometry: 137 deg, 1500 m, 915 MHz, ~20 dB SNR @ boresight."""
    return _build_peak_scenario(_TARGET_SNR_DB_AT_BORESIGHT)


@pytest.fixture
def flat_scenario() -> SimulationScenario:
    """Same geometry as the peak scenario but the emitter is ~30 dB below the floor.

    The combined effect of the antenna's back-lobe floor (-30 dB) and the
    weak emitter makes the sweep RSSI essentially constant within the
    per-heading noise -- the prominence gate must reject and ``estimate``
    must return ``None``.
    """
    return _build_peak_scenario(target_snr_db=-30.0)


@pytest.fixture
def snr_scenario_factory() -> Callable[[float], SimulationScenario]:
    """Factory: ``snr_scenario_factory(snr_db)`` -> ``SimulationScenario``."""
    return _build_peak_scenario


@pytest.fixture(scope="session")
def golden_dir() -> Path:
    """Absolute path to the directory holding committed golden ``.npz`` fixtures."""
    return _TESTS_DIR / "golden"


# ----------------------------------------------------------------------------
# WS-B-003 (L2 MUSIC) coherent-scenario fixtures.
#
# A 4-element UCA with r = lambda/4 at 915 MHz is the canonical scenario
# the ticket pins down: full 360-deg coverage (no ULA front/back ambiguity)
# at a spacing well below the half-wavelength aliasing limit. The antenna
# pattern is widened to HPBW=180 deg so the off-boresight emitter at 137
# deg sees a defined gain rather than the back-lobe floor; the L2 path is
# concerned with phase coherence, not amplitude shape, and tx_power_db is
# adjusted to compensate the per-element antenna gain so the per-channel
# SNR at the array matches the target.
# ----------------------------------------------------------------------------

_L2_UCA_N_ELEMENTS = 4
_L2_UCA_RADIUS_OVER_LAMBDA = 0.25
_L2_EMITTER_AZIMUTH_DEG = 137.0
_L2_EMITTER_RANGE_M = 1500.0
_L2_ANTENNA_HPBW_DEG = 180.0
_L2_REFERENCE_SNR_DB = 20.0


def _l2_antenna_gain_db(delta_deg: float, hpbw_deg: float) -> float:
    """Antenna gain (dB) at ``delta_deg`` off boresight for the L2 antenna pattern.

    Mirrors ``rfmesh_sdr.simulator.antenna.AntennaPattern.gain_linear``
    closed-form (cos^2 taper with a -30 dB back-lobe floor) so the
    tx_power-for-target-SNR solver matches the simulator exactly.
    """
    abs_delta = abs(delta_deg)
    if abs_delta >= hpbw_deg:
        taper = 0.0
    else:
        arg = math.pi * abs_delta / (2.0 * hpbw_deg)
        taper = math.cos(arg) ** 2
    floor_linear = 10.0 ** (-30.0 / 10.0)
    gain_linear = max(taper, floor_linear)
    return 10.0 * math.log10(gain_linear)


def _build_l2_uca_scenario(target_snr_db: float) -> SimulationScenario:
    """Build a UCA-4 coherent scenario at a configurable per-channel SNR.

    Per-channel SNR (signal vs the ADC noise floor) is held to
    ``target_snr_db`` at the emitter's azimuth by absorbing the antenna
    gain at that angle into ``tx_power_db``.
    """
    wavelength_m = _SPEED_OF_LIGHT_M_PER_S / _CENTER_FREQ_HZ
    radius_m = _L2_UCA_RADIUS_OVER_LAMBDA * wavelength_m
    array = ArraySpec.uca(n_elements=_L2_UCA_N_ELEMENTS, radius_m=radius_m)

    pl_amplitude = wavelength_m / (4.0 * math.pi * _L2_EMITTER_RANGE_M)
    pl_power_db = 20.0 * math.log10(pl_amplitude)
    antenna_gain_db = _l2_antenna_gain_db(_L2_EMITTER_AZIMUTH_DEG, _L2_ANTENNA_HPBW_DEG)
    tx_power_db = target_snr_db + _NOISE_FLOOR_DBFS - pl_power_db - antenna_gain_db

    return SimulationScenario(
        emitters=(
            EmitterSpec(
                azimuth_deg=_L2_EMITTER_AZIMUTH_DEG,
                range_m=_L2_EMITTER_RANGE_M,
                frequency_hz=_CENTER_FREQ_HZ,
                tx_power_db=tx_power_db,
            ),
        ),
        antenna=AntennaPattern(hpbw_deg=_L2_ANTENNA_HPBW_DEG),
        sample_rate_hz=_SAMPLE_RATE_HZ,
        center_freq_hz=_CENTER_FREQ_HZ,
        noise_floor_dbfs=_NOISE_FLOOR_DBFS,
        array=array,
    )


@pytest.fixture
def l2_uca_scenario() -> SimulationScenario:
    """Canonical L2 scenario: UCA-4, r/lambda=0.25, emitter at 137 deg, 20 dB SNR."""
    return _build_l2_uca_scenario(_L2_REFERENCE_SNR_DB)


@pytest.fixture
def l2_uca_scenario_factory() -> Callable[[float], SimulationScenario]:
    """Factory: ``l2_uca_scenario_factory(snr_db)`` -> ``SimulationScenario``."""
    return _build_l2_uca_scenario


@pytest.fixture
def l2_low_snr_scenario() -> SimulationScenario:
    """L2 scenario at -10 dB SNR; the eigenvalue gate should reject the bearing."""
    return _build_l2_uca_scenario(-10.0)


# ---------------------------------------------------------------------------
# WS-B-004 coherent-mode L2 (MVDR/Capon) fixtures
# ---------------------------------------------------------------------------

# At 915 MHz, lambda = c / f ~= 0.3276 m. The MVDR test uses r = lambda/4
# (a smaller-than-half-wavelength radius) to drive the steering vectors
# apart enough that a UCA of only N = 4 elements still produces a clean
# Capon peak. The convention is consistent with the WS-A coherent
# fixtures' wavelength derivation; reproducing it here keeps the DSP
# tests independent of the SDR test tree.
_MVDR_WAVELENGTH_M = _SPEED_OF_LIGHT_M_PER_S / _CENTER_FREQ_HZ
_MVDR_UCA_RADIUS_M = 0.25 * _MVDR_WAVELENGTH_M
# Isotropic per-element pattern: 180 deg HPBW with the back-lobe floor
# clamped at the boresight gain produces a uniform unity pattern, so the
# array's directional response comes entirely from the steering vector.
_MVDR_ISOTROPIC_HPBW_DEG = 180.0
_MVDR_ISOTROPIC_FLOOR_DB = 0.0
_MVDR_COHERENT_RANGE_M = 1500.0
_MVDR_PEAK_AZIMUTH_DEG = 137.0
_MVDR_TARGET_SNR_DB = 20.0


def _build_mvdr_uca4_scenario(target_snr_db: float) -> SimulationScenario:
    """Build the canonical MVDR UCA-4 scenario at a configurable target SNR.

    UCA with 4 elements at r = lambda/4 (915 MHz), single CW emitter at
    array-local 137 deg at 1500 m. Heading defaults to 0, so the
    geographic azimuth and the array-local azimuth coincide -- the Capon
    scan's recovered peak position is directly comparable to the truth.
    """
    tx_power_db = _tx_power_db_for_snr(
        target_snr_db=target_snr_db,
        distance_m=_MVDR_COHERENT_RANGE_M,
        frequency_hz=_CENTER_FREQ_HZ,
        noise_floor_dbfs=_NOISE_FLOOR_DBFS,
    )
    return SimulationScenario(
        emitters=(
            EmitterSpec(
                azimuth_deg=_MVDR_PEAK_AZIMUTH_DEG,
                range_m=_MVDR_COHERENT_RANGE_M,
                frequency_hz=_CENTER_FREQ_HZ,
                tx_power_db=tx_power_db,
            ),
        ),
        antenna=AntennaPattern(
            hpbw_deg=_MVDR_ISOTROPIC_HPBW_DEG,
            back_lobe_floor_db=_MVDR_ISOTROPIC_FLOOR_DB,
        ),
        sample_rate_hz=_SAMPLE_RATE_HZ,
        center_freq_hz=_CENTER_FREQ_HZ,
        noise_floor_dbfs=_NOISE_FLOOR_DBFS,
        array=ArraySpec.uca(n_elements=4, radius_m=_MVDR_UCA_RADIUS_M),
    )


@pytest.fixture
def mvdr_uca4_scenario() -> SimulationScenario:
    """4-element UCA, r = lambda/4, CW emitter at 137 deg, ~20 dB SNR.

    Drives the MVDR peak-recovery acceptance test and the golden-file
    pseudospectrum at seed=42. The UCA's 360-deg unambiguous coverage
    lets the scan span the full circle without front/back disambiguation
    machinery; ``N = 4`` is the smallest array for which an L2 demo is
    credible.
    """
    return _build_mvdr_uca4_scenario(_MVDR_TARGET_SNR_DB)


@pytest.fixture
def mvdr_uca4_snr_scenario_factory() -> Callable[[float], SimulationScenario]:
    """Factory: ``mvdr_uca4_snr_scenario_factory(snr_db)`` -> UCA-4 scenario at that SNR."""
    return _build_mvdr_uca4_scenario


# ---------------------------------------------------------------------------
# WS-B-007 (L2 null-steering) two-emitter scenario fixtures.
#
# Canonical scenario from the WS-B-007 ticket / ADR-008 D6:
#   signal at theta_s = 30 deg, jammer at theta_j = 100 deg,
#   N = 4 UCA at lambda/4, SNR signal/jammer/noise = 10/20/0 dB,
#   T = 4096 snapshots, seed = 42.
# The signal is the "look" direction (whose response MVDR preserves);
# the jammer is the implicit-in-R interferer the null forms against.
# ---------------------------------------------------------------------------

_NULL_STEERING_SIGNAL_AZIMUTH_DEG = 30.0
_NULL_STEERING_JAMMER_AZIMUTH_DEG = 100.0
_NULL_STEERING_EMITTER_RANGE_M = 1500.0
_NULL_STEERING_SIGNAL_SNR_DB = 10.0
_NULL_STEERING_JAMMER_SNR_DB = 20.0


def _build_null_steering_two_emitter_scenario(
    *,
    signal_snr_db: float = _NULL_STEERING_SIGNAL_SNR_DB,
    jammer_snr_db: float = _NULL_STEERING_JAMMER_SNR_DB,
    signal_azimuth_deg: float = _NULL_STEERING_SIGNAL_AZIMUTH_DEG,
    jammer_azimuth_deg: float = _NULL_STEERING_JAMMER_AZIMUTH_DEG,
) -> SimulationScenario:
    """Build the two-emitter UCA-4 scenario for WS-B-007 null-steering tests.

    Reuses the MVDR isotropic-pattern convention (180 deg HPBW, 0 dB
    back-lobe floor) so the array's directional selectivity comes
    entirely from the steering vectors -- the L2 path concern is
    phase coherence, not antenna shape.
    """
    signal_tx_power_db = _tx_power_db_for_snr(
        target_snr_db=signal_snr_db,
        distance_m=_NULL_STEERING_EMITTER_RANGE_M,
        frequency_hz=_CENTER_FREQ_HZ,
        noise_floor_dbfs=_NOISE_FLOOR_DBFS,
    )
    jammer_tx_power_db = _tx_power_db_for_snr(
        target_snr_db=jammer_snr_db,
        distance_m=_NULL_STEERING_EMITTER_RANGE_M,
        frequency_hz=_CENTER_FREQ_HZ,
        noise_floor_dbfs=_NOISE_FLOOR_DBFS,
    )
    # Use slightly different carrier frequencies so the two coherent
    # tones do not synthesise a single beating waveform -- the L2
    # covariance estimator needs two independent contributions for
    # the rank-2 signal-plus-jammer subspace to materialise. 10 kHz
    # offset is well within the analysis bandwidth and large enough
    # to decorrelate over T = 4096 samples at 2.048 MS/s.
    return SimulationScenario(
        emitters=(
            EmitterSpec(
                azimuth_deg=signal_azimuth_deg,
                range_m=_NULL_STEERING_EMITTER_RANGE_M,
                frequency_hz=_CENTER_FREQ_HZ,
                tx_power_db=signal_tx_power_db,
            ),
            EmitterSpec(
                azimuth_deg=jammer_azimuth_deg,
                range_m=_NULL_STEERING_EMITTER_RANGE_M,
                frequency_hz=_CENTER_FREQ_HZ + 10_000.0,
                tx_power_db=jammer_tx_power_db,
            ),
        ),
        antenna=AntennaPattern(
            hpbw_deg=_MVDR_ISOTROPIC_HPBW_DEG,
            back_lobe_floor_db=_MVDR_ISOTROPIC_FLOOR_DB,
        ),
        sample_rate_hz=_SAMPLE_RATE_HZ,
        center_freq_hz=_CENTER_FREQ_HZ,
        noise_floor_dbfs=_NOISE_FLOOR_DBFS,
        array=ArraySpec.uca(n_elements=4, radius_m=_MVDR_UCA_RADIUS_M),
    )


@pytest.fixture
def null_steering_two_emitter_scenario() -> SimulationScenario:
    """Canonical 2-emitter UCA-4 scenario: signal at 30 deg, jammer at 100 deg.

    Per ADR-008 D6 acceptance criteria: SNR signal = 10 dB, jammer =
    20 dB, noise = 0 dB; the array is UCA N=4 at radius = lambda/4
    for 915 MHz; isotropic per-element pattern; T = 4096 snapshots
    per call.
    """
    return _build_null_steering_two_emitter_scenario()


@pytest.fixture
def null_steering_two_emitter_factory() -> Callable[..., SimulationScenario]:
    """Factory: ``null_steering_two_emitter_factory(signal_snr_db=..., ...)``."""
    return _build_null_steering_two_emitter_scenario
