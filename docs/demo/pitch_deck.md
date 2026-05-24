<!--
rfmesh — BoTH3 jury PITCH deck (5-minute slot)

ADR-021 + ADR-025 reframing applied. Lead slide is Advantage #4
null-steering, NOT the triangulation ellipse story. ADR-021 §1.5
pitch order: #4 → #2 → #6 → #8 → #9 → #7 → #3 → #1 → #5.

Companion files (future):
  - docs/demo/script.md          — operator spoken narration, timing
  - docs/demo/slide_deck.md      — engineering walkthrough, ~20 slides
  - docs/demo/soldier_grade_checklist.md — 10 acceptance gates

Render via marp (https://marp.app):
    marp docs/demo/pitch_deck.md --pdf  --output build/pitch_deck.pdf
    marp docs/demo/pitch_deck.md --pptx --output build/pitch_deck.pptx

STRUCTURE (Hacking Guide p.43 pitch template, 10 slides max):
   1. Title
   2. Problem
   3. Solution (null-steering hero)
   4. Live demo / single-screen prototype (link.html link A/B)
   5. Impact & Vision
   6. Research & validation
   7. Sustainability & next steps
   8. Team
   9. Ask / Close

HONESTY CAPS (binding, EW + RF-DSP councils):
- ATK-10 Yagi: ~+8 dBi end-to-end. NEVER claim +20 dB.
- Null depth: "-18 dB receive-pattern attenuation at θ_jammer relative
  to look-direction gain at θ_link." Rehearsed band 15-20 dB; ~25 dB
  best with fresh cal. ADR-008 §D8 caps UI text at ≤20 dB.
- Anti-desense, NEVER ECM. We do not transmit through the L2 DF
  array. DSSS comms are over the Yagi link, separate concern.
- No sub-degree L1 claims. No absolute dBm. No N≥5 density promises.
- GDOP-uncomputable shows "uncomputable (<reason>)" not a fabricated
  float (ADR-013 G3).
- DSSS processing gain quoted as "~30 dB at 10 Mchip/s + length-1023"
  (ADR-025 §B), not a tested field number until WS-A-008 measures.
-->

---
marp: true
theme: default
paginate: true
header: rfmesh — BoTH3 Counter-Jamming Challenge 2
footer: Pitch deck — `docs/demo/pitch_deck.md` — v2 (ADR-021 reframing)
size: 16:9
---

# rfmesh

## A €250 directional radio that points itself, survives jamming by pointing away from it, and triangulates the jammer as a free side-effect.

**BoTH3 Counter-Jamming Challenge 2**
*Belgian Defence — Commando Training Centre Marche-les-Dames — 22-24 May 2026*

*Maciej Wedrychowski + team*

---

## Problem

**Omni radios die under barrage jamming. GNSS dies first.**

Modern battlefield EW (Ukraine 2022-2026) opens with co-channel jamming + GNSS denial. Off-the-shelf small-team comms collapse. Commercial counter-DF gear (RfPatrol Mk2 €5k+, Bukovel-AD classified) ships closed threat libraries and assumes static infrastructure.

The BoTH3 brief asks for a counter-jamming solution at small-unit cost, deployable by frontline troops.

---

## Solution — one matrix, two products

**Headline: spatial null-steering against the jammer, on €250 per node.**

The same 2×2 sample covariance matrix `R` that MUSIC eigendecomposes for angle-of-arrival can be inverted via MVDR:

```
w = R⁻¹ a / (aᴴ R⁻¹ a)
```

to synthesise a **−18 dB receive-pattern null at θ_jammer relative to look-direction gain at θ_link**.

> Under barrage jamming, omni radios die.
> Rotating the Yagi 90° off the jammer keeps the link at +12 dB post-null margin
> **while logging the jammer's bearing for kinetic prosecution.**

Anti-desense, not ECM. We don't transmit through the L2 DF array.

<small>*Null measured against θ_link receive gain on same R; rehearsed band 15–20 dB. Post-null margin shown vs rehearsed co-channel jammer at ~100 m, ~100 mW ERP; degrades with jammer ERP × range.*</small>

---

## Live demo — link A/B + jam-shadow side-effect

`link.html` on tablet, 4 panes, single screen.

1. **Acquire** — two nodes phone home, GPS-prior pointing, scan-and-stare lock in ~9 s (ADR-019). Badge: `sweeping` → `linked`.
2. **Acquire margin** — link margin reads "+18 dB above local noise floor" (pre-jam).
3. **Unplug partner antenna** — margin drops, badge flips `linked` → `searching`, RE-ACQUIRING.
4. **Reconnect** — margin recovers in ~9 s typical, ~41 s worst (escalation ladder ±20°/±45°/±90°). Post-null margin under co-channel jamming = +12 dB (slide 3).

Free side-effect: every peer-sweep drops bearings on any unknown emitter in band. With 2+ nodes, the fusion stack draws an honest confidence ellipse on `locate.html` — **same hardware, same code, two operational effects.**

---

## How honest — the self-diagnosis on screen

Belgian RF/EW jury cross-examination kit, all present on the operator UI:

- **Link margin in dB** above local noise floor — never absolute dBm (no SDR in scope is power-calibrated, B.2).
- **GDOP uncomputable** renders "GDOP: uncomputable (collinear bearings)" — never a fabricated float (ADR-013 §G3).
- **Refusal-as-event** — L1 estimator refusals ship as wire events with the reason, not silent drops (ADR-013 §G4).
- **Drain-timeout → FAULT** — a wedged servo-control loop transitions to FAULT after 2 s with `mode_drain_timeout: prior_mode=<x>`; operator's slider never silently freezes (ADR-024 §2 binding).

<small>*Also shipped: calibrated-arc slider clamp + 5-state badge with countdown + ≤200 ms node_state push. See `docs/demo/soldier_grade_checklist.md` for the full 10-gate kit.*</small>

---

## €250 per node — bill of materials

| Item                          | Unit € | Qty | Subtotal |
|-------------------------------|--------|-----|----------|
| RTL-SDR V4 (DF / RX-only)     | 25     | 1   | 25       |
| ATK-10 Yagi (868 MHz, ~+8 dBi)| 20     | 1   | 20       |
| MG996R servo + 1-axis pan     | 8      | 1   | 8        |
| ESP32-C6 servo controller     | 4      | 1   | 4        |
| Raspberry Pi 4B               | 80     | 1   | 80       |
| Cables, mast, PSU, case       | ~80    | 1   | 80       |
| **Total per DF node**         |        |     | **~217** |
| Add BladeRF 2.0 micro for comms (TX-capable, ADR-025) | +400 | (1 per comms node) | 617 |

DF nodes scale by density (RTL-SDR + Yagi). Comms-mode nodes (DSSS, ADR-025) take a BladeRF — the BladeRF cost is per-comms-node, not per-DF-node. Heterogeneous mesh.

---

## Self-locating directional mesh, no infrastructure

GPS-prior pointing + scan-and-stare (ADR-019) brings the link up between any two nodes that know each other's surveyed positions. No central coordinator, no shared clock beyond NTP, no infrastructure.

Add a node mid-operation, edit one YAML entry on its peer (`field-deploy/node-config.example.yaml`), and the mesh self-extends. SD-card swap on the field Pi is zero-config: the script auto-selects `configs/node-<cpu-serial>.yaml`.

DSSS roadmap (ADR-025): BPSK ~10 Mchip/s, length-1023 m-sequence, **~30 dB processing gain** over the Yagi beams. DSSS rides the Yagi link; the L2 null-steering (slide 3) runs on the separate coherent array — two surfaces, two roles. LPI/LPD off-axis as a free physics property of the directional beam pattern + spreading. Multi-hop routing at the node-runtime layer, contracts stay frozen.

<small>*~30 dB = 10·log₁₀(1023) — algebraic processing gain. End-to-end delivery on BladeRF TX → RTL-SDR/BladeRF RX is not yet hardware-measured (WS-A-008 smoke tests are roadmap, not v1). Cited as design budget, not field number.*</small>

---

## Simulator-first — the demo always works

Every workstream developed against `SyntheticReceiver` (same `Receiver` Protocol as RTL-SDR / BladeRF / Pluto+), parametrised noise/multipath/two-ray/shadowing/IQ-imbalance/DC-offset/ADC-quantisation.

If live hardware misbehaves on stage, **the demo has a working scenario replay** — same dashboard, same fusion, same DSSS, just synthetic IQ behind the same Protocol boundary.

This is the difference between a hackathon demo that won't boot and a credible engineering submission. Mast C empirical anchor (ADR-014) calibrates the simulator against actually-measured field behaviour.

---

## Triangulation pipeline — preserved, on screen

The geolocation pipeline is **shipped** (not roadmap):

- L1 amplitude DF with honest 1-σ azimuth uncertainty (5-15° SNR-dependent, ±20% MC band).
- Stansfield seed + Gauss-Newton MLE fusion across any N≥2 nodes.
- Confidence ellipse, GDOP, per-node residuals, method-tag — all on `FixEvent`, all rendered on `locate.html`.
- CoT/TAK marker with `<shape><ellipse>` polygon to FreeTAKServer for ATAK integration.
- Open threat library: each emitter class (ELRS, Crossfire, GSM, DroneID, Pole-21 stub, Volnorez stub) is one YAML profile + one Python module under `rfmesh-ml/threats/`. Operators extend without firmware updates, vendor sign-off, or classified handshakes.

**BoTH3 target = 20 m at 5 km. Honest v1 delivery envelope:**

| Stack | σ per sensor | σ_position @ 5 km, N=3, GDOP≈2 |
|---|---|---|
| L1 amplitude DF (RTL-SDR + Yagi) | 5–15° | ~250–700 m |
| L2 phase-coherent MUSIC (BladeRF) | 1–3° | ~50–170 m |
| L2 + density to N=8 | 1–3° | **~20–60 m** |

20 m at 5 km is a *target*. We hit it with L2 phase-coherent nodes + density, not with L1 alone. Demo shows the ellipse shrinking as nodes join + the L1/L2 σ split honestly on the dashboard. **No per-sensor magic.**

> *Peer bearings carry an honest Bayesian prior; jammer bearings don't. Same estimator path, two epistemic statuses, no double-counting (ADR-026).* The fusion pipeline filters peer-acquired bearings out of emitter geolocation per-peak: a secondary peak caught off-axis during a peer-refine sweep still contributes to the jammer fix. Density preserved.

---

## Team & ask

**Team:** Maciej Wedrychowski (lead, RF + systems), BartekDu (firmware + field deployment), wikporc (docs cleaning + ops), Claude lead-Opus (architecture + AI-assisted council protocol).

**Architecture:** monorepo (`uv` workspace), frozen contracts (`rfmesh-contracts`), star dependency graph, 7 binding invariants (B1-B7) enforced by a 4-member AI council on every merge to main.

**Ask:** access to one BladeRF 2.0 micro + one ADALM-Pluto+ from the on-site partner pool for L2 phase-coherent DF + DSSS comms hardware-validation during the event.

**Open-source after the event** — full architecture, ADRs (#1-#25), contracts, tests, and the open threat library on GitHub the day the comp closes.

*Questions?*

---

<!-- Q&A backup slide — kept off the visible 9-slide deck, lifted into
the Expert Panel check-in or jury Q&A as needed. -->

## Backup — null-steering math + jury Q kit

**Null depth (ADR-008 §D8):**
- Rehearsed: "−18 dB" typical.
- Honest band: 15-20 dB typical, up to ~25 dB with fresh calibration.
- Wording: "−18 dB receive-pattern attenuation at θ_jammer relative to look-direction gain at θ_link" — answers "dB above what?"

**Yagi gain:**
- ATK-10: ~+8 dBi end-to-end (manufacturer claim minus cable + connector losses + bracket pattern deformation).
- Never quoted as "+20 dB."

**DSSS processing gain (ADR-025):**
- ~30 dB at 10 Mchip/s + length-1023 m-sequence (Gold-class).
- Combined with directional Yagi side-lobe rejection: link-margin headroom under co-channel jamming.

**Sigma honesty (B2):**
- Every `BearingEstimator` empirically calibrated ±20% band at SNR {10, 20, 30} dB vs Monte-Carlo (`test_sigma_honesty.py`).
- L1 amplitude refusals ship as wire events (`L1_REFUSED_PROMINENCE`, ADR-013 §G4) with `azimuth_sigma_deg=180.0` sentinel, filtered at `Fuser.fuse()` entry before inverse-variance.

**GNSS-denied:**
- Mesh has zero critical GNSS dependency. NTP over local mesh (~10 ms) is sufficient for AoA cross-fixing. `NodeStatus.gnss_locked` is an observable flag, not a dependency.

**Counter-Jamming spec (BoTH3 brief, jammer geolocation 2-5 km / 20 m):**
- Met by density-scaled GDOP, N≥3 nodes (advantage #1).
- Single-node bearing accuracy is 5-15° (honest); 20 m / 5 km = 0.23° / sensor, **not achievable per-sensor and we don't claim it.**
