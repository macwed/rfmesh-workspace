"""``IQReader`` -- read uint8-interleaved IQ captures with optional sidecar metadata.

The reader is the consume side of the WS-A-004 file I/O foundation:
WS-B's golden-file regression tests and any future replay pipeline
load captured IQ through this class. It is pure Python -- no
hardware, no DSP, no contract types other than ``IQMetadata`` (which
lives in this package).

Two consumption modes are exposed:

* ``read_all()``    -- bulk read, returns the entire file as one
  ``(n_samples,)`` ``complex64`` array. Convenient for small captures
  and for tests; will allocate the full file into memory.
* ``iter_chunks(n)``-- streaming read, yields ``complex64`` chunks of
  ``n`` samples. The final chunk may be short -- that is the *only*
  legitimate short read at the I/O layer (file end, not protocol
  violation; see acceptance 1.c). Use this when the capture is larger
  than comfortable RAM.

Sidecar handling is optional-by-design: when ``<path>.json`` exists, it
is loaded into ``self.metadata``; when it does not, ``self.metadata``
is ``None`` and the reader behaves identically apart from that field.
A missing sidecar is *not* an error -- legacy or hand-managed captures
may have none. A *malformed* sidecar (invalid JSON, wrong schema
version, failed validation) *is* an error and raises
``MalformedIQFileError``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import IO

import numpy as np
from pydantic import ValidationError

from ..exceptions import MalformedIQFileError
from .constants import BYTES_PER_SAMPLE, DEFAULT_CHUNK_SAMPLES
from .conversion import uint8_pair_to_complex64
from .metadata import IQMetadata


def _sidecar_path(iq_path: Path) -> Path:
    """Return ``<iq_path>.json`` -- the sidecar lives next to the payload.

    The convention is that the sidecar mirrors the payload's full name
    with a single trailing ``.json``, so ``foo.iq`` pairs with
    ``foo.iq.json``. Doing this rather than ``foo.json`` keeps the
    pairing robust if the user accumulates multiple capture files in
    one directory.
    """
    return iq_path.with_suffix(iq_path.suffix + ".json")


class IQReader:
    """Reader for uint8-interleaved IQ capture files with optional sidecar metadata.

    Construct with the path to the ``.iq`` payload. The reader does not
    hold an open file handle outside ``iter_chunks``; ``read_all`` opens,
    reads, closes in one operation. ``iter_chunks`` is a context-manager-
    friendly streaming iterator.

    Use as a context manager when streaming::

        with IQReader(path) as reader:
            for chunk in reader.iter_chunks(65536):
                process(chunk)

    Or for bulk reads, plain construction is fine::

        all_samples = IQReader(path).read_all()
    """

    def __init__(self, path: Path | str) -> None:
        """Open a capture, validate file size, and load the sidecar if present.

        Args:
            path: Path to the ``.iq`` payload. The sidecar at
                ``<path>.json`` is loaded if it exists.

        Raises:
            FileNotFoundError: ``path`` does not exist.
            MalformedIQFileError: payload has an odd byte count (not a
                whole number of uint8 IQ samples) OR sidecar exists but
                fails to parse / validate OR sidecar's ``n_samples``
                disagrees with the actual byte count.
        """
        iq_path = Path(path)
        if not iq_path.exists():
            msg = f"IQReader: capture file not found: {iq_path}"
            raise FileNotFoundError(msg)

        self._path = iq_path
        self._byte_count = iq_path.stat().st_size

        if self._byte_count % BYTES_PER_SAMPLE != 0:
            msg = (
                f"IQReader: payload byte count {self._byte_count} is not a multiple of "
                f"{BYTES_PER_SAMPLE} (one I byte + one Q byte per sample); "
                f"file is truncated or not in rtl_sdr_uint8 format: {iq_path}"
            )
            raise MalformedIQFileError(msg)

        self._n_samples = self._byte_count // BYTES_PER_SAMPLE
        self._metadata = self._load_sidecar_if_present()

        # Cross-check sidecar vs file size when both are present. A
        # mismatch is *the* truncation symptom; surfacing it now beats
        # producing partial samples later.
        if self._metadata is not None and self._metadata.n_samples != self._n_samples:
            msg = (
                f"IQReader: payload contains {self._n_samples} samples but sidecar declares "
                f"{self._metadata.n_samples}; capture is truncated or sidecar is wrong "
                f"(path: {iq_path})."
            )
            raise MalformedIQFileError(msg)

        # ``_fh`` is the streaming-mode handle. ``read_all`` opens its own.
        self._fh: IO[bytes] | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        """Path to the ``.iq`` payload this reader was constructed with."""
        return self._path

    @property
    def metadata(self) -> IQMetadata | None:
        """Sidecar metadata if present at construction; ``None`` otherwise.

        A legacy capture without a sidecar reads back normally; only the
        provenance / tuning information is missing.
        """
        return self._metadata

    @property
    def n_samples(self) -> int:
        """Total IQ samples in the payload (derived from file size)."""
        return self._n_samples

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

    def read_all(self) -> np.ndarray:
        """Load the entire capture into a single ``(n_samples,)`` ``complex64`` array.

        Allocates the full file plus an interim ``float32`` array; for
        captures larger than a few hundred MB prefer ``iter_chunks``.
        """
        raw = np.fromfile(self._path, dtype=np.uint8)
        # Defensive: ``np.fromfile`` can in theory return fewer bytes than the
        # file size if the OS short-reads -- treat that as malformed.
        if raw.size != self._byte_count:
            msg = (
                f"IQReader.read_all: expected to read {self._byte_count} bytes from "
                f"{self._path}, got {raw.size}."
            )
            raise MalformedIQFileError(msg)
        return uint8_pair_to_complex64(raw)

    def iter_chunks(
        self,
        chunk_samples: int = DEFAULT_CHUNK_SAMPLES,
    ) -> Iterator[np.ndarray]:
        """Yield ``complex64`` chunks of up to ``chunk_samples`` samples each.

        Args:
            chunk_samples: Target chunk size in IQ samples. Must be > 0.
                The final chunk may be short -- that is the file-end
                signal, not a malformed-read.

        Yields:
            ``(<= chunk_samples,)`` ``complex64`` arrays. Concatenating
            all yielded chunks produces the same data as ``read_all()``.

        Raises:
            ValueError: ``chunk_samples`` is non-positive.
        """
        if chunk_samples <= 0:
            msg = f"IQReader.iter_chunks: chunk_samples must be > 0 (got {chunk_samples})."
            raise ValueError(msg)
        chunk_bytes = chunk_samples * BYTES_PER_SAMPLE
        with self._path.open("rb") as fh:
            while True:
                raw_bytes = fh.read(chunk_bytes)
                if not raw_bytes:
                    return
                if len(raw_bytes) % BYTES_PER_SAMPLE != 0:
                    # A mid-file odd-length chunk would imply a corrupted
                    # capture (the size check at __init__ should have caught
                    # this, but the streaming path is defensive too).
                    msg = (
                        f"IQReader.iter_chunks: chunk has {len(raw_bytes)} bytes, "
                        f"not a multiple of {BYTES_PER_SAMPLE}; file corrupted."
                    )
                    raise MalformedIQFileError(msg)
                yield uint8_pair_to_complex64(np.frombuffer(raw_bytes, dtype=np.uint8))

    # ------------------------------------------------------------------
    # Context-manager surface
    # ------------------------------------------------------------------

    def __enter__(self) -> IQReader:
        """Enter is a no-op: streaming reads open their own handles per call."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Exit is a no-op for the same reason as enter; here for `with` ergonomics."""

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_sidecar_if_present(self) -> IQMetadata | None:
        """Load ``<path>.json`` into an ``IQMetadata`` if it exists, else None.

        A missing sidecar is silent (it is a legitimate state). A
        malformed sidecar is a ``MalformedIQFileError`` -- the sidecar
        existing but lying about the payload is the precise scenario
        Invariant 4 demands we surface loudly.
        """
        sidecar = _sidecar_path(self._path)
        if not sidecar.exists():
            return None
        try:
            return IQMetadata.model_validate_json(sidecar.read_text())
        except ValidationError as exc:
            msg = f"IQReader: sidecar at {sidecar} failed validation: {exc}."
            raise MalformedIQFileError(msg) from exc
        except ValueError as exc:
            # JSON syntax errors and similar parse-time problems.
            msg = f"IQReader: sidecar at {sidecar} is not valid JSON: {exc}."
            raise MalformedIQFileError(msg) from exc
