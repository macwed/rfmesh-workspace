"""Tests for ``SendCommsMessageCommand`` parse + discriminated-union routing."""

from __future__ import annotations

import json

import pytest
from rfmesh_node.commands import (
    AllStopCommand,
    ClearFaultCommand,
    ManualSteerCommand,
    SendCommsMessageCommand,
    parse_command,
)


def test_send_comms_message_parse_roundtrip() -> None:
    payload = {
        "kind": "send_comms_message",
        "peer_node_id": "node-rtl-02",
        "payload_text": "hello",
    }
    cmd = parse_command(json.dumps(payload))
    assert isinstance(cmd, SendCommsMessageCommand)
    assert cmd.peer_node_id == "node-rtl-02"
    assert cmd.payload_text == "hello"
    assert cmd.payload_bytes() == b"hello"


def test_send_comms_message_utf8_payload() -> None:
    """Non-ASCII text round-trips through UTF-8 encoding."""
    payload = {
        "kind": "send_comms_message",
        "peer_node_id": "node-rtl-02",
        "payload_text": "łącze",
    }
    cmd = parse_command(json.dumps(payload))
    assert isinstance(cmd, SendCommsMessageCommand)
    assert cmd.payload_bytes() == "łącze".encode()


def test_send_comms_message_rejects_empty_text() -> None:
    payload = {
        "kind": "send_comms_message",
        "peer_node_id": "node-rtl-02",
        "payload_text": "",
    }
    with pytest.raises(ValueError):
        parse_command(json.dumps(payload))


def test_send_comms_message_rejects_oversize_text() -> None:
    payload = {
        "kind": "send_comms_message",
        "peer_node_id": "node-rtl-02",
        "payload_text": "x" * 257,
    }
    with pytest.raises(ValueError):
        parse_command(json.dumps(payload))


def test_other_commands_still_parse() -> None:
    """Adding the new kind did not break the existing union."""
    steer = parse_command(
        json.dumps({"kind": "manual_steer", "axis": 0, "target_angle_deg": 30.0}),
    )
    assert isinstance(steer, ManualSteerCommand)
    stop = parse_command(json.dumps({"kind": "all_stop"}))
    assert isinstance(stop, AllStopCommand)
    clear = parse_command(json.dumps({"kind": "clear_fault"}))
    assert isinstance(clear, ClearFaultCommand)


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValueError):
        parse_command(json.dumps({"kind": "self_destruct"}))
