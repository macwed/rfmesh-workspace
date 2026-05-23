# ADR-016 — RF-unplausibility lens: terrain-cover (concealment) + leakage siting

**Status:** PROPOSED (2026-05-23)
**Date:** 2026-05-23
**Author:** lead-Opus, synthesising a 3-specialist design review (EW/RF-physics, RF-tactician, geospatial-siting). Codex math-review deferred (specialists converged with no live disagreement; available on request to independently challenge the reduction algebra).
**SCHEMA_VERSION change:** **NONE.** Stays `1.1.0`. Deployment-layer + pure-`_rf_grid`-reuse only; the frozen contract is untouched (B1 satisfied without a bump, same posture as ADR-015).

## Context

The both3 ops-map currently runs one lens — **RF-plausibility**: given a `FixEvent`, its
contributing bearings, and a terrain raster, it heats *where the red emitter plausibly is*:

```
P_emitter(cell) ∝ AoA_likelihood(cell) × RF_path_weight(cell→our_sensors)
```

`RF_path_weight` is single-knife-edge ITU-R P.526 diffraction over LiDAR
(`posterior.py:_rf_grid`, `_j_v`). Frequency enters only through the Fresnel
parameter `v ∝ h·√(1/λ)` (`posterior.py:240`), so the band dependence is
**physical, not hand-tuned** — sub-GHz shadows are mild, 5.8 GHz shadows sharp.

**The ask: flip the lens.** Treat the mesh nodes as **RED TEAM** (hostile DF /
ESM / jammers). For every candidate cell where we might place a friendly asset
(field hospital, command post, relay, Starlink terminal), compute our **exposure**
to red across an operator-chosen **set of bands**, and surface the **negative** —
the "dark spots", RF defilade — as a siting cue. Plus a **leakage** map (where our
own emissions reach red) and its negative (minimal-leakage emitter siting).

This is **terrain masking / RF defilade / counter-SIGINT siting / EMCON geometry** —
a real, named planning task today done slowly, expert-only, single-band, in QGIS /
SignalServer / CloudRF-class tools. The contribution is making it fast, multi-band,
and tied to the same fix picture the operator already has.

### Two findings that shape the decision

1. **The kernel is already correct and reusable.** `_rf_grid` computes per-cell,
   per-node, frequency-dependent diffraction transmission and **never zeroes** a
   shadow (`w = max(floor, 1 − loss/scale_db)`, `posterior.py:246`; `floor=0.15`,
   `config.py:72`). The flip reuses it verbatim and changes only (a) what is
   combined over nodes, (b) the loop over bands, (c) the sign, (d) the surface
   sampled per band.

2. **The entire risk surface is wording and surface-model choice, not math.** We
   have **no calibrated power** — no dBm, no ERP, no RX sensitivity, no noise
   floor (binding, `INHERITED_CONTEXT.md §1.3`, `INTERFACES.md §0`). Therefore the
   lens may **never** claim a cell is "safe / hidden / undetectable". The strongest
   honest claim is a **relative terrain-cover ranking**. Worded as "concealment" on
   the 30 m DEM at 5.8 GHz, it is a credibility trap that dies in the jury's first
   60 seconds.

## Decision

**Add a second lens that reuses the diffraction kernel as a relative
terrain-cover estimator, in two mutually-exclusive modes, combined honestly over
red nodes and over a chosen band set, sampled from a band-appropriate surface,
and labelled as relative cover — never detectability.**

### 1. The reduction (N red nodes × M bands → 1 raster)

Per cell, per band `b`, per red node `n`, the per-path transmission is exactly
`_rf_grid`'s soft weight (identical code):

```
T_{n,b} = max(floor, 1 − J(v_{n,b}) / scale_db)        ∈ [floor, 1]
```

**Reduce over nodes — probability-of-detection union** (not max, not sum). Treat
`T_{n,b}` as red node n's chance of hearing you; "exposed if ANY red node hears
you" is the complement of "every node misses you":

```
E_b(cell) = 1 − Π_n (1 − T_{n,b})        # bounded [0,1], monotone in node count
```

`max` throws away corroboration from a 2nd sensor; `sum` is unbounded and breaks
normalization. The union is the dual of the `floor`-clamped product the codebase
already trusts — and `floor` does honesty work here: with `floor=0.15`, a single
node can never push `(1−T)` below 0.85, so the union **refuses to promise total
concealment**. Keep `floor`; removing it to get darker maps is a B3 violation.

