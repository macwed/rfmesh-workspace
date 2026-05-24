# rfmesh — Interfaces

**Status:** binding. Mirrors frozen state of `packages/rfmesh-contracts/`.
Edits only by lead architect, lockstep with contracts package.
**Audience:** every workstream agent and reviewer. Dictionary you
consult when need to know *what field means*, not just type.
**Date:** 2026-05-14.
**Mirrors contracts at:** `SCHEMA_VERSION = "1.3.0"`.

Doc does not duplicate Pydantic schemas — those authoritative, read directly for field names, types, validators. Doc carries what schemas cannot: **meaning of each field, who produces, who consumes, units, validity in wider system, boundaries.** Schema vs doc disagree: schema wins, doc is bug. Two readers disagree on field meaning: doc is tiebreaker.

Governance — who may change contract, how — see `ARCHITECTURE.md` §3
and `AGENTS.md` § "The Five Invariants".

---

## §0 Universal conventions

Apply across every type in contracts package. Not restated per-field.

**Units and physical conventions** (binding, never negotiable on wire):

- *Time.* `t_unix_ns` is integer nanoseconds since Unix epoch, UTC.
  Integer, not float — float64 loses nanosecond resolution past ~2⁵³ ns
  (≈ 104 days). Producers compute from `time.time_ns()` or
  `time.clock_gettime_ns(time.CLOCK_REALTIME)`; consumers treat as
  opaque ordering and batching key.
- *Geodetic coordinates.* WGS-84. Latitude / longitude in **decimal degrees**.
  Height in **metres above WGS-84 ellipsoid (HAE)**, not above mean sea
  level. Matches u-blox GNSS output and CoT/ATAK input directly — no
  datum conversion ever in this codebase.
- *Local tangent plane.* **ENU** (East-North-Up), metres, right-handed. Used
  for fusion geometry (Stansfield, MLE, GDOP) and confidence ellipse.
  Each operation picks one ENU origin (typically centroid of deployed
  nodes); ellipse interpreted relative to accompanying fix position, so origin travels implicitly.
- *Azimuths and bearings.* **Degrees, true north = 0, clockwise positive,
  range [0, 360).** Never magnetic, never radians, on wire. Per-sensor
  magnetic or platform-relative conversions happen at node edge.
- *Angular uncertainty.* Degrees, **1-σ** (one standard deviation).
  95% confidence ellipse fields are exception, explicitly named, derived
  from covariance via chi-square scaling.
- *Power and SNR.* Decibels. SNR is dB **above node's own noise-floor
  estimate**, not absolute. **No `dBm` field anywhere.** None of
  SDRs in scope (RTL-SDR V4, HackRF One, bladeRF 2.0 micro, Pluto+) is
  power-calibrated; claiming absolute dBm to RF/EW jury = credibility own-goal. `ReceiverCapabilities.is_power_calibrated` is checked fact, not tribal assumption.

**Validation discipline** (binding, applied uniformly):

- Every Pydantic model uses `ConfigDict(frozen=True, extra="forbid")`.
  Message immutable once constructed, typo'd or stale field is loud error at parse time — never silently ignored. Data-layer half of Invariant 4 ("No silent fallbacks") from `AGENTS.md`.
- Numeric ranges (lat/lon, azimuth, gain, frequencies) enforced by Field
  constraints. Cross-field invariants (L2 capability requires array;
  ULA requires `element_spacing_m`; LoRa bearer requires port) enforced
  by `@model_validator(mode="after")`.
- Optional fields (`X | None`) are *deliberate*: `None` means "datum
  not available" (not measured / not classified / not present on
  bearer). Different statement from default value. Producers must
  not paper over absence with silent default.

**Versioning** (binding, single most important type-system mechanism):

Every wire-format message carries `schema_version: Literal[SCHEMA_VERSION]`.
Because `SCHEMA_VERSION` is module-level constant, `Literal` resolves
at type-check time. Workstream built against old contract version that
constructs `BearingReport` after contract bump is `mypy` error in that
workstream's own CI — drift caught locally before integration. Operational effect: lead bumps `SCHEMA_VERSION`, every dependent
workstream's `mypy` immediately tells what to update, no message of
wrong version ever reaches wire.

`SCHEMA_VERSION` is semantic: MAJOR.MINOR.PATCH. PATCH is doc-only. MINOR is
additive and backward-compatible (extra optional field, extra enum
member that consumers can treat as "unknown"). MAJOR is breaking and
requires all workstreams to update in lockstep. Current value in
`version.py`; only accepted ADR bumps it.

---

## §1 Enumerations (`rfmesh_contracts.enums`)

Every enum is `str` enum. String value appears in YAML configs,
JSON-on-wire, and CoT remarks — renaming value is MAJOR bump.

### `Capability`

What single mesh node can do with hardware it has. Declared by
operator in `NodeConfig.capabilities`; intersected at node startup with what
detected SDR + array physically supports; mismatch fatal at boot,
never silent downgrade.

- `L1_RSSI` (`"l1_rssi"`) — amplitude-comparison DF via servo-rotated
  directional antenna and single-channel SDR. Per-bearing uncertainty
  typically 5–15° in benign environments, wider in heavy multipath.
- `L2_MUSIC` (`"l2_music"`) — phase-coherent subspace DF on 2+ element
  array. Requires `CoherentReceiver` hardware and successful array
  calibration. Per-bearing uncertainty 1–3° under benign conditions.
