"""Transport abstraction for the servo UART driver.

The wire protocol layer (:mod:`rfmesh_servo.protocol`) operates on
bytes; the driver (:mod:`rfmesh_servo.driver`) needs a way to push
those bytes onto a wire and pull frames back. We define a minimal
:class:`Transport` Protocol so unit tests can inject a fake (loopback,
fault-injecting, byte-by-byte trickle) without dragging in pyserial.

The :class:`SerialTransport` adapter lazy-imports ``pyserial``. ``pyserial``
is a top-level dependency of ``rf-mesh`` (see ``pyproject.toml``), so a
plain ``uv sync`` is enough to use it on real hardware; the lazy import
keeps the rest of the codebase importable on a broken install.

The :class:`TcpTransport` adapter speaks the same COBS+TLV wire
protocol over a TCP socket, so the ESP32-C6 can host a TCP server
(WiFi station) instead of USB-CDC. Wire format unchanged; only the
byte pipe differs. Use case: 2x ESP32-C6 on the same laptop where
USB-CDC enumeration / autosuspend / hub contention destabilises the
link. Selected at the YAML layer by writing ``servo_port:
"tcp://host:port"`` instead of ``"/dev/ttyACM0"``.
"""

from __future__ import annotations

import contextlib
import logging
import socket
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import serial

logger = logging.getLogger(__name__)

_TCP_DEFAULT_PORT = 5555
_TCP_CONNECT_TIMEOUT_S = 5.0
_TCP_PORT_MAX = 65535


class Transport(Protocol):
    """Minimal byte-stream interface the driver needs.

    Implementations must be safe for the driver's single-outstanding,
    request-then-reply usage; concurrent access is not required.
    """

    def read(self, n: int, timeout_s: float) -> bytes:
        """Read up to ``n`` bytes, blocking for at most ``timeout_s`` seconds.

        Returns whatever is available when the timeout expires (possibly
        an empty bytes object). Returning fewer bytes than requested is
        not an error — the driver framing layer reassembles frames.
        """
        ...

    def write(self, data: bytes) -> None:
        """Write ``data`` to the wire. Should block until all bytes are queued."""
        ...

    def reset_input_buffer(self) -> None:
        """Discard any bytes currently buffered on the receive side.

        Called by the driver after :class:`~rfmesh_servo.protocol.Cmd.RESET`
        and after frame-decode resync to drop garbage that accumulated
        across a USB enumeration glitch.
        """
        ...

    def close(self) -> None:
        """Release the underlying resource."""
        ...


class SerialTransport:
    """:class:`Transport` backed by ``pyserial``.

    Lazy-imports ``pyserial`` so a broken install (or a stripped-down
    environment) can still import the rest of the package. ``pyserial`` is
    a declared top-level dependency of ``rf-mesh``; a plain ``uv sync``
    pulls it in.

    Args:
        port: Device path, e.g. ``/dev/ttyACM0``.
        baudrate: Nominal baud rate. USB-CDC ignores this in practice but
            ``pyserial`` requires a value; the spec sets 115200.
    """

    def __init__(self, port: str, baudrate: int = 115200) -> None:
        try:
            import serial as _serial
        except ImportError as exc:
            raise ImportError(
                "pyserial is required for SerialTransport. Reinstall the project with `uv sync`."
            ) from exc

        self._serial: serial.Serial = _serial.Serial(port=port, baudrate=baudrate, timeout=0)
        logger.debug("SerialTransport opened %s @ %d baud", port, baudrate)

    def read(self, n: int, timeout_s: float) -> bytes:
        self._serial.timeout = timeout_s
        return bytes(self._serial.read(n))

    def write(self, data: bytes) -> None:
        self._serial.write(data)
        self._serial.flush()

    def reset_input_buffer(self) -> None:
        self._serial.reset_input_buffer()

    def close(self) -> None:
        self._serial.close()


