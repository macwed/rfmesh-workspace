# rfmesh — The Eight Architectural Advantages

**Status:** binding pitch content (B7). Edits by lead via ADR only.
**Date:** 2026-05-23 (advantage order reframed per ADR-021 directional-comms pivot; content unchanged, only the priority order moves).
**Audience:** every collaborator, every council subagent, every jury-facing surface (slides, dashboard captions, CoT remarks).

This file is the canonical statement of what the rfmesh project competes on. Every demo claim, every slide caption, every jury Q&A answer cross-references one of the eight advantages here. If a proposed change would remove or weaken one of them, escalate to the lead before proceeding (per AGENTS.md §1 Invariant B7).

---

## §0 What rfmesh is competing on

rfmesh is a **€250 directional radio that points itself, survives jamming by pointing away from it, and triangulates the jammer as a free side-effect** (ADR-021 reframing, 2026-05-23). Target event: the BoTH3 Challenge 2 (Counter-Jamming, Belgian MoD, Commando Training Centre Marche-les-Dames). The brief asks for counter-jamming solutions; the technically defensible niche of the same hardware (Yagi + servo + RTL-SDR + ESP32) is **side-lobe rejection of co-channel jammers + GPS-prior auto-acquisition of peer links** — directional comms first. The triangulation pipeline (L1 amplitude DF + Stansfield/MLE fusion + ATAK + confidence ellipses) is preserved as a side-effect feature; the cross-fix story is honest, demonstrable, and still on the dashboard.

The jury includes first-class RF/EW specialists of the Belgian Defence. They will technically scrutinise every number on every slide. They will ask questions calibrated to distinguish *"this person built and understands a working directional radio + DF system"* from *"this person ran an AI and showed up at our event"*. The project's success is measured by their judgement.

---

## §1 The winning edge — eight architectural advantages (order per ADR-021)

We are not trying to out-engineer Bukovel-AD (classified Ukrainian system) or RfPatrol Mk2 (commercial, €5k+, closed threat library). We are trying to demonstrate, on €250-per-node hardware, **a small number of architectural advantages that those systems either do not have or do not ship**. Each one is grounded in a real technical decision the architecture already encodes.

The priority order (which slides lead the pitch) follows ADR-021: **#4 null-steering is the headline**, followed by GNSS-denied + honesty + budget; then simulator + heterogeneous + density + threat library as supporting evidence. Advantage *numbering* is preserved (everything in the codebase + ADRs cross-references the original numbers); only the pitch order moves.

### Advantage #1 — Scaling through deployment density, not per-sensor magic

The "20 m at 5 km" spec is 0.23° of bearing accuracy at the sensor — which no two-element array under hackathon conditions can deliver. We meet the spec by **N ≥ 3 nodes with bounded GDOP** and weighted-least-squares fusion. This is honest, demonstrable, and the demo shows the confidence ellipse shrinking as nodes join. Commercial gear rarely publishes this scaling math; the open-source community sometimes does (KrakenSDR community knowledge), but never as a coherent product proposition for the operator.

### Advantage #2 — GNSS-denied by construction

Most adversary EW playbooks start by jamming GNSS. Our mesh has **no critical dependency on GNSS at all** — node positions are surveyed at deployment, time is synchronised by NTP over the local mesh network (~10 ms — sufficient for AoA cross-fixing, which is what we do; we explicitly do **not** do TDOA, which would require ns-level sync that only GNSS-disciplined oscillators provide). `NodeStatus.gnss_locked` is a **separate, observable** flag — the system *observes* GNSS jamming as an EW indicator while continuing to function. This is a pitch slide that lands with a Belgian Defence audience.

### Advantage #3 — Heterogeneous mesh — cheap nodes work alongside good ones

A fusion solver that takes an iterable of `BearingReport`s, each carrying its own honest `azimuth_sigma_deg`, automatically weights them inverse-variance. An 8°-σ RTL-SDR L1 node and a 1.5°-σ bladeRF L2 node contribute to the *same* fix, each according to its honest uncertainty. This is the architectural move that lets €30 dongles share a deployment with €500 phase-coherent radios from the on-site partner pool. Nobody else builds this — commercial gear is single-tier, military gear is single-tier.

### Advantage #4 — Dual-use null-steering: one matrix, two products

The same 2×2 sample covariance matrix R that MUSIC eigendecomposes for angle-of-arrival can be inverted via MVDR (`w = R⁻¹·a / (a^H·R⁻¹·a)`) to synthesize a spatial null toward the jammer — protecting own-comms while simultaneously geolocating the threat. Our hardware already supports it (it is one weight-vector formula away from MUSIC), and the BoTH3 brief explicitly names null-steering as a clever-hack option. The slide caption is *"One matrix, two products: target geolocation for kinetic effect, null-steering for own-comms protection."* Most amateur DF demos miss this entirely.

The framing on slides and in spoken script is **anti-desense, not ECM** (we are not transmitting through this array; we are protecting our own L2 coherent DF channel from being desensitised by a co-channel jammer while continuing to produce bearings on it). ADR-008 §D8 caps UI text quoting null depth at ≤ 20 dB.

### Advantage #5 — Open, extensible threat library as moat

