"""``IQMetadata`` -- the sidecar Pydantic model for an IQ capture file.

A capture on disk is two files with the same stem:

* ``<stem>.iq``       -- the uint8-interleaved IQ payload (rtl_sdr native).
* ``<stem>.iq.json``  -- the ``IQMetadata`` sidecar, serialised via Pydantic.

The sidecar is *optional* at read time: a legacy capture file without a
sidecar still reads back as ``complex64`` -- only the metadata field on
the reader is ``None``. The recorder, however, always writes the
sidecar; an empty sidecar at write time would itself be a silent
fallback (Invariant 4) -- the operator either has metadata or has
nothing.

VERSIONING -- AND THE DELIBERATE GAP WITH ``rfmesh_contracts``
--------------------------------------------------------------
``IQMetadata.schema_version`` is the *capture file format* version, not
the *contract schema* version. They evolve independently:

* ``rfmesh_contracts.SCHEMA_VERSION`` (currently 1.0.0) governs the
  on-the-wire ``BearingReport`` / ``FixEvent`` / ``NodeStatus`` types
  exchanged between workstreams. A bump invalidates every workstream's
  mypy checks until it updates.

* ``IQMetadata.schema_version`` governs the on-disk ``.iq.json``
  sidecar. A capture made today should still be readable by a future
  ``rfmesh-sdr`` even after the contracts have moved. A bump here is
  about ``.iq`` payload format change (e.g. adding int16 capture
  support), not about contracts.

Conflating the two would couple the capture file format to every
contract change, which is the opposite of what either invariant
intends. ``WS-A-004`` flags this explicitly in its stop conditions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Locked at 1.0.0 for the v1 file format. A future int16 / float32 capture
# format bumps this; readers branch on it. The Literal makes a stale or
# typo'd version a Pydantic validation error at load time, not a silent
# misinterpretation of bytes.
_IQ_METADATA_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"

# The only payload format this ticket supports. HackRF's int16 stream,
# bladeRF / Pluto+ float32 coherent dumps, etc. are out-of-scope per the
# WS-A-004 "Out of scope" list and will land with their respective
# hardware-backend tickets.
_IQ_PAYLOAD_FORMAT_RTL_SDR_UINT8: Literal["rtl_sdr_uint8"] = "rtl_sdr_uint8"


class IQMetadata(BaseModel):
    """Frozen Pydantic sidecar for an rtl_sdr-style uint8 IQ capture.

    ``ConfigDict(frozen=True, extra="forbid")`` mirrors the discipline
    of ``rfmesh_contracts`` -- a typo'd field in a hand-edited sidecar
    is rejected at load time, not silently absorbed.

    Required fields cover what a downstream consumer must know to
    interpret the bytes: how fast they were sampled, what they were
    tuned to, when capture began, how many samples were written, and
    which payload format the file uses. Optional fields ride along for
    provenance: who captured it, which serial, what gain, free-form
    notes. ``None`` means "not recorded"; the schema does not invent
    defaults.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0.0"] = _IQ_METADATA_SCHEMA_VERSION

    sample_rate_hz: float = Field(gt=0.0)
    center_freq_hz: float = Field(gt=0.0)
    n_samples: int = Field(gt=0)
    start_time_utc: datetime
    format: Literal["rtl_sdr_uint8"] = _IQ_PAYLOAD_FORMAT_RTL_SDR_UINT8

    source: str | None = None
    serial: str | None = None
    gain_db: float | None = None
    notes: str | None = None

    @field_validator("start_time_utc")
    @classmethod
    def _require_tzaware_utc(cls, v: datetime) -> datetime:
        """Reject naive datetimes; require an explicit UTC offset.

        A naive timestamp at read time is ambiguous (local time? UTC?
        unspecified?); making the schema reject one keeps every capture
        comparable across deployment sites. The producer side (the
        ``IQRecorder``) constructs ``datetime.now(UTC)`` so this never
        fires for in-pipeline captures -- it is here to catch
        hand-authored or migrated sidecars.
        """
        if v.tzinfo is None or v.utcoffset() is None:
            msg = (
                "IQMetadata.start_time_utc must be timezone-aware (UTC); "
                "got a naive datetime. Use datetime.now(datetime.UTC) at the producer."
            )
            raise ValueError(msg)
        return v

    @property
    def expected_payload_size_bytes(self) -> int:
        """Byte count the ``.iq`` payload must have to match this sidecar.

        Used by ``IQReader`` to detect a truncated capture (a recording
        cut short by a crash or a disk-full) before it tries to interpret
        the truncated bytes as samples.
        """
        # 2 bytes per sample (I, Q) -- matches BYTES_PER_SAMPLE in io.constants;
        # repeated literally here so the metadata module does not have to
        # import siblings to compute a self-describing property.
        return self.n_samples * 2
