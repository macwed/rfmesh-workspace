# ADR-017 — Jam-shadow lens + unified exposure engine + combined "ideal-site" view

**Status:** PROPOSED (2026-05-23)
**Date:** 2026-05-23
**Author:** lead-Opus, synthesising a second 3-specialist council pass (EW/RF-physics,
RF-tactician, geospatial) on top of ADR-016.
**SCHEMA_VERSION change:** **NONE** (stays `1.1.0`). Deployment-layer + pure
`_rf_grid` reuse only; the frozen contract is untouched (B1 satisfied).
**Supersedes/extends:** ADR-016 (concealment/leakage). This ADR generalises that
design into one shared engine and adds the jam-shadow lens and the combined view.

## Context

ADR-016 introduced the inverse-of-plausibility lens family (concealment, leakage).
A third ask: given a known **jammer** strong enough to disrupt electronics, find per
frequency the **dark spots where its signal is weakest** = where our links survive —
a heatmap laid on the current map. The council confirmed (twice) this is the **same
reciprocal knife-edge kernel**, ~90 % shared with concealment, differing in: red-node
role, which heights sit on which path end, band meaning (our links, not threat bands),
output sign, and one new honesty mechanism — **ERP burnthrough**. The user chose to
build all three lenses + a combined "ideal-site" view on one engine.

## Decision

**One exposure engine; each lens is a configuration.** Per cell, per band `b`, per red
node `n` (filtered by catalog `role`): the shared `_path_weight_chunk` transmission
`T = max(floor, 1 − J(v)/scale_db_eff)`; union over nodes `E_b = 1 − Π_n(1 − T_n)`;
reduce over bands; sign to survival `S = 1 − E`; normalize once on `S`; polygonize with
the existing `_bands` HDR. (`posterior.py`.)

| Lens | Red role | Heights (cell end / node end) | Bands | Band reduce | ERP gate |
|---|---|---|---|---|---|
| concealment | df/recon | asset / sensor mast | threat (hide-from) | `max` | no |
| leakage | df/recon | our mast / sensor | our emit band | per-band | no |
| jam-shadow | jammer | victim rx / jammer mast | our links | `max` | **yes** |
| combined | both | — | both | — | from jam side |

**ERP burnthrough (jam-shadow):** `scale_db_eff = scale_db · k[erp_class]`,
`k = {very_high:1.5, high:1.25, medium:1.0, low:0.85}`, capped at 1.5 — higher ERP
needs more terrain dB to earn the same shadow. `k[medium]=1.0` makes an all-medium
jam-shadow numerically a sign-flipped concealment map (the regression anchor,
`tests/test_exposure.py::test_medium_erp_jamshadow_equals_sign_flipped_concealment`).
high/very_high also trip a `burnthrough` flag → `reliable=false` + a "shadow won't hold"
note. The `role` gate ensures passive collectors (df/recon) and spoofers never get a
jam reach-shadow.

**Combined "ideal-site"** (`combined_geojson`): one survival surface per sub-lens over a
shared AOI, intersected by element-wise `min` (good only where good in **both**),
renormalized, polygonized. One single-meaning layer — never two opposite-semantics
ramps at once.

**Band → surface → resolution** (`enhance.surface_res_for_band` / `finest_band_surface`):
sub-GHz→DTM/coarse, 2.4-5.8 GHz→DSM/fine; high bands on the 30 m DEM are stamped
`fidelity_warning` (B3) — never a 1 m claim. LiDAR is read synchronously per-AOI via the
existing `LidarSource.window` in an executor (no new job machinery).

## Honesty locks (binding, B2/B3/B4 + no-dBm)

1. No dBm/km/%/"safe/hidden/protected/jam-proof". Only **relative diffraction loss vs
   free-space (dB)** + ordinal HIGH/MOD/LOW. Layer title "relative terrain cover".
2. `floor` kept; no cell is ever fully dark (survival never total).
3. `role` gate: only `role=jammer` produces a jam reach-shadow.
4. ERP burnthrough caps optimism and flags high/very_high as unreliable.
5. High bands require staged LiDAR DSM; else degraded label.
6. Each lens normalizes on its own peak; bands combined in the raw transmission domain,
   normalized once (the ADR-016 no-`argmax` lesson).

## Consequences (deployment-layer only — what shipped)

- `posterior.py`: `_path_weight_chunk` (shared kernel, extracted from `_rf_grid` —
  RF-plausibility numerics preserved); `_Red`, `ERP_K`, `LENS_CONFIG`, role sets;
  `_exposure_band_grid` (node-union), `_exposure_surface` (one survival grid),
  `exposure_geojson`, `combined_geojson`, `exposure_probe`.
- `enhance.py`: `BAND_SURFACE`, `surface_res_for_band`, `finest_band_surface`.
- `app.py`: `POST /exposure`, `POST /exposure/probe`, `POST /exposure/combined`,
  `GET /exposure/options`, `GET/POST/DELETE /exposure/reds` (operator markers);
  `_auto_jammer_reds` (mesh fixes whose top candidate is `role=jammer`), `_gather_reds`.
- `store.py`: operator-placed red markers (`add_red`/`list_reds`/`delete_red`/`clear_reds`).
- `frontend/exposure.js` + `index.html` + `style.css`: the lens UI (S3).
- `pyproject.toml`: declare `numpy`; dev group with `pytest`; `tests/` (16 tests).

## References

- ADR-016 (concealment/leakage — math, color discipline, band→surface, no-`argmax`).
- 2× 3-specialist council, 2026-05-22/23 (briefs in `deployment/_work/concealment-lens/`).
- `AGENTS.md` §1 (B1–B4); `INHERITED_CONTEXT.md §1.3`; `INTERFACES.md §0` (no dBm).
- `equipment_catalog.json` `band_gate`/`erp_class`/`role` (single source of truth).

## Sign-off

On accept: set `Status: ACCEPTED`, add `**Accepted by:**`. If rejected, set
`Status: REJECTED` with the reason.
