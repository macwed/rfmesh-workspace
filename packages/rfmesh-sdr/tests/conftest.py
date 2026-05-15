"""Shared pytest fixtures for the rfmesh-sdr simulator tests.

WS-A-001 scenarios:

* ``default_scenario`` -- a single emitter at boresight (azimuth 0) at 1 km,
  915 MHz, configured for ~20 dB SNR. Used by the conformance, lifecycle,
  determinism, and read-exact tests where the absolute geometry does not
  matter.
* ``peak_test_scenario`` -- the WS-A-001 acceptance-criterion-1h geometry:
  CW emitter at azimuth 137 deg, range 1500 m, 915 MHz, ~20 dB SNR at
  boresight. Used by ``test_peakfind``.

WS-A-002 coherent-mode scenarios:

* ``ula_scenario`` -- 4-element ULA at d = lambda/2 (915 MHz), CW emitter
  at array-local 35 deg, 30 dB SNR.
* ``uca_scenario`` -- 8-element UCA at r = 0.5 * lambda (915 MHz), CW
  emitter at array-local 137 deg, 30 dB SNR.
* ``custom_scenario`` -- 4-element CUSTOM non-symmetric layout, CW
  emitter at array-local 50 deg, 30 dB SNR.
* ``impaired_ula_scenario`` -- the ``ula_scenario`` plus per-channel
  complex offsets for calibration tests.
* ``low_snr_calibration_ula_scenario`` -- ``ula_scenario`` configured so
  the calibration reference SNR sits 3 dB *below* the noise floor (for
  the ``CalibrationFailedError`` test).

The L2 fixtures use an isotropic element pattern
(``hpbw_deg=180.0, back_lobe_floor_db=0.0``) so the per-element gain is
unity everywhere -- the array's directional information comes from the
steering vector, not from the element pattern.

Plus a ``simulator_node_config`` fixture that builds an in-memory
``NodeConfig`` for the lifecycle test's ``configure()`` call.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from pydantic import AnyUrl
from rfmesh_contracts import (  # type: ignore[import-untyped, unused-ignore]
    BearerConfig,
    BearerKind,
    Capability,
    GeodeticPosition,
    NodeConfig,
    SDRConfig,
)
from rfmesh_sdr import (
    AntennaPattern,
    ArraySpec,
    ChannelImpairments,
    EmitterSpec,
    SimulationScenario,
)

# Physical / scenario constants used by the fixtures. Centralised so the
# expectations in the tests (e.g. "approximately 20 dB SNR at boresight")
# stay in sync with the values that produced them.
_SPEED_OF_LIGHT_M_PER_S = 299_792_458.0
_CENTER_FREQ_HZ = 915e6
_SAMPLE_RATE_HZ = 2_048_000.0
_NOISE_FLOOR_DBFS = -100.0
_HPBW_DEG = 50.0
_PEAK_AZIMUTH_DEG = 137.0
_PEAK_RANGE_M = 1500.0
_DEFAULT_RANGE_M = 1000.0
_TARGET_SNR_DB_AT_BORESIGHT = 20.0


def _tx_power_db_for_snr(
    target_snr_db: float,
    distance_m: float,
    frequency_hz: float,
    noise_floor_dbfs: float,
) -> float:
    """Solve for tx_power_db that yields ``target_snr_db`` at boresight, given Friis.

    The math (all dB):
        P_rx = tx_power_db + path_loss_db + antenna_gain_db
        SNR  = P_rx - noise_floor_dbfs
    At boresight ``antenna_gain_db = 0``, so:
        tx_power_db = target_snr_db + noise_floor_dbfs - path_loss_db
    """
    wavelength_m = _SPEED_OF_LIGHT_M_PER_S / frequency_hz
    pl_amplitude = wavelength_m / (4.0 * math.pi * distance_m)
    pl_power_db = 20.0 * math.log10(pl_amplitude)
    return target_snr_db + noise_floor_dbfs - pl_power_db


@pytest.fixture
def default_scenario() -> SimulationScenario:
    """Single boresight emitter at 1 km, 915 MHz, ~20 dB SNR.

    Convenient ground truth for tests that need *some* signal in the IQ
    block. The emitter sits at azimuth 0, so at heading 0 the antenna is
    pointed straight at it.
    """
    tx_power_db = _tx_power_db_for_snr(
        target_snr_db=_TARGET_SNR_DB_AT_BORESIGHT,
        distance_m=_DEFAULT_RANGE_M,
        frequency_hz=_CENTER_FREQ_HZ,
        noise_floor_dbfs=_NOISE_FLOOR_DBFS,
    )
    return SimulationScenario(
        emitters=(
            EmitterSpec(
                azimuth_deg=0.0,
                range_m=_DEFAULT_RANGE_M,
                frequency_hz=_CENTER_FREQ_HZ,
                tx_power_db=tx_power_db,
                phase_deg=30.0,
            ),
        ),
        antenna=AntennaPattern(hpbw_deg=_HPBW_DEG),
        sample_rate_hz=_SAMPLE_RATE_HZ,
        center_freq_hz=_CENTER_FREQ_HZ,
        noise_floor_dbfs=_NOISE_FLOOR_DBFS,
    )


@pytest.fixture
def peak_test_scenario() -> SimulationScenario:
    """Acceptance-criterion-1h geometry: emitter at 137 deg, 1500 m, ~20 dB SNR."""
    tx_power_db = _tx_power_db_for_snr(
        target_snr_db=_TARGET_SNR_DB_AT_BORESIGHT,
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
def simulator_node_config() -> NodeConfig:
    """A minimal L1-RSSI, sim-driver NodeConfig for the lifecycle test."""
    return NodeConfig(
        node_id="sim-test-node",
        position=GeodeticPosition(lat_deg=52.0, lon_deg=21.0),
        heading_deg=0.0,
        sdr=SDRConfig(
            driver="sim",
            sample_rate_hz=_SAMPLE_RATE_HZ,
            center_freq_hz=_CENTER_FREQ_HZ,
            gain_db=30.0,
        ),
        capabilities=(Capability.L1_RSSI,),
        bearer=BearerConfig(kind=BearerKind.WIFI),
        fusion_endpoint=AnyUrl("udp://127.0.0.1:9000"),
    )


# ---------------------------------------------------------------------------
# WS-A-002 coherent-mode fixtures
# ---------------------------------------------------------------------------

# At 915 MHz, lambda = c/f ~= 0.3276 m, so lambda/2 ~= 0.1638 m.
_COHERENT_WAVELENGTH_M = _SPEED_OF_LIGHT_M_PER_S / _CENTER_FREQ_HZ
_COHERENT_HALF_WAVELENGTH_M = _COHERENT_WAVELENGTH_M / 2.0
_COHERENT_SNR_DB = 30.0
_COHERENT_RANGE_M = 1000.0
_ULA_AZIMUTH_DEG = 35.0
_UCA_AZIMUTH_DEG = 137.0
_CUSTOM_AZIMUTH_DEG = 50.0
_ISOTROPIC_HPBW_DEG = 180.0
_ISOTROPIC_FLOOR_DB = 0.0


def _isotropic_pattern() -> AntennaPattern:
    """Construct an effectively isotropic element pattern.

    With ``back_lobe_floor_db = 0`` the floor equals the boresight gain
    (1.0), so the ``max(taper, floor)`` clamp returns 1.0 for every angle
    -- a uniform per-element pattern. Used in the coherent-mode tests so
    the array's directional response comes entirely from the steering
    vector, not from the (L1-flavoured) element pattern.
    """
    return AntennaPattern(
        hpbw_deg=_ISOTROPIC_HPBW_DEG,
        back_lobe_floor_db=_ISOTROPIC_FLOOR_DB,
    )


def _coherent_tx_power_db() -> float:
    """tx_power_db that yields 30 dB SNR at boresight at the coherent fixture's range."""
    return _tx_power_db_for_snr(
        target_snr_db=_COHERENT_SNR_DB,
        distance_m=_COHERENT_RANGE_M,
        frequency_hz=_CENTER_FREQ_HZ,
        noise_floor_dbfs=_NOISE_FLOOR_DBFS,
    )


