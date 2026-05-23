# rfmesh — Architecture

**Status:** binding. Edits only by the lead architect, via an ADR (`docs/adr/`).
**Audience:** everyone working on rfmesh — the lead, council subagents, Claude
Code workers, and Maciej as project owner.
**Date:** 2026-05-14 (last amended 2026-05-23 for ADR-021 directional-comms
reframing; the geolocation framing remains as a side-effect feature).
**Mirrors contracts at:** `SCHEMA_VERSION = "1.2.0"`.

This document is the *why*. It explains the small number of decisions that
every layer depends on, and the reasoning behind each. The *what* of any
given component lives in its package; the *interface contracts* it must meet
live in `INTERFACES.md`. Start here.

The document is short on purpose. If something is not stated as a binding
invariant, it is a layer's freedom to decide, and disagreements are
resolved by ADR — not by editing this file in a rush.

---

## §0 What rfmesh is

rfmesh is a **€250 directional radio that points itself, survives jamming by
pointing away from it, and triangulates the jammer as a free side-effect**
(per ADR-021, 2026-05-23). Each node carries an SDR and a directional Yagi
on a 1-axis pan servo. Two nodes that want to talk auto-acquire each other
(GPS-prior pointing + scan-and-stare refinement, ADR-019) and lock a
directional inter-node link with side-lobe rejection of off-axis co-channel
jammers as the binding tactical advantage. The geolocation pipeline (L1
amplitude DF + Stansfield/MLE fusion + ATAK markers + confidence ellipses)
is preserved as a **side-effect feature**: every peer-acquisition sweep
produces RSSI-vs-bearing data that drops bearings on any unknown emitter in
the band, and with 2+ nodes the same fusion stack draws an honest
confidence ellipse — the same hardware, two operational effects.

Targeted event: **BoTH3 Counter-Jamming Challenge 2** (Belgian MoD,
Commando Training Centre Marche-les-Dames, 22–24 May 2026).

The system is hardware-agnostic by *structure*, not by marketing: any SDR the
on-site partner pool yields — RTL-SDR V4, HackRF One, bladeRF 2.0 micro,
ADALM-Pluto+ — drops in behind the same software through a frozen Protocol.
Pre-event development runs entirely on the developer's own kit; on-site
integration is a configuration change, not a code change.

A note on the project name: the same name `rfmesh` also identifies the prior
codebase at `github.com/macwed/rf-mesh`, which is the parts donor (see
`docs/deprecated/SALVAGE_AUDIT.md`). Throughout this document, "rfmesh"
refers to the new workspace; the old repo is referred to as "the old repo"
or "rf-mesh".

---

## §1 The three capability layers (not hardware tiers)

The architecture has three capability layers that any node can independently
opt into. They are layers of *what the node does with its samples*, not tiers
of *what hardware it has*. A node declares which layers it runs; the node
runtime checks declared capabilities against detected hardware at startup and
fails loudly if they do not match.

**L1 — Detection.** Amplitude-comparison direction finding with a
servo-rotated directional antenna and a single-channel SDR. The node sweeps
the antenna in azimuth, samples IQ at each angle, computes RSSI, finds the
peak. Output: one `BearingReport` per sweep, with a 1-σ uncertainty in the
5–15° band, depending on SNR, antenna HPBW (50° for the ATK-10), and local
multipath. Hardware-agnostic: RTL-SDR, HackRF, or any other single-channel
SDR through the `Receiver` Protocol.