- `L2_CAPON` (`"l2_capon"`) — phase-coherent Capon (MVDR-spectrum) DF on
  same array. Same R as MUSIC, different DoA estimator (peak of
  `1/(a^H R^-1 a)` instead of MUSIC's noise-subspace projector). More
  robust than MUSIC in small-snapshot / low-SNR regimes. Per-bearing
  uncertainty 1–3° under benign conditions. **Added in SCHEMA_VERSION
  1.1.0 (ADR-008)**; previously, WS-B-004's Capon estimator emitted
  `L2_MVDR_NULL` under docstring caveat.
- `L2_MVDR_NULL` (`"l2_mvdr_null"`) — **null-steering** (dual-use
  sibling of `L2_MUSIC` / `L2_CAPON`). Same R inverted via MVDR
  distortionless-response weight formula `w = R⁻¹ a / (a^H R⁻¹ a)` (`a`
  is look-direction steering vector) to synthesise spatial null
  on any strong off-look source present in R — typically co-channel
  jammer. Protects project's own L2 coherent DF channel from
  desensitisation by jammer while system continues to produce
  bearings on it (**anti-desense, not ECM**). Does NOT emit
  `BearingReport`; is receive-weight synthesiser invoked as
  operator action. Node declaring `L2_MVDR_NULL` advertises
  capability; no streamed wire-format product emitted in v1.1.0.
  Implementation: `rfmesh_dsp.l2_null_steering` (WS-B-007). Same R, same array.
- `L3_CLASSIFY` (`"l3_classify"`) — emitter classification by edge ML. Needs
  compute node (Raspberry Pi class), SDR-agnostic.
- `L1_REFUSED_PROMINENCE` (`"l1_refused_prominence"`) — **capability state,
  not bearing method.** Added in SCHEMA_VERSION 1.2.0 (ADR-013, G4). A
  `BearingReport` carrying `method = L1_REFUSED_PROMINENCE` is the
  wire-level surface for an L1 amplitude-sweep refusal: estimator
  inspected sweep, declined to emit a bearing (prominence-gate
  failure, saddle, vertex out of window, singular covariance,
  non-finite variance, under-populated sweep). Free-form cause on
  `BearingReport.refusal_reason`. Direction sentinels:
  `azimuth_deg = 0.0`, `azimuth_sigma_deg = 180.0` (infinite-uncertainty
  equivalent — contract requires both fields present, but consumers
  MUST branch on `method` first). Fuser skips these reports; ops
  dashboard renders refusal symbol + reason instead of sigma wedge.
- `COMMS_DSSS` (`"comms_dsss"`) — DSSS directional-comms participation.
  Added in SCHEMA_VERSION 1.3.0 (ADR-025). Requires both RX and TX paths
  on the configured SDR (HackRF One, ADALM-Pluto+, BladeRF 2.0 micro);
  RTL-SDR V4 is RX-only and a node declaring `COMMS_DSSS` on RTL-SDR
  is a fatal startup error (B3). **Mutually exclusive with DF
  capabilities** (`L1_RSSI`, `L2_MUSIC`, `L2_CAPON`, `L2_MVDR_NULL`)
  in v1.3.0 — a node runs DF mode OR comms mode, not both
  concurrently on one SDR/Yagi (selected by CLI flag / config).
  `L3_CLASSIFY` may coexist with `COMMS_DSSS` (SDR-agnostic
  classification on tapped IQ). Concurrent DF + COMMS deferred to
  a future ADR.

**Producer:** operator (via `NodeConfig`) for declared capabilities;
the L1 estimator path produces `L1_REFUSED_PROMINENCE` on
`BearingReport.method` at refusal events. **Consumer:** node
runtime (intersects with hardware), dashboard (filters/labels),
fusion (skips `L1_REFUSED_PROMINENCE`, weights everything else),
`BearingReport.method` field (reports which capability produced bearing
or refusal).

### `EmitterClass`

Output label of L3 classifier. *Open, extensible threat library* —
project's moat. Adding member = MINOR bump, must be paired with threat-profile module under `rfmesh-ml/threats/` plus docs entry in `docs/threat-library.md`.

- `UNKNOWN` (`"unknown"`) — **mandatory, load-bearing.** Classifier ran
  and genuinely not confident enough to assign label. Different from
  `None` (classifier did not run at all) — see `None`-versus-`UNKNOWN`
  distinction in `BearingReport.emitter_class` below.
- `ELRS` — ExpressLRS R/C control link (868/915 MHz or 2.4 GHz, LoRa-based FHSS).
- `CROSSFIRE` — TBS Crossfire R/C control link (868/915 MHz, long-range FHSS).
- `GSM_JAMMER` — GSM-band handset emission, analog of IED
  command-detonation phone. Distinctive in *context* (lone uplink burst from
  static, non-infrastructure location), not in waveform.
- `POLE21` — Pole-21 GNSS jamming complex. **Stub at v1.0.0**: profile
  module is documented placeholder pending real-IQ capture. Contract
  shape stable; classifier will not emit label until profile trained.
- `VOLNOREZ` — Volnorez vehicle-mounted counter-FPV jammer. Stub, same
  status as `POLE21`.
- `DRONEID` — DJI DroneID / OcuSync downlink beacon (Mavic-class).

**Producer:** L3 classifier in `rfmesh-ml`. **Consumer:**
`BearingReport`, `FixEvent` (consensus across contributing nodes),
CoT marker remarks, ops dashboard.

### `ConfidenceLevel`

Coarse, **human-facing** confidence band attached to `FixEvent`. Used
for ATAK marker styling and one-word annotations on ops dashboard.

- `HIGH` — geometry strong (low GDOP, tight ellipse, residuals consistent)
  and/or classification probability high. Safe to action.
- `MEDIUM` — usable but caveated: moderate GDOP, or single redundant
  bearing, or mid-probability classification. Cue further collection.
- `LOW` — weak: near-collinear geometry, ellipse larger than operational
  tolerance, or low-probability label. Report, do not action alone.

**Producer:** `rfmesh-fusion`, derived from quantitative evidence per
policy in §3 (`FixEvent.confidence_level`). **Consumer:** CoT
publisher, dashboard. Not *the truth* — truth is
`FixEvent.covariance_m2`, `confidence_ellipse_95`, `gdop`, and
`classification_confidence`. Band is discretized display layer.

### `ArrayGeometry`

Physical layout of phase-coherent antenna array on L2 node. Selects
steering-vector / array-manifold model L2 DSP uses.

- `ULA` — uniform linear array. Two or more elements, equal spacing along
  line. Simplest manifold. Inherent **front/back (mirror) ambiguity** about
  array axis — resolved by extra element, ground-plane reflector
  attenuating back lobe, coarse L1 bearing from same node, or
  platform motion. Contracts allow; DSP workstream
  responsible for resolution strategy.
- `UCA` — uniform circular array. Elements equally spaced on circle. No
  front/back ambiguity, 360° unambiguous coverage; needs ≥ 3 elements.
  KrakenSDR-style layout.
- `CUSTOM` — arbitrary element coordinates supplied explicitly in
  `ArrayConfig.element_positions_m`. Escape hatch for whatever physical
  arrangement on-site hardware actually permits.

**Producer:** `NodeConfig` author (operator). **Consumer:** `rfmesh-dsp`'s
steering-vector module, SDR workstream's calibration routine.

### `BearerKind`

Transport between node and fusion server. Absorbed entirely
inside node-runtime workstream's transport layer; DSP and fusion never
see this. Listed in contract only because `NodeConfig` must declare it.

- `WIFI` — UDP + msgpack over Wi-Fi / Ethernet. Primary: high bandwidth,
  low latency, carries optional debug payloads (raw pseudospectra).
- `LORA` — compressed bearing reports over LoRa link. Fallback for
  EW-contested conditions. Bandwidth tiny; only essential `BearingReport`
  fields sent, debug payloads dropped.
- `BOTH` — run both. Fusion de-duplicates by `(node_id, t_unix_ns)`. Wi-Fi
  preferred when healthy, LoRa as hot standby.

**Producer:** `NodeConfig` author. **Consumer:** node runtime's
transport layer (chooses implementation), dashboard (shows which bearer
delivered given report, for telemetry).

---

## §2 Geospatial value types (`rfmesh_contracts.geospatial`)

Two small, frozen value objects shared by both messages and configs.

### `GeodeticPosition`

Point on Earth, with isotropic 1-σ uncertainty.

- `lat_deg`, `lon_deg` — WGS-84 decimal degrees, validator-bounded.
- `hae_m` — height above WGS-84 ellipsoid, metres. Default 0.0 for
  bench/sim use; real nodes populate from GNSS or content with 0
  when altitude irrelevant to fix geometry (most ground-level cases).
- `sigma_m` — isotropic 1-σ position uncertainty, metres, ≥ 0. **For
  node:** GNSS or survey-of-record uncertainty (typically 5–10 m for
  smartphone survey, 2–5 m for ATGM336H, smaller for precision
  receiver). **For emitter `FixEvent.position`:** scalar convenience
  summary only — *authoritative* uncertainty there is anisotropic
  `confidence_ellipse_95`, consumers needing precision consult
  that, not `sigma_m`.

**Producer:** operator (node positions, via `NodeConfig`), GNSS reading
(if used), fusion solver (emitter positions). **Consumer:** fusion
solver (weights node positions, `sigma_m` as noise term in
geometry), CoT publisher (renders `position` and ellipse if
available), dashboard.

### `EllipseENU`

2-D confidence ellipse in local East-North tangent plane, centred on
accompanying `FixEvent.position`.

- `semi_major_m`, `semi_minor_m` — ellipse semi-axis lengths in metres.
  Validator enforces `semi_major_m >= semi_minor_m`; if producer wants to
  swap, must rotate `orientation_deg` by 90° to keep semantics consistent.
- `orientation_deg` — angle of semi-major axis from local East toward
  North, ENU-plane mathematical positive, range [-180, +180]°.

**Convention.** By contract, ellipse renders **95% confidence
contour** of 2-D Gaussian. For Gaussian, that is
`sqrt(chi²_inv(0.95, df=2)) ≈ 2.448` σ contour: semi-axis = 2.448 ·
σ_principal, where σ_principal is square root of covariance matrix's
eigenvalue along that axis. Fusion workstream owns conversion in
`covariance.py`; contract carries rendered ellipse so consumers
(CoT, dashboard) never need to scale.

`area_m2` is derived property (π · a · b), handy for logs and for demo
visual of "ellipse shrinking as nodes added". Not on wire; computed on demand.

**Producer:** fusion solver (always paired with `covariance_m2` on
`FixEvent`). **Consumer:** CoT publisher (renders as polygon
approximation), ops dashboard (renders as SVG ellipse).

---

## §3 Wire-format messages (`rfmesh_contracts.messages`)

Three message types cross workstream boundaries. Contract that
makes parallel workstream development possible.

### `BearingReport`

**Atom of system.** One node's estimate of azimuth to one
emitter, at one instant.

**Producer:** DSP workstream's L1 or L2 estimator (`l1_rssi.py` or
`l2_music.py`), wrapped by node runtime in `rfmesh-node`.
**Consumer:** fusion workstream's `Fuser.fuse()`, dashboard.

