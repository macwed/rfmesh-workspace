"""CRC-16/CCITT-FALSE for the servo UART wire protocol.

Polynomial ``0x1021``, initial value ``0xFFFF``, no input/output reflection,
no final XOR. Defined in §2.2 of ``docs/wire-protocols/servo_uart_v1.md``.
"""

from __future__ import annotations

_POLY = 0x1021
_INIT = 0xFFFF
_MASK = 0xFFFF


def crc16_ccitt_false(data: bytes) -> int:
    """Return the CRC-16/CCITT-FALSE of ``data`` as a 16-bit unsigned int.

    Args:
        data: Bytes to checksum.

    Returns:
        The CRC value in the range ``[0, 0xFFFF]``.
    """
    crc = _INIT
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ _POLY) & _MASK if crc & 0x8000 else (crc << 1) & _MASK
    return crc


__all__ = ["crc16_ccitt_false"]
