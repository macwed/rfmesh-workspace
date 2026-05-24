"""Async ``CommsLoop`` -- the DSSS directional-comms orchestrator (ADR-025 Iter 4).

Wires together:

* **Pointing** -- reuses ``rendezvous.expected_servo_angle`` to compute
  the servo angle that points the Yagi at the peer (GPS-prior),
  ``servo_motion.move_smooth`` to move there. v1.3.0 does not
  re-acquire mid-link; the comms loop assumes the peer stays still.
  Re-acquisition is Iter 5+ (mobile peers).
* **TDD scheduling** -- ``SlotSchedule.current_slot`` decides whether
  this tick is a TX, RX, or guard window. The loop sleeps until the
  next slot boundary rather than busy-polling.
* **TX side** -- drains the operator's outbox queue; encodes the
  next message via ``rfmesh_dsss.encode_frame``, modulates / spreads
  it, and writes the chips through the configured ``Transmitter``.
  One message per TX slot in v1.3.0 (the slot is sized to fit one
  max-payload frame).
* **RX side** -- reads one slot's worth of chips through the
  configured ``Receiver``, despreads, demodulates, decodes via
  ``rfmesh_dsss.decode_frame``, and pushes the decoded
  ``FrameContents`` to the inbox callback (the backend uses this to
  fan out to the operator's UI via the existing WS channel).

LIFECYCLE

``open() -> acquire() -> run() -> close()``. ``run()`` is the
long-running coroutine the node-runtime supervisor awaits; it
returns when the supervisor sets the ``stop`` event.

NO SILENT FALLBACKS (B3)

* Transmitter / Receiver lifecycle violations propagate as
  ``TransmitterNotOpenError`` / ``ReceiverNotOpenError``.
* A frame whose CRC fails on decode is dropped (the caller sees a
  ``FrameDecodeError`` logged through ``on_frame_dropped`` and
  the link counter increments). No silently-zeroed payload reaches
  the inbox.
* If acquisition fails (peer out of arc, servo not calibrated),
  ``CommsLoop`` surfaces ``PeerOutOfArcError`` /
  ``RuntimeError`` to the supervisor.

NOT IN ITER 4

* Multi-peer fanout / routing -- Iter 5.
* Carrier-phase tracking across slots (block-mode estimator on each
  RX slot is sufficient for the loopback / hardware single-link case).
* Encryption / authentication of the payload -- payload bytes are
  opaque to the framing layer; an upper layer (operator console)
  adds these when needed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import numpy as np
from rfmesh_contracts import (  # type: ignore[import-untyped, unused-ignore]
    CommsConfig,
    GeodeticPosition,
    Receiver,
    Transmitter,
)
from rfmesh_dsss.framing import FrameContents, decode_frame, encode_frame
from rfmesh_dsss.modulation import bpsk_demodulate, bpsk_modulate
from rfmesh_dsss.pn_sequence import generate_m_sequence
from rfmesh_dsss.spreading import despread, spread

from rfmesh_node.rendezvous import (  # type: ignore[import-untyped, unused-ignore]
    expected_servo_angle,
)
from rfmesh_node.servo_motion import (  # type: ignore[import-untyped, unused-ignore]
    MotionConfig,
    move_smooth,
)

from .comms_config import CommsLinkConfig
from .tdd import SlotKind, SlotSchedule

logger = logging.getLogger(__name__)


#: Optional callback invoked on every decoded inbound frame. The
#: ``Node`` wires this to the command-channel's WS push so the
#: operator's UI receives the message in near-real-time.
InboxCallback = Callable[[FrameContents], Awaitable[None]]

#: Optional callback invoked when a frame fails to decode. Carries
#: the reason string so the dashboard can render "CRC mismatch" /
#: "sync-word lost" / ... rather than silently dropping the event.
DropCallback = Callable[[str], Awaitable[None]]

#: Optional callback for link-state changes (linked / unlinked).
#: Receives a small dict the backend can fan out verbatim as a WS
#: frame.
StatusCallback = Callable[[dict[str, object]], Awaitable[None]]


@dataclass
class CommsLoopStats:
    """Lightweight running counters for the dashboard.

    All counters are monotonic; the UI computes rates by diffing
    consecutive snapshots.
    """

    frames_sent: int = 0
    frames_received: int = 0
    frames_dropped: int = 0
    last_rx_t_unix_ns: int | None = None
    last_tx_t_unix_ns: int | None = None
    link_up: bool = False

    def snapshot(self) -> dict[str, object]:
        return {
            "frames_sent": self.frames_sent,
            "frames_received": self.frames_received,
            "frames_dropped": self.frames_dropped,
            "last_rx_t_unix_ns": self.last_rx_t_unix_ns,
            "last_tx_t_unix_ns": self.last_tx_t_unix_ns,
            "link_up": self.link_up,
        }


@dataclass
class CommsLoop:
    """The async comms orchestrator. Construct, ``acquire()``, then ``run()``.

    Parameters
    ----------
    self_node_id, self_position, boresight_heading_deg:
        Identity / survey of the local node. Used to compute the
        servo angle that points the antenna at the peer.
    comms_link:
        Operator-authored peer roster + link policy (this side).
    comms_config:
        Frozen physical-layer DSSS parameters (chip rate, spreading
        factor, LFSR taps + seed, TDD slot timings, max payload).
    transmitter, receiver:
        The single-channel TX / RX pair. In simulator mode these are
        ``SyntheticTransmitter`` + ``LoopbackReceiver`` from
        ``rfmesh_sdr.simulator`` sharing a ``LoopbackChannel``; on
        hardware they are the BladeRF / HackRF / Pluto adapters.
    servo:
        ``ServoDriver`` for the pan axis. ``None`` is legal for
        simulator-only tests that skip pointing (the loop logs a
        warning and proceeds straight to TDD).
    motion_config:
        Servo motion parameters. ``None`` selects ``MotionConfig()``
        defaults.
    on_received, on_frame_dropped, on_status:
        Optional async callbacks the node-runtime wires to the
        operator's command-channel WS.
    """

    self_node_id: str
    self_position: GeodeticPosition
    boresight_heading_deg: float
    comms_link: CommsLinkConfig
    comms_config: CommsConfig
    transmitter: Transmitter
    receiver: Receiver
    servo: object | None = None  # ServoDriver, kept generic for testability
    motion_config: MotionConfig | None = None
    on_received: InboxCallback | None = None
    on_frame_dropped: DropCallback | None = None
    on_status: StatusCallback | None = None

    _outbox: deque[bytes] = field(default_factory=deque)
    _schedule: SlotSchedule | None = None
    _stats: CommsLoopStats = field(default_factory=CommsLoopStats)
    _pn_cache: np.ndarray | None = None

    @property
    def stats(self) -> CommsLoopStats:
        """Read-only access to the running counters."""
        return self._stats

    def queue_outbound(self, payload: bytes) -> None:
        """Append a payload to the TX outbox.

        Thread-safe-enough for asyncio: the deque is consumed only
        from the same loop. The backend's ``SendCommsMessage``
        handler calls this; the next TX slot drains one message.
        """
        if len(payload) > self.comms_config.frame_payload_max_bytes:
            msg = (
                f"comms outbox: payload of {len(payload)} bytes exceeds "
                f"frame_payload_max_bytes={self.comms_config.frame_payload_max_bytes}."
            )
            raise ValueError(msg)
        self._outbox.append(payload)

    async def acquire(self) -> None:
        """Point the antenna at the peer; initialise the TDD schedule.

        Raises ``PeerOutOfArcError`` if the peer falls outside the
        configured servo arc -- caller refuses to start the loop
        (B3 -- no silent clamp).
        """
        target_angle = expected_servo_angle(
            self.self_position,
            self.comms_link.peer.position,
            self.boresight_heading_deg,
            min_servo_deg=self.comms_link.min_servo_deg,
            max_servo_deg=self.comms_link.max_servo_deg,
        )
        if self.servo is not None:
            motion = self.motion_config or MotionConfig()
            # current_deg=0.0 assumes the servo is parked at boresight.
            # The supervisor (node.py) is responsible for parking the
            # servo before handing control to the CommsLoop; if a
            # follow-up iter introduces mid-link re-acquisition the
            # comms loop will track current_deg internally.
            await move_smooth(
                self.servo,
                0,
                0.0,
                target_angle,
                motion,
            )
            await asyncio.sleep(self.comms_link.settle_s)
        else:
            logger.warning(
                "CommsLoop.acquire(): no servo configured -- skipping pointing "
                "(simulator-only mode).",
            )
        epoch_ns = time.time_ns()
        self._schedule = SlotSchedule(
            slot_ms=self.comms_config.tdd_slot_ms,
            guard_ms=self.comms_config.tdd_guard_ms,
            epoch_unix_ns=epoch_ns,
            role=self.comms_link.link_role,
        )
        if self.comms_link.initial_outbox_message is not None:
            self._outbox.append(self.comms_link.initial_outbox_message)
        self._stats.link_up = True
        await self._maybe_emit_status()

    async def run(self, stop: asyncio.Event) -> None:
        """Long-running coroutine; returns when ``stop`` is set.

        Each tick: query schedule, run one TX or RX slot, sleep until
        the next slot boundary. ``stop`` is checked between slots so
        an in-flight TX completes cleanly.
        """
        if self._schedule is None:
            msg = "CommsLoop.run() called before acquire()."
            raise RuntimeError(msg)
        self.transmitter.open()
        self.receiver.open()
        try:
            while not stop.is_set():
                now_ns = time.time_ns()
                slot = self._schedule.current_slot(now_ns)
                if slot is SlotKind.TX:
                    await self._tx_slot()
                elif slot is SlotKind.RX:
                    await self._rx_slot()
                next_ns = self._schedule.next_slot_boundary_ns(now_ns)
                sleep_s = max((next_ns - time.time_ns()) / 1e9, 0.0)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=sleep_s)
                    return  # stop fired during sleep
                except TimeoutError:
                    continue
        finally:
            self.transmitter.close()
            self.receiver.close()
            self._stats.link_up = False
            await self._maybe_emit_status()

    async def _tx_slot(self) -> None:
        """One TX slot: send up to one outbox message; otherwise skip."""
        if not self._outbox:
            return
        payload = self._outbox.popleft()
        chips = self._encode_and_spread(payload)
        n_written = self.transmitter.write(chips)
        if n_written != chips.size:
            msg = (
                f"CommsLoop._tx_slot: transmitter wrote {n_written} of "
                f"{chips.size} chips -- short writes are not silently retried."
            )
            raise RuntimeError(msg)
        self._stats.frames_sent += 1
        self._stats.last_tx_t_unix_ns = time.time_ns()
        await self._maybe_emit_status()

    async def _rx_slot(self) -> None:
        """One RX slot: read one frame's worth of chips; decode or drop loudly."""
        chips_per_frame = self._chips_per_frame(self.comms_config.frame_payload_max_bytes)
        # If the receiver's buffer doesn't have enough chips yet (e.g.
        # the peer hasn't transmitted yet on this slot), skip rather
        # than block the loop. The next RX slot will try again.
        try:
            chips = self.receiver.read(chips_per_frame)
        except BufferError:
            return
        except Exception:
            self._stats.frames_dropped += 1
            if self.on_frame_dropped is not None:
                await self.on_frame_dropped("receiver read failed")
            return
        try:
            contents = self._despread_and_decode(chips)
        except Exception as exc:
            self._stats.frames_dropped += 1
            if self.on_frame_dropped is not None:
                await self.on_frame_dropped(str(exc))
            return
        self._stats.frames_received += 1
        self._stats.last_rx_t_unix_ns = time.time_ns()
        if self.on_received is not None:
            await self.on_received(contents)
        await self._maybe_emit_status()

    def _pn(self) -> np.ndarray:
        """Lazy-cached PN sequence derived from comms_config."""
        if self._pn_cache is None:
            register_length = max(self.comms_config.lfsr_taps)
            self._pn_cache = generate_m_sequence(
                register_length,
                tuple(self.comms_config.lfsr_taps),
                self.comms_config.lfsr_seed,
            )
        return self._pn_cache

    def _encode_and_spread(self, payload: bytes) -> np.ndarray:
        """``encode_frame`` -> ``bpsk_modulate`` -> ``spread``. Pure DSSS plumbing.

        Pads ``payload`` to ``frame_payload_max_bytes`` with NULs so
        every TX writes the same chip count -- the RX side reads a
        fixed max-size chunk per slot and doesn't have to discover the
        actual frame length before knowing how many chips to ask for.
        The decoded ``payload`` on the RX side contains the padding;
        consumers strip trailing NULs (or use the application layer's
        own length convention). Acceptable tradeoff for v1.3.0 MVP --
        Iter 5 multi-hop will introduce a length-prefixed read that
        avoids the per-slot padding overhead.
        """
        max_bytes = self.comms_config.frame_payload_max_bytes
        padded = payload + b"\x00" * (max_bytes - len(payload))
        bits = encode_frame(
            src_node_id=_hash_node_id(self.self_node_id),
            dst_node_id=_hash_node_id(self.comms_link.peer.node_id),
            sequence_no=self._stats.frames_sent & 0xFF,
            payload=padded,
            payload_max_bytes=max_bytes,
        )
        symbols = bpsk_modulate(bits)
        return spread(symbols, self._pn())

    @staticmethod
    def strip_padding(payload: bytes) -> bytes:
        """Strip trailing NUL bytes from a received payload.

        Mirror of ``_encode_and_spread``'s NUL-padding policy. Public
        because the backend's ``comms_rx`` WS push uses it to surface
        the operator's original message text on the dashboard.
        """
        return payload.rstrip(b"\x00")

    def _despread_and_decode(self, chips: np.ndarray) -> FrameContents:
        symbols = despread(chips, self._pn())
        bits = bpsk_demodulate(symbols)
        return decode_frame(bits, payload_max_bytes=self.comms_config.frame_payload_max_bytes)

    def _chips_per_frame(self, payload_max_bytes: int) -> int:
        """How many chips the receiver should read per RX slot."""
        from rfmesh_dsss.framing import FIXED_OVERHEAD_BITS  # noqa: PLC0415
        return (FIXED_OVERHEAD_BITS + 8 * payload_max_bytes) * self._pn().size

    async def _maybe_emit_status(self) -> None:
        if self.on_status is None:
            return
        await self.on_status(self._stats.snapshot())


def _hash_node_id(node_id: str) -> int:
    """Stable 1-byte hash of a node-id string for the frame header.

    The frame header uses a single byte per src / dst (256 nodes
    max -- enough for v1.3.0). The mesh-wide ``NodeConfig.node_id``
    is a free-form string; this routine hashes it into ``[0, 255]``
    deterministically so the two sides see the same byte. Collisions
    are operator-visible (two distinct node-ids hashing to the same
    byte would cross-talk); for the BoTH3 demo with O(10) nodes the
    collision probability is negligible. Iter 5 multi-hop introduces
    an explicit node-id table.
    """
    return sum(node_id.encode("utf-8")) & 0xFF
