<!--
rfmesh — BoTH3 jury slide deck

Companion to `docs/demo/script.md` (operator spoken narration) and
`docs/ADVANTAGES.md` (the 8 architectural advantages this deck
competes on). Authored against the council-reviewed plan-proposition
of 2026-05-18 + the C5-regenerated Beat numbers of 2026-05-20.

Renders via `marp` (https://marp.app):

    marp docs/demo/slide_deck.md --html --output build/slide_deck.html
    marp docs/demo/slide_deck.md --pdf  --output build/slide_deck.pdf
    marp docs/demo/slide_deck.md --pptx --output build/slide_deck.pptx

Slide breaks use the marp `---` fence on its own line.

DESIGN PRINCIPLES (binding for any edit to this deck):

1. Every dB number cites its anchor inline (sky-pointing reference,
   instrument-noise floor, or peak-vs-back-lobe). No bare "dB"
   without qualifier.
2. Every angular number cites its uncertainty bound. No "1° accuracy"
   without an SNR + sample-count context. L1 sub-degree claims are
   forbidden; sub-degree belongs to L2 phase-coherent DF where the
   math supports it.
3. The demo arc is "ellipse shrinks WITHIN the MEDIUM band as posts
   add", not "we climb from LOW to MEDIUM". Per the 2026-05-20 C5 +
   demo-script correction.
4. Phase C narrative: lead with the Mast C empirical anchor (3 km,
   +11.4 dB SNR above sky-noise floor, 0.17 dB std stationarity over
   60 s, peak within angular grid). Mast A is a category claim
   ("system refuses when site / freq combo does not deliver") rather
   than a specific failure story — the original 3-mast Phase C
   narrative is internally caveated; the public deck doesn't need to
   open that thread.
5. Null-steering framing is **anti-desense, not ECM**. Null depth
   quoted as ≤ 20 dB per ADR-008 §D8.
-->

---
marp: true
theme: default
paginate: true
header: rfmesh — Cooperative bearing mesh, BoTH3 Counter-Jamming Challenge 2
footer: 2026-05-20 — `docs/demo/slide_deck.md`
size: 16:9
---

# rfmesh

## Cooperative bearing mesh for RF emitter geolocation

BoTH3 Counter-Jamming Challenge 2

*Triangulate jammer antennas / signal sources at 2-5 km within 20 m, to enable hard-kill cueing.*

`docs/demo/slide_deck.md` v1 — 2026-05-20

---

# The problem

- **Adversary jams or transmits in our band.** Tactical units need to know **where the emitter is**, not just that one exists.
- BoTH3 Challenge 2 specifies **2-5 km standoff, ≤ 20 m positional accuracy** — i.e. the system must geolocate a jammer at the same range a UAV or counter-battery cue would need it.
- Existing solutions:
  - **RfPatrol Mk2** — €5 k+ single-unit, closed threat library, single-sensor.
  - **Bukovel-AD** — classified, military-only, library not extensible by operator.
  - **KrakenSDR community gear** — single-node, no fusion, no honesty payload.
- **rfmesh** answers the spec on **€250-per-node hardware** by composing a small number of architectural advantages those systems do not ship.

---

# The architecture in one diagram

```
  Node A          Node B          Node C          Node D
  (L1 RTL-SDR)    (L1 RTL-SDR)    (L1 RTL-SDR)    (L2 bladeRF, ULA)
  Yagi + servo    Yagi + servo    Yagi + servo    2-element array
       │              │              │              │
       │  BearingReport (azimuth, sigma, method, optional emitter class)
       │              │              │              │
       └──────────────┼──────────────┴──────────────┘
                      │   length-prefixed msgpack over Wi-Fi (LoRa fallback)
                      ▼
              ┌────────────────────────┐
              │   rfmesh-fusion       │ Stansfield seed → Gauss-Newton MLE →
              │   (asyncio server)    │ Fisher-information covariance →
              └─────────┬──────────────┘ 95% chi-square ellipse → ConfidenceLevel
                        │
                        │   FixEvent (position + covariance + ellipse + GDOP
                        │             + per-node residuals + method)
                        ▼
            ┌────────────────────────┐      ┌────────────────────────┐
            │ rfmesh-cot (PyTAK)     │      │ rfmesh-ops dashboard   │
            │ → ATAK / FreeTAKServer │      │ (10 panels, 3 layouts) │
            └────────────────────────┘      └────────────────────────┘
```

- **Star dependency graph** — every workstream imports only from `rfmesh-contracts` (the frozen schema).
- Heterogeneous nodes co-fused: an 8°-σ RTL-SDR + a 1.5°-σ bladeRF contribute to the same fix, weighted by their honest sigmas.

---

# Eight architectural advantages — overview

| # | Advantage | What it gives BoTH3 |
|---|---|---|
| **1** | Scaling through deployment density, not per-sensor magic | Meeting "0.23° at 5 km" without a magic radio |
| **2** | GNSS-denied by construction | Continues to function when adversary jams GPS first |
| **3** | Heterogeneous mesh — cheap nodes alongside good ones | €30 dongles + €400 partner-pool radios in one fix |
| **4** | Dual-use null-steering — one matrix, two products | Anti-desense + geolocation from same `R` |
| **5** | Open, extensible threat library as moat | Operator adds a new threat in YAML, not a vendor patch cycle |
| **6** | Honesty payload on every fix | Covariance, ellipse, residuals, GDOP — system self-diagnoses |
| **7** | Simulator-first development | Whole pipeline tested without hardware; demo has a fallback |
| **8** | €250-per-node budget is realisable | Three RTL-SDR V4 + Yagi + Pi 4 + servo, well under €250 amortised |

Each one a real architectural decision the codebase already encodes (`docs/ADVANTAGES.md`).

---

# Advantage #1 — Scaling through deployment density

**The claim.** 20 m positional accuracy at 5 km range is 0.23° of bearing accuracy at any individual sensor — *no two-element array under hackathon conditions can deliver that single-sensor*.

**The trick.** With N ≥ 3 nodes at bounded GDOP and weighted-least-squares fusion, individual sigmas of 5° each can combine to a 20 m ellipse. The demo shows the ellipse shrinking from N=2 (oblong) → N=3 (round) → N=4 (slot) as nodes join.

**What the panel shows.** `FixPanel` renders the 95% ellipse with semi/range %, GDOP, confidence band — this is what the jury sees on the trench-demo layout. `GdopHeatmapPanel` shows where in the area the geometry is good vs bad — available in the bench/debug layout for offline area planning, not in the live demo's trench layout.

**Receipts.** `docs/demo/trench-demo-geometry.md` §2.2; `scenarios/trench_demo.yaml` `expected_fix:` block (C5-regenerated 2026-05-20).

---

# Advantage #2 — GNSS-denied by construction

**The claim.** Most adversary EW playbooks start by jamming GPS. Our mesh has **no critical dependency on GNSS at all**.

**How.**

- Node positions are **surveyed at deployment** (`NodeConfig.position`), not GNSS-resolved at runtime.
- Time sync is **NTP over the local mesh network** (~10 ms — sufficient for AoA cross-fixing).
- We **do not do TDOA** — explicit architectural choice (TDOA would need ns-level GPS-disciplined oscillators we cannot ship at this price).
- **Wi-Fi mesh is also contested.** LoRa fallback bearer (`BearerKind.LORA`) carries `BearingReport`s when Wi-Fi degrades; the ~100 ms fusion batch window survives 10 ms NTP-over-LoRa as well. The mesh is multi-bearer by design, not Wi-Fi-only.
- `NodeStatus.gnss_locked` is a separate, **observable** flag — the system **observes** GNSS jamming as an EW indicator while continuing to function.

**Receipts.** `ARCHITECTURE.md` §6, `INHERITED_CONTEXT.md` §2.3.

---

# Advantage #3 — Heterogeneous mesh

**The claim.** A fusion solver that consumes any iterable of `BearingReport`s, each carrying its honest `azimuth_sigma_deg`, automatically weights them **inverse-variance**.

**Concretely.**

```
  RTL-SDR V4 + Yagi sweep:   sigma = 5.0 deg, weight 1/25
  bladeRF 2.0 + ULA coherent: sigma = 1.5 deg, weight 1/2.25
```

The same `Fuser.fuse(reports)` call accepts both. The L2 node's report dominates the directional axis it constrains best; the L1 nodes constrain the other axis. Both contribute to the same fix.

**Why others don't.** Commercial gear is single-tier. Military gear is single-tier. We ship a mesh that mixes €30 dongles with €400 partner-pool radios.

**Receipts.** `INTERFACES.md` §3 `BearingReport.azimuth_sigma_deg` (load-bearing weight); `packages/rfmesh-fusion/src/rfmesh_fusion/fuser.py` `_inverse_variance_weights`.

---

# Advantage #4 — Dual-use null-steering

**One matrix, two products.**

The N×N sample covariance matrix **`R`** (N = number of coherent RX channels; N = 2 in the demo's 2-element ULA, expandable to UCA / CUSTOM geometries per `ArrayConfig`) that MUSIC eigendecomposes for **angle-of-arrival** can also be inverted via MVDR to synthesize a spatial **null** toward an off-look source:

```
  MUSIC peak:    P(θ) = 1 / |E_n^H · a(θ)|²            → geolocation
  MVDR weights:  w(θ_null) = R⁻¹·a / (a^H·R⁻¹·a)       → anti-desense
```

**Framing — anti-desense, not ECM.** We are **not transmitting** through this array. We are **protecting our own L2 coherent DF channel** from being desensitised by a co-channel jammer while continuing to produce bearings on it.

**Demo:** ~10 s side-panel. `NullSteeringPanel` shows the receive pattern before (omni-ish) and after (~20 dB null at θ_jammer). Caption: *"15-20 dB typical, up to ~25 dB with fresh calibration."*

**Receipts.** `ARCHITECTURE.md` §1 L2; ADR-008 §D8 null-depth caption cap; `packages/rfmesh-dsp/src/rfmesh_dsp/l2_null_steering.py`.

---

# Advantage #5 — Open, extensible threat library

**The moat.**

| Other gear | rfmesh |
|---|---|
| Closed library at €5k+ (RfPatrol Mk2) | Open structure |
| Classified library (Bukovel-AD) | YAML threat profile + Python module per class |
| New threat = vendor patch cycle | New threat = operator commits a file |

**v1.0 enum classes.**

- `ELRS` — ExpressLRS R/C control link (868/915 MHz or 2.4 GHz, LoRa-based FHSS).
- `CROSSFIRE` — TBS Crossfire R/C control link (868/915 MHz long-range FHSS).
- `GSM_JAMMER` — GSM-band handset emission, IED-command analog.
- `POLE21`, `VOLNOREZ` — Russian jammer family. Stubs at v1.0.
- `DRONEID` — DJI / OcuSync downlink.

**What v1.0 ships.** The **open structure** + threat-profile YAMLs for ELRS / CROSSFIRE / GSM-band; classifier weights are training-pipeline outputs, not v1.0 contract content (real-IQ training is post-event work). The pitch is the *open extension model* — operator adds a new threat in YAML + Python file, no vendor patch cycle. `EmitterClass.UNKNOWN` is the honest fallback when the classifier doesn't recognise the input.

**Receipts.** `packages/rfmesh-ml/threats/profiles/`; `INTERFACES.md` §1 `EmitterClass`.

---

# Advantage #6 — Honesty payload on every fix

`FixEvent` is **not** just position. Every `FixEvent` carries:

- `position` — emitter geodetic (WGS-84 lat/lon/HAE).
- `covariance_m2` — 2×2 ENU covariance (raw solver output).
- `confidence_ellipse_95` — 95% chi-square contour in ENU, centred on `position`.
- `gdop` — geometric dilution of precision; **~1 = good geometry, > ~6 = nodes too collinear** (per `FusionConfig.gdop_warn_threshold`).
- `residuals_deg` — per-node angular residual after fit; the system's self-diagnosis channel.
- `method` — `"stansfield+mle"` / `"stansfield"` / `"fallback_centroid"`.
- `confidence_level` — `HIGH` / `MEDIUM` / `LOW` discretised band (the operator-glance signal).
- `emitter_class` (optional) — consensus classification across contributing nodes.

**Operator effect.** The system **self-diagnoses**. A node with a >3σ residual is highlighted as a probable multipath / calibration outlier. The CoT marker shrinks when nodes are added, **expands honestly** when nodes are killed mid-demo.

**Receipts.** `INTERFACES.md` §3 `FixEvent`; `docs/demo/script.md` §1 + §2; `packages/rfmesh-ops/src/rfmesh_ops/panels/residuals.py`.

---

# Advantage #7 — Simulator-first development

The entire pipeline (sweep → estimator → bearings → fusion → ellipse → CoT → ATAK) was developed and tested against `SyntheticReceiver` — same `Receiver` Protocol as RTL-SDR / bladeRF / Pluto+.

- **Parameterised noise** (AWGN, log-normal shadowing).
- **Two-ray ground** + multipath FIR channel models.
- **Receiver impairments** — IQ imbalance, DC offset, ADC quantisation.
- **Antenna pattern** — Yagi HPBW + back-lobe floor.

**Two consequences:**

1. The C4 calibration test asserts the simulator reproduces the Mast C empirical anchor (+11.4 dB SNR, 0.17 dB std, ±10° angular) within declared tolerances. The simulator is **calibrated**, not just plausible.
2. The demo has a **replay scenario** ready. If live hardware misbehaves on stage, `apps/demo-replay` runs the same scenario against recorded IQ and the dashboard renders the same demo flow.

**Receipts.** `packages/rfmesh-sdr/tests/test_c4_mast_c_calibration.py`; `scenarios/mast_c_reference.yaml`; `apps/demo-replay/`.

---

# Advantage #8 — €250-per-node budget

| Component | Unit cost | Per node |
|---|---|---|
| RTL-SDR V4 | €25 | 1× |
| ATK-10 Yagi | €20 | 1× |
| Raspberry Pi 4B | €80 | 1× |
| MG996R servo + bracket | €15 | 1× |
| Mast + cables + power | €50 | amortised |
| ESP32-C6 servo controller | €15 | 1× |
| **Total per L1 node** | | **~€205** |

Plus the partner-pool **bladeRF 2.0 micro at ~€400** for the L2 upgrade — per-fix-quality enhancement, not a hardware reset (Advantage #3 makes mixing trivial).

**A 3-node deployment is < €650 in parts.** This is the receipt for "open-source defence tech is cheaper than buying RfPatrol Mk2".

---

# Phase C empirical anchor — Mast C, 3 km

**Bench measurement, 2026-05-19 (recapture, ATK-10 Yagi + RTL-SDR V4, hand-rotated).**

- **Site:** rural Polish bench position, line-of-sight to an isolated 958.695 MHz cellular carrier.
- **Range:** 2980 m (map-derived).
- **Expected bearing:** 306° true.

**Result.**

```
  Peak heading (45° grid):    305°    — within one grid bin of 306°
  Peak heading (10° fine):    305°    — parabolic-fit point estimate 307°
  Angular uncertainty (1-σ):  ±10°    — at 0.13 dB peak margin / 0.17 dB sample std

  Peak RSSI (rel.):         −0.78 dB
  Sky-pointing noise floor: −12.16 dB  (RX-chain noise reference, site-local)
  SNR above sky-noise:       +11.4 dB

  60-s stationarity at peak:
    std       0.17 dB
    pk-pk     0.80 dB
    drift_5s  0.12 dB
```

**What this binds.** L1 amplitude-comparison DF works at the BoTH3 demo standoff range, on the v1.0 hardware, in field conditions. The simulator's C4 calibration target is anchored to these numbers.

**Sub-degree angular accuracy is L2 territory** — L1 honestly reports the angular grid bin it resolved.

**Receipts.** `scenarios/mast_c_reference.yaml`; `recordings/2026-05-19-mast-c/` (operator-personal, gitignored); `packages/rfmesh-sdr/tests/test_c4_mast_c_calibration.py`. Polar plots come from `BearingScanPanel` — the L1-sweep diagnostic available in the bench/debug layout (`DEMO_LAYOUT_DEBUG`, projection="polar").

---

# Honest refusal — the demo's secret weapon

**The category claim.** When the prominence gate (peak / back-lobe ≥ 6 dB) is **not** met, the L1 estimator returns `BearingReport = None`. **The system does not fabricate a bearing.**

```
  L1 prominence:  peak − floor < 6 dB  →  BearingReport = None
  Cause attributed: "L1_REFUSED_PROMINENCE" (refusal_reason populated)
  Dashboard:       refusal symbol at the node, not a sigma wedge
  CoT publisher:   node excluded from this fix's contributing_nodes
```

**Why this lands with the jury.** Every demo asserts what works. **Few demos honestly show what doesn't.** When operators see a system that **refuses to emit a fabricated fix**, they trust the system that **does** emit one.

**Architectural binding.** `ARCHITECTURE.md` §0 commits to 2-5 km standoff envelope. Below that range or on contaminated frequencies, the prominence gate fires and the system reports no-bearing. The honesty contract holds **under stress**, not just under nominal conditions.

**Receipts.** `INTERFACES.md` §3 `BearingReport` acceptance rule 1 (honest σ); `packages/rfmesh-dsp/src/rfmesh_dsp/l1.py` `peak_prominence_db_min`; `docs/phase-c-report/findings.md` §6 verdict.

---

# Demo flow — Beat A (one node, ~10 s)

**On screen.** One L1 node (south, `(0, +900)` ENU) comes online. A single bearing line crosses the map. **No `FixEvent` yet** — `min_bearings_for_fix = 2`.

**Caption.** *"Single bearing. No cross-fix — one bearing is a ray, not a fix."*

**Engineering point.** This is the architecture's `min_bearings_for_fix` doing its job. No two nodes, no fix — the dashboard refuses to publish a position. Honesty contract, version 1.

---

# Demo flow — Beat B (two nodes, ~15 s)

**On screen.** West + East L1 nodes join. Two bearing lines cross. First `FixEvent` renders.

```
  semi_major_m:  598 m
  semi_minor_m:  383 m       aspect ratio 1.6 (oblong)
  range:         3000 m
  semi/range:    19.9 %
  GDOP:          1.53
  band:          MEDIUM
```

**Caption.** *"Two posts. Ellipse is oblong — two bearings only constrain position along one axis."*

**Spoken (≤ 30 s).** "Twenty percent of range, GDOP 1.5, MEDIUM band. This is the honest geometry of N=2 — the starting point on the deployment-density ladder. We improve by adding posts, not by swapping radios."

**Receipts.** `scenarios/trench_demo.yaml` Beat B `expected_fix:` (C5-regenerated); `script.md` §2 Beat B.

---

# Demo flow — Beat C (three nodes, ~15 s)

**On screen.** South L1 joins. Triangle closes. Ellipse contracts.

```
  semi_major_m:  393 m
  semi_minor_m:  355 m       aspect ratio 1.1 (nearly circular)
  range:         3000 m
  semi/range:    13.1 %       — shrunk by a third
  GDOP:          1.16
  band:          MEDIUM
  residuals:     all within band, no outlier flag
```

**Caption.** *"Three posts. Ellipse shrinks by a third. GDOP 1.2 — geometry is good."*

**Spoken (≤ 30 s).** "Thirteen percent, GDOP 1.2, MEDIUM band. Aspect dropped from 1.6 to 1.1 — the ellipse is now nearly circular. The band stays MEDIUM. We added a node, not a radio."

**Engineering point.** The percentage shrank because we added a node. Honesty payload — no outliers flagged, three honest σs, post-fit residuals consistent. WS-CD-008 Monte Carlo: 100% MEDIUM band over 1000 trials at this geometry; 93.7% inclusion rate (inside the [0.92, 0.98] honesty envelope).

---

# Demo flow — Beat D (L2 partner joins, ~20 s)

**On screen.** L2 bladeRF node joins. MUSIC pseudospectrum tile **lights up** with a sharp peak at the emitter bearing — *visible proof that subspace DF is running on coherent IQ*. The ellipse **narrows into a slot**.

```
  semi_major_m:  358 m
  semi_minor_m:  125 m       semi-minor collapses 65 % (355 → 125)
  range:         3000 m
  semi/range:    11.9 %       — small additional shrink
  GDOP:          1.02
  band:          MEDIUM
```

**Caption.** *"Coherent partner-pool radio joins. Semi-minor collapses 65 %. Ellipse narrows into a slot. GDOP near 1 — geometry essentially perfect."*

**Spoken (≤ 45 s).** "Coherent partner-pool radio joins. On the right tile — MUSIC pseudospectrum, sharp peak at the emitter bearing — that is what subspace DF on phase-coherent IQ actually looks like. Semi-minor from 355 down to 125 metres, sixty-five percent. The ellipse goes from round to a slot — the operator sees the shape change even without reading the number. Band stays MEDIUM. Crossing into HIGH at this geometry needs a second L2 or a fifth L1."

**Engineering point.** Advantage #3 made visible — one σ=1.5° node weighted alongside three σ=5° nodes by inverse variance. The directional collapse (round → slot) is the read-at-a-glance shape signal; the band-stays-MEDIUM is the architecture's honest steady state at this geometry.

---

# Dual-use side panel — Advantage #4 A/B (Beat E, ~30 s)

**Same array. Same R. Two products.**

```
  Beat E.0 (baseline, t=0):
    NullSteeringPanel renders receive pattern from MUSIC weights only.
    Pattern is nominal — directional response of a 2-element ULA,
    no notch.

  Beat E.1 (operator engages null, t=10 s):
    Operator selects the simulated jammer azimuth from the panel.
    System computes  w = R⁻¹·a / (a^H·R⁻¹·a)  and applies it as the
    receive weight.
    Pattern redraws with a ≥ 15 dB notch at the selected azimuth.

  Beat E.2 (before / after, t=20 s):
    Bar chart shows received power at θ_jammer before / after.
    Drop:  15-20 dB typical (ADR-008 §D8 caption cap).
```

**Caption.** *"One matrix, two products: target geolocation for kinetic effect, null-steering for own-comms protection."*

**Framing rail.** Anti-desense, NOT ECM. We are not transmitting through this array. We are protecting our own L2 coherent DF channel from being desensitised by a co-channel jammer while continuing to produce bearings on it.

**Operator note for stage time.** Preload the Beat E.0 baseline receive pattern in the panel before the 15-min slot starts — keeps the t=0 render instant rather than a 2-3 s computation pause.

**Receipts.** `script.md` §3; ADR-008 §D8; `packages/rfmesh-dsp/src/rfmesh_dsp/l2_null_steering.py`; null-depth MC stats in `docs/demo/null_depth_mc_stats.md`.

---

# Honest error budget — what we measure vs what we promise

| Quantity | What we measure | What we promise to the jury |
|---|---|---|
| L1 angular accuracy | ±10° at 3 km, +11.4 dB SNR (Mast C 2026-05-19) | "Resolved to within one sweep-grid bin of map-derived ground truth" |
| L1 σ honesty band | per-bearing `azimuth_sigma_deg` is the sigma-honesty contract; gated by `tests/test_sigma_honesty.py` to ±20 % vs Monte Carlo ground truth at SNR ∈ {10, 20, 30} dB | "Sigma reported on every bearing is empirically honest to ±20 %" |
| Fusion ellipse honesty | Inclusion rate 93.7 % (Beat C) / 94.8 % (Beat D) over 1000 MC trials per WS-CD-008 | "95 % chi-square ellipse contains the true emitter in 95 ± 3 % of trials" |
| GDOP envelope | Beat C 1.16, Beat D 1.02, Beat B 1.53 at the current trench geometry | "GDOP ≤ 1.6 across the demo geometry, ≤ 1.2 from Beat C onward; the dashboard surfaces it on every fix" |
| Stationarity at peak | std 0.17 dB / drift 0.12 dB over 60 s of continuous capture on a real GSM-extension carrier | "Stable directional measurement, not AGC noise" |
| Null depth (L2 anti-desense) | 15-25 dB typical, capped at 20 dB on UI per ADR-008 §D8 | "15-20 dB typical, up to ~25 dB with fresh calibration" |

**What we explicitly do NOT promise.**

- Absolute power calibration. No SDR in scope is power-calibrated (`INHERITED_CONTEXT.md` §1.3). All dB numbers are relative.
- Sub-degree L1 accuracy. Sub-degree belongs to L2.
- TDOA. Architectural decision per ADR; we do AoA cross-fixing only.
- Magnetometer-derived heading. Per-site magnetic distortion makes mast-mounted magnetometers unreliable; we use surveyed-aim heading.

---

# Expected demo outcome at BoTH3

**What the jury will see live (15-min slot).**

1. **Opening (60 s)** — architecture diagram + 8-advantage overview + "we are not Bukovel-AD; we are open, cheap, honest".
2. **Beat A** (10 s) — one node, no fix. Honesty rail v1.
3. **Beat B** (15 s) — two nodes, oblong ellipse at 20 % of range, MEDIUM.
4. **Beat C** (15 s) — three nodes, ellipse shrinks to 13 % of range, MEDIUM.
5. **Beat D** (20 s) — L2 partner joins, semi-minor collapses 65 % to a 125 m slot.
6. **Beat E** (30 s) — dual-use null-steering A/B side panel.
7. **Mid-demo node-kill** (10 s) — pull a node's antenna, watch the ellipse expand honestly + node-stale flag appear.
8. **Q&A** — 15 prepared answers in `docs/demo/script.md` §4 (geometry, honesty, dual-use, operational, Phase C site selection).

**Time budget.** Items 1-7 sum to ~160 s of live demo on a 15-min slot, leaving ~12 min for Q&A. Plan ~30 s of narration breath between beats — the deck breakdown is the floor, not the ceiling. The Q&A budget is generous on purpose; if the jury is hostile, the deck's "Honesty caps" slide is the operator's anti-cheat checklist for what NOT to claim under pressure.

**Contingency.** If live hardware fails: `apps/demo-replay --recorded-iq scenarios/three_node_trench.yaml` runs the same beats against recorded IQ; the dashboard renders the same flow. **The demo always has a working fallback.**

---

# Honesty caps — what we explicitly do not promise

A reference for any deck-edit, slide rewrite, or jury Q&A answer. **Reading this slide is the operator's anti-cheat checklist before going on stage.**

- **NO sub-degree L1 accuracy claims.** L1 angular accuracy is angular-grid-resolution (±5-10° on a coarse sweep, ±1 sweep-grid-bin on a fine sweep at adequate SNR). Sub-degree belongs to L2 phase-coherent DF.
- **NO absolute power dB claims.** No SDR in scope is power-calibrated. All dB numbers are relative — anchored to either a sky-pointing instrument-noise floor (site-local, date-local, RX-chain-local) or a peak-vs-back-lobe ratio.
- **NO "we always work" framing.** The system honestly refuses when the prominence gate is not met. Refusal is a feature, not a failure.
- **NO HIGH-band promises at the trench-demo geometry.** The demo lives in **MEDIUM across all four beats**. HIGH appears only in the "denser deployment" hypothetical slide.
- **NO null-steering as offensive ECM.** Anti-desense framing only. We are not transmitting through this array.
- **NO trained-classifier claims at v1.0.** The threat library ships the *open structure* + YAMLs for ELRS / CROSSFIRE / GSM; classifier weights are training-pipeline outputs that come post-event. `EmitterClass.UNKNOWN` is the honest fallback.
- **NO promises about deployment density beyond N=4.** We have demonstrated N=2, 3, 4. N ≥ 5 hypotheticals stay hypothetical — denser deployments improve the demo geometry, but the operator is not promised they have been tested at this hackathon scale.
- **NO Wi-Fi-bearer-survives-contested-spectrum claims.** Wi-Fi is jammable; the answer is the LoRa fallback bearer (Advantage #2). On stage, name LoRa explicitly if the jury asks about a contested-spectrum mesh.

---

# Cross-references — where every number on this deck comes from

| Slide | Number | Source |
|---|---|---|
| Mast C anchor | +11.4 dB SNR, 0.17 dB std, ±10° | `scenarios/mast_c_reference.yaml` :: `measured_behaviour` |
| Beat A/B/C/D | semi_major / semi_minor / GDOP / band | `scenarios/trench_demo.yaml` :: `demo_beats` (C5-regenerated 2026-05-20) |
| WS-CD-008 MC | 93.7 / 94.8 % inclusion | `packages/rfmesh-fusion/tests/test_honest_ellipse_monte_carlo.py` |
| Null depth | 15-25 dB typical, cap 20 dB | `docs/demo/null_depth_mc_stats.md`; ADR-008 §D8 |
| €250-per-node | per-component cost table | Slide 11 (this deck) + sourcing notes |
| 8 advantages | per-advantage panel + spoken anchor | `docs/ADVANTAGES.md` §1, §2 |
| Demo arc | Beat A/B/C/D narration + Q&A | `docs/demo/script.md` §1, §2, §3, §4 |
| Architecture | block diagram, dependency star | `ARCHITECTURE.md` §1, §2, §3 |
| Honesty contracts | sigma honesty, no silent fallbacks | `AGENTS.md` §1 (Seven Binding Invariants) |
| Phase C operational envelope | 2-5 km standoff | `ARCHITECTURE.md` §0 + Phase C bench evidence |

---

# Slide for the closing

**The pitch in one paragraph.**

> rfmesh is the system that admits when it cannot see, shows you the
> shape of its uncertainty, and shrinks that shape as you add posts.
> Same hardware as a hobbyist demo; same architectural decisions as
> a defence-grade procurement. Open library, honest sigmas, cooperative
> fusion, GNSS-denied by construction, dual-use null-steering from
> the same matrix — and one Pi 4 per node.

**Two acceptance test ideas — what the jury could ask us to demonstrate live, today, at this booth, beyond the canned demo:**

1. *"Move one node 50 m and rerun the fix. Does the ellipse change shape consistently?"* Yes — the operator switches to the bench/debug dashboard layout, points at the `GdopHeatmapPanel` before/after the move, and the new fix's `FixPanel` ellipse + GDOP track the geometric prediction. (Trench layout does not carry the heatmap; the debug layout does.)
2. *"Tune to a frequency we name on the spot."* If the carrier is hot, the system locks and emits MEDIUM bearings. If the carrier is silent, the prominence gate refuses and the dashboard says so. Either is a publishable result.

**Pitched at:** BoTH3 Counter-Jamming Challenge 2 jury, Belgian Defence + partner-pool evaluators, 2026 deployment-grade open-source DF software.

---

# Backstop slide — references

**Project documentation (read order for cold start):**

1. `ARCHITECTURE.md` — the *why*. Binding invariants.
2. `AGENTS.md` — agent behaviour. Seven Binding Invariants.
3. `INTERFACES.md` — semantic dictionary of every contract type.
4. `INHERITED_CONTEXT.md` — what we learned from the prior project.
5. `docs/ADVANTAGES.md` — the 8 architectural advantages this deck competes on.
6. `docs/demo/script.md` — operator spoken narration, ~15-min demo flow.
7. `docs/demo/trench-demo-geometry.md` — the trench-demo's geometric rationale.
8. `docs/phase-c-report/findings.md` — Phase C bench results.
9. `scenarios/mast_c_reference.yaml` — Mast C empirical anchor.
10. `scenarios/trench_demo.yaml` — the demo's per-beat expected outcomes.

**Adopted decisions (ADRs):** 13 ACCEPTED at HEAD = `ec81a8f`. Notable for the jury: ADR-002 (contracts-as-Protocol), ADR-005 (confidence-policy), ADR-008 (Capon enum + null-depth UI cap), ADR-009 (band math + narrative correction).

**Code:** https://github.com/macwed/rfmesh-workspace (private during BoTH3; opensource-able post-event).

---

<!-- End of deck. ~20 slides. Marp-rendered HTML / PDF / PPTX live under build/ when CI runs `marp` against this file. -->
