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
from both3_poc.ws import init_registries, push_to_ui_subscribers, router as ws_router
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
    assert resp.status_code == 503  # noqa: PLR2004
    body = resp.json()
    assert "not connected" in body["detail"]


def test_post_command_422_on_bad_payload(client: TestClient) -> None:
    # Missing required field "target_angle_deg".
    resp = client.post("/command/node-a", json={"kind": "manual_steer", "axis": 0})
    assert resp.status_code == 422  # noqa: PLR2004


def test_post_command_422_on_unknown_kind(client: TestClient) -> None:
    resp = client.post(
        "/command/node-a",
        json={"kind": "wibble", "axis": 0, "target_angle_deg": 0.0},
    )
    assert resp.status_code == 422  # noqa: PLR2004


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
        assert resp.status_code == 200  # noqa: PLR2004
        # Read the frame pushed down the node socket.
        received_text = node_ws.receive_text()
        received = json.loads(received_text)
        assert received["kind"] == "manual_steer"
        assert received["target_angle_deg"] == 12.5  # noqa: PLR2004
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
        assert resp.status_code == 200  # noqa: PLR2004
        body = resp.json()
        assert set(body["delivered_to"]) == {"node-a", "node-b"}
        assert body["failed"] == []
        # Both nodes receive the frame.
        for ws in (ws_a, ws_b):
            received = json.loads(ws.receive_text())
            assert received["kind"] == "all_stop"


def test_command_broadcast_422_on_non_stop_kind(client: TestClient) -> None:
    resp = client.post("/command_broadcast", json={"kind": "manual_steer"})
    assert resp.status_code == 422  # noqa: PLR2004


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
        assert resp.status_code == 200  # noqa: PLR2004
        received = json.loads(ui_ws.receive_text())
        assert received["kind"] == "bearing"
        assert received["data"]["node_id"] == "node-a"


def test_push_to_ui_subscribers_no_subscribers_is_noop(app: FastAPI) -> None:
    """With zero subscribers, the helper must return without raising."""

    @app.post("/_test_push_empty")
    async def _push() -> dict[str, str]:
        await push_to_ui_subscribers(app, {"kind": "bearing", "data": {}})
        return {"ok": "true"}

    client = TestClient(app)
    resp = client.post("/_test_push_empty")
    assert resp.status_code == 200  # noqa: PLR2004
