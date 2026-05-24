"""Integration tests for the backend's auto-fuse-on-/bearings pipeline.

The Phase 3 (2-node MVP) glue:

1. ``POST /bearings`` accepts BearingReport JSON + stores + UI-pushes +
   feeds the FusionService inbox via ``push_for_test``.
2. FusionService batches every ``batch_window_ms``, runs
   ``StansfieldMLEFuser.fuse()`` over the batch, publishes the
   resulting FixEvent via ``DashboardPubSub``.
3. ``_FixToStoreAndUiSubscriber`` upserts the fix into ``Store`` +
   fans it to UI WS subscribers as ``kind="fix"``.

The end-to-end gate is: post 2 bearings that intersect at a known
point, wait for the batch window, assert a FixEvent appears in
``store.list_fixes()`` AND is pushed to the /ws/ui subscriber.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def fuse_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force fusion on + tighten batch window for the test loop."""
    monkeypatch.setenv("FUSION_ENABLED", "true")
    monkeypatch.setenv("FUSION_BATCH_WINDOW_MS", "100")
    monkeypatch.setenv("FUSION_MIN_BEARINGS", "2")
    monkeypatch.setenv("SEED_DEMO", "false")
    monkeypatch.setenv("COT_ENDPOINT_URL", "tcp://127.0.0.1:1")
    # Frontend dir + posterior deps are not exercised in this test.
    monkeypatch.setenv("FRONTEND_DIR", "/tmp/no-such-frontend")
    yield


@pytest.fixture
def app(fuse_env: None) -> Iterator[Any]:
    """Build a fresh app with auto-fuse enabled. Lifespan runs in TestClient."""
    # Import after env is patched so Settings.from_env picks it up.
    from both3_poc.app import app as _app

    yield _app


def _bearing_payload(
    *, node_id: str, lat: float, lon: float, az: float, t_ns: int
) -> dict[str, Any]:
    """Construct a BearingReport JSON payload (schema_version omitted = default)."""
    return {
        "node_id": node_id,
        "t_unix_ns": t_ns,
        "node_position": {"lat_deg": lat, "lon_deg": lon, "hae_m": 0.0, "sigma_m": 5.0},
        "azimuth_deg": az,
        "azimuth_sigma_deg": 3.0,
        "method": "l1_rssi",
    }


def _bearing_to_emitter(
    node_lat: float, node_lon: float, emitter_lat: float, emitter_lon: float
) -> float:
    """True bearing from node to emitter in degrees CW from north (small-angle)."""
    dlat = emitter_lat - node_lat
    dlon = (emitter_lon - node_lon) * math.cos(math.radians(node_lat))
    return math.degrees(math.atan2(dlon, dlat)) % 360.0


def test_two_bearings_fuse_into_fix_event(app: Any) -> None:
    """Two intersecting bearings -> FixEvent in store within ~2 batch windows."""
    node_a = (50.330, 5.000)
    node_b = (50.330, 5.010)
    emitter = (50.340, 5.005)
    az_a = _bearing_to_emitter(*node_a, *emitter)
    az_b = _bearing_to_emitter(*node_b, *emitter)

    t_now = time.time_ns()
    client = TestClient(app)
    with client:
        # Sanity: store starts empty (SEED_DEMO=false).
        assert client.get("/health").json()["fix_count"] == 0
        # Ingest both bearings as one batch so they share the time window.
        r = client.post(
            "/bearings",
            json=[
                _bearing_payload(
                    node_id="node-a", lat=node_a[0], lon=node_a[1], az=az_a, t_ns=t_now
                ),
                _bearing_payload(
                    node_id="node-b", lat=node_b[0], lon=node_b[1], az=az_b, t_ns=t_now
                ),
            ],
        )
        assert r.status_code == 200
        assert r.json()["accepted"] == 2

        # Wait for batch_window + a bit more so fuse_loop drains.
        # Batch window = 100 ms; one full cycle is collect + fuse + publish.
        deadline = time.monotonic() + 3.0
        fix_count = 0
        while time.monotonic() < deadline:
            fix_count = client.get("/health").json()["fix_count"]
            if fix_count > 0:
                break
            time.sleep(0.05)
        assert fix_count == 1, "FusionService did not publish a FixEvent within 3 s"


def test_single_bearing_does_not_fuse(app: Any) -> None:
    """min_bearings_for_fix=2 -> a single report yields no fix."""
    t_now = time.time_ns()
    client = TestClient(app)
    with client:
        r = client.post(
            "/bearings",
            json=_bearing_payload(
                node_id="node-a", lat=50.33, lon=5.0, az=45.0, t_ns=t_now
            ),
        )
        assert r.status_code == 200
        # Wait through 2 batch windows.
        time.sleep(0.3)
        assert client.get("/health").json()["fix_count"] == 0


def test_fix_pushed_to_ui_ws_subscriber(app: Any) -> None:
    """The FixEvent is fanned to UI as kind='fix' via push_to_ui_subscribers."""
    node_a = (50.330, 5.000)
    node_b = (50.330, 5.010)
    emitter = (50.340, 5.005)
    az_a = _bearing_to_emitter(*node_a, *emitter)
    az_b = _bearing_to_emitter(*node_b, *emitter)
    t_now = time.time_ns()
    client = TestClient(app)
    with client, client.websocket_connect("/ws/ui") as ui_ws:
        client.post(
            "/bearings",
            json=[
                _bearing_payload(
                    node_id="node-a", lat=node_a[0], lon=node_a[1], az=az_a, t_ns=t_now
                ),
                _bearing_payload(
                    node_id="node-b", lat=node_b[0], lon=node_b[1], az=az_b, t_ns=t_now
                ),
            ],
        )
        # Drain WS frames: expect bearing*2, then fix (after batch window).
        # TestClient's receive_text() blocks until a frame arrives; we
        # cap the loop with a hard frame budget. Bearings push first,
        # fix follows after the batch_window_ms drain (~100 ms).
        saw_fix = False
        for _ in range(8):
            text = ui_ws.receive_text()
            payload = json.loads(text)
            if payload.get("kind") == "fix":
                saw_fix = True
                assert "fix_id" in payload
                assert "data" in payload
                break
        assert saw_fix, "No fix payload pushed to UI WS within 8 frames"
