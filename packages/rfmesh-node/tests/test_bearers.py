"""Tests for the three ``Bearer`` Protocol conformers + the envelope codec.

Covers:

* WifiBearer round-trip of BearingReport over UDP loopback.
* WifiBearer round-trip of NodeStatus.
* LoraBearer drops raw_pseudospectrum (bandwidth invariant).
* LoraBearer envelope size is within reasonable budget without pseudospectrum.
* BothBearer dedup: same (node_id, t_unix_ns) seen via both -> delivered once.
* BothBearer failover: LoRa unavailable -> Wi-Fi still ships.
* Envelope round-trip preserves Pydantic fields.
* Oversized message rejected by MAX_MESSAGE_BYTES guard.
* Malformed payload raises EnvelopeDecodeError.
* Unknown envelope type raises EnvelopeDecodeError.
"""

from __future__ import annotations

import time

import msgpack
import pytest
from conftest import find_free_udp_port, make_bearing_report, make_node_status
from rfmesh_contracts import BearingReport, Capability
from rfmesh_node import BothBearer, LoraBearer, WifiBearer
from rfmesh_node.bearer.envelope import (
    MAX_MESSAGE_BYTES,
    EnvelopeDecodeError,
    EnvelopeTooLargeError,
    decode_envelope,
    encode_envelope,
)

# ---------------------------------------------------------------------------
# WifiBearer
# ---------------------------------------------------------------------------


