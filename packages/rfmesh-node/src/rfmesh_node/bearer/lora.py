"""``LoraBearer`` -- compressed-essential-fields bearer over a serial LoRa link.

LoRa is the EW-resilient fallback path: low bandwidth, robust against
Wi-Fi jamming. Bandwidth is so constrained (typically < 1 kB/s payload
after framing) that the ``raw_pseudospectrum`` debug payload on
``BearingReport`` is *dropped* before send -- the contract documents
this in INTERFACES.md §3.

v1.0 status
-----------
The serial LoRa hardware (an SX1276 module behind an Arduino-ESP32
bridge -- ``lora_beacon_spec.md`` salvage) is on Workstream A's bench
plan but **not yet built**. This bearer therefore ships with a
``loopback=True`` test mode that round-trips through an in-memory
deque instead of a serial port. Real-hardware integration is a future
ticket; the wire format (the msgpack envelope from ``envelope.py``)
is settled here so the future hardware ticket only swaps the
byte-pump.

Synchronous ``Bearer`` Protocol (matches contract).
"""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING

from rfmesh_contracts import BearingReport, NodeStatus

from .envelope import EnvelopeDecodeError, decode_envelope, encode_envelope, strip_pseudospectrum

if TYPE_CHECKING:
    from collections.abc import Sequence

_LOG = logging.getLogger(__name__)


class LoraBearer:
    """Serial-port LoRa bearer (loopback mode in v1.0)."""

    def __init__(
        self,
        serial_port: str,
        baud: int = 9600,
        *,
        loopback: bool = False,
    ) -> None:
        """Configure the LoRa bearer.

        Args:
            serial_port: e.g. ``"/dev/ttyUSB0"``. Required by
                ``BearerConfig`` validation even in loopback mode --
                so the config remains valid for the future hardware
                ticket.
            baud: Serial baud (placeholder for real hardware).
            loopback: Test mode -- the bearer does not open a serial
                device; ``send_*`` push bytes onto an in-memory deque
                this same instance drains via ``receive``. Default
                False.
        """
        if not serial_port:
            msg = "LoraBearer: serial_port must be non-empty."
            raise ValueError(msg)
        if baud <= 0:
            msg = f"LoraBearer: baud must be > 0 (got {baud})."
            raise ValueError(msg)
        self._serial_port = serial_port
        self._baud = baud
        self._loopback = loopback
        self._loopback_inbox: deque[BearingReport | NodeStatus] = deque()
        self._last_envelope_bytes: int = 0
        self._closed = False

    def send_bearing(self, report: BearingReport) -> None:
        """Encode + drop ``raw_pseudospectrum`` + transmit."""
        if self._closed:
            msg = "LoraBearer.send_bearing: bearer is closed."
            raise RuntimeError(msg)
        compact = strip_pseudospectrum(report)
        envelope = encode_envelope(compact)
        self._last_envelope_bytes = len(envelope)
        self._write_bytes(envelope)

    def send_status(self, status: NodeStatus) -> None:
        """Encode + transmit a heartbeat."""
        if self._closed:
            msg = "LoraBearer.send_status: bearer is closed."
            raise RuntimeError(msg)
        envelope = encode_envelope(status)
        self._last_envelope_bytes = len(envelope)
        self._write_bytes(envelope)

    def _write_bytes(self, data: bytes) -> None:
        if not self._loopback:
            msg = (
                "LoraBearer: real-serial transport not implemented in v1.0 "
                "(LoRa hardware on WS-A bench plan). Use loopback=True for "
                "tests; configure WIFI bearer for live use."
            )
            raise NotImplementedError(msg)
        try:
            message = decode_envelope(data)
        except EnvelopeDecodeError as exc:
            _LOG.warning("LoraBearer loopback: malformed envelope: %s", exc)
            return
        if isinstance(message, BearingReport | NodeStatus):
            self._loopback_inbox.append(message)

    @property
    def last_envelope_bytes(self) -> int:
        """Size of the last envelope sent (test introspection)."""
        return self._last_envelope_bytes

    def receive(self) -> Sequence[BearingReport | NodeStatus]:
        """Drain the inbox (loopback only in v1.0)."""
        if not self._loopback_inbox:
            return ()
        out = tuple(self._loopback_inbox)
        self._loopback_inbox.clear()
        return out

    def close(self) -> None:
        """Release the serial handle (no-op in loopback). Idempotent."""
        self._closed = True
        self._loopback_inbox.clear()


__all__ = ["LoraBearer"]
