"""Beat E preload — precompute and cache the dual-use receive-pattern A/B.

Demo-integrity council recommendation from the 2026-05-20 slide-deck
review: pre-render the Beat E.0 baseline + Beat E.1 engaged-null
patterns before stage time so the live demo does not pause for the
2-3 s ``compute_receive_pattern`` call when the operator presses
"Engage null".

WORKFLOW
--------
Pre-stage (operator runs once, persists the cache):

    rfmesh-beat-e-preload --out cache/beat_e.npz \\
        --array=ULA --n=2 --spacing=0.164 --freq=915e6 \\
        --signal-deg=306 --jammer-deg=126

At stage time the dashboard's NullSteeringPanel calls
``set_pattern_from_cache(path)`` instead of recomputing — render is
instant from a 720-sample numpy array.

WHAT IT COMPUTES
----------------
- ``baseline`` (Beat E.0): the receive pattern with uniform weights
  ``w = 1/sqrt(N) * ones(N)`` — the "no-null-engaged" reference
  shape.
- ``engaged`` (Beat E.1): the receive pattern after MVDR null
  steering against a synthetic R built with one signal at
  ``signal_azimuth_deg`` and one jammer at ``jammer_azimuth_deg``,
  with a configurable jammer-to-signal power ratio.

Both patterns share the same azimuth grid (0..360 deg at
``scan_step_deg``).

CACHE FORMAT
------------
A single ``.npz`` file:

    azimuths_deg          : float64 (M,)
    baseline_gain_db      : float64 (M,)
    engaged_gain_db       : float64 (M,)
    baseline_depth_db     : float64 (scalar 0-d array; uniform pattern)
    engaged_depth_db      : float64 (scalar 0-d array)
    metadata              : str (JSON: array_geometry, n_elements,
                                       element_spacing_m, frequency_hz,
                                       signal_azimuth_deg,
                                       jammer_azimuth_deg, generated_at)

The cache is portable across machines — purely numpy arrays + a JSON
metadata blob.
"""

from __future__ import annotations

import dataclasses
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import numpy.typing as npt
from rfmesh_contracts import ArrayGeometry
from rfmesh_dsp.array_manifold import steering_vector
from rfmesh_dsp.l2_null_steering import (
    compute_null_steering_weights,
    compute_receive_pattern,
)

_DEFAULT_FREQUENCY_HZ: float = 915e6
_SPEED_OF_LIGHT_M_PER_S: float = 299_792_458.0
_DIM: int = 2
_DEFAULT_SCAN_STEP_DEG: float = 0.5
_DEFAULT_JAMMER_TO_SIGNAL_DB: float = 20.0
_DEFAULT_SIGNAL_SNR_DB: float = 30.0
_DEG_TO_RAD: float = math.pi / 180.0


@dataclasses.dataclass(frozen=True)
class BeatEPattern:
    """One receive-pattern snapshot — baseline or engaged.

    Attributes:
        azimuths_deg: ``(M,) float64`` azimuth scan grid, [0, 360).
        gain_db: ``(M,) float64`` receive gain in dB.
        depth_db: Look:null ratio in dB (max gain - min gain). Capped
            at 20 dB for UI display per ADR-008 §D8; uncapped
            for raw analysis.
    """

    azimuths_deg: npt.NDArray[np.float64]
    gain_db: npt.NDArray[np.float64]
    depth_db: float


@dataclasses.dataclass(frozen=True)
class BeatECache:
    """A complete cached Beat E pre-render: baseline + engaged + metadata.

    Loaded via :func:`load_pattern_cache`. Written via
    :func:`save_pattern_cache`.
    """

    baseline: BeatEPattern
    engaged: BeatEPattern
    metadata: dict[str, object]