**L2 — Precision DF.** Phase-coherent subspace direction finding on a two-or-
more-element antenna array, using one of three subspace estimators:
**MUSIC** (eigendecomposition of `R`, contract enum `L2_MUSIC`), **Capon /
MVDR-spectrum** (peak of `1/(a^H R^-1 a)`, contract enum `L2_CAPON` added
in SCHEMA_VERSION 1.1.0 per ADR-008), and the dual-use sibling **MVDR
null-steering** (contract enum `L2_MVDR_NULL`). Requires a phase-coherent
multi-channel SDR (bladeRF 2.0 micro or ADALM-Pluto+, both AD936x-family,
single RFIC, coherent by construction). Output: a `BearingReport` with 1–3°
uncertainty under benign conditions for MUSIC / Capon; null-steering does
*not* emit `BearingReport`s — it produces a receive weight vector that
nulls a specified off-look direction. The *same* sample covariance matrix
R that the DoA estimators consume can also be inverted (MVDR distortionless
weight `w = R^-1 a / (a^H R^-1 a)`) to synthesize a spatial null toward an
active jammer. The framing is **anti-desense** (protecting our own L2
coherent DF channel from being desensitised by a co-channel jammer), **not
ECM** — we are not transmitting through this array. ADR-008 §D8 caps UI
text quoting null depth at ≤ 20 dB; the slide caption is "−18 dB" with a
"15–20 dB typical, up to ~25 dB with fresh calibration" rehearsed band.
**One matrix, two products.**

**L3 — Classification.** Edge ML emitter classification on a Raspberry Pi 4,
typically a small CNN over STFT spectrograms. Labels the emitter (ELRS,
Crossfire, GSM, Pole-21, Volnorez, DroneID, UNKNOWN) with a confidence in
[0, 1]. This is the project's competitive moat: an *open, extensible* threat
library, in a market where commercial gear (RfPatrol Mk2) ships closed
libraries at €5k+ and military gear (Bukovel-AD) keeps its library classified.
L3 is SDR-agnostic; it sees IQ regardless of which `Receiver` produced it.

The fusion server consumes `BearingReport`s indifferent to layer — an 8°-σ
L1 bearing and a 1.5°-σ L2 bearing differ only in their `azimuth_sigma_deg`
and `method`, and the weighted-least-squares fix uses sigma as the inverse
weight automatically. This is the architectural move that lets the mesh be
heterogeneous: a single fix can combine cheap RTL-SDR L1 nodes and expensive
phase-coherent L2 nodes, each contributing according to its honest
uncertainty. L3 labels ride along on the same `BearingReport`, and the fusion
output (`FixEvent`) inherits a consensus class if the contributing nodes
classified.

---

## §2 The three axes of variability, each absorbed in exactly one place

The hardware reality the system must handle is uncertain along three
orthogonal axes. The architecture is designed so each axis is fully absorbed
inside one component, and never leaks into DSP or fusion logic.

**Axis 1 — SDR hardware type.** RTL-SDR V4 / HackRF One / bladeRF 2.0 micro /
ADALM-Pluto+ differ in sample rate, ADC bit depth, channel count, gain
stages, and calibration availability. **Absorbed in `rfmesh-sdr` behind the
`Receiver` / `CoherentReceiver` Protocols.** DSP code receives IQ; it never
branches on what produced it.

**Axis 2 — per-node capability.** A node with an RTL-SDR can do L1, possibly
L3. A node with a bladeRF in coherent mode can do L1, L2, possibly L3. A
node with a Raspberry Pi added can do L3 regardless. **Absorbed in
`rfmesh-node` by capability detection at startup.** The node intersects its
declared `NodeConfig.capabilities` with what the detected SDR + array
physically supports, builds the right processing pipeline, and tags every
`BearingReport` with the `method` used.

**Axis 3 — node count.** Two nodes give a single fix with a stretched ellipse.
Three give an over-determined fit and per-node residuals for self-diagnosis.
More give statistical confidence and resilience to dropouts. **Absorbed in
`rfmesh-fusion` by being indifferent to N**: the Stansfield weighted-LS
estimator accepts any iterable of N ≥ 2 bearings, and the geometry-dilution
check (GDOP) decides whether the resulting fix is worth publishing. There is
no "expected number of nodes" constant anywhere in the system.

The discipline this requires is that **no workstream depends on another
workstream's implementation, only on the frozen contracts.** All three axes
collapse to one rule, and the rest of this document is consequences of it.

---

## §3 The contracts are the contract