**Reduce over bands — `max` (worst band wins)**, i.e. AND semantics: a cell is
concealed only if concealed in *every* selected band.

```
E(cell)  = max_{b ∈ selected} E_b(cell)
C(cell)  = 1 − E(cell)                    # concealment ∈ [0,1], high = dark/safe
C_norm   = C / C.max()                    # normalize ONCE, at the end
```

**Cross-band normalization is load-bearing (the ADR-015 lesson, reprised).**
`J(v)/scale_db` uses the *same* dimensionless scale for every band, but `J(v)`
is intrinsically larger at high bands (v ∝ 1/√λ), so high-band `T` saturates
toward `floor` faster. Combine bands in the **raw [0,1] transmission domain** and
normalize **once** on `C` (the `posterior.py:552` pattern). Per-band
re-normalization would erase the very physics — "high bands hide more easily" —
that the lens exists to show. This is the geospatial analog of ADR-015's
no-`argmax`, no-lossy-collapse rule.

`C_norm` then feeds the **existing** `_bands` highest-density-region polygonizer
(`posterior.py:580`) unchanged — the 50/80/95 % contours now carve concealment
mass, zero new polygon code.

### 2. Two modes, mutually exclusive (never overlaid)

The diffraction kernel is **reciprocal in geometry**, so the same per-path `T` is
read in two directions. They are **the same relative-cover map read two ways** —
not two independent detectability claims.

- **Concealment** (`mode=concealment`, source = red radiating *inward*):
  "how well can red hear me here." `_rf_grid` with `nodes = red_sensors`,
  `emitter_h` = the quiet asset (e.g. 2 m trench CP). Reduce by detection-union.
- **Leakage** (`mode=leakage`, source = candidate friendly cell radiating
  *outward*): "how much of *my* emission reaches red." Same kernel, **path
  direction flipped**, `emitter_h` = your antenna mast (e.g. 10 m — you raised it
  to communicate). `L(cell)=max_b[1−Π_n(1−T_{cell→n,b})]`; **negative-of-leakage**
  `minLeak = 1 − L`, polygonized by the same `_bands`.

They diverge by **antenna height** and **reduction**, and the divergence is the
operator insight: a cell can be well-hidden from red's sensors yet leak badly
under a tall mast. Show one mode at a time — an EW officer must never have to ask
"is dark good or bad on this layer?". One mode, one meaning of dark.

### 3. Band → surface → resolution (the "different object size" physics)

First Fresnel-zone radius `r₁ = √(λ d₁ d₂ / d)`. An obstacle attenuates only when
its height above the sight-line is a fraction (~0.5) of `r₁`. So bigger λ ⇒ fatter
zone ⇒ only large features block ⇒ a coarser/smoothed surface is the *honest*
sampler; smaller λ ⇒ thin zone ⇒ walls/vehicles/canopy block ⇒ need the fine DSM.
This is the user's "different size objects hide from different frequencies", made
quantitative. Band edges are the **canonical Hz** in
`equipment_catalog.json:band_gate` (single source of truth; "2/3/4/5" are display
sugar — ADR-015).

| Band (λ) | r₁ @ 3 km midpoint | Surface | RES_GRID `res` | Fresnel-smoothing kernel | Note |
|---|---|---|---|---|---|
| 433 / 868 / 915 MHz (0.7–0.33 m) | ~16–24 m | **DTM** (MNT bare earth) | 4 (cell 60 m, 64 samp) | ~8–12 m | fat zone; buildings/trees are sub-zone. **Least reliable — ground-wave** ignores terrain at ≤450 MHz |
| ~1.5 GHz GNSS L1 (0.19 m) | ~12 m | **DSM** | 3 (cell 50 m, 96 samp) | ~5 m | large buildings start to matter |
| 2.4 GHz (0.125 m) | ~9.7 m | **DSM** (MNS) | 2 (cell 40 m, 160 samp) | ~3–5 m | rooftop/canopy edges block |
| 5.8 GHz (0.052 m) | ~6.2 m | **DSM** | 1 (cell 30 m, 256 samp) | ~1–3 m | walls/vehicles block; needs the 1 m surface |

