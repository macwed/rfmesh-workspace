"""Shared fixtures for rfmesh-contracts tests.

Per ADR-006, no `__init__.py` under tests/. This conftest.py is loaded
by pytest's rootdir-based discovery. Fixtures are kept minimal — the
contract types are declarative, so factories just produce one sane
instance of each.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from rfmesh_contracts.config import (  # type: ignore[import-untyped, unused-ignore]
    ArrayConfig,
    BearerConfig,
    FusionConfig,
    NodeConfig,
    SDRConfig,
)
from rfmesh_contracts.enums import (  # type: ignore[import-untyped, unused-ignore]
    ArrayGeometry,
    BearerKind,
    Capability,
)
from rfmesh_contracts.geospatial import (  # type: ignore[import-untyped, unused-ignore]
    EllipseENU,
    GeodeticPosition,
)
from rfmesh_contracts.messages import (  # type: ignore[import-untyped, unused-ignore]
    BearingReport,
    FixEvent,
    NodeStatus,
)
from rfmesh_contracts.version import SCHEMA_VERSION  # type: ignore[import-untyped, unused-ignore]

# A plausible UTC nanosecond timestamp (2026-05-14T00:00:00Z).
_DEFAULT_T_UNIX_NS: int = 1_778_976_000_000_000_000


@pytest.fixture
def sample_position() -> GeodeticPosition:
    return GeodeticPosition(lat_deg=52.0, lon_deg=21.0, hae_m=0.0, sigma_m=5.0)


@pytest.fixture
def sample_ellipse() -> EllipseENU:
    return EllipseENU(semi_major_m=100.0, semi_minor_m=50.0, orientation_deg=45.0)


@pytest.fixture
def sample_bearing_report(sample_position: GeodeticPosition) -> BearingReport:
    return BearingReport(
        node_id="test-node-001",
        t_unix_ns=_DEFAULT_T_UNIX_NS,
        node_position=sample_position,
        azimuth_deg=137.5,
        azimuth_sigma_deg=1.5,
        method=Capability.L2_MUSIC,
    )


@pytest.fixture
def sample_fix_event(
    sample_position: GeodeticPosition, sample_ellipse: EllipseENU
) -> FixEvent:
    return FixEvent(
        fix_id=UUID("12345678-1234-5678-1234-567812345678"),
        t_unix_ns=_DEFAULT_T_UNIX_NS,
        position=sample_position,
        covariance_m2=(2500.0, 0.0, 625.0),
        confidence_ellipse_95=sample_ellipse,
        confidence_level="medium",  # ConfidenceLevel.MEDIUM string value
        contributing_nodes=("node-a", "node-b", "node-c"),
        residuals_deg=(0.5, -0.3, 0.2),
        gdop=1.2,
        method="stansfield+mle",
    )


@pytest.fixture
def sample_node_status(sample_position: GeodeticPosition) -> NodeStatus:
    return NodeStatus(
        node_id="test-node-001",
        t_unix_ns=_DEFAULT_T_UNIX_NS,
        position=sample_position,
        active_capabilities=(Capability.L1_RSSI,),
        gnss_locked=True,
        healthy=True,
        status_detail="",
    )


@pytest.fixture
def sample_sdr_config() -> SDRConfig:
    return SDRConfig(
        driver="rtlsdr",
        sample_rate_hz=2.4e6,
        center_freq_hz=915.0e6,
        gain_db=30.0,
        bias_tee=False,
    )


@pytest.fixture
def sample_array_config() -> ArrayConfig:
    return ArrayConfig(
        geometry=ArrayGeometry.ULA,
        n_elements=2,
        element_spacing_m=0.164,
    )


@pytest.fixture
def sample_bearer_config() -> BearerConfig:
    return BearerConfig(
        kind=BearerKind.WIFI,
        heartbeat_interval_s=2.0,
    )


@pytest.fixture
def sample_node_config(
    sample_position: GeodeticPosition,
    sample_sdr_config: SDRConfig,
    sample_bearer_config: BearerConfig,
) -> NodeConfig:
    return NodeConfig(
        node_id="test-node-001",
        position=sample_position,
        heading_deg=90.0,
        sdr=sample_sdr_config,
        capabilities=(Capability.L1_RSSI,),
        bearer=sample_bearer_config,
        fusion_endpoint="udp://10.0.0.1:9000",
    )


@pytest.fixture
def sample_fusion_config() -> FusionConfig:
    return FusionConfig(listen_url="udp://0.0.0.0:9000")


@pytest.fixture
def current_schema_version() -> str:
    return SCHEMA_VERSION
