# ADR-020 — Frequency as a dimension: per-band reports + band-aware fusion association

**Status:** PROPOSED (2026-05-22)
**Date:** 2026-05-22
**Author:** lead-Opus, synthesising a 4-agent design debate (RF physicist / aggregation engineer / data-model architect / ops-UX advocate).
**SCHEMA_VERSION change:** **NONE.** Stays `1.1.0`. This is the headline result — the feature lives entirely in the deployment/PoC layer and the pure-fusion grouping logic; the frozen contract is untouched (B1 satisfied without an ADR-to-bump).

## Context

**The reported problem.** On the both3 ops-map a jammer can jam several frequency
bands (informally "2, 3, 4, 5, all"). A naive design picks **one representative
frequency per map point** via `argmax` over per-band intensity, then derives a
per-point uncertainty ("sigma") from it. Two jammers at the **same point with
equal intensity across all bands** make the `argmax` tie — "max frequency jammed"
is undefined, and any sigma derived from it is undefined too.

**Root cause.** The `argmax`-to-one-band step is a **lossy reduction the contract
never asked for**. Collapsing several real per-band detections into one scalar is
exactly the silent-corruption mode B2 (honest sigma) and B3 (no silent fallback)
exist to forbid. The tie is a symptom; the disease is the collapse.

**Two findings from the debate that change the picture:**

1. **`BearingReport` has no `center_freq_hz`.** (Verified against
   `packages/rfmesh-contracts/src/rfmesh_contracts/messages.py` — the atom carries
   `azimuth_deg`, `azimuth_sigma_deg`, `method`, `snr_db`, `emitter_class`, …, but
   no frequency. `center_freq_hz` exists only on `SDRConfig`.) An earlier
   exploration claimed otherwise; it was wrong. This does **not** weaken the
   no-contract-change result — it strengthens it, because the deployment layer has
   already solved frequency without the contract: `deployment/backend/src/both3_poc/store.py`
   carries frequency as a **side annotation** (`_fix_freq`, keyed by `fix_id`) with
   an explicit comment that `FixEvent` has no frequency field. `demo_fixes.json`
   already emits one fix per `(position, center_freq_hz)`.

2. **A latent fusion bug.** `packages/rfmesh-fusion/src/rfmesh_fusion/fuser.py`
   batches bearings on **time only** (median ±`batch_window_ms/2`) and never reads
   frequency. The moment any node emits per-band reports, the fuser will cross-fix
   node-A's f1 bearing with node-B's f2 bearing into **one wrong ellipse**. This
   risk is present in the current code; it is only masked because the demo seeds
   one frequency per fix. Adding the frequency dimension does not create this bug —
   it surfaces a keying gap that already exists.

## Decision

**Treat frequency as an added dimension. Do not pick a max — keep bands as distinct
reports/fixes, and associate bearings in fusion by `(time-window, band)` instead of
`(time-window)` alone.** The tie dissolves because nothing is collapsed. Versioning
frame: **v1 = 2-D (lat, lon) in the frozen contract; v2 = the frequency dimension,
added in the deployment layer only.**

### What happens to sigma (the literal question)

Under the per-band model **sigma never sees a tie.** Each band-fix is solved from
that band's bearings, each bearing carrying its own honest `azimuth_sigma_deg`
(derived from that band's SNR — RF physicist: per-band is the textbook-correct
*narrowband* estimator, so the inputs are cleaner, not faked; B2 holds). Each
band-fix gets its **own** covariance via the existing
`covariance.compute_covariance` inverse-variance chain, so the per-band ellipses
genuinely differ in shape — they are not one ellipse copied across bands. Two
co-located jammers across all bands → several honest per-band ellipses at one
place, **not** one undefined scalar. Where two emitters are truly co-channel +
co-located + identical, sigma honestly **widens** (see resolvability boundary) — it
never silently shrinks or invents a phantom band.

### Resolvability boundary (RF physicist, accepted as binding)

- **Cross-frequency (different bands): solvable at one node.** Channelize the
  node's wideband IQ into sub-bands (the primitives exist:
  `rfmesh_dsp.spectrum.compute_spectrogram`/`find_spectral_peak`,
  `rfmesh_dsp.rssi.compute_rssi_in_band`/`compute_snr_db`), then do DF per
  sub-band. No new DSP primitive needed for L1.
- **Co-channel, separable in azimuth: solvable** down to the Rayleigh/CRB limit
  (one `R`, `n_sources ≥ 2`).
