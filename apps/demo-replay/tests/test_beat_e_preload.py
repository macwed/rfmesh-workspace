"""Tests for the Beat E preload helper.

Covers:
- precompute_baseline_pattern returns sane uniform-weights envelope
- precompute_engaged_null_pattern shows a notch near the jammer azimuth
- save/load round-trip preserves arrays + depth + metadata
- CLI entry-point parses args + writes the cache
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from rfmesh_contracts import ArrayGeometry
from rfmesh_demo_replay.beat_e_preload import (
    BeatECache,
    BeatEPattern,
    load_pattern_cache,
    precompute_and_save,
    precompute_baseline_pattern,
    precompute_engaged_null_pattern,
)

_N_ELEMENTS = 2
_SPACING_M = 0.164  # lambda/2 at 915 MHz
_FREQ_HZ = 915e6
_SIGNAL_DEG = 306.0
_JAMMER_DEG = 126.0  # 180 deg from signal — clean null test geometry
_SCAN_STEP_DEG = 0.5
_EXPECTED_BINS = int(360 / _SCAN_STEP_DEG)
_NULL_SEARCH_WINDOW_DEG = 15.0  # match l2_null_steering default
_DEPTH_MIN_DB_FOR_ENGAGED = 10.0  # null pattern must show at least 10 dB depth


def test_baseline_pattern_uniform_weights() -> None:
    """Beat E.0 baseline returns a pattern with shape matching the scan grid."""
    pattern = precompute_baseline_pattern(
        array_geometry=ArrayGeometry.ULA,
        n_elements=_N_ELEMENTS,
        element_spacing_m=_SPACING_M,
        frequency_hz=_FREQ_HZ,
        scan_step_deg=_SCAN_STEP_DEG,
    )
    assert isinstance(pattern, BeatEPattern)
    assert pattern.azimuths_deg.shape == (_EXPECTED_BINS,)
    assert pattern.gain_db.shape == (_EXPECTED_BINS,)
    assert pattern.azimuths_deg.dtype == np.float64
    assert pattern.gain_db.dtype == np.float64
    assert np.isfinite(pattern.gain_db).all()
    # Uniform weights on a 2-element ULA produce a cardioid-like
    # pattern with limited but non-zero directional depth (the
    # array's geometric pattern, not a synthesised null).
    assert pattern.depth_db >= 0.0


def test_engaged_null_pattern_shows_notch_at_jammer() -> None:
    """Beat E.1 engaged-null pattern has a notch within the search window of the jammer azimuth."""
    pattern = precompute_engaged_null_pattern(
        array_geometry=ArrayGeometry.ULA,
        n_elements=_N_ELEMENTS,
        element_spacing_m=_SPACING_M,
        signal_azimuth_deg=_SIGNAL_DEG,
        jammer_azimuth_deg=_JAMMER_DEG,
        frequency_hz=_FREQ_HZ,
        scan_step_deg=_SCAN_STEP_DEG,
    )
    assert pattern.gain_db.shape == (_EXPECTED_BINS,)
    # The null should sit within ±15° of the jammer azimuth (the
    # default search window in compute_null_steering_weights).
    min_idx = int(np.argmin(pattern.gain_db))
    null_azimuth_deg = float(pattern.azimuths_deg[min_idx])
    # Account for ULA front/back ambiguity: a 180-deg-off-signal
    # jammer may show up at jammer_deg OR (180 - jammer_deg). Accept
    # either for the null-location assertion.
    candidates = (_JAMMER_DEG, (180.0 - _JAMMER_DEG) % 360.0)
    deltas = [min(abs(null_azimuth_deg - c), 360.0 - abs(null_azimuth_deg - c)) for c in candidates]
    assert min(deltas) <= _NULL_SEARCH_WINDOW_DEG, (
        f"Null at {null_azimuth_deg:.1f} deg, expected within "
        f"±{_NULL_SEARCH_WINDOW_DEG} deg of {candidates}."
    )
    # Engaged-null pattern must show real directional depth, not just
    # the baseline-geometry pattern.
    assert pattern.depth_db >= _DEPTH_MIN_DB_FOR_ENGAGED, (
        f"Engaged-null pattern depth {pattern.depth_db:.2f} dB is below the "
        f"minimum {_DEPTH_MIN_DB_FOR_ENGAGED} dB sanity threshold — the null "
        "is not actually steering."
    )


def test_save_load_round_trip(tmp_path: Path) -> None:
    """save_pattern_cache + load_pattern_cache preserves arrays + metadata."""
    out_path = tmp_path / "beat_e.npz"
    cache = precompute_and_save(
        out_path=out_path,
        array_geometry=ArrayGeometry.ULA,
        n_elements=_N_ELEMENTS,
        element_spacing_m=_SPACING_M,
        signal_azimuth_deg=_SIGNAL_DEG,
        jammer_azimuth_deg=_JAMMER_DEG,
        frequency_hz=_FREQ_HZ,
        scan_step_deg=_SCAN_STEP_DEG,
    )
    assert out_path.exists()
    reloaded = load_pattern_cache(out_path)
    assert isinstance(reloaded, BeatECache)
    np.testing.assert_array_equal(reloaded.baseline.azimuths_deg, cache.baseline.azimuths_deg)
    np.testing.assert_array_equal(reloaded.baseline.gain_db, cache.baseline.gain_db)
    np.testing.assert_array_equal(reloaded.engaged.gain_db, cache.engaged.gain_db)
    assert reloaded.baseline.depth_db == pytest.approx(cache.baseline.depth_db)
    assert reloaded.engaged.depth_db == pytest.approx(cache.engaged.depth_db)
    # Metadata block survives JSON round-trip.
    assert reloaded.metadata["array_geometry"] == "ula"
    assert reloaded.metadata["n_elements"] == _N_ELEMENTS
    assert reloaded.metadata["signal_azimuth_deg"] == _SIGNAL_DEG
    assert reloaded.metadata["jammer_azimuth_deg"] == _JAMMER_DEG
    assert "generated_at" in reloaded.metadata
    # Beat E.2 bar-chart numbers survive round-trip.
    assert reloaded.baseline_jammer_gain_db == pytest.approx(cache.baseline_jammer_gain_db)
    assert reloaded.engaged_jammer_gain_db == pytest.approx(cache.engaged_jammer_gain_db)


def test_jammer_rejection_property_is_positive() -> None:
    """jammer_rejection_db must be positive — engaged pattern attenuates jammer."""
    cache_data = precompute_engaged_null_pattern(
        array_geometry=ArrayGeometry.ULA,
        n_elements=_N_ELEMENTS,
        element_spacing_m=_SPACING_M,
        signal_azimuth_deg=_SIGNAL_DEG,
        jammer_azimuth_deg=_JAMMER_DEG,
        frequency_hz=_FREQ_HZ,
    )
    # Compute the jammer-bin sample manually + check it is below the
    # max pattern gain (i.e. the jammer direction IS attenuated, not
    # boosted).
    bin_idx = int(np.argmin(np.abs(cache_data.azimuths_deg - _JAMMER_DEG)))
    jammer_gain_db = float(cache_data.gain_db[bin_idx])
    max_gain_db = float(cache_data.gain_db.max())
    assert max_gain_db - jammer_gain_db > 5.0, (
        f"Engaged pattern fails to attenuate jammer: max gain "
        f"{max_gain_db:.2f} dB vs jammer-bin gain {jammer_gain_db:.2f} dB."
    )


def test_save_load_round_trip_creates_parent_dirs(tmp_path: Path) -> None:
    """save_pattern_cache creates parent directories if missing."""
    out_path = tmp_path / "deep" / "nested" / "cache.npz"
    assert not out_path.parent.exists()
    cache = precompute_and_save(
        out_path=out_path,
        array_geometry=ArrayGeometry.ULA,
        n_elements=_N_ELEMENTS,
        element_spacing_m=_SPACING_M,
        signal_azimuth_deg=_SIGNAL_DEG,
        jammer_azimuth_deg=_JAMMER_DEG,
        frequency_hz=_FREQ_HZ,
    )
    assert out_path.exists()
    assert out_path.parent.is_dir()
    reloaded = load_pattern_cache(out_path)
    assert reloaded.baseline.azimuths_deg.shape == cache.baseline.azimuths_deg.shape


def test_load_missing_cache_raises(tmp_path: Path) -> None:
    """load_pattern_cache raises FileNotFoundError on missing path."""
    with pytest.raises(FileNotFoundError):
        load_pattern_cache(tmp_path / "does_not_exist.npz")


def test_engaged_null_requires_two_or_more_elements() -> None:
    """A 1-element array cannot synthesise a null — caller error surface."""
    with pytest.raises(ValueError, match="n_elements >= 2"):
        precompute_engaged_null_pattern(
            array_geometry=ArrayGeometry.ULA,
            n_elements=1,
            element_spacing_m=_SPACING_M,
            signal_azimuth_deg=_SIGNAL_DEG,
            jammer_azimuth_deg=_JAMMER_DEG,
            frequency_hz=_FREQ_HZ,
        )


def test_cli_writes_cache(tmp_path: Path) -> None:
    """`rfmesh-beat-e-preload` CLI end-to-end smoke test."""
    out_path = tmp_path / "cli_beat_e.npz"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rfmesh_demo_replay.cli.preload_beat_e",
            "--out",
            str(out_path),
            "--array",
            "ULA",
            "--n",
            str(_N_ELEMENTS),
            "--spacing",
            str(_SPACING_M),
            "--freq",
            str(_FREQ_HZ),
            "--signal-deg",
            str(_SIGNAL_DEG),
            "--jammer-deg",
            str(_JAMMER_DEG),
            "--log-level",
            "WARNING",  # quiet on CI
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, f"CLI failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    assert out_path.exists()
    cache = load_pattern_cache(out_path)
    assert cache.baseline.azimuths_deg.shape == (_EXPECTED_BINS,)
    assert cache.metadata["signal_azimuth_deg"] == _SIGNAL_DEG
    # JSON metadata blob must round-trip.
    json.dumps(cache.metadata, default=str)


def test_cli_custom_geometry_refused(tmp_path: Path) -> None:
    """CLI refuses CUSTOM geometry — direct API only."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rfmesh_demo_replay.cli.preload_beat_e",
            "--out",
            str(tmp_path / "custom.npz"),
            "--array",
            "CUSTOM",
            "--n",
            str(_N_ELEMENTS),
            "--spacing",
            str(_SPACING_M),
            "--signal-deg",
            str(_SIGNAL_DEG),
            "--jammer-deg",
            str(_JAMMER_DEG),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert b"CUSTOM" in result.stderr or b"CUSTOM" in result.stdout


