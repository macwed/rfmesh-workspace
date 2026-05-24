"""DSSS frame format: preamble + sync word + header + payload + CRC.

Wire format (pre-spread, BPSK bits):

::

    | preamble (Np bits)
    | sync word (Nsw bits)
    | header (Nh bits)
    | payload (variable, <= CommsConfig.frame_payload_max_bytes)
    | CRC-16 (16 bits) |

* **Preamble**: 64 bits, derived from a length-63 m-sequence
  (Fibonacci LFSR ``x**6 + x + 1``, seed 1) padded with one zero
  bit. The m-sequence's two-level cyclic autocorrelation property
  carries through the BPSK modulation and the data-PN spread (which
  itself has the same property), giving the matched filter a sharp
  peak with sidelobes ~1/64 of the peak. An earlier draft used an
  alternating ``0101...`` preamble; that pattern has a near-peak
  autocorrelation sidelobe at +/- 1023 chips (one data-symbol shift),
  which under chip-level noise tips into the wrong "peak" ~60 % of
  the time and gives an unworkable acquisition probability. The
  m-sequence preamble fixes that; see the
  ``test_frame_acquisition_probability_at_low_snr`` regression test.
* **Sync word**: a fixed 32-bit distinctive pattern (CCSDS Attached
  Sync Marker, ``0x1ACFFC1D``). Bit-rotationally distinctive --
  small Hamming distance to any rotation of itself, which is what
  lets the receiver disambiguate true sync from a multipath echo of
  the preamble. The receiver tolerates a configurable small Hamming
  distance (default: 2 bit-flips) so a single corrupted chip-block
  does not throw the frame.
* **Header** (4 bytes = 32 bits, big-endian byte order):
  * ``src_node_id`` (1 byte): producing-node identifier, mesh assumes
    <= 255 nodes;
  * ``dst_node_id`` (1 byte): destination node, or ``0xFF`` for
    broadcast;
  * ``sequence_no`` (1 byte): wraps every 256 frames per ``(src, dst)``
    pair; sufficient given the v1.3.0 ~10 kbit/s rate;
  * ``payload_len_bytes`` (1 byte): bounds the despreader's
    integration window.
* **Payload**: opaque to this module. The node-layer comms loop
  decides what goes here (application bytes, retry markers, peer
  status pings). Bounded by ``CommsConfig.frame_payload_max_bytes``;
  oversize input raises in ``encode_frame``, oversize decoded
  payload raises in ``decode_frame``.
* **CRC-16**: CRC-16/CCITT-FALSE (polynomial ``0x1021``, init
  ``0xFFFF``, no input/output reflection, no final XOR). Same
  polynomial as the servo wire codec. The implementation is
  **inlined here, not imported** from ``rfmesh-servo`` because
  WD-1 forbids cross-sibling imports; the two implementations are
  identical byte-for-byte and the test suites in both packages
  pin the same check value (``CRC(b"123456789") == 0x29B1``) so
  drift surfaces immediately on either side.

WHY AN INTERNAL CRC RATHER THAN ``zlib.crc32``

* CRC-16 is 16 bits over the wire; CRC-32 doubles the overhead per
  frame and the bitrate is precious.
* CRC-16/CCITT-FALSE is what the firmware already uses for the
  servo channel; engineers reading both side-by-side see the same
  algorithm.

BIT-ORDER CONVENTION

Within each byte: MSB-first. Big-endian byte order for integer
fields. ``encode_frame`` and ``decode_frame`` are inverses;
``decode_frame(encode_frame(...))`` round-trips exactly. The bit
arrays are ``np.int8`` of ``{0, 1}`` so they feed straight into
``bpsk_modulate`` -- no implicit unit conversion at the framing /
modulation boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np

from .exceptions import FrameDecodeError
from .pn_sequence import generate_m_sequence

#: Preamble length (bits). 64 bits = length-63 m-sequence
#: (``x**6 + x + 1``, seed 1) + one zero pad bit. The m-sequence
#: gives the matched filter clean two-level autocorrelation
#: side-lobes (~1/64 of the peak), unlike the alternating
#: ``0101...`` pattern an earlier draft used (which had a near-peak
#: sidelobe at +/- 1023 chips and an unworkable acquisition
#: probability under noise).
PREAMBLE_LEN_BITS: Final[int] = 64

#: Tap tuple defining the length-63 preamble m-sequence
#: (``x**6 + x + 1`` in the ``CommsConfig.lfsr_taps`` polynomial-power
#: convention -- ``pn_sequence._validate_lfsr_params`` enforces it).
_PREAMBLE_TAPS: Final[tuple[int, int]] = (6, 1)

#: Initial register state for the preamble m-sequence. Any non-zero
#: seed produces a cyclic shift of the same sequence; the value here
#: is the mesh-wide convention so receivers and senders agree on the
#: exact 63-chip pattern without additional configuration.
_PREAMBLE_SEED: Final[int] = 1

#: Sync word: CCSDS Attached Sync Marker (ASM). 32 bits, distinctive
#: under bit rotation, widely used in space-link telemetry for the
#: same reason it works here.
SYNC_WORD_HEX: Final[int] = 0x1ACFFC1D
SYNC_WORD_LEN_BITS: Final[int] = 32

#: Header: src + dst + sequence_no + payload_len_bytes (1 byte each).
HEADER_LEN_BYTES: Final[int] = 4
HEADER_LEN_BITS: Final[int] = HEADER_LEN_BYTES * 8

#: CRC-16 over header + payload.
CRC_LEN_BITS: Final[int] = 16
CRC_POLY: Final[int] = 0x1021
CRC_INIT: Final[int] = 0xFFFF

#: Tolerance: maximum bit-flips allowed in the sync word during
#: detection. Two single-bit errors will not throw the frame; three
#: will -- conservative because a wrong sync sends the decoder into
#: garbage with no second chance.
SYNC_HAMMING_TOLERANCE: Final[int] = 2

#: Reserved ``dst_node_id`` value meaning "broadcast to every node".
BROADCAST_DST: Final[int] = 0xFF

#: Fixed overhead = preamble + sync + header + CRC (bits, no payload).
FIXED_OVERHEAD_BITS: Final[int] = (
    PREAMBLE_LEN_BITS + SYNC_WORD_LEN_BITS + HEADER_LEN_BITS + CRC_LEN_BITS
)

#: Upper bound on any header byte field (unsigned 8-bit).
_MAX_BYTE_VALUE: Final[int] = 0xFF


@dataclass(frozen=True)
class FrameContents:
    """Decoded frame header + payload, returned by ``decode_frame``."""

    src_node_id: int
    dst_node_id: int
    sequence_no: int
    payload: bytes


def _byte_field_check(value: int, name: str) -> None:
    if not 0 <= value <= _MAX_BYTE_VALUE:
        msg = f"{name} must fit in one unsigned byte [0, 255] (got {value})."
        raise FrameDecodeError(msg)


def _crc16_ccitt_false(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no refl, no xorout.

    Check value: ``_crc16_ccitt_false(b"123456789") == 0x29B1``
    (pinned by ``test_framing.test_crc16_check_value``). Same
    algorithm as ``rfmesh-servo``; the two implementations are not
    shared (WD-1 forbids cross-sibling imports) -- they are kept
    drift-free by the matching check-value test in each package.
    """
    crc = CRC_INIT
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _bytes_to_bits(data: bytes) -> np.ndarray:
    """Unpack bytes to MSB-first ``int8`` bit array."""
    arr = np.frombuffer(data, dtype=np.uint8)
    bits = np.unpackbits(arr, bitorder="big")
    return bits.astype(np.int8)