@pytest.fixture
def ula_scenario() -> SimulationScenario:
    """4-element ULA at d = lambda/2 for 915 MHz; CW emitter at array-local 35 deg.

    Heading defaults to 0, so an emitter at azimuth 35 deg produces
    delta_local = 35 deg with no extra setup -- the standard L1 -> L2
    geometry-to-array convention via Decision 1.
    """
    array = ArraySpec.ula(n_elements=4, spacing_m=_COHERENT_HALF_WAVELENGTH_M)
    return SimulationScenario(
        emitters=(
            EmitterSpec(
                azimuth_deg=_ULA_AZIMUTH_DEG,
                range_m=_COHERENT_RANGE_M,
                frequency_hz=_CENTER_FREQ_HZ,
                tx_power_db=_coherent_tx_power_db(),
            ),
        ),
        antenna=_isotropic_pattern(),
        sample_rate_hz=_SAMPLE_RATE_HZ,
        center_freq_hz=_CENTER_FREQ_HZ,
        noise_floor_dbfs=_NOISE_FLOOR_DBFS,
        array=array,
    )


@pytest.fixture
def uca_scenario() -> SimulationScenario:
    """8-element UCA at r = 0.5 * lambda for 915 MHz; CW emitter at array-local 137 deg."""
    array = ArraySpec.uca(n_elements=8, radius_m=_COHERENT_HALF_WAVELENGTH_M)
    return SimulationScenario(
        emitters=(
            EmitterSpec(
                azimuth_deg=_UCA_AZIMUTH_DEG,
                range_m=_COHERENT_RANGE_M,
                frequency_hz=_CENTER_FREQ_HZ,
                tx_power_db=_coherent_tx_power_db(),
            ),
        ),
        antenna=_isotropic_pattern(),
        sample_rate_hz=_SAMPLE_RATE_HZ,
        center_freq_hz=_CENTER_FREQ_HZ,
        noise_floor_dbfs=_NOISE_FLOOR_DBFS,
        array=array,
    )


