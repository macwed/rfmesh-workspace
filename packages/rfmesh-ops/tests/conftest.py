"""Test conftest for rfmesh-ops.

Forces matplotlib's ``Agg`` backend BEFORE any panel import so the
suite runs headless (no DISPLAY required) in CI and on laptops without
X / Wayland.

This file MUST run before any ``rfmesh_ops`` import in the test
session; pytest discovers and imports conftest.py before test modules,
so the ``matplotlib.use("Agg")`` here precedes any
``import matplotlib.pyplot`` triggered by panel modules.
"""

from __future__ import annotations

import asyncio
import math
import struct
from collections.abc import Iterator
from uuid import UUID

import matplotlib

# Force the headless Agg backend BEFORE any pyplot / panel import. This
# file is imported by pytest before any test module, so the backend pin
# precedes any rfmesh_ops import elsewhere in the suite.
matplotlib.use("Agg")

import matplotlib.figure as mpl_figure
import matplotlib.pyplot as plt
import pytest
from matplotlib.axes import Axes
from rfmesh_contracts import (
    BearingReport,
    Capability,
    ConfidenceLevel,
    EllipseENU,
    EmitterClass,
    FixEvent,
    GeodeticPosition,
    NodeStatus,
)


@pytest.fixture
def figure_and_axes() -> Iterator[tuple[mpl_figure.Figure, Axes]]:
    """A fresh matplotlib Figure + single Axes for one panel-under-test.

    The fixture closes the figure after the test so plt's global figure
    registry does not accumulate.
    """
    fig = plt.figure()
    ax = fig.add_subplot(1, 1, 1)
    try:
        yield fig, ax
    finally:
        plt.close(fig)


@pytest.fixture
def axes(
    figure_and_axes: tuple[mpl_figure.Figure, Axes],
) -> Axes:
    """Just the Axes, for panel tests that do not need the Figure handle."""
    _, ax = figure_and_axes
    return ax


@pytest.fixture
def sample_geodetic_position() -> GeodeticPosition:
    """A node-class GeodeticPosition (Poznan-ish bench coordinates)."""
    return GeodeticPosition(lat_deg=52.40, lon_deg=16.93, hae_m=80.0, sigma_m=5.0)


@pytest.fixture
def sample_emitter_position() -> GeodeticPosition:
    """An emitter-class GeodeticPosition for FixEvent construction."""
    # ~3 km north-east of the bench position above.
    return GeodeticPosition(lat_deg=52.42, lon_deg=16.96, hae_m=0.0, sigma_m=120.0)


@pytest.fixture
def sample_bearing_report(sample_geodetic_position: GeodeticPosition) -> BearingReport:
    """A canonical L1 BearingReport with no pseudospectrum payload."""
    return BearingReport(
        node_id="node-rtl-01",
        t_unix_ns=1_700_000_000_000_000_000,
        node_position=sample_geodetic_position,
        azimuth_deg=45.0,
        azimuth_sigma_deg=5.0,
        method=Capability.L1_RSSI,
        snr_db=18.0,
    )


@pytest.fixture
def sample_l2_bearing_report(sample_geodetic_position: GeodeticPosition) -> BearingReport:
    """A canonical L2 MUSIC bearing with a synthesised pseudospectrum."""
    # 720 float32 samples over [0, 360) at 0.5 deg step (the
    # INTERFACES.md §3 convention).
    n_samples = 720
    samples = bytearray()
    for k in range(n_samples):
        az = k * 0.5
        # A Gaussian bump at 45 deg, value in log-magnitude (negative
        # baseline, peak near 0).
        log_mag = -30.0 + 30.0 * math.exp(-((az - 45.0) ** 2) / (2.0 * 2.0**2))
        samples.extend(_f32_to_le_bytes(log_mag))
    return BearingReport(
        node_id="node-bladerf-01",
        t_unix_ns=1_700_000_000_100_000_000,
        node_position=sample_geodetic_position,
        azimuth_deg=45.0,
        azimuth_sigma_deg=1.2,
        method=Capability.L2_MUSIC,
        snr_db=22.0,
        raw_pseudospectrum=bytes(samples),
    )


def _f32_to_le_bytes(value: float) -> bytes:
    return struct.pack("<f", value)


@pytest.fixture
def sample_fix_event(sample_emitter_position: GeodeticPosition) -> FixEvent:
    """A canonical 3-node MEDIUM-band FixEvent."""
    return FixEvent(
        fix_id=UUID("12345678-1234-5678-1234-567812345678"),
        t_unix_ns=1_700_000_000_500_000_000,
        position=sample_emitter_position,
        covariance_m2=(150.0**2, 0.0, 100.0**2),
        confidence_ellipse_95=EllipseENU(
            semi_major_m=393.0,
            semi_minor_m=357.0,
            orientation_deg=10.0,
        ),
        confidence_level=ConfidenceLevel.MEDIUM,
        contributing_nodes=("node-rtl-01", "node-rtl-02", "node-rtl-03"),
        residuals_deg=(0.5, -0.3, 0.2),
        gdop=1.16,
        method="stansfield+mle",
        emitter_class=EmitterClass.ELRS,
    )


@pytest.fixture
def sample_fix_with_outlier(sample_emitter_position: GeodeticPosition) -> FixEvent:
    """A FixEvent with one node showing a >3 sigma residual (sigma~1 deg)."""
    return FixEvent(
        fix_id=UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        t_unix_ns=1_700_000_000_600_000_000,
        position=sample_emitter_position,
        covariance_m2=(200.0**2, 0.0, 150.0**2),
        confidence_ellipse_95=EllipseENU(
            semi_major_m=500.0,
            semi_minor_m=400.0,
            orientation_deg=5.0,
        ),
        confidence_level=ConfidenceLevel.LOW,
        contributing_nodes=("node-rtl-01", "node-rtl-02", "node-rtl-03"),
        residuals_deg=(0.2, -0.1, -8.5),  # the third bearing is the outlier
        gdop=2.0,
        method="stansfield+mle",
    )


@pytest.fixture
def sample_node_status(sample_geodetic_position: GeodeticPosition) -> NodeStatus:
    """A canonical healthy NodeStatus."""
    return NodeStatus(
        node_id="node-rtl-01",
        t_unix_ns=1_700_000_000_000_000_000,
        position=sample_geodetic_position,
        active_capabilities=(Capability.L1_RSSI,),
        gnss_locked=True,
        healthy=True,
        status_detail="",
    )


@pytest.fixture
def sample_gnss_denied_status(sample_geodetic_position: GeodeticPosition) -> NodeStatus:
    """A NodeStatus with gnss_locked=False -- the EW indicator path."""
    return NodeStatus(
        node_id="node-rtl-02",
        t_unix_ns=1_700_000_000_000_000_000,
        position=GeodeticPosition(
            lat_deg=sample_geodetic_position.lat_deg + 0.01,
            lon_deg=sample_geodetic_position.lon_deg + 0.01,
        ),
        active_capabilities=(Capability.L1_RSSI,),
        gnss_locked=False,
        healthy=True,
        status_detail="GNSS denied (EW indicator)",
    )


@pytest.fixture
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    """A fresh asyncio loop per test (pytest-asyncio compatible).

    Some tests use ``asyncio.run`` directly, but this fixture is here
    for tests that want explicit loop control without pulling in
    pytest-asyncio.
    """
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()