def _bits_to_bytes(bits: np.ndarray) -> bytes:
    """Pack MSB-first ``int8`` bit array to bytes. Length must be a multiple of 8."""
    if bits.size % 8 != 0:
        msg = f"bits length must be a multiple of 8 (got {bits.size})."
        raise FrameDecodeError(msg)
    packed = np.packbits(bits.astype(np.uint8), bitorder="big")
    return bytes(packed)


def _preamble_bits() -> np.ndarray:
    """Generate the 64-bit preamble: length-63 m-sequence + 1 zero pad.

    Mapping ``+1 -> 0``, ``-1 -> 1`` so the BPSK modulator
    (``bpsk_modulate``, ``0 -> +1`` / ``1 -> -1``) reproduces the
    raw m-sequence symbols on the wire -- the spread preamble is
    then ``[+/- PN]`` per the m-sequence pattern, and the matched
    filter's two-level autocorrelation property holds at the
    chip level.
    """
    m_seq = generate_m_sequence(6, _PREAMBLE_TAPS, _PREAMBLE_SEED)  # 63 chips, +/- 1
    bits63 = np.where(m_seq == 1, np.int8(0), np.int8(1))
    pad = np.zeros(PREAMBLE_LEN_BITS - bits63.size, dtype=np.int8)
    return np.concatenate([bits63, pad]).astype(np.int8)


