# rfmesh — Workstreams

**Status:** binding for ownership and dependencies. Acceptance criteria are
the lead's targets; subagents may refine within their own scope. The
package-ownership table in §1 is canonical for *who writes which files*;
the per-workstream agent-name column ("Mid-level agent") is a
historical fossil and is now an annotation only (see note below).
**Audience:** lead-Opus + council subagents, Maciej as project owner.
**Date:** 2026-05-14 (original); §0 + §1 header amended 2026-05-17 for
the lead-Opus handoff per `AGENTS.md` §1.

This document is **the** ownership map. It says who owns which package,
what each workstream delivers, in what order, against what acceptance
criteria, and crucially **what each workstream inherits from the old
repository per `SALVAGE_AUDIT.md`.** No file is owned by two workstreams,
and no workstream depends on another workstream's *implementation* —
dependencies are on the frozen contracts only.

Read this in tandem with `SALVAGE_AUDIT.md` (file-by-file disposition) and
the per-workstream bootstrap that names what the agent starts with.

---

## §0 The ownership rule

Each row of the table below names **one** owner package or document group.
A file lives in exactly one workstream. A workstream may *read* from
`rfmesh-contracts` (universally) and from the inherited code in other
workstreams' salvage rows (as documented reference), but it **writes** only
to its own files. Cross-workstream changes require lead review.

**Operational model (updated 2026-05-17).** Workstream-zero is the lead
(this conversation, lead-Opus / Claude Code). Workstreams A, B, C+D
remain useful as **logical package groupings** (the rows of §1 describe
which files belong together); the "three mid-level Opus agents in
worktree isolation" model has been replaced by lead-Opus running a
**council of subagents** (architect / code-reviewer / rf-dsp-specialist
/ demo-integrity, defined in `.claude/agents/*.yaml`) plus
ticket-builder subagents spawned per package. The council reviews
diffs and ADRs before merge; the lead executes commits and pushes
to `main` directly. See `CLAUDE.md` (project root) lines 5-49 for the
canonical council protocol.

---

## §1 The workstream table

| # | Workstream | Historical agent (annotation only — see §0) | Owns (packages / files) | Depends on (contracts only) | Salvage from `rf-mesh` | Reviewer for cross-cutting |
|---|---|---|---|---|---|---|
| **0** | **Lead — contracts & docs** | (this conversation) | `packages/rfmesh-contracts/`, `ARCHITECTURE.md`, `INTERFACES.md`, `INHERITED_CONTEXT.md`, `WORKSTREAMS.md`, `AGENTS.md`, `SALVAGE_AUDIT.md`, `docs/adr/` | — | Audit-level only (no source files in this workstream salvaged) | All other workstreams |
| **A** | **SDR + simulator + firmware salvage** | Opus-A | `packages/rfmesh-sdr/`, `packages/rfmesh-servo/`, `firmware/`, `tools/` (servo & capture tooling) | `rfmesh-contracts` | **TAKE:** entire `firmware/` tree (incl. ESP32-S2 port — see §1.1 in INHERITED_CONTEXT), `rfmesh/scan/servo/` (host driver, all 6 modules), `rfmesh/dsp/` is *not* in A's row, `rfmesh/io/iq_reader.py` & `iq_recorder.py` & `IQMetadata`, `rfmesh/io/constants.py`, `tools/servo_repl.py`, `docs/wire-protocols/servo_uart_v1.md`, `docs/hardware_calibration.md`, `lora_beacon_spec.md`. **REFACTOR:** `rfmesh/io/device.py` (ABC → `Receiver` Protocol), `rfmesh/io/devices/rtlsdr.py` (subclass → Protocol-conformer), `rfmesh/io/calibration.py` (re-homed + add ArrayCalibration sibling), `rfmesh/cli/servo_calibrate.py`, `rfmesh/cli/calibrate_dongles.py`, `rfmesh/cli/record_iq.py`. **LEAVE:** `rfmesh/io/devices/bladerf.py` stub (rewrite fresh as `BladeRFCoherentReceiver`). | Lead |
| **B** | **DSP + ML** | Opus-B | `packages/rfmesh-dsp/`, `packages/rfmesh-ml/`, `packages/rfmesh-ml/threats/` | `rfmesh-contracts`, `rfmesh_sdr.simulator.SyntheticReceiver` (consumes its IQ) | **TAKE:** `rfmesh/dsp/rssi.py` (~265 LoC, 99–100% covered), `rfmesh/dsp/spectrum.py` (~194 LoC, ditto), `rfmesh/dsp/constants.py`. **REWRITE-with-reference:** none in B's scope from the old repo — L1 bearing estimation, L2 MUSIC/MVDR, the array-manifold / steering-vector layer, and all of L3 (STFT features + classifier + threat profiles) are new work. | Lead, Opus-A (Receiver Protocol conformance) |
| **C+D** | **Fusion + CoT + node runtime + ops** | Opus-CD | `packages/rfmesh-fusion/`, `packages/rfmesh-cot/`, `packages/rfmesh-node/`, `packages/rfmesh-ops/`, `apps/`, `scenarios/`, `configs/` | `rfmesh-contracts` only | **REFACTOR:** `rfmesh/mesh/protocol.py` framing codec (length-prefixed MsgPack envelope kept, payload types swapped to contracts messages). **REWRITE-with-reference:** `rfmesh/mesh/node.py` (4-thread concurrency model as proven prior art), `rfmesh/mesh/aggregator.py` (asyncio server scaffold as proven prior art), `rfmesh/cli/run_node.py`, `rfmesh/cli/run_aggregator.py`. **NEW:** all of `rfmesh-fusion` (Stansfield + MLE + GDOP + ellipse), all of `rfmesh-cot` (PyTAK adapter), all of `rfmesh-ops` (dashboard). **TAKE:** `.pre-commit-config.yaml` patterns, `.github/workflows/ci.yml` patterns, `pyproject.toml` tool config (ruff/mypy/pytest), `.gitignore`, `LICENSE`. | Lead, Opus-A (transport on `Receiver` boundary), Opus-B (consumes `BearingReport`s) |