def _build_positions(
    array_geometry: ArrayGeometry,
    n_elements: int,
    element_spacing_m: float,
    element_positions_m: npt.NDArray[np.float64] | None,
) -> npt.NDArray[np.float64]:
    """Derive ``(N, 2)`` element positions per the simulator convention.

    Mirrors ``rfmesh_dsp.l2_null_steering._build_positions`` (private
    there). ULA along +y; UCA in xy-plane at radius
    ``element_spacing_m``; CUSTOM passes through.
    """
    if array_geometry is ArrayGeometry.ULA:
        positions = np.zeros((n_elements, _DIM), dtype=np.float64)
        positions[:, 1] = np.arange(n_elements, dtype=np.float64) * element_spacing_m
        return positions
    if array_geometry is ArrayGeometry.UCA:
        radius = element_spacing_m
        alphas = 2.0 * math.pi * np.arange(n_elements, dtype=np.float64) / float(n_elements)
        positions = np.empty((n_elements, _DIM), dtype=np.float64)
        positions[:, 0] = radius * np.cos(alphas)
        positions[:, 1] = radius * np.sin(alphas)
        return positions
    if array_geometry is ArrayGeometry.CUSTOM:
        if element_positions_m is None:
            msg = "CUSTOM geometry requires element_positions_m."
            raise ValueError(msg)
        if element_positions_m.shape != (n_elements, _DIM):
            msg = (
                f"CUSTOM geometry: element_positions_m shape must be "
                f"({n_elements}, {_DIM}); got {element_positions_m.shape}."
            )
            raise ValueError(msg)
        return np.asarray(element_positions_m, dtype=np.float64)
    msg = f"unknown ArrayGeometry: {array_geometry}"
    raise ValueError(msg)


def precompute_baseline_pattern(
    *,
    array_geometry: ArrayGeometry,
    n_elements: int,
    element_spacing_m: float,
    element_positions_m: npt.NDArray[np.float64] | None = None,
    frequency_hz: float = _DEFAULT_FREQUENCY_HZ,
    scan_step_deg: float = _DEFAULT_SCAN_STEP_DEG,
) -> BeatEPattern:
    """Beat E.0 baseline — uniform weights, no null engaged.

    The "no-null reference" the operator narrates at t=0 ("nominal —
    directional response of a 2-element ULA, no notch"). Uniform
    unit-norm weights ``w_i = 1/sqrt(N)`` produce an envelope set
    purely by the array's geometric pattern — no MVDR shaping.
    """
    if n_elements < 1:
        msg = f"n_elements must be >= 1 (got {n_elements})."
        raise ValueError(msg)
    w = (np.ones(n_elements, dtype=np.complex64) / math.sqrt(n_elements)).astype(np.complex64)
    azimuths_deg, gain_db = compute_receive_pattern(
        w,
        array_geometry=array_geometry,
        n_elements=n_elements,
        element_spacing_m=element_spacing_m,
        element_positions_m=element_positions_m,
        frequency_hz=frequency_hz,
        scan_step_deg=scan_step_deg,
    )
    depth_db = float(gain_db.max() - gain_db.min())
    return BeatEPattern(
        azimuths_deg=azimuths_deg.astype(np.float64),
        gain_db=gain_db.astype(np.float64),
        depth_db=depth_db,
    )


