# ADR-018 — Directional comms primary; geolocation secondary

**Status:** PROPOSED (2026-05-23)
**Date:** 2026-05-23 (PROPOSED)
**Author:** lead-Opus (drafting at operator request, post 5-member council)
**SCHEMA_VERSION change:** none in this ADR. A follow-up paired with the peer-acquisition contract additions will bump to 1.2.0; that bump is scoped to a separate ADR (ADR-019 or sibling).

## Context

The project's headline framing — "cooperative bearing mesh for RF emitter geolocation, targeting BoTH3 Counter-Jamming Challenge 2" (`ARCHITECTURE.md` §0) — pre-dates the field-validation experience and the council pass on directional-comms tradecraft. Three forces converged to push a reframing:

1. **Hardware reality**: ATK-10 Yagi is ~+8 dBi end-to-end, not +20 dB as earlier handouts claimed. The headline range claim ("20 m at 5 km") is achievable as a side-effect of triangulation but is not where the system most clearly out-competes the alternatives. Selling raw range against TDOA and KrakenSDR rivals invites a head-to-head we don't always win.
2. **EW tradecraft (council ew-specialist)**: the unique problem a 50° HPBW Yagi-on-servo solves is **LPI/LPD + side-lobe rejection of co-channel jammers** (15-20 dB null at boresight-orthogonal), not range. This maps directly to the BoTH3 Counter-Jamming brief and is the genuinely defensible niche.
3. **Field-deployability**: nodes must be deployable by frontline troops with no RF training and may be moved during operations. A geolocation-first framing presumes static sensor placement; a comms-first framing assumes movement and re-acquisition as the default.

The pivot does **not** delete the geolocation pipeline — every contract, estimator, fusion algorithm and dashboard panel for emitter DF stays shipped. Triangulation becomes a side-effect feature: when nodes sweep to acquire each other for comms, the same RSSI-vs-bearing data drops bearings on any unknown emitters detected in the band. With 2+ nodes, the existing fusion stack draws a confidence ellipse.

## Decision

The project's primary mission, effective 2026-05-23, is:

> **A €250 directional radio that points itself, survives jamming by pointing away from it, and triangulates the jammer as a free side-effect.**

Secondary mission: cooperative bearing mesh for RF emitter geolocation (unchanged from prior framing; same hardware, same code).

### Headline-advantage reordering

`docs/ADVANTAGES.md` advantage prioritisation is reframed (no advantage is deleted; all eight remain binding per B7). The pitch deck leads with:

1. **Advantage #4 — Null-steering / side-lobe rejection** (was a slide in the middle; now headline). One sentence: "Under barrage jamming, omni radios die; rotating the Yagi 90° off the jammer keeps the link at +12 dB margin while logging the jammer's bearing for kinetic prosecution."
2. **Advantage #2 — GNSS-denied operation** (unchanged; strengthened by comms-first framing — comms still works under GNSS jamming).
3. **Advantage #6 — Honesty payload** (unchanged; reframed for link health — "link margin: +14 dB, predicted re-acquire 30 s" replaces sigma-ellipse-GDOP as the soldier-visible honesty surface).
4. **Advantage #8 — €250 per node** (unchanged; sharpens against €5k commercial directional radios).
5. **Advantage #7 — Simulator-first** (unchanged).
6. **Advantage #3 — Heterogeneous mesh** (unchanged but demoted; relevant for triangulation side-effect, not the headline).
7. **Advantage #1 — Scaling-by-density** (demoted; relevant for triangulation side-effect).
8. **Advantage #5 — Open threat library** (demoted to roadmap section of the deck).

A candidate **Advantage #9 — "Self-locating directional mesh with no infrastructure"** is proposed as additive (no advantage removal — B7 compatible). Per the existing ADVANTAGES.md governance, addition of a new advantage requires an ADR; this ADR carries that proposal. Acceptance of ADR-018 includes acceptance of Advantage #9.

### Binding-doc edits this ADR authorises

- `ARCHITECTURE.md` §0 — replace the headline framing paragraph (currently "What rfmesh is") with the new comms-first framing. The §1-§7 invariants are **not** edited by this ADR; they remain binding.
- `docs/ADVANTAGES.md` — reorder advantages as above; add Advantage #9.
- `docs/demo/pitch_deck.md` + `docs/demo/slide_deck.md` — lead with the link-A/B slide (acquire → margin shown → partner unplugged → margin drops → re-acquire) before the geolocation slides. Same DNA, comms framing.

These edits are deferred until ADR-018 is ACCEPTED. The lead-Opus performs them; subagents may draft suggestions.

### V1.0 scope boundary

Static-deployed nodes only. Vehicle-mount + hand-carry-with-redeploy scenarios surface a mast-stabilisation / IMU-pointing problem that prior decisions deliberately parked (`INHERITED_CONTEXT.md` §2.2 — 6-axis IMUs in inventory, repositioned as tilt-monitoring not heading). Bringing the IMU back for active pointing-stabilisation is **out of scope for v1.0** and tracked as a future ticket. The pitch may show "vehicle-mount roadmap" as a follow-up; the v1.0 demo runs static.

