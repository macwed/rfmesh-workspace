"""rfmesh-dsp -- L1 RSSI bearing estimation and L2 MUSIC/MVDR subspace DF.

Workstream B. Pure: no network, no file I/O beyond vendor data tables at
import, no subprocess, no SDR access (Invariant 5). Every new function must
have a golden-file test in ``tests/golden/`` (Invariant 3).
"""

from __future__ import annotations

from rfmesh_dsp.array_covariance import forward_backward_smooth, sample_covariance
from rfmesh_dsp.array_manifold import steering_matrix, steering_vector
from rfmesh_dsp.l1 import L1AmplitudeSweepEstimator
from rfmesh_dsp.rssi import (
    ClippingReport,
    compute_noise_floor_dbfs,
    compute_rssi_dbfs,
    compute_rssi_in_band,
    compute_snr_db,
    detect_clipping,
)
from rfmesh_dsp.spectrum import (
    compute_fft,
    compute_psd,
    compute_spectrogram,
    find_spectral_peak,
)

__all__ = [
    "ClippingReport",
    "L1AmplitudeSweepEstimator",
    "compute_fft",
    "compute_noise_floor_dbfs",
    "compute_psd",
    "compute_rssi_dbfs",
    "compute_rssi_in_band",
    "compute_snr_db",
    "compute_spectrogram",
    "detect_clipping",
    "find_spectral_peak",
    "forward_backward_smooth",
    "sample_covariance",
    "steering_matrix",
    "steering_vector",
]
