# rfmesh — Data Pipeline & Node/Server Boundary

**Status:** explanatory, **non-binding**. Mirrors code at `SCHEMA_VERSION = "1.3.0"`.
Derived from `ARCHITECTURE.md` (§1, §2, §7), `INTERFACES.md` (§2, §3, §5), and the
package sources cited inline. If this doc and the code disagree, **the code wins** and
this doc is a bug — the binding sources are `ARCHITECTURE.md` / `INTERFACES.md` / the
`rfmesh-contracts` schemas.
**Audience:** anyone needing one clear answer to *"what computes what, where, and where
does node code end and server code begin?"*

---

## 0. The one-paragraph answer

Radio capture, FFT, spectrograms ("waterfalls"), and the angle estimate **all happen on
the field node**. The node ships only a tiny result — one azimuth ± uncertainty per sweep
(`BearingReport`, ~500 bytes). The **server/aggregator** collects bearings from several
nodes and cross-fixes them into a position; the **confidence ellipse is computed on the
server**, not on the node, because an ellipse needs ≥ 2 angles from different nodes — a
single node only ever produces one angle. The heavy data (raw IQ, full spectra) is
**discarded on the node immediately** after the angle is computed and is never transmitted.
A node only generates gigabytes if you explicitly run the IQ-**recording** tools (a bench /
demo-contingency feature, time-boxed and operator-invoked) — never on the live mesh path.

---

## 1. Stage-by-stage: what runs where

The boundary is one message type: **`BearingReport`**. Everything up to and including the
angle estimate is **node**; everything geometric (positions, ellipses) is **server**.

| # | Stage | What it does | Side | File / function |
|---|-------|--------------|------|-----------------|
| 1 | Capture IQ | RTL-SDR / coherent SDR → `complex64` samples (2–4 MS/s) | **NODE** | `rfmesh-sdr` `devices/rtlsdr.py` `RTLSDRDevice.read(n)`; `Receiver` Protocol |
| 2 | FFT / spectrum / **waterfall** | windowed FFT, Welch PSD, STFT spectrogram | **NODE** | `rfmesh-dsp/spectrum.py` `compute_fft`, `compute_psd`, `compute_spectrogram` |
| 3 | Angle estimate ("waveform at what angle") | L1 RSSI sweep peak-fit **or** L2 MUSIC/Capon subspace peak | **NODE** | `rfmesh-dsp/l1.py`; `rfmesh-dsp/l2_music.py`, `l2_capon.py`; manifold in `array_manifold.py` |
| 4 | Honest σ | 1-σ uncertainty on the azimuth (load-bearing weight, Invariant B2) | **NODE** | `rfmesh-dsp/l1.py`, `l2_music.py` (sigma estimators) |
| 5 | (optional) Classify | L3 edge-ML emitter label from STFT features | **NODE** (RPi) | `rfmesh-ml` |
| — | **Emit `BearingReport`** | azimuth + σ + method + snr (+ optional class, + optional pseudospectrum) | **NODE → wire** | wrapped by `rfmesh-node`; schema in `rfmesh-contracts/messages.py` |
| ===|=== **WIRE BOUNDARY** ===|=== UDP/msgpack (WiFi) or compressed (LoRa) ===|===|=== `rfmesh-node/bearer/` (`WifiBearer`, `LoraBearer`, `BothBearer`) |
| 6 | Time-batch | group bearings in a ~100 ms window per `FusionConfig.batch_window_ms` | **SERVER** | `rfmesh-node` fusion ingest |
| 7 | Cross-fix → position | Stansfield weighted-LS seed + Gauss-Newton MLE refine | **SERVER** | `rfmesh-fusion/stansfield.py`, `mle.py`, orchestrated by `fuser.py` |
| 8 | **ELLIPSE** | Fisher info → 2×2 ENU covariance → eigendecomp → 95% ellipse | **SERVER** | `rfmesh-fusion/covariance.py` `compute_covariance` → `covariance_to_ellipse` |
| 9 | GDOP, residuals, confidence | geometry quality + per-node self-diagnosis + HIGH/MED/LOW | **SERVER** | `rfmesh-fusion/gdop.py`, `residuals.py`, `confidence.py` |
| — | **Emit `FixEvent`** | position + covariance + ellipse + gdop + residuals + method | **SERVER → out** | `rfmesh-fusion/fuser.py` |
| 10 | Output | hostile-emitter marker + ellipse polygon in ATAK; ops dashboard | **SERVER** | `rfmesh-cot/`, `rfmesh-ops/` |

Purity is enforced (Invariant B5 / `import-linter`): `rfmesh-dsp` and `rfmesh-fusion` do
**zero** network or SDR I/O — they are pure functions, runnable in CI with no hardware.

---

## 2. Where the ellipse is calculated — direct answer

**On the server/aggregator**, in `rfmesh-fusion`:

- `compute_covariance(emitter_xy, bearings, node_positions_enu)` — builds the analytic
  Jacobian `J` (rad/m), applies inverse-variance weights `w_i = 1/σ_i²`, forms the Fisher
  information `F = JᵀWJ`, and returns the 2×2 ENU covariance `Σ = F⁻¹` (m²).
