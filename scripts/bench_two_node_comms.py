"""End-to-end DSSS comms demo: two synthetic nodes exchange messages.

Runs entirely in one Python process with two ``LoopbackChannel`` s
(one per direction) so a developer or jury member can see the full
ADR-025 pipeline -- encode_frame -> bpsk_modulate -> spread -> TX
into channel -> RX out of channel -> despread -> bpsk_demodulate
-> decode_frame -> printed message -- without any radio, servo, or
backend running.

Usage:

    uv run python scripts/bench_two_node_comms.py

Expected output:

    [node-A] received from node-B: 'node-B hello'
    [node-B] received from node-A: 'node-A hello'
    [node-A] received from node-B: 'reply 1'
    [node-B] received from node-A: 'reply 2'
    ...
    bench complete: A frames {sent=N, received=N}, B frames {sent=N, received=N}

The two CommsLoops use complementary TDD roles (A=tx_first,
B=rx_first) so the slot phases line up. The bench drives the
``_tx_slot`` / ``_rx_slot`` paths directly rather than waiting on
wall-clock slot boundaries -- a real two-process run uses the
``run()`` task that ``Node`` schedules, which sleeps to the next
slot boundary; the bench skips that for speed.

For the actual soldier-facing end-to-end (backend + frontend +
two nodes), run instead:

    # terminal 1
    uv run uvicorn both3_poc.app:app --reload
    # terminal 2
    uv run rfmesh-node --config configs/node-comms-A.yaml
    # terminal 3
    uv run rfmesh-node --config configs/node-comms-B.yaml
    # browser
    open http://localhost:8000/static/link.html

The bench script below is the simulator-only smoke test; the
backend + frontend + two-process flow is documented in
docs/runbook-demo.md (TODO -- separate doc deliverable).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Make the rfmesh-* packages importable from the repo root without
# needing `uv pip install -e .` in the bench environment.
_REPO_ROOT = Path(__file__).resolve().parent.parent
for pkg in (
    "packages/rfmesh-contracts/src",
    "packages/rfmesh-dsss/src",
    "packages/rfmesh-sdr/src",
    "packages/rfmesh-node/src",
):
    p = str(_REPO_ROOT / pkg)
    if p not in sys.path:
        sys.path.insert(0, p)

from rfmesh_contracts import CommsConfig, GeodeticPosition  # noqa: E402
from rfmesh_node.comms import CommsLinkConfig, PeerEntry  # noqa: E402
from rfmesh_node.comms.comms_loop import CommsLoop  # noqa: E402
from rfmesh_sdr.simulator import (  # noqa: E402
    LoopbackChannel,
    LoopbackReceiver,
    SyntheticTransmitter,
)


_A_POS = GeodeticPosition(lat_deg=50.330, lon_deg=5.000, hae_m=200.0, sigma_m=5.0)
_B_POS = GeodeticPosition(lat_deg=50.33050, lon_deg=5.005, hae_m=200.0, sigma_m=5.0)


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


def _make_loop(
    *,
    self_id: str,
    self_pos: GeodeticPosition,
    peer_id: str,
    peer_pos: GeodeticPosition,
    tx_channel: LoopbackChannel,
    rx_channel: LoopbackChannel,
    role: str,
    boresight_deg: float,
) -> CommsLoop:
    tx = SyntheticTransmitter(tx_channel)
    rx = LoopbackReceiver(rx_channel)
    link = CommsLinkConfig(
        peer=PeerEntry(node_id=peer_id, position=peer_pos),
        link_role=role,  # type: ignore[arg-type]
    )
    return CommsLoop(
        self_node_id=self_id,
        self_position=self_pos,
        boresight_heading_deg=boresight_deg,
        comms_link=link,
        comms_config=_make_comms_config(),
        transmitter=tx,
        receiver=rx,
        servo=None,
    )


async def _print_received(node_id: str, contents: object) -> None:
    """Inbox callback that decodes + prints with the sender / receiver labels."""
    peer = getattr(contents, "src_node_id", "?")
    payload = getattr(contents, "payload", b"")
    text = CommsLoop.strip_padding(payload).decode("utf-8", errors="replace")
    print(f"[{node_id}] received from {peer}: {text!r}")  # noqa: T201


async def _exchange_messages(n_rounds: int = 4) -> None:
    """Two CommsLoops swap messages through two unidirectional channels."""
    # Two simplex channels: A writes to ab, B reads from ab; B writes to ba, A reads from ba.
    ch_ab = LoopbackChannel(sample_rate_hz=10e6)
    ch_ba = LoopbackChannel(sample_rate_hz=10e6)

    loop_a = _make_loop(
        self_id="node-A",
        self_pos=_A_POS,
        peer_id="node-B",
        peer_pos=_B_POS,
        tx_channel=ch_ab,
        rx_channel=ch_ba,
        role="tx_first",
        boresight_deg=90.0,
    )
    loop_b = _make_loop(
        self_id="node-B",
        self_pos=_B_POS,
        peer_id="node-A",
        peer_pos=_A_POS,
        tx_channel=ch_ba,
        rx_channel=ch_ab,
        role="rx_first",
        boresight_deg=270.0,
    )

    loop_a.on_received = lambda c: _print_received("node-A", c)  # type: ignore[assignment]
    loop_b.on_received = lambda c: _print_received("node-B", c)  # type: ignore[assignment]

    await loop_a.acquire()
    await loop_b.acquire()
    loop_a.transmitter.open()
    loop_a.receiver.open()
    loop_b.transmitter.open()
    loop_b.receiver.open()

    # Seed each side with a hello.
    loop_a.queue_outbound(b"node-A hello")
    loop_b.queue_outbound(b"node-B hello")

    try:
        for i in range(n_rounds):
            # A transmits; B receives.
            await loop_a._tx_slot()  # noqa: SLF001 -- bench driving private API on purpose
            await loop_b._rx_slot()  # noqa: SLF001
            # B transmits; A receives.
            await loop_b._tx_slot()  # noqa: SLF001
            await loop_a._rx_slot()  # noqa: SLF001
            # Queue follow-up replies for the next round.
            if i + 1 < n_rounds:
                loop_a.queue_outbound(f"A reply {i + 1}".encode())
                loop_b.queue_outbound(f"B reply {i + 1}".encode())
    finally:
        loop_a.transmitter.close()
        loop_a.receiver.close()
        loop_b.transmitter.close()
        loop_b.receiver.close()

    print(  # noqa: T201
        f"bench complete: A frames {{sent={loop_a.stats.frames_sent}, "
        f"received={loop_a.stats.frames_received}}}, "
        f"B frames {{sent={loop_b.stats.frames_sent}, "
        f"received={loop_b.stats.frames_received}}}",
    )


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(_exchange_messages(n_rounds=4))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