def precompute_engaged_null_pattern(
    *,
    array_geometry: ArrayGeometry,
    n_elements: int,
    element_spacing_m: float,
    signal_azimuth_deg: float,
    jammer_azimuth_deg: float,
    element_positions_m: npt.NDArray[np.float64] | None = None,
    frequency_hz: float = _DEFAULT_FREQUENCY_HZ,
    scan_step_deg: float = _DEFAULT_SCAN_STEP_DEG,
    signal_snr_db: float = _DEFAULT_SIGNAL_SNR_DB,
    jammer_to_signal_db: float = _DEFAULT_JAMMER_TO_SIGNAL_DB,
) -> BeatEPattern:
    """Beat E.1 engaged-null — synthetic R with jammer + signal, MVDR weights.

    Build a noise-free synthetic covariance matrix:

        R = sigma_s^2 * a(signal) @ a(signal).H
          + sigma_j^2 * a(jammer) @ a(jammer).H
          + sigma_n^2 * I

    where ``sigma_s^2`` and ``sigma_j^2`` follow the configured SNR /
    JNR and ``sigma_n^2 = 1`` (the noise reference). Then call
    ``compute_null_steering_weights(R, a_signal)`` to derive the
    MVDR distortionless-toward-signal weights and
    ``compute_receive_pattern(w)`` to render the resulting pattern.

    The pattern shows a clear notch at the jammer azimuth — the
    Beat E.1 / E.2 visual the demo narrates.
    """
    if n_elements < _DIM:
        msg = (
            f"engaged-null requires n_elements >= {_DIM} (got {n_elements}); a 1-element "
            "array cannot synthesise a null."
        )
        raise ValueError(msg)
    positions = _build_positions(
        array_geometry,
        n_elements,
        element_spacing_m,
        element_positions_m,
    )
    wavelength_m = _SPEED_OF_LIGHT_M_PER_S / frequency_hz
    a_signal = steering_vector(
        geometry=array_geometry,
        element_positions_m=positions,
        azimuth_rad=signal_azimuth_deg * _DEG_TO_RAD,
        wavelength_m=wavelength_m,
    )
    a_jammer = steering_vector(
        geometry=array_geometry,
        element_positions_m=positions,
        azimuth_rad=jammer_azimuth_deg * _DEG_TO_RAD,
        wavelength_m=wavelength_m,
    )
    # Noise reference is 1; signal and jammer follow SNR/JNR ratios.
    # `a` is unit-norm per steering_vector contract, so the outer-product
    # magnitude is set entirely by the variance scaling.
    sigma_s_sq = 10.0 ** (signal_snr_db / 10.0)
    sigma_j_sq = sigma_s_sq * 10.0 ** (jammer_to_signal_db / 10.0)
    r = (
        sigma_s_sq * np.outer(a_signal, np.conj(a_signal))
        + sigma_j_sq * np.outer(a_jammer, np.conj(a_jammer))
        + np.eye(n_elements, dtype=np.complex128)
    ).astype(np.complex64)
    result = compute_null_steering_weights(
        r_covariance=r,
        look_steering_vector=a_signal.astype(np.complex64),
        array_geometry=array_geometry,
        n_elements=n_elements,
        element_spacing_m=element_spacing_m,
        element_positions_m=element_positions_m,
        frequency_hz=frequency_hz,
    )
    azimuths_deg, gain_db = compute_receive_pattern(
        result.weights,
        array_geometry=array_geometry,
        n_elements=n_elements,
        element_spacing_m=element_spacing_m,
        element_positions_m=element_positions_m,
        frequency_hz=frequency_hz,
        scan_step_deg=scan_step_deg,
    )
    depth_db = float(gain_db.max() - gain_db.min())
    return BeatEPattern(
        azimuths_deg=azimuths_deg.astype(np.float64),
        gain_db=gain_db.astype(np.float64),
        depth_db=depth_db,
    )


