"""High-level driver for the ESP32-C3 servo controller (servo UART v1).

Wraps :mod:`rfmesh_servo.protocol` and a :class:`Transport` into a
synchronous, single-outstanding-command client. Every public command
sends one frame and waits for the matching reply with a per-call
timeout; concurrent calls are not supported and not required (the
protocol is master/slave, host-initiated).

See ``docs/wire-protocols/servo_uart_v1.md`` for the wire contract this
class implements.
"""

from __future__ import annotations

import logging
import time
from types import TracebackType
from typing import Final

from rfmesh_servo.exceptions import (
    FrameError,
    IncompatibleProtocolError,
    LinenoiseDetectedError,
    ServoCommandError,
    ServoProtocolError,
    ServoTimeoutError,
)
from rfmesh_servo.messages import (
    Ack,
    AxisPosition,
    AxisRequest,
    Calibration,
    ErrorReply,
    MoveRequest,
    Pong,
)
from rfmesh_servo.protocol import (
    PROTOCOL_VERSION,
    Cmd,
    decode_frame,
    encode_frame,
)
from rfmesh_servo.transport import Transport

logger = logging.getLogger(__name__)

DEFAULT_REPLY_TIMEOUT_S: Final[float] = 0.5
"""Per §4.3 of the spec: 500 ms before treating the link as broken."""

DEFAULT_CONNECT_GRACE_S: Final[float] = 1.5
"""How long to wait for an unsolicited startup PONG before falling back to PING."""

_READ_CHUNK_BYTES: Final[int] = 256
_BROADCAST_AXIS: Final[int] = 0xFF


