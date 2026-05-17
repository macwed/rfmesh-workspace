# ADR-003 — No GNSS / no TDOA / no magnetometer on the critical path

**Status:** ACCEPTED (backfilled 2026-05-18)
**Date:** 2026-05-18 — decision originally taken pre-WS-CD-001 (April 2026); backfilled per architect council finding F6 (project audit 2026-05-18).
**Author:** lead-Opus (backfill).
**SCHEMA_VERSION change:** **none** — backfilled documentation of an existing decision.

## Context

Three commonly-suggested "upgrades" to a directional-RF geolocation system have been explicitly rejected from the rfmesh architecture's critical path:

1. **GNSS-disciplined timing / GNSS positioning** for the nodes themselves.
2. **TDOA (time-difference-of-arrival) multilateration** as an alternative to AoA cross-fixing.
3. **Magnetometer-based heading reference** for the antenna boresight.

`WORKSTREAMS.md` §2 references this ADR by number, but the ADR itself was never authored. This file backfills it.

## Decision

**No GNSS, no TDOA, no magnetometer.** The architecture commits to:

1. **AoA cross-fixing** (`Fuser` Protocol consumes `BearingReport`s and runs weighted Stansfield + Gauss-Newton MLE per ADR-007) as the *only* geolocation algorithm.
2. **Surveyed node positions** written into `NodeConfig.position` at deployment time, with an honest `sigma_m` (typically 5-10 m for smartphone-GPS-or-map survey; 2-5 m for an ATGM336H module if available). If GNSS is present at a node it overrides this at runtime, but **the mesh does not depend on it**.
3. **NTP timing** over the local mesh network for inter-node time alignment (~10 ms typical, vs the `FusionConfig.batch_window_ms = 100 ms` default — 10× margin).
4. **Heading-by-aim** for antenna boresight: at deployment, the operator points the Yagi at a known visible landmark (mast, chimney, building corner), reads the azimuth off the map, writes it into `NodeConfig.heading_deg`. The mechanical zero-stop in the servo firmware (`firmware/main/calibration.c` per `INHERITED_CONTEXT.md` §1.2) gives an absolute angular reference within the bracket; the heading-by-aim links bracket-frame to geographic.
5. **`NodeStatus.gnss_locked`** exists as a *separate, observed* flag — the mesh does not act on it for positioning/timing; the dashboard surfaces it so the operator can *observe* GNSS jamming as an EW indicator. **Observed, not depended-on.**

## Consequences

**What this enables:**

1. **Resilience against the three things adversary EW preferentially attacks.** GNSS is jammed first on a contested front; PTP / GPS-disciplined oscillators require infrastructure (PTP-aware switches, GPSDO cabling) we do not have; magnetometers at the mast of an SDR rig measure local field distortion from servos / coils / cables / antenna feeds / host PSU — not Earth field. By design, none of these is a single-point failure mode for rfmesh's positioning, timing, or pointing.
2. **The system runs in a GNSS-denied environment by construction**, not as a degraded mode. This is a credibility point for the BoTH3 jury (the brief explicitly flags GNSS-denied operation as a target capability).
3. **Lower hardware cost.** No GPSDO ($200+), no GNSS modules per node ($30+), no magnetometer-equipped IMU ($15+ vs the 6-axis GY-6500 / MPU-6500 we already have). The 6-axis IMUs become *optional tilt-monitoring* for `NodeStatus.healthy` rather than load-bearing heading.
4. **AoA cross-fixing degrades gracefully under multipath.** Multipath widens the bearing's `azimuth_sigma_deg`, which propagates into a wider confidence ellipse — visible, honest, not fatal. TDOA degrades catastrophically under multipath: coherent time-of-arrival measurements get poisoned by reflected paths and the multilateration becomes systematically wrong. Per `INHERITED_CONTEXT.md` §2.1, multipath is the *known* dominant impairment in the deployment environment (forests, trenches, concrete). The architecture accepts the impairment it can absorb (AoA → wider ellipse) and refuses the algorithm it would amplify (TDOA → wrong position).
5. **Per-site magnetometer calibration is not a recurring operational cost.** Heading-by-aim is a one-time deployment step that takes minutes per node and is cable-routing-invariant.

**What this costs:**

1. **Position accuracy is bearing-limited, not ranging-limited.** A wide-baseline TDOA system *could* in principle deliver tighter positions than our weighted AoA at certain geometries. We accept this trade-off because the geometry where AoA wins (multipath-dominated, GNSS-denied) is exactly where the BoTH3 demo operates.
2. **Operator has to survey node positions.** Phone GPS during survey is sufficient (5-10 m); the operational overhead is one survey reading per node at deployment.
3. **Heading-by-aim requires a visible landmark.** In dense forest with no landmarks, this would fail; for the BoTH3 site (open arc with the mast at a known bearing) it works. Mitigation if a future deployment has no landmarks: surveyed antenna alignment from a known-reference station, or a one-time celestial-navigation alignment (sun azimuth at known time).
4. **`NodeStatus.gnss_locked` is a feature we surface but do not act on.** A new agent reading the code may try to "fix" this by making the fusion server prefer GNSS-locked nodes; that would re-introduce a GNSS dependency. This ADR is what they read instead.

