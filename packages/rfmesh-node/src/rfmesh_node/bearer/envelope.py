"""Length-prefixed msgpack envelope -- the shared wire format.

Reused from the salvaged ``rfmesh/mesh/protocol.py`` (see
``SALVAGE_AUDIT.md`` Part 5). Three pieces:

1. A 4-byte big-endian unsigned length prefix.
2. A msgpack-encoded ``{"type": str, "payload": dict}`` envelope.
3. The payload is the ``model_dump(mode="json")`` of a Pydantic
   message from ``rfmesh-contracts``.

The codec is bearer-agnostic -- ``WifiBearer`` writes the prefix +
payload onto a UDP socket; ``LoraBearer`` does the same to a serial
stream; tests round-trip the bytes through memory.

Bandwidth guard: ``MAX_MESSAGE_BYTES`` (64 KiB) caps the envelope
size on the wire. Larger inputs raise ``EnvelopeTooLargeError`` --
the silent-truncate failure mode Invariant B3 forbids.

Why msgpack-style ``{type, payload}``?
--------------------------------------
The two message types (``BearingReport`` and ``NodeStatus``) share
a few field names but are *not* a sum type at the Pydantic layer.
A ``type`` discriminator on the envelope is the simplest way for
the receiver to dispatch decoding to the right model without a
union-discriminator dance in Pydantic. ``FixEvent`` is also
carried so the dashboard pub-sub channel can use the same codec.
"""

from __future__ import annotations

import struct
from typing import Any

import msgpack  # type: ignore[import-untyped]
from rfmesh_contracts import BearingReport, FixEvent, NodeStatus

# Envelope cap. 64 KiB is generous for any L2 pseudospectrum
# payload (the ``raw_pseudospectrum`` convention is 720 float32 =
# 2880 B; even with overhead, a wire envelope above 64 KiB is a
# bug, not a feature). Larger payloads are rejected as
# ``EnvelopeTooLargeError`` rather than truncated silently.
MAX_MESSAGE_BYTES: int = 65_536

# Length-prefix struct: big-endian uint32 (network order).
_LENGTH_STRUCT = struct.Struct(">I")
_LENGTH_PREFIX_BYTES: int = _LENGTH_STRUCT.size  # 4

# Envelope ``type`` discriminator strings. Pinned as module constants
# so a typo in a bearer would surface at import time, not as a silent
# decode failure on the wire.
_TYPE_BEARING_REPORT: str = "bearing_report"
_TYPE_NODE_STATUS: str = "node_status"
_TYPE_FIX_EVENT: str = "fix_event"


class EnvelopeError(Exception):
    """Base class for every envelope codec failure."""


class EnvelopeTooLargeError(EnvelopeError):
    """The encoded envelope exceeds ``MAX_MESSAGE_BYTES``."""


class EnvelopeDecodeError(EnvelopeError):
    """The envelope could not be decoded -- malformed prefix, payload, or type."""


def encode_envelope(message: BearingReport | NodeStatus | FixEvent) -> bytes:
    """Serialise a contract message to a length-prefixed msgpack envelope.

    Returns the full byte string (prefix + payload) ready for
    transport. Raises ``EnvelopeTooLargeError`` if the result would
    exceed ``MAX_MESSAGE_BYTES``.
    """
    if isinstance(message, BearingReport):
        msg_type = _TYPE_BEARING_REPORT
    elif isinstance(message, NodeStatus):
        msg_type = _TYPE_NODE_STATUS
    elif isinstance(message, FixEvent):
        msg_type = _TYPE_FIX_EVENT
    else:
        msg = (
            f"encode_envelope: unsupported message type {type(message).__name__}; "
            "expected BearingReport, NodeStatus, or FixEvent."
        )
        raise EnvelopeError(msg)

    payload = message.model_dump(mode="json")
    body = msgpack.packb({"type": msg_type, "payload": payload}, use_bin_type=True)
    assert isinstance(body, bytes)  # msgpack guarantees bytes from packb

    if len(body) > MAX_MESSAGE_BYTES:
        msg = (
            f"encode_envelope: payload of type {msg_type} is {len(body)} bytes "
            f"(> MAX_MESSAGE_BYTES={MAX_MESSAGE_BYTES}); refusing to send "
            "(would be a silent-truncate failure on a stream bearer)."
        )
        raise EnvelopeTooLargeError(msg)

    return _LENGTH_STRUCT.pack(len(body)) + body


