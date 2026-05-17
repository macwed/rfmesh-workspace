"""Shared fixtures for ``rfmesh-node`` tests.

Note: this file is intentionally NOT a package init -- no
``__init__.py`` in this directory, per ADR-006 / the workspace-wide
"no ``__init__.py`` under tests/" convention.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest
from rfmesh_contracts import (
    ArrayConfig,
    ArrayGeometry,
    BearerConfig,
    BearerKind,
    BearingReport,
    Capability,
    ConfidenceLevel,
    EllipseENU,
    EmitterClass,
    FixEvent,
    FusionConfig,
    GeodeticPosition,
    NodeConfig,
    NodeStatus,
    SDRConfig,
)

# ---------------------------------------------------------------------------
# Tiny factories -- one place a test asks for a known-good message and gets
# it. Pydantic models are frozen; tests that need a variant ``model_copy``.
# ---------------------------------------------------------------------------


@pytest.fixture()
def node_position() -> GeodeticPosition:
    """A nominal node position (Belgian Defence trench-demo neighbourhood)."""
    return GeodeticPosition(lat_deg=50.85, lon_deg=4.35, hae_m=50.0, sigma_m=5.0)


@pytest.fixture()
def emitter_position() -> GeodeticPosition:
    """An emitter position 1 km roughly east of the node."""
    return GeodeticPosition(lat_deg=50.85, lon_deg=4.365, hae_m=50.0, sigma_m=50.0)


@pytest.fixture()
def sdr_config() -> SDRConfig:
    """A simulator-driven SDRConfig (the only path the CLI plumbs in v1.0)."""
    return SDRConfig(
        driver="sim",
        sample_rate_hz=2_400_000.0,
        center_freq_hz=915_000_000.0,
        gain_db=30.0,
    )


@pytest.fixture()
def bearer_config() -> BearerConfig:
    """A Wi-Fi-only bearer config."""
    return BearerConfig(kind=BearerKind.WIFI, heartbeat_interval_s=0.05)


@pytest.fixture()
def l1_node_config(
    node_position: GeodeticPosition,
    sdr_config: SDRConfig,
    bearer_config: BearerConfig,
) -> NodeConfig:
    return NodeConfig(
        node_id="node-l1-test",
        position=node_position,
        heading_deg=0.0,
        sdr=sdr_config,
        array=None,
        capabilities=(Capability.L1_RSSI,),
        bearer=bearer_config,
        fusion_endpoint="udp://127.0.0.1:9000",
    )


@pytest.fixture()
def l2_array_config() -> ArrayConfig:
    return ArrayConfig(
        geometry=ArrayGeometry.ULA,
        n_elements=2,
        element_spacing_m=0.164,
    )


@pytest.fixture()
def l2_node_config(
    node_position: GeodeticPosition,
    sdr_config: SDRConfig,
    bearer_config: BearerConfig,
    l2_array_config: ArrayConfig,
) -> NodeConfig:
    return NodeConfig(
        node_id="node-l2-test",
        position=node_position,
        heading_deg=0.0,
        sdr=sdr_config.model_copy(update={"driver": "bladerf"}),
        array=l2_array_config,
        capabilities=(Capability.L2_MUSIC,),
        bearer=bearer_config,
        fusion_endpoint="udp://127.0.0.1:9000",
    )


def make_bearing_report(
    *,
    node_id: str = "node-l1-test",
    t_unix_ns: int = 1_700_000_000_000_000_000,
    azimuth_deg: float = 90.0,
    azimuth_sigma_deg: float = 3.0,
    method: Capability = Capability.L1_RSSI,
    raw_pseudospectrum: bytes | None = None,
    emitter_class: EmitterClass | None = None,
    classification_confidence: float | None = None,
    node_position: GeodeticPosition | None = None,
) -> BearingReport:
    """Factory for a valid BearingReport with sensible defaults."""
    if node_position is None:
        node_position = GeodeticPosition(lat_deg=50.85, lon_deg=4.35, hae_m=50.0, sigma_m=5.0)
    return BearingReport(
        node_id=node_id,
        t_unix_ns=t_unix_ns,
        node_position=node_position,
        azimuth_deg=azimuth_deg,
        azimuth_sigma_deg=azimuth_sigma_deg,
        method=method,
        raw_pseudospectrum=raw_pseudospectrum,
        emitter_class=emitter_class,
        classification_confidence=classification_confidence,
    )


def make_node_status(
    *,
    node_id: str = "node-l1-test",
    t_unix_ns: int = 1_700_000_000_000_000_000,
    healthy: bool = True,
    node_position: GeodeticPosition | None = None,
) -> NodeStatus:
    """Factory for a valid NodeStatus."""
    if node_position is None:
        node_position = GeodeticPosition(lat_deg=50.85, lon_deg=4.35, hae_m=50.0, sigma_m=5.0)
    return NodeStatus(
        node_id=node_id,
        t_unix_ns=t_unix_ns,
        position=node_position,
        active_capabilities=(Capability.L1_RSSI,),
        gnss_locked=True,
        healthy=healthy,
    )


def make_fix_event(
    *,
    t_unix_ns: int = 1_700_000_000_000_000_000,
    contributing_nodes: tuple[str, ...] = ("node-a", "node-b"),
) -> FixEvent:
    import uuid

    return FixEvent(
        fix_id=uuid.uuid4(),
        t_unix_ns=t_unix_ns,
        position=GeodeticPosition(lat_deg=50.85, lon_deg=4.36, hae_m=50.0, sigma_m=20.0),
        covariance_m2=(100.0, 0.0, 100.0),
        confidence_ellipse_95=EllipseENU(
            semi_major_m=22.0,
            semi_minor_m=20.0,
            orientation_deg=0.0,
        ),
        confidence_level=ConfidenceLevel.MEDIUM,
        contributing_nodes=contributing_nodes,
        residuals_deg=tuple(0.0 for _ in contributing_nodes),
        gdop=3.0,
        method="stansfield+mle",
    )


@pytest.fixture()
def fusion_config_fast() -> FusionConfig:
    """FusionConfig tuned for tight test loops (10 ms windows)."""
    return FusionConfig(
        listen_url="udp://127.0.0.1:9000",
        batch_window_ms=10.0,
        node_stale_after_s=5.0,
        min_bearings_for_fix=2,
    )


def find_free_udp_port() -> int:
    """Bind a UDP socket to port 0, read the kernel-assigned port, close it.

    Used by the bearer tests so two ``WifiBearer`` instances on the same
    host do not collide.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Lightweight in-test Fuser fake -- avoids importing rfmesh_fusion in unit
# tests, keeps the test suite focused on the FusionService glue.
# ---------------------------------------------------------------------------


class FakeFuser:
    """Records every fuse() call; returns a configurable FixEvent (or None)."""

    def __init__(self, fix: FixEvent | None) -> None:
        self._fix = fix
        self.calls: list[tuple[Any, ...]] = []

    def fuse(self, bearings: Any, config: Any) -> FixEvent | None:
        # Materialise the iterable so the test can inspect what we got.
        materialised = tuple(bearings)
        self.calls.append((materialised, config))
        return self._fix


class FakeCotPublisher:
    """Records every published FixEvent."""

    def __init__(self, *, raise_on_publish: bool = False) -> None:
        self.published: list[FixEvent] = []
        self._raise = raise_on_publish

    def publish(self, fix: FixEvent) -> None:
        if self._raise:
            msg = "FakeCotPublisher: configured to raise."
            raise RuntimeError(msg)
        self.published.append(fix)