`packages/rfmesh-contracts/` is the **single source of truth** for everything
that crosses a workstream boundary: data shapes (Pydantic models), behavioural
shapes (`typing.Protocol` classes), enumerations, the schema version, and the
two shared geometric value types. It depends only on `pydantic` and `numpy`.
It contains no runtime logic, no I/O, no inheritance from anything else in
rfmesh. The dependency graph of the entire project is a **star with
rfmesh-contracts at the centre** — every workstream imports from it, no
workstream imports from any other.

This is what lets many AI coding agents work in parallel without their work
colliding. The contracts are the contract — literally. The semantics of each
type, the producers and consumers, and the units/conventions on every field
are documented in `INTERFACES.md`. The wire format is whatever an envelope
layer chooses (the salvaged length-prefixed MsgPack codec is the default);
the meaning is fixed here.

**Governance.** Only the lead edits `rfmesh-contracts`. A workstream agent who
believes a contract must change writes a CHANGE-REQUEST ADR under
`docs/adr/` and stops — it does not edit the contract. Every accepted change
ends with the lead bumping `SCHEMA_VERSION` in `version.py`, which — because
every message pins `schema_version` as a `Literal[SCHEMA_VERSION]` — is
caught by `mypy` in every workstream still built against the old version.
This is the type-system tripwire that protects parallel agents from silent
drift. `AGENTS.md`'s "Seven Binding Invariants" enforces the procedural side; the
`Literal` enforces the technical side. Together they make contract drift
hard to introduce by accident.

A side consequence worth stating: `rfmesh-contracts` carries **no exception
hierarchy**. Exceptions live with the workstream that raises them. The
contracts package stays pure data + Protocol. This is one of the small
decisions that keeps the dependency star clean.

---

## §4 The simulator is a first-class citizen, not a test fixture

Software in this project does not wait on hardware. Workstream A's first
deliverable is a `SyntheticReceiver` that *implements the `Receiver` Protocol*
and emits IQ generated from a configured emitter geometry, with parameterised
noise, multipath, and (for coherent mode) per-channel phase offsets that the
L2 calibration routine must back out. It is not a mock. It is a peer of every
hardware-backed `Receiver`. The same DSP code that runs on real samples runs
on synthetic ones with no code change — only a config-string swap.

This is what makes the multi-agent build viable. Workstreams B (DSP), C
(fusion + CoT), and D (node runtime) can build, test, and validate
end-to-end against ground truth without any radio attached. Hardware
integration becomes a single concentrated session per workstream, near the
end, against a fully tested pipeline — not a dependency that blocks progress
every time a piece of metal misbehaves. This is also why the previous
project's hardware smoke test became the schedule's bottleneck: software was
written *against* hardware. Here it is written against the simulator and
*validated on* hardware.

**Honesty constraint.** A simulator that lies makes the tests it backs into a
fiction. The simulator's noise, multipath, and calibration-error models are
documented, their parameter ranges chosen to match the conditions in
`INHERITED_CONTEXT.md` (especially Phase C uncertainty around multipath
dominance and antenna pattern deformation), and they are themselves tested
against known-good closed-form cases. The simulator is treated as production
code, reviewed under the same gates as everything else.

---

## §5 Hardware-validation runs in parallel and is gated honestly

The previous project's Phase C smoke test — the one that asked whether the
bearing-scan physics actually works on this antenna, this servo, this SDR,
in this environment — was never executed. The new architecture does *not*
relax that question. It changes how it is scheduled.

**Phase C runs as the first hardware-validation gate, on Maciej's bench, in
parallel with software development.** It is not on the software critical
path; the simulator unblocks the software regardless. But it is tracked as a
real risk gate, with an explicit branching decision: if Phase C reveals that
bearing-scan physics fundamentally does not work in this hardware + site
configuration (multipath dominance, polarization issue, antenna pattern
deformation from the bracket — the candidates are enumerated in
`INHERITED_CONTEXT.md`), the architectural response is documented in advance,
not improvised. The procedure already exists: `tower_sanity_playbook.md`,
inherited from Thread 1's outputs (Salvage Audit, Part 6). It moves into the
new repo's `docs/` and is executed as-is.

