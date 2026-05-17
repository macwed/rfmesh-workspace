# rfmesh — Interfaces

**Status:** binding. Mirrors the frozen state of `packages/rfmesh-contracts/`.
Edits only by the lead architect, in lockstep with the contracts package.
**Audience:** every workstream agent and reviewer. This is the dictionary you
consult when you need to know *what a field means*, not just what type it is.
**Date:** 2026-05-14.
**Mirrors contracts at:** `SCHEMA_VERSION = "1.1.0"`.

This document does not duplicate the Pydantic schemas — those are
authoritative, and you read them directly when you want field names, types,
and validators. This document carries what the schemas cannot: **the meaning
of each field, who produces it, who consumes it, what units it is in, what
makes it valid in the wider system, and where its boundaries are.** When the
schema and this document disagree, the schema wins, and the document is a
bug. When two readers disagree about what a field means, this document is
the tiebreaker.

For governance — who may change a contract, how — see `ARCHITECTURE.md` §3
and `AGENTS.md` § "The Five Invariants".

---

## §0 Universal conventions

These apply across every type in the contracts package. They are not
restated per-field.

**Units and physical conventions** (binding, never negotiable on the wire):

- *Time.* `t_unix_ns` is integer nanoseconds since the Unix epoch, UTC.
  Integer, not float — float64 loses nanosecond resolution past ~2⁵³ ns
  (≈ 104 days). Producers compute this from `time.time_ns()` or
  `time.clock_gettime_ns(time.CLOCK_REALTIME)`; consumers treat it as an
  opaque ordering and batching key.
- *Geodetic coordinates.* WGS-84. Latitude / longitude in **decimal degrees**.
  Height in **metres above the WGS-84 ellipsoid (HAE)**, not above mean sea
  level. This matches u-blox GNSS output and CoT/ATAK input directly — no
  datum conversion ever happens in this codebase.
- *Local tangent plane.* **ENU** (East-North-Up), metres, right-handed. Used
  for fusion geometry (Stansfield, MLE, GDOP) and for the confidence ellipse.
  Each operation picks one ENU origin (typically the centroid of deployed
  nodes); the ellipse is interpreted relative to its accompanying fix
  position, so the origin travels implicitly.
- *Azimuths and bearings.* **Degrees, true north = 0, clockwise positive,
  range [0, 360).** Never magnetic, never radians, on the wire. Per-sensor
  magnetic or platform-relative conversions happen at the node edge.
- *Angular uncertainty.* Degrees, **1-σ** (one standard deviation). The
  95% confidence ellipse fields are an exception, explicitly named, derived
  from the covariance via chi-square scaling.
- *Power and SNR.* Decibels. SNR is in dB **above the node's own noise-floor
  estimate**, not absolute. **There is no `dBm` field anywhere.** None of the
  SDRs in scope (RTL-SDR V4, HackRF One, bladeRF 2.0 micro, Pluto+) is
  power-calibrated; claiming absolute dBm to an RF/EW jury would be a
  credibility own-goal. `ReceiverCapabilities.is_power_calibrated` is a
  checked fact, not a tribal assumption.

**Validation discipline** (binding, applied uniformly):

- Every Pydantic model uses `ConfigDict(frozen=True, extra="forbid")`. A
  message is immutable once constructed, and a typo'd or stale field is a
  loud error at parse time — never a silently ignored setting. This is the
  data-layer half of Invariant 4 ("No silent fallbacks") from `AGENTS.md`.
- Numeric ranges (lat/lon, azimuth, gain, frequencies) are enforced by Field
  constraints. Cross-field invariants (an L2 capability requires an array;
  ULA requires `element_spacing_m`; LoRa bearer requires a port) are enforced
  by `@model_validator(mode="after")`.
- Optional fields (`X | None`) are *deliberate*: `None` means "this datum is
  not available" (not measured / not classified / not present on this
  bearer). It is a different statement from a default value. Producers must
  not paper over absence with a silent default.

**Versioning** (binding, the single most important type-system mechanism):

Every wire-format message carries `schema_version: Literal[SCHEMA_VERSION]`.
Because `SCHEMA_VERSION` is a module-level constant, the `Literal` resolves
at type-check time. A workstream built against an old contract version that
constructs a `BearingReport` after a contract bump is a `mypy` error in that
workstream's own CI — the drift is caught locally before integration. The
operational effect: the lead bumps `SCHEMA_VERSION`, every dependent
workstream's `mypy` immediately tells it what to update, and no message of
the wrong version ever reaches the wire.

`SCHEMA_VERSION` is semantic: MAJOR.MINOR.PATCH. PATCH is doc-only. MINOR is
additive and backward-compatible (an extra optional field, an extra enum
member that consumers can treat as "unknown"). MAJOR is breaking and
requires all workstreams to update in lockstep. The current value is in
`version.py`; only an accepted ADR bumps it.

---

## §1 Enumerations (`rfmesh_contracts.enums`)

Every enum is a `str` enum. The string value is what appears in YAML configs,
JSON-on-the-wire, and CoT remarks — renaming a value is a MAJOR bump.

