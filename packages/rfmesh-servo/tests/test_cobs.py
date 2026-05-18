"""COBS encode/decode tests, including the §7.2 wire-spec vectors."""

from __future__ import annotations

import os

import pytest
from rfmesh_servo.cobs import cobs_decode, cobs_encode
from rfmesh_servo.exceptions import FrameError


@pytest.mark.parametrize(
    ("plain", "encoded"),
    [
        (b"\x01", b"\x02\x01"),
        (b"\x00", b"\x01\x01"),
        (b"\x01\x00\x02", b"\x02\x01\x02\x02"),
    ],
    ids=["single_nonzero", "single_zero", "zero_in_middle"],
)
def test_cobs_spec_vectors(plain: bytes, encoded: bytes) -> None:
    """§7.2 of docs/wire-protocols/servo_uart_v1.md."""
    assert cobs_encode(plain) == encoded
    assert cobs_decode(encoded) == plain


def test_cobs_encoded_contains_no_zero_bytes() -> None:
    for length in (0, 1, 7, 32, 253, 254, 255, 510, 1024):
        plain = os.urandom(length)
        encoded = cobs_encode(plain)
        assert b"\x00" not in encoded


def test_cobs_round_trip_random() -> None:
    for length in (0, 1, 7, 32, 253, 254, 255, 510, 1024):
        plain = os.urandom(length)
        assert cobs_decode(cobs_encode(plain)) == plain


def test_cobs_round_trip_all_zeros() -> None:
    for length in (1, 16, 254, 300):
        plain = b"\x00" * length
        assert cobs_decode(cobs_encode(plain)) == plain


def test_cobs_round_trip_at_254_byte_boundary() -> None:
    """The 0xFF code byte handles 254 non-zero bytes without an implicit trailing zero."""
    for length in (253, 254, 255, 256, 508):
        plain = bytes((i % 255) + 1 for i in range(length))  # no zeros
        encoded = cobs_encode(plain)
        assert b"\x00" not in encoded
        assert cobs_decode(encoded) == plain


def test_cobs_decode_rejects_empty_input() -> None:
    with pytest.raises(FrameError):
        cobs_decode(b"")


def test_cobs_decode_rejects_zero_code_byte() -> None:
    with pytest.raises(FrameError):
        cobs_decode(b"\x00\x01")


def test_cobs_decode_rejects_truncated_block() -> None:
    """Code byte claims 5 following bytes but buffer has fewer."""
    with pytest.raises(FrameError):
        cobs_decode(b"\x05\x01\x02")
