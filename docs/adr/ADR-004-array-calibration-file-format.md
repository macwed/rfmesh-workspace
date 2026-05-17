# ADR-004: Array calibration file format

**Status:** ACCEPTED (2026-05-17, lead-Opus + Maciej, conditional on WS-B review points #1 and #2 incorporated below; point #3 deferred to a follow-up)
**Date:** 2026-05-15
**Author:** Opus-A (Workstream A)
**Reviewers:** Lead, Opus-B (Workstream B — consumer of this file)
**Coordination point:** `WORKSTREAMS.md` §4, row #3 (Array calibration
file format).

---

## Context

`ArrayConfig.calibration_file` (`packages/rfmesh-contracts/src/rfmesh_contracts/config.py`)
is a path to a per-element phase/gain calibration table produced by
Workstream A's calibration routine and consumed by Workstream B's L2
DSP (MUSIC steering and MVDR null-steering). The contract names the
field but deliberately leaves the file format unspecified — that is
this ADR's job.

Without a decided format, two failure modes appear:

1. WS-A invents one shape; WS-B reads a different shape; integration
   day blows up. (Exactly the kind of cross-workstream coupling
   `AGENTS.md` Invariants 1 and 2 exist to prevent — but here, the
   coupling is *forced* by the contract field referring to a file
   neither workstream owns.)
2. The format becomes ad-hoc and "good enough for the bench", so when
   the L2 hardware integration session arrives we either redesign it
   under time pressure or accept a fragile, undocumented path. Both
   are bad outcomes.

The contract docstring (`config.py:171-180`) is unambiguous on one
point: *an L2 node asked to run MUSIC without a calibration file MUST
fail loudly, not silently emit garbage bearings* (Invariant 4). So the
file is load-bearing for L2 honesty. It deserves a real format.

## Decision (proposed)

Use **NumPy `.npz` (uncompressed) for the data + a JSON sidecar for
metadata**. One pair of files per calibration capture:

```
<basename>.npz       # numerical payload
<basename>.json      # human-readable metadata
```

### `.npz` payload (one archive, multiple named arrays)

| Key | Shape | dtype | Meaning |
|---|---|---|---|
| `complex_corrections` | `(n_freq, n_elements)` | `complex128` | Per-element-per-frequency calibration *corrections*: multiplying a raw channel by `complex_corrections[f, i]` (NOT by `1/...`) gives the calibrated channel referenced to channel 0. By convention `complex_corrections[:, 0]` is identically `1+0j`. Naming aligns the on-disk field with WS-A's in-memory `Calibration.complex_corrections` record so one convention covers both (WS-B sign-off point #1, 2026-05-16). |
| `frequencies_hz` | `(n_freq,)` | `float64` | Calibration frequencies, ascending. Consumers interpolate (linear in log-magnitude, linear in phase after unwrap) for frequencies between samples. |
| `element_positions_m` | `(n_elements, 2)` | `float64` | Element positions in the array's local (x, y) frame, metres. Redundant with `ArrayConfig.element_positions_m` for CUSTOM, but carried in the file so a calibration is self-describing. For ULA/UCA derived from `ArrayConfig.element_spacing_m` and `geometry`. |
| `noise_floor_estimate_dbfs` | `()` (scalar) | `float64` | Noise floor at the time of calibration, for sanity-checking that calibration was performed under benign conditions. Not load-bearing; advisory. |

### `.json` sidecar (UTF-8 JSON, pretty-printed, < 1 KB typical)

```json
{
  "schema_version": "1.0.0",
  "rfmesh_sdr_version": "<git-sha or pip version>",
  "node_id": "node-pluto-01",
  "array": {
    "geometry": "ula",
    "n_elements": 2,
    "element_spacing_m": 0.164
  },
  "receiver": {
    "driver": "pluto",
    "serial": "1044734c9605000114001a00f4942..."
  },
  "calibration": {
    "method": "pilot_tone",
    "reference_channel": 0,
    "n_samples_per_freq": 1048576,
    "snr_db_estimate": 32.4,
    "captured_utc": "2026-06-12T14:08:33Z",
    "captured_t_unix_ns": 1786543713000000000
  },
  "checksum": {
    "algorithm": "sha256",
    "npz_sha256": "<hex>"
  }
}
```

The `schema_version` here is the **calibration-file schema version**,
NOT `rfmesh_contracts.SCHEMA_VERSION`. They evolve independently; the
calibration loader rejects unknown calibration-file schemas the same
way `rfmesh-contracts` consumers reject unknown contract schemas.

### Loader ownership (WS-B sign-off point #2, 2026-05-16)

The calibration file loader (`ArrayCalibration`) lives **only** in
`rfmesh-sdr` (`packages/rfmesh-sdr/src/rfmesh_sdr/calibration.py`). It is
**never** imported by `rfmesh-dsp`. The DSP-side L2 estimators consume
*already-calibrated* coherent IQ via the `CoherentReceiver` Protocol;
they do not see calibration metadata or perform file I/O. This split
enforces Invariant B5 ("`rfmesh-dsp` is pure: no file I/O") and
Invariant B2 (cross-workstream coupling is via contracts only — and the
calibration file is *not* a contract). A reviewer who sees an
`ArrayCalibration` import inside `rfmesh-dsp` rejects the diff.

### Loader API (in `rfmesh_sdr.calibration`, NOT in contracts)