Cold-start peer discovery in strict EMCON (no omni handshake permitted) is **unsolved** and deferred to a separate ADR (recommended: ADR-019). V1.0 cold-start assumes one omni LoRa handshake to bootstrap; once peers have approximate bearings the system holds in directional-only mode.

### Manual steering control plane

A new bidirectional control plane connects the user's web UI to deployed nodes through the existing backend:

- **Transport**: long-lived WebSocket from each node to the backend (`ws://backend/ws/node/{node_id}`). Node initiates so it works behind cellular NAT. Reconnect-on-drop with exponential backoff.
- **Backend → Node command channel**: backend maintains a per-node WS registry. `POST /command/{node_id}` (UI → backend) is validated and forwarded to the registered WS. Returns 503 if the node is not registered (no silent fallback, B3).
- **UI push channel**: separate `ws://backend/ws/ui` for the frontend to receive live updates (replaces HTTP polling for the bearings/links view; the existing 2.5 s polling stays as a fallback for the locate.html / emit.html maps).
- **Command envelope**: stays out of `rfmesh-contracts`. The Pydantic model lives in `rfmesh-node/src/rfmesh_node/commands.py` and is re-exported to the backend by direct import. Contracts (B1) are reserved for cross-workstream data products; UI↔node coordination is server-vs-node infrastructure, not a contract.

### State machine on the node

Manual steering temporarily overrides the sweep loop. State enum: `SWEEPING | MANUAL_HOLD | PARKED | ACQUIRED_PEER | FAULT`. State transitions: operator command, peer-found event, idle timeout (manual hold releases after a configurable timeout → returns to `SWEEPING`), uncalibrated-servo refusal (`FAULT`).

