# TICKET WS-A-004: Port the salvaged IQ I/O foundation — IQReader, IQRecorder, IQMetadata, constants

## Goal (one sentence)

Port the architecture-neutral IQ file I/O foundation from the prior
project (`io/iq_reader.py`, `io/iq_recorder.py`, `io/formats.py`'s
`IQMetadata`, and the relevant constants from `io/constants.py`) into
`rfmesh-sdr` as `rfmesh_sdr.io.*` — pure Python, no hardware
dependencies, no DSP coupling — so Workstream B can build **golden
files** and **replay tests** against captured-on-bench IQ streams,
and so a future RTLSDRDevice port (WS-A-005) can reuse the
already-deduplicated `_to_complex64` instead of reintroducing the old
duplication.

## Context (links only)

- Salvage source: `SALVAGE_AUDIT.md` Part 4a (file I/O — TAKE
  verbatim) and 4b (formats.py — only `IQMetadata` survives;
  `RSSIMeasurement`, `NodeConfig`, `AggregatorConfig` are LEAVE).
- Why this is high-leverage: WS-B's σ-honesty test (and any future
  L3 classifier training) wants the ability to record IQ from the
  simulator or from bench hardware once, then replay it through
  pipeline tests deterministically. Without recorder + reader,
  golden-file regression discipline is unrooted.
- Why this is not the same ticket as the RTLSDRDevice port: this
  ticket is pure Python with **no hardware-side dependencies at
  all**; WS-A-005 (the subprocess RTLSDRDevice port) needs `rtl_sdr`
  binary in PATH plus hardware-test infrastructure plus a real
  device for verification. Different cadence, different gate.
- Architecture: `ARCHITECTURE.md` §4 (simulator first-class; replay
  testing complements live simulator testing).
- Prior tickets: WS-A-001/002 must be merged (`rfmesh-sdr` package
  exists). This ticket does NOT depend on WS-A-003.

## Acceptance criteria

1. `uv run pytest packages/rfmesh-sdr -v` passes. All prior WS-A
   tests continue to pass. New tests:

   a. `tests/io/test_reader_writer_roundtrip.py::test_record_then_read_is_identity`
      — Write a known `complex64` numpy array via `IQRecorder` to a
      temp `.iq` file (uint8-interleaved RTL-SDR-style format).
      Read back via `IQReader`. The roundtrip is identity within
      the 8-bit quantization grid (which is the entire point of the
      format). Specifically: `np.allclose(read_back,
      original_quantized_to_uint8_grid, atol=1/127.5)` holds.

   b. `tests/io/test_reader_writer_roundtrip.py::test_recorder_metadata_sidecar`
      — `IQRecorder(...).start(path)` produces `path.iq` AND
      `path.iq.json` (the `IQMetadata` sidecar). The sidecar
      contains `sample_rate_hz`, `center_freq_hz`, `start_time_utc`,
      `n_samples`, `format="rtl_sdr_uint8"`, plus optional caller
      metadata. Loading the sidecar via `IQMetadata.model_validate_json`
      roundtrips losslessly.

   c. `tests/io/test_reader.py::test_iq_reader_chunked_read`
      — `IQReader` exposes both `read_all() -> np.ndarray` and
      `iter_chunks(chunk_samples: int) -> Iterator[np.ndarray]`.
      Test that the concatenation of all chunks equals
      `read_all()`. Test that asking for a chunk size larger than
      the remaining samples yields a final short chunk (the only
      legitimate short read in the I/O layer — file end, not
      protocol violation).

   d. `tests/io/test_reader.py::test_iq_reader_bad_path_raises`
      — Opening a missing file raises `FileNotFoundError`. Opening
      a truncated/malformed file (odd byte count, since complex64
      ↔ 2 uint8 means total byte count must be even) raises
      `MalformedIQFileError` (define in `rfmesh_sdr.exceptions`).

   e. `tests/io/test_metadata.py::test_iq_metadata_schema_versioning`
      — `IQMetadata` carries an explicit `schema_version: Literal["1.0.0"]`
      so future format changes are detectable. Constructing with
      an older/newer version string raises Pydantic validation
      error.

   f. `tests/io/test_metadata.py::test_iq_metadata_round_trip_json`
      — Pydantic `model_dump_json()` → `model_validate_json()` is
      identity. Required for sidecar persistence.

   g. `tests/io/test_constants.py::test_rtl_sdr_dc_offset_value`
      — `RTL_SDR_DC_OFFSET = 127.5` (the value baked into the
      uint8 → complex64 conversion: `(byte - 127.5) / 127.5`).
      `BYTES_PER_SAMPLE = 2`.

   h. `tests/io/test_record_simulator.py::test_record_synthetic_receiver_replay`
      — End-to-end: open a `SyntheticReceiver`, record N samples
      via `IQRecorder` to a temp file, close the receiver. Open
      the temp file via `IQReader.read_all()`. Verify the recovered
      samples match what the receiver originally produced (within
      the uint8 quantization grid). This is the "the I/O layer
      doesn't lie about what came through it" honesty check.

