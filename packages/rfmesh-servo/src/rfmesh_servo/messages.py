"""Typed payload structs for the servo UART wire protocol.

Each dataclass corresponds to a payload layout in §3 of
``docs/wire-protocols/servo_uart_v1.md`` and provides ``pack``/``unpack``
helpers for the binary form. Frame envelope (CMD/LEN/CRC + COBS) lives in
:mod:`rfmesh_servo.protocol`; this module deals with payload bytes
only.

Conventions:

* Angles are exchanged on the wire as ``int16 LE`` deci-degrees (degrees ×
  10). The dataclasses expose ``float`` degrees for ergonomics; the
  ``pack``/``unpack`` round-trip is exact only at deci-degree granularity.
* ``reserved`` bytes are written as zero on transmit and ignored on
  receive (matching firmware behaviour: drop frames only on semantic
  failure, not on stray non-zero reserved bits).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import ClassVar, Final

from rfmesh_servo.exceptions import FrameError

_INT16_MIN: Final[int] = -(2**15)
_INT16_MAX: Final[int] = 2**15 - 1
NEVER_MOVED_SENTINEL: Final[int] = 0xFFFFFFFF


def _deci(angle_deg: float) -> int:
    """Convert a float degree value to int16 deci-degrees, raising on overflow."""
    deci = round(angle_deg * 10.0)
    if not _INT16_MIN <= deci <= _INT16_MAX:
        raise ValueError(
            f"angle {angle_deg}° (= {deci} deci-deg) is outside int16 range "
            f"[{_INT16_MIN}, {_INT16_MAX}]"
        )
    return deci


@dataclass(frozen=True, slots=True)
class MoveRequest:
    """Payload for ``MOVE`` (CMD 0x01). See §3.2."""

    axis: int
    angle_deg: float

    def pack(self) -> bytes:
        return struct.pack("<BBh", self.axis & 0xFF, 0, _deci(self.angle_deg))

    @classmethod
    def unpack(cls, data: bytes) -> MoveRequest:
        if len(data) != 4:
            raise FrameError(f"MOVE payload must be 4 bytes, got {len(data)}")
        axis, _reserved, deci = struct.unpack("<BBh", data)
        return cls(axis=axis, angle_deg=deci / 10.0)


@dataclass(frozen=True, slots=True)
class AxisRequest:
    """Single-byte ``axis`` payload used by ``POS_QUERY`` (0x02), ``STOP`` (0x04),
    ``CAL_QUERY`` (0x06), and ``CAL_PERSIST`` (0x08).

    ``CAL_PERSIST`` accepts the broadcast value ``0xFF`` for "all axes"; the
    other commands treat ``0xFF`` as an unconfigured axis.
    """

    axis: int

    def pack(self) -> bytes:
        return struct.pack("<B", self.axis & 0xFF)

    @classmethod
    def unpack(cls, data: bytes) -> AxisRequest:
        if len(data) != 1:
            raise FrameError(f"axis-only payload must be 1 byte, got {len(data)}")
        return cls(axis=data[0])


@dataclass(frozen=True, slots=True)
class AxisPosition:
    """Payload for ``POS_REPLY`` (CMD 0x03). See §3.4.

    Attributes:
        axis: Echoed axis ID.
        commanded_angle_deg: Last commanded angle in degrees. ``0.0`` when
            ``ms_since_move == NEVER_MOVED_SENTINEL``.
        ms_since_move: Milliseconds since the last ``MOVE`` for this axis,
            or :data:`NEVER_MOVED_SENTINEL` if the axis has not moved
            since boot.
    """

    axis: int
    commanded_angle_deg: float
    ms_since_move: int

    @property
    def is_settled_estimate(self) -> bool:
        """True if the firmware reports ≥ 250 ms since the last move (MG996 typical)."""
        return self.ms_since_move != NEVER_MOVED_SENTINEL and self.ms_since_move >= 250

    def pack(self) -> bytes:
        return struct.pack(
            "<BBhI",
            self.axis & 0xFF,
            0,
            _deci(self.commanded_angle_deg),
            self.ms_since_move & 0xFFFFFFFF,
        )

    @classmethod
    def unpack(cls, data: bytes) -> AxisPosition:
        if len(data) != 8:
            raise FrameError(f"POS_REPLY payload must be 8 bytes, got {len(data)}")
        axis, _reserved, deci, ms = struct.unpack("<BBhI", data)
        return cls(axis=axis, commanded_angle_deg=deci / 10.0, ms_since_move=ms)


@dataclass(frozen=True, slots=True)
class Calibration:
    """Payload for ``CAL_SET`` (0x05) / ``CAL_REPLY`` (0x07). See §3.6, §3.7.

    Validation matches the firmware's: pulses in ``[400, 2600]``, strictly
    ordered pulse and angle endpoints. Defaults from §3.7 are mirrored in
    :meth:`default`.

    Attributes:
        axis: Axis ID.
        pulse_min_us: PWM pulse width at ``angle_min_deg``.
        pulse_max_us: PWM pulse width at ``angle_max_deg``.
        angle_min_deg: Mechanical angle at ``pulse_min_us``.
        angle_max_deg: Mechanical angle at ``pulse_max_us``.
    """

    axis: int
    pulse_min_us: int
    pulse_max_us: int
    angle_min_deg: float
    angle_max_deg: float

    DEFAULT_PULSE_MIN_US: ClassVar[int] = 500
    DEFAULT_PULSE_MAX_US: ClassVar[int] = 2500
    DEFAULT_ANGLE_MIN_DEG: ClassVar[float] = -90.0
    DEFAULT_ANGLE_MAX_DEG: ClassVar[float] = 90.0

    @classmethod
    def default(cls, axis: int) -> Calibration:
        return cls(
            axis=axis,
            pulse_min_us=cls.DEFAULT_PULSE_MIN_US,
            pulse_max_us=cls.DEFAULT_PULSE_MAX_US,
            angle_min_deg=cls.DEFAULT_ANGLE_MIN_DEG,
            angle_max_deg=cls.DEFAULT_ANGLE_MAX_DEG,
        )

    @property
    def is_default(self) -> bool:
        return (
            self.pulse_min_us == self.DEFAULT_PULSE_MIN_US
            and self.pulse_max_us == self.DEFAULT_PULSE_MAX_US
            and _deci(self.angle_min_deg) == _deci(self.DEFAULT_ANGLE_MIN_DEG)
            and _deci(self.angle_max_deg) == _deci(self.DEFAULT_ANGLE_MAX_DEG)
        )

    def validate(self) -> None:
        """Mirror firmware's validation; raise ValueError on violation.

        The driver checks before transmitting so callers get a Python
        exception instead of a firmware ``ERR_BAD_CALIBRATION`` round-trip.
        """
        if not 400 <= self.pulse_min_us <= 2600:
            raise ValueError(f"pulse_min_us {self.pulse_min_us} outside [400, 2600]")
        if not 400 <= self.pulse_max_us <= 2600:
            raise ValueError(f"pulse_max_us {self.pulse_max_us} outside [400, 2600]")
        if self.pulse_min_us >= self.pulse_max_us:
            raise ValueError(
                f"pulse_min_us ({self.pulse_min_us}) must be < pulse_max_us ({self.pulse_max_us})"
            )
        if _deci(self.angle_min_deg) >= _deci(self.angle_max_deg):
            raise ValueError(
                f"angle_min ({self.angle_min_deg}) must be < angle_max ({self.angle_max_deg})"
            )

    def pack(self) -> bytes:
        return struct.pack(
            "<BBHHhhH",
            self.axis & 0xFF,
            0,
            self.pulse_min_us,
            self.pulse_max_us,
            _deci(self.angle_min_deg),
            _deci(self.angle_max_deg),
            0,
        )

    @classmethod
    def unpack(cls, data: bytes) -> Calibration:
        if len(data) != 12:
            raise FrameError(f"calibration payload must be 12 bytes, got {len(data)}")
        axis, _r1, pmin, pmax, amin, amax, _r2 = struct.unpack("<BBHHhhH", data)
        return cls(
            axis=axis,
            pulse_min_us=pmin,
            pulse_max_us=pmax,
            angle_min_deg=amin / 10.0,
            angle_max_deg=amax / 10.0,
        )


@dataclass(frozen=True, slots=True)
class Pong:
    """Payload for ``PONG`` (CMD 0x11). See §3.10.

    Attributes:
        proto_version: Protocol version the firmware speaks.
        fw_major: Firmware major version.
        fw_minor: Firmware minor version.
        fw_patch: Firmware patch version.
        git_short_sha: 8-char ASCII SHA prefix.
        axis_count: Number of configured axes.
    """

    proto_version: int
    fw_major: int
    fw_minor: int
    fw_patch: int
    git_short_sha: str
    axis_count: int

    def pack(self) -> bytes:
        sha_bytes = self.git_short_sha.encode("ascii")
        if len(sha_bytes) != 8:
            raise ValueError(f"git_short_sha must be exactly 8 ASCII chars, got {len(sha_bytes)}")
        return struct.pack(
            "<BBBB8sB3s",
            self.proto_version & 0xFF,
            self.fw_major & 0xFF,
            self.fw_minor & 0xFF,
            self.fw_patch & 0xFF,
            sha_bytes,
            self.axis_count & 0xFF,
            b"\x00\x00\x00",
        )

    @classmethod
    def unpack(cls, data: bytes) -> Pong:
        if len(data) != 16:
            raise FrameError(f"PONG payload must be 16 bytes, got {len(data)}")
        proto, major, minor, patch, sha, axis_count, _reserved = struct.unpack("<BBBB8sB3s", data)
        try:
            sha_str = sha.decode("ascii")
        except UnicodeDecodeError as exc:
            raise FrameError(f"PONG git_short_sha is not ASCII: {sha!r}") from exc
        return cls(
            proto_version=proto,
            fw_major=major,
            fw_minor=minor,
            fw_patch=patch,
            git_short_sha=sha_str,
            axis_count=axis_count,
        )


@dataclass(frozen=True, slots=True)
class Ack:
    """Payload for ``ACK`` (CMD 0xF0). Single byte echoing the acknowledged CMD."""

    cmd: int

    def pack(self) -> bytes:
        return struct.pack("<B", self.cmd & 0xFF)

    @classmethod
    def unpack(cls, data: bytes) -> Ack:
        if len(data) != 1:
            raise FrameError(f"ACK payload must be 1 byte, got {len(data)}")
        return cls(cmd=data[0])


@dataclass(frozen=True, slots=True)
class ErrorReply:
    """Payload for ``ERROR`` (CMD 0xFF). See §3.13.

    Attributes:
        original_cmd: The CMD byte that triggered the error.
        error_code: One of the ``ERR_*`` codes (see §5).
        message: Optional UTF-8 detail (≤ 64 bytes), empty when omitted.
    """

    original_cmd: int
    error_code: int
    message: str = field(default="")

    def pack(self) -> bytes:
        msg_bytes = self.message.encode("utf-8")
        if len(msg_bytes) > 64:
            raise ValueError(f"ERROR message must be ≤ 64 bytes UTF-8, got {len(msg_bytes)}")
        return bytes([self.original_cmd & 0xFF, self.error_code & 0xFF]) + msg_bytes

    @classmethod
    def unpack(cls, data: bytes) -> ErrorReply:
        if len(data) < 2:
            raise FrameError(f"ERROR payload must be ≥ 2 bytes, got {len(data)}")
        msg_bytes = data[2:]
        try:
            message = msg_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FrameError(f"ERROR message is not valid UTF-8: {msg_bytes!r}") from exc
        return cls(original_cmd=data[0], error_code=data[1], message=message)


__all__ = [
    "NEVER_MOVED_SENTINEL",
    "Ack",
    "AxisPosition",
    "AxisRequest",
    "Calibration",
    "ErrorReply",
    "MoveRequest",
    "Pong",
]