The same gating philosophy applies to L2: phase-coherent capture is **not**
on the critical path. L2 lands if and only if the calibration handshake
works on either Pluto+ (if it arrives before BoTH3) or borrowed bladeRF
(on-site). If it does not land, the system ships L1+fusion+CoT — already a
credible BoTH3 submission per the architecture above — with L2 as a
documented extension. This is the explicit no-shortcuts position: a working
L1 demo with honest error budgets beats a half-working L2 demo with magic.

The full set of inherited hardware quirks — TriLan cable management, MG996R
clones with unknown pulse ranges, vertical polarization as a binding fact
(20–30 dB cross-pol loss if violated), the negative-result vector list that
spares re-exploration of blind alleys, the unidentified cellular reference
frequency — lives in `INHERITED_CONTEXT.md`. It is read before any hardware
design decision, the same way `INTERFACES.md` is read before any contract
boundary decision.

---

## §6 Why no GNSS, no TDOA, no magnetometer — and what that buys us

Three small structural decisions that have outsized consequences for the
system's resilience story.

**No GNSS on the critical path.** Node positions are set by survey at
deployment time and written into `NodeConfig.position` with an honest
`sigma_m` (telephone GPS is ±5–10 m, which is far smaller than the bearing-
driven position uncertainty at 1–5 km range — the dominant term). Time
synchronization between nodes is plain NTP over the local mesh network,
which gives ~10 ms — sufficient for AoA cross-fixing, which only needs
consistent bearing-report timestamps, not ns-level alignment. **GNSS modules
are not required.** If they are present (organizer pool has ATGM336H), they
are a convenience, never a dependency.

**No TDOA.** Time-difference-of-arrival multilateration is what would require
ns-level synchronization, GPS-disciplined oscillators, and known USB/cable
latencies. The architecture deliberately commits to AoA cross-fixing instead,
which gets the same kind of positional output from much looser timing. TDOA
is a parking-lot upgrade for a future system version with budget and time;
not in scope here.

**No magnetometer for heading.** The 6-axis IMUs in Maciej's inventory
(GY-6500 / MPU-6500) do not have a magnetometer; 9-axis ones would, but a
magnetometer at the mast of an SDR rig measures local magnetic distortion
from servos, cables, and the SDR itself — not Earth field. Calibration is
fragile and per-site. **Node heading is set by surveyed alignment:** point
the Yagi at a known visible landmark (mast, chimney, building corner), read
the azimuth off a map, write it into `NodeConfig.heading_deg`. The
mechanical zero-stop in the servo firmware (already implemented, see
`SALVAGE_AUDIT.md` §1) gives an absolute angular reference relative to the
mount. This delivers ~1–2° heading accuracy with no extra hardware, and is
not subject to magnetic distortion. IMUs become an optional tilt-monitoring
health-check (`NodeStatus.healthy` / `status_detail`), not a heading source.

**What this buys.** The mesh has no critical dependency on any of the three
things adversary EW preferentially attacks: GNSS (jammed first on the
front), time-sync infrastructure (a wired network with PTP-aware switches —
which we do not have anyway), and magnetometer integrity (which a maritime
EW environment trashes). The system runs in a GNSS-denied environment by
construction. This is not an excuse for missing budget; it is a pitch slide.
`NodeStatus.gnss_locked` is a *separate, visible* flag so a deployment can
*observe* GNSS jamming as an EW indicator, while the mesh continues to
function on its own positioning and timing. The contracts already model this.

---

## §7 What the demo shows, and why each piece is there

A BoTH3 demo for an RF/EW-expert jury must show *engineering*, not magic.
The architecture is shaped to make the following live, in front of judges:

- **Live L1 RSSI sweep** — polar plot of RSSI vs azimuth on each node, peak-
  fit bearing with a σ-wedge. Shows the physics is real, not a black box.
- **Live MUSIC pseudospectrum** — 1-D plot of P_MUSIC(θ) on each L2 node,
  peaks annotated. The single piece of evidence that subspace DF is actually
  running on coherent IQ. Without this, an expert in the room cannot
  distinguish L2 from "we said L2".
