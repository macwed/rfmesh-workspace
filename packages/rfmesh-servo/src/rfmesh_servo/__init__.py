"""rfmesh-servo -- host-side servo driver and antenna sweep control.

Workstream A. Salvaged from ``rfmesh/scan/servo/`` in the prior repo,
re-homed under the new package with package-local exceptions. Controls
MG996R (and clones) via the ESP32-S2 servo controller per
``docs/wire-protocols/servo_uart_v1.md``.

Public API
----------

    from rfmesh_servo import ServoDriver, SerialTransport
    from rfmesh_servo.messages import Calibration, Pong

The lower-level codec (``crc``, ``cobs``, ``protocol``, ``messages``)
is exposed for tests and bench tooling but is not part of the stable
surface.
"""

from rfmesh_servo.driver import ServoDriver
from rfmesh_servo.exceptions import (
    FrameError,
    IncompatibleProtocolError,
    LinenoiseDetectedError,
    ServoCommandError,
    ServoError,
    ServoProtocolError,
    ServoTimeoutError,
)
from rfmesh_servo.messages import (
    AxisPosition,
    Calibration,
    ErrorReply,
    Pong,
)
from rfmesh_servo.protocol import (
    PROTOCOL_VERSION,
    Cmd,
    ErrorCode,
    decode_frame,
    encode_frame,
)
from rfmesh_servo.transport import SerialTransport, Transport

__all__ = [
    "PROTOCOL_VERSION",
    "AxisPosition",
    "Calibration",
    "Cmd",
    "ErrorCode",
    "ErrorReply",
    "FrameError",
    "IncompatibleProtocolError",
    "LinenoiseDetectedError",
    "Pong",
    "SerialTransport",
    "ServoCommandError",
    "ServoDriver",
    "ServoError",
    "ServoProtocolError",
    "ServoTimeoutError",
    "Transport",
    "decode_frame",
    "encode_frame",
]
