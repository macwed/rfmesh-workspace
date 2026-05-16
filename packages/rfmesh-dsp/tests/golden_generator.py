"""Golden-file generator for the L1 estimator tests.

Run as::

    uv run python packages/rfmesh-dsp/tests/golden_generator.py

Generates two files under ``packages/rfmesh-dsp/tests/golden/``:

* ``l1_sweep_137deg_20db.npz`` -- RSSI(heading) array for the canonical
  ``peak_test_scenario`` at seed=42; the basic-correctness golden file
  consumed by ``test_l1_estimator.py::test_golden_sweep_rssi``.
* ``l1_sigma_table.npz`` -- median claimed sigma over 200 trials at each
  of SNR in {5, 10, 20, 30} dB; the table golden consumed by
  ``test_l1_estimator.py::test_golden_sigma_table``.

The script is deterministic: re-running with the constants below pinned
produces byte-identical outputs. The generator is committed (Acceptance
Criterion 2) so a future change to ``SyntheticReceiver`` or to the L1
fit path that perturbs the goldens can be regenerated and reviewed in a
single diff.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from rfmesh_contracts import GeodeticPosition  # type: ignore[import-untyped, unused-ignore]
from rfmesh_dsp import L1AmplitudeSweepEstimator, compute_rssi_dbfs
from rfmesh_sdr import (  # type: ignore[import-untyped, unused-ignore]
    SimulationScenario,
    SyntheticReceiver,
)

# Make conftest's scenario builders importable without going through
# pytest's collection machinery.
_TESTS_DIR = Path(__file__).parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

import conftest  # type: ignore[import-not-found, unused-ignore]  # noqa: E402

_GOLDEN_SEED = 42
_SWEEP_BLOCK_SAMPLES = 8192
_SWEEP_STEP_DEG = 1.0
_SIGMA_TABLE_SNRS_DB: tuple[float, ...] = (5.0, 10.0, 20.0, 30.0)
_SIGMA_TABLE_TRIALS = 200
_GOLDEN_SWEEP_SNR_DB = 20.0
_NODE_ID = "golden-node"
_T_UNIX_NS = 1_700_000_000_000_000_000
_NODE_POSITION = GeodeticPosition(lat_deg=52.0, lon_deg=21.0)
_GOLDEN_DIR = _TESTS_DIR / "golden"


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


def main() -> None:
    sweep_path = generate_sweep_golden()
    sigma_path = generate_sigma_table_golden()
    print(f"wrote {sweep_path}")
    print(f"wrote {sigma_path}")


if __name__ == "__main__":
    main()