The L3 classifier in `rfmesh-ml/threats/` is one Python module per emitter class (ELRS, Crossfire, GSM jammer, Pole-21, Volnorez, DroneID, UNKNOWN). Adding a new threat is a YAML profile + a Python module — no firmware update, no vendor sign-off. RfPatrol Mk2 ships a closed library at €5k+; Bukovel-AD keeps its library classified. We ship the **structure** as v1.0 and let operators extend it. This is the moat — not the v1.0 library contents (stubs for the classified threats are honest about what is unverified), but the open extension model.

### Advantage #6 — Honesty payload on every fix

`FixEvent` carries not just position, but covariance, 95% ellipse, GDOP, per-node residuals, solver method, and (optionally) classification with confidence. The ATAK marker renders the ellipse as a polygon, shrinks when nodes are added, expands when nodes are killed. A node many sigmas off its own reported uncertainty is highlighted as a probable multipath/calibration outlier — the system **self-diagnoses**. This is the demo behaviour an RF/EW expert specifically looks for; its absence is how amateur demos fail to convince.

### Advantage #7 — Simulator-first development

The entire system was developed and tested against a `SyntheticReceiver` that implements the same `Receiver` Protocol as the real RTL-SDR / bladeRF / Pluto+ — with parameterised noise, multipath, two-ray, shadowing, IQ imbalance, DC offset, ADC quantisation. This makes every workstream hardware-free in development, and — equally important — it gives us a **replay scenario** if live hardware misbehaves on stage. The demo always has a working fallback.

### Advantage #8 — €250-per-node budget is realisable

Three RTL-SDR V4 (€25 each) + three ATK-10 Yagi antennas (€20 each) + three Raspberry Pi 4B (€80 each) + servos, masts, cables = well under €250 amortised. The phase-coherent upgrade path (bladeRF 2.0 micro at €400 from partner pool) is per-fix-quality enhancement, not a hardware reset.

---

### Advantage #9 — Self-locating directional mesh, no infrastructure (additive per ADR-021)

Each pair of nodes that wants to talk computes the great-circle bearing
to each other from surveyed positions, points the Yagis there directly
(GPS-prior pointing per ADR-019), and refines via a small scan-and-stare
mini-sweep. No central coordinator, no infrastructure, no shared clock
beyond NTP. The link comes up in ~9 seconds typical / ~41 seconds worst
case (escalation ladder ±20° → ±45° → ±90°). Add a node mid-operation,
edit one YAML entry on its peer, and the mesh self-extends. This is
what "troop-deployable" actually means in software terms.

---

## §1.5 Pitch order (ADR-021)

The deck leads with **#4 null-steering** as the headline counter-jamming
sentence: *"Under barrage jamming, omni radios die; rotating the Yagi
90° off the jammer keeps the link at +12 dB margin while logging the
jammer's bearing for kinetic prosecution."* Then in order:

1. **#4 Null-steering** — headline (counter-jamming brief, dual-use)
2. **#2 GNSS-denied** — survives EW's first move
3. **#6 Honesty payload** — reframed for link health (margin / age / state)
4. **#8 €250 per node** — vs €5k commercial directional radios
5. **#9 Self-locating mesh** — troop-deployable, additive per ADR-021
6. **#7 Simulator-first** — the demo has a working fallback
7. **#3 Heterogeneous mesh** — side-effect-feature evidence (triangulation)
8. **#1 Scaling via density** — triangulation slide (side-effect)
9. **#5 Open threat library** — roadmap section, not a flagship slide

Advantage numbering in cross-references (commit messages, ADRs, code
comments) stays the same as §1 above — only the **pitch order** moves.

---

## §2 How the advantages compound

These eight advantages compound. The demo shows them as a coherent story: **cheap nodes, smart software, honest output, EW-resilient, operator-extensible, real ATAK integration**. That is the pitch.

The demo script (`docs/demo/script.md`) maps each advantage onto a specific dashboard panel + spoken sentence. The ops dashboard layout (`packages/rfmesh-ops/src/rfmesh_ops/layouts.py::DEMO_LAYOUT_TRENCH`) is composed so each panel visualises at least one advantage. The cross-reference table:

| Advantage | Dashboard panel | Spoken-script anchor |
|---|---|---|
| #1 Scaling via density | FixPanel (% of range), GdopHeatmapPanel | script.md §2 Beats B/C/D |
| #2 GNSS-denied | NodeStatusPanel (`gnss_locked` flag) | script.md §4 Q9 |
| #3 Heterogeneous | L1vsL2Panel (σ comparison) | script.md §2 Beat D |
| #4 Null-steering | NullSteeringPanel | script.md §3 Beat E |
| #5 Threat library | ClassificationOverlayPanel | script.md §2 Beat C / Q&A |
| #6 Honesty payload | FixPanel + ResidualsPanel + ellipse rendering | script.md §1 + §2 |
| #7 Simulator-first | (recorded-IQ announcement at demo start) | script.md §5 |
| #8 €250/node budget | (slide-only, not a dashboard panel) | script.md §6 |

---

## §3 Binding rule

Any subagent or council reviewer who proposes a change that **removes** one of these advantages must surface it as an ADR proposal first — per AGENTS.md §1 Invariant B7. **Tightening or extending an advantage is fine.** The list is the floor of what the demo guarantees, not the ceiling.

A ninth advantage (#9 — self-locating directional mesh) was added
additively per ADR-021; the same rule binds future additions.

## §4 What to cross-reference

When a doc or ticket needs to anchor a claim to one of these advantages, cite:

```
docs/ADVANTAGES.md §1 Advantage #N
```

`docs/deprecated/` carries the retired pre-pivot pitch materials; do
not cite them in new work.