- **Co-channel + co-located + identical: UNRESOLVABLE at a single node.** The two
  sources share one steering vector and merge into **one** dominant eigenvalue of
  `R`; there is no second signal eigenvector to find, and forcing `n_sources=2`
  would fabricate a ghost. The only honest answers are (a) the **mesh** resolves
  them by geometry (N≥2 nodes, GDOP-weighted cross-fix), or (b) **one report with
  honestly wider sigma**. The existing `l2_null_steering` eigen-gate
  (`jammer_dominance_db = 10·log10(λ_max/λ₂)`) is the same boundary, reused.

### Resolutions to the five cross-examination disputes (lead calls)

1. **Over-counting one node's per-band reports.** Band is a **partition key**:
   fusion runs once per `(window, band)`, so within a band-group a node contributes
   exactly **one** bearing → Stansfield inverse-variance weighting is unaffected,
   no over-count. Per-band fixes of one physical emitter are **linked by a
   co-location/track key for display, not re-fused geometrically.** (Resolves
   physicist Q1, data-model Q2, aggregation's own caveat.)

2. **`center_freq_hz = None` (e.g. an L1 node that does not band-resolve).** Never
   drop silently (B3). Default: **float the un-banded bearing into every active
   band-group**, tagged `band_origin="unbanded"` — a real bearing line legitimately
   constrains the geometry of every band hypothesis. (Alternative, noted not
   chosen: keep a separate band-agnostic fix.) A node that *can* channelize should
   prefer emitting real per-band reports.

3. **Broadband / "all".** No `"all"` sentinel. A barrage emitter is modelled as
   it physically is — **present in every band it occupies** — by **replicating its
   bearing into each overlapped band-group** for correct per-band geometry
   (matches `equipment_catalog.json`, which already encodes multi-band emitters as
   multiple `bands_hz` ranges). To avoid fragmenting one threat into N markers, all
   its band-fixes share a **co-location/track key** so the UI renders one marker
   with a multi-band badge.

4. **When may the screen claim "2 emitters"?** A single node **may not** claim two
   identical co-channel co-located sources. Multiplicity is asserted only when (a) a
   node's eigen-test clears a second signal eigenvalue (MDL/AIC) above the noise
   floor, or (b) the mesh cross-fix shows bimodal/high `FixEvent.residuals_deg`.
   Otherwise the fix is flagged **`UNRESOLVED` / "≥2 OR 1 — geometry insufficient"**.
   Plumb `jammer_dominance_db` (and a residual-consistency-derived multiplicity
   flag) through to the fix properties so the UI can show it (B4).

5. **Per-band sigma + the `×N` co-location badge.** Per-band sigmas are genuinely
   different (each from its band's SNR). The `×N` badge counts **distinct physical
   emitters (distinct track keys)** at a cell, **not** band-fixes of one emitter —
   so the same track key that prevents broadband fragmentation (dispute 3) is the
   dedup key for the badge.

### Rendering (ops-UX, accepted)

Frequency is a categorical/physics axis and **must not steal the color channel
that already means confidence** in `deployment/frontend/app.js`. Confidence stays
the marker/ellipse fill; **band gets its own channel**: per-band ellipse stroke
from a colour-blind-safe band ramp; stacked band chips (`[B2][B3][B4][B5]`) that
double as a band-filter toggle (reusing the existing checkbox/click-to-reweight
idioms); a `×N` co-location badge; an `UNRESOLVED` badge + "needs a 2nd sensor at a
different bearing / GDOP too high here" cue for the co-channel case. One new legend
row per new symbol, in the existing Means/Read/Do/But structure.

## Why no contract change

- The dimension lives in the **deployment layer**, which is outside the frozen
  `packages/rfmesh-contracts/` tree (B1 applies only inside it).
- Frequency is already carried deployment-side (`store._fix_freq`); we extend that
  from `fix → freq` to `fix → BandKey`, plus a co-location/track key. Backend-only.
- The single source of truth for band edges is the **existing**
  `deployment/backend/data/equipment_catalog.json` `band_gate` ranges (consumed by
  `inference.py:_freq_in_ranges`). No parallel band enum — a second source of truth
  is the one way this design goes wrong.
- The only contract-touching option (a band enum / per-band field on `FixEvent`)
  would be an additive **MINOR** bump — but ADR-013 already shows the
  `extra="forbid"` asymmetry makes even a MINOR bump abrupt on the consumer side,
  and it is **not needed here.** Costed and declined.

## Consequences (code, post-acceptance — deployment + pure-fusion only)

1. `packages/rfmesh-fusion/` — add a **pure** `association.py::group_by_band(
   bearings, band_edges) -> Mapping[BandKey, list[BearingReport]]` (B5-pure,
   deterministic, table-driven). Callers iterate groups and call the existing
   `Fuser.fuse()` once per `(window, band)`. Closes the latent cross-band-fusion
   bug. No contract import change.
2. `deployment/backend/src/both3_poc/store.py` — `_fix_freq: dict[UUID, float|None]`
   → carry a `BandKey` per fix + a co-location/track key. Band edges + the
   broadband-replication policy come from config/catalog, passed in (kept out of
   pure fusion).
3. `deployment/backend/src/both3_poc/posterior.py` — `posterior_geojson` already
   takes a scalar `center_freq_hz`; it is now called per band-fix. Wavelength/Fresnel
   already vary with frequency, so per-band shadows are already correct.
4. `deployment/frontend/app.js` — band ramp strokes, band chips/filter toggle, `×N`
   and `UNRESOLVED` badges, legend rows (per "Rendering").
5. Tests (deployment pattern; DSP/fusion follow WD-2 golden discipline):
   - **Worked example:** two jammers, same point, equal intensity, all bands →
     assert a set of per-band fixes/ellipses with distinct per-band sigmas, and
     **no `argmax`, no tie** (B3) with honest sigma (B2).
   - **Counter-example:** two identical co-channel co-located sources, one node →
     assert it resolves to one `UNRESOLVED` fix with honestly wider sigma (not two
     crisp markers), and that the mesh path narrows it (B3/B4).
   - **Regression:** `group_by_band` prevents an f1-bearing + f2-bearing cross-fix.

## Tradeoffs considered

- **Per-band reports vs a per-band intensity vector on one fix.** Vector keeps one
  marker but reintroduces "which band represents it" at the UI and complicates the
  honest per-band covariance. Rejected in favour of per-band fixes + a track key.
- **Replicate broadband into each band vs a `BROADBAND` bucket.** A dedicated
  bucket recreates the `argmax`-winner problem one level up. Rejected.
- **Contract field for band vs deployment annotation.** Annotation chosen: no
  `extra="forbid"` blast radius, no workspace-wide mypy tripwire, matches existing
  precedent.

## Open items for sign-off

- Confirm the numeric labels "2/3/4/5" map onto specific `equipment_catalog.json`
  `band_gate` ranges (display sugar over the canonical Hz edges).
- Confirm the co-location/track-key policy (reuse the CoT track-UID notion from
  `INTERFACES.md` §3 `FixEvent.fix_id` vs stable track).

## References

- 4-agent design debate, 2026-05-22 (this conversation, lead-synthesised).
- `packages/rfmesh-fusion/src/rfmesh_fusion/fuser.py` — time-only batching (latent
  cross-band bug); `covariance.py` — the per-group inverse-variance σ chain.
- `deployment/backend/src/both3_poc/store.py` — `_fix_freq` side-annotation
  precedent; `inference.py:_freq_in_ranges` + `data/equipment_catalog.json`
  `band_gate` — the band-edge single source of truth; `posterior.py` —
  `posterior_geojson` (per-band shadows already frequency-dependent).
- `packages/rfmesh-dsp/.../spectrum.py`, `rssi.py` — channelization primitives.
- `packages/rfmesh-dsp/.../l2_null_steering.py` — `jammer_dominance_db` eigen-gate
  (the resolvability boundary, reused).
- `deployment/frontend/app.js` — existing visual grammar extended, not replaced.
- ADR-013 — the `extra="forbid"` MINOR-bump asymmetry that makes the no-contract
  path preferable; `version.py` — SCHEMA_VERSION semantics (still live at 1.1.0).
- `AGENTS.md` §1 (B1–B5); `INTERFACES.md` §0/§3.

## Sign-off

When Maciej reviews and accepts: set `Status: ACCEPTED`, add `**Accepted by:**`,
then lead-Opus executes the deployment + `group_by_band` changes under
"Consequences", council-reviewed per commit. If rejected, set `Status: REJECTED`
with the reason; the latent cross-band-fusion keying gap in `fuser.py` should be
filed on the backlog regardless, since it exists independently of this feature.
