"""Golden-file generator for the rfmesh-dsp tests.

Run with::

    uv run python packages/rfmesh-dsp/tests/golden_generator.py

This is a one-shot tool, not test code: the tests *consume* the golden
files and assert byte-equal (or near-byte-equal) recomputation; only this
generator writes them. Rerun only when adopting a deliberate, lead-reviewed
numerical change (e.g. a contract bump that changes a tolerance).

The script is deterministic: re-running with the constants below pinned
produces byte-identical outputs. Generated under
``packages/rfmesh-dsp/tests/golden/``.

WS-B-001 fixtures (L1 estimator):

* ``l1_sweep_137deg_20db.npz`` -- RSSI(heading) array for the canonical
  ``peak_test_scenario`` at seed=42; consumed by
  ``test_l1_estimator.py::test_golden_sweep_rssi``.
* ``l1_sigma_table.npz`` -- median claimed sigma over 200 trials at each
  of SNR in {5, 10, 20, 30} dB; consumed by
  ``test_l1_estimator.py::test_golden_sigma_table``.

WS-B-002 fixtures (array manifold + covariance):

* ``manifold_ula_8elem_half_lambda.npz`` -- ``(8, 361)`` ULA steering matrix
  at d = lambda/2 for 915 MHz, over azimuths [0, 360] deg at 1 deg step
  (361 inclusive samples). Stored as complex128.
* ``manifold_uca_6elem_quarter_lambda.npz`` -- ``(6, 361)`` UCA steering
  matrix at r = lambda/4 for 915 MHz, same azimuth grid. Stored as complex128.
* ``covariance_known_seed.npz`` -- sample covariance for a fixed
  deterministic ``(4, 1024) complex64`` block generated from a known
  ``default_rng(20260514)`` seed.

WS-B-003 fixtures (L2 MUSIC estimator):

* ``l2_music_pseudospectrum_137deg_uca4_20db.npz`` -- the (720,) MUSIC
  pseudospectrum P(theta) for the canonical UCA-4 scenario at seed=42.
* ``l2_music_sigma_table.npz`` -- for SNR in {10, 20, 30} dB, the median
  claimed sigma over 200 trials with ``receiver.reseed(i)`` per trial.

WS-B-004 fixtures (L2 MVDR/Capon):

* ``l2_mvdr_pseudospectrum_137deg_uca4_20db.npz`` -- 720-sample Capon
  pseudospectrum for the canonical UCA-4 / r=lambda/4 / 137 deg /
  20 dB / 4096-coherent-sample scenario at seed=42; consumed by
  ``test_l2_mvdr.py::test_golden_pseudospectrum``.
* ``l2_mvdr_sigma_table.npz`` -- median claimed sigma over 200 trials
  at SNR in {10, 20, 30} dB on the same scenario; consumed by
  ``test_l2_mvdr.py::test_golden_sigma_table``.

Each ``.npz`` snapshots the inputs *alongside* the outputs so a future
divergence can be triaged without re-deriving the geometry from scratch.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from rfmesh_contracts import (  # type: ignore[import-untyped, unused-ignore]
    ArrayConfig,
    ArrayGeometry,
    GeodeticPosition,
)
from rfmesh_dsp import (
    L1AmplitudeSweepEstimator,
    L2MusicEstimator,
    L2MvdrEstimator,
    compute_rssi_dbfs,
)
from rfmesh_dsp.array_covariance import sample_covariance
from rfmesh_dsp.array_manifold import steering_matrix
from rfmesh_sdr import (  # type: ignore[import-untyped, unused-ignore]
    SimulationScenario,
    SyntheticReceiver,
)

# Make conftest's scenario builders importable without going through
# pytest's collection machinery.
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

import conftest  # type: ignore[import-not-found, unused-ignore]  # noqa: E402

_GOLDEN_DIR = _TESTS_DIR / "golden"

# --- WS-B-001 (L1 estimator) constants --------------------------------------
_GOLDEN_SEED = 42
_SWEEP_BLOCK_SAMPLES = 8192
_SWEEP_STEP_DEG = 1.0
_SIGMA_TABLE_SNRS_DB: tuple[float, ...] = (5.0, 10.0, 20.0, 30.0)
_SIGMA_TABLE_TRIALS = 200
_GOLDEN_SWEEP_SNR_DB = 20.0
_NODE_ID = "golden-node"
_T_UNIX_NS = 1_700_000_000_000_000_000
_NODE_POSITION = GeodeticPosition(lat_deg=52.0, lon_deg=21.0)

# --- WS-B-002 (array manifold + covariance) constants -----------------------
_SPEED_OF_LIGHT_M_PER_S = 299_792_458.0
_REFERENCE_FREQ_HZ = 915e6
_REFERENCE_WAVELENGTH_M = _SPEED_OF_LIGHT_M_PER_S / _REFERENCE_FREQ_HZ

_AZIMUTHS_DEG = np.arange(361, dtype=np.float64)  # [0, 360] inclusive, 1 deg step
_AZIMUTHS_RAD = np.deg2rad(_AZIMUTHS_DEG)

_ULA_N = 8
_UCA_N = 6
_COV_N_CHANNELS = 4
_COV_N_SAMPLES = 1024
_COV_SEED = 20260514

# --- WS-B-003 (L2 MUSIC) constants ------------------------------------------
_L2_UCA_N = 4
_L2_UCA_RADIUS_OVER_LAMBDA = 0.25
_L2_UCA_RADIUS_M = _L2_UCA_RADIUS_OVER_LAMBDA * _REFERENCE_WAVELENGTH_M
_L2_COHERENT_BLOCK_SAMPLES = 4096
_L2_GOLDEN_PSEUDO_SEED = 42
_L2_GOLDEN_PSEUDO_SNR_DB = 20.0
_L2_SIGMA_TABLE_SNRS_DB: tuple[float, ...] = (10.0, 20.0, 30.0)
_L2_SIGMA_TABLE_TRIALS = 200

# --- WS-B-004 (L2 MVDR/Capon) constants -------------------------------------
_MVDR_NODE_ID = "golden-mvdr-node"
_MVDR_NODE_POSITION = GeodeticPosition(lat_deg=52.0, lon_deg=21.0)
_MVDR_T_UNIX_NS = 1_700_000_000_000_000_000
_MVDR_N_COHERENT_SAMPLES = 4096
_MVDR_GOLDEN_SEED = 42
_MVDR_SIGMA_TABLE_SNRS_DB: tuple[float, ...] = (10.0, 20.0, 30.0)
_MVDR_SIGMA_TABLE_TRIALS = 200
_MVDR_GOLDEN_PSPEC_SNR_DB = 20.0


# --- WS-B-001 generators ----------------------------------------------------


def run_sweep_rssi(scenario: SimulationScenario, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Drive a 0..359 deg, 1 deg-step sweep; return (headings_deg, rssi_dbfs)."""
    receiver = SyntheticReceiver(scenario, seed=seed)
    receiver.open()
    headings = np.arange(0.0, 360.0, _SWEEP_STEP_DEG, dtype=np.float64)
    rssi = np.empty(headings.size, dtype=np.float64)
    for i, h in enumerate(headings):
        receiver.set_antenna_heading(float(h))
        iq = receiver.read(_SWEEP_BLOCK_SAMPLES)
        rssi[i] = compute_rssi_dbfs(iq)
    receiver.close()
    return headings, rssi


