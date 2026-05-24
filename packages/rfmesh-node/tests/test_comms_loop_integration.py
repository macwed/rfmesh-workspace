"""End-to-end integration test for ``CommsLoop`` (ADR-025 Iter 4).

One ``LoopbackChannel`` shared by a ``SyntheticTransmitter`` and a
``LoopbackReceiver``. Two ``CommsLoop`` instances would normally run
on the two sides; this test runs ONE loop on the TX side, drains the
outbox into the channel directly via ``_tx_slot`` / ``_rx_slot``
calls (avoiding the wall-clock wait for the next TDD slot) and
asserts the decoded payload pops out on the RX side.

The full async ``run()`` path is exercised by a separate slot-aware
test that drives ``time`` manually -- here we just prove the wiring
works: encode -> spread -> TX -> channel -> RX -> despread -> decode.

Lives in ``rfmesh-node/tests`` and uses ``rfmesh-sdr`` as a
test-only dependency picked up transitively from the workspace
venv (mirror of the ``rfmesh-dsss`` E2E test pattern).
"""

from __future__ import annotations

import asyncio

import pytest
from rfmesh_contracts import CommsConfig, GeodeticPosition
from rfmesh_dsss.framing import FrameContents
from rfmesh_node.comms import CommsLinkConfig, PeerEntry
from rfmesh_node.comms.comms_loop import CommsLoop
from rfmesh_node.comms.tdd import SlotKind, SlotSchedule
from rfmesh_sdr.simulator import (
    LoopbackChannel,
    LoopbackReceiver,
    SyntheticTransmitter,
)

_SELF_POS = GeodeticPosition(lat_deg=50.330, lon_deg=5.000, hae_m=200.0, sigma_m=5.0)
_PEER_POS = GeodeticPosition(lat_deg=50.331, lon_deg=5.005, hae_m=200.0, sigma_m=5.0)


def _make_comms_config() -> CommsConfig:
    return CommsConfig(
        carrier_freq_hz=868e6,
        chip_rate_hz=10e6,
        lfsr_taps=(10, 3),
        lfsr_seed=1,
        tdd_slot_ms=200.0,
        tdd_guard_ms=30.0,
        frame_payload_max_bytes=64,
    )


def _make_link_config(peer_id: str = "node-peer", role: str = "tx_first") -> CommsLinkConfig:
    return CommsLinkConfig(
        peer=PeerEntry(node_id=peer_id, position=_PEER_POS),
        link_role=role,  # type: ignore[arg-type]
    )


def _make_loop(
    channel: LoopbackChannel,
    *,
    role: str = "tx_first",
) -> CommsLoop:
    tx = SyntheticTransmitter(channel)
    rx = LoopbackReceiver(channel)
    return CommsLoop(
        self_node_id="node-A",
        self_position=_SELF_POS,
        boresight_heading_deg=0.0,
        comms_link=_make_link_config(role=role),
        comms_config=_make_comms_config(),
        transmitter=tx,
        receiver=rx,
        servo=None,  # simulator: skip pointing
    )


@pytest.mark.asyncio
async def test_acquire_initialises_schedule() -> None:
    """``acquire()`` (no servo) sets up the TDD schedule + link_up=True."""
    channel = LoopbackChannel(sample_rate_hz=10e6)
    loop = _make_loop(channel)
    await loop.acquire()
    assert loop.stats.link_up is True
    assert loop._schedule is not None
    assert loop._schedule.role == "tx_first"


@pytest.mark.asyncio
async def test_tx_slot_drains_outbox_into_channel() -> None:
    """A queued payload is encoded + spread + written to the channel on _tx_slot."""
    channel = LoopbackChannel(sample_rate_hz=10e6)
    loop = _make_loop(channel)
    await loop.acquire()
    loop.queue_outbound(b"hello")
    loop.transmitter.open()
    try:
        await loop._tx_slot()
    finally:
        loop.transmitter.close()
    assert loop.stats.frames_sent == 1
    # Channel should now have one frame's worth of chips buffered.
    expected_chips = loop._chips_per_frame(loop.comms_config.frame_payload_max_bytes)
    assert channel.available() == expected_chips


