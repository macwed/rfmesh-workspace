"""ScenarioLoader unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from rfmesh_contracts import Capability, EmitterClass
from rfmesh_demo_replay import (
    Scenario,
    ScenarioLoader,
)


def test_loader_parses_tiny_scenario(tiny_scenario_path: Path) -> None:
    """tiny_scenario.yaml loads cleanly into a Scenario object."""
    scenario = ScenarioLoader().load(tiny_scenario_path)
    assert isinstance(scenario, Scenario)
    assert scenario.scenario_id == "tiny_v1"
    assert {n.node_id for n in scenario.nodes} == {"node-west", "node-east"}
    assert len(scenario.beats) == 1
    assert scenario.beats[0].beat_id == "SMOKE"
    assert scenario.beats[0].active_nodes == ("node-west", "node-east")
    assert Capability.L1_RSSI in scenario.nodes[0].capabilities


def test_loader_parses_existing_trench_demo() -> None:
    """The design-intent scenarios/trench_demo.yaml round-trips through the loader.

    Locks the loader's tolerance for the YAML shape the architect
    has documented (capital-case ``UNKNOWN``, ``demo_beats``
    vs ``beats``, ``channel`` vs ``channel_model``).
    """
    repo_root = Path(__file__).resolve().parents[3]
    yaml_path = repo_root / "scenarios" / "trench_demo.yaml"
    if not yaml_path.exists():
        pytest.skip("scenarios/trench_demo.yaml not available in this checkout")
    scenario = ScenarioLoader().load(yaml_path)
    assert scenario.scenario_id == "trench_demo_v1"
    assert len(scenario.nodes) == 4
    assert len(scenario.beats) == 4
    assert scenario.emitter.expected_class is EmitterClass.UNKNOWN


def test_loader_rejects_missing_required_field(tmp_path: Path, tiny_scenario_path: Path) -> None:
    """A YAML missing a required top-level field raises ValidationError."""
    payload = yaml.safe_load(tiny_scenario_path.read_text(encoding="utf-8"))
    del payload["emitter"]
    broken = tmp_path / "broken.yaml"
    broken.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ValidationError):
        ScenarioLoader().load(broken)


def test_loader_rejects_bad_emitter_class(tmp_path: Path, tiny_scenario_path: Path) -> None:
    """A scenario with an unknown emitter class fails fast."""
    payload = yaml.safe_load(tiny_scenario_path.read_text(encoding="utf-8"))
    payload["emitter"]["expected_class"] = "not-a-real-class"
    broken = tmp_path / "bad_class.yaml"
    broken.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ValidationError):
        ScenarioLoader().load(broken)


def test_loader_rejects_bad_enu_origin(tmp_path: Path, tiny_scenario_path: Path) -> None:
    """Out-of-range lat/lon on the ENU origin fails validation."""
    payload = yaml.safe_load(tiny_scenario_path.read_text(encoding="utf-8"))
    payload["enu_origin"]["lat_deg"] = 999.0
    broken = tmp_path / "bad_origin.yaml"
    broken.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ValidationError):
        ScenarioLoader().load(broken)


def test_loader_rejects_beat_with_unknown_node(tmp_path: Path, tiny_scenario_path: Path) -> None:
    """A beat that references a node id missing from the scenario is rejected."""
    payload = yaml.safe_load(tiny_scenario_path.read_text(encoding="utf-8"))
    payload["demo_beats"][0]["active_nodes"] = ["node-west", "ghost-node"]
    broken = tmp_path / "ghost_beat.yaml"
    broken.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ValidationError):
        ScenarioLoader().load(broken)


def test_loader_node_to_node_config_geometry(tiny_scenario_path: Path) -> None:
    """to_node_config places the geodetic position consistently with the ENU offset.

    The 1 km east node's longitude offset should be ~ 1000 / (111320 * cos(50 deg))
    degrees east of the origin -- about +0.014 deg.
    """
    scenario = ScenarioLoader().load(tiny_scenario_path)
    east = next(n for n in scenario.nodes if n.node_id == "node-east")
    cfg = east.to_node_config(scenario.enu_origin)
    assert cfg.position.lat_deg == pytest.approx(50.0, abs=1e-3)
    # 1000 m east at 50 deg lat -> ~0.01396 deg lon east
    assert cfg.position.lon_deg == pytest.approx(5.0 + 0.01396, abs=1e-3)
