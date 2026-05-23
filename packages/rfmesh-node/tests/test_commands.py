"""Unit tests for ``rfmesh_node.commands``.

Covers the discriminated-union parser ``parse_command``:

* Round-trip manual_steer + all_stop.
* Bad JSON raises ValueError.
* Unknown kind raises ValueError.
* Schema-invalid payload raises ValueError.
* Extra keys raise (``extra="forbid"``).
"""

from __future__ import annotations

import json

import pytest
from rfmesh_node.commands import (
    AllStopCommand,
    ManualSteerCommand,
    parse_command,
)


def test_parse_manual_steer_happy_path() -> None:
    frame = json.dumps(
        {
            "kind": "manual_steer",
            "axis": 0,
            "target_angle_deg": 12.5,
            "timeout_s": 7.0,
            "requestor_id": "test",
        }
    )
    cmd = parse_command(frame)
    assert isinstance(cmd, ManualSteerCommand)
    assert cmd.target_angle_deg == 12.5
    assert cmd.timeout_s == 7.0


def test_parse_manual_steer_defaults() -> None:
    frame = json.dumps({"kind": "manual_steer", "axis": 0, "target_angle_deg": 0.0})
    cmd = parse_command(frame)
    assert isinstance(cmd, ManualSteerCommand)
    assert cmd.timeout_s == 10.0
    assert cmd.requestor_id == "unknown"


def test_parse_all_stop() -> None:
    cmd = parse_command(json.dumps({"kind": "all_stop"}))
    assert isinstance(cmd, AllStopCommand)


def test_parse_rejects_non_json() -> None:
    with pytest.raises(ValueError, match="not JSON"):
        parse_command("this is not json")


def test_parse_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="schema validation"):
        parse_command(json.dumps({"kind": "wibble"}))


def test_parse_rejects_missing_required_field() -> None:
    # axis missing.
    with pytest.raises(ValueError, match="schema validation"):
        parse_command(json.dumps({"kind": "manual_steer", "target_angle_deg": 0.0}))


def test_parse_rejects_extra_keys() -> None:
    frame = json.dumps(
        {
            "kind": "manual_steer",
            "axis": 0,
            "target_angle_deg": 0.0,
            "secret": "boom",
        }
    )
    with pytest.raises(ValueError, match="schema validation"):
        parse_command(frame)


def test_parse_rejects_out_of_range_axis() -> None:
    frame = json.dumps({"kind": "manual_steer", "axis": -1, "target_angle_deg": 0.0})
    with pytest.raises(ValueError, match="schema validation"):
        parse_command(frame)


def test_parse_rejects_too_long_timeout() -> None:
    # timeout_s > 600 should fail.
    frame = json.dumps(
        {
            "kind": "manual_steer",
            "axis": 0,
            "target_angle_deg": 0.0,
            "timeout_s": 9999.0,
        }
    )
    with pytest.raises(ValueError, match="schema validation"):
        parse_command(frame)
