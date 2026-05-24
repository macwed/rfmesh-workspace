"""Node-layer comms configuration (peer roster + link policy).

Mirror of ``RendezvousConfig`` (ADR-019) on the comms side: the data
the *operator* needs to wire a peer link, expressed as a frozen
dataclass that the node-runtime composition layer consumes. The
*physical-layer* DSSS parameters (chip rate, spreading factor, LFSR
taps, TDD slot widths) live in ``rfmesh_contracts.config.CommsConfig``
-- those are cross-workstream and frozen. Per-deployment knobs that
do not need to cross a workstream boundary live here.

WHY THIS IS NOT A CONTRACT

* The peer roster differs per deployment and is not consumed by any
  other workstream (the fusion server does not know about peer
  links; the CoT publisher does not either). Pulling it into
  ``rfmesh-contracts`` would lock in a deployment shape we have not
  validated, for zero cross-workstream benefit. ADR-025 §"Why peer
  roster / routing / TDD timing stay node-layer".
* Same reasoning as RendezvousConfig; the precedent is established.

WHAT v1.3.0 SUPPORTS

* One peer per node, link mode = STAR_FIRST (one fixed side starts
  the TDD cycle in the TX slot). Multi-peer roster + dynamic
  scheduling is deferred to Iter 5 (multi-hop routing).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rfmesh_contracts import GeodeticPosition  # type: ignore[import-untyped, unused-ignore]

#: Per-link role at boot. ``tx_first`` opens the TDD cycle in the TX
#: slot; ``rx_first`` opens in the RX slot. Pair-mismatch (both
#: ``tx_first`` or both ``rx_first``) collides on-air -- the operator
#: picks complementary roles when authoring the YAML for each side.
LinkRole = Literal["tx_first", "rx_first"]


@dataclass(frozen=True)
class PeerEntry:
    """Identity + survey of one peer in the comms roster.

    ``node_id`` is the peer's stable identifier (matches the peer's
    ``NodeConfig.node_id``). ``position`` is the surveyed peer
    location (operator-set, same convention as ``NodeConfig.position``).
    ``boresight_heading_deg`` is the peer's own antenna heading --
    not strictly required by this side's pointing math, but carried
    so multi-peer scenarios can validate geometry symmetrically.
    """

    node_id: str
    position: GeodeticPosition
    boresight_heading_deg: float | None = None


@dataclass(frozen=True)
class CommsLinkConfig:
    """One directional-comms link's deployment policy. Node-layer (no contract change).

    Pairs an operator-authored peer survey with the TDD role this
    side opens with. Physical-layer parameters
    (chip rate, spreading factor, slot widths, payload bound) live
    on the frozen ``rfmesh_contracts.CommsConfig`` and are passed
    separately to the ``CommsLoop``.

    Fields
    ------
    peer:
        Peer roster entry. v1.3.0 supports one peer per CommsLoop;
        Iter 5 (multi-hop) extends to a tuple of peers.
    link_role:
        Which TDD half this side opens with. The two sides MUST be
        complementary (``tx_first`` <-> ``rx_first``); on-air
        collision is loud (CRC mismatch -> frame dropped at the
        peer) but the YAML author owns the choice.
    refine_half_arc_deg:
        Half-arc the servo sweeps around the GPS-prior bearing
        before locking onto the peer (mirror of
        ``RendezvousConfig.refine_half_arc_deg``). Wider arcs
        tolerate worse survey-of-record position; narrower arcs
        lock faster.
    settle_s:
        Servo settle time after pointing, before the comms loop
        starts the first TDD slot. Default 0.20 s (same as
        rendezvous).
    min_servo_deg / max_servo_deg:
        Reachable arc on this side. PeerOutOfArcError if the
        peer falls outside -- refuse, do not clamp (B3 mirror
        of rendezvous).
    link_hold_s:
        Maximum time we hold the link without a received frame
        before declaring the link down and re-acquiring. The
        operator sees the link-down event on the dashboard;
        the loop re-runs ``acquire``.
    initial_outbox_message:
        Optional payload sent on the first TX slot after acquire.
        Useful for liveness-checks / smoke tests; in production
        the operator sends from the UI.
    """

    peer: PeerEntry
    link_role: LinkRole = "tx_first"
    refine_half_arc_deg: float = 20.0
    settle_s: float = 0.20
    min_servo_deg: float = -90.0
    max_servo_deg: float = 90.0
    link_hold_s: float = 30.0
    initial_outbox_message: bytes | None = None