2. `uv run mypy packages/rfmesh-sdr` clean (strict).
3. `uv run ruff check packages/rfmesh-sdr` clean.
4. `uv run ruff format --check packages/rfmesh-sdr` clean.
5. No new runtime dependencies. (Salvage uses pure-stdlib + numpy +
   pydantic, all already present.)
6. No imports from other workstream packages.

## Out of scope (explicit non-goals)

- **RTLSDRDevice itself.** Subprocess-wrapped `rtl_sdr` binary
  hardware path is WS-A-005's scope. This ticket establishes
  the I/O foundation that A-005 will reuse — nothing more.
- DongleCalibration refactor (`io/calibration.py` per SALVAGE_AUDIT
  Part 4c). That moves with the hardware path in WS-A-005, since
  the re-seating against `serial`-keyed maps is meaningful only
  when there is a `Receiver.capabilities().serial` to key against.
- Streaming network I/O. WS-CD's domain.
- A `replay-as-Receiver` adapter (a class that implements `Receiver`
  Protocol by reading from a recorded `.iq` file instead of from
  the simulator or hardware). Tempting and useful, but it adds a
  third concrete `Receiver` implementation surface for QC; defer to
  its own ticket once we know what WS-B and WS-CD's replay tests
  actually need from it.
- Anything beyond uint8-interleaved RTL-SDR format. (Future:
  int16-interleaved for HackRF, float32 for capture from coherent
  receivers. Add when needed, not speculatively.)
- Mutating `packages/rfmesh-contracts/**` (Invariant 1).

## Files you may touch

- `packages/rfmesh-sdr/src/rfmesh_sdr/io/__init__.py`               (create — re-export `IQReader`, `IQRecorder`, `IQMetadata`)
- `packages/rfmesh-sdr/src/rfmesh_sdr/io/constants.py`              (create — port from salvage `io/constants.py`)
- `packages/rfmesh-sdr/src/rfmesh_sdr/io/conversion.py`             (create — `uint8_pair_to_complex64`, `complex64_to_uint8_pair`; the deduplicated `_to_complex64` from salvage, plus its inverse)
- `packages/rfmesh-sdr/src/rfmesh_sdr/io/reader.py`                 (create — port from salvage `io/iq_reader.py` ~142 LoC, refactored to use `conversion`)
- `packages/rfmesh-sdr/src/rfmesh_sdr/io/recorder.py`               (create — port from salvage `io/iq_recorder.py` ~154 LoC, refactored to use `conversion`, writes sidecar via `IQMetadata`)
- `packages/rfmesh-sdr/src/rfmesh_sdr/io/metadata.py`               (create — `IQMetadata` Pydantic model with `schema_version: Literal["1.0.0"]`)
- `packages/rfmesh-sdr/src/rfmesh_sdr/exceptions.py`                (modify — add `MalformedIQFileError`)
- `packages/rfmesh-sdr/src/rfmesh_sdr/__init__.py`                  (modify — re-export `IQReader`, `IQRecorder`, `IQMetadata`, the new exception)
- `packages/rfmesh-sdr/tests/io/test_reader_writer_roundtrip.py`    (create)
- `packages/rfmesh-sdr/tests/io/test_reader.py`                     (create)
- `packages/rfmesh-sdr/tests/io/test_metadata.py`                   (create)
- `packages/rfmesh-sdr/tests/io/test_constants.py`                  (create)
- `packages/rfmesh-sdr/tests/io/test_record_simulator.py`           (create — uses the conftest scenarios from WS-A-001)
- `packages/rfmesh-sdr/tests/conftest.py`                           (modify if needed — a `tmp_iq_path` fixture using `pytest.tmp_path` is fine)

