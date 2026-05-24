"""Tests for the TDD slot scheduler (ADR-025 Iter 4)."""

from __future__ import annotations

import pytest
from rfmesh_node.comms.tdd import SlotKind, SlotSchedule


def _schedule(role: str = "tx_first", epoch_ns: int = 1_000_000_000) -> SlotSchedule:
    return SlotSchedule(
        slot_ms=100.0,
        guard_ms=20.0,
        epoch_unix_ns=epoch_ns,
        role=role,  # type: ignore[arg-type]
    )


def test_cycle_ms_is_two_slots_plus_two_guards() -> None:
    s = _schedule()
    assert s.cycle_ms == pytest.approx(2 * (100.0 + 20.0))


def test_tx_first_role_slot0_is_tx() -> None:
    s = _schedule(role="tx_first")
    # 1 ms after epoch -> deep inside slot 0.
    assert s.current_slot(s.epoch_unix_ns + 1_000_000) == SlotKind.TX


def test_rx_first_role_slot0_is_rx() -> None:
    s = _schedule(role="rx_first")
    assert s.current_slot(s.epoch_unix_ns + 1_000_000) == SlotKind.RX


def test_slot_to_guard_transition() -> None:
    """At ``slot_ms`` exactly, the schedule enters the first guard."""
    s = _schedule(role="tx_first")
    # 100 ms after epoch -> first guard.
    t = s.epoch_unix_ns + int(100.0 * 1_000_000)
    assert s.current_slot(t) == SlotKind.GUARD


def test_guard_to_rx_transition_tx_first() -> None:
    """After ``slot_ms + guard_ms`` (tx_first), the second slot is RX."""
    s = _schedule(role="tx_first")
    t = s.epoch_unix_ns + int(120.0 * 1_000_000) + 1_000_000  # 1 ms into RX
    assert s.current_slot(t) == SlotKind.RX


def test_rx_to_second_guard_then_cycle_wraps() -> None:
    """Second guard then wraps back into slot 0 (TX for tx_first)."""
    s = _schedule(role="tx_first")
    # Total cycle = 240 ms. 240 ms + 1 ms = back in slot 0.
    t = s.epoch_unix_ns + int(241.0 * 1_000_000)
    assert s.current_slot(t) == SlotKind.TX


def test_pre_epoch_is_guard() -> None:
    """Defensive: ticks before the schedule's epoch return GUARD, not TX."""
    s = _schedule(epoch_ns=10_000_000_000)
    assert s.current_slot(s.epoch_unix_ns - 1_000_000) == SlotKind.GUARD


def test_next_slot_boundary_advances() -> None:
    """``next_slot_boundary_ns`` returns a strictly-future timestamp."""
    s = _schedule(role="tx_first")
    t = s.epoch_unix_ns + 1_000_000  # 1 ms past epoch
    nxt = s.next_slot_boundary_ns(t)
    assert nxt > t
    # The next boundary is at slot_ms after epoch -- 100 ms.
    assert nxt == s.epoch_unix_ns + int(100.0 * 1_000_000)


def test_next_slot_boundary_for_pre_epoch_is_epoch() -> None:
    s = _schedule(epoch_ns=10_000_000_000)
    assert s.next_slot_boundary_ns(s.epoch_unix_ns - 1_000_000) == s.epoch_unix_ns


def test_constructor_rejects_non_positive_slot() -> None:
    with pytest.raises(ValueError, match="slot_ms"):
        SlotSchedule(slot_ms=0.0, guard_ms=10.0, epoch_unix_ns=1, role="tx_first")


def test_constructor_rejects_negative_guard() -> None:
    with pytest.raises(ValueError, match="guard_ms"):
        SlotSchedule(slot_ms=100.0, guard_ms=-1.0, epoch_unix_ns=1, role="tx_first")


def test_constructor_rejects_non_positive_epoch() -> None:
    with pytest.raises(ValueError, match="epoch_unix_ns"):
        SlotSchedule(slot_ms=100.0, guard_ms=10.0, epoch_unix_ns=0, role="tx_first")


def test_zero_guard_is_legal() -> None:
    """Zero guard interval is unusual but legal (back-to-back slots)."""
    s = SlotSchedule(slot_ms=100.0, guard_ms=0.0, epoch_unix_ns=1, role="tx_first")
    # 50 ms in: slot 0 (TX).
    assert s.current_slot(1 + int(50e6)) == SlotKind.TX
    # 150 ms in: slot 1 (RX).
    assert s.current_slot(1 + int(150e6)) == SlotKind.RX
