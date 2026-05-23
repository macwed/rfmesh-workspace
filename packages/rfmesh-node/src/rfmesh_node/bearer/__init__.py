"""Bearer implementations for ``rfmesh-node``.

Three concrete bearers, all structural-conformers of
``rfmesh_contracts.Bearer``:

* ``WifiBearer`` -- UDP + length-prefixed msgpack envelope.
* ``LoraBearer`` -- serial-port LoRa, drops ``raw_pseudospectrum``
  to fit the bandwidth budget.
* ``BothBearer`` -- composite; sends on both, de-duplicates received
  messages by ``(node_id, t_unix_ns)``.

The envelope codec is in ``envelope.py`` -- one place that knows
the wire format, used by every bearer.
"""

from __future__ import annotations

from .both import BothBearer
from .envelope import (
    MAX_MESSAGE_BYTES,
    EnvelopeDecodeError,
    EnvelopeError,
    EnvelopeTooLargeError,
    decode_envelope,
    encode_envelope,
)
from .http import HttpBearer
from .lora import LoraBearer
from .wifi import WifiBearer

__all__ = [
    "MAX_MESSAGE_BYTES",
    "BothBearer",
    "EnvelopeDecodeError",
    "EnvelopeError",
    "EnvelopeTooLargeError",
    "HttpBearer",
    "LoraBearer",
    "WifiBearer",
    "decode_envelope",
    "encode_envelope",
]
