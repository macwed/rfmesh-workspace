"""``IQRecorder`` -- write ``complex64`` IQ chunks to an rtl_sdr-style ``.iq`` file.

The recorder is the produce side of the WS-A-004 file I/O foundation:
something (the simulator, bench hardware later) hands ``IQRecorder``
``complex64`` chunks; ``IQRecorder`` quantizes them into the uint8
interleaved on-disk format and emits a paired ``IQMetadata`` sidecar
on close. **No hardware is invoked from this module.** The salvaged
recorder was an ``rtl_sdr`` subprocess wrapper; that subprocess path
lives one ticket downstream (WS-A-005) where it has a hardware
runtime to test against. Here, the recorder is the architecture-neutral
piece: pure Python, pure NumPy, pure stdlib.

Path convention
---------------
The constructor accepts the *capture parameters* (sample rate, centre
freq, optional provenance fields); ``start(path)`` accepts the
*destination*. Given a destination ``path``:

* If ``path`` already ends in ``.iq``, the payload is written to
  ``path`` and the sidecar to ``path + ".json"``.
* Otherwise the payload is written to ``path + ".iq"`` and the sidecar
  to ``path + ".iq.json"``.

This means a caller writing ``rec.start(tmp_path / "capture")``
produces ``tmp_path / "capture.iq"`` and ``tmp_path / "capture.iq.json"``
-- the binding acceptance criterion 1.b. A caller already holding a
fully-qualified ``capture.iq`` path can pass that too.

Lifecycle
---------
::

    rec = IQRecorder(sample_rate_hz=2.048e6, center_freq_hz=915e6,
                     source="synthetic")
    rec.start(tmp_path / "capture")
    rec.write(chunk)           # complex64 -> uint8 -> file
    rec.write(another_chunk)   # appends, total n_samples grows
    rec.close()                # flushes file, writes sidecar with final n_samples

The context-manager form is equivalent::

    with IQRecorder(sample_rate_hz=..., ...).start(tmp_path / "capture") as rec:
        rec.write(chunk)
    # close() runs on __exit__, even if write() raised
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import IO

import numpy as np

from .conversion import complex64_to_uint8_pair
from .metadata import IQMetadata


def _resolve_payload_and_sidecar(path: Path | str) -> tuple[Path, Path]:
    """Return ``(payload_path, sidecar_path)`` given a user-supplied destination.

    The pair always satisfies ``payload_path.name + ".json" == sidecar_path.name``
    so the reader's auto-discovery rule is symmetric to the writer's rule.
    """
    p = Path(path)
    payload = p if p.suffix == ".iq" else p.with_name(p.name + ".iq")
    sidecar = payload.with_name(payload.name + ".json")
    return payload, sidecar


class IQRecorder:
    """Writes uint8-interleaved IQ + a sidecar; not safe for concurrent writes.

    A single writer at a time. ``write()`` calls are serial; the producer
    is a single thread (the test fixture, or a node-runtime capture task).
    Multi-writer safety is out of scope -- if a future use case wants
    that, wrap the recorder in a Queue + drain task and document the
    overhead.
    """

    def __init__(
        self,
        sample_rate_hz: float,
        center_freq_hz: float,
        source: str | None = None,
        serial: str | None = None,
        gain_db: float | None = None,
        notes: str | None = None,
    ) -> None:
        """Configure a recorder; ``start(path)`` opens the destination.

        Args:
            sample_rate_hz: Sample rate of the IQ stream, Hz. Stored on
                the sidecar; pinned at config time so the recorder does
                not silently start writing at a rate that disagrees with
                metadata.
            center_freq_hz: Centre frequency of the capture, Hz.
            source: Free-form provenance identifier (e.g.
                ``"synthetic_receiver"``, ``"rtlsdr_0"``). Optional.
            serial: Device serial, when applicable.
            gain_db: Gain setting at capture time, dB.
            notes: Free-form notes, e.g. ``"trench-demo dry run 3"``.
        """
        if sample_rate_hz <= 0.0:
            msg = f"IQRecorder: sample_rate_hz must be > 0 (got {sample_rate_hz})."
            raise ValueError(msg)
        if center_freq_hz <= 0.0:
            msg = f"IQRecorder: center_freq_hz must be > 0 (got {center_freq_hz})."
            raise ValueError(msg)
        self._sample_rate_hz = sample_rate_hz
        self._center_freq_hz = center_freq_hz
        self._source = source
        self._serial = serial
        self._gain_db = gain_db
        self._notes = notes

        # Populated by start(); ``None`` until then. write/close before
        # start() is a lifecycle bug surfaced as RuntimeError.
        self._payload_path: Path | None = None
        self._sidecar_path: Path | None = None
        self._fh: IO[bytes] | None = None
        self._n_samples_written: int = 0
        self._start_time_utc: datetime | None = None
        self._closed: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, path: Path | str) -> IQRecorder:
        """Open the payload file for writing and stamp the start time.

        Returns ``self`` so the call chains cleanly with the context-
        manager form: ``with IQRecorder(...).start(path) as rec:`` works.

        Args:
            path: Destination path. See module docstring for the
                ``.iq``-extension handling rule.

        Returns:
            ``self`` (for chaining).

        Raises:
            RuntimeError: ``start()`` was already called on this instance
                (the recorder is single-use; construct a fresh instance
                for a new capture).
        """
        if self._fh is not None or self._closed:
            msg = (
                "IQRecorder.start(): recorder is already started or has been closed; "
                "construct a fresh IQRecorder for a new capture."
            )
            raise RuntimeError(msg)

        payload, sidecar = _resolve_payload_and_sidecar(path)
        # Ensure parent dir exists -- a caller passing ``tmp_path / "capture"``
        # has tmp_path created, but a caller passing a nested ``a/b/capture``
        # under a non-existent ``a/b/`` would otherwise fail at open(); the
        # recorder is the right place to absorb that small bit of plumbing.
        payload.parent.mkdir(parents=True, exist_ok=True)

        self._payload_path = payload
        self._sidecar_path = sidecar
        # ``"wb"`` rather than ``"xb"``: overwriting an existing capture is
        # the operator's choice; the alternative is a midnight crash because
        # the previous run did not clean up.
        self._fh = payload.open("wb")
        self._start_time_utc = datetime.now(UTC)
        return self

    def write(self, samples: np.ndarray) -> None:
        """Quantize a ``complex64`` chunk and append it to the open payload.

        Args:
            samples: ``(n,)`` ``complex64`` (or castable) array. Values
                are expected in [-1, +1]; out-of-range values saturate.

        Raises:
            RuntimeError: ``start()`` has not been called, or ``close()``
                already ran.
        """
        if self._fh is None or self._payload_path is None:
            msg = (
                "IQRecorder.write(): call start(path) before write(); "
                "the recorder has no destination yet."
            )
            raise RuntimeError(msg)
        if self._closed:
            msg = "IQRecorder.write(): recorder is closed; write() after close() is invalid."
            raise RuntimeError(msg)

        # Single-channel only at the I/O layer -- coherent multi-channel
        # capture has its own format ticket; ``samples`` must be 1-D.
        if samples.ndim != 1:
            msg = (
                f"IQRecorder.write(): samples must be 1-D (got shape {samples.shape}); "
                "coherent multi-channel capture is out of scope for this format."
            )
            raise ValueError(msg)

        bytes_out = complex64_to_uint8_pair(samples)
        self._fh.write(bytes_out.tobytes())
        self._n_samples_written += int(samples.size)

    def close(self) -> None:
        """Flush and close the payload, then write the sidecar. Idempotent.

        Calling close() before any write() is allowed: it produces an
        empty ``.iq`` file plus a sidecar with ``n_samples = 0`` -- which
        would itself fail the sidecar's ``n_samples > 0`` validator and
        raise. We surface that early rather than persist a sidecar-less
        empty capture.
        """
        if self._closed:
            return
        if self._fh is None or self._payload_path is None or self._sidecar_path is None:
            # Closing without start() is a no-op rather than a raise --
            # symmetric with the contract that close() is idempotent.
            self._closed = True
            return
        self._fh.close()
        self._fh = None

        if self._start_time_utc is None:
            # Defensive: should be set during start().
            msg = "IQRecorder.close(): start time was not recorded; this should be unreachable."
            raise RuntimeError(msg)

        metadata = IQMetadata(
            sample_rate_hz=self._sample_rate_hz,
            center_freq_hz=self._center_freq_hz,
            n_samples=self._n_samples_written,
            start_time_utc=self._start_time_utc,
            source=self._source,
            serial=self._serial,
            gain_db=self._gain_db,
            notes=self._notes,
        )
        self._sidecar_path.write_text(metadata.model_dump_json(indent=2))
        self._closed = True

    # ------------------------------------------------------------------
    # Context-manager surface
    # ------------------------------------------------------------------

    def __enter__(self) -> IQRecorder:
        """Enter assumes ``start()`` has already been called.

        Pattern: ``with IQRecorder(...).start(path) as rec:`` -- the
        ``.start(path)`` call sites the destination; ``__enter__`` is a
        no-op. If start() has not been called we raise rather than
        silently failing later.
        """
        if self._fh is None:
            msg = (
                "IQRecorder.__enter__(): call start(path) before entering the "
                "context manager. Usage: ``with IQRecorder(...).start(path) as rec:``."
            )
            raise RuntimeError(msg)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the recorder on exit -- even if the with-body raised."""
        self.close()

    # ------------------------------------------------------------------
    # Properties (read-only views useful in tests)
    # ------------------------------------------------------------------

    @property
    def payload_path(self) -> Path | None:
        """The ``.iq`` payload path, available after ``start()``."""
        return self._payload_path

    @property
    def sidecar_path(self) -> Path | None:
        """The ``.iq.json`` sidecar path, available after ``start()``."""
        return self._sidecar_path

    @property
    def n_samples_written(self) -> int:
        """Cumulative samples written so far -- the value the sidecar will carry on close."""
        return self._n_samples_written