@pytest.fixture
def custom_scenario() -> SimulationScenario:
    """4-element non-symmetric CUSTOM layout; CW emitter at array-local 50 deg.

    The positions reproduce the example in WS-A-002 Acceptance 1(g):
    ``(0, 0), (0.1, 0), (0, 0.15), (0.13, 0.07)`` metres. Channel 0 sits
    at the origin by the CUSTOM convention; the layout is intentionally
    not a ULA and not a UCA.
    """
    positions = np.array(
        [
            [0.0, 0.0],
            [0.1, 0.0],
            [0.0, 0.15],
            [0.13, 0.07],
        ],
        dtype=np.float64,
    )
    array = ArraySpec.custom(positions)
    return SimulationScenario(
        emitters=(
            EmitterSpec(
                azimuth_deg=_CUSTOM_AZIMUTH_DEG,
                range_m=_COHERENT_RANGE_M,
                frequency_hz=_CENTER_FREQ_HZ,
                tx_power_db=_coherent_tx_power_db(),
            ),
        ),
        antenna=_isotropic_pattern(),
        sample_rate_hz=_SAMPLE_RATE_HZ,
        center_freq_hz=_CENTER_FREQ_HZ,
        noise_floor_dbfs=_NOISE_FLOOR_DBFS,
        array=array,
    )


@pytest.fixture
def impaired_ula_scenario() -> SimulationScenario:
    """``ula_scenario`` with explicit per-channel complex offsets injected.

    Offsets reproduce the WS-A-002 Acceptance 1(j) example values: channel 0
    fixed at 1 + 0j (reference), then 0.95, 1.1, 1.02 amplitudes with
    +0.4, -0.25, +0.18 rad phase offsets. Emitter at array-local 0 deg
    (boresight) so the steering vector is identity and the per-channel
    impairments dominate the cross-channel measurements.
    """
    array = ArraySpec.ula(n_elements=4, spacing_m=_COHERENT_HALF_WAVELENGTH_M)
    offsets = np.array(
        [
            1.0 + 0.0j,
            0.95 * np.exp(1j * 0.4),
            1.1 * np.exp(1j * -0.25),
            1.02 * np.exp(1j * 0.18),
        ],
        dtype=np.complex128,
    )
    impairments = ChannelImpairments(channel_offsets=offsets)
    return SimulationScenario(
        emitters=(
            EmitterSpec(
                azimuth_deg=0.0,
                range_m=_COHERENT_RANGE_M,
                frequency_hz=_CENTER_FREQ_HZ,
                tx_power_db=_coherent_tx_power_db(),
            ),
        ),
        antenna=_isotropic_pattern(),
        sample_rate_hz=_SAMPLE_RATE_HZ,
        center_freq_hz=_CENTER_FREQ_HZ,
        noise_floor_dbfs=_NOISE_FLOOR_DBFS,
        array=array,
        impairments=impairments,
    )


@pytest.fixture
def low_snr_calibration_ula_scenario() -> SimulationScenario:
    """``ula_scenario`` with calibration-reference SNR 3 dB below the noise floor.

    Used to verify that ``calibrate()`` raises ``CalibrationFailedError``
    rather than silently producing a useless calibration.
    """
    array = ArraySpec.ula(n_elements=4, spacing_m=_COHERENT_HALF_WAVELENGTH_M)
    return SimulationScenario(
        emitters=(
            EmitterSpec(
                azimuth_deg=0.0,
                range_m=_COHERENT_RANGE_M,
                frequency_hz=_CENTER_FREQ_HZ,
                tx_power_db=_coherent_tx_power_db(),
            ),
        ),
        antenna=_isotropic_pattern(),
        sample_rate_hz=_SAMPLE_RATE_HZ,
        center_freq_hz=_CENTER_FREQ_HZ,
        noise_floor_dbfs=_NOISE_FLOOR_DBFS,
        array=array,
        calibration_reference_snr_db=-3.0,
    )
