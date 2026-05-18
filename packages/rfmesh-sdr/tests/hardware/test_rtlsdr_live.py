"""Live-hardware smoke tests for ``RTLSDRDevice`` (WS-A-005).

Skipped unless ``RFMESH_HARDWARE=1`` is set AND ``rtl_sdr`` is on PATH.
Same gating pattern as ``packages/rfmesh-servo/tests/test_integration.py``
so CI and ``just verify`` never touch these.

To run::

    RFMESH_HARDWARE=1 uv run pytest -m hardware \\
        packages/rfmesh-sdr/tests/hardware/test_rtlsdr_live.py
"""

from __future__ import annotations

import os
import shutil

import numpy as np
import pytest
from pydantic import AnyUrl
from rfmesh_contracts.config import BearerConfig, NodeConfig, SDRConfig
from rfmesh_contracts.enums import BearerKind, Capability
from rfmesh_contracts.geospatial import GeodeticPosition
from rfmesh_sdr.devices import RTLSDRDevice

_HW_GATE: bool = os.environ.get("RFMESH_HARDWARE") == "1"
_RTL_SDR_AVAILABLE: bool = shutil.which("rtl_sdr") is not None

_LIVE_SAMPLE_COUNT = 100_000
_LIVE_SAMPLE_RATE_HZ = 2_400_000.0
_LIVE_CENTER_FREQ_HZ = 915_000_000.0
_LIVE_GAIN_DB = 30.0


pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(not _HW_GATE, reason="RFMESH_HARDWARE=1 not set"),
    pytest.mark.skipif(not _RTL_SDR_AVAILABLE, reason="rtl_sdr binary not in PATH"),
]


def _bench_node_config(gain_db: float = _LIVE_GAIN_DB) -> NodeConfig:
    """Bench-side NodeConfig: 915 MHz, 2.4 MS/s, explicit gain (no AGC)."""
    return NodeConfig(
        node_id="bench-rtlsdr",
        position=GeodeticPosition(lat_deg=52.0, lon_deg=21.0),
        heading_deg=0.0,
        sdr=SDRConfig(
            driver="rtlsdr",
            sample_rate_hz=_LIVE_SAMPLE_RATE_HZ,
            center_freq_hz=_LIVE_CENTER_FREQ_HZ,
            gain_db=gain_db,
        ),
        capabilities=(Capability.L1_RSSI,),
        bearer=BearerConfig(kind=BearerKind.WIFI),
        fusion_endpoint=AnyUrl("udp://127.0.0.1:9000"),
    )


def test_live_read_returns_complex64_block() -> None:
    """Real V4 dongle reads 100 k samples; dtype + finiteness asserted."""
    device = RTLSDRDevice()
    device.open()
    device.configure(_bench_node_config())
    try:
        samples = device.read(_LIVE_SAMPLE_COUNT)
    finally:
        device.close()
    assert samples.dtype == np.complex64
    assert samples.shape == (_LIVE_SAMPLE_COUNT,)
    assert np.isfinite(samples).all()


def test_live_gain_setting_is_explicit() -> None:
    """Explicit gain reproducible; capabilities reflects configured rate.

    Explicit gain is the DF-honest path per INHERITED_CONTEXT.md
    Section 1.3 -- 'auto' AGC drifts the noise floor between sweeps and
    breaks every sigma estimate. This test does not measure power
    calibration (no SDR in scope is power-calibrated,
    ``is_power_calibrated=False``) -- it asserts the configured gain is
    the one passed.
    """
    device = RTLSDRDevice()
    device.open()
    device.configure(_bench_node_config(gain_db=30.0))
    try:
        _samples = device.read(10_000)
        caps = device.capabilities()
        assert caps.actual_sample_rate_hz == _LIVE_SAMPLE_RATE_HZ
        assert caps.is_power_calibrated is False
        assert caps.driver == "rtlsdr"
        assert caps.n_coherent_channels == 1
    finally:
        device.close()