class ServoDriver:
    """Synchronous host driver for the servo controller.

    The driver does NOT own the transport's lifecycle by default — pass
    ``own_transport=True`` (or use the context manager form) if you want
    :meth:`close` to also close the transport. This matches the test
    pattern of injecting a fake transport whose lifecycle is managed by
    the test itself.

    Typical use::

        with ServoDriver(SerialTransport("/dev/ttyACM0"), own_transport=True) as servo:
            servo.connect()
            servo.move(axis=0, angle_deg=45.0)
            pos = servo.position(axis=0)

    Args:
        transport: Byte-stream transport implementing :class:`Transport`.
        reply_timeout_s: Per-reply timeout in seconds.
        connect_grace_s: How long :meth:`connect` waits for an unsolicited
            startup PONG before sending PING itself.
        own_transport: If true, :meth:`close` closes the transport.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        reply_timeout_s: float = DEFAULT_REPLY_TIMEOUT_S,
        connect_grace_s: float = DEFAULT_CONNECT_GRACE_S,
        own_transport: bool = False,
    ) -> None:
        self._transport = transport
        self._reply_timeout_s = reply_timeout_s
        self._connect_grace_s = connect_grace_s
        self._own_transport = own_transport
        self._rx_buffer = bytearray()
        self._pong: Pong | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __enter__(self) -> ServoDriver:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._own_transport:
            self._transport.close()

    @property
    def firmware_info(self) -> Pong | None:
        """Cached PONG from the most recent :meth:`connect` (or :meth:`ping`)."""
        return self._pong

    # ------------------------------------------------------------------
    # Connection / liveness
    # ------------------------------------------------------------------

    def connect(self) -> Pong:
        """Establish the connection: drain, read unsolicited PONG (best effort), validate version.

        Per §4.4: after connection the host MUST send PING and validate
        ``proto_version`` before any other command. We try to consume an
        unsolicited startup PONG first (saves one round trip when the
        firmware just enumerated), and fall back to an explicit PING.

        Raises:
            IncompatibleProtocolError: PONG advertises a non-matching
                protocol version.
            LinenoiseDetectedError: We received bytes that don't form a
                valid frame — likely the firmware is in linenoise shell
                mode (see §6).
            ServoTimeoutError: No reply within :attr:`connect_grace_s` +
                :attr:`reply_timeout_s` after sending PING.
        """
        self._transport.reset_input_buffer()
        self._rx_buffer.clear()

        cmd, payload = self._try_unsolicited_pong()
        if cmd is None:
            self._send(Cmd.PING, b"")
            cmd, payload = self._read_decoded_frame(self._reply_timeout_s)

        if cmd != Cmd.PONG:
            raise ServoProtocolError(f"expected PONG (0x{Cmd.PONG:02x}), got cmd 0x{cmd:02x}")
        pong = Pong.unpack(payload)
        if pong.proto_version != PROTOCOL_VERSION:
            raise IncompatibleProtocolError(
                f"firmware advertises protocol v{pong.proto_version}, "
                f"driver requires v{PROTOCOL_VERSION}"
            )
        self._pong = pong
        logger.info(
            "servo controller online: fw %d.%d.%d sha=%s axes=%d",
            pong.fw_major,
            pong.fw_minor,
            pong.fw_patch,
            pong.git_short_sha,
            pong.axis_count,
        )
        return pong

    def _try_unsolicited_pong(self) -> tuple[int | None, bytes]:
        """Read one frame within the grace window; return ``(None, b"")`` on timeout."""
        try:
            return self._read_decoded_frame(self._connect_grace_s)
        except ServoTimeoutError:
            return None, b""

    def ping(self) -> Pong:
        """Send PING and return the firmware's PONG."""
        self._send(Cmd.PING, b"")
        cmd, payload = self._read_decoded_frame(self._reply_timeout_s)
        if cmd != Cmd.PONG:
            raise ServoProtocolError(f"expected PONG (0x{Cmd.PONG:02x}), got cmd 0x{cmd:02x}")
        self._pong = Pong.unpack(payload)
        return self._pong

    # ------------------------------------------------------------------
    # Motion / state
    # ------------------------------------------------------------------

    def move(self, axis: int, angle_deg: float) -> None:
        """Set the target angle for ``axis``. Returns when firmware ACKs (no settling wait)."""
        request = MoveRequest(axis=axis, angle_deg=angle_deg)
        self._send_expect_ack(Cmd.MOVE, request.pack())

    def position(self, axis: int) -> AxisPosition:
        """Query the last commanded angle and ms-since-last-move for ``axis``."""
        self._send(Cmd.POS_QUERY, AxisRequest(axis=axis).pack())
        _cmd, payload = self._await_reply(Cmd.POS_QUERY, expected=Cmd.POS_REPLY)
        return AxisPosition.unpack(payload)

    def stop(self, axis: int) -> None:
        """Disable PWM on ``axis`` (servo goes limp)."""
        self._send_expect_ack(Cmd.STOP, AxisRequest(axis=axis).pack())

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------

    def set_calibration(self, calibration: Calibration) -> None:
        """Write ``calibration`` to firmware RAM. Use :meth:`persist_calibration` to flash it."""
        calibration.validate()
        self._send_expect_ack(Cmd.CAL_SET, calibration.pack())

    def get_calibration(self, axis: int) -> Calibration:
        """Read the current RAM calibration for ``axis``."""
        self._send(Cmd.CAL_QUERY, AxisRequest(axis=axis).pack())
        _cmd, payload = self._await_reply(Cmd.CAL_QUERY, expected=Cmd.CAL_REPLY)
        return Calibration.unpack(payload)

    def persist_calibration(self, axis: int | None = None) -> None:
        """Persist RAM calibration to NVS for ``axis`` (or all axes if ``None``)."""
        target = _BROADCAST_AXIS if axis is None else axis
        self._send_expect_ack(Cmd.CAL_PERSIST, AxisRequest(axis=target).pack())

    # ------------------------------------------------------------------
    # System
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Soft-reboot the firmware. Acks immediately; expect a USB enumeration glitch.

        After this returns, the caller must reopen the transport (USB-CDC
        re-enumerates) and call :meth:`connect` again. The driver does
        NOT do that automatically — the underlying device path may even
        change.
        """
        self._send_expect_ack(Cmd.RESET, b"")

    # ------------------------------------------------------------------
    # Internal: frame I/O
    # ------------------------------------------------------------------

    def _send(self, cmd: int, payload: bytes) -> None:
        wire = encode_frame(cmd, payload)
        logger.debug("-> cmd=0x%02x len=%d wire=%s", cmd, len(payload), wire.hex())
        self._transport.write(wire)

    def _send_expect_ack(self, cmd: int, payload: bytes) -> None:
        self._send(cmd, payload)
        _reply_cmd, reply_payload = self._await_reply(cmd, expected=Cmd.ACK)
        ack = Ack.unpack(reply_payload)
        if ack.cmd != cmd:
            raise ServoProtocolError(f"ACK echoed cmd 0x{ack.cmd:02x} but request was 0x{cmd:02x}")

    def _await_reply(self, original_cmd: int, *, expected: int) -> tuple[int, bytes]:
        """Read the next decoded frame; raise on ERROR or unexpected cmd.

        Args:
            original_cmd: The command we just sent (for ERROR attribution).
            expected: The CMD byte we expect on success.

        Returns:
            ``(cmd, payload)`` where ``cmd == expected``.

        Raises:
            ServoCommandError: Firmware replied with ERROR for our cmd.
            ServoProtocolError: We got a different CMD than expected.
            ServoTimeoutError: No frame arrived within the timeout.
        """
        cmd, payload = self._read_decoded_frame(self._reply_timeout_s)
        if cmd == Cmd.ERROR:
            err = ErrorReply.unpack(payload)
            raise ServoCommandError(err.original_cmd, err.error_code, err.message)
        if cmd != expected:
            raise ServoProtocolError(
                f"expected cmd 0x{expected:02x} for request 0x{original_cmd:02x}, got 0x{cmd:02x}"
            )
        return cmd, payload

    def _read_decoded_frame(self, timeout_s: float) -> tuple[int, bytes]:
        """Read wire frames until one decodes; surface linenoise hint on persistent garbage.

        Per §2.1 the receiver silently drops malformed frames and resyncs
        at the next ``0x00``. We do that, but if we burn the entire
        timeout on garbage — bytes that failed to decode as a frame OR
        bytes that arrived without a ``0x00`` terminator — surface
        :class:`LinenoiseDetectedError` instead of plain
        :class:`ServoTimeoutError`. The most likely cause of sustained
        non-protocol bytes is the firmware sitting in linenoise shell mode
        (see §6).
        """
        deadline = time.monotonic() + timeout_s
        saw_garbage = False
        last_error: FrameError | None = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if saw_garbage or self._rx_buffer:
                    raise LinenoiseDetectedError(
                        "received non-protocol bytes from servo controller; "
                        "the firmware may be in linenoise shell mode. "
                        "Send `proto\\n` from a terminal or power-cycle the controller."
                    ) from last_error
                raise ServoTimeoutError(f"no reply within {timeout_s * 1000:.0f} ms")
            try:
                wire = self._read_wire_frame(remaining)
            except ServoTimeoutError:
                if self._rx_buffer or saw_garbage:
                    raise LinenoiseDetectedError(
                        "received non-protocol bytes from servo controller; "
                        "the firmware may be in linenoise shell mode. "
                        "Send `proto\\n` from a terminal or power-cycle the controller."
                    ) from last_error
                raise
            if not wire:
                continue
            try:
                return decode_frame(wire)
            except FrameError as exc:
                logger.debug("dropped malformed frame (%d bytes): %s", len(wire), exc)
                saw_garbage = True
                last_error = exc

    def _read_wire_frame(self, timeout_s: float) -> bytes:
        """Read bytes until the first ``0x00`` terminator. Return them (without the 0x00).

        Raises :class:`ServoTimeoutError` if no terminator is seen within
        ``timeout_s``. Bytes accumulated before the timeout are kept in
        the internal buffer for the next call (they may complete a frame
        once more bytes arrive).
        """
        deadline = time.monotonic() + timeout_s
        while True:
            idx = self._rx_buffer.find(b"\x00")
            if idx >= 0:
                wire = bytes(self._rx_buffer[:idx])
                del self._rx_buffer[: idx + 1]
                return wire
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ServoTimeoutError(f"no frame terminator within {timeout_s * 1000:.0f} ms")
            chunk = self._transport.read(_READ_CHUNK_BYTES, remaining)
            if chunk:
                self._rx_buffer.extend(chunk)


__all__ = [
    "DEFAULT_CONNECT_GRACE_S",
    "DEFAULT_REPLY_TIMEOUT_S",
    "ServoDriver",
]