The diffraction math needs **no change** (v already scales with λ). The new wiring
is the band → `(surface, res)` lookup feeding the existing `EnhanceManager`
windowed read (DSM via `Resampling.max`, which preserves ridge/rooftop peaks).

**Two hard gates (B3 — no silent fallback):**
- **Refuse or loudly caveat 2.4 / 5.8 GHz on the 30 m Copernicus DEM.** r₁ ≈ 6–10 m
  ≪ 30 m cell — claiming sub-cell concealment the data cannot support. High bands
  require the staged LiDAR DSM; else stamp the degraded source label (the
  `enhance.py:9-12` "never label 30 m as 1 m" rule, extended to band fidelity).
- **Grid cell vs Fresnel-zone mismatch:** even `res=1` is `cell_m=30`. The
  *grid* resolution sets candidate-site spacing; the *along-path* `n_path_samples`
  (256 at res=1) sets whether the diffracting edge is found. Keep them conceptually
  separate in the UI; do not advertise 1 m fidelity the grid does not sample.

### 4. Red position uncertainty (optional, honest-by-default)

Red node positions may be *our own* estimates carrying a `confidence_ellipse_95`.
The correct exposure marginalizes terrain transmission over red's position PDF
rather than evaluating at the centroid:

```
E(cell) = Σ_r  transmission(cell → r) · p_red(r)        # quadrature over red's ellipse
```

This reuses the existing AoA/ellipse Gaussian-weight pattern (`posterior.py:296-306`)
as a **red-location weight**. It is *more* honest — a hide in defilade from red's
best-estimate point can be in clear LOS one ellipse-radius away; centroid-only
**overstates** concealment. Cost: ×(quadrature points). Ship a coarse 5–9-point
quadrature; if centroid-only for cost, **label** "red position assumed exact"
(B3). Combine across red nodes assuming **independent** sensors (state it; a fused
red network detects more).

### 5. Rendering (ADR-015 color discipline is binding)

Confidence already owns the fill-hue channel (`app.js`, `COLORS[confidence_level]`);
ADR-015 forbids a new physics axis from hijacking it.

- **Concealment magnitude → lightness/opacity of one neutral hue** (dark slate
  ramp), *not* a hue ramp. "Dark = where you hide" reads as shadow and stays
  orthogonal to the green/amber/red confidence semantics. **Never** invert the
  basemap or use green = "safe".
- **Band → stroke color** from the ADR-015 colour-blind-safe band ramp; reuse the
  `[B2][B3][B4][B5]` chips as the band filter/slider.
- **One legend row** in the existing Means/Read/Do/But accordion:
  *Means: terrain cover from RED across the selected bands. Read: darker = lower
  combined exposure (relative). Do: site CP/hospital in the dark core. But:
  diffraction fills shadows — never zero; check the worst-band number on hover.*