@pytest.mark.asyncio
async def test_rx_slot_decodes_frame_into_inbox_callback() -> None:
    """A frame written to the channel comes out on the RX side as FrameContents."""
    channel = LoopbackChannel(sample_rate_hz=10e6)
    received: list[FrameContents] = []

    async def capture(contents: FrameContents) -> None:
        received.append(contents)

    loop = _make_loop(channel)
    loop.on_received = capture
    await loop.acquire()
    # Write a frame via the transmitter (TX slot path).
    loop.queue_outbound(b"hello-iter-4")
    loop.transmitter.open()
    loop.receiver.open()
    try:
        await loop._tx_slot()
        await loop._rx_slot()
    finally:
        loop.transmitter.close()
        loop.receiver.close()
    assert len(received) == 1
    assert CommsLoop.strip_padding(received[0].payload) == b"hello-iter-4"
    assert loop.stats.frames_received == 1
    assert loop.stats.frames_dropped == 0


@pytest.mark.asyncio
async def test_rx_slot_drops_corrupted_frame_loudly() -> None:
    """A frame written then corrupted in-channel triggers on_frame_dropped (B3)."""
    channel = LoopbackChannel(sample_rate_hz=10e6, chip_snr_db=-40.0, rng_seed=1)
    drops: list[str] = []

    async def on_drop(reason: str) -> None:
        drops.append(reason)

    loop = _make_loop(channel)
    loop.on_frame_dropped = on_drop
    await loop.acquire()
    loop.queue_outbound(b"will-be-corrupted")
    loop.transmitter.open()
    loop.receiver.open()
    try:
        await loop._tx_slot()
        await loop._rx_slot()
    finally:
        loop.transmitter.close()
        loop.receiver.close()
    # At -40 dB chip SNR the CRC is overwhelmingly likely to fail.
    assert loop.stats.frames_dropped == 1
    assert len(drops) == 1


@pytest.mark.asyncio
async def test_rx_slot_no_data_returns_quietly() -> None:
    """RX slot with an empty channel does not raise -- next slot will retry."""
    channel = LoopbackChannel(sample_rate_hz=10e6)
    loop = _make_loop(channel)
    await loop.acquire()
    loop.transmitter.open()
    loop.receiver.open()
    try:
        # No outbox queued, no TX slot run -- channel buffer is empty.
        await loop._rx_slot()
    finally:
        loop.transmitter.close()
        loop.receiver.close()
    assert loop.stats.frames_received == 0
    assert loop.stats.frames_dropped == 0


@pytest.mark.asyncio
async def test_queue_outbound_rejects_oversize_payload() -> None:
    """Payload larger than ``frame_payload_max_bytes`` raises (B3)."""
    loop = _make_loop(LoopbackChannel(sample_rate_hz=10e6))
    with pytest.raises(ValueError, match="exceeds frame_payload_max_bytes"):
        loop.queue_outbound(b"x" * 100)


@pytest.mark.asyncio
async def test_run_returns_when_stop_set() -> None:
    """``run()`` exits cleanly when the supervisor sets ``stop``."""
    channel = LoopbackChannel(sample_rate_hz=10e6)
    loop = _make_loop(channel)
    await loop.acquire()
    stop = asyncio.Event()
    # Fire stop immediately; the loop should exit on the first sleep tick.
    stop.set()
    await asyncio.wait_for(loop.run(stop), timeout=2.0)
    assert loop.stats.link_up is False


def test_slot_schedule_alignment_between_peers() -> None:
    """Two SlotSchedules with complementary roles + same epoch are TX/RX phased."""
    epoch = 2_000_000_000
    a = SlotSchedule(slot_ms=100.0, guard_ms=10.0, epoch_unix_ns=epoch, role="tx_first")
    b = SlotSchedule(slot_ms=100.0, guard_ms=10.0, epoch_unix_ns=epoch, role="rx_first")
    # Mid slot 0: A says TX, B says RX.
    t = epoch + int(50e6)
    assert a.current_slot(t) == SlotKind.TX
    assert b.current_slot(t) == SlotKind.RX
    # Mid slot 1: A says RX, B says TX.
    t = epoch + int(160e6)
    assert a.current_slot(t) == SlotKind.RX
    assert b.current_slot(t) == SlotKind.TX
