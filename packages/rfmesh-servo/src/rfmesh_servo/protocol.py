"""Frame codec for the servo UART wire protocol.

Implements the CMD/LEN/PAYLOAD/CRC TLV envelope and the COBS framing layer
described in §2 of ``docs/wire-protocols/servo_uart_v1.md``. Knows nothing
about the underlying transport: it deals only in bytes.

A "wire frame" is a COBS-encoded payload terminated by a single ``0x00``
byte. A "logical frame" is the pre-COBS bytes ``[CMD][LEN][PAYLOAD][CRC16
LE]``. The CRC is computed over ``CMD || LEN || PAYLOAD`` only (NOT over
the CRC bytes or the COBS encoding).
"""

from __future__ import annotations

from enum import IntEnum
from typing import Final

from rfmesh_servo.cobs import cobs_decode, cobs_encode
from rfmesh_servo.crc import crc16_ccitt_false
from rfmesh_servo.exceptions import FrameError

PROTOCOL_VERSION: Final[int] = 1
"""Wire protocol version. Mirrors §3.10 of the spec."""

FRAME_TERMINATOR: Final[int] = 0x00
"""Byte value that terminates a COBS-encoded frame on the wire."""

MAX_PAYLOAD_BYTES: Final[int] = 253
"""Per §2 the LEN byte covers payloads of 0–253 bytes (CMD+LEN+253+CRC2 = 256)."""


class Cmd(IntEnum):
    """Command bytes from §3.1."""

    MOVE = 0x01
    POS_QUERY = 0x02
    POS_REPLY = 0x03
    STOP = 0x04
    CAL_SET = 0x05
    CAL_QUERY = 0x06
    CAL_REPLY = 0x07
    CAL_PERSIST = 0x08
    PING = 0x10
    PONG = 0x11
    RESET = 0x20
    ACK = 0xF0
    ERROR = 0xFF


class ErrorCode(IntEnum):
    """Error codes from §5."""

    ERR_NO_SUCH_AXIS = 0x01
    ERR_ANGLE_OUT_OF_RANGE = 0x02
    ERR_BAD_CALIBRATION = 0x03
    ERR_NVS_FAIL = 0x04
    ERR_NOT_CALIBRATED = 0x05
    ERR_BUSY = 0x06
    ERR_UNKNOWN_CMD = 0xFE
    ERR_INTERNAL = 0xFF


def encode_frame(cmd: int, payload: bytes) -> bytes:
    """Build a wire frame ready to write to the transport.

    Layout: ``COBS(CMD || LEN || PAYLOAD || CRC16_LE) || 0x00``.

    Args:
        cmd: Command byte (0–255).
        payload: Raw payload bytes (0–253).

    Returns:
        The full wire frame including the trailing ``0x00`` terminator.

    Raises:
        ValueError: If ``cmd`` is out of range or ``payload`` exceeds
            :data:`MAX_PAYLOAD_BYTES`.
    """
    if not 0 <= cmd <= 0xFF:
        raise ValueError(f"cmd byte must fit in u8, got {cmd}")
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError(
            f"payload length {len(payload)} exceeds MAX_PAYLOAD_BYTES ({MAX_PAYLOAD_BYTES})"
        )
    body = bytes([cmd, len(payload)]) + payload
    crc = crc16_ccitt_false(body)
    body += crc.to_bytes(2, "little")
    return cobs_encode(body) + bytes([FRAME_TERMINATOR])


def decode_frame(wire: bytes) -> tuple[int, bytes]:
    """Decode a wire frame into ``(cmd, payload)``.

    Args:
        wire: Frame bytes. May or may not include the trailing ``0x00``;
            the terminator is stripped if present. Must not contain
            interior ``0x00`` bytes.

    Returns:
        ``(cmd, payload)``.

    Raises:
        FrameError: On COBS decode failure, length mismatch, CRC mismatch,
            or other framing errors. The transport-level reader is
            responsible for catching this and resyncing at the next
            ``0x00`` (per §2.1).
    """
    if not wire:
        raise FrameError("empty wire frame")
    if wire[-1] == FRAME_TERMINATOR:
        wire = wire[:-1]
    if not wire:
        raise FrameError("wire frame contains only the terminator")
    decoded = cobs_decode(wire)
    if len(decoded) < 4:
        raise FrameError(f"decoded frame must be ≥ 4 bytes (cmd+len+crc), got {len(decoded)}")
    cmd = decoded[0]
    length = decoded[1]
    expected_total = 2 + length + 2
    if len(decoded) != expected_total:
        raise FrameError(
            f"frame length mismatch: LEN={length} implies {expected_total} bytes total, "
            f"got {len(decoded)}"
        )
    payload = decoded[2 : 2 + length]
    crc_bytes = decoded[2 + length :]
    received_crc = int.from_bytes(crc_bytes, "little")
    expected_crc = crc16_ccitt_false(decoded[: 2 + length])
    if received_crc != expected_crc:
        raise FrameError(
            f"CRC mismatch: received 0x{received_crc:04x}, expected 0x{expected_crc:04x}"
        )
    return cmd, payload


__all__ = [
    "FRAME_TERMINATOR",
    "MAX_PAYLOAD_BYTES",
    "PROTOCOL_VERSION",
    "Cmd",
    "ErrorCode",
    "decode_frame",
    "encode_frame",
]
