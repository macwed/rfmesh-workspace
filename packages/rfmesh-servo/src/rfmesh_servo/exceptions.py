"""Servo-specific exceptions.

Re-homed from the salvaged monolithic ``rfmesh.exceptions`` per
``SALVAGE_AUDIT.md`` Part 2 + Part 7 ("Servo-specific exceptions in
monolithic ``rfmesh/exceptions.py`` move with code"). The hierarchy is
preserved byte-for-byte; only the module home moves.

The new repo deliberately keeps exceptions package-local rather than
inheriting a workspace-wide ``RFMeshError`` base. Cross-package
exception inheritance would introduce a sibling-import edge that the
``import-linter`` star-independence contract forbids
(``WORKSTREAMS.md`` §1 / ``WD-1``). The base ``ServoError`` instead
roots inside this package, and other packages catch their own bases.
"""

from __future__ import annotations


class ServoError(Exception):
    """Base for every exception raised by ``rfmesh_servo``."""


class ServoProtocolError(ServoError):
    """Base class for servo UART protocol errors.

    See ``docs/wire-protocols/servo_uart_v1.md`` for the wire contract
    these errors gate.
    """


class FrameError(ServoProtocolError):
    """Raised on COBS decode failure, CRC mismatch, length mismatch, or unknown CMD.

    Per the wire spec, the receiver silently drops such frames and resyncs at
    the next ``0x00`` terminator. This exception surfaces the decision point
    to the caller (driver or test) so it can choose to log and continue.
    """


class IncompatibleProtocolError(ServoProtocolError):
    """Raised when the firmware's PONG advertises a protocol version this driver does not speak."""


class LinenoiseDetectedError(ServoProtocolError):
    """Raised when the driver suspects the firmware is in linenoise shell mode.

    Detected by failing to parse the unsolicited startup PONG as a valid frame.
    Recovery requires sending ``proto\\n`` over the same port from a terminal,
    or power-cycling the controller.
    """


class ServoTimeoutError(ServoProtocolError):
    """Raised when no reply arrives within the configured timeout window."""


class ServoCommandError(ServoProtocolError):
    """Raised when the firmware replies with an ERROR frame.

    Attributes:
        original_cmd: The command byte that triggered the error.
        error_code: One of the ``ERR_*`` codes from §5 of the wire spec.
        message: Optional UTF-8 detail from the firmware (may be empty).
    """

    def __init__(self, original_cmd: int, error_code: int, message: str = "") -> None:
        self.original_cmd = original_cmd
        self.error_code = error_code
        self.message = message
        detail = f": {message}" if message else ""
        super().__init__(
            f"servo command 0x{original_cmd:02x} failed with code 0x{error_code:02x}{detail}"
        )