def claim_sigma_one_trial(scenario: SimulationScenario, seed: int) -> float | None:
    """Run one full sweep through the L1 estimator and return claimed sigma, or None."""
    receiver = SyntheticReceiver(scenario, seed=seed)
    receiver.open()
    estimator = L1AmplitudeSweepEstimator(
        node_id=_NODE_ID,
        node_position=_NODE_POSITION,
        sweep_step_deg=_SWEEP_STEP_DEG,
        sweep_dwell_samples=_SWEEP_BLOCK_SAMPLES,
    )
    estimator.begin_sweep(t_unix_ns=_T_UNIX_NS)
    for h_deg in np.arange(0.0, 360.0, _SWEEP_STEP_DEG):
        receiver.set_antenna_heading(float(h_deg))
        iq = receiver.read(_SWEEP_BLOCK_SAMPLES)
        estimator.observe(float(h_deg), iq)
    report = estimator.estimate(np.zeros(_SWEEP_BLOCK_SAMPLES, dtype=np.complex64))
    receiver.close()
    if report is None:
        return None
    return float(report.azimuth_sigma_deg)


def generate_sweep_golden() -> Path:
    """Generate the per-heading RSSI golden array (137 deg, 20 dB, seed 42)."""
    scenario = conftest._build_peak_scenario(_GOLDEN_SWEEP_SNR_DB)
    headings, rssi = run_sweep_rssi(scenario, seed=_GOLDEN_SEED)
    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    out = _GOLDEN_DIR / "l1_sweep_137deg_20db.npz"
    np.savez(out, headings_deg=headings, rssi_dbfs=rssi)
    return out