```python
from rfmesh_sdr.calibration import ArrayCalibration

cal = ArrayCalibration.from_files(npz_path, json_path)
# or, if the sidecar is alongside:
cal = ArrayCalibration.load(npz_path)  # finds the .json next to it

# Pure numpy operation; no I/O after loading:
# (apply multiplies channels by complex_corrections, no 1/x)
calibrated = cal.apply(raw_coherent_block, frequency_hz)
```

The loader validates: checksum match, n_elements consistency with
`ArrayConfig`, frequency range covering the requested operating
frequency (refuses extrapolation; raises if the operating frequency
is outside `[frequencies_hz[0], frequencies_hz[-1]]`).

## Alternatives considered

### Alternative 1 — Single JSON file with complex offsets inline

**Pros:** human-readable end-to-end; one file; trivially diff-able.
**Cons:** complex numbers need explicit `{"re": ..., "im": ...}`
encoding (ugly), large arrays bloat JSON badly (a 1024-frequency
calibration at 5 elements is ~80 KB JSON vs ~80 KB npz — but the
JSON is ~3× slower to parse and not visually scannable anyway), no
checksum on the numeric payload.
**Verdict:** rejected. The hybrid (.npz + .json) gives the JSON
metadata's auditability for everything that matters to a human,
without paying the JSON tax on the dense numeric payload.

### Alternative 2 — HDF5

**Pros:** industry standard for scientific data with metadata.
**Cons:** runtime dependency on `h5py`, which pulls in `libhdf5`,
which is a system library — exactly the kind of dep we don't want
for a hackathon deliverable. NumPy's native `.npz` is in-tree and
free.
**Verdict:** rejected on dependency-cost grounds.

### Alternative 3 — YAML for everything

**Pros:** matches the contract's config file convention.
**Cons:** complex-number arrays in YAML are horrible. Same dense-data
tax as JSON, worse parser performance.
**Verdict:** rejected. Use YAML for configs, npz+JSON for data.

### Alternative 4 — Pickle

**Pros:** trivial to write and read.
**Cons:** unsafe to load from any source, version-fragile across
Python/NumPy versions, opaque to non-Python tooling, opaque to git
diff.
**Verdict:** rejected. Pickle is for caches, not for persisted
load-bearing data.

## Consequences

**Positive.**
- Two workstreams can integrate against a documented file without
  asking each other every time.
- The format is inspectable: `python -m json.tool <file>.json` shows
  the metadata; `numpy.load(<file>.npz)` shows the arrays. No bespoke
  tooling.
- Adding new metadata fields (e.g. temperature-dependence flag,
  per-element gain-stage info) is a MINOR bump of the
  calibration-file schema — additive, backward-compatible.
- Checksum in the JSON sidecar protects against silent corruption of
  the .npz between calibration and deployment.

**Negative.**
- Two files per calibration. We can ship a single-file convenience
  loader (`ArrayCalibration.load(<basename>)` resolves both) to make
  this invisible at the API level, but on disk and in `git diff` they
  are still two files.
- `complex128` doubles the bytes vs `complex64`. Justified because
  calibration data is computed once per session and lives in memory
  for the duration; we want maximum precision in the offsets. The
  consumer downcasts to `complex64` on application against the IQ.

**Operational.**
- The calibration routine (later WS-A ticket) writes both files
  atomically: write `<basename>.npz.tmp` and `<basename>.json.tmp`,
  fsync, rename both. Avoids the half-written-file failure mode.

## Open questions

1. **Polarisation per frequency?** A calibration at 915 MHz and a
   calibration at 2.4 GHz may produce different `complex_offsets`
   because the per-frequency cable-and-circuit phase response is
   nonlinear. The proposed format handles this via `(n_freq,
   n_elements)` array — n_freq=1 for a single-band capture, n_freq>1
   for a wideband sweep. The DSP-side question is whether MUSIC at
   915 MHz needs more than one calibration point or whether one is
   "good enough" — defer to Opus-B (will probably end up an empirical
   answer).
2. **Should the calibration depend on antenna heading?** Probably
   not for L2 DSP (the array's *internal* phase relationships are
   what calibration captures, and those are antenna-orientation-
   independent at the cable level), but worth confirming with the
   hardware reality during the first L2 bench session.
3. **Versioning of `complex_offsets` interpretation.** Right now the
   spec says "multiplying by `1/complex_offsets[f, i]` gives
   calibrated channel". An alternative convention is "calibrated =
   raw × complex_offsets[f, i]". Both are valid; we just pick one and
   document it loudly. Open to Opus-B's preference if it matches an
   existing pyargus / KrakenSDR convention.

## Sign-off requirements before this ADR moves to ACCEPTED

- [ ] Opus-B confirms the format is loadable / usable for L2 MUSIC.
- [ ] Lead confirms no dependency violation (NumPy is already a
      contracts-package dep; JSON is stdlib).
- [ ] One concrete reference file pair (synthetic, generated by the
      simulator's coherent mode in WS-A-002) checked into the repo
      under `packages/rfmesh-sdr/tests/data/example_calibration.{npz,json}`
      to make the spec impossible to misread.

---

## Provenance

This ADR is the deliverable for cross-workstream coordination point
#3 in `WORKSTREAMS.md` §4. It is being raised early — *before* the L2
hardware path is built — so that WS-B can mock the loader against
this exact shape while writing the MUSIC code, and so WS-A-002 knows
what `calibrate()`'s in-memory representation must serialise to.
