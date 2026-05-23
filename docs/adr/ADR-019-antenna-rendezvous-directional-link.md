# ADR-019 — Antenna rendezvous: directional mutual-pointing for node-to-node links

**Status:** PROPOSED (2026-05-23)
**Date:** 2026-05-23
**Author:** lead-Opus, synthesising a 3-specialist council (rendezvous-algorithm /
RF-physics / servo-integration) on the directional-deafness problem raised by the
operator.
**SCHEMA_VERSION change:** **NONE**. The peer roster (id + position) and all timing
live in a node-layer `RendezvousConfig`, exactly as `L1SweepConfig` already does;
`packages/rfmesh-contracts/**` is untouched (B1 satisfied). No `Bearer`/`NodeStatus`
field is added or changed.
**Scope:** `packages/rfmesh-node/` only.

## Context

Each L1 node carries a directional ATK-10 Yagi (50° HPBW) on a 1-axis pan servo with
a calibrated ±90° arc. Today the only servo motion is `L1SweepLoop`
(`packages/rfmesh-node/src/rfmesh_node/l1_sweep.py`): a blind ±90° sawtooth that
finds the bearing to an *emitter* (jammer).

A new operational need: two nodes must point their Yagis **at each other** to bring up
a directional inter-node link, and — because the antenna is shared — time-share that
one servo between *holding the link* and *jammer DF* ("make and hold the link, break
off periodically to find/update jammers").

The operator's first instinct was "make every second node sweep counter-clockwise."
That does not solve it: two nodes both alternating direction can stay phase-locked, and
on two *moving* directional beams the mutual main-lobe overlap almost never coincides.
This is the classic directional-neighbour-discovery (deafness) problem.

**RF physics (council finding).** With both ends directional the two-way response is the
*product* of two patterns — a cos⁴ roll-off, ~4× faster in dB than the single-ended
cos² the validated L1 path assumes. The peer is detectable only over ~±20–25° of mutual
alignment, not the whole arc, so a blind double-sweep is not viable. The enabler is that
**both nodes know both GPS positions** (peer position is surveyed and supplied in
config): each computes the expected bearing to the other and points there directly, so
mutual overlap is achieved *by construction*.

**Motion-rule correction (operator).** The same-side-approach "backlash discipline"
(`INHERITED_CONTEXT.md` §3.1.1) is a **host-side convention in `l1_sweep.py`** that
exists so a *measured* DF bearing is repeatable to sub-degree. It is **not**
firmware-enforced — `firmware/main/servo.c` only clamps to the calibrated ±90° range.
For link establishment the antenna merely needs to sit inside the peer's main lobe
(~±25°), where a few degrees of backlash is irrelevant. So connection mode moves
**bidirectionally**, limited only by the ±90° arc; the same-side discipline is retained
only for the jammer `L1SweepLoop`, where bearing accuracy depends on it.

## Decision

**1. GPS-prior pointing.** Each node computes the great-circle initial bearing to the
peer from the two surveyed positions, converts to a servo angle
`wrap180(bearing_to_peer − heading_deg)` (the inverse of the forward map
`l1_sweep.py:173`), and points there directly. Out-of-arc (`|angle| > 90°`, i.e. peer
behind boresight) is **refused loudly** before any motion (B3), never clamped — it is a
mount/survey problem (B6), reported via `NodeStatus.status_detail`.

**2. Scan-and-stare for refinement.** Roles are derived with no negotiation from the
stable `node_id`: **lower id STAREs** (holds at its computed bearing), **higher id
SCANs** (a small ±N° refine mini-sweep, default ±20°, that crosses the stationary
peer in one pass). Both nodes compute the same split from the same two strings, so
simultaneous registration cannot race. Equal `node_id` is a **loud failure** (both would
SCAN → deafness) — duplicate ids are an operator config error.

**3. Bidirectional connection-mode motion.** The refine mini-sweep and re-point move in
either direction, limited only by the ±90° arc. No backoff, no forced same-side
approach. Any residual lash in a refined peer bearing is absorbed by the honest
`azimuth_sigma_deg` (B2). The same-side discipline stays in `L1SweepLoop` (jammer DF)
unchanged.

**4. Lock confirmation = the link.** Lock is declared when the directional link carries
traffic, or when the refine sweep's RSSI clears the existing 6 dB prominence gate at the
GPS-prior bearing. The refine **reuses `L1AmplitudeSweepEstimator`**
(`packages/rfmesh-dsp/src/rfmesh_dsp/l1.py`) — peak fit, honest sigma, and the loud
`last_refusal_reason` come for free; no new DSP, no new golden surface beyond the
geodesic-bearing helper. On no-lock the SCANNER climbs an escalation ladder
(±20° → ±45° → ±90°); on timeout/exhaustion it reaches **FAILED** (loud `status_detail`)
and yields the servo to the jammer sweep.

**5. Servo time-sharing.** A node supervisor owns the connected `ServoDriver` and
alternates **between passes only** (never mid-sweep): RENDEZVOUS/REFINE → LINK_HOLD
(point at peer, hold `link_hold_s`, optional ±4° drift re-verify) → JAMMER_SWEEP (one
`L1SweepLoop` pass, emits a `BearingReport` over the unchanged path) → fast GPS-prior
re-acquire → LINK_HOLD. Exactly one loop issues `move` at any instant (the servo
protocol is single-outstanding-command, master/slave — `driver.py:6-7`). This requires
hoisting `servo.connect()`/`close()` out of `L1SweepLoop.run` up to `Node` so mode
flips do not re-enumerate USB-CDC.

**6. Trigger = operator/config toggle.** Peer `node_id` + `position` are supplied in
`RendezvousConfig` (CLI `--peer-id`/`--peer-lat`/`--peer-lon`). There is no
node-to-node channel and no fusion→node downlink today, and this ADR adds none. (A
fusion roster reflector for auto-trigger-on-registration is parked for a later ADR; it
would still need no contract change.)

**Bounded time (peer in-arc).** Fast path ~9 s typical to mutual lock (≤1 heartbeat
trigger + GPS-prior point + one ±20° refine + confirm). Worst case with the full
escalation ladder ~41 s, or immediate loud refusal when out-of-arc.

## Consequences

- **New node-layer surface:** `rendezvous.py` (config, `geodesic_initial_bearing_deg`,
  `role`, `expected_servo_angle`, `RendezvousLoop`) and a supervisor in `node.py`. The
  geodesic helper is implemented node-local (WD-1: no `rfmesh-fusion` sibling import).
- **Servo-lifecycle hoist** touches the tested connect/close seam in
  `tests/test_l1_sweep.py` (assertions move to a Node-level test).
- **Phase-C caveat (B6, honesty):** both-ends-directional mutual overlap and
  bracket-induced pattern offset were never bench-measured (the validated path is
  single-ended). The GPS-prior gate therefore carries generous slack, and the refine
  sweep is the field opportunity to finally measure that offset. This ADR makes no
  claim the link closes at a given range — the link itself is the only proof, surfaced
  honestly.
- **No new dependency, no contract change, star topology preserved.**
- When rendezvous is disabled (default `enabled=False`), node behaviour is byte-for-byte
  today's (sweep-only or heartbeat-only).