def generate_sigma_table_golden() -> Path:
    """Generate the (SNR, median claimed sigma) golden table."""
    snrs = np.array(_SIGMA_TABLE_SNRS_DB, dtype=np.float64)
    median_sigmas = np.empty(snrs.size, dtype=np.float64)
    for i, snr_db in enumerate(snrs):
        scenario = conftest._build_peak_scenario(float(snr_db))
        sigmas: list[float] = []
        for trial in range(_SIGMA_TABLE_TRIALS):
            sigma = claim_sigma_one_trial(scenario, seed=trial)
            if sigma is not None:
                sigmas.append(sigma)
        median_sigmas[i] = float(np.median(sigmas)) if sigmas else float("nan")
    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    out = _GOLDEN_DIR / "l1_sigma_table.npz"
    np.savez(out, snr_db=snrs, median_claimed_sigma_deg=median_sigmas)
    return out


# --- WS-B-002 generators ----------------------------------------------------


def _ula_positions(n_elements: int, spacing_m: float) -> np.ndarray:
    """ULA on the y-axis, channel 0 at the origin (matches ``ArraySpec.ula``)."""
    positions = np.zeros((n_elements, 2), dtype=np.float64)
    positions[:, 1] = np.arange(n_elements, dtype=np.float64) * spacing_m
    return positions


def _uca_positions(n_elements: int, radius_m: float) -> np.ndarray:
    """UCA centred at origin, channel 0 at ``(r, 0)`` (matches ``ArraySpec.uca``)."""
    alphas = 2.0 * np.pi * np.arange(n_elements, dtype=np.float64) / n_elements
    positions = np.empty((n_elements, 2), dtype=np.float64)
    positions[:, 0] = radius_m * np.cos(alphas)
    positions[:, 1] = radius_m * np.sin(alphas)
    return positions


def generate_manifold_ula_golden() -> Path:
    """Generate the half-lambda 8-element ULA steering-matrix golden."""
    spacing_m = 0.5 * _REFERENCE_WAVELENGTH_M
    positions = _ula_positions(_ULA_N, spacing_m)
    manifold = steering_matrix(
        geometry=ArrayGeometry.ULA,
        element_positions_m=positions,
        azimuths_rad=_AZIMUTHS_RAD,
        wavelength_m=_REFERENCE_WAVELENGTH_M,
    )
    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    out = _GOLDEN_DIR / "manifold_ula_8elem_half_lambda.npz"
    np.savez(
        out,
        manifold=manifold,
        positions=positions,
        azimuths_rad=_AZIMUTHS_RAD,
        wavelength_m=np.float64(_REFERENCE_WAVELENGTH_M),
    )
    return out


def generate_manifold_uca_golden() -> Path:
    """Generate the quarter-lambda 6-element UCA steering-matrix golden."""
    radius_m = 0.25 * _REFERENCE_WAVELENGTH_M
    positions = _uca_positions(_UCA_N, radius_m)
    manifold = steering_matrix(
        geometry=ArrayGeometry.UCA,
        element_positions_m=positions,
        azimuths_rad=_AZIMUTHS_RAD,
        wavelength_m=_REFERENCE_WAVELENGTH_M,
    )
    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    out = _GOLDEN_DIR / "manifold_uca_6elem_quarter_lambda.npz"
    np.savez(
        out,
        manifold=manifold,
        positions=positions,
        azimuths_rad=_AZIMUTHS_RAD,
        wavelength_m=np.float64(_REFERENCE_WAVELENGTH_M),
    )
    return out


def generate_covariance_golden() -> Path:
    """Generate the known-seed sample-covariance golden."""
    rng = np.random.default_rng(_COV_SEED)
    real = rng.standard_normal((_COV_N_CHANNELS, _COV_N_SAMPLES))
    imag = rng.standard_normal((_COV_N_CHANNELS, _COV_N_SAMPLES))
    block = (real + 1j * imag).astype(np.complex64)
    cov = sample_covariance(block)
    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    out = _GOLDEN_DIR / "covariance_known_seed.npz"
    np.savez(
        out,
        block=block,
        covariance=cov,
        seed=np.int64(_COV_SEED),
        n_channels=np.int64(_COV_N_CHANNELS),
        n_samples=np.int64(_COV_N_SAMPLES),
    )
    return out


