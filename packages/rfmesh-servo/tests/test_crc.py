"""CRC-16/CCITT-FALSE tests, including the §7.1 wire-spec vectors."""

from __future__ import annotations

import pytest
from rfmesh_servo.crc import crc16_ccitt_false


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"", 0xFFFF),
        (b"123456789", 0x29B1),
        (bytes.fromhex("010400008403"), 0x2589),
        (bytes.fromhex("1000"), 0x1E7C),
    ],
    ids=["empty", "ascii_123456789", "move_payload", "ping_body"],
)
def test_crc_spec_vectors(data: bytes, expected: int) -> None:
    """§7.1 of docs/wire-protocols/servo_uart_v1.md: must match canonical algorithm."""
    assert crc16_ccitt_false(data) == expected


def test_crc_is_byte_order_sensitive() -> None:
    a = crc16_ccitt_false(b"\x10\x00")
    b = crc16_ccitt_false(b"\x00\x10")
    assert a != b


def test_crc_is_deterministic_and_pure() -> None:
    payload = bytes(range(256))
    assert crc16_ccitt_false(payload) == crc16_ccitt_false(payload)


def test_crc_in_u16_range() -> None:
    for n in (0, 1, 7, 16, 64, 253):
        crc = crc16_ccitt_false(bytes(n))
        assert 0 <= crc <= 0xFFFF