def save_pattern_cache(
    path: Path | str,
    cache: BeatECache,
) -> None:
    """Write a ``BeatECache`` to disk as a single ``.npz`` file.

    Creates parent directories if missing. The cache is self-describing
    through the embedded JSON metadata blob.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata_json = json.dumps(cache.metadata, indent=2, sort_keys=True, default=str)
    # Store metadata as a numpy string scalar (no pickle); this lets
    # load_pattern_cache use np.load(allow_pickle=False), which is
    # both safer and ~5x faster on cold-start interpreter than the
    # object-dtype + pickle path.
    np.savez(
        path,
        azimuths_deg=cache.baseline.azimuths_deg,
        baseline_gain_db=cache.baseline.gain_db,
        engaged_gain_db=cache.engaged.gain_db,
        baseline_depth_db=np.asarray(cache.baseline.depth_db, dtype=np.float64),
        engaged_depth_db=np.asarray(cache.engaged.depth_db, dtype=np.float64),
        metadata=np.asarray(metadata_json, dtype=np.str_),
    )


def load_pattern_cache(path: Path | str) -> BeatECache:
    """Read a ``BeatECache`` previously written by :func:`save_pattern_cache`."""
    path = Path(path)
    if not path.exists():
        msg = f"Beat E cache file not found: {path}"
        raise FileNotFoundError(msg)
    # allow_pickle=False — the cache is plain numpy arrays + a JSON
    # string; no Python object needs to round-trip through pickle.
    # Faster cold-start load and safer (no arbitrary code execution
    # via a tampered cache file).
    with np.load(path, allow_pickle=False) as data:
        azimuths_deg = np.asarray(data["azimuths_deg"], dtype=np.float64)
        baseline_gain_db = np.asarray(data["baseline_gain_db"], dtype=np.float64)
        engaged_gain_db = np.asarray(data["engaged_gain_db"], dtype=np.float64)
        baseline_depth_db = float(data["baseline_depth_db"])
        engaged_depth_db = float(data["engaged_depth_db"])
        metadata_json = str(data["metadata"])
    metadata: dict[str, object] = json.loads(metadata_json)
    baseline = BeatEPattern(
        azimuths_deg=azimuths_deg,
        gain_db=baseline_gain_db,
        depth_db=baseline_depth_db,
    )
    engaged = BeatEPattern(
        azimuths_deg=azimuths_deg,
        gain_db=engaged_gain_db,
        depth_db=engaged_depth_db,
    )
    return BeatECache(baseline=baseline, engaged=engaged, metadata=metadata)


def precompute_and_save(
    out_path: Path | str,
    *,
    array_geometry: ArrayGeometry,
    n_elements: int,
    element_spacing_m: float,
    signal_azimuth_deg: float,
    jammer_azimuth_deg: float,
    element_positions_m: npt.NDArray[np.float64] | None = None,
    frequency_hz: float = _DEFAULT_FREQUENCY_HZ,
    scan_step_deg: float = _DEFAULT_SCAN_STEP_DEG,
    signal_snr_db: float = _DEFAULT_SIGNAL_SNR_DB,
    jammer_to_signal_db: float = _DEFAULT_JAMMER_TO_SIGNAL_DB,
) -> BeatECache:
    """End-to-end: compute baseline + engaged + write cache to ``out_path``.

    Returns the freshly computed cache (so callers can use it
    immediately without a re-read).
    """
    baseline = precompute_baseline_pattern(
        array_geometry=array_geometry,
        n_elements=n_elements,
        element_spacing_m=element_spacing_m,
        element_positions_m=element_positions_m,
        frequency_hz=frequency_hz,
        scan_step_deg=scan_step_deg,
    )
    engaged = precompute_engaged_null_pattern(
        array_geometry=array_geometry,
        n_elements=n_elements,
        element_spacing_m=element_spacing_m,
        signal_azimuth_deg=signal_azimuth_deg,
        jammer_azimuth_deg=jammer_azimuth_deg,
        element_positions_m=element_positions_m,
        frequency_hz=frequency_hz,
        scan_step_deg=scan_step_deg,
        signal_snr_db=signal_snr_db,
        jammer_to_signal_db=jammer_to_signal_db,
    )
    metadata: dict[str, object] = {
        "array_geometry": array_geometry.value,
        "n_elements": n_elements,
        "element_spacing_m": element_spacing_m,
        "frequency_hz": frequency_hz,
        "scan_step_deg": scan_step_deg,
        "signal_azimuth_deg": signal_azimuth_deg,
        "jammer_azimuth_deg": jammer_azimuth_deg,
        "signal_snr_db": signal_snr_db,
        "jammer_to_signal_db": jammer_to_signal_db,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    cache = BeatECache(baseline=baseline, engaged=engaged, metadata=metadata)
    save_pattern_cache(out_path, cache)
    return cache