# --- WS-B-003 generators ----------------------------------------------------


def _l2_uca_array_config() -> ArrayConfig:
    """The canonical UCA-4 ``ArrayConfig`` matching the conftest scenario."""
    return ArrayConfig(  # type: ignore[no-any-return, unused-ignore]
        geometry=ArrayGeometry.UCA,
        n_elements=_L2_UCA_N,
        element_spacing_m=_L2_UCA_RADIUS_M,
    )


def _build_l2_estimator(receiver: SyntheticReceiver) -> L2MusicEstimator:
    """Build the canonical L2 estimator bound to ``receiver``."""
    return L2MusicEstimator(
        node_id=_NODE_ID,
        node_position=_NODE_POSITION,
        receiver=receiver,
        array_config=_l2_uca_array_config(),
        operating_frequency_hz=_REFERENCE_FREQ_HZ,
    )


def claim_l2_sigma_one_trial(scenario: SimulationScenario, seed: int) -> float | None:
    """One coherent capture + MUSIC bearing; return claimed sigma or ``None``.

    Mirrors L1's ``claim_sigma_one_trial`` but on the L2 path: open the
    receiver once, calibrate once, reseed (no re-calibrate) per trial,
    one ``read_coherent`` + ``estimate`` per trial.
    """
    receiver = SyntheticReceiver(scenario, seed=seed)
    receiver.open()
    receiver.calibrate()
    estimator = _build_l2_estimator(receiver)
    estimator.set_timestamp(_T_UNIX_NS)
    block = receiver.read_coherent(_L2_COHERENT_BLOCK_SAMPLES)
    report = estimator.estimate(block)
    receiver.close()
    if report is None:
        return None
    return float(report.azimuth_sigma_deg)


def generate_l2_pseudospectrum_golden() -> Path:
    """Generate the canonical 137 deg / UCA-4 / 20 dB MUSIC pseudospectrum."""
    scenario = conftest._build_l2_uca_scenario(_L2_GOLDEN_PSEUDO_SNR_DB)
    receiver = SyntheticReceiver(scenario, seed=_L2_GOLDEN_PSEUDO_SEED)
    receiver.open()
    receiver.calibrate()
    estimator = _build_l2_estimator(receiver)
    block = receiver.read_coherent(_L2_COHERENT_BLOCK_SAMPLES)
    pseudo = estimator.pseudospectrum(block)
    receiver.close()

    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    out = _GOLDEN_DIR / "l2_music_pseudospectrum_137deg_uca4_20db.npz"
    np.savez(
        out,
        pseudospectrum=pseudo,
        snr_db=np.float64(_L2_GOLDEN_PSEUDO_SNR_DB),
        seed=np.int64(_L2_GOLDEN_PSEUDO_SEED),
        n_elements=np.int64(_L2_UCA_N),
        radius_m=np.float64(_L2_UCA_RADIUS_M),
        wavelength_m=np.float64(_REFERENCE_WAVELENGTH_M),
        n_samples=np.int64(_L2_COHERENT_BLOCK_SAMPLES),
    )
    return out


def generate_l2_sigma_table_golden() -> Path:
    """Generate the (SNR, median claimed sigma) golden table for L2 MUSIC."""
    snrs = np.array(_L2_SIGMA_TABLE_SNRS_DB, dtype=np.float64)
    median_sigmas = np.empty(snrs.size, dtype=np.float64)
    for i, snr_db in enumerate(snrs):
        scenario = conftest._build_l2_uca_scenario(float(snr_db))
        sigmas: list[float] = []
        for trial in range(_L2_SIGMA_TABLE_TRIALS):
            sigma = claim_l2_sigma_one_trial(scenario, seed=trial)
            if sigma is not None:
                sigmas.append(sigma)
        median_sigmas[i] = float(np.median(sigmas)) if sigmas else float("nan")
    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    out = _GOLDEN_DIR / "l2_music_sigma_table.npz"
    np.savez(out, snr_db=snrs, median_claimed_sigma_deg=median_sigmas)
    return out


# --- WS-B-004 (L2 MVDR/Capon) generators ------------------------------------


