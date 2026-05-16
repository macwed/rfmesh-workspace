"""rfmesh-sdr.io -- on-disk IQ capture format (uint8 interleaved + sidecar).

Pure Python: no hardware drivers, no subprocess, no DSP coupling. The
public surface is three classes:

* ``IQMetadata``  -- Pydantic sidecar model for an IQ capture.
* ``IQReader``    -- read a capture back as ``complex64``, with optional
  sidecar metadata.
* ``IQRecorder``  -- write ``complex64`` chunks to disk in uint8 IQ
  format, with a paired sidecar.

The hardware-side ``rtl_sdr``-subprocess recorder is WS-A-005's scope.
"""

from __future__ import annotations

from .metadata import IQMetadata
from .reader import IQReader
from .recorder import IQRecorder

__all__ = [
    "IQMetadata",
    "IQReader",
    "IQRecorder",
]
