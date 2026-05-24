"""NodeController dispatch of SendCommsMessageCommand (ADR-025 Iter 4.5)."""

from __future__ import annotations

from collections import deque

import pytest
from rfmesh_node.commands import SendCommsMessageCommand
from rfmesh_node.controller import NodeController, NodeState


class _FakeCommsLoop:
    """Captures payloads handed to ``queue_outbound`` for assertions."""

    def __init__(self, max_bytes: int = 64) -> None:
        self.outbox: deque[bytes] = deque()
        self._max = max_bytes

    def queue_outbound(self, payload: bytes) -> None:
        if len(payload) > self._max:
            msg = f"payload of {len(payload)} bytes exceeds {self._max}"
            raise ValueError(msg)
        self.outbox.append(payload)


def _controller(
    comms_loop: object | None = None,
    state: NodeState = NodeState.PARKED,
) -> NodeController:
    """Minimal controller with no servo / no DF loops."""
    ctrl = NodeController(
        servo=object(),  # never called by these tests
        sweep_loop=None,
        rendezvous_loop=None,
        calibrated_arc_deg=None,
        node_id="node-test",
        comms_loop=comms_loop,
    )
    ctrl._state = state
    return ctrl


def _cmd(text: str = "hi", peer: str = "node-peer") -> SendCommsMessageCommand:
    return SendCommsMessageCommand(
        peer_node_id=peer,
        payload_text=text,
        requestor_id="ui-test",
    )


@pytest.mark.asyncio
async def test_routes_to_comms_loop_when_wired() -> None:
    fake = _FakeCommsLoop()
    ctrl = _controller(comms_loop=fake)
    refusal = await ctrl.dispatch_command(_cmd("hello"))
    assert refusal is None
    assert list(fake.outbox) == [b"hello"]


@pytest.mark.asyncio
async def test_refuses_when_no_comms_loop() -> None:
    ctrl = _controller(comms_loop=None)
    refusal = await ctrl.dispatch_command(_cmd())
    assert refusal is not None
    assert refusal["kind"] == "command_refused"
    assert refusal["refused_kind"] == "send_comms_message"
    assert "comms loop not wired" in str(refusal["reason"])


@pytest.mark.asyncio
async def test_refuses_in_fault_state() -> None:
    fake = _FakeCommsLoop()
    ctrl = _controller(comms_loop=fake, state=NodeState.FAULT)
    refusal = await ctrl.dispatch_command(_cmd())
    assert refusal is not None
    assert refusal["kind"] == "command_refused"
    assert "FAULT" in str(refusal["reason"])
    assert not fake.outbox


@pytest.mark.asyncio
async def test_oversize_payload_refused_with_reason() -> None:
    fake = _FakeCommsLoop(max_bytes=16)
    ctrl = _controller(comms_loop=fake)
    refusal = await ctrl.dispatch_command(_cmd(text="x" * 32))
    assert refusal is not None
    assert refusal["kind"] == "command_refused"
    assert "exceeds" in str(refusal["reason"])
    assert not fake.outbox


@pytest.mark.asyncio
async def test_utf8_payload_encoded_on_wire() -> None:
    """UTF-8 multibyte chars go through verbatim."""
    fake = _FakeCommsLoop()
    ctrl = _controller(comms_loop=fake)
    refusal = await ctrl.dispatch_command(_cmd(text="łącze"))
    assert refusal is None
    assert list(fake.outbox) == ["łącze".encode()]


@pytest.mark.asyncio
async def test_unrelated_commands_still_dispatch_when_comms_wired() -> None:
    """Wiring comms_loop must not regress the existing kind switch."""
    from rfmesh_node.commands import ClearFaultCommand

    fake = _FakeCommsLoop()
    ctrl = _controller(comms_loop=fake, state=NodeState.FAULT)
    # ClearFaultCommand transitions FAULT -> previous mode (PARKED here).
    refusal = await ctrl.dispatch_command(ClearFaultCommand())
    # clear_fault returns None on success; FAULT cleared, comms outbox untouched.
    assert refusal is None
    assert not fake.outbox