---

## §2 What each workstream delivers (acceptance criteria)

These are the lead's targets at v1.0.0 of the contracts. Lead-Opus
authors tickets per the `AGENTS.md` §4 format and spawns ticket-builder
subagents to execute them; the council (`.claude/agents/*.yaml`) reviews
diffs and ADRs before merge per `CLAUDE.md` lines 23-49.

### Workstream 0 (Lead) — deliverables

1. `rfmesh-contracts` package — **delivered** (Pydantic models + Protocols
   + enums + version pinning, smoke-tested, mypy-clean).
2. Five coordination documents: `ARCHITECTURE.md`, `INTERFACES.md`,
   `INHERITED_CONTEXT.md`, `WORKSTREAMS.md` (this), `AGENTS.md`.
3. `SALVAGE_AUDIT.md` with Thread 1's amendments folded in — **delivered**.
4. Three workstream bootstraps (A, B, C+D).
5. ADR template + first ADRs documenting binding decisions
   (`ADR-001-monorepo-uv-workspace`, `ADR-002-contracts-as-protocol`,
   `ADR-003-no-gnss-no-tdoa-no-magnetometer`, etc.).
6. Ongoing: contract change-control (review ADR proposals, bump
   `SCHEMA_VERSION`, broadcast to all workstreams).

### Workstream A — deliverables

**A is on the critical path because B/C+D consume A's `SyntheticReceiver`
to develop without hardware. A's first deliverable must be the simulator,
not the hardware path.**

1. **`SyntheticReceiver` first.** A `Receiver` (and `CoherentReceiver`)
   implementation that emits IQ from a configured emitter geometry — angle,
   range, frequency, SNR, multipath taps, per-channel phase offsets for
   coherent mode. Parameterised noise model. Documented and tested against
   closed-form cases (known angle in → angle out via L1 peak-find within
   tolerance). **Until this lands, B and C+D are blocked.** Target: first
   working version within the first sprint.
2. **`RTLSDRDevice` → `Receiver` Protocol port.** The salvaged
   `rtl_sdr`-subprocess implementation re-seated against the new Protocol.
   Logic preserved; lifecycle adapted (`open/configure/read/capabilities/
   close`). Tests come across.
3. **`SoapyReceiver`.** Generic `Receiver` covering RTL-SDR / HackRF /
   single-channel bladeRF / single-channel Pluto via SoapySDR. Two
   `Receiver` implementations is not duplication — see `SALVAGE_AUDIT.md`
   Part 4d's recommendation; `RTLSDRDevice` is the proven pre-flight path,
   `SoapyReceiver` is the on-site-flexibility path.
4. **Servo packages** (`rfmesh-servo`) — salvage near-whole; refactor only
   import paths and the exceptions split.
5. **Firmware salvage + S2 port.** Firmware moves over; ESP32-S2 port is
   a scoped ticket (~2–3 h per `INHERITED_CONTEXT.md` §1.1).
6. **`BladeRFCoherentReceiver`** + **`PlutoCoherentReceiver`.** Native
   bindings (libbladeRF, pyadi-iio); coherent `read_coherent(n)` returning
   `(n_channels, n)` aligned IQ; `calibrate()` handshake. **Hardware
   validation is best-effort pre-event** (Pluto+ delivery uncertain per
   `INHERITED_CONTEXT.md` §3.4); simulator-coherent mode is the developed
   path until then.