## Tradeoffs considered

**Why no GNSS on the critical path:**

GNSS at a node would provide (a) self-positioning, (b) timing discipline for TDOA, (c) frequency-reference discipline for coherent operations. We have alternatives for each:

- (a) Surveyed positions during deployment, 5-10 m accuracy, **smaller than the dominant bearing-driven position uncertainty at 1-5 km range**.
- (b) NTP over local Wi-Fi mesh, ~10 ms accuracy, **smaller than the `batch_window_ms = 100 ms` default** for AoA batching.
- (c) Each SDR has an internal TCXO sufficient for L1 and (with calibration) L2. We do not need 1 Hz GPS-disciplined frequency for sub-degree DF.

The point at which GNSS would matter is **none of our operating regime**. Adding it would be ceremony.

**Why no TDOA:**

Even if we wanted TDOA, we'd need:

- ns-level inter-node timing (we have 10 ms via NTP — three orders of magnitude away).
- A PTP-aware switched network (we have plain Wi-Fi).
- A GPSDO per node ($200 × N nodes, plus install per site).
- Coherent IQ-time-of-arrival across non-coherent SDRs (architecturally impossible with disparate-clock RTL-SDRs).

The investment is multi-thousand-dollar hardware + a different architecture, for a system that **degrades catastrophically under the dominant impairment (multipath)** at our deployment sites. AoA accepts what we cannot avoid; TDOA requires what we cannot afford and rewards what we cannot guarantee.

**Why no magnetometer for heading:**

A magnetometer at the mast measures the **local** magnetic field, which is dominated by:

- The metal bracket holding the antenna
- The MG996R servo's permanent magnets and PWM-driven coils (changes with servo position!)
- The RTL-SDR's USB power-supply noise (changes with sample rate / gain)
- The antenna feed currents (small but in the geometry we care about)
- The host laptop's switched-mode PSU and any cabling

Earth field is a small fraction of this. Hard-iron / soft-iron calibration partially addresses it, but the calibration is **per-site and per-cabling** — moving a cable changes the calibration. In a contested environment with cable repositioning under fire, this becomes unstable. Heading-by-aim is more accurate (~1-2°), more robust, and free.

The 6-axis IMUs in inventory (GY-6500 / MPU-6500) are repositioned as **optional tilt monitoring** for `NodeStatus.healthy` / `status_detail` — they detect if the antenna has been knocked over, which is useful for operational health visibility. They are not heading sources.

## When this decision could change

- **A future BoTH3-class event with a less-contested EW environment** + a budget for GPSDOs + a controlled deployment site without multipath. Different problem, different architecture.
- **A vehicular / mobile node** (out of scope per `ARCHITECTURE.md` §8). Surveyed-position-at-deploy does not work for moving platforms; GNSS becomes mandatory. Parking-lot.
- **A different operating regime** (e.g. open-water maritime, no multipath, line-of-sight only, no GNSS denial). TDOA might dominate. Different problem, different architecture.

For rfmesh v1.0 → BoTH3 demo, the decision stands.

## Why this is documented and not just done

Every RF/EW expert who sees a directional-RF geolocation system asks two questions on the first slide:

1. *"Why not TDOA?"* — answered above.
2. *"How do you handle GNSS denial?"* — answered above.

The jury Q&A rehearsal at `docs/demo/script.md` §4 carries condensed versions of these answers. This ADR is the long-form receipt that the answers are reasoned, not improvised. A future agent considering adding any of the three rejected items can read this ADR and either accept the framing or write a counter-ADR; the conversation is grounded in evidence rather than re-litigated from scratch.

## References

- `WORKSTREAMS.md` §2 — cites ADR-003 by number.
- `ARCHITECTURE.md` §6 — the binding architectural statement of this decision (the "what"). This ADR is the "why".
- `INHERITED_CONTEXT.md` §2.1 — TDOA rejection rationale (in expanded prose).
- `INHERITED_CONTEXT.md` §2.2 — magnetometer rejection rationale.
- `INHERITED_CONTEXT.md` §2.3 — GNSS rejection rationale.
- `INTERFACES.md` §3 `NodeStatus.gnss_locked` — the "observed, not depended-on" flag.
- `INTERFACES.md` §4 `NodeConfig.position` + `NodeConfig.heading_deg` — surveyed values, smaller-than-bearing-error sigma.
- `docs/demo/script.md` §4 — jury Q&A rehearsal cites this ADR's framing.
- `AGENTS.md` §2 — agents do not second-guess physical-world decisions; they treat them as facts.