def _mvdr_array_config(scenario: SimulationScenario) -> ArrayConfig:
    """Build the ``ArrayConfig`` matching the simulator UCA fixture."""
    array = scenario.array
    if array is None:
        msg = "MVDR golden generation requires a scenario with an array spec."
        raise RuntimeError(msg)
    # The MVDR scenario fixtures are all UCA; reproduce the radius from
    # the element positions (channel 0 is at (r, 0)).
    radius_m = float(array.element_positions_m[0, 0])
    return ArrayConfig(
        geometry=ArrayGeometry.UCA,
        n_elements=int(array.n_elements),
        element_spacing_m=radius_m,
    )


def _mvdr_estimator_for(
    scenario: SimulationScenario,
    receiver: SyntheticReceiver,
) -> L2MvdrEstimator:
    """Construct a calibrated MVDR estimator paired with ``receiver``."""
    return L2MvdrEstimator(
        node_id=_MVDR_NODE_ID,
        node_position=_MVDR_NODE_POSITION,
        receiver=receiver,
        array_config=_mvdr_array_config(scenario),
        operating_frequency_hz=scenario.center_freq_hz,
    )


def claim_mvdr_sigma_one_trial(
    scenario: SimulationScenario,
    seed: int,
) -> float | None:
    """Run one Capon estimate from a single coherent block; return sigma or None."""
    receiver = SyntheticReceiver(scenario, seed=seed)
    receiver.open()
    receiver.calibrate()
    estimator = _mvdr_estimator_for(scenario, receiver)
    estimator.set_timestamp(_MVDR_T_UNIX_NS)
    block = receiver.read_coherent(_MVDR_N_COHERENT_SAMPLES)
    report = estimator.estimate(block)
    receiver.close()
    if report is None:
        return None
    return float(report.azimuth_sigma_deg)


def generate_mvdr_pseudospectrum_golden() -> Path:
    """Generate the 720-sample Capon pseudospectrum at seed=42, 20 dB.

    Deterministic recompute target for the byte-near-equal golden gate;
    consumed by ``test_l2_mvdr.py::test_golden_pseudospectrum``.
    """
    scenario = conftest._build_mvdr_uca4_scenario(_MVDR_GOLDEN_PSPEC_SNR_DB)
    receiver = SyntheticReceiver(scenario, seed=_MVDR_GOLDEN_SEED)
    receiver.open()
    receiver.calibrate()
    estimator = _mvdr_estimator_for(scenario, receiver)
    block = receiver.read_coherent(_MVDR_N_COHERENT_SAMPLES)
    pseudospectrum = estimator.compute_pseudospectrum(block)
    scan_azimuths_deg = estimator.scan_azimuths_deg.copy()
    receiver.close()

    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    out = _GOLDEN_DIR / "l2_mvdr_pseudospectrum_137deg_uca4_20db.npz"
    np.savez(
        out,
        pseudospectrum=pseudospectrum,
        azimuths_deg=scan_azimuths_deg,
        n_samples=np.int64(_MVDR_N_COHERENT_SAMPLES),
        seed=np.int64(_MVDR_GOLDEN_SEED),
    )
    return out


def generate_mvdr_sigma_table_golden() -> Path:
    """Generate the (SNR, median claimed sigma) golden table for Capon."""
    snrs = np.array(_MVDR_SIGMA_TABLE_SNRS_DB, dtype=np.float64)
    median_sigmas = np.empty(snrs.size, dtype=np.float64)
    for i, snr_db in enumerate(snrs):
        scenario = conftest._build_mvdr_uca4_scenario(float(snr_db))
        sigmas: list[float] = []
        for trial in range(_MVDR_SIGMA_TABLE_TRIALS):
            sigma = claim_mvdr_sigma_one_trial(scenario, seed=trial)
            if sigma is not None:
                sigmas.append(sigma)
        median_sigmas[i] = float(np.median(sigmas)) if sigmas else float("nan")
    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    out = _GOLDEN_DIR / "l2_mvdr_sigma_table.npz"
    np.savez(out, snr_db=snrs, median_claimed_sigma_deg=median_sigmas)
    return out


def main() -> None:
    paths = [
        generate_sweep_golden(),
        generate_sigma_table_golden(),
        generate_manifold_ula_golden(),
        generate_manifold_uca_golden(),
        generate_covariance_golden(),
        generate_l2_pseudospectrum_golden(),
        generate_l2_sigma_table_golden(),
        generate_mvdr_pseudospectrum_golden(),
        generate_mvdr_sigma_table_golden(),
    ]
    for p in paths:
        print(f"wrote {p}")


if __name__ == "__main__":
    main()
