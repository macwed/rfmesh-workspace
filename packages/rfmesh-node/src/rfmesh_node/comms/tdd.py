"""TDD half-duplex slot scheduler for the directional-comms loop.

A node pair shares one carrier on the same Yagi link. Half-duplex
TDD partitions wall-clock time into alternating ``TX`` and ``RX``
slots separated by short ``GUARD`` intervals. The two sides agree on
the schedule out-of-band (the YAML configuration on each side picks
``link_role = tx_first | rx_first``); ``SlotSchedule.current_slot``
returns the slot category the local side should drive *right now*
given UTC wall-clock time.

DESIGN

* Pure function of ``CommsConfig.tdd_slot_ms`` /
  ``tdd_guard_ms`` + ``epoch_unix_ns`` + ``role``. No I/O, no async,
  no shared mutable state. Tested with hand-computed timings.
* Loose synchronisation (~10 ms NTP) is absorbed by ``tdd_guard_ms``.
  ``CommsConfig._spreading_consistency`` does not validate guard
  width against NTP skew -- the operator picks a guard wider than
  the worst-case skew on the deployment network, and a frame that
  straddles a slot boundary is dropped by the framing-layer CRC
  (loud failure, B3).
* Slot indices are 0-indexed from ``epoch_unix_ns``. The ``role``
  determines which parity (even vs odd) is TX on the local side.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class SlotKind(StrEnum):
    """Three classes of TDD slot: TX, RX, or guard interval."""

    TX = "tx"
    RX = "rx"
    GUARD = "guard"


@dataclass(frozen=True)
class SlotSchedule:
    """TDD slot arithmetic for one node's role.

    Construct once per ``CommsLoop`` lifetime from frozen
    ``CommsConfig`` values; query ``current_slot(t_unix_ns)`` from
    the loop to decide what to do this tick.

    Fields
    ------
    slot_ms:
        Width of one TX or RX slot, milliseconds. Sourced from
        ``CommsConfig.tdd_slot_ms``.
    guard_ms:
        Width of the guard interval between adjacent TX and RX
        slots, milliseconds. Sourced from
        ``CommsConfig.tdd_guard_ms``. The cycle is
        ``slot + guard + slot + guard``.
    epoch_unix_ns:
        Wall-clock instant the schedule's slot-0 begins. Pinned at
        ``CommsLoop`` construction (typically: nearest second
        boundary after acquire-complete) and never moved afterwards
        -- both peers compute the same schedule offline from the
        same epoch, so their slots align.
    role:
        ``"tx_first"`` means slot 0 (even) is TX on this side;
        ``"rx_first"`` means slot 0 is RX. The two peers MUST
        configure complementary roles.
    """

    slot_ms: float
    guard_ms: float
    epoch_unix_ns: int
    role: Literal["tx_first", "rx_first"]

    def __post_init__(self) -> None:
        if self.slot_ms <= 0.0:
            msg = f"slot_ms must be > 0 (got {self.slot_ms})."
            raise ValueError(msg)
        if self.guard_ms < 0.0:
            msg = f"guard_ms must be >= 0 (got {self.guard_ms})."
            raise ValueError(msg)
        if self.epoch_unix_ns <= 0:
            msg = f"epoch_unix_ns must be > 0 (got {self.epoch_unix_ns})."
            raise ValueError(msg)

    @property
    def cycle_ms(self) -> float:
        """One full TX+guard+RX+guard cycle, milliseconds."""
        return 2.0 * (self.slot_ms + self.guard_ms)

    def current_slot(self, t_unix_ns: int) -> SlotKind:
        """Return the slot category at wall-clock instant ``t_unix_ns``.

        Negative-relative-time (``t < epoch``) returns ``GUARD`` --
        defensive, the loop will park during pre-epoch ticks rather
        than transmit early. The normal case (``t >= epoch``) walks
        a deterministic ``[slot, guard, slot, guard, ...]`` cycle.
        """
        if t_unix_ns < self.epoch_unix_ns:
            return SlotKind.GUARD
        offset_ms = (t_unix_ns - self.epoch_unix_ns) / 1_000_000.0
        position_in_cycle = offset_ms % self.cycle_ms
        first_slot_end = self.slot_ms
        first_guard_end = first_slot_end + self.guard_ms
        second_slot_end = first_guard_end + self.slot_ms
        # Beyond second_slot_end is the second guard (wraps into next cycle).
        if position_in_cycle < first_slot_end:
            return self._slot0_kind()
        if position_in_cycle < first_guard_end:
            return SlotKind.GUARD
        if position_in_cycle < second_slot_end:
            return self._slot1_kind()
        return SlotKind.GUARD

    def next_slot_boundary_ns(self, t_unix_ns: int) -> int:
        """Wall-clock ns at the *next* slot/guard transition after ``t_unix_ns``.

        Used by the comms loop to ``await asyncio.sleep`` until the
        next interesting tick rather than busy-polling. If
        ``t_unix_ns < epoch_unix_ns``, returns ``epoch_unix_ns``.
        """
        if t_unix_ns < self.epoch_unix_ns:
            return self.epoch_unix_ns
        offset_ms = (t_unix_ns - self.epoch_unix_ns) / 1_000_000.0
        position_in_cycle = offset_ms % self.cycle_ms
        boundaries = (
            self.slot_ms,
            self.slot_ms + self.guard_ms,
            2.0 * self.slot_ms + self.guard_ms,
            self.cycle_ms,
        )
        for b in boundaries:
            if position_in_cycle < b:
                delta_ms = b - position_in_cycle
                return t_unix_ns + int(delta_ms * 1_000_000.0)
        # Defensive -- mod operation guarantees position < cycle_ms.
        return t_unix_ns + int(self.cycle_ms * 1_000_000.0)

    def _slot0_kind(self) -> SlotKind:
        return SlotKind.TX if self.role == "tx_first" else SlotKind.RX

    def _slot1_kind(self) -> SlotKind:
        return SlotKind.RX if self.role == "tx_first" else SlotKind.TX
