"""Tests for run_dashboard's Beat E preload integration.

Covers:
- _preload_beat_e_into_dashboard populates NullSteeringPanel when the
  layout contains one (DEBUG layout case).
- Missing cache file raises FileNotFoundError.
- No-op + warning when the layout has no NullSteeringPanel
  (MINIMAL layout case).
- CLI parser accepts --beat-e-cache.

Uses an in-process fake Dashboard rather than spinning up the full
asyncio dashboard runtime — keeps the test fast.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from rfmesh_ops.cli.run_dashboard import (
    _build_parser,
    _preload_beat_e_into_dashboard,
)
from rfmesh_ops.panels.null_steering import NullSteeringPanel

_AZIMUTH_BINS = 720
_BIN_STEP_DEG = 360.0 / _AZIMUTH_BINS


def _write_fake_cache(path: Path, *, jammer_deg: float = 126.0) -> dict[str, float]:
    """Write a minimal Beat E cache .npz that the dashboard can load.

    Returns the expected per-bin numbers for the assertions.
    """
    azimuths = np.arange(0.0, 360.0, _BIN_STEP_DEG, dtype=np.float64)
    # Baseline = flat 0 dB; engaged = flat 0 dB except a -20 dB notch at jammer.
    baseline_gain = np.zeros(_AZIMUTH_BINS, dtype=np.float64)
    engaged_gain = np.zeros(_AZIMUTH_BINS, dtype=np.float64)
    jammer_idx = int(np.argmin(np.abs(azimuths - jammer_deg)))
    engaged_gain[jammer_idx] = -20.0
    metadata_json = json.dumps({"signal_azimuth_deg": 306.0, "jammer_azimuth_deg": jammer_deg})
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        path,
        azimuths_deg=azimuths,
        baseline_gain_db=baseline_gain,
        engaged_gain_db=engaged_gain,
        baseline_depth_db=np.asarray(0.0, dtype=np.float64),
        engaged_depth_db=np.asarray(20.0, dtype=np.float64),
        baseline_jammer_gain_db=np.asarray(0.0, dtype=np.float64),
        engaged_jammer_gain_db=np.asarray(-20.0, dtype=np.float64),
        metadata=np.asarray(metadata_json, dtype=np.str_),
    )
    return {
        "baseline_jammer": 0.0,
        "engaged_jammer": -20.0,
        "engaged_depth": 20.0,
    }


def _make_fake_dashboard_with_null_panel() -> tuple[MagicMock, NullSteeringPanel]:
    """Fake Dashboard exposing a single NullSteeringPanel via .panels."""
    import matplotlib.pyplot as plt

    _fig, ax = plt.subplots()
    panel = NullSteeringPanel(ax)
    dashboard = MagicMock()
    dashboard.panels = (panel,)
    return dashboard, panel


def _make_fake_dashboard_without_null_panel() -> MagicMock:
    """Fake Dashboard with no NullSteeringPanel (MINIMAL layout analog)."""
    dashboard = MagicMock()
    dashboard.panels = ()
    return dashboard


def test_preload_populates_null_steering_panel(tmp_path: Path) -> None:
    """_preload_beat_e_into_dashboard fills NullSteeringPanel from a cache .npz."""
    cache_path = tmp_path / "beat_e.npz"
    _write_fake_cache(cache_path)
    dashboard, panel = _make_fake_dashboard_with_null_panel()
    _preload_beat_e_into_dashboard(dashboard, cache_path)
    # Panel state replaced from cache.
    assert panel._azimuths_deg is not None
    assert panel._gain_db is not None
    assert panel._azimuths_deg.shape == (_AZIMUTH_BINS,)
    assert panel._claimed_depth_db == pytest.approx(20.0)


def test_preload_missing_cache_raises(tmp_path: Path) -> None:
    """Missing cache → FileNotFoundError, message points to the missing path."""
    dashboard, _ = _make_fake_dashboard_with_null_panel()
    with pytest.raises(FileNotFoundError, match="Beat E cache file not found"):
        _preload_beat_e_into_dashboard(dashboard, tmp_path / "missing.npz")


def test_preload_warns_when_layout_has_no_null_panel(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MINIMAL-style layout (no NullSteeringPanel) → warning, no crash."""
    cache_path = tmp_path / "beat_e.npz"
    _write_fake_cache(cache_path)
    dashboard = _make_fake_dashboard_without_null_panel()
    with caplog.at_level(logging.WARNING, logger="rfmesh_ops"):
        _preload_beat_e_into_dashboard(dashboard, cache_path)
    assert any("does not include NullSteeringPanel" in record.message for record in caplog.records)


def test_parser_accepts_beat_e_cache_arg() -> None:
    """`--beat-e-cache PATH` parses to a Path."""
    parser = _build_parser()
    args = parser.parse_args(["--connect", "ws://x:9001", "--beat-e-cache", "/tmp/x.npz"])
    assert args.beat_e_cache == Path("/tmp/x.npz")
    assert args.layout == "trench"  # default


def test_parser_beat_e_cache_default_is_none() -> None:
    """`--beat-e-cache` is optional; default is None."""
    parser = _build_parser()
    args = parser.parse_args(["--connect", "ws://x:9001"])
    assert args.beat_e_cache is None
