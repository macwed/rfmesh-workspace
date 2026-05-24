"""Tests for ``NodeRuntimeConfig`` (ADR-022 single-YAML wrapper)."""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError
from rfmesh_contracts import Capability, NodeConfig
from rfmesh_node.runtime_config import (
    CommandEndpointConfig,
    MotionOverrideConfig,
    NodeRuntimeConfig,
    RendezvousOverrideConfig,
    SweepOverrideConfig,
)


def _minimal_node_payload(node_id: str = "node-test") -> dict:
    """Return a NodeConfig YAML payload that satisfies the contract validators."""
    return {
        "schema_version": "1.3.0",
        "node_id": node_id,
        "position": {"lat_deg": 50.33, "lon_deg": 5.0, "hae_m": 200.0, "sigma_m": 5.0},
        "heading_deg": 142.0,
        "sdr": {
            "driver": "rtlsdr",
            "sample_rate_hz": 2_048_000.0,
            "center_freq_hz": 868_000_000.0,
            "gain_db": 30.0,
        },
        "capabilities": ["l1_rssi"],
        "bearer": {"kind": "wifi", "heartbeat_interval_s": 2.0},
        "fusion_endpoint": "http://10.0.0.1:8000/bearings",
    }


def test_minimal_yaml_round_trip() -> None:
    """A YAML with just a `node:` block validates and exposes defaults."""
    payload = {"node": _minimal_node_payload()}
    runtime = NodeRuntimeConfig.model_validate(payload)
    assert runtime.servo_port is None
    assert runtime.rendezvous is None
    assert runtime.command_endpoint.enabled is False
    assert runtime.sweep.step_deg == 2.0  # SweepOverrideConfig default
    assert runtime.motion.max_vel_dps == 25.0  # MotionOverrideConfig default
    # The node block must materialise to a frozen NodeConfig identical to the contract.
    assert isinstance(runtime.node, NodeConfig)
    assert Capability.L1_RSSI in runtime.node.capabilities


def test_typo_in_yaml_key_rejected() -> None:
    """`extra="forbid"` everywhere -- a typo'd key fails at load time (B3)."""
    payload = {
        "node": _minimal_node_payload(),
        "sweap": {"step_deg": 1.5},  # typo: "sweap" not "sweep"
    }
    with pytest.raises(ValidationError, match="sweap"):
        NodeRuntimeConfig.model_validate(payload)


def test_sweep_arc_order_validated() -> None:
    """A reversed sweep arc is a loud refusal, not a silent swap."""
    payload = {
        "node": _minimal_node_payload(),
        "sweep": {"min_deg": 30.0, "max_deg": -30.0},
    }
    with pytest.raises(ValidationError, match=r"min_deg.*must be < max_deg"):
        NodeRuntimeConfig.model_validate(payload)


def test_rendezvous_requires_servo_port() -> None:
    """ADR-019 needs a servo; without one, rendezvous is a loud refusal."""
    payload = {
        "node": _minimal_node_payload(),
        "rendezvous": {
            "peer_node_id": "node-test-2",
            "peer": {"lat_deg": 50.34, "lon_deg": 5.005},
        },
    }
    with pytest.raises(ValidationError, match="rendezvous requires servo_port"):
        NodeRuntimeConfig.model_validate(payload)


def test_rendezvous_with_servo_port_validates() -> None:
    payload = {
        "node": _minimal_node_payload(),
        "servo_port": "/dev/ttyACM0",
        "rendezvous": {
            "peer_node_id": "node-test-2",
            "peer": {"lat_deg": 50.34, "lon_deg": 5.005},
        },
    }
    runtime = NodeRuntimeConfig.model_validate(payload)
    assert runtime.rendezvous is not None
    assert runtime.rendezvous.peer_node_id == "node-test-2"


def test_rendezvous_arc_must_be_inside_sweep_arc() -> None:
    """A wider rendezvous arc than the sweep arc would point outside calibration."""
    payload = {
        "node": _minimal_node_payload(),
        "servo_port": "/dev/ttyACM0",
        "sweep": {"min_deg": -45.0, "max_deg": 45.0},
        "rendezvous": {
            "peer_node_id": "node-test-2",
            "peer": {"lat_deg": 50.34, "lon_deg": 5.005},
            "min_servo_deg": -90.0,
            "max_servo_deg": 90.0,
        },
    }
    with pytest.raises(ValidationError, match="wider than the L1 sweep arc"):
        NodeRuntimeConfig.model_validate(payload)


def test_command_endpoint_requires_url_when_enabled() -> None:
    """Enabling the command channel without a URL is a loud refusal."""
    payload = {
        "node": _minimal_node_payload(),
        "command_endpoint": {"enabled": True, "backend_ws_url": None},
    }
    with pytest.raises(ValidationError, match="requires backend_ws_url"):
        NodeRuntimeConfig.model_validate(payload)


def test_command_endpoint_rejects_http_scheme() -> None:
    """The command channel is WebSocket; http(s) is a load-time refusal."""
    payload = {
        "node": _minimal_node_payload(),
        "command_endpoint": {"enabled": True, "backend_ws_url": "http://10.0.0.1:8000"},
    }
    with pytest.raises(ValidationError, match="must use ws:// or wss://"):
        NodeRuntimeConfig.model_validate(payload)


def test_sweep_override_materialises_to_dataclass() -> None:
    s = SweepOverrideConfig(step_deg=1.5, dwell_samples=2048)
    dc = s.to_dataclass()
    assert dc.step_deg == 1.5
    assert dc.dwell_samples == 2048
    assert dc.axis == 0  # default preserved


def test_rendezvous_override_materialises_to_dataclass() -> None:
    r = RendezvousOverrideConfig(
        peer_node_id="node-b",
        peer={"lat_deg": 50.34, "lon_deg": 5.005},
        refine_half_arc_deg=15.0,
    )
    dc = r.to_dataclass()
    assert dc.peer_node_id == "node-b"
    assert dc.peer_position.lat_deg == 50.34
    assert dc.refine_half_arc_deg == 15.0


def test_motion_override_materialises_to_dataclass() -> None:
    m = MotionOverrideConfig(max_vel_dps=10.0, invert_direction=True)
    dc = m.to_dataclass()
    assert dc.max_vel_dps == 10.0
    assert dc.invert_direction is True


def test_example_yaml_validates() -> None:
    """The field-deploy example YAML must validate cleanly (operator surface)."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    example = repo_root / "field-deploy" / "node-config.example.yaml"
    with example.open("r", encoding="utf-8") as fp:
        payload = yaml.safe_load(fp)
    runtime = NodeRuntimeConfig.model_validate(payload)
    assert runtime.node.node_id == "node-rtl-01"
    assert runtime.servo_port == "/dev/ttyACM0"
    assert runtime.command_endpoint.enabled is False  # safe default in the example


def test_command_endpoint_default_is_disabled() -> None:
    payload = {"node": _minimal_node_payload()}
    runtime = NodeRuntimeConfig.model_validate(payload)
    assert isinstance(runtime.command_endpoint, CommandEndpointConfig)
    assert runtime.command_endpoint.enabled is False
    assert runtime.command_endpoint.backend_ws_url is None
