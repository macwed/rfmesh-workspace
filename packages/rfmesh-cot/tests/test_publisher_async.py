"""Integration tests for ``PyTAKCotPublisher`` against a loopback TCP server.

Tests:

* ``test_publish_ships_bytes``              -- end-to-end: publish() ->
                                                bytes hit the wire.
* ``test_publish_node_status_ships``        -- heartbeat ships too.
* ``test_async_context_manager``            -- ``async with`` works.
* ``test_closed_publisher_raises``          -- post-close publish() ->
                                                CotTransportError.
* ``test_invalid_endpoint_url_raises``      -- bad URL is rejected at
                                                construction.
* ``test_transport_failure_surfaces``       -- broken connection makes
                                                next publish() raise.
"""

from __future__ import annotations

import asyncio
import warnings
from xml.etree import ElementTree as ET

import pytest

# pytak emits warnings on import about missing optional crypto support;
# silence them in tests so the output stays readable.
warnings.filterwarnings(
    "ignore",
    message=".*cryptography.*",
    category=UserWarning,
)

# Fixtures (``make_fix_event``, ``loopback_tcp_server``) come from
# conftest.py via pytest auto-discovery. Their concrete types live
# there; here we alias to ``Any`` because tests/ has no __init__.py
# (AGENTS.md §3.5).
from typing import Any  # noqa: E402

from rfmesh_cot import CotTransportError, PyTAKCotPublisher  # noqa: E402

FixEventFactory = Any
LoopbackServer = Any


async def _drain_tx_queue(publisher: PyTAKCotPublisher) -> None:
    """Wait for the publisher's TX queue to empty.

    The publisher's ``publish()`` is sync; the actual write happens in
    the TX task. Tests need to wait for the queue to drain before
    inspecting received bytes.
    """
    # Access the private queue is a deliberate test affordance --
    # we are testing the publisher's internal scheduling.
    for _ in range(100):
        q = publisher._tx_queue
        if q is None or q.empty():
            # Give one event-loop tick for drain() to complete.
            await asyncio.sleep(0.01)
            return
        await asyncio.sleep(0.01)
    msg = "TX queue did not drain within 1s"
    raise AssertionError(msg)


async def test_publish_ships_bytes(
    loopback_tcp_server: LoopbackServer,
    make_fix_event: FixEventFactory,
) -> None:
    """End-to-end: publish() encodes a FixEvent and ships it down a TCP socket.

    Verifies the wire bytes parse as the expected CoT <event>.
    """
    fix = make_fix_event()
    async with PyTAKCotPublisher(loopback_tcp_server.url) as publisher:
        publisher.publish(fix)
        await _drain_tx_queue(publisher)
    # Server has captured the encoded blob; it should parse as XML.
    received = bytes(loopback_tcp_server.received)
    assert len(received) > 0
    root = ET.fromstring(received)
    assert root.tag == "event"
    assert "rfmesh.fix." in root.attrib["uid"]


async def test_publish_node_status_ships(
    loopback_tcp_server: LoopbackServer,
) -> None:
    """``publish_node_status`` ships a friendly-marker CoT blob."""
    from rfmesh_contracts import Capability, GeodeticPosition, NodeStatus

    status = NodeStatus(
        node_id="node-rtl-01",
        t_unix_ns=1_700_000_000_000_000_000,
        position=GeodeticPosition(lat_deg=50.0, lon_deg=5.0, hae_m=0.0, sigma_m=3.0),
        active_capabilities=(Capability.L1_RSSI,),
        gnss_locked=True,
        healthy=True,
        status_detail="",
    )
    async with PyTAKCotPublisher(loopback_tcp_server.url) as publisher:
        publisher.publish_node_status(status)
        await _drain_tx_queue(publisher)
    received = bytes(loopback_tcp_server.received)
    assert len(received) > 0
    root = ET.fromstring(received)
    assert root.attrib["uid"] == "rfmesh.node.node-rtl-01"
    assert root.attrib["type"].startswith("a-f-")


async def test_async_context_manager(
    loopback_tcp_server: LoopbackServer,
    make_fix_event: FixEventFactory,
) -> None:
    """``async with`` opens the transport and tears it down on exit."""
    fix = make_fix_event()
    publisher = PyTAKCotPublisher(loopback_tcp_server.url)
    async with publisher:
        publisher.publish(fix)
        await _drain_tx_queue(publisher)
        # While inside the context, writer is open.
        assert publisher._writer is not None
    # After exit, the publisher is closed.
    assert publisher._closed is True


async def test_closed_publisher_raises(
    loopback_tcp_server: LoopbackServer,
    make_fix_event: FixEventFactory,
) -> None:
    """A closed publisher rejects further publish() with CotTransportError."""
    fix = make_fix_event()
    publisher = PyTAKCotPublisher(loopback_tcp_server.url)
    async with publisher:
        publisher.publish(fix)
        await _drain_tx_queue(publisher)
    # publisher.__aexit__ set _closed=True.
    with pytest.raises(CotTransportError, match="closed"):
        publisher.publish(fix)


def test_invalid_endpoint_url_raises() -> None:
    """Constructor rejects URLs without a scheme."""
    with pytest.raises(ValueError, match="PyTAK-style URL"):
        PyTAKCotPublisher("not_a_url")


async def test_transport_failure_surfaces(
    loopback_tcp_server: LoopbackServer,
    make_fix_event: FixEventFactory,
) -> None:
    """A broken pipe makes the *next* publish() raise CotTransportError.

    No silent retry. The publisher records the transport error and
    surfaces it on the next public call (Invariant B3).
    """
    fix = make_fix_event()
    async with PyTAKCotPublisher(loopback_tcp_server.url) as publisher:
        publisher.publish(fix)
        await _drain_tx_queue(publisher)
        # Force-close the server side -- the next write will fail.
        await loopback_tcp_server.close_all_clients()
        # First publish after the disconnect may succeed (buffered);
        # we keep enqueuing until the TX loop notices the broken pipe.
        # Cap at ~20 attempts so a true regression (no error surfaces)
        # still fails the test.
        raised = False
        for _ in range(20):
            try:
                publisher.publish(fix)
            except CotTransportError:
                raised = True
                break
            await asyncio.sleep(0.05)
        if not raised:
            # The TX loop should have parked the error by now; one
            # last publish triggers the check.
            with pytest.raises(CotTransportError):
                publisher.publish(fix)


async def test_repeated_close_is_idempotent(
    loopback_tcp_server: LoopbackServer,
) -> None:
    """Calling ``__aexit__`` twice is safe (clean teardown paths use it)."""
    publisher = PyTAKCotPublisher(loopback_tcp_server.url)
    async with publisher:
        pass
    # Already closed; second close-via-aexit-equivalent is a no-op.
    await publisher.__aexit__(None, None, None)
    assert publisher._closed is True