Note: per ADR-006 / AGENTS.md §3.5, do NOT create
`packages/rfmesh-sdr/tests/io/__init__.py`. pytest discovers test
files without it.

## Files you may NOT touch

- `packages/rfmesh-contracts/**` (Invariant 1).
- Other workstream packages (Invariant 2).
- WS-A-001 / 002 / 003 source or test files. The I/O layer is a
  new namespace; no existing file needs modification beyond the
  `__init__.py` re-exports and `exceptions.py` addition.

## Design hints (non-binding)

### Salvage discipline

The salvage code is "TAKE-grade" — well-tested, architecture-neutral.
**Do not redesign it.** Port it, refactor only what is necessary to:

- deduplicate `_to_complex64` (was in two places; lands in one,
  `io/conversion.py`),
- adopt the workspace's typing conventions (mypy strict, ruff
  format),
- replace any `loguru` references with the workspace logger
  (if any).

If the salvage source is available at a known path, you may
`view` it for reference; otherwise, the file shape is documented
in `SALVAGE_AUDIT.md` Part 4a.

### `IQMetadata` shape

A Pydantic model with `ConfigDict(frozen=True, extra="forbid")` —
same discipline as `rfmesh-contracts` and as the WS-A-002
internal data models.

```python
class IQMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0.0"] = "1.0.0"
    sample_rate_hz: float        # Hz, positive
    center_freq_hz: float        # Hz, positive
    n_samples: int               # positive
    start_time_utc: datetime     # tz-aware UTC
    format: Literal["rtl_sdr_uint8"] = "rtl_sdr_uint8"

    # Optional caller metadata (any of these may be None):
    source: str | None = None        # e.g. "synthetic_receiver", "rtlsdr_0"
    serial: str | None = None        # device serial, when applicable
    gain_db: float | None = None
    notes: str | None = None
```

### `IQRecorder` lifecycle

Modelled on context-manager pattern:

```python
with IQRecorder(path, sample_rate_hz=2.048e6, center_freq_hz=915e6,
                source="synthetic") as rec:
    for chunk in receiver.iter_chunks(8192):  # or however the caller drives it
        rec.write(chunk)
# On __exit__, writes the sidecar with final n_samples and end-time-utc.
```

Single-writer; not safe for concurrent writes from multiple threads.
That is fine — the producer is one thread.

### `IQReader` lifecycle

Two modes — both safe to use, the caller picks:

```python
# Bulk read:
reader = IQReader(path)
all_samples = reader.read_all()

# Streaming read (for large files / memory pressure):
with IQReader(path) as reader:
    for chunk in reader.iter_chunks(chunk_samples=65536):
        process(chunk)
```

If a sidecar exists, `IQReader.metadata: IQMetadata | None` is
populated. If not, `metadata is None`. A missing sidecar is **not**
an error — older capture files may not have one.

### Why uint8-interleaved as the only supported format

RTL-SDR's native output is uint8 interleaved (I/Q bytes alternating,
each in `[0, 255]` representing `[-1.0, +1.0]` via the
`(byte - 127.5) / 127.5` map). Float-precision capture from
coherent receivers (bladeRF, Pluto+) will eventually need its own
format; defer until those hardware paths land in WS-A-005+.

## Stop conditions

- Stop after producing the diff. Paste pytest + mypy + ruff output.
- If the roundtrip test (1.a) fails for a quantization reason that
  is not simply "you forgot to round before casting to uint8",
  STOP and surface — the salvage's `_to_complex64` is field-tested
  and any divergence is suspicious.
- If `IQMetadata.schema_version` interacts oddly with `rfmesh_contracts.SCHEMA_VERSION`
  (different concepts — capture-file format version vs contract
  schema version), STOP and surface. They are deliberately
  separate; if mypy or pydantic confuses them, that is worth a
  scratchpad note.
- If you find that salvage code requires a dependency we do not
  have (`scipy`, `h5py`, anything else), STOP and surface — likely
  means the salvage is reaching past architecture-neutral and the
  port needs a smaller scope.

## Provenance note

This ticket is the first piece of pure salvage in WS-A — code that
existed in the prior project, was well-tested, and is
architecture-neutral. Per `AGENTS.md` salvage discipline: minimal
refactor, maximal preservation of the field-validated behaviour.
The risk profile of this ticket is much lower than WS-A-001/002/003
because the algorithms exist and work; the work is mechanical
translation across the contract boundary.