The state machine lives in a new `NodeController` class **above** L1SweepLoop. L1SweepLoop is refactored to `run_one_sweep()` (callable, returns a `BearingReport | None`); the looping behaviour moves into `NodeController`. `Node.__init__` accepts `controller: NodeController | None` with `sweep_loop: L1SweepLoop | None` kept as a deprecated alias for one release (backward-compat with BartekDu's just-shipped path).

### Manual-steer safety (3 layers, all binding)

1. **Firmware (deepest)**: servo firmware clamps to per-axis NVS calibration limits via `cal_types.h`. Out-of-range returns `ERR_RANGE`; no movement. This stays unchanged.
2. **Node-side (`rfmesh-node`)**: `NodeController` validates angle against cached `cal.angle_min_deg`/`angle_max_deg` before issuing `servo.move()`. Out-of-range returns an error message back to the backend over the WS; never silently clamps.
3. **Backend / UI**: client-side range hint from `/node/{id}/capabilities` for slider clamping (UX), but the authoritative refusal is the node side. Backend forwards the command and surfaces the node-side error to the UI as a red toast (`"NODE-A: requested 285°, calibrated arc is 30-270°"`).

### Anti-sync acquisition protocol

For two nodes to acquire each other under symmetric sweeping, the joint geometric probability of mutual coverage is ~7.7 % per dwell — but with zero phase drift across identical periods the actual probability is bistable (1 or 0 depending on phase). The recommended protocol:

- **Asymmetric beacon-and-search**, role assigned by lowest `node_id` (deterministic, requires no shared clock, survives mesh-comms loss during acquisition).
- Lowest `node_id` is **PARKED**: holds antenna at last-known peer bearing (or surveyed default), transmits beacon duty-cycled.
- Higher `node_id` is **SEARCHER**: sweeps full arc continuously.
- After lock, roles swap so the previously-PARKED node refines its bearing on the now-stationary searcher.
- Re-acquisition triggers (OR-gate): RSSI drop >6 dB, T_stale 30 s, IMU motion delta >5°/2 m, operator command.
- Implementation lives in a new `PeerAcquisitionService` in `rfmesh-node` (not centralised — survives the "troop-deployable, mobile" pivot intent).

### Peer-bearing sigma honesty (B2)

A `BearingReport` produced by acquiring a peer is **not** a DF measurement of an unknown emitter — it has a strong Bayesian prior from prior comms or surveyed positions. Feeding peer-bearings into the existing `FixEvent` pipeline at face-value sigma double-counts information and breaks B2.

**Required (deferred to ADR-019 paired with SCHEMA_VERSION 1.2.0 bump)**:

- Add `Capability.L1_PEER_RSSI` enum member to `rfmesh_contracts.enums`. Bearings produced by peer-acquisition sweeps set `BearingReport.method = Capability.L1_PEER_RSSI`; the fusion solver filters these out of emitter-geolocation fixes.
- Re-run `test_sigma_honesty.py` with a peer-acquisition fixture in `SyntheticReceiver` (known beacon, known LOS, prior on bearing). Expected: tighter sigma than the unknown-emitter case.
- Add `NodeStatus.peer_links: tuple[PeerLink, ...] | None` field (optional → MINOR bump compatible).

ADR-018 explicitly **does not** make these contract changes. They wait for ADR-019 which is paired with the SCHEMA_VERSION 1.2.0 bump (B1 governance). V1 of the comms-mode code emits `method=Capability.L1_RSSI` even for peer-acquisition sweeps; the fusion side currently has no peer-filtering logic, but it also doesn't yet have a comms-mode caller producing peer-bearings — so the dishonesty is theoretical until peer-acquisition lands. The ADR-019 contract bump and the PeerAcquisitionService implementation must land together.

### UI mandate

The matplotlib `rfmesh-ops` dashboard becomes dev/diagnostic only. The canonical operator UI is the existing browser frontend (`deployment/frontend/`), served by the FastAPI backend at `http://host:8000`.

A new soldier-facing page `link.html` (sibling to `locate.html`) is the canonical comms-first screen:

- Map of nodes with state-colored discs and pointing arrows.
- Link ribbons between paired nodes (green/amber/red by margin).
- Per-node detail panel: manual steer (map-tap-to-steer primary, numeric slider fallback with hard clamps at calibrated arc), big STOP button, link margin in dB, last-acquired timestamp.
- Mesh-wide ALL-STOP button (red, two-tap confirm) on every screen.

`rfmesh-ops` matplotlib code stays in-tree as a `/dev` route (FastAPI-served PNGs) accessible behind a flag. Not deleted — that would be a future trap.

## Consequences

### Positive

- The pivot reframes the project against its genuinely defensible niche (LPI/LPD + spatial jammer rejection) rather than competing on raw range.
- Manual steering plus auto-acquisition gives the operator one clear cause-and-effect surface — a soldier-grade UX that doesn't require RF training.
- Triangulation pipeline is preserved as a side-effect feature. The 8th advantage (€250 budget) gets stronger because the same hardware now serves two purposes.
- Helper modules (`servo_motion.py`, `command_channel.py`) land as additive new files; BartekDu's just-shipped field-node path (`run_node.py`, `l1_sweep.py`, `http.py`) is byte-identical until the operator opts into comms mode via a new flag.

### Negative

- Pitch-deck rewrite work (lead-Opus task; not subagent).
- Frontend `link.html` is a new build with several soldier-grade UX must-haves before deployment (10-item list documented in council demo-integrity report; tracked separately).
- Vehicle-mount scenario is parked. Pitch must not over-claim mobility for v1.0.
- Cold-start in EMCON is unsolved (ADR-019).
- One eventual SCHEMA_VERSION bump (1.2.0) is on the path for peer-bearing honesty; tracked separately.

### Risks

- An RF/EW jury fact-checks the +8 dBi vs +20 dB claim. Mitigation: pitch never claims +20 dB; quotes side-lobe rejection numbers (15-20 dB null) which are correct.
- Operator confusion between manual steering and auto-acquisition modes. Mitigation: explicit state badge in `link.html` (`SWEEPING` / `MANUAL_HOLD` / `ACQUIRED → NODE-B  +18 dB`); manual hold has a visible countdown to auto-resume.
- Backend WS registry adds state; per-node command delivery surface introduces a new B3 risk (silent command drop on disconnected node). Mitigation: backend returns 503 with reason when node not registered; UI surfaces this; tests cover one disconnect + reconnect.

## Alternatives considered

- **Keep geolocation-primary, add comms as a Year-2 feature.** Rejected: comms is the more defensible competition position now; deferring loses the market window.
- **Drop geolocation entirely.** Rejected: triangulation pipeline is shipped, tested, and a free side-effect of peer-sweep data. Deletion would throw away validated work.
- **Commercial directional radio bake-off.** Rejected: BoTH3 is open-source / cost-constrained; €250 vs €5k is the binding competitive advantage.
- **Beamforming via L2 phased-array.** Rejected for v1.0: L2 hardware (KrakenSDR / bladeRF / Pluto+) is not on-bench and not in budget. L1 amplitude DF with ±8 dBi gain is sufficient for the link establishment task (L1 sigma 5-15° tolerates the ±20° pointing error within which Yagi gain stays above +10 dB margin).

## References

- Council pass 2026-05-23 (five members: architect, rf-dsp-specialist, demo-integrity, code-reviewer, ew-specialist). Verdicts:
  - architect: REQUEST-ADR (this ADR)
  - rf-dsp-specialist: NOTE + one B2 sigma-honesty issue (deferred to ADR-019)
  - demo-integrity: APPROVE-WITH-RECOMMENDATIONS + 10 soldier-grade must-haves
  - code-reviewer: 2 BLOCKs (L1SweepLoop mode-switching surface, backend WS registry) + recommendations
  - ew-specialist: BLOCK-WITH-REASON — RECOMMEND REFRAMING (accepted)
- `INHERITED_CONTEXT.md` §1.3, §2.2, §3.4 — Yagi gain reality, IMU positioning, hardware availability
- `AGENTS.md` §1 — Seven Binding Invariants (B1 contracts frozen, B2 sigma honesty, B3 no silent fallbacks, B6 hardware = operator domain, B7 advantages binding)
- BartekDu's commit `6ca3c43` (field L1 node deployment) — preserved unchanged by this pivot's v1.0 default path
- `docs/adr/ADR-015-firmware-target-esp32c6.md` (precedent for operator-driven pivots)