- `covariance_to_ellipse(cov_2x2)` — eigendecomposes `Σ`, scales the semi-axes by the
  χ²(0.95, df=2) ≈ 5.991 quantile (so semi-axis ≈ 2.448·σ_principal), and returns
  `EllipseENU(semi_major_m, semi_minor_m, orientation_deg)`.

Both live in `packages/rfmesh-fusion/src/rfmesh_fusion/covariance.py`. The scaling
convention is documented in `INTERFACES.md` §2 (`EllipseENU`).

**Why not on the node:** a single node measures one bearing — a ray, not a fix. The ellipse
is the intersection geometry of ≥ 2 rays from different node positions, weighted by each
node's honest σ. Only the server holds all the bearings, so only the server can compute it.
This is also why the node's σ is "load-bearing": an over-optimistic σ from one node poisons
the ellipse for every fix it touches (Invariant B2).

---

## 3. The wire boundary: `BearingReport`

The node→server contract (`rfmesh-contracts/messages.py`, see `INTERFACES.md` §3). What
crosses the wire per sweep:

- `node_id`, `t_unix_ns`, `node_position` (WGS-84 + σ)
- `azimuth_deg` (geographic, heading already corrected at the node)
- `azimuth_sigma_deg` (1-σ — the fusion weight)
- `method` (`L1_RSSI` / `L2_MUSIC` / `L2_CAPON` / ...), `snr_db`
- optional `emitter_class` + `classification_confidence`
- optional `raw_pseudospectrum` — **debug only**, 720 × float32 @ 0.5° = **2,880 bytes**,
  **WiFi only** (LoRa strips it; `rfmesh-node/bearer/lora.py`)

The node is otherwise stateless about the rest of the mesh (star topology). `NodeStatus`
heartbeats (~0.5 Hz) ride the same bearers.

```
NODE                                          │  WIRE  │              SERVER
SDR ─ FFT ─ angle(L1/L2) ─ σ ─► BearingReport ─┼───────┼─► batch ─ Stansfield+MLE
        (raw IQ discarded here)                │ ~500B │      └─ covariance ─ ELLIPSE ─► FixEvent ─► ATAK
```

---

## 4. The "GB per node" concern — resolved

### Live mesh path: never gigabytes

Raw IQ is large — `complex64` at 2 MS/s ≈ **16 MB/s** single-channel, ~**32 MB/s** for a
2-channel coherent L2 node — but on the live path it is **consumed and discarded** in
stage 2–4. Nodes are stateless processors: read IQ → compute angle → emit `BearingReport`
→ forget the IQ. No ring buffer, no disk. The "waterfall" spectrogram is computed on the
node for L3 features only and is likewise discarded; it is **not** transmitted.

Actual wire rates per node (bearing reports + heartbeat):

| Node type | Rate | Per hour |
|-----------|------|----------|
| L1 | ~5 KB/s | ~18 MB |
| L2 over WiFi (incl. 2,880 B pseudospectrum) | ~34 KB/s | ~122 MB |
| L2 over LoRa (pseudospectrum stripped) | ~5 KB/s | ~18 MB |

A whole multi-node demo is **hundreds of MB**, not GB. A `MAX_MESSAGE_BYTES` cap (64 KiB,
`rfmesh-node/bearer/envelope.py`) guards against accidental oversized payloads.

### Where the gigabytes actually come from: the recording tools

Raw IQ recording **does** exist, but only as deliberate, time-boxed, operator-invoked
tooling — never the live mesh:

- **`IQRecorder`** (`rfmesh-sdr/io/recorder.py`) — writes uint8-interleaved `.iq`
  (rtl_sdr-native, ~4 MB/s at 2 MS/s) for golden-file / replay test data.
- **`ReplayRecorder`** (`apps/demo-replay/.../recorder.py`) — Phase-C bench capture,
  writes `complex64` `.iqx` (~16 MB/s single-ch, ~64 MB/s coherent) via the
  `rfmesh-demo-record --scenario … --output … --duration-s N` CLI. This is the jury-demo
  **failure contingency** (replay a scenario if live hardware misbehaves on stage).

So the gigabytes are bounded by the `--duration-s` you ask for: a 60 s coherent `.iqx`
capture ≈ **~3.8 GB**, a 60 s single-channel `.iq` ≈ **~240 MB**. These are produced on
purpose, on a bench, for a finite window — not streamed across the mesh and not generated
by the live pipeline.

**Bottom line:** the live system cannot produce GB-per-node; the recording tools can, and
that is by design (golden data, demo contingency), gated by an explicit duration and an
operator command.

---

## 5. Quick reference — packages by side

**Node-side:** `rfmesh-sdr` (capture + IQ I/O), `rfmesh-servo` + `firmware/` (antenna
sweep), `rfmesh-dsp` (L1/L2 angle + σ), `rfmesh-ml` (L3 class), `rfmesh-node` (runtime +
bearers).

**Server-side:** `rfmesh-fusion` (cross-fix + covariance + **ellipse** + GDOP),
`rfmesh-cot` (ATAK output), `rfmesh-ops` (dashboard).

**Both / boundary:** `rfmesh-contracts` (frozen message + config schemas — the wire),
`apps/demo-replay` (runs the whole pipeline in-process for the demo).
