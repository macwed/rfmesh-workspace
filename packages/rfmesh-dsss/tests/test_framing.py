"""Tests for the DSSS frame format: encode / decode + CRC."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from rfmesh_dsss.exceptions import FrameDecodeError
from rfmesh_dsss.framing import (
    BROADCAST_DST,
    CRC_LEN_BITS,
    FIXED_OVERHEAD_BITS,
    HEADER_LEN_BITS,
    PREAMBLE_LEN_BITS,
    SYNC_WORD_HEX,
    SYNC_WORD_LEN_BITS,
    _crc16_ccitt_false,
    decode_frame,
    encode_frame,
)

_GOLDEN = Path(__file__).parent / "golden" / "frame_roundtrip.npz"

_PAYLOAD_MAX = 64


def test_crc16_check_value() -> None:
    """CRC-16/CCITT-FALSE check value: ``0x29B1`` over ``b"123456789"``.

    This is the canonical sanity check for the algorithm; the
    ``rfmesh-servo`` package's CRC test pins the same value, so a
    silent drift on either side surfaces immediately.
    """
    assert _crc16_ccitt_false(b"123456789") == 0x29B1


def test_overhead_constants_sum_correctly() -> None:
    """``FIXED_OVERHEAD_BITS = preamble + sync + header + CRC``."""
    expected = PREAMBLE_LEN_BITS + SYNC_WORD_LEN_BITS + HEADER_LEN_BITS + CRC_LEN_BITS
    assert expected == FIXED_OVERHEAD_BITS


def test_encode_then_decode_roundtrip() -> None:
    """A frame survives ``decode_frame(encode_frame(...))`` exactly."""
    payload = b"hello, dsss"
    bits = encode_frame(
        src_node_id=1,
        dst_node_id=2,
        sequence_no=42,
        payload=payload,
        payload_max_bytes=_PAYLOAD_MAX,
    )
    decoded = decode_frame(bits, payload_max_bytes=_PAYLOAD_MAX)
    assert decoded.src_node_id == 1
    assert decoded.dst_node_id == 2
    assert decoded.sequence_no == 42
    assert decoded.payload == payload


def test_encode_then_decode_empty_payload() -> None:
    """Zero-byte payload is legal -- ``decode_frame`` returns ``b""``."""
    bits = encode_frame(0, 0, 0, b"", payload_max_bytes=_PAYLOAD_MAX)
    decoded = decode_frame(bits, payload_max_bytes=_PAYLOAD_MAX)
    assert decoded.payload == b""


def test_encode_then_decode_broadcast_dst() -> None:
    """``BROADCAST_DST`` (0xFF) round-trips through the dst field."""
    bits = encode_frame(7, BROADCAST_DST, 1, b"x", payload_max_bytes=_PAYLOAD_MAX)
    decoded = decode_frame(bits, payload_max_bytes=_PAYLOAD_MAX)
    assert decoded.dst_node_id == BROADCAST_DST


def test_bit_stream_length_is_overhead_plus_payload() -> None:
    """``len(bits) == FIXED_OVERHEAD_BITS + 8 * payload_bytes``."""
    payload = b"abcdefghij"
    bits = encode_frame(1, 2, 3, payload, payload_max_bytes=_PAYLOAD_MAX)
    assert bits.size == FIXED_OVERHEAD_BITS + 8 * len(payload)


def test_crc_corruption_raises() -> None:
    """A flipped CRC bit raises ``FrameDecodeError`` -- not silent garbage."""
    payload = b"abc"
    bits = encode_frame(1, 2, 3, payload, payload_max_bytes=_PAYLOAD_MAX)
    corrupted = bits.copy()
    corrupted[-1] ^= 1  # flip the last CRC bit
    with pytest.raises(FrameDecodeError, match="CRC mismatch"):
        decode_frame(corrupted, payload_max_bytes=_PAYLOAD_MAX)


def test_payload_corruption_raises() -> None:
    """A flipped payload bit raises CRC mismatch (CRC covers payload + header)."""
    payload = b"abcdef"
    bits = encode_frame(1, 2, 3, payload, payload_max_bytes=_PAYLOAD_MAX)
    corrupted = bits.copy()
    # Flip a bit somewhere inside the payload region.
    payload_start = PREAMBLE_LEN_BITS + SYNC_WORD_LEN_BITS + HEADER_LEN_BITS
    corrupted[payload_start + 4] ^= 1
    with pytest.raises(FrameDecodeError, match="CRC mismatch"):
        decode_frame(corrupted, payload_max_bytes=_PAYLOAD_MAX)


def test_oversize_payload_on_encode_raises() -> None:
    """``len(payload) > payload_max_bytes`` raises before any bits are emitted."""
    with pytest.raises(FrameDecodeError, match="payload length"):
        encode_frame(1, 2, 3, b"x" * 65, payload_max_bytes=64)


def test_oversize_declared_length_on_decode_raises() -> None:
    """Decoded header claiming ``payload_len > payload_max_bytes`` raises.

    This is the defence against a forged or corrupted header trying
    to drain a giant buffer (or against a sender misconfigured with
    a larger frame_payload_max_bytes than the receiver). The receiver
    refuses, the comms loop drops the frame.
    """
    bits = encode_frame(1, 2, 3, b"a" * 32, payload_max_bytes=32)
    with pytest.raises(FrameDecodeError, match="exceeds payload_max_bytes"):
        decode_frame(bits, payload_max_bytes=16)


def test_bad_byte_field_raises() -> None:
    """Header byte fields must fit in [0, 255]."""
    with pytest.raises(FrameDecodeError, match="src_node_id"):
        encode_frame(256, 0, 0, b"", payload_max_bytes=_PAYLOAD_MAX)
    with pytest.raises(FrameDecodeError, match="dst_node_id"):
        encode_frame(0, -1, 0, b"", payload_max_bytes=_PAYLOAD_MAX)


def test_sync_word_one_bit_flip_recovered() -> None:
    """Hamming tolerance lets the decoder ride out one bit-flip in the sync word."""
    bits = encode_frame(1, 2, 3, b"sync", payload_max_bytes=_PAYLOAD_MAX)
    corrupted = bits.copy()
    sync_start = PREAMBLE_LEN_BITS
    corrupted[sync_start + 5] ^= 1
    decoded = decode_frame(corrupted, payload_max_bytes=_PAYLOAD_MAX)
    assert decoded.payload == b"sync"


def test_sync_word_three_bit_flips_refused() -> None:
    """Three flips exceed Hamming tolerance and refuse acquisition."""
    bits = encode_frame(1, 2, 3, b"sync", payload_max_bytes=_PAYLOAD_MAX)
    corrupted = bits.copy()
    sync_start = PREAMBLE_LEN_BITS
    for offset in (1, 5, 9):
        corrupted[sync_start + offset] ^= 1
    with pytest.raises(FrameDecodeError, match="sync-word"):
        decode_frame(corrupted, payload_max_bytes=_PAYLOAD_MAX)


def test_sync_word_value() -> None:
    """The sync word is the CCSDS Attached Sync Marker ``0x1ACFFC1D``."""
    assert SYNC_WORD_HEX == 0x1ACFFC1D


def test_truncated_input_raises() -> None:
    """A bit-stream too short to contain header + CRC raises."""
    bits = encode_frame(1, 2, 3, b"abc", payload_max_bytes=_PAYLOAD_MAX)
    truncated = bits[: PREAMBLE_LEN_BITS + SYNC_WORD_LEN_BITS + 8]
    with pytest.raises(FrameDecodeError, match="too short"):
        decode_frame(truncated, payload_max_bytes=_PAYLOAD_MAX)


def test_golden_match() -> None:
    """Freshly-computed encode matches the checked-in golden artefact."""
    npz = np.load(_GOLDEN)
    payload = bytes(npz["payload"])
    bits = encode_frame(
        src_node_id=int(npz["src_node_id"]),
        dst_node_id=int(npz["dst_node_id"]),
        sequence_no=int(npz["sequence_no"]),
        payload=payload,
        payload_max_bytes=int(npz["payload_max_bytes"]),
    )
    np.testing.assert_array_equal(bits, npz["bits"])
    # And the inverse decode still works on the golden bits.
    decoded = decode_frame(npz["bits"], payload_max_bytes=int(npz["payload_max_bytes"]))
    assert decoded.payload == payload