def _wait_for_message(bearer: WifiBearer, timeout_s: float = 1.0) -> tuple:
    """Poll bearer.receive() until a message arrives or timeout. Helper."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        out = bearer.receive()
        if out:
            return tuple(out)
        time.sleep(0.005)
    return ()


def test_wifi_bearer_roundtrip_bearing_report() -> None:
    """A BearingReport ships and arrives intact over UDP loopback."""
    port = find_free_udp_port()
    server = WifiBearer(f"udp://127.0.0.1:{port}", bind=True)
    try:
        client = WifiBearer(f"udp://127.0.0.1:{port}", bind=False)
        try:
            report = make_bearing_report(azimuth_deg=37.5, azimuth_sigma_deg=4.2)
            client.send_bearing(report)
            received = _wait_for_message(server)
            assert len(received) == 1
            got = received[0]
            assert isinstance(got, BearingReport)
            assert got.node_id == report.node_id
            assert got.azimuth_deg == pytest.approx(37.5)
            assert got.azimuth_sigma_deg == pytest.approx(4.2)
        finally:
            client.close()
    finally:
        server.close()


def test_wifi_bearer_roundtrip_node_status() -> None:
    """A NodeStatus heartbeat round-trips over UDP loopback."""
    port = find_free_udp_port()
    server = WifiBearer(f"udp://127.0.0.1:{port}", bind=True)
    try:
        client = WifiBearer(f"udp://127.0.0.1:{port}", bind=False)
        try:
            status = make_node_status(healthy=False)
            client.send_status(status)
            received = _wait_for_message(server)
            assert len(received) == 1
            got = received[0]
            assert got.node_id == status.node_id
            assert got.healthy is False
        finally:
            client.close()
    finally:
        server.close()


def test_wifi_bearer_rejects_non_udp_scheme() -> None:
    with pytest.raises(ValueError, match="udp://"):
        WifiBearer("tcp://127.0.0.1:9000")


# ---------------------------------------------------------------------------
# LoraBearer -- loopback mode + the pseudospectrum-drop invariant.
# ---------------------------------------------------------------------------


def test_lora_bearer_drops_raw_pseudospectrum() -> None:
    """LoRa MUST drop ``raw_pseudospectrum`` (bandwidth budget)."""
    bearer = LoraBearer("/dev/null", loopback=True)
    try:
        # A real-looking pseudospectrum payload (720 float32 = 2880 B).
        pseudospectrum = bytes(2880)
        report = make_bearing_report(
            method=Capability.L2_MUSIC,
            raw_pseudospectrum=pseudospectrum,
        )
        bearer.send_bearing(report)
        received = bearer.receive()
        assert len(received) == 1
        got = received[0]
        assert isinstance(got, BearingReport)
        # The bearing fields survive, the debug payload is stripped.
        assert got.azimuth_deg == pytest.approx(report.azimuth_deg)
        assert got.raw_pseudospectrum is None
    finally:
        bearer.close()


def test_lora_bearer_packet_within_reasonable_budget() -> None:
    """A pseudospectrum-less bearing envelope fits in a small frame.

    The 256-byte LoRa MTU is a future hardware constraint; we are not
    enforcing it as a hard cap yet because the on-air framing protocol
    is not finalised. But the post-strip envelope must be small enough
    that it could plausibly fit one LoRa packet -- < 1 KiB is the
    architect's spec implicit upper bound.
    """
    bearer = LoraBearer("/dev/null", loopback=True)
    try:
        report = make_bearing_report(azimuth_deg=42.0)
        bearer.send_bearing(report)
        # The bearer recorded the envelope size; check it.
        assert bearer.last_envelope_bytes < 1024
        # And the envelope cap is still in force.
        assert bearer.last_envelope_bytes < MAX_MESSAGE_BYTES
    finally:
        bearer.close()


def test_lora_bearer_send_after_close_raises() -> None:
    bearer = LoraBearer("/dev/null", loopback=True)
    bearer.close()
    with pytest.raises(RuntimeError):
        bearer.send_bearing(make_bearing_report())


def test_lora_bearer_real_serial_not_yet_implemented() -> None:
    """The real-hardware path raises loudly (B3: no silent fall-through)."""
    bearer = LoraBearer("/dev/null", loopback=False)
    with pytest.raises(NotImplementedError):
        bearer.send_bearing(make_bearing_report())


# ---------------------------------------------------------------------------
# BothBearer -- dedup + failover.
# ---------------------------------------------------------------------------


def test_both_bearer_dedups_by_node_id_and_t_unix_ns() -> None:
    """Same (node_id, t_unix_ns) seen via both halves -> delivered once."""
    port = find_free_udp_port()
    server_wifi = WifiBearer(f"udp://127.0.0.1:{port}", bind=True)
    try:
        client_wifi = WifiBearer(f"udp://127.0.0.1:{port}", bind=False)
        client_lora = LoraBearer("/dev/null", loopback=True)
        composite = BothBearer(wifi=client_wifi, lora=client_lora)
        try:
            report = make_bearing_report(t_unix_ns=1_234_567_890_000_000_000)
            composite.send_bearing(report)
            # Drain the Wi-Fi side into the loopback wifi server.
            _wait_for_message(server_wifi)
            # Now build a server-side BothBearer that wraps both halves
            # and feed each side with a copy of the message. We simulate
            # the bearer receive-side using the existing LoRa loopback
            # for the LoRa half and the server_wifi for the Wi-Fi half.
            server_lora = LoraBearer("/dev/null", loopback=True)
            try:
                server_lora.send_bearing(report)  # echoes into its inbox
            except NotImplementedError:
                pytest.fail("LoRa loopback should not raise")
            server_both = BothBearer(wifi=server_wifi, lora=server_lora)
            try:
                # First call: should see one of the two halves; the other
                # is deduped.
                got_first = list(server_both.receive())
                # The bearer may have drained the Wi-Fi side during the
                # earlier _wait_for_message; we accept either path as
                # long as exactly one delivery occurs across all paths.
                total = len(got_first)
                assert total <= 2
                # Now manually push the same logical message via the
                # LoRa loopback once more to assert dedup over a second
                # call:
                server_lora.send_bearing(report)
                got_second = server_both.receive()
                # Combined across the two receive() calls we must have
                # exactly one BearingReport for this (node_id,
                # t_unix_ns).
                all_msgs = list(got_first) + list(got_second)
                bearings = [m for m in all_msgs if isinstance(m, BearingReport)]
                same_key = [
                    b
                    for b in bearings
                    if b.node_id == report.node_id and b.t_unix_ns == report.t_unix_ns
                ]
                assert len(same_key) == 1
            finally:
                server_both.close()
        finally:
            composite.close()
    finally:
        server_wifi.close()


def test_both_bearer_falls_through_when_lora_unavailable() -> None:
    """When LoRa is the real-hardware path (NotImplemented), Wi-Fi still ships."""
    port = find_free_udp_port()
    server = WifiBearer(f"udp://127.0.0.1:{port}", bind=True)
    try:
        wifi = WifiBearer(f"udp://127.0.0.1:{port}", bind=False)
        lora_unavailable = LoraBearer("/dev/null", loopback=False)
        composite = BothBearer(wifi=wifi, lora=lora_unavailable)
        try:
            report = make_bearing_report()
            composite.send_bearing(report)  # must not raise
            received = _wait_for_message(server)
            assert len(received) == 1
            assert received[0].node_id == report.node_id
        finally:
            composite.close()
    finally:
        server.close()


def test_both_bearer_lora_down_surfaces_via_health_summary() -> None:
    """LoRa NotImplementedError is observable -- health_summary() returns canonical string.

    Regression test for the architect-council BLOCK on commit f8b2a48
    (B3: BothBearer must not silently swallow LoRa-unavailable; the
    state must surface to NodeStatus.status_detail and on to the
    operator dashboard).
    """
    port = find_free_udp_port()
    server = WifiBearer(f"udp://127.0.0.1:{port}", bind=True)
    try:
        wifi = WifiBearer(f"udp://127.0.0.1:{port}", bind=False)
        lora_unavailable = LoraBearer("/dev/null", loopback=False)
        composite = BothBearer(wifi=wifi, lora=lora_unavailable)
        try:
            # Before any send: both bearers presumed available, summary empty.
            assert composite.is_lora_available() is True
            assert composite.health_summary() == ""

            # First send flips the LoRa flag (NotImplementedError caught
            # and logged once, not silently suppressed).
            composite.send_bearing(make_bearing_report())
            assert composite.is_lora_available() is False
            assert composite.health_summary() == "LoRa bearer down, Wi-Fi only"

            # Subsequent send keeps the summary; no LoRa call retried.
            composite.send_bearing(make_bearing_report())
            assert composite.is_lora_available() is False
            assert composite.health_summary() == "LoRa bearer down, Wi-Fi only"
        finally:
            composite.close()
    finally:
        server.close()


def test_both_bearer_status_send_also_surfaces_lora_down() -> None:
    """The status path flips the LoRa flag identically to the bearing path."""
    port = find_free_udp_port()
    server = WifiBearer(f"udp://127.0.0.1:{port}", bind=True)
    try:
        wifi = WifiBearer(f"udp://127.0.0.1:{port}", bind=False)
        lora_unavailable = LoraBearer("/dev/null", loopback=False)
        composite = BothBearer(wifi=wifi, lora=lora_unavailable)
        try:
            # Send a NodeStatus through the composite. The Wi-Fi half
            # ships; LoRa raises and is surfaced through health_summary(),
            # not swallowed.
            composite.send_status(make_node_status())
            assert composite.is_lora_available() is False
            assert composite.health_summary() == "LoRa bearer down, Wi-Fi only"
        finally:
            composite.close()
    finally:
        server.close()


# ---------------------------------------------------------------------------
# Envelope codec -- round-trip + the size / decode guards.
# ---------------------------------------------------------------------------


def test_envelope_roundtrip_preserves_bearing_fields() -> None:
    """encode -> decode preserves every BearingReport field."""
    report = make_bearing_report(
        azimuth_deg=212.5,
        azimuth_sigma_deg=1.2,
        method=Capability.L2_MUSIC,
    )
    raw = encode_envelope(report)
    got = decode_envelope(raw)
    assert isinstance(got, BearingReport)
    assert got.model_dump() == report.model_dump()


def test_envelope_too_large_raises() -> None:
    """A BearingReport whose pseudospectrum overflows MAX_MESSAGE_BYTES raises."""
    huge_pseudospectrum = bytes(MAX_MESSAGE_BYTES + 1)
    report = make_bearing_report(
        method=Capability.L2_MUSIC,
        raw_pseudospectrum=huge_pseudospectrum,
    )
    with pytest.raises(EnvelopeTooLargeError):
        encode_envelope(report)


def test_decode_envelope_rejects_malformed_prefix() -> None:
    """A truncated length prefix is rejected with a clear error."""
    with pytest.raises(EnvelopeDecodeError):
        decode_envelope(b"\x00\x00")  # only 2 bytes, prefix wants 4


def test_decode_envelope_rejects_unknown_type() -> None:
    """An envelope with an unknown ``type`` discriminator is rejected."""
    body = msgpack.packb({"type": "unknown_kind", "payload": {}}, use_bin_type=True)
    framed = len(body).to_bytes(4, "big") + body
    with pytest.raises(EnvelopeDecodeError, match="unknown envelope type"):
        decode_envelope(framed)