7. **Calibration tools.** Per-dongle (salvaged) and per-array (new — for
   L2 coherent phase/gain offsets).
8. **LoRa beacon firmware** per `lora_beacon_spec.md` — the controlled
   test emitter for pre-event Phase C work.

### Workstream B — deliverables

1. **L1 bearing estimator.** `BearingEstimator` implementer consuming
   `IQBlock` from a servo-swept antenna, producing `BearingReport`s with
   `method=Capability.L1_RSSI`. The salvaged `dsp.rssi` and `dsp.spectrum`
   are the substrate; new code is the sweep-aware peak-finder, the σ
   estimator (honest, per `INTERFACES.md` §3 `BearingReport`), and the
   integration with the servo driver's angle stream.
2. **Array-manifold module.** Steering vectors for ULA, UCA, and CUSTOM
   geometries per `ArrayConfig`. Forward-backward smoothing helper.
   Sample-covariance estimator with snapshot averaging.
3. **L2 MUSIC estimator.** `BearingEstimator` consuming `CoherentIQBlock`,
   producing `BearingReport`s with `method=Capability.L2_MUSIC`. Refuses
   to emit on uncalibrated input. Optional `raw_pseudospectrum` payload
   (binary format per `INTERFACES.md` §3).
4. **L2 MVDR null-steering.** The dual-use sibling. Same `R`; produces a
   weight vector that nulls a specified bearing. Demo path consumes this.
5. **L3 classification pipeline.** STFT-based feature extraction, a small
   CNN/ResNet baseline trained on synthetic data with hooks for real-IQ
   captures, ONNX export for RPi inference. Threat library: one module per
   `EmitterClass` member in `packages/rfmesh-ml/threats/`; the moat is the
   *open structure*, not the v1.0.0 contents.
6. **Regression test for the 25 dB SNR-invariant** per `INHERITED_CONTEXT.md`
   §5.1 — explicit, named, in the new test suite.

### Workstream C+D — deliverables

1. **`Fuser` implementer** (`rfmesh-fusion`) — Stansfield seed +
   Gauss-Newton MLE refinement, covariance from the Fisher information,
   `EllipseENU` from chi-square scaling, GDOP computation, `confidence_level`
   policy per `INTERFACES.md` §3. Outlier handling for `fallback_centroid`
   degenerate geometries.
2. **CoT publisher** (`rfmesh-cot`) — PyTAK-backed `CotPublisher`. Hostile-
   emitter marker for `FixEvent.position`, ellipse polygon from
   `confidence_ellipse_95`, classification overlay in remarks if available.
   Compatibility with FreeTAKServer.
3. **Node runtime** (`rfmesh-node`) — the asyncio service that wires
   `Receiver` → `BearingEstimator` → `Bearer.send_bearing` → fusion ingest.
   Capability detection at startup (intersects `NodeConfig.capabilities`
   with detected hardware; fatal on mismatch). Heartbeat emitter.
4. **Transport layer** (`Bearer` implementers) — `WifiBearer` (UDP +
   msgpack envelope salvaged from `mesh/protocol.py`), `LoraBearer`
   (compressed, drops `raw_pseudospectrum`), `BothBearer` (deduplicates).
5. **Fusion-server ingest** — the `aggregator.py` equivalent. Consumes
   `BearingReport`s from one or more `Bearer`s, time-batches per
   `FusionConfig.batch_window_ms`, calls `Fuser.fuse()`, publishes
   `FixEvent`s to CoT and to the dashboard.
6. **Ops dashboard** (`rfmesh-ops`) — live nodes, bearings (with σ
   wedges), MUSIC pseudospectra (where available), live fix + ellipse,
   per-node residuals, GDOP overlay. The demo surface per
   `ARCHITECTURE.md` §7.
7. **Demo replay app** (`apps/demo-replay`) — plays back recorded IQ
   through the entire pipeline. The demo contingency if live hardware
   misbehaves.
8. **Regression test for the subscriber-registration race** per
   `INHERITED_CONTEXT.md` §5.2 — explicit, named, in the new test suite.

---

## §3 Dependency order and parallelism

