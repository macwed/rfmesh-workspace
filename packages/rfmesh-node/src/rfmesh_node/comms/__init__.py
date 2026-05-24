"""rfmesh_node.comms -- node-layer orchestration for DSSS directional comms (ADR-025).

Owns the *deployment* / *orchestration* side of comms:

* ``CommsLinkConfig`` + ``PeerEntry`` -- peer roster and per-link
  parameters that are operator concerns, not frozen contracts. Mirror
  of ``RendezvousConfig`` (ADR-019): the same governance pattern --
  contracts stay narrow, node-layer absorbs deployment knobs.
* ``SlotSchedule`` -- TDD half-duplex slot arithmetic. Pure function
  of wall-clock time + assignment; no I/O, no async.
* ``CommsLoop`` -- the async loop that ties pointing
  (``rendezvous.expected_servo_angle`` + ``servo_motion.move_smooth``)
  to TDD slot timing to TX/RX of DSSS-spread frames through the
  configured ``Transmitter`` / ``Receiver`` pair. Reuses
  ``rfmesh_dsss`` for the physical layer.

Mutually exclusive with the DF pipeline in v1.3.0 (ADR-025 Decision B):
a node is either DF-mode or COMMS-mode at any one time, never both.
The node-runtime composition layer (`node.py`) picks one path from the
declared ``NodeConfig.capabilities``; ``CommsLoop`` never runs on the
same servo + SDR as ``L1SweepLoop``.
"""

from __future__ import annotations

from .comms_config import CommsLinkConfig, PeerEntry
from .tdd import SlotKind, SlotSchedule

__all__ = [
    "CommsLinkConfig",
    "PeerEntry",
    "SlotKind",
    "SlotSchedule",
]