- **A/B precision panel** — same emitter, same instant, L1 bearing vs L2
  bearing side by side: *"L1: 8.2° ±3°. L2: 1.4° ±0.6°."* The moment the
  array's value becomes visible.
- **Cross-fix with confidence ellipse** — 2-D ENU plot of each node's
  bearing line and its uncertainty cone, the least-squares intersection, the
  95% covariance ellipse from `FixEvent.confidence_ellipse_95`, the centroid.
  GDOP annotated on the plot.
- **Residuals panel** — per-node bearing residual after the fit, from
  `FixEvent.residuals_deg`. A node whose residual is many σ from zero is
  highlighted as a probable outlier (multipath, calibration). The system
  self-diagnoses; this is what an expert audience expects to see.
- **GDOP heatmap** — for current node positions and a candidate emitter
  region, a colormap of GDOP across the area. Honest because it shows
  *where* the system is precise and where it isn't — the difference between
  a demo and an engineering artefact.
- **Null-steering A/B** — the dual-use slide. Same array, same R. MUSIC peak
  at θ_jammer. Apply MVDR weights `w = R⁻¹ a(θ) / (a^H R⁻¹ a)`, replay,
  show the receive pattern with a 20+ dB null at θ_jammer. Caption: *"One
  matrix, two products: target geolocation for kinetic effect, null-steering
  for own-comms protection."*
- **CoT marker live in ATAK** on a tablet, with the confidence ellipse
  rendered as a polygon. The marker shrinks visibly as nodes are added; if
  a node is killed mid-demo (cable pull, antenna disconnect), the marker
  expands honestly — the system *degrades*, it does not *lie*. Plugs into
  the real C2 stack the operator already knows.
- **L3 classification overlay** — emitter-class label and confidence next to
  the marker. *"ELRS uplink, 0.92."* The open library shown as a YAML
  threat-profile file the operator can read.

Each of these is the architectural responsibility of one workstream, all
producing the same data types (`BearingReport`, `FixEvent`) for the same
fusion server, displayed in the same ATAK marker. The demo is not bolted on
at the end. It is the architecture made visible.

---

## §8 What is binding and what is not

**Binding.** Every statement in §1–§6, and the contracts in
`packages/rfmesh-contracts/`. Changes go through ADRs and `SCHEMA_VERSION`
bumps; workstream agents propose, lead disposes.

**Not binding.** Anything about the *internal structure* of a workstream's
package — how `rfmesh-dsp` organizes its modules, whether `rfmesh-fusion`
solves Stansfield seed then Gauss-Newton or jumps straight to ML, how the
node runtime sequences its asyncio tasks. Those are the workstream's
freedoms, scoped by the contracts they must honour and the workstream's own
acceptance tests. The lead reviews; the lead does not micromanage.

**Out of scope.** Anything that would be lovely but is not on the path to a
working BoTH3 demo: TDOA, vehicular DF, FiberSense integration, a full
threat-library training pipeline, hardened enclosures, dedicated power
distribution. These have homes in `docs/adr/` if they need to be discussed,
in a "parking lot" ADR. The point of saying "out of scope" here is so an
Opus agent does not, in good faith, spec a ticket that drags one of them in.

---

## §9 Documents in the coordination package

For navigation:

- **`ARCHITECTURE.md`** (this file) — the *why*. Binding invariants.
- **`INTERFACES.md`** — the *what*. Semantic dictionary of every contract type.
- **`AGENTS.md`** — the *how*. Rules for AI agents: the Seven Binding
  Invariants, allowed commands, escalation, council protocol.
- **`CLAUDE.md`** — the *quick reference*. Claude Code project autonomy
  policy + council protocol; binds at every session start.
- **`README.md`** — the *what is this repo*. One-page orientation; points
  here.
- **`docs/DOC_INDEX.md`** — the *pointer index*. Table of contents naming
  every section, ADR, and binding fact so an agent can read selectively
  instead of scanning the whole tree.