```
         ┌────────────────────────────────────────────────┐
         │ Workstream 0 (Lead): contracts + docs (Day 0)  │
         └────────────────────┬───────────────────────────┘
                              │ rfmesh-contracts frozen at 1.0.0
                              │
                ┌─────────────┴──────────────┐
                │                            │
                ▼                            ▼
   ┌────────────────────────┐    ┌────────────────────────────┐
   │ Workstream A:          │    │ Workstream C+D:            │
   │  SyntheticReceiver     │    │  Fuser against synthetic   │
   │  first (Day 0.5)       │    │  BearingReports (Day 1)    │
   │  → unblocks B and C+D  │    │  → independent of DSP      │
   └────────────┬───────────┘    └────────────┬───────────────┘
                │                              │
                │                              │
                ▼                              │
   ┌────────────────────────┐                  │
   │ Workstream B:          │                  │
   │  L1 estimator on       │                  │
   │  SyntheticReceiver     │                  │
   │  IQ (Day 1.5)          │                  │
   └────────────┬───────────┘                  │
                │                              │
                └──────────────┬───────────────┘
                               │
                               ▼
              ┌────────────────────────────────┐
              │ Workstream C+D: node runtime   │
              │ wires Receiver → Estimator →   │
              │ Bearer → Fuser → CoT (Day 3+)  │
              │ all against simulator          │
              └─────────────────┬──────────────┘
                                │
                                ▼
              ┌────────────────────────────────┐
              │ Workstream A in parallel:      │
              │ real RTLSDRDevice + Soapy +    │
              │ Coherent receivers + firmware  │
              │ S2 port + LoRa beacon          │
              └─────────────────┬──────────────┘
                                │
                                ▼
              ┌────────────────────────────────┐
              │ Hardware integration session   │
              │ per workstream — concentrated, │
              │ end of project                 │
              └────────────────────────────────┘
```

**Critical path:** Lead → A's `SyntheticReceiver` → B's L1 estimator → C+D's
node-runtime wiring. **Parallel:** C+D's fusion + CoT can start as soon as
the contracts are frozen (against ad-hoc synthetic `BearingReport`s, before
A's `SyntheticReceiver` is fully shaped). A's hardware path (RTLSDRDevice,
Soapy, Coherent receivers, S2 port, beacon) runs alongside everything else
and only converges at hardware integration.

**Phase C smoke test** (per `INHERITED_CONTEXT.md` §3.1) runs as a parallel,
non-blocking hardware-validation gate on Maciej's bench. If FAIL, its
diagnostics may update A's `SyntheticReceiver` noise/multipath models or
trigger an ADR; it does not pause B/C+D.

---

## §4 Cross-workstream coordination points

These are the only places where workstreams must agree on something
beyond the frozen contracts. Each is named here so the responsible parties
know to coordinate; each is also a candidate ADR if discussion is needed.

| # | Coordination | Workstreams | Resolution venue |
|---|---|---|---|
| 1 | `SoapyReceiver` vs proven `RTLSDRDevice` — both kept, both satisfy `Receiver`. Which is default in configs? | A (owner), B, C+D (consumers) | ADR after A has both implementations working |
| 2 | `raw_pseudospectrum` binary format — endianness and angular step are conventional, not enforced by validator | A or B (producer of MUSIC pseudospectrum), C+D (ops dashboard consumer) | Settled in `INTERFACES.md` §3 at 0.5° step, little-endian float32; consumers test |
| 3 | Calibration file format for arrays (`ArrayConfig.calibration_file`) | A (produces), B (consumes) | ADR before L2 is integrated |
| 4 | CoT marker type strings and remarks formatting for `EmitterClass` overlay | C+D (sole owner), Lead (review) | Documented in `rfmesh-cot/markers.py`; ADR if non-trivial |
| 5 | Demo scenario YAMLs (`scenarios/trench_demo.yaml`) — node positions, emitter timing, expected outcomes | C+D (owns scenarios), A (provides simulator hooks), Maciej (deployment knowledge) | Lead-reviewed; updated as Phase C results land |

Anything not in this table or in the contracts is **not** a cross-workstream
concern — agents resolve it inside their workstream without coordination.

---

## §5 Out-of-scope items (documented to prevent drift)

These are real concerns that **do not belong in any v1.0.0 workstream** and
must be parked rather than smuggled in via well-meaning tickets:

- **TDOA multilateration.** Architectural decision binding per
  `INHERITED_CONTEXT.md` §2.3. Parking-lot, future system version.
- **Vehicular / mobile DF.** Static deployments only. Parking-lot.
- **Audio / acoustic detection.** A separate project (FiberSense) for a
  separate event (EUDIS). Not in rfmesh. Do not reference.
- **Hardened enclosures, weatherproofing, military-grade packaging.**
  Demo hardware, not field-deployable kit.
- **Full multi-week ML training pipeline for L3.** v1.0.0 ships an open
  *structure*, not a trained-against-all-classes library. Stub profiles
  for the unverified classes (per `INTERFACES.md` §1 `EmitterClass`) are
  the v1.0.0 deliverable.
- **Anything mechanical / physical / RF-deployment.** Mast design, cable
  routing, antenna mounting, soldering, 3D printing, polarization,
  deployment ergonomics — **Maciej's domain entirely.** No agent reasons
  about these or proposes changes. Agents treat physical-world assumptions
  as facts.

Out-of-scope items live in `docs/adr/parking-lot/` if they need to be
discussed. The lead refuses tickets that drag them in.