def decode_envelope(data: bytes) -> BearingReport | NodeStatus | FixEvent:
    """Deserialise a length-prefixed msgpack envelope (raises on malformed input).

    Accepts the *full* prefix + body. For a UDP datagram this is the
    payload of the single packet. Stream bearers (LoRa, TCP) frame
    by the 4-byte prefix and call ``decode_envelope`` on each frame.
    """
    if len(data) < _LENGTH_PREFIX_BYTES:
        msg = (
            f"decode_envelope: input is {len(data)} bytes, shorter than the "
            f"{_LENGTH_PREFIX_BYTES}-byte length prefix."
        )
        raise EnvelopeDecodeError(msg)
    declared_length = _LENGTH_STRUCT.unpack(data[:_LENGTH_PREFIX_BYTES])[0]
    body = data[_LENGTH_PREFIX_BYTES : _LENGTH_PREFIX_BYTES + declared_length]
    if len(body) != declared_length:
        msg = (
            f"decode_envelope: length prefix claims {declared_length} bytes "
            f"but body is {len(body)} bytes."
        )
        raise EnvelopeDecodeError(msg)
    if declared_length > MAX_MESSAGE_BYTES:
        msg = (
            f"decode_envelope: declared length {declared_length} exceeds "
            f"MAX_MESSAGE_BYTES={MAX_MESSAGE_BYTES}."
        )
        raise EnvelopeTooLargeError(msg)

    try:
        envelope = msgpack.unpackb(body, raw=False)
    except Exception as exc:
        msg = f"decode_envelope: msgpack unpack failed: {exc}"
        raise EnvelopeDecodeError(msg) from exc

    if not isinstance(envelope, dict):
        msg = f"decode_envelope: expected an envelope dict, got {type(envelope).__name__}."
        raise EnvelopeDecodeError(msg)
    msg_type = envelope.get("type")
    payload = envelope.get("payload")
    if not isinstance(msg_type, str):
        msg = f"decode_envelope: envelope missing 'type' (got {msg_type!r})."
        raise EnvelopeDecodeError(msg)
    if not isinstance(payload, dict):
        msg = f"decode_envelope: envelope 'payload' must be a dict (got {type(payload).__name__})."
        raise EnvelopeDecodeError(msg)

    typed_payload: dict[str, Any] = payload
    try:
        if msg_type == _TYPE_BEARING_REPORT:
            return BearingReport.model_validate(typed_payload)
        if msg_type == _TYPE_NODE_STATUS:
            return NodeStatus.model_validate(typed_payload)
        if msg_type == _TYPE_FIX_EVENT:
            return FixEvent.model_validate(typed_payload)
    except Exception as exc:
        msg = f"decode_envelope: pydantic validation of type {msg_type!r} failed: {exc}"
        raise EnvelopeDecodeError(msg) from exc

    msg = (
        f"decode_envelope: unknown envelope type {msg_type!r} "
        f"(expected one of {_TYPE_BEARING_REPORT!r}, {_TYPE_NODE_STATUS!r}, "
        f"{_TYPE_FIX_EVENT!r})."
    )
    raise EnvelopeDecodeError(msg)


def strip_pseudospectrum(message: BearingReport) -> BearingReport:
    """Return a copy of ``message`` with ``raw_pseudospectrum`` cleared.

    Used by ``LoraBearer`` to honour the bandwidth invariant
    documented in INTERFACES.md §3: the L2 pseudospectrum debug
    payload rides on Wi-Fi only; LoRa drops it. Pydantic models are
    frozen so we ``model_copy(update=...)``.
    """
    if message.raw_pseudospectrum is None:
        return message
    return message.model_copy(update={"raw_pseudospectrum": None})


__all__ = [
    "MAX_MESSAGE_BYTES",
    "EnvelopeDecodeError",
    "EnvelopeError",
    "EnvelopeTooLargeError",
    "decode_envelope",
    "encode_envelope",
    "strip_pseudospectrum",
]