- **`docs/adr/`** — append-only architectural-decision record.
- **`docs/MANUAL.md`** — operator-facing setup / dev / demo guide.
- **`docs/ADVANTAGES.md`** — the 8 pitch advantages (B7). Reordered per
  ADR-021.
- **`docs/wire-protocols/servo_uart_v1.md`** — frozen wire spec.
- **Appendix A** below — folded interface conventions worth reading
  alongside `INTERFACES.md`.
- **Appendix B** below — folded hardware quirks + binding regression
  anchors from the retired `INHERITED_CONTEXT.md`.

Read order for someone joining cold: this file (`ARCHITECTURE.md`) →
`INTERFACES.md` → `AGENTS.md` → `docs/DOC_INDEX.md` for selective
deep-dives. `docs/deprecated/` carries the historical files
(`WORKSTREAMS.md`, `SALVAGE_AUDIT.md`, full `INHERITED_CONTEXT.md`, sprint
logs, retired tickets) — reference only; **not binding**.

---

## Appendix A — Universal contract conventions (mirror of `INTERFACES.md` §0)

These conventions are binding on every contract in `rfmesh-contracts/`.
Pydantic docstrings are canonical; this appendix is the quick-lookup
version so an agent does not have to load all of `INTERFACES.md` to
recall a sign or a unit.

- **Time.** `t_unix_ns` = integer nanoseconds since Unix epoch, UTC.
  Integer (not float — float64 loses ns precision past ~104 days).
- **Geodetic.** WGS-84. Latitude / longitude in **decimal degrees**;
  height in **metres above WGS-84 ellipsoid (HAE)**, not above mean sea
  level. Matches u-blox + CoT directly — no datum conversion in this
  codebase.
- **Local tangent plane.** **ENU** (East-North-Up), metres, right-handed.
- **Azimuths and bearings.** Degrees, **true north = 0, clockwise
  positive, range [0, 360)**. Never magnetic, never radians on wire.
- **Angular uncertainty.** Degrees, **1-σ** (one standard deviation).
  95% ellipses derived via chi-square scaling from the covariance.
- **Power and SNR.** Decibels. SNR is dB **above this node's own
  noise-floor estimate**, not absolute. **No `dBm` field anywhere** —
  none of the SDRs in scope (RTL-SDR V4, HackRF One, bladeRF, Pluto+)
  is power-calibrated; `ReceiverCapabilities.is_power_calibrated`
  is the checked fact.

Validation discipline:

- Every Pydantic model uses `ConfigDict(frozen=True, extra="forbid")`.
- Numeric ranges enforced by `Field(...)` constraints; cross-field
  invariants enforced by `@model_validator(mode="after")`.
- Optional fields (`X | None`) mean "datum not available" — never a
  silent default.
- Every wire message carries `schema_version: Literal[SCHEMA_VERSION]`;
  any bump is caught at type-check time across every workstream.

For the full per-type semantics (every field on `BearingReport`,
`FixEvent`, `NodeStatus`, every config, every Protocol) read
`INTERFACES.md`.

---

## Appendix B — Hardware quirks + regression anchors (folded from retired `INHERITED_CONTEXT.md`)

These are facts about hardware, RF physics, and prior-project failures
that **cannot be derived from reading the current code**. Binding.

### B.1 Servo cal: MG996R clone pulse range is unknown until calibrated per axis

MG996R clones deviate from the nominal 500–2500 µs ±90° hobby
convention; deviation is undocumented on the package. Every axis must
be calibrated; firmware exposes a `cal` command + per-axis NVS table
(`cal_types.h`, `calibration.c`). Concretely:

- DSP code consumes calibrated angle, never raw PWM.
- A node refuses to boot into L1 against an uncalibrated axis (B3).
- No hard-coded pulse-to-angle constants anywhere in DSP code.

### B.2 SDR amplitude readings are not absolute power

No SDR in scope (RTL-SDR V4, HackRF One, bladeRF 2.0 micro, ADALM
Pluto+) is power-calibrated. Contracts carry no `dBm` field. Any UI
label of "dBm" is a UX bug. SNR is always "dB above this node's own
noise floor". Honest path to absolute power would require a one-point
on-site calibration against a signal generator — not done.

