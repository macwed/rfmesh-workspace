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
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import serial

logger = logging.getLogger(__name__)


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


__all__ = ["SerialTransport", "Transport"]
