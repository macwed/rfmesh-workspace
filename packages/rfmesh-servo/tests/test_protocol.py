"""Frame codec tests for rfmesh_servo.protocol."""

from __future__ import annotations

import pytest
from rfmesh_servo.exceptions import FrameError
from rfmesh_servo.protocol import (
    MAX_PAYLOAD_BYTES,
    PROTOCOL_VERSION,
    Cmd,
    decode_frame,
    encode_frame,
)


def test_protocol_version_is_one() -> None:
    """§3.1: PROTOCOL_VERSION = 1 in v1."""
    assert PROTOCOL_VERSION == 1


def test_ping_frame_matches_spec_7_3() -> None:
    """§7.3: PING wire bytes are 02 10 03 7C 1E 00."""
    wire = encode_frame(Cmd.PING, b"")
    assert wire == bytes.fromhex("02 10 03 7C 1E 00")


def test_decode_ping_round_trip() -> None:
    wire = encode_frame(Cmd.PING, b"")
    cmd, payload = decode_frame(wire)
    assert cmd == Cmd.PING
    assert payload == b""


@pytest.mark.parametrize(
    ("cmd", "payload"),
    [
        (Cmd.MOVE, bytes.fromhex("01 00 00 84 03"[6:])),
        (Cmd.PING, b""),
        (Cmd.STOP, b"\x00"),
        (Cmd.POS_QUERY, b"\x00"),
        (Cmd.CAL_PERSIST, b"\xff"),
        (Cmd.RESET, b""),
        (Cmd.ACK, b"\x01"),
        (Cmd.ERROR, b"\x01\x02"),
    ],
)
def test_round_trip_for_each_command(cmd: Cmd, payload: bytes) -> None:
    wire = encode_frame(cmd, payload)
    decoded_cmd, decoded_payload = decode_frame(wire)
    assert decoded_cmd == cmd
    assert decoded_payload == payload


def test_round_trip_with_max_payload() -> None:
    payload = bytes(range(256))[:MAX_PAYLOAD_BYTES]
    wire = encode_frame(0x42, payload)
    cmd, decoded = decode_frame(wire)
    assert cmd == 0x42
    assert decoded == payload


def test_round_trip_with_payload_containing_zero_bytes() -> None:
    """COBS must transparently carry interior 0x00 bytes."""
    payload = bytes([0x00, 0x42, 0x00, 0x00, 0xFF, 0x00])
    wire = encode_frame(Cmd.MOVE, payload)
    assert b"\x00" not in wire[:-1]
    cmd, decoded = decode_frame(wire)
    assert cmd == Cmd.MOVE
    assert decoded == payload


def test_encode_rejects_oversized_payload() -> None:
    with pytest.raises(ValueError, match="MAX_PAYLOAD_BYTES"):
        encode_frame(Cmd.MOVE, b"\x00" * (MAX_PAYLOAD_BYTES + 1))


def test_encode_rejects_out_of_range_cmd() -> None:
    with pytest.raises(ValueError):
        encode_frame(0x100, b"")
    with pytest.raises(ValueError):
        encode_frame(-1, b"")


def test_decode_rejects_empty_input() -> None:
    with pytest.raises(FrameError):
        decode_frame(b"")
    with pytest.raises(FrameError):
        decode_frame(b"\x00")


def test_decode_rejects_corrupted_crc() -> None:
    wire = bytearray(encode_frame(Cmd.PING, b""))
    # Flip the lowest CRC bit. The byte index of the CRC inside the COBS
    # output for a 4-byte logical frame is the last code-byte position; we
    # reach it by decoding then re-encoding with one bit flipped.
    cmd, _payload = decode_frame(bytes(wire))
    assert cmd == Cmd.PING
    # Construct a fresh corrupt frame: same logical body but wrong CRC.
    from rfmesh_servo.cobs import cobs_encode

    bad_logical = bytes([Cmd.PING, 0x00, 0x00, 0x00])  # zero CRC
    bad_wire = cobs_encode(bad_logical) + b"\x00"
    with pytest.raises(FrameError, match="CRC"):
        decode_frame(bad_wire)


def test_decode_rejects_length_mismatch() -> None:
    """LEN says 5 but only 0 payload bytes follow."""
    from rfmesh_servo.cobs import cobs_encode
    from rfmesh_servo.crc import crc16_ccitt_false

    body = bytes([Cmd.PING, 0x05])
    crc = crc16_ccitt_false(body).to_bytes(2, "little")
    wire = cobs_encode(body + crc) + b"\x00"
    with pytest.raises(FrameError, match="length mismatch"):
        decode_frame(wire)


def test_decode_strips_optional_terminator() -> None:
    """Caller may or may not pass the trailing 0x00 in."""
    wire_with = encode_frame(Cmd.PING, b"")
    wire_without = wire_with[:-1]
    a = decode_frame(wire_with)
    b = decode_frame(wire_without)
    assert a == b