### B.3 Why AoA, not TDOA

TDOA needs ns-level sync (GPS-disciplined oscillators + known cable
latencies); both jam-first under EW. AoA cross-fixing needs only that
the fusion batch window be wider than inter-node clock skew (NTP over
mesh = ~10 ms; default `FusionConfig.batch_window_ms = 100`). Plus
AoA degrades to "ellipse wider" under multipath, while TDOA degrades
to "fix is wrong". TDOA is parked for a future system, not v1.

### B.4 Why no magnetometer for heading

A magnetometer at the mast measures local field from servos, cables,
SDR PSU, not Earth field. Calibration is per-site and per-cabling.
Heading-by-aim (point Yagi at a known visible landmark, read azimuth
off the map, write into `NodeConfig.heading_deg`) gives ~1–2° with
no extra hardware. The mechanical zero-stop in firmware links bracket
frame to geographic. 6-axis IMUs in inventory become optional
tilt-monitoring (`NodeStatus.healthy`), not heading.

### B.5 GNSS is observed, not depended on

The mesh has no critical dependency on GNSS (which is jammed first on
the front). `NodeStatus.gnss_locked` exists as a separate flag so a
deployment can *observe* GNSS jamming as an EW indicator while the
mesh continues to function on surveyed positions + NTP timing. This is
a slide-worthy advantage, not a workaround.

### B.6 Agent push-back authority (procedural, still binding)

When a ticket or instruction conflicts with binding facts (this
appendix, `AGENTS.md` §1, `INTERFACES.md` §0), the agent **stops with
evidence and surfaces the conflict** — never papers over it with a
silent workaround. Inherited from the prior project's two real bug
catches (§B.7, §B.8); preserved deliberately as a quality gate.

### B.7 Regression anchor — the 25 dB SNR-invariant error

Prior project proposed a test invariant `in_band_snr >= peak_snr - 5`;
for a CW signal this is wrong by **25 dB** (peak SNR concentrates all
signal power into one FFT bin while in-band SNR spreads it across the
bin width). **Binding regression test for `rfmesh-dsp`:** a CW input
at known SNR validates `peak_snr_db` and `in_band_snr_db` against
closed-form expectations to within 0.5 dB. The numeric relationship
between them is *computed*, never assumed.

### B.8 Regression anchor — the subscriber-registration race

Prior aggregator's `subscribe_measurements()` had a window where a
subscriber registered after a measurement arrived but before the
dispatch loop iterated would miss the just-arrived measurement.
**Binding regression test for `rfmesh-fusion` / `rfmesh-node` ingest:**
a subscriber registering immediately before a known burst of
`BearingReport`s arrives receives every report in the burst. No
first-N-dropped, no race-window.

### B.9 Open hardware-validation items (status snapshot)

These were "open" in the prior project; current state:

- **Phase C smoke test** — *DONE.* Mast C is the empirical anchor per
  ADR-014; simulator calibrated against the measured behaviour
  (`scenarios/mast_c_reference.yaml` + `packages/rfmesh-sdr/tests/test_c4_mast_c_calibration.py`).
  The original `tower_sanity_playbook.md` lives in `docs/deprecated/hardware/`
  for historical reference.
- **Controlled emitter beacon (LoRa)** — *DONE.* Firmware in `firmware-beacon/`
  per `firmware-beacon/lora_beacon_spec.md`.
- **Pluto+ delivery** — *still uncertain.* L2 phase-coherent DF is
  developed against the coherent `SyntheticReceiver`; ships as
  hardware-validated only if Pluto+ or borrowed bladeRF arrives in time.
- **Vehicle-mount IMU stabilization** — *out of scope for v1.0* per
  ADR-021 §"V1.0 scope boundary."
- **Cold-start peer discovery in EMCON** — *unsolved.* Deferred to
  ADR-019 follow-up. V1 cold-start assumes one omni LoRa handshake
  to bootstrap.
