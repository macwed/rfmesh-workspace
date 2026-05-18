"""Shared test helpers for the servo UART driver: a Transport fake."""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest
from rfmesh_servo.messages import Pong
from rfmesh_servo.protocol import Cmd, decode_frame, encode_frame
from rfmesh_servo.transport import Transport


class FakeTransport(Transport):
    """In-memory Transport for driver tests.

    Two usage patterns:

    * Preload ``rx_queue`` with bytes the driver should read; inspect
      ``tx_log`` after the call.
    * Set ``on_write`` to a callback that decodes each outgoing frame and
      appends a reply via :meth:`reply_with`. This simulates a fake
      firmware that responds to commands.

    ``read`` honours the requested timeout but sleeps in 10 ms slices to
    avoid hot-spinning when no data is available, so tests using short
    timeouts (50 ms) finish quickly.
    """

    def __init__(self) -> None:
        self.tx_log = bytearray()
        self.rx_queue = bytearray()
        self.pending_after_reset = bytearray()
        self.on_write: Callable[[FakeTransport, bytes], None] | None = None
        self.input_buffer_resets: int = 0
        self.closed = False

    def write(self, data: bytes) -> None:
        self.tx_log.extend(data)
        if self.on_write is not None:
            self.on_write(self, bytes(data))

    def read(self, n: int, timeout_s: float) -> bytes:
        deadline = time.monotonic() + timeout_s
        while True:
            if self.rx_queue:
                chunk = bytes(self.rx_queue[:n])
                del self.rx_queue[:n]
                return chunk
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return b""
            time.sleep(min(remaining, 0.01))

    def reset_input_buffer(self) -> None:
        self.rx_queue.clear()
        self.input_buffer_resets += 1
        if self.pending_after_reset:
            self.rx_queue.extend(self.pending_after_reset)
            self.pending_after_reset.clear()

    def close(self) -> None:
        self.closed = True

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------

    def queue_frame(self, cmd: int, payload: bytes) -> None:
        self.rx_queue.extend(encode_frame(cmd, payload))

    def queue_raw(self, data: bytes) -> None:
        self.rx_queue.extend(data)

    def queue_unsolicited_frame(self, cmd: int, payload: bytes) -> None:
        """Simulate firmware sending a frame after the host opens the port.

        The bytes are buffered and only delivered to ``rx_queue`` when the
        driver calls :meth:`reset_input_buffer` (which it does in
        :meth:`ServoDriver.connect`). Without this, pre-queued data would
        be cleared by the reset before the driver could read it.
        """
        self.pending_after_reset.extend(encode_frame(cmd, payload))

    def queue_unsolicited_raw(self, data: bytes) -> None:
        self.pending_after_reset.extend(data)

    def last_sent_frame(self) -> tuple[int, bytes]:
        """Decode the most recently written frame (assumes single 0x00 terminator)."""
        idx = self.tx_log.rfind(0x00)
        if idx < 0:
            raise AssertionError("no frame terminator in tx_log")
        # Find the frame *before* the last 0x00.
        prev = self.tx_log.rfind(0x00, 0, idx)
        wire = bytes(self.tx_log[prev + 1 : idx + 1])
        return decode_frame(wire)


def make_pong(proto_version: int = 1) -> Pong:
    return Pong(
        proto_version=proto_version,
        fw_major=0,
        fw_minor=2,
        fw_patch=3,
        git_short_sha="deadbeef",
        axis_count=1,
    )


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def auto_pong_transport() -> FakeTransport:
    """Transport whose firmware responds to PING with a v1 PONG."""
    transport = FakeTransport()

    def responder(self_: FakeTransport, frame_bytes: bytes) -> None:
        cmd, _payload = decode_frame(frame_bytes)
        if cmd == Cmd.PING:
            self_.queue_frame(Cmd.PONG, make_pong().pack())

    transport.on_write = responder
    return transport