def _sync_word_bits() -> np.ndarray:
    """Encode the 32-bit sync word as MSB-first bits."""
    sync_bytes = SYNC_WORD_HEX.to_bytes(4, byteorder="big")
    return _bytes_to_bits(sync_bytes)


def encode_frame(
    src_node_id: int,
    dst_node_id: int,
    sequence_no: int,
    payload: bytes,
    payload_max_bytes: int,
) -> np.ndarray:
    """Build one DSSS frame as a 1-D ``int8`` bit array (ready for ``bpsk_modulate``).

    Parameters
    ----------
    src_node_id, dst_node_id, sequence_no:
        Each must fit in one unsigned byte ``[0, 255]``;
        ``dst_node_id == BROADCAST_DST`` (255) is the broadcast
        sentinel.
    payload:
        Application bytes. ``len(payload)`` must be in
        ``[0, payload_max_bytes]``; oversize input raises
        ``FrameDecodeError``.
    payload_max_bytes:
        Per-link bound. Sourced from
        ``CommsConfig.frame_payload_max_bytes`` at the comms-loop
        layer; carried as an explicit argument here so this module
        stays decoupled from the config (importing CommsConfig is
        fine, but threading it through every call would couple the
        DSP layer to the orchestration layer for no benefit).

    Returns
    -------
    ``np.ndarray`` of ``dtype=int8``, shape ``(N,)``, with elements
    in ``{0, 1}``. ``N = FIXED_OVERHEAD_BITS + 8 * len(payload)``.
    """
    _byte_field_check(src_node_id, "src_node_id")
    _byte_field_check(dst_node_id, "dst_node_id")
    _byte_field_check(sequence_no, "sequence_no")
    if not 0 <= len(payload) <= payload_max_bytes:
        msg = (
            f"payload length must be in [0, {payload_max_bytes}] "
            f"(got {len(payload)})."
        )
        raise FrameDecodeError(msg)
    if payload_max_bytes <= 0:
        msg = f"payload_max_bytes must be positive (got {payload_max_bytes})."
        raise FrameDecodeError(msg)

    header_bytes = bytes(
        [src_node_id, dst_node_id, sequence_no, len(payload)],
    )
    crc = _crc16_ccitt_false(header_bytes + payload)
    crc_bytes = crc.to_bytes(2, byteorder="big")

    parts = [
        _preamble_bits(),
        _sync_word_bits(),
        _bytes_to_bits(header_bytes),
        _bytes_to_bits(payload),
        _bytes_to_bits(crc_bytes),
    ]
    return np.concatenate(parts).astype(np.int8)


def _hamming_distance(a: np.ndarray, b: np.ndarray) -> int:
    """Number of bit positions where ``a`` and ``b`` differ."""
    if a.size != b.size:
        msg = f"hamming distance needs equal-length arrays (got {a.size} vs {b.size})."
        raise FrameDecodeError(msg)
    return int(np.count_nonzero(a != b))