### `Capability`

What a single mesh node can do with the hardware it has. Declared by the
operator in `NodeConfig.capabilities`; intersected at node startup with what
the detected SDR + array physically supports; a mismatch is fatal at boot,
never a silent downgrade.

- `L1_RSSI` (`"l1_rssi"`) — amplitude-comparison DF via servo-rotated
  directional antenna and a single-channel SDR. Per-bearing uncertainty
  typically 5–15° in benign environments, wider in heavy multipath.
- `L2_MUSIC` (`"l2_music"`) — phase-coherent subspace DF on a 2+ element
  array. Requires `CoherentReceiver` hardware and a successful array
  calibration. Per-bearing uncertainty 1–3° under benign conditions.
- `L2_CAPON` (`"l2_capon"`) — phase-coherent Capon (MVDR-spectrum) DF on
  the same array. Same R as MUSIC, different DoA estimator (peak of
  `1/(a^H R^-1 a)` instead of MUSIC's noise-subspace projector). More
  robust than MUSIC in small-snapshot / low-SNR regimes. Per-bearing
  uncertainty 1–3° under benign conditions. **Added in SCHEMA_VERSION
  1.1.0 (ADR-008)**; previously, WS-B-004's Capon estimator emitted
  `L2_MVDR_NULL` under a docstring caveat.
- `L2_MVDR_NULL` (`"l2_mvdr_null"`) — **null-steering** (dual-use
  sibling of `L2_MUSIC` / `L2_CAPON`). The same R inverted via the MVDR
  distortionless-response weight formula `w = R⁻¹ a / (a^H R⁻¹ a)` (`a`
  is the look-direction steering vector) to synthesise a spatial null
  on any strong off-look source present in R — typically a co-channel
  jammer. Protects the project's own L2 coherent DF channel from
  desensitisation by the jammer while the system continues to produce
  bearings on it (**anti-desense, not ECM**). Does NOT emit a
  `BearingReport`; it is a receive-weight synthesiser invoked as an
  operator action. A node declaring `L2_MVDR_NULL` advertises the
  capability; no streamed wire-format product is emitted in v1.1.0.
  Implementation: `rfmesh_dsp.l2_null_steering` (WS-B-007). Same R, same
  array.
- `L3_CLASSIFY` (`"l3_classify"`) — emitter classification by edge ML. Needs
  a compute node (Raspberry Pi class), SDR-agnostic.

**Producer:** the operator (via `NodeConfig`). **Consumer:** the node
runtime (intersects with hardware), the dashboard (filters/labels), the
`BearingReport.method` field (reports which capability produced a bearing).

### `EmitterClass`

The output label of the L3 classifier. The *open, extensible threat library*
that is the project's moat. Adding a member is a MINOR bump and must be
paired with a threat-profile module under `rfmesh-ml/threats/` plus a
documentation entry in `docs/threat-library.md`.

- `UNKNOWN` (`"unknown"`) — **mandatory, load-bearing.** The classifier ran
  and is genuinely not confident enough to assign a label. Different from
  `None` (classifier did not run at all) — see the `None`-versus-`UNKNOWN`
  distinction in `BearingReport.emitter_class` below.
- `ELRS` — ExpressLRS R/C control link (868/915 MHz or 2.4 GHz, LoRa-based
  FHSS).
- `CROSSFIRE` — TBS Crossfire R/C control link (868/915 MHz, long-range
  FHSS).
- `GSM_JAMMER` — GSM-band handset emission, the analog of an IED
  command-detonation phone. Distinctive in *context* (lone uplink burst from
  a static, non-infrastructure location), not in waveform.
- `POLE21` — Pole-21 GNSS jamming complex. **Stub at v1.0.0**: profile
  module is a documented placeholder pending real-IQ capture. The contract
  shape is stable; the classifier will not emit this label until the profile
  is trained.
- `VOLNOREZ` — Volnorez vehicle-mounted counter-FPV jammer. Stub, same
  status as `POLE21`.
- `DRONEID` — DJI DroneID / OcuSync downlink beacon (Mavic-class).

**Producer:** the L3 classifier in `rfmesh-ml`. **Consumer:** the
`BearingReport`, the `FixEvent` (consensus across contributing nodes), the
CoT marker remarks, the ops dashboard.

### `ConfidenceLevel`

The coarse, **human-facing** confidence band attached to a `FixEvent`. Used
for ATAK marker styling and one-word annotations on the ops dashboard.

- `HIGH` — geometry strong (low GDOP, tight ellipse, residuals consistent)
  and/or classification probability high. Safe to action.
- `MEDIUM` — usable but caveated: moderate GDOP, or a single redundant
  bearing, or a mid-probability classification. Cue further collection.
- `LOW` — weak: near-collinear geometry, ellipse larger than operational
  tolerance, or low-probability label. Report, but do not action alone.

**Producer:** `rfmesh-fusion`, derived from quantitative evidence according
to the policy in §3 (`FixEvent.confidence_level`). **Consumer:** the CoT
publisher, the dashboard. It is *not* the truth — the truth is
`FixEvent.covariance_m2`, `confidence_ellipse_95`, `gdop`, and
`classification_confidence`. The band is the discretized display layer.

### `ArrayGeometry`

Physical layout of a phase-coherent antenna array on an L2 node. Selects the
steering-vector / array-manifold model the L2 DSP uses.

- `ULA` — uniform linear array. Two or more elements, equal spacing along a
  line. Simplest manifold. Inherent **front/back (mirror) ambiguity** about
  the array axis — resolved by an extra element, a ground-plane reflector
  attenuating the back lobe, a coarse L1 bearing from the same node, or
  platform motion. The contracts allow this; the DSP workstream is
  responsible for the resolution strategy.
- `UCA` — uniform circular array. Elements equally spaced on a circle. No
  front/back ambiguity, 360° unambiguous coverage; needs ≥ 3 elements. The
  KrakenSDR-style layout.
- `CUSTOM` — arbitrary element coordinates supplied explicitly in
  `ArrayConfig.element_positions_m`. The escape hatch for whatever physical
  arrangement the on-site hardware actually permits.

**Producer:** `NodeConfig` author (operator). **Consumer:** `rfmesh-dsp`'s
steering-vector module, the SDR workstream's calibration routine.

### `BearerKind`

Transport used between a node and the fusion server. Absorbed entirely
inside the node-runtime workstream's transport layer; DSP and fusion never
see this. Listed in the contract only because `NodeConfig` must declare it.

- `WIFI` — UDP + msgpack over Wi-Fi / Ethernet. Primary: high bandwidth,
  low latency, carries optional debug payloads (raw pseudospectra).
- `LORA` — compressed bearing reports over a LoRa link. Fallback for
  EW-contested conditions. Bandwidth tiny; only essential `BearingReport`
  fields are sent, debug payloads dropped.
- `BOTH` — run both. Fusion de-duplicates by `(node_id, t_unix_ns)`. Wi-Fi
  preferred when healthy, LoRa as hot standby.

**Producer:** `NodeConfig` author. **Consumer:** the node runtime's
transport layer (chooses implementation), the dashboard (shows which bearer
delivered a given report, for telemetry).

---

## §2 Geospatial value types (`rfmesh_contracts.geospatial`)

Two small, frozen value objects shared by both messages and configs.

### `GeodeticPosition`

A point on the Earth, with an isotropic 1-σ uncertainty.

- `lat_deg`, `lon_deg` — WGS-84 decimal degrees, validator-bounded.
- `hae_m` — height above the WGS-84 ellipsoid, metres. Default 0.0 for
  bench/sim use; real nodes populate it from GNSS or are content with 0
  when altitude is irrelevant to the fix geometry (most ground-level cases).
- `sigma_m` — isotropic 1-σ position uncertainty, metres, ≥ 0. **For a
  node:** the GNSS or survey-of-record uncertainty (typically 5–10 m for a
  smartphone survey, 2–5 m for an ATGM336H, smaller for a precision
  receiver). **For an emitter `FixEvent.position`:** a scalar convenience
  summary only — the *authoritative* uncertainty there is the anisotropic
  `confidence_ellipse_95`, and consumers needing precision should consult
  that, not `sigma_m`.

**Producer:** the operator (node positions, via `NodeConfig`), GNSS reading
(if used), the fusion solver (emitter positions). **Consumer:** the fusion
solver (weights node positions, with `sigma_m` as a noise term in the
geometry), the CoT publisher (renders `position` and an ellipse if
available), the dashboard.

### `EllipseENU`

A 2-D confidence ellipse in the local East-North tangent plane, centred on
the accompanying `FixEvent.position`.

- `semi_major_m`, `semi_minor_m` — ellipse semi-axis lengths in metres.
  Validator enforces `semi_major_m >= semi_minor_m`; if a producer wants to
  swap, it must rotate `orientation_deg` by 90° to keep semantics consistent.
- `orientation_deg` — angle of the semi-major axis from local East toward
  North, ENU-plane mathematical positive, range [-180, +180]°.

**Convention.** By contract, the ellipse renders the **95% confidence
contour** of a 2-D Gaussian. For a Gaussian, that is the
`sqrt(chi²_inv(0.95, df=2)) ≈ 2.448` σ contour: semi-axis = 2.448 ·
σ_principal, where σ_principal is the square root of the covariance matrix's
eigenvalue along that axis. The fusion workstream owns this conversion in
`covariance.py`; the contract carries the rendered ellipse so consumers
(CoT, dashboard) never need to scale.

`area_m2` is a derived property (π · a · b), handy for logs and for the demo
visual of "ellipse shrinking as nodes are added". Not on the wire; computed
on demand.

**Producer:** the fusion solver (always paired with `covariance_m2` on a
`FixEvent`). **Consumer:** the CoT publisher (renders as a polygon
approximation), the ops dashboard (renders as the SVG ellipse).

---

## §3 Wire-format messages (`rfmesh_contracts.messages`)

Three message types cross workstream boundaries. They are the contract that
makes parallel workstream development possible.

### `BearingReport`

**The atom of the system.** One node's estimate of the azimuth to one
emitter, at one instant.

**Producer:** the DSP workstream's L1 or L2 estimator (`l1_rssi.py` or
`l2_music.py`), wrapped by the node runtime in `rfmesh-node`.
**Consumer:** the fusion workstream's `Fuser.fuse()`, the dashboard.