class TcpTransport:
    """:class:`Transport` backed by a TCP socket.

    Connects in ``__init__`` (matches :class:`SerialTransport` lifecycle).
    The remote end is the ESP32-C6 TCP server bound to
    ``CONFIG_SERVO_TCP_PORT`` (default 5555). The wire is byte-identical
    to USB-CDC: COBS-framed TLV with CRC-16/CCITT-FALSE, terminator
    ``0x00``. The TCP transport adds no framing of its own.

    No auto-reconnect on mid-session disconnect — the driver surfaces
    the :class:`ConnectionError` (B3, fail loudly). Callers that want
    resilience open a new transport.

    Args:
        host: Hostname or IPv4 of the ESP32-C6. mDNS names like
            ``node-01.local`` work when ``avahi-daemon`` is up on the
            laptop.
        port: TCP port; default 5555 matches the firmware default.
        connect_timeout_s: Connect-call timeout. Read/write timeouts are
            per-call (see :meth:`read`).

    Raises:
        ConnectionError: Connect refused / timed out / unreachable.
            Wraps the underlying :class:`OSError` so callers do not
            need to import the socket module.
    """

    def __init__(
        self,
        host: str,
        port: int = _TCP_DEFAULT_PORT,
        connect_timeout_s: float = _TCP_CONNECT_TIMEOUT_S,
    ) -> None:
        if not host:
            msg = "TcpTransport: host must be a non-empty string."
            raise ValueError(msg)
        if not 1 <= port <= _TCP_PORT_MAX:
            msg = f"TcpTransport: port out of range (got {port}, expected 1..{_TCP_PORT_MAX})."
            raise ValueError(msg)

        try:
            self._sock = socket.create_connection((host, port), timeout=connect_timeout_s)
        except OSError as exc:
            raise ConnectionError(
                f"TcpTransport: cannot connect to {host}:{port} "
                f"(connect_timeout_s={connect_timeout_s}): {exc}"
            ) from exc
        # Disable Nagle so small frames (most servo commands < 32 B)
        # are flushed immediately instead of waiting on the ACK clock.
        # Servo control is request-then-reply, latency-sensitive, not
        # throughput-sensitive.
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._host = host
        self._port = port
        logger.debug("TcpTransport opened tcp://%s:%d", host, port)

    def read(self, n: int, timeout_s: float) -> bytes:
        """Read up to ``n`` bytes, blocking for at most ``timeout_s`` seconds.

        Short reads are honest (B3): the driver's framing layer
        reassembles. Returns ``b""`` on timeout. A peer-closed socket
        is signalled by an empty return after the kernel drains the
        receive buffer — the driver detects this via repeated empty
        reads + framing-layer timeout, not by an exception, matching
        the :class:`SerialTransport` shape.
        """
        self._sock.settimeout(timeout_s)
        try:
            return self._sock.recv(n)
        except TimeoutError:
            return b""

    def write(self, data: bytes) -> None:
        """Send all bytes. Blocks until the kernel accepts them."""
        # ``sendall`` raises on partial send; we never want to silently
        # drop the tail of a COBS frame (B3 — short writes corrupt the
        # next decode pass on the peer).
        self._sock.sendall(data)

    def reset_input_buffer(self) -> None:
        """Drain anything currently readable on the socket, non-blocking.

        Matches :meth:`SerialTransport.reset_input_buffer` semantics:
        called after RESET or a frame-decode resync to drop stale
        bytes (e.g. half-frame the firmware queued before a reboot).
        """
        self._sock.setblocking(False)
        try:
            while True:
                chunk = self._sock.recv(4096)
                if not chunk:
                    return
        except BlockingIOError:
            return
        except OSError as exc:
            logger.debug("TcpTransport.reset_input_buffer: %s", exc)
            return
        finally:
            self._sock.setblocking(True)

    def close(self) -> None:
        """Close the socket. Idempotent."""
        with contextlib.suppress(OSError):
            self._sock.shutdown(socket.SHUT_RDWR)
        self._sock.close()


__all__ = ["SerialTransport", "TcpTransport", "Transport"]