def find_sync_word(bits: np.ndarray, search_start: int = 0) -> int:
    """Scan ``bits`` for the sync word with Hamming tolerance.

    Returns the bit index *after* the sync word (i.e. the start of
    the header). Raises ``FrameDecodeError`` if no candidate within
    ``SYNC_HAMMING_TOLERANCE`` is found.

    Parameters
    ----------
    bits:
        ``int8`` bit array to search.
    search_start:
        Earliest index to consider as the start of the sync word.
        Typically passed by the comms loop as the end of the
        preamble (i.e. start of the sync window).
    """
    sync = _sync_word_bits()
    if bits.size - search_start < sync.size:
        msg = (
            f"bit stream too short to contain sync word starting at "
            f"{search_start} (need {sync.size}, have {bits.size - search_start})."
        )
        raise FrameDecodeError(msg)
    best_start = -1
    best_dist = SYNC_HAMMING_TOLERANCE + 1
    for i in range(search_start, bits.size - sync.size + 1):
        window = bits[i : i + sync.size]
        dist = _hamming_distance(window, sync)
        if dist < best_dist:
            best_dist = dist
            best_start = i
            if dist == 0:
                break
    if best_start < 0 or best_dist > SYNC_HAMMING_TOLERANCE:
        msg = (
            f"no sync-word match within Hamming tolerance "
            f"{SYNC_HAMMING_TOLERANCE} bits in window."
        )
        raise FrameDecodeError(msg)
    return best_start + sync.size


def decode_frame(bits: np.ndarray, payload_max_bytes: int) -> FrameContents:
    """Inverse of ``encode_frame``: parse bits back to ``FrameContents``.

    Assumes the input starts at the preamble (i.e. no chip-level
    alignment search; the comms loop hands a clean post-acquisition
    bit-stream in). Raises ``FrameDecodeError`` on any structural
    invalidity:

    * CRC mismatch (corrupted header or payload);
    * sync-word not found within Hamming tolerance;
    * declared payload length exceeds ``payload_max_bytes`` (defends
      the receiver against a forged or corrupted header trying to
      drain a giant buffer);
    * bit-stream too short to contain header + declared payload +
      CRC after the sync word.
    """
    if bits.ndim != 1:
        msg = f"bits must be 1-D (got shape {bits.shape})."
        raise FrameDecodeError(msg)
    # Skip preamble; tolerate small misalignment by starting sync
    # search a few bits before/after the nominal preamble boundary.
    # In v1.3.0 the upstream layer (correlation) hands us a tightly
    # aligned stream; the +/-4-bit window absorbs the small jitter
    # the matched filter leaves behind without re-running acquisition.
    search_start = max(0, PREAMBLE_LEN_BITS - 4)
    header_start = find_sync_word(bits, search_start=search_start)
    if bits.size < header_start + HEADER_LEN_BITS + CRC_LEN_BITS:
        msg = "bit stream too short to contain header + CRC after sync."
        raise FrameDecodeError(msg)
    header_bits = bits[header_start : header_start + HEADER_LEN_BITS]
    header_bytes = _bits_to_bytes(header_bits)
    src_node_id, dst_node_id, sequence_no, declared_len = header_bytes
    if declared_len > payload_max_bytes:
        msg = (
            f"declared payload length {declared_len} exceeds "
            f"payload_max_bytes {payload_max_bytes}."
        )
        raise FrameDecodeError(msg)
    payload_start = header_start + HEADER_LEN_BITS
    payload_end = payload_start + declared_len * 8
    crc_end = payload_end + CRC_LEN_BITS
    if bits.size < crc_end:
        msg = (
            f"bit stream truncated: declared payload {declared_len} B "
            f"+ CRC needs {crc_end} bits, have {bits.size}."
        )
        raise FrameDecodeError(msg)
    payload_bits = bits[payload_start:payload_end]
    payload = _bits_to_bytes(payload_bits) if declared_len > 0 else b""
    crc_bits = bits[payload_end:crc_end]
    received_crc = int.from_bytes(_bits_to_bytes(crc_bits), byteorder="big")
    expected_crc = _crc16_ccitt_false(header_bytes + payload)
    if received_crc != expected_crc:
        msg = (
            f"CRC mismatch: received 0x{received_crc:04X}, "
            f"expected 0x{expected_crc:04X}."
        )
        raise FrameDecodeError(msg)
    return FrameContents(
        src_node_id=int(src_node_id),
        dst_node_id=int(dst_node_id),
        sequence_no=int(sequence_no),
        payload=payload,
    )