- **Optional LOS viewshed cross-check** (hatched outline, captioned "hard
  line-of-sight only — ignores diffraction; reality is softer"). Pre-empts "is
  this just a viewshed?"; the diffraction dark core should sit inside the
  LOS-hidden set. `clr` (terrain − sightline) is already computed in `_rf_grid`.

### 6. The slider / preset model (RF-tactician)

Threat-named presets, not frequency-named — the planner thinks "hide from the GNSS
jammer", not "1.5–1.6 GHz". Three controls per band, no fourth:

1. **Band on/off** — include this threat in the worst-case-OR combine.
2. **Assumed red ERP class** {low/med/high} — **defaulted from catalog `erp_class`**
   of the chosen system. Discrete, never a dBm slider (we have no calibrated budget).
3. **Red posture** {jammer-reach | DF-detect} — wired to catalog `role` so a
   *passive* collector (Svet-KU, Kalinka: `role=df/recon`) can never be drawn with
   a jamming-reach ring. This is also the leakage/detectability switch.

Presets to ship: **Hide from GNSS jammer** (GNSS L1, ERP high — Pole-21/Zhitel),
**Hide from FPV/DroneID** (sub-GHz + 2.4 + 5.8, ERP med — Volnorez/trench),
**Hide from comms intercept** (cellular + L-band, DF-detect passive),
**Hide from Starlink locator** (Ku, DF-detect — Kalinka).

### 7. Honesty constraints (the load-bearing decision)

These are not polish — they are what keeps the lens jury-credible (B2/B3/B4, the
ew-specialist lens):

- **Layer title "Relative terrain cover (diffraction)"** — never
  "concealment / hidden / safe" in operator-facing copy (internal name is fine).
- **No dBm, no detection-range-in-km, no "% concealed", no green SAFE cell.** The
  only number shown is **dB of diffraction loss** (honest — referenced to
  free-space, not dBm) and ordinal HIGH/MODERATE/LOW, reusing the existing
  loss-label tiering (`posterior.py:623-628`).
- **Mandatory on-layer disclaimer:** "Ranks terrain path obstruction only. Models
  single-edge diffraction over a height surface — **not** transmit power, receiver
  sensitivity, antenna gain, polarization, multipath/reflection, tropospheric
  ducting, low-band ground-wave, sidelobes, or multi-edge cascades. Makes **no
  claim** any emitter is undetectable. Relative siting guidance, not a guarantee."
- **Keep `floor`; never zero a cell.** The map never shows total concealment.
- **Brittle-mask flag:** a site masked only by a single edge a few metres above the
  sight-line (`clearance_m` small in `_dominant_obstruction`) loses cover if red
  moves 200 m or gains a mast. Flag low-clearance masks as **brittle** — the most
  operationally honest thing the tool can say, and the number already exists.
- **Comms-hole tension:** a perfect RF-defilade hole for a CP is also a hole for
  *your own* uplink. Surface the tension; do not imply RF cover = safe (kinetic /
  counter-battery geometry is explicitly **not modeled**).

## Consequences (deployment-layer + pure-`_rf_grid` reuse only)

1. `deployment/backend/src/both3_poc/posterior.py` — add a `concealment_geojson`
   (and `concealment_probe`) alongside `posterior_geojson`/`probe`. Reuse
   `_rf_grid`, `_j_v`, `_dominant_obstruction`, `_bands`. New: detection-union
   node reduce, worst-band reduce, sign flip, `direction` flag (concealment vs
   leakage swaps `emitter_h`/`node_h` endpoints), optional red-ellipse quadrature.
   Drop the AoA term (siting has no bearing geometry).
2. `deployment/backend/src/both3_poc/enhance.py` — band → `(surface, res)` lookup;
   per-band surface selection feeding the existing windowed read; degraded-source
   stamping extended to band fidelity (refuse high bands on 30 m DEM).
3. `deployment/backend/src/both3_poc/app.py` — new endpoints mirroring the
   `/posterior` + `/enhance` split (synchronous coarse default; async per-band
   LiDAR job reusing `/enhance/jobs/{job_id}`):
   - `POST /concealment` — sync, Copernicus, selected bands at coarse res
   - `POST /concealment/enhance` — 202 + job; per-band LiDAR per §3
   - `GET  /concealment/probe` — hover: per-red-node exposure + dominant obstruction
   - `GET  /concealment/options` — bands from catalog `band_gate`, per-band
     surface/res defaults, which LiDAR surfaces are staged
4. `deployment/backend/data/equipment_catalog.json` — no change; it is the band /
   `erp_class` / `role` source of truth the presets read.
5. `deployment/frontend/app.js` — mode toggle (Hide-quiet-asset | Site-emitter),
   neutral dark-opacity ramp + band strokes + chips/sliders, threat presets, one
   Means/Read/Do/But legend row, optional LOS cross-check, brittle-mask flag.
6. Tests (deployment pattern):
   - **Union vs max:** two red nodes both partially exposing a cell → union exposure
     strictly exceeds either alone (no concealment over-claim).
   - **Worst-band AND:** a cell shadowed at 900 MHz but open at 5.8 GHz is **not**
     concealed (band reduce = max).
   - **Cross-band normalization:** high-band shadows are sharper than low-band for
     the same terrain (assert `J(v)` ordering preserved; no per-band renorm).
   - **High-band gate:** 5.8 GHz request on the 30 m DEM returns the degraded label,
     never a 1 m-fidelity claim (B3).
   - **Reciprocity / mode split:** concealment and leakage share per-path `T` but
     differ when `emitter_h ≠ node_h`.
   - **Floor honesty:** no cell ever reaches `C=1.0` exactly (floor present).

## Proposed staged implementation plan

- **S0 — kernel extraction (pure, no API).** Refactor the `_rf_grid` call sites so
  the per-`(node,band)` `T` grid is reusable by both the existing posterior product
  and the new union reduce. Add `concealment_geojson` with node-union + worst-band
  + sign flip, Copernicus only, single mode (concealment). Unit tests for the
  reduction algebra. *Committable: backend math + tests, no frontend.*
- **S1 — band→surface + endpoints.** Band→`(surface,res)` lookup in `enhance.py`;
  high-band DSM gate + degraded labelling; `POST /concealment`, `/concealment/probe`,
  `/concealment/options`. *Committable: API live, curl-testable.*
- **S2 — leakage mode + negative map.** `direction` flag, `emitter_h`/`node_h`
  swap, `minLeak = 1 − L`. *Committable: both modes backend-complete.*
- **S3 — frontend.** Mode toggle, neutral dark ramp, band chips/sliders, threat
  presets, legend row, hover probe wiring, brittle-mask flag, optional LOS overlay.
  *Committable: demo-ready lens.*
- **S4 — honesty hardening + red-ellipse quadrature (optional).** On-layer
  disclaimer, ordinal-only readouts, brittle-mask copy; red position quadrature if
  time. *Committable: jury-credible.*

Each stage is a stage-named committable deliverable; scratch (specialist briefs)
lives under `deployment/_work/concealment-lens/`.

## Tradeoffs considered

- **Union vs max over nodes.** Max is simpler and is the union's degenerate
  single-node case, but discards a 2nd red sensor's corroboration — wrong for a
  mesh adversary. Union chosen.
- **Worst-band vs mean over bands.** Mean averages away a high-band leak that gets
  the asset killed. Worst-band (AND) is the doctrinally safe default; mean offered
  as an advanced toggle only.
- **Per-band renorm vs single final norm.** Per-band renorm erases the band physics
  (ADR-015 lesson). Single final norm chosen.
- **Centroid vs ellipse quadrature for red position.** Centroid is cheap but
  over-claims concealment. Quadrature default; centroid allowed only if labelled.
- **"Concealment" naming vs "relative terrain cover".** The honest name costs a
  little marketing punch and buys jury survival. Honest name chosen.

## Open items for sign-off

- Confirm the threat→band→ERP/posture preset table against the live
  `equipment_catalog.json` entries (Pole-21, Zhitel, Volnorez, trench jammer,
  Svet-KU, Kalinka, R-330Zh, Borisoglebsk-2).
- Confirm asset/mast default heights for the two modes (quiet asset ~2 m; emitter
  mast — operator-set, default 10 m?).
- Confirm whether red nodes come from the live mesh fixes (other detected emitters
  as red) or are operator-placed markers, or both.
- Decide whether S4 red-ellipse quadrature is in v1 or deferred.

## References

- 3-specialist design review, 2026-05-23 (EW/RF-physics, RF-tactician,
  geospatial-siting; lead-synthesised). Raw briefs:
  `deployment/_work/concealment-lens/`.
- `deployment/backend/src/both3_poc/posterior.py` — `_rf_grid` (kernel reused),
  `_j_v`, `_dominant_obstruction`, `_bands`, `floor`/`scale_db`.
- `deployment/backend/src/both3_poc/enhance.py` — `RES_GRID`, DTM/DSM
  `LidarSource`, degraded-source honesty rule (`enhance.py:9-12`).
- `deployment/backend/data/equipment_catalog.json` — `band_gate` (band-edge SoT),
  `erp_class`, `role` (passive-DF vs jammer guardrail).
- ADR-015 — frequency-as-dimension; no-`argmax` / no-lossy-collapse; color-channel
  discipline; deployment-only no-bump precedent.
- `AGENTS.md` §1 (B1/B2/B3/B4); `INHERITED_CONTEXT.md §1.3`; `INTERFACES.md §0`
  (no dBm — the load-bearing honesty constraint).
- `ARCHITECTURE.md` §7 (what the demo shows).

## Sign-off

When Maciej reviews and accepts: set `Status: ACCEPTED`, add `**Accepted by:**`,
then lead-Opus executes S0→S3 (S4 optional) under "Consequences", council-reviewed
per commit. If rejected, set `Status: REJECTED` with the reason.
