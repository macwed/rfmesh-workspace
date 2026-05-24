"""Unit tests for ``both3_poc.ws``.

Covers:

* Node WS connect / disconnect updates the registry.
* Reconnect overwrites prior registry entry (last writer wins).
* Manual-steer command pushes to the registered node WS.
* Manual-steer command for an unregistered node returns 503.
* Schema-invalid manual-steer payload returns 422.
* ``command_broadcast`` reaches every registered node.
* UI subscriber receives a ``bearing`` payload pushed via
  ``push_to_ui_subscribers``.

Hardware-free and backend-free: the tests use FastAPI ``TestClient``
which spins up a transient ASGI test server.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from both3_poc.ws import init_registries, push_to_ui_subscribers
from both3_poc.ws import router as ws_router
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app() -> FastAPI:
    """Minimal FastAPI app with just the WS router + registries."""
    a = FastAPI()
    a.include_router(ws_router)
    init_registries(a)
    return a


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Node WS lifecycle
# ---------------------------------------------------------------------------


def test_node_ws_register_on_connect_unregister_on_disconnect(
    client: TestClient, app: FastAPI
) -> None:
    assert len(app.state.node_ws_registry) == 0
    with client.websocket_connect("/ws/node/node-a"):
        assert "node-a" in app.state.node_ws_registry.node_ids()
        assert len(app.state.node_ws_registry) == 1
    # Context manager closes the WS -> handler's finally runs.
    assert len(app.state.node_ws_registry) == 0


def test_node_ws_reconnect_overwrites_entry(client: TestClient, app: FastAPI) -> None:
    with client.websocket_connect("/ws/node/node-a"):
        first_ws = app.state.node_ws_registry.get("node-a")
        # Note: the test client doesn't actually let us hold two
        # simultaneous WS to the same path easily, so we open / close /
        # reopen. The "last writer wins" guarantee is what's tested.
    with client.websocket_connect("/ws/node/node-a"):
        second_ws = app.state.node_ws_registry.get("node-a")
        assert second_ws is not None
        assert second_ws is not first_ws


# ---------------------------------------------------------------------------
# Manual-steer command routing
# ---------------------------------------------------------------------------


def test_post_command_404_when_node_offline(client: TestClient) -> None:
    payload: dict[str, Any] = {
        "kind": "manual_steer",
        "axis": 0,
        "target_angle_deg": 45.0,
    }
    resp = client.post("/command/no-such-node", json=payload)
    assert resp.status_code == 503
    body = resp.json()
    assert "not connected" in body["detail"]


def test_post_command_422_on_bad_payload(client: TestClient) -> None:
    # Missing required field "target_angle_deg".
    resp = client.post("/command/node-a", json={"kind": "manual_steer", "axis": 0})
    assert resp.status_code == 422


def test_post_command_422_on_unknown_kind(client: TestClient) -> None:
    resp = client.post(
        "/command/node-a",
        json={"kind": "wibble", "axis": 0, "target_angle_deg": 0.0},
    )
    assert resp.status_code == 422


def test_post_command_pushes_to_registered_node(client: TestClient) -> None:
    """The command JSON is sent down the node's WS as a text frame."""
    payload = {
        "kind": "manual_steer",
        "axis": 0,
        "target_angle_deg": 12.5,
        "timeout_s": 5.0,
        "requestor_id": "ui-test",
    }
    with client.websocket_connect("/ws/node/node-a") as node_ws:
        resp = client.post("/command/node-a", json=payload)
        assert resp.status_code == 200
        # Read the frame pushed down the node socket.
        received_text = node_ws.receive_text()
        received = json.loads(received_text)
        assert received["kind"] == "manual_steer"
        assert received["target_angle_deg"] == 12.5
        assert received["requestor_id"] == "ui-test"


# ---------------------------------------------------------------------------
# command_broadcast
# ---------------------------------------------------------------------------


def test_command_broadcast_reaches_all_registered_nodes(client: TestClient) -> None:
    with (
        client.websocket_connect("/ws/node/node-a") as ws_a,
        client.websocket_connect("/ws/node/node-b") as ws_b,
    ):
        resp = client.post("/command_broadcast", json={"kind": "all_stop"})
        assert resp.status_code == 200
        body = resp.json()
        assert set(body["delivered_to"]) == {"node-a", "node-b"}
        assert body["failed"] == []
        # Both nodes receive the frame.
        for ws in (ws_a, ws_b):
            received = json.loads(ws.receive_text())
            assert received["kind"] == "all_stop"


