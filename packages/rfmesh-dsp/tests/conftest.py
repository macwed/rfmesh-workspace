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