def test_panel_set_pattern_from_arrays() -> None:
    """NullSteeringPanel renders from preloaded arrays without DSP recompute."""
    import matplotlib.pyplot as plt
    from rfmesh_ops.panels.null_steering import NullSteeringPanel

    fig, ax = plt.subplots()
    panel = NullSteeringPanel(ax)
    azimuths = np.linspace(0.0, 360.0, 720, endpoint=False, dtype=np.float64)
    gain_db = -10.0 + 5.0 * np.cos(np.radians(azimuths))
    panel.set_pattern_from_arrays(azimuths, gain_db, claimed_depth_db=10.0)
    # Internal state populated for re-render.
    assert panel._azimuths_deg is not None
    assert panel._gain_db is not None
    assert panel._claimed_depth_db == 10.0
    plt.close(fig)


def test_panel_set_pattern_from_arrays_shape_mismatch() -> None:
    """NullSteeringPanel rejects mismatched array shapes."""
    import matplotlib.pyplot as plt
    from rfmesh_ops.panels.null_steering import NullSteeringPanel

    fig, ax = plt.subplots()
    panel = NullSteeringPanel(ax)
    azimuths = np.linspace(0.0, 360.0, 720, endpoint=False, dtype=np.float64)
    gain_db = np.zeros(100, dtype=np.float64)
    with pytest.raises(ValueError, match="shape mismatch"):
        panel.set_pattern_from_arrays(azimuths, gain_db)
    plt.close(fig)
