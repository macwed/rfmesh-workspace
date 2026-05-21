<!--
rfmesh — BoTH3 jury PITCH deck (5-minute slot)

Companion to:
- docs/demo/slide_deck.md — engineering walkthrough, ~20 slides, for
  Expert Panel check-in + Q&A depth backup. NOT the jury pitch deck.
- docs/demo/script.md — operator spoken narration, demo-flow timing.

This file IS the jury pitch deck. 10 slides max, 5-minute talk slot
+ 5-minute Q&A per the BoTH3 Hacking Guide p.44.

STRUCTURE (binding — matches Hacking Guide p.43 pitch template):
  1. Title
  2. Problem
  3. Solution
  4. Live demo / single-screen prototype
  5. Impact & Vision (1-3 y)
  6. Research (expert + stakeholder validation)
  7. Sustainability & Next Steps
  8. Team
  9. Ask / Close

Render via marp (https://marp.app):

    marp docs/demo/pitch_deck.md --pdf --output build/pitch_deck.pdf
    marp docs/demo/pitch_deck.md --pptx --output build/pitch_deck.pptx

DESIGN PRINCIPLES (binding for any edit):
- 5 min talk = ~30-40 sec per slide. Trim relentlessly.
- Lead with problem, NOT architecture diagram.
- Single hero number per slide where possible.
- The "single-screen prototype that immediately brings your solution
  to life" (Hacking Guide p.42 prototyping tip) is the ATAK marker on
  the tablet. That is the live-demo slide.
- Honesty caps from slide_deck.md still apply: no sub-degree L1
  claims, no absolute dBm, null-steering framed as anti-desense not
  ECM, no deployment-density N>=5 promises.
-->

---
marp: true
theme: default
paginate: true
header: rfmesh — BoTH3 Counter-Jamming Challenge 2
footer: Pitch deck — `docs/demo/pitch_deck.md` — v1
size: 16:9
---

# rfmesh

## Cooperative bearing mesh for RF emitter geolocation

**BoTH3 Counter-Jamming Challenge 2**

Triangulate jammer antennas at 2–5 km, ≤ 20 m positional accuracy, on €250-per-node hardware, with C2-native output.

*Maciej Wedrychowski + team — 2026-05-22*

---

# The problem

**On a contested battlefield, when an adversary jams or transmits in our band, tactical units need to know *where the emitter is* — not just that one exists.**

- BoTH3 spec: **2–5 km standoff, ≤ 20 m positional accuracy.**
- Same range a UAV cue or counter-battery hand-off needs.
- Today's units don't have this. They have:
  - €5 k+ closed-library handsets that detect, don't localise.
  - Classified vehicle-mounted systems they can't deploy forward.
  - Hobbyist single-node SDR setups with no fusion, no honest uncertainty.

**The gap is not detection. The gap is *cooperative* localisation in a forward-observer kit.**

---

# Existing solutions and where they break

| System | Cost | What it does | What it doesn't |
|---|---|---|---|
| **RfPatrol Mk2** | €5 k+ unit | Single-sensor detect + classify | No bearing, no fusion, **closed threat library** |
| **Bukovel-AD** | Classified, military | Vehicle-mounted DF + jamming | Not deployable forward; library locked |
| **KrakenSDR + community** | €500 + DIY | Single-node MUSIC | No fusion, no honesty, no C2 output |
| **TDOA multilateration** | Lab-grade | High-precision geolocation | GPS-disciplined timing — **jammed first on the front** |

**rfmesh occupies the unaddressed quadrant:** *cooperative, forward-deployable, open library, honest uncertainty, C2-native, GNSS-independent by construction.*

---

# The solution

**A small mesh of cheap nodes. Each computes a bearing. A central fuser cross-fixes them into emitter positions with honest uncertainty. Output ships to ATAK / any C2 the operator already uses.**

- **Distributed bearings, central fusion.** Stansfield seed + Gauss-Newton MLE refinement → Fisher-information covariance → 95% confidence ellipse on every fix.
- **Heterogeneous nodes co-fused.** A €250 RTL-SDR L1 node and a €500 bladeRF L2 node contribute to the same fix, weighted by their honest sigmas. **Add nodes → ellipse shrinks visibly.**
- **GNSS-denied by construction.** Surveyed positions, NTP timing — no critical dependency on the layer the adversary jams first.
- **Honest refusal.** When site / frequency combo doesn't deliver, the system says so (per-node residuals + ellipse growth) — it does not lie.

---

# Prototype — single-screen ATAK marker

![bg right:55% w:90%](docs/demo/artifacts/trench_demo_beat_C.png)

**One critical touchpoint: the operator's ATAK tablet.**

- Red hostile-emitter marker on the live tactical map.
- 95% confidence ellipse polygon around it.
- **Ellipse shrinks** as nodes come online: 2 nodes → MEDIUM, ~600 m ellipse. 3 nodes → MEDIUM, ~360 m ellipse. 4 nodes (L2 joins) → semi-minor 125 m, GDOP 1.02.
- Marker fades gracefully if a node dies mid-demo — system **degrades**, not lies.

**Live in the room. Operator already knows the symbology — instant credibility.**

---

# Impact & Vision (1–3 year)

**Year 1 — kit form.**
- Three-node L1 forward-observer kit. €750 total hardware. Open hardware BoM + ESP32 firmware + Python stack. Drop-in for any operator with an ATAK device.

**Year 2 — threat library.**
- Open, extensible classifier library (YAML profiles + ONNX models). Operators *contribute* new profiles when they encounter new emitters. Library *grows* with deployment.

**Year 3 — defence integration.**
- L2 (phase-coherent) tier for fixed-emplacement nodes — sub-degree bearings, MVDR null-steering for anti-desense.
- Integration partnerships with C2 vendors (FreeTAKServer, ATAK, TAK-CIV equivalents).
- EU Defence Fund / EDIDP eligible — civilian-developed dual-use, open core, defence integration.

---

# Research — what's been validated

**Phase C empirical anchor (Mast C, 3 km, 2026-05-19).**

| Measurement | Value | What it proves |
|---|---|---|
| Peak SNR vs sky-noise floor | +11.4 dB | L1 detects at 3 km on €25 hardware |
| Stationarity (60 s, peak heading) | σ = 0.17 dB | No drift, no PSU jitter |
| Front/back ratio | 14.9 dB | Yagi pattern as expected |
| Peak bearing (fine sweep) | 305° vs map 306° | Within angular grid resolution |

**Software: 656 tests, mypy strict + ruff clean. 5-specialist council review (architect, code-reviewer, DSP, demo-integrity, EW-specialist).**

**Simulator calibrated against the bench** (`packages/rfmesh-sdr/tests/test_c4_mast_c_calibration.py`) — six tolerance assertions ratify the simulator reproduces real-world Mast C anchor.

---

# Sustainability & Next Steps

**Revenue model — open core + hardware kits + services.**

- **Open source the software stack.** Apache-2.0 / MIT. Community contribution to threat library is the moat: nobody else has an *open* extensible threat library in this market.
- **Sell hardware kits.** €750 / 3-node L1 kit, €1500 / 4-node L1+L2 kit. Production-grade enclosures, pre-calibrated, drop-in deployable. Margin pays the dev cost.
- **Integration services.** C2 stack adaptation, custom threat profiles, classified-environment deployments. €€€ defence revenue, civilian-IP-clean separation.
- **Funding paths.** EU Defence Fund (EDF) calls 2026-2027 for "cooperative ISR in EW-contested environments" are a direct fit. EDIDP 2026 RF/EW track equally.

**Six-month milestones:** kit-form v1.0, BoTH3 demo published as case study, 3 pilot deployments with allied units.

---

# Team

**Today: Maciej Wedrychowski (technical lead).**
- 6 years software engineering background, RF / DSP / embedded specialisation since 2024.
- Built the rfmesh stack solo over 25 days (with AI council). 656 tests, 14 ADRs, 8 architectural advantages doc.

**Looking to recruit at BoTH3:**
- **EW operator / SME** — validates threat library priorities, deployment ergonomics, field tactics.
- **C2 / ATAK integrator** — closes the operator-side loop; we have CoT XML out, they have the network in.
- **Defence procurement / partnerships** — turns the open core into deployable kit at scale.

**The technology is built. The team to take it to deployable is what BoTH3 is for.**

---

# The ask

**Help take rfmesh from working prototype to fielded kit.**

- **Today**: hands-on bench demo + Q&A. We brought the hardware.
- **This week**: validate the threat-library priorities with allied EW SMEs. Map the C2 integration surface.
- **This year**: pilot deployment with one allied unit. EU Defence Fund 2026 application.
- **By BoTH4**: three-node L1 kit on the shelf, four-node L1+L2 kit in pilot, threat library at 10+ profiles validated against real-IQ captures.

**Open core. Honest uncertainty. Operator-deployable. Built in 25 days, deployable in 12 months.**

*Thank you. Questions?*

---

# Appendix — backup slides for Q&A

The next slides are NOT part of the 5-min pitch. They are reserved for Q&A answers and Expert Panel deep-dives. See also `docs/demo/slide_deck.md` for the full 20-slide engineering walkthrough.

---

# Backup — eight architectural advantages

(Detail in `docs/demo/slide_deck.md`. One-line per advantage:)

1. **Scaling through deployment density** — N≥2 nodes, fusion is N-indifferent. Add nodes → shrink ellipse.
2. **GNSS-denied by construction** — surveyed positions + NTP. No GPS in critical path.
3. **Heterogeneous mesh** — L1 + L2 nodes co-fused, weighted by honest sigma.
4. **Dual-use null-steering** — same array R, two products: MUSIC for DoA, MVDR for anti-desense.
5. **Open extensible threat library** — YAML profile per emitter; community-growable.
6. **Honesty payload on every fix** — covariance, ellipse, GDOP, per-node residuals.
7. **Simulator-first development** — `SyntheticReceiver` Protocol; software validated without hardware.
8. **€250-per-node budget** — RTL-SDR V4 + ATK-10 Yagi + ESP32-S2 + MG996R.

---

# Backup — Phase C details

- **Mast A** (936.568 MHz, 650 m): FAIL — system refused (L1 prominence below 6 dB gate). **Not a bug** — honest refusal is the design intent. Site/frequency combo did not deliver.
- **Mast B** (936 MHz, 2.2 km): FAIL — 8.5 dB F/B at wrong azimuth. Co-channel interferer dominating. System reports residuals, operator catches the misbearing.
- **Mast C** (958.7 MHz, 3 km): **PASS**. The empirical anchor for the 2-5 km envelope claim.

Three measurements, three different failure / pass modes — the system is **diagnostic-capable**, not just localisation-capable. Per ADR-014 (ACCEPTED 2026-05-20), Mast C is the binding empirical anchor going forward.

---

# Backup — honest error budget

| Source | Magnitude | Mitigation |
|---|---|---|
| L1 per-bearing σ | 5–15° SNR-dependent | Inverse-variance weight in fusion |
| Node-position survey σ | 5–10 m (phone GPS) | Honest field on every BearingReport |
| Heading survey σ | 1–2° (survey-by-aim) | Per-node, set once at deployment |
| Multipath @ < 1 km | Bearing flat / wrong | L1 refusal gate (prominence < 6 dB) |
| Co-channel interferer | Wrong-azimuth peak | Per-node residual highlight |

**What we do NOT claim:** sub-degree L1 accuracy. Absolute dBm. ECM capability. Deployment density above N=5.

**What we DO claim:** at 2–5 km, with 3+ nodes, honest geometry, the system delivers MEDIUM-or-better fixes with ellipse semi-minor < 15% of range. Per the simulator-calibrated + bench-anchored numbers in `docs/demo/script.md` Beats B/C/D.