**Fields and their meanings:**

- `schema_version` — pinned to `SCHEMA_VERSION`, see §0.
- `node_id` — stable identifier of the producing node; matches the
  `NodeConfig.node_id` of that node. Used by fusion to associate a bearing
  with the node's position (carried separately in `node_position`, but
  `node_id` is the cross-reference key for dashboards and de-duplication
  across redundant bearers).
- `t_unix_ns` — acquisition timestamp, integer nanoseconds. **Fusion
  batches bearings into short windows** (the default `FusionConfig.
  batch_window_ms = 100 ms`) by this value, so all bearings inside a window
  refer to "the same instant". Node clocks are NTP-disciplined to ~10 ms,
  which is sufficient for AoA — see `ARCHITECTURE.md` §6 on why we do not
  need ns-level sync.
- `node_position` — where the node was when it took this bearing. Carried
  *with every report* (not looked up separately) so a moving or repositioned
  node is handled correctly and so the fusion solver is stateless w.r.t.
  node positions. For the BoTH3 deployment, nodes are static during a fix;
  this still costs nothing and avoids a future-trap.
- `azimuth_deg` — estimated geographic azimuth, [0, 360), degrees, true
  north = 0, clockwise positive. Antenna heading correction has *already*
  been applied at the node (the node's own `heading_deg` from its config).
  Fusion does *not* re-rotate; it consumes geographic bearings.
- `azimuth_sigma_deg` — 1-σ uncertainty of `azimuth_deg`, degrees, strictly
  > 0. **The weight the fusion solver uses (inverse-variance).** Must be an
  honest estimate from the node's own SNR, pseudospectrum sharpness, and
  calibration quality. The DSP workstream's sigma estimation is itself
  tested against simulator ground truth; honesty here is load-bearing for
  the whole system — an over-optimistic sigma poisons fusion for every other
  node contributing to the same fix.
- `method` — which `Capability` produced this bearing. Lets fusion and the
  dashboard distinguish coarse from fine bearings for display and
  diagnostics, without changing how they are mathematically combined (which
  is `azimuth_sigma_deg`-weighted).
- `snr_db` (optional) — SNR of the emitter at this node, dB above the
  node's noise-floor estimate. Diagnostic only — the bearing's weight is
  `azimuth_sigma_deg`, not this. `None` means "not reported"; not all DSP
  paths estimate it.
- `emitter_class` (optional) — L3 classifier label for this emitter, if
  this node ran classification.
  **`None` vs `EmitterClass.UNKNOWN` is a meaningful distinction:**
    - `None`: the node did not classify (no L3 capability, or classifier did
      not run on this snapshot).
    - `EmitterClass.UNKNOWN`: the classifier did run and is not confident
      enough to assign a label.
  Consumers must preserve the distinction; fusion aggregates accordingly.
- `classification_confidence` (optional) — classifier probability for
  `emitter_class`, in [0, 1]. `None` iff `emitter_class` is `None`. The
  *quantitative* classifier output; the discretized band for display lives
  on `FixEvent.confidence_level`.
- `raw_pseudospectrum` (optional, debug payload) — the L2 MUSIC/Capon
  pseudospectrum as little-endian float32 log-magnitude samples over
  [0, 360) degrees at **0.5° angular step** (so 720 samples = 2880 bytes;
  validator does not enforce this, but the convention is what the dashboard
  expects). Present only on the Wi-Fi bearer (LoRa drops it for bandwidth).
  Consumed by the ops dashboard to render the live pseudospectrum tile;
  fusion ignores it.

**Acceptance rules for a `BearingReport` to be useful to fusion:**

1. `azimuth_sigma_deg` is honest — derived from this node's own SNR /
   pseudospectrum / calibration, not a constant default.
2. `t_unix_ns` is within the active fusion window.
3. `node_position` is current (matches the producing node's last
   `NodeStatus.position` within the same NTP-tolerance).
4. The node is not flagged stale by fusion (see `FusionConfig.
   node_stale_after_s`).

A report that fails (1) is the worst silent corruption mode in the system —
hence the DSP workstream tests this with ground-truth assertions in the
simulator and the lead reviews any sigma-estimation change as if it were a
contract change.

### `FixEvent`

**A fused emitter geolocation.** The cross-fix of several `BearingReport`s,
with full honesty payload.

**Producer:** the fusion workstream's `Fuser.fuse()`.
**Consumer:** the CoT publisher (renders as a hostile-emitter marker with
ellipse polygon in ATAK), the ops dashboard.

**Fields and their meanings:**

- `schema_version` — pinned, see §0.
- `fix_id` — UUID, unique per fix. A moving emitter produces a *stream* of
  `FixEvent`s, each with a distinct `fix_id`; the CoT layer may use a stable
  *track* UID separately (a moving emitter is one track, many fixes). Logs,
  diagnostics, and the dashboard refer to individual solutions by `fix_id`.
- `t_unix_ns` — representative timestamp, typically the midpoint of the time
  window of the contributing `BearingReport`s.
- `position` — estimated emitter position, WGS-84, as `GeodeticPosition`.
  This is the centre of `confidence_ellipse_95`. Its `sigma_m` is a scalar
  convenience summary; the ellipse is authoritative.
- `covariance_m2` — the 2×2 position covariance in local ENU as
  `(σ_xx, σ_xy, σ_yy)` in square metres, the unique entries of the
  symmetric matrix `[[σ_xx, σ_xy], [σ_xy, σ_yy]]`. Raw solver output.
  `confidence_ellipse_95` is its geometric rendering; both are carried so a
  downstream tracker (a Kalman filter, say) can consume the matrix without
  having to invert the ellipse. The fusion solver uses ENU local to the
  fix's geometry; consumers needing global covariance reproject if needed.
- `confidence_ellipse_95` — 95% confidence ellipse in ENU, centred on
  `position`. The human-facing uncertainty. Derived from `covariance_m2` by
  the chi-square scaling described in §2.
- `confidence_level` — `ConfidenceLevel`. The discretized display band.
  **Fusion's policy for setting this** (binding):
    - `HIGH` iff `gdop ≤ FusionConfig.gdop_warn_threshold` (default 6.0)
      *and* `confidence_ellipse_95.semi_major_m` is below an operational
      tolerance (workstream-C-set, typically a fraction of fix range) *and*
      no residual exceeds 3σ of its node's sigma.
    - `LOW` iff `gdop` is above threshold *or* `semi_major_m` exceeds
      tolerance *or* the solver fell back to `fallback_centroid`.
    - `MEDIUM` otherwise.
- `contributing_nodes` — tuple of `node_id`s of the bearings used, in
  *the same order as `residuals_deg`*. Length is the number of bearings
  fused, ≥ 2 (the minimum per `FusionConfig.min_bearings_for_fix`).
- `residuals_deg` — per-node angular residual after the fit, degrees:
  measured bearing minus the bearing the solved position implies, for each
  node in `contributing_nodes`, same order, same length. **The system's
  self-diagnosis channel.** A node whose residual is many σ from zero (σ
  here being that node's reported `azimuth_sigma_deg`) is highlighted by the
  dashboard as a probable multipath/calibration outlier; the operator gets
  to see *which* node is misbehaving, not just that the fix is loose.
- `gdop` — Geometric Dilution of Precision for this fix's sensor geometry.
  Strictly > 0; low (~1–3) means favourable node placement, high (> ~6)
  means near-collinear geometry that stretches the ellipse regardless of
  per-bearing quality. Reported on the wire so the operator understands
  *why* a fix is loose and where to reposition.
- `method` — string, the solver mode used. Set values at v1.0.0:
    - `"stansfield"` — closed-form weighted least-squares only (no
      refinement).
    - `"stansfield+mle"` — Stansfield seed refined by maximum-likelihood
      Gauss-Newton iteration. The default; Stansfield alone is provably
      biased for finite samples, MLE refinement removes the bias.
    - `"fallback_centroid"` — degenerate geometry; the solver declined to
      trust the intersection and returned the weighted centroid of the
      bearing-line crossings. Always paired with `confidence_level = LOW`.
  Kept as a free-form `str` rather than an enum because the set of solver
  modes is an internal fusion concern that may evolve without a contract
  bump (consumers parse for display; they do not branch on it).
- `emitter_class` (optional) — consensus classification across the
  contributing nodes' `BearingReport`s, if any classified. `None` means no
  node classified; `EmitterClass.UNKNOWN` means nodes classified but did
  not agree or were individually unsure. **Geolocation never depends on
  this** — classification is a metadata overlay, not a gate on the fix.

### `NodeStatus`

**Periodic node health heartbeat.** Deliberately small: it rides the same
bearers as `BearingReport`s, and on LoRa every byte matters.

**Producer:** every node, on `BearerConfig.heartbeat_interval_s` (default
2.0 s). **Consumer:** the fusion workstream (decides which nodes are live;
a silent node is excluded from new fixes), the ops dashboard.

**Fields and their meanings:**

- `schema_version` — pinned, see §0.
- `node_id` — stable identifier, matches `NodeConfig.node_id`.
- `t_unix_ns` — heartbeat timestamp.
- `position` — the node's current self-reported position. Usually static for
  this project's deployments, but carried every heartbeat so a repositioned
  node updates fusion and the dashboard without a separate mechanism.
- `active_capabilities` — what this node is *actually running right now* —
  the result of intersecting `NodeConfig.capabilities` with detected
  hardware. If this is narrower than what the operator declared, the
  dashboard shows it; no silent downgrade.
- `gnss_locked` — whether the node currently has a valid GNSS fix. **False
  in a GNSS-jammed environment** — itself an EW indicator worth surfacing,
  *not* a mode of failure. Per `ARCHITECTURE.md` §6, the mesh does not
  depend on GNSS for positioning or timing; this flag exists so a
  deployment can *observe* GNSS denial. When false, `position` is the
  last-known fix (or the survey-of-record value).
- `healthy` — node-level self-assessment, the one-glance red/green for the
  dashboard. True iff the SDR is streaming, processing keeps up, and the
  bearer is connected. False otherwise.
- `status_detail` — short human-readable elaboration, especially when
  `healthy` is False, e.g. `"SDR overflow"`, `"LoRa bearer down, Wi-Fi only"`.
  Empty string when nothing to add. Free-form but conventionally < 80 chars.

---

## §4 Configuration schemas (`rfmesh_contracts.config`)

YAML files load into these. Validation is at load time; a malformed config
is rejected before any hardware is touched.

### `SDRConfig`

Which radio a node uses and how it is tuned. (Axis 1: SDR hardware type.)

- `driver` — `"rtlsdr" | "hackrf" | "bladerf" | "pluto" | "sim"`. The only
  place in a node's config that names a hardware family. The SDR workstream
  maps each value to a `Receiver` (or `CoherentReceiver`) implementation.
  `"sim"` selects the `SyntheticReceiver`, which satisfies the same Protocol
  with synthetic IQ — same pipeline, no hardware (see `ARCHITECTURE.md` §4).
- `serial` (optional) — hardware serial / identifier when several devices of
  the same `driver` are attached to one host. `None` means "first device the
  driver finds"; ignored for `sim`. For RTL-SDR specifically, the old repo
  documented a `--device-serial` foot-gun (index-based addressing is
  fragile); the SDR workstream resolves to serial when possible.
- `sample_rate_hz` — requested ADC sample rate. The achievable rate is
  driver-dependent — RTL-SDR V4 is reliable to ~2.4 MS/s, bladeRF to tens of
  MS/s — and the `Receiver` implementation reports the *actual* rate it
  obtained through `ReceiverCapabilities.actual_sample_rate_hz`. A
  meaningful shortfall is surfaced (Invariant 4), not hidden.
- `center_freq_hz` — tuner centre frequency. For a coherent 2-RX device,
  *both* channels share this LO — which is exactly what makes them
  phase-coherent, so a single value here is correct.
- `gain_db` — receiver gain in dB, or the string `"auto"` for the driver's
  AGC. **Prefer an explicit number for DF work**: a fixed, known gain keeps
  the noise floor stable across an antenna sweep, which matters for honest
  SNR and sigma estimates. `"auto"` is bring-up convenience, not measurement.
- `bias_tee` — boolean. Default False. Enabling DC on the antenna port into
  hardware that does not expect it can release magic smoke; explicit opt-in.

### `ArrayConfig`

Physical antenna-array description for L2 nodes. Present iff `NodeConfig`
declares an L2 capability; cross-validated.

- `geometry` — `ArrayGeometry`. Selects the steering-vector model.
- `n_elements` — number of antenna elements / coherent RX channels, ≥ 2.
- `element_spacing_m` — for ULA/UCA only (parametric geometries). ULA: the
  inter-element spacing along the array axis. UCA: the array *radius*.
  Typically λ/2 at the design frequency (~16.4 cm at 915 MHz, ~6.2 cm at
  2.4 GHz). Validator enforces: required for ULA/UCA, must be `None` for
  CUSTOM.
- `element_positions_m` — for CUSTOM only. Tuple of (x, y) coordinates in
  metres in the array's local frame. Validator enforces: required for
  CUSTOM, must be `None` for ULA/UCA, and length must equal `n_elements`.
- `calibration_file` (optional) — path to a per-element phase/gain
  calibration table (frequency-dependent complex offsets) produced by the
  SDR workstream's calibration routine. `None` means "not yet calibrated"
  — and an L2 node asked to run MUSIC without a calibration **must fail
  loudly, not silently emit garbage bearings** (Invariant 4 again,
  surfaced specifically because phase DF without per-channel calibration
  is a classic silent-failure mode).

### `BearerConfig`

Transport configuration. Absorbed entirely by the node-runtime workstream.

- `kind` — `BearerKind`. The selector.
- `lora_serial_port` (optional) — serial device for the LoRa radio (e.g.
  `"/dev/ttyUSB0"`). Validator: required when `kind` is `LORA` or `BOTH`,
  must be `None` for `WIFI` (a stray value from a copy-pasted config implies
  hardware that is not there — caught at load).
- `heartbeat_interval_s` — how often the node emits a `NodeStatus`, seconds,
  > 0. Default 2.0. `FusionConfig.node_stale_after_s` is a small multiple
  of this (default 6.0 = 3× heartbeats), so a node is dropped from fixes
  after missing three heartbeats.

### `NodeConfig`

The complete configuration of one mesh node. Axes 1 and 2 converge here.

- `schema_version` — pinned, see §0.
- `node_id` — stable, unique identifier, e.g. `"node-rtl-01"`. Uniqueness
  across the mesh is the operator's responsibility.
- `position` — `GeodeticPosition`. The surveyed/known position. A node with
  GNSS overrides this at runtime from its fix (and the runtime updates
  `NodeStatus.position`); this config value is the fallback and the
  bench/sim value. **Accurate node positions matter** — position error
  propagates linearly into fix error; honest `sigma_m` is part of the
  fusion weight.
- `heading_deg` (optional) — mounting heading of the directional antenna or
  array boresight, degrees true, clockwise from north, range [0, 360).
  Required for an L1 node (the servo sweep is relative to this) and for a
  ULA L2 node (the array axis orientation). May be `None` for a UCA node
  (rotationally symmetric). Validator enforces. **Set by survey-and-align,
  not by magnetometer** — see `ARCHITECTURE.md` §6 and
  `INHERITED_CONTEXT.md` on why magnetometers at the mast are unreliable.
- `sdr` — `SDRConfig`.
- `array` (optional) — `ArrayConfig`. Required iff the node declares an L2
  capability; must be `None` for L1-only. Validator enforces.
- `capabilities` — tuple of `Capability`, length ≥ 1. The operator's
  intent. Intersected at startup with hardware-detected capability; a
  declared capability the hardware cannot meet is fatal.
- `bearer` — `BearerConfig`.
- `fusion_endpoint` — URL where this node ships reports (e.g.
  `"udp://10.0.0.1:9000"`). The node is otherwise unaware of the rest of
  the mesh; the star topology keeps nodes stateless about each other.

### `FusionConfig`

Central fusion server configuration. Note what is *not* here: **any notion
of the expected number of nodes.** That is Axis 3 absorbed by absence —
the solver accepts whatever bearings arrive and cross-fixes any N ≥ 2.

- `schema_version` — pinned, see §0.
- `listen_url` — where the fusion server receives node reports. Must match
  the nodes' `fusion_endpoint`.
- `batch_window_ms` — width of the time window over which `BearingReport`s
  are grouped before solving a fix, milliseconds. Default 100.0. Wide
  enough to gather all nodes' reports for "one instant"; narrow enough that
  a moving emitter has not travelled far within it. Tunable per scenario.
- `node_stale_after_s` — silence threshold for excluding a node from new
  fixes, seconds. Default 6.0. A small multiple of the nodes'
  `heartbeat_interval_s`. The mechanism behind the "pull a node's antenna
  mid-demo, watch the ellipse grow" robustness story.
- `min_bearings_for_fix` — minimum bearings required to attempt a fix.
  Default 2; hard floor 2 (one bearing is a ray, not a fix). Three or more
  yields residuals for self-diagnosis (see `FixEvent.residuals_deg`).
- `gdop_warn_threshold` — GDOP above which a fix is flagged as
  geometrically weak. Default 6.0. **Not a reject threshold** — a weak fix
  is still information, honestly labelled (`confidence_level = LOW`).
  Display and warning only.
- `cot_url` (optional) — where the CoT publisher ships `FixEvent`s (e.g.
  `"tcp://10.0.0.2:8087"` for a FreeTAKServer). `None` disables CoT output,
  useful for headless bench runs that only watch the ops dashboard.

---

## §5 Behavioural contracts (`rfmesh_contracts.protocols`)

Behaviour, not data: the abstract operations one workstream may assume
another provides. All are `typing.Protocol` (structural typing — an
implementation conforms by shape), several are `@runtime_checkable` so
tests can `isinstance`-check cheaply. The full method signatures and
docstrings live in `protocols.py`; this section explains *what each
Protocol is for* and *who depends on whom*.

### `Receiver`

A source of single-channel baseband IQ. The L1 signal-input contract.

- **Implementers (`rfmesh-sdr`):** `SoapyReceiver` (generic, covers
  RTL-SDR / HackRF / single-channel bladeRF / single-channel Pluto via
  SoapySDR), the legacy `RTLSDRDevice` ported to the Protocol (kept as a
  proven `rtl_sdr`-subprocess path; see `SALVAGE_AUDIT.md` Part 4d), and
  `SyntheticReceiver` (the simulator — same Protocol, generated IQ, no
  hardware).
- **Consumers (`rfmesh-dsp` L1, via `rfmesh-node`):** read IQ blocks,
  produce `BearingReport`s.

The hard guarantee a `Receiver` makes — and the consumer may rely on:
`read(n)` returns exactly `n` samples or it raises. A silent short read
would corrupt every downstream estimate. (Invariant 4.)

### `CoherentReceiver` (extends `Receiver`)

A source of phase-coherent multi-channel IQ. The L2 signal-input contract.
Because it extends `Receiver`, an L2-capable node can still do L1 on
channel 0 with the same instance.

- **Implementers (`rfmesh-sdr`):** `BladeRFCoherentReceiver` (likely native
  `libbladeRF` for clean coherent control), `PlutoCoherentReceiver` (via
  `pyadi-iio` in `ad9361` 2r2t mode — requires the documented Pluto
  firmware mode-switch), `SyntheticReceiver` in coherent mode.
- **Consumers (`rfmesh-dsp` L2):** read coherent IQ blocks, produce L2
  `BearingReport`s.

The hard guarantee: `read_coherent(n)` returns a `(n_channels, n)`
sample-aligned, phase-coherent block, *to the extent the last successful
`calibrate()` established*; `is_calibrated` truthfully reports whether
that has happened. **L2 DSP code refuses to emit bearings from an
uncalibrated coherent stream.** Calibration is not implicit and never
silently bypassed.

### `BearingEstimator`

Turns IQ into a `BearingReport`. The contract DSP exposes to the node.

- **Implementers (`rfmesh-dsp`):** L1 amplitude-sweep estimator (consumes
  1-D IQ), L2 MUSIC estimator (consumes 2-D coherent IQ), any future
  estimator that satisfies the shape.
- **Consumers (`rfmesh-node`):** the node runtime owns one or more
  estimators per its active capabilities, feeds them samples, ships the
  reports.

`method` (a property) advertises which `Capability` the estimator
implements, so the runtime can match estimators to active capabilities
without hard-coding classes. `estimate()` returns `BearingReport | None`,
and **`None` is a valid, expected outcome** (emitter below threshold,
pseudospectrum too flat to peak-pick, uncalibrated input) — *not* an error.
Fabricating a confident bearing from nothing is what an estimator must
never do (Invariant 4 again — it appears wherever silent failure modes
are most tempting).

### `Fuser`

Cross-fixes `BearingReport`s into a `FixEvent`. The fusion contract.

- **Implementer (`rfmesh-fusion`):** the Stansfield + MLE solver, with its
  GDOP gate and `fallback_centroid` degenerate-geometry path.
- **Consumers (`rfmesh-node`'s fusion-side runtime, the ops dashboard):**
  feed batches, receive fixes.

`fuse()` takes any iterable of `BearingReport`s — indifferent to count
(Axis 3) and to method (Axes 1/2). Returns `FixEvent | None`; `None`
means "cannot responsibly solve" (below `min_bearings_for_fix`, or
geometry so degenerate that even `fallback_centroid` is undefensible).
A returned `FixEvent` always carries its honesty payload; weak-but-real
fixes are *labelled* weak (`confidence_level = LOW`), not withheld.

### `CotPublisher`

Publishes a `FixEvent` as Cursor-on-Target. One method.

- **Implementer (`rfmesh-cot`):** PyTAK-backed, serialises a `FixEvent`
  to CoT XML (hostile-emitter marker + `<shape><ellipse>` polygon from
  `confidence_ellipse_95`) and ships to the configured TAK endpoint.
- **Consumer:** the fusion workstream's publish step, or the ops layer.

`publish()` is synchronous from the caller's view; transport queueing is
the implementer's business. A transmit failure raises — not silently
swallowed.

### `Bearer`

Transport between a node and the fusion server. Internal to the
node-runtime workstream; in the contract only because the runtime's
producer side and transport side are tested against each other
independently.

- **Implementers (`rfmesh-node`):** `WifiBearer` (UDP + msgpack),
  `LoraBearer` (compressed, drops `raw_pseudospectrum`), and a composite
  `BothBearer` that de-duplicates by `(node_id, t_unix_ns)`.
- **Consumers:** node producer side (sends `BearingReport`s and
  `NodeStatus`es), fusion-server side (receives).

Send is split by message type (not a union) because LoRa treats them
differently — `BearingReport`s may be heavily compressed on LoRa,
`NodeStatus`es are already tiny. Receive returns *messages since the
last call*; ordering is best-effort, fusion time-windows regardless.

### Type aliases for the signal path

`IQBlock` and `CoherentIQBlock` are NumPy `complex64` array aliases. The
shape and dtype conventions (1-D `(n,)` and 2-D `(n_channels, n)`,
respectively) are part of the contract; the alias is *not* a class
because forcing every hot IQ buffer through a Pydantic model would be
absurd. Workstreams' tests assert the shape and dtype at API boundaries.

---

## §6 Change-control reminders

1. The schemas in `packages/rfmesh-contracts/` are authoritative. This
   document mirrors them; on disagreement, the schema wins and the doc is
   a bug to file against the lead.
2. A field's semantics — what it *means*, not what type it is — change only
   by ADR. A wording fix here without a schema change is fine. A wording
   change that implies a semantic change is a contract change in disguise
   and goes through ADR.
3. `SCHEMA_VERSION` is the tripwire. Any contract change ends with a bump.
4. If you, an agent or a reviewer, find an ambiguity in this document, do
   not resolve it in code — file a question to the lead. Two reasonable
   interpretations of the same field across two workstreams is exactly the
   integration-day failure this whole governance layer exists to prevent.
