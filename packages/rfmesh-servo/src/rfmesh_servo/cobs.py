"""COBS (Consistent Overhead Byte Stuffing) for the servo UART wire protocol.

Reference: Cheshire & Baker, "Consistent Overhead Byte Stuffing" (1999).

Encodes an arbitrary byte string into a stream that contains no ``0x00``
bytes, allowing a single ``0x00`` to serve as an unambiguous frame
delimiter. The encoder here intentionally does NOT append the trailing
delimiter — the framing layer in :mod:`rfmesh_servo.protocol` handles
that, so this module's output and input are pure encoded payloads.
"""

from __future__ import annotations

from rfmesh_servo.exceptions import FrameError


def cobs_encode(data: bytes) -> bytes:
    """Encode ``data`` with COBS. Output contains no ``0x00`` bytes.

    Args:
        data: Arbitrary bytes (may contain ``0x00``).

    Returns:
        COBS-encoded bytes. The caller is responsible for appending the
        ``0x00`` frame terminator if desired.
    """
    out = bytearray()
    code_index = 0
    out.append(0)
    code = 1
    for byte in data:
        if byte == 0:
            out[code_index] = code
            code_index = len(out)
            out.append(0)
            code = 1
        else:
            out.append(byte)
            code += 1
            if code == 0xFF:
                out[code_index] = code
                code_index = len(out)
                out.append(0)
                code = 1
    out[code_index] = code
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    """Decode COBS-encoded ``data``. Input MUST NOT contain a ``0x00`` terminator.

    Args:
        data: COBS-encoded payload (no ``0x00`` bytes; the framing layer
            strips the terminator before calling this function).

    Returns:
        The original pre-encoding bytes.

    Raises:
        FrameError: If the encoded data is malformed (zero code byte,
            truncated block, or stray ``0x00``).
    """
    if not data:
        raise FrameError("empty COBS payload")
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        code = data[i]
        if code == 0:
            raise FrameError("unexpected 0x00 inside COBS payload")
        block_end = i + code
        if block_end > n:
            raise FrameError(f"COBS block at offset {i} (code={code}) overruns buffer")
        out.extend(data[i + 1 : block_end])
        i = block_end
        if code < 0xFF and i < n:
            out.append(0)
    return bytes(out)


__all__ = ["cobs_decode", "cobs_encode"]