def test_command_broadcast_422_on_non_stop_kind(client: TestClient) -> None:
    resp = client.post("/command_broadcast", json={"kind": "manual_steer"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# UI push fan-out
# ---------------------------------------------------------------------------


def test_ui_ws_subscriber_receives_pushed_bearing(app: FastAPI) -> None:
    """A bearing-shaped payload pushed via the helper reaches a UI WS subscriber.

    Mounts a small synthetic POST route on the test app that calls
    ``push_to_ui_subscribers`` directly. The UI WS receives the JSON
    frame; the route returns 204. Lets us test the fan-out path without
    standing up the full ``/bearings`` ingest pipeline (which needs a
    Store + posterior engine + lifespan).
    """
    bearing = {
        "kind": "bearing",
        "data": {"node_id": "node-a", "azimuth_deg": 137.0, "azimuth_sigma_deg": 4.2},
    }

    @app.post("/_test_push")
    async def _test_push() -> dict[str, str]:
        await push_to_ui_subscribers(app, bearing)
        return {"ok": "true"}

    client = TestClient(app)
    with client.websocket_connect("/ws/ui") as ui_ws:
        resp = client.post("/_test_push")
        assert resp.status_code == 200
        received = json.loads(ui_ws.receive_text())
        assert received["kind"] == "bearing"
        assert received["data"]["node_id"] == "node-a"


# ---------------------------------------------------------------------------
# ADR-024 §8 clear_fault route
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ADR-022 /status ingest + node_status fan-out
# ---------------------------------------------------------------------------


_VALID_STATUS_PAYLOAD = {
    "schema_version": "1.4.0",
    "node_id": "node-a",
    "t_unix_ns": 1_700_000_000_000_000_000,
    "position": {
        "lat_deg": 50.85,
        "lon_deg": 4.35,
        "hae_m": 50.0,
        "sigma_m": 5.0,
    },
    "active_capabilities": ["l1_rssi"],
    "gnss_locked": True,
    "healthy": True,
    "status_detail": "",
}


def test_post_status_accepts_single_payload(client: TestClient) -> None:
    resp = client.post("/status", json=_VALID_STATUS_PAYLOAD)
    assert resp.status_code == 200
    assert resp.json() == {"accepted": 1, "errors": []}


def test_post_status_422_on_invalid_payload(client: TestClient) -> None:
    resp = client.post("/status", json={"node_id": "node-a"})
    assert resp.status_code == 422


def test_post_status_fans_node_status_to_ui_subscribers(app: FastAPI) -> None:
    test_client = TestClient(app)
    with test_client.websocket_connect("/ws/ui") as ui_ws:
        resp = test_client.post("/status", json=_VALID_STATUS_PAYLOAD)
        assert resp.status_code == 200
        received = json.loads(ui_ws.receive_text())
        assert received["kind"] == "node_status"
        assert received["node_id"] == "node-a"
        assert received["data"]["gnss_locked"] is True


def test_clear_fault_pushes_to_registered_node(client: TestClient) -> None:
    """POST /node/{id}/clear_fault forwards the JSON to the node WS."""
    with client.websocket_connect("/ws/node/node-a") as node_ws:
        resp = client.post(
            "/node/node-a/clear_fault",
            json={"kind": "clear_fault", "requestor_id": "ui-test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"delivered_to": "node-a", "kind": "clear_fault"}
        received = json.loads(node_ws.receive_text())
        assert received["kind"] == "clear_fault"
        assert received["requestor_id"] == "ui-test"


def test_clear_fault_503_when_node_offline(client: TestClient) -> None:
    resp = client.post(
        "/node/no-such-node/clear_fault",
        json={"kind": "clear_fault"},
    )
    assert resp.status_code == 503
    assert "not connected" in resp.json()["detail"]


def test_clear_fault_422_on_wrong_kind(client: TestClient) -> None:
    resp = client.post(
        "/node/node-a/clear_fault",
        json={"kind": "manual_steer"},  # wrong kind for this route
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# ADR-022 node_hello capability handshake + /node/{id}/capabilities
# ---------------------------------------------------------------------------


def test_node_hello_cached_and_served_via_http(client: TestClient, app: FastAPI) -> None:
    """A node_hello frame from the node is cached and served at /capabilities."""
    hello = {
        "kind": "node_hello",
        "node_id": "node-a",
        "active_capabilities": ["l1_rssi"],
        "heading_deg": 142.0,
        "calibrated_geographic_arc_deg": {"min": 52.0, "max": 232.0},
        "cal_provenance": "config",
        "peer": None,
        "controller_ready": False,
    }
    with client.websocket_connect("/ws/node/node-a") as ws:
        ws.send_text(json.dumps(hello))
        # Give the handler a chance to process the frame before we GET.
        resp = client.get("/node/node-a/capabilities")
        assert resp.status_code == 200
        body = resp.json()
        assert body["node_id"] == "node-a"
        assert body["online"] is True
        assert body["hello"]["calibrated_geographic_arc_deg"]["min"] == 52.0


def test_capabilities_404_when_no_hello_seen(client: TestClient) -> None:
    resp = client.get("/node/never-connected/capabilities")
    assert resp.status_code == 404
    assert "no capability snapshot" in resp.json()["detail"]


def test_hello_snapshot_persists_across_disconnect(client: TestClient, app: FastAPI) -> None:
    """Brief flap must NOT lose the slider clamp; the snapshot survives."""
    hello = {"kind": "node_hello", "node_id": "node-a", "active_capabilities": ["l1_rssi"]}
    with client.websocket_connect("/ws/node/node-a") as ws:
        ws.send_text(json.dumps(hello))
        resp = client.get("/node/node-a/capabilities")
        assert resp.status_code == 200
    # WS closed -> node is offline, but the cached snapshot remains.
    resp = client.get("/node/node-a/capabilities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["online"] is False
    assert body["hello"]["active_capabilities"] == ["l1_rssi"]


def test_list_capabilities_returns_all_cached(client: TestClient) -> None:
    hello_a = {"kind": "node_hello", "node_id": "node-a"}
    hello_b = {"kind": "node_hello", "node_id": "node-b"}
    with client.websocket_connect("/ws/node/node-a") as wa:
        wa.send_text(json.dumps(hello_a))
        with client.websocket_connect("/ws/node/node-b") as wb:
            wb.send_text(json.dumps(hello_b))
            resp = client.get("/nodes/capabilities")
            assert resp.status_code == 200
            ids = {n["node_id"] for n in resp.json()["nodes"]}
            assert ids == {"node-a", "node-b"}


def test_non_json_node_frame_dropped(client: TestClient) -> None:
    """A non-JSON frame from the node is dropped (B3 -- never silently misinterpret)."""
    with client.websocket_connect("/ws/node/node-a") as ws:
        ws.send_text("not json at all")
        # Subsequent valid hello must still be cached.
        ws.send_text(json.dumps({"kind": "node_hello", "node_id": "node-a"}))
        resp = client.get("/node/node-a/capabilities")
        assert resp.status_code == 200


def test_command_refused_frame_fanned_out_to_ui(client: TestClient, app: FastAPI) -> None:
    """A node-side `command_refused` frame surfaces to UI subscribers."""
    refused = {
        "kind": "command_refused",
        "node_id": "node-a",
        "refused_kind": "manual_steer",
        "requestor_id": "ui-link",
        "reason": "NodeController not yet wired",
    }
    with (
        client.websocket_connect("/ws/ui") as ui_ws,
        client.websocket_connect("/ws/node/node-a") as node_ws,
    ):
        node_ws.send_text(json.dumps(refused))
        received = json.loads(ui_ws.receive_text())
        assert received["kind"] == "command_refused"
        assert received["node_id"] == "node-a"
        assert received["data"]["reason"] == "NodeController not yet wired"


def test_push_to_ui_subscribers_no_subscribers_is_noop(app: FastAPI) -> None:
    """With zero subscribers, the helper must return without raising."""

    @app.post("/_test_push_empty")
    async def _push() -> dict[str, str]:
        await push_to_ui_subscribers(app, {"kind": "bearing", "data": {}})
        return {"ok": "true"}

    client = TestClient(app)
    resp = client.post("/_test_push_empty")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# ADR-025 Iter 4.6: comms WS fan-out (node -> backend -> UI)
# ---------------------------------------------------------------------------


def test_comms_rx_frame_fanned_out_to_ui(client: TestClient, app: FastAPI) -> None:
    """A node-side ``comms_rx`` frame reaches every UI subscriber verbatim.

    Soldier-loop demo gate: node decodes a DSSS frame, pushes
    ``{kind: comms_rx, node_id, data: {peer, text, t_unix_ns}}`` on
    its ``/ws/node/{id}`` socket; backend relays to ``/ws/ui``
    subscribers; ``link.js`` appends to the message log.
    """
    frame = {
        "kind": "comms_rx",
        "node_id": "node-a",
        "data": {
            "peer": "node-b",
            "text": "hello soldier",
            "t_unix_ns": 1_800_000_000_000_000_000,
        },
    }
    with (
        client.websocket_connect("/ws/ui") as ui_ws,
        client.websocket_connect("/ws/node/node-a") as node_ws,
    ):
        node_ws.send_text(json.dumps(frame))
        received = json.loads(ui_ws.receive_text())
        assert received["kind"] == "comms_rx"
        assert received["node_id"] == "node-a"
        assert received["data"]["peer"] == "node-b"
        assert received["data"]["text"] == "hello soldier"


def test_comms_status_frame_fanned_out_to_ui(client: TestClient, app: FastAPI) -> None:
    """A node-side ``comms_status`` snapshot reaches UI subscribers verbatim."""
    snap = {
        "kind": "comms_status",
        "node_id": "node-a",
        "data": {
            "link_up": True,
            "frames_sent": 3,
            "frames_received": 5,
            "frames_dropped": 1,
            "last_rx_t_unix_ns": 1_800_000_000_000_000_000,
            "last_tx_t_unix_ns": 1_800_000_000_000_000_001,
        },
    }
    with (
        client.websocket_connect("/ws/ui") as ui_ws,
        client.websocket_connect("/ws/node/node-a") as node_ws,
    ):
        node_ws.send_text(json.dumps(snap))
        received = json.loads(ui_ws.receive_text())
        assert received["kind"] == "comms_status"
        assert received["node_id"] == "node-a"
        assert received["data"]["link_up"] is True
        assert received["data"]["frames_sent"] == 3
        assert received["data"]["frames_received"] == 5
        assert received["data"]["frames_dropped"] == 1


def test_node_state_frame_fanned_out_to_ui(client: TestClient, app: FastAPI) -> None:
    """A node-side ``node_state`` frame (ADR-024) reaches UI subscribers.

    Closes a pre-existing gap surfaced during the Iter 4.6 review:
    NodeController pushes ``node_state`` via CommandChannel but the
    backend was silently dropping the frame. The dashboard's
    state-badge flip therefore depended on the 2-second heartbeat
    cadence rather than the event-driven ~200 ms path the
    demo-integrity rec called for. Fixed alongside comms fan-out.
    """
    state = {
        "kind": "node_state",
        "node_id": "node-a",
        "state": "sweeping",
        "status_detail": "",
        "manual_hold_expires_at": None,
        "controller_ready": True,
    }
    with (
        client.websocket_connect("/ws/ui") as ui_ws,
        client.websocket_connect("/ws/node/node-a") as node_ws,
    ):
        node_ws.send_text(json.dumps(state))
        received = json.loads(ui_ws.receive_text())
        assert received["kind"] == "node_state"
        assert received["node_id"] == "node-a"
        assert received["data"]["state"] == "sweeping"


def test_unknown_node_frame_kind_is_ignored(client: TestClient, app: FastAPI) -> None:
    """Future / unknown frame kinds are silently dropped (additive schema)."""
    junk = {"kind": "self_destruct", "node_id": "node-a", "payload": "boom"}
    with (
        client.websocket_connect("/ws/ui") as ui_ws,
        client.websocket_connect("/ws/node/node-a") as node_ws,
    ):
        node_ws.send_text(json.dumps(junk))
        # No frame should arrive at the UI subscriber. Confirm by
        # sending a known-good frame after and checking it is the
        # FIRST thing the UI receives.
        known = {
            "kind": "comms_rx",
            "node_id": "node-a",
            "data": {"peer": "node-b", "text": "ping", "t_unix_ns": 1},
        }
        node_ws.send_text(json.dumps(known))
        received = json.loads(ui_ws.receive_text())
        assert received["kind"] == "comms_rx"
        assert received["data"]["text"] == "ping"