**Fields and meanings:**

- `schema_version` — pinned to `SCHEMA_VERSION`, see §0.
- `node_id` — stable identifier of producing node; matches
  `NodeConfig.node_id` of that node. Used by fusion to associate bearing
  with node's position (carried separately in `node_position`, but
  `node_id` is cross-reference key for dashboards and de-duplication
  across redundant bearers).
- `t_unix_ns` — acquisition timestamp, integer nanoseconds. **Fusion
  batches bearings into short windows** (default `FusionConfig.
  batch_window_ms = 100 ms`) by this value, so all bearings inside window
  refer to "same instant". Node clocks NTP-disciplined to ~10 ms,
  sufficient for AoA — see `ARCHITECTURE.md` §6 on why no need ns-level sync.
- `node_position` — where node was when took bearing. Carried
  *with every report* (not looked up separately) so moving or repositioned
  node handled correctly and fusion solver stateless w.r.t.
  node positions. For BoTH3 deployment, nodes static during fix;
  still costs nothing and avoids future-trap.
- `azimuth_deg` — estimated geographic azimuth, [0, 360), degrees, true
  north = 0, clockwise positive. Antenna heading correction *already*
  applied at node (node's own `heading_deg` from config).
  Fusion does *not* re-rotate; consumes geographic bearings.
- `azimuth_sigma_deg` — 1-σ uncertainty of `azimuth_deg`, degrees, strictly
  > 0. **Weight fusion solver uses (inverse-variance).** Must be
  honest estimate from node's own SNR, pseudospectrum sharpness, and
  calibration quality. DSP workstream's sigma estimation itself
  tested against simulator ground truth; honesty load-bearing for
  whole system — over-optimistic sigma poisons fusion for every other
  node contributing to same fix.
- `method` — which `Capability` produced bearing. Lets fusion and
  dashboard distinguish coarse from fine bearings for display and
  diagnostics, without changing how mathematically combined (which
  is `azimuth_sigma_deg`-weighted).
- `snr_db` (optional) — SNR of emitter at node, dB above
  node's noise-floor estimate. Diagnostic only — bearing's weight is
  `azimuth_sigma_deg`, not this. `None` means "not reported"; not all DSP
  paths estimate.
- `emitter_class` (optional) — L3 classifier label for emitter, if
  node ran classification.
  **`None` vs `EmitterClass.UNKNOWN` is meaningful distinction:**
    - `None`: node did not classify (no L3 capability, or classifier did
      not run on snapshot).
    - `EmitterClass.UNKNOWN`: classifier ran and not confident
      enough to assign label.
  Consumers must preserve distinction; fusion aggregates accordingly.
- `classification_confidence` (optional) — classifier probability for
  `emitter_class`, in [0, 1]. `None` iff `emitter_class` is `None`.
  *Quantitative* classifier output; discretized band for display lives
  on `FixEvent.confidence_level`.
- `raw_pseudospectrum` (optional, debug payload) — L2 MUSIC/Capon
  pseudospectrum as little-endian float32 log-magnitude samples over
  [0, 360) degrees at **0.5° angular step** (so 720 samples = 2880 bytes;
  validator does not enforce, but convention is what dashboard
  expects). Present only on Wi-Fi bearer (LoRa drops for bandwidth).
  Consumed by ops dashboard to render live pseudospectrum tile;
  fusion ignores.
- `refusal_reason` (optional) — free-form diagnostic string when
  `method = Capability.L1_REFUSED_PROMINENCE`. Wire-level surface
  of L1 estimator's `last_refusal_reason` (E1). Examples:
  `"prominence-gate failure (1.96 dB front-back < 6 dB)"`,
  `"saddle"`, `"vertex out of sweep window"`,
  `"singular covariance"`, `"non-finite variance"`,
  `"sweep underpopulated"`. `None` on every healthy bearing.
  Consumers (ops dashboard `BearingsPanel` / `BearingScanPanel`)
  render alongside refusal symbol; fusion ignores
  (already skipped by `method` filter). Added in
  SCHEMA_VERSION 1.2.0 (ADR-013 G4).

**Acceptance rules for `BearingReport` to be useful to fusion:**

1. `azimuth_sigma_deg` is honest — derived from node's own SNR /
   pseudospectrum / calibration, not constant default.
2. `t_unix_ns` is within active fusion window.
3. `node_position` is current (matches producing node's last
   `NodeStatus.position` within same NTP-tolerance).
4. Node not flagged stale by fusion (see `FusionConfig.
   node_stale_after_s`).

Report failing (1) = worst silent corruption mode in system —
hence DSP workstream tests with ground-truth assertions in
simulator and lead reviews any sigma-estimation change as if
contract change.

### `FixEvent`

**Fused emitter geolocation.** Cross-fix of several `BearingReport`s,
with full honesty payload.

**Producer:** fusion workstream's `Fuser.fuse()`.
**Consumer:** CoT publisher (renders as hostile-emitter marker with
ellipse polygon in ATAK), ops dashboard.

**Fields and meanings:**

- `schema_version` — pinned, see §0.
- `fix_id` — UUID, unique per fix. Moving emitter produces *stream* of
  `FixEvent`s, each with distinct `fix_id`; CoT layer may use stable
  *track* UID separately (moving emitter = one track, many fixes). Logs,
  diagnostics, dashboard refer to individual solutions by `fix_id`.
- `t_unix_ns` — representative timestamp, typically midpoint of time
  window of contributing `BearingReport`s.
- `position` — estimated emitter position, WGS-84, as `GeodeticPosition`.
  Centre of `confidence_ellipse_95`. `sigma_m` is scalar
  convenience summary; ellipse authoritative.
- `covariance_m2` — 2×2 position covariance in local ENU as
  `(σ_xx, σ_xy, σ_yy)` in square metres, unique entries of
  symmetric matrix `[[σ_xx, σ_xy], [σ_xy, σ_yy]]`. Raw solver output.
  `confidence_ellipse_95` is geometric rendering; both carried so
  downstream tracker (Kalman filter, say) can consume matrix without
  inverting ellipse. Fusion solver uses ENU local to
  fix's geometry; consumers needing global covariance reproject if needed.
- `confidence_ellipse_95` — 95% confidence ellipse in ENU, centred on
  `position`. Human-facing uncertainty. Derived from `covariance_m2` by
  chi-square scaling described in §2.
- `confidence_level` — `ConfidenceLevel`. Discretized display band.
  **Fusion's policy for setting** (binding):
    - `HIGH` iff `gdop ≤ FusionConfig.gdop_warn_threshold` (default 6.0)
      *and* `confidence_ellipse_95.semi_major_m` below operational
      tolerance (workstream-C-set, typically fraction of fix range) *and*
      no residual exceeds 3σ of its node's sigma.
    - `LOW` iff `gdop` above threshold *or* `semi_major_m` exceeds
      tolerance *or* solver fell back to `fallback_centroid`.
    - `MEDIUM` otherwise.
- `contributing_nodes` — tuple of `node_id`s of bearings used, in
  *same order as `residuals_deg`*. Length = number of bearings
  fused, ≥ 2 (minimum per `FusionConfig.min_bearings_for_fix`).
- `residuals_deg` — per-node angular residual after fit, degrees:
  measured bearing minus bearing solved position implies, for each
  node in `contributing_nodes`, same order, same length. **System's
  self-diagnosis channel.** Node whose residual is many σ from zero (σ
  here = that node's reported `azimuth_sigma_deg`) highlighted by
  dashboard as probable multipath/calibration outlier; operator sees
  *which* node misbehaving, not just that fix loose.
- `gdop` — Geometric Dilution of Precision for fix's sensor geometry.
  Strictly > 0; low (~1–3) means favourable node placement, high (> ~6)
  means near-collinear geometry that stretches ellipse regardless of
  per-bearing quality. Reported on wire so operator understands
  *why* fix loose and where to reposition.
- `method` — string, solver mode used. Set values at v1.0.0:
    - `"stansfield"` — closed-form weighted least-squares only (no refinement).
    - `"stansfield+mle"` — Stansfield seed refined by maximum-likelihood
      Gauss-Newton iteration. Default; Stansfield alone provably
      biased for finite samples, MLE refinement removes bias.
    - `"fallback_centroid"` — degenerate geometry; solver declined to
      trust intersection and returned weighted centroid of
      bearing-line crossings. Always paired with `confidence_level = LOW`.
  Kept as free-form `str` rather than enum because set of solver
  modes is internal fusion concern that may evolve without contract
  bump (consumers parse for display; do not branch on it).
- `emitter_class` (optional) — consensus classification across
  contributing nodes' `BearingReport`s, if any classified. `None` means no
  node classified; `EmitterClass.UNKNOWN` means nodes classified but did
  not agree or individually unsure. **Geolocation never depends on
  this** — classification is metadata overlay, not gate on fix.
- `gdop_uncomputable_reason` (optional) — free-form reason `gdop`
  is sentinel placeholder rather than measured dilution. `None` on
  healthy fix; populated by fusion solver when `compute_gdop()` raises
  `DegenerateGeometryError` (collinear nodes through emitter, parallel
  bearing lines). Ops dashboard renders `"GDOP: uncomputable (<reason>)"`
  instead of `gdop:.2f`. `gdop` field still carries a strictly-positive
  sentinel (`> gdop_warn_threshold * 10`) only to satisfy contract
  validator; `confidence_level` forced LOW on this path per ADR-005 D4.
  Added in SCHEMA_VERSION 1.2.0 (ADR-013 G3).

### `NodeStatus`

**Periodic node health heartbeat.** Deliberately small: rides same
bearers as `BearingReport`s, on LoRa every byte matters.

**Producer:** every node, on `BearerConfig.heartbeat_interval_s` (default
2.0 s). **Consumer:** fusion workstream (decides which nodes live;
silent node excluded from new fixes), ops dashboard.

**Fields and meanings:**

- `schema_version` — pinned, see §0.
- `node_id` — stable identifier, matches `NodeConfig.node_id`.
- `t_unix_ns` — heartbeat timestamp.
- `position` — node's current self-reported position. Usually static for
  project's deployments, but carried every heartbeat so repositioned
  node updates fusion and dashboard without separate mechanism.
- `active_capabilities` — what node *actually running right now* —
  result of intersecting `NodeConfig.capabilities` with detected
  hardware. If narrower than what operator declared,
  dashboard shows; no silent downgrade.
- `gnss_locked` — whether node currently has valid GNSS fix. **False
  in GNSS-jammed environment** — itself EW indicator worth surfacing,
  *not* mode of failure. Per `ARCHITECTURE.md` §6, mesh does not
  depend on GNSS for positioning or timing; flag exists so
  deployment can *observe* GNSS denial. When false, `position` is
  last-known fix (or survey-of-record value).
- `healthy` — node-level self-assessment, one-glance red/green for
  dashboard. True iff SDR streaming, processing keeps up, and
  bearer connected. False otherwise.
- `status_detail` — short human-readable elaboration, especially when
  `healthy` is False, e.g. `"SDR overflow"`, `"LoRa bearer down, Wi-Fi only"`.
  Empty string when nothing to add. Free-form but conventionally < 80 chars.

---

## §4 Configuration schemas (`rfmesh_contracts.config`)

YAML files load into these. Validation at load time; malformed config
rejected before any hardware touched.

### `SDRConfig`

Which radio node uses and how tuned. (Axis 1: SDR hardware type.)

- `driver` — `"rtlsdr" | "hackrf" | "bladerf" | "pluto" | "sim"`. Only
  place in node's config that names hardware family. SDR workstream
  maps each value to `Receiver` (or `CoherentReceiver`) implementation.
  `"sim"` selects `SyntheticReceiver`, satisfies same Protocol
  with synthetic IQ — same pipeline, no hardware (see `ARCHITECTURE.md` §4).
- `serial` (optional) — hardware serial / identifier when several devices of
  same `driver` attached to one host. `None` means "first device
  driver finds"; ignored for `sim`. For RTL-SDR specifically, old repo
  documented `--device-serial` foot-gun (index-based addressing
  fragile); SDR workstream resolves to serial when possible.
- `sample_rate_hz` — requested ADC sample rate. Achievable rate is
  driver-dependent — RTL-SDR V4 reliable to ~2.4 MS/s, bladeRF to tens of
  MS/s — and `Receiver` implementation reports *actual* rate it
  obtained through `ReceiverCapabilities.actual_sample_rate_hz`.
  Meaningful shortfall surfaced (Invariant 4), not hidden.
- `center_freq_hz` — tuner centre frequency. For coherent 2-RX device,
  *both* channels share this LO — which is exactly what makes them
  phase-coherent, so single value here correct.
- `gain_db` — receiver gain in dB, or string `"auto"` for driver's
  AGC. **Prefer explicit number for DF work**: fixed, known gain keeps
  noise floor stable across antenna sweep, matters for honest
  SNR and sigma estimates. `"auto"` is bring-up convenience, not measurement.
- `bias_tee` — boolean. Default False. Enabling DC on antenna port into
  hardware that does not expect it can release magic smoke; explicit opt-in.

### `ArrayConfig`

Physical antenna-array description for L2 nodes. Present iff `NodeConfig`
declares L2 capability; cross-validated.

- `geometry` — `ArrayGeometry`. Selects steering-vector model.
- `n_elements` — number of antenna elements / coherent RX channels, ≥ 2.
- `element_spacing_m` — for ULA/UCA only (parametric geometries). ULA:
  inter-element spacing along array axis. UCA: array *radius*.
  Typically λ/2 at design frequency (~16.4 cm at 915 MHz, ~6.2 cm at
  2.4 GHz). Validator enforces: required for ULA/UCA, must be `None` for CUSTOM.
- `element_positions_m` — for CUSTOM only. Tuple of (x, y) coordinates in
  metres in array's local frame. Validator enforces: required for
  CUSTOM, must be `None` for ULA/UCA, length must equal `n_elements`.
- `calibration_file` (optional) — path to per-element phase/gain
  calibration table (frequency-dependent complex offsets) produced by
  SDR workstream's calibration routine. `None` means "not yet calibrated"
  — and L2 node asked to run MUSIC without calibration **must fail
  loudly, not silently emit garbage bearings** (Invariant 4 again,
  surfaced specifically because phase DF without per-channel calibration
  is classic silent-failure mode).

### `BearerConfig`

Transport configuration. Absorbed entirely by node-runtime workstream.

- `kind` — `BearerKind`. Selector.
- `lora_serial_port` (optional) — serial device for LoRa radio (e.g.
  `"/dev/ttyUSB0"`). Validator: required when `kind` is `LORA` or `BOTH`,
  must be `None` for `WIFI` (stray value from copy-pasted config implies
  hardware not there — caught at load).
- `heartbeat_interval_s` — how often node emits `NodeStatus`, seconds,
  > 0. Default 2.0. `FusionConfig.node_stale_after_s` is small multiple
  of this (default 6.0 = 3× heartbeats), so node dropped from fixes
  after missing three heartbeats.

### `NodeConfig`

Complete configuration of one mesh node. Axes 1 and 2 converge here.

- `schema_version` — pinned, see §0.
- `node_id` — stable, unique identifier, e.g. `"node-rtl-01"`. Uniqueness
  across mesh is operator's responsibility.
- `position` — `GeodeticPosition`. Surveyed/known position. Node with
  GNSS overrides this at runtime from fix (and runtime updates
  `NodeStatus.position`); this config value is fallback and
  bench/sim value. **Accurate node positions matter** — position error
  propagates linearly into fix error; honest `sigma_m` is part of
  fusion weight.
- `heading_deg` (optional) — mounting heading of directional antenna or
  array boresight, degrees true, clockwise from north, range [0, 360).
  Required for L1 node (servo sweep relative to this) and for
  ULA L2 node (array axis orientation). May be `None` for UCA node
  (rotationally symmetric). Validator enforces. **Set by survey-and-align,
  not by magnetometer** — see `ARCHITECTURE.md` §6 and
  `INHERITED_CONTEXT.md` on why magnetometers at mast unreliable.
- `sdr` — `SDRConfig`.
- `array` (optional) — `ArrayConfig`. Required iff node declares L2
  capability; must be `None` for L1-only. Validator enforces.
- `capabilities` — tuple of `Capability`, length ≥ 1. Operator's
  intent. Intersected at startup with hardware-detected capability;
  declared capability hardware cannot meet is fatal.
- `bearer` — `BearerConfig`.
- `fusion_endpoint` — URL where node ships reports (e.g.
  `"udp://10.0.0.1:9000"`). Node otherwise unaware of rest of
  mesh; star topology keeps nodes stateless about each other.

### `FusionConfig`

Central fusion server configuration. Note what *not* here: **any notion
of expected number of nodes.** That is Axis 3 absorbed by absence —
solver accepts whatever bearings arrive and cross-fixes any N ≥ 2.

- `schema_version` — pinned, see §0.
- `listen_url` — where fusion server receives node reports. Must match
  nodes' `fusion_endpoint`.
- `batch_window_ms` — width of time window over which `BearingReport`s
  grouped before solving fix, milliseconds. Default 100.0. Wide
  enough to gather all nodes' reports for "one instant"; narrow enough that
  moving emitter has not travelled far within it. Tunable per scenario.
- `node_stale_after_s` — silence threshold for excluding node from new
  fixes, seconds. Default 6.0. Small multiple of nodes'
  `heartbeat_interval_s`. Mechanism behind "pull node's antenna
  mid-demo, watch ellipse grow" robustness story.
- `min_bearings_for_fix` — minimum bearings required to attempt fix.
  Default 2; hard floor 2 (one bearing = ray, not fix). Three or more
  yields residuals for self-diagnosis (see `FixEvent.residuals_deg`).
- `gdop_warn_threshold` — GDOP above which fix flagged as
  geometrically weak. Default 6.0. **Not reject threshold** — weak fix
  still information, honestly labelled (`confidence_level = LOW`).
  Display and warning only.
- `cot_url` (optional) — where CoT publisher ships `FixEvent`s (e.g.
  `"tcp://10.0.0.2:8087"` for FreeTAKServer). `None` disables CoT output,
  useful for headless bench runs that only watch ops dashboard.

### `CommsConfig`

DSSS directional-comms physical-layer configuration. Required when
`NodeConfig.capabilities` contains `Capability.COMMS_DSSS`. Added in
SCHEMA_VERSION 1.3.0 (ADR-025). Carries **cross-workstream** parameters
only — node-layer concerns (peer roster, routing table, TDD slot
assignment) live in `rfmesh-node/comms/comms_config.py` as
RendezvousConfig-style helpers, not in frozen contracts.

- `carrier_freq_hz` — RF carrier frequency, Hz, strictly > 0. The
  ATK-10 Yagi covers 868–915 MHz; typical European deployment
  value is 868e6 or 915e6. Independent of any DF carrier (DF and
  COMMS modes are mutually exclusive in v1.3.0).
- `chip_rate_hz` — DSSS chip rate, Hz. Target ~10e6 for the
  BoTH3 build. Cross-checked against `actual_sample_rate_hz` from
  Receiver/Transmitter capabilities at node startup (not here —
  realised rate may differ from requested).
- `spreading_factor` — chips per data symbol; default 1023.
  Must equal `2**n - 1` for the LFSR polynomial (validator
  enforces). 1023 = `2**10 - 1` gives processing gain
  `10*log10(1023) ≈ 30 dB`.
- `lfsr_taps` — feedback-tap tuple (1-indexed, smallest first)
  for the m-sequence LFSR. Validator: `max(taps) == log2(spreading_factor + 1)`,
  `min(taps) >= 1`, `len(taps) >= 1`. Canonical length-10
  polynomial: `(10, 3)` (i.e. `x^10 + x^3 + 1`).
- `lfsr_seed` — non-zero initial LFSR state. Zero is a fixed-point
  (would produce all-zero output, not an m-sequence). All mesh
  nodes in v1.3.0 share the **same** PN sequence (single shared
  spreading code; per-link codes deferred); operator-set value is
  the mesh-wide secret.
- `tdd_slot_ms` — TDD half-duplex slot width, ms, strictly > 0.
  Typical 100–500 ms for v1.3.0's ~10 kbit/s throughput.
- `tdd_guard_ms` — guard interval between slots, ms, >= 0. Must
  comfortably exceed worst-case NTP skew (~10 ms) plus RF settling
  on retune. Generous default (20–50 ms) trades throughput for
  robustness.
- `frame_payload_max_bytes` — max DSSS-frame payload bytes,
  strictly > 0. Set so one frame fits inside one TDD slot at the
  configured chip rate / spreading / coding. Framing module
  enforces; oversize payloads must fragment at application layer.

**Producer:** operator (via YAML), when node declares `COMMS_DSSS`.
**Consumer:** `rfmesh-dsss` DSP (carrier, chips, PN), `rfmesh-sdr`
TX/RX drivers (sample-rate validation), `rfmesh-node` comms loop
(TDD scheduling, framing).

---

## §5 Behavioural contracts (`rfmesh_contracts.protocols`)

Behaviour, not data: abstract operations one workstream may assume
another provides. All are `typing.Protocol` (structural typing —
implementation conforms by shape), several are `@runtime_checkable` so
tests can `isinstance`-check cheaply. Full method signatures and
docstrings live in `protocols.py`; section explains *what each
Protocol is for* and *who depends on whom*.

### `Receiver`

Source of single-channel baseband IQ. L1 signal-input contract.

- **Implementers (`rfmesh-sdr`):** `SoapyReceiver` (generic, covers
  RTL-SDR / HackRF / single-channel bladeRF / single-channel Pluto via
  SoapySDR), legacy `RTLSDRDevice` ported to Protocol (kept as
  proven `rtl_sdr`-subprocess path; see `SALVAGE_AUDIT.md` Part 4d), and
  `SyntheticReceiver` (simulator — same Protocol, generated IQ, no hardware).
- **Consumers (`rfmesh-dsp` L1, via `rfmesh-node`):** read IQ blocks,
  produce `BearingReport`s.

Hard guarantee `Receiver` makes — consumer may rely on:
`read(n)` returns exactly `n` samples or raises. Silent short read
would corrupt every downstream estimate. (Invariant 4.)

### `CoherentReceiver` (extends `Receiver`)

Source of phase-coherent multi-channel IQ. L2 signal-input contract.
Because extends `Receiver`, L2-capable node can still do L1 on
channel 0 with same instance.

- **Implementers (`rfmesh-sdr`):** `BladeRFCoherentReceiver` (likely native
  `libbladeRF` for clean coherent control), `PlutoCoherentReceiver` (via
  `pyadi-iio` in `ad9361` 2r2t mode — requires documented Pluto
  firmware mode-switch), `SyntheticReceiver` in coherent mode.
- **Consumers (`rfmesh-dsp` L2):** read coherent IQ blocks, produce L2 `BearingReport`s.

Hard guarantee: `read_coherent(n)` returns `(n_channels, n)`
sample-aligned, phase-coherent block, *to extent last successful
`calibrate()` established*; `is_calibrated` truthfully reports whether
that has happened. **L2 DSP code refuses to emit bearings from
uncalibrated coherent stream.** Calibration not implicit and never
silently bypassed.

### `Transmitter`

Sink for single-channel baseband IQ. DSSS comms TX contract.
Symmetric to `Receiver`. Added in SCHEMA_VERSION 1.3.0 (ADR-025).

- **Implementers (`rfmesh-sdr`, Iter 3+):** `BladeRFTransmitter` /
  `HackRFTransmitter` / `PlutoTransmitter` (whichever hardware
  on-site), and `SyntheticTransmitter` (simulator — same Protocol,
  IQ written to in-process `loopback_channel` that one or more
  `SyntheticReceiver`s read; full comms protocol testable with
  zero hardware).
- **Consumers (`rfmesh-dsss` framing/modulation output, via
  `rfmesh-node`):** write IQ blocks, ship DSSS frames.

Hard guarantee `Transmitter` makes — consumer may rely on:
`write(iq)` returns exactly `len(iq)` samples transmitted or raises.
No silent short-write fallback (B3, mirror of `Receiver.read`).
Partial DSSS frame is worse than no frame because despreader syncs
on garbage. `TransmitterCapabilities` exposes `driver`,
`n_tx_channels`, `actual_sample_rate_hz`, `max_tx_power_normalized`
(no dBm — same B.2 honesty rule as RX).

### `CoherentTransmitter` (extends `Transmitter`)

Sink for phase-coherent multi-channel IQ. Reserved for future
transmit-beamforming / TX-null-steering. Added in SCHEMA_VERSION
1.3.0 (ADR-025) for symmetry; **no implementation in v1.3.0**.
DSSS BPSK needs one TX chain. A `CoherentTransmitter` declaration
is a no-op until a future ADR adds operational semantics.

Hard guarantee: `write_coherent(n_channels, n)` returns samples per
channel transmitted or raises. Phase coherence across channels
relies on RX-side `calibrate()` via reciprocity (paired device).

### `BearingEstimator`

Turns IQ into `BearingReport`. Contract DSP exposes to node.

- **Implementers (`rfmesh-dsp`):** L1 amplitude-sweep estimator (consumes
  1-D IQ), L2 MUSIC estimator (consumes 2-D coherent IQ), any future
  estimator that satisfies shape.
- **Consumers (`rfmesh-node`):** node runtime owns one or more
  estimators per active capabilities, feeds samples, ships reports.

`method` (property) advertises which `Capability` estimator
implements, so runtime can match estimators to active capabilities
without hard-coding classes. `estimate()` returns `BearingReport | None`,
and **`None` is valid, expected outcome** (emitter below threshold,
pseudospectrum too flat to peak-pick, uncalibrated input) — *not* error.
Fabricating confident bearing from nothing is what estimator must
never do (Invariant 4 again — appears wherever silent failure modes most tempting).

### `Fuser`

Cross-fixes `BearingReport`s into `FixEvent`. Fusion contract.

- **Implementer (`rfmesh-fusion`):** Stansfield + MLE solver, with its
  GDOP gate and `fallback_centroid` degenerate-geometry path.
- **Consumers (`rfmesh-node`'s fusion-side runtime, ops dashboard):**
  feed batches, receive fixes.

`fuse()` takes any iterable of `BearingReport`s — indifferent to count
(Axis 3) and to method (Axes 1/2). Returns `FixEvent | None`; `None`
means "cannot responsibly solve" (below `min_bearings_for_fix`, or
geometry so degenerate that even `fallback_centroid` is undefensible).
Returned `FixEvent` always carries its honesty payload; weak-but-real
fixes *labelled* weak (`confidence_level = LOW`), not withheld.

**ADR-013 G4 — `L1_REFUSED_PROMINENCE` filter.** Input
`BearingReport`s with `method = Capability.L1_REFUSED_PROMINENCE`
are SKIPPED at `fuse()` entry, not weighted as
`1/azimuth_sigma_deg²`. The sentinel `azimuth_sigma_deg = 180.0`
on a refusal report would otherwise contribute trivially-low
weight but still occupy a slot in the `contributing_nodes` tuple
and shift the centroid — both incorrect. The filter runs before
the time-window + min-count check, so a batch of all-refusals
returns `None`.

### `CotPublisher`

Publishes `FixEvent` as Cursor-on-Target. One method.

- **Implementer (`rfmesh-cot`):** PyTAK-backed, serialises `FixEvent`
  to CoT XML (hostile-emitter marker + `<shape><ellipse>` polygon from
  `confidence_ellipse_95`) and ships to configured TAK endpoint.
- **Consumer:** fusion workstream's publish step, or ops layer.

`publish()` is synchronous from caller's view; transport queueing is
implementer's business. Transmit failure raises — not silently swallowed.

### `Bearer`

Transport between node and fusion server. Internal to
node-runtime workstream; in contract only because runtime's
producer side and transport side tested against each other
independently.

- **Implementers (`rfmesh-node`):** `WifiBearer` (UDP + msgpack),
  `LoraBearer` (compressed, drops `raw_pseudospectrum`), and composite
  `BothBearer` that de-duplicates by `(node_id, t_unix_ns)`.
- **Consumers:** node producer side (sends `BearingReport`s and
  `NodeStatus`es), fusion-server side (receives).

Send split by message type (not union) because LoRa treats them
differently — `BearingReport`s may be heavily compressed on LoRa,
`NodeStatus`es already tiny. Receive returns *messages since
last call*; ordering best-effort, fusion time-windows regardless.

### Type aliases for the signal path

`IQBlock` and `CoherentIQBlock` are NumPy `complex64` array aliases.
Shape and dtype conventions (1-D `(n,)` and 2-D `(n_channels, n)`,
respectively) part of contract; alias *not* class
because forcing every hot IQ buffer through Pydantic model would be
absurd. Workstreams' tests assert shape and dtype at API boundaries.

---

## §6 Change-control reminders

1. Schemas in `packages/rfmesh-contracts/` authoritative. Doc
   mirrors; on disagreement, schema wins and doc is
   bug to file against lead.
2. Field's semantics — what *means*, not type — change only
   by ADR. Wording fix here without schema change is fine. Wording
   change that implies semantic change is contract change in disguise
   and goes through ADR.
3. `SCHEMA_VERSION` is tripwire. Any contract change ends with bump.
4. If you, agent or reviewer, find ambiguity in doc, do
   not resolve in code — file question to lead. Two reasonable
   interpretations of same field across two workstreams is exactly
   integration-day failure this whole governance layer exists to prevent.