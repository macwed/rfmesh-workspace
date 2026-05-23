"""HttpBearer: POSTs a BearingReport as JSON to <base>/bearings."""

from __future__ import annotations

import json
from types import TracebackType
from typing import Any

import pytest
from rfmesh_contracts import BearingReport, Capability, GeodeticPosition
from rfmesh_node.bearer.http import HttpBearer

_POS = GeodeticPosition(lat_deg=50.33, lon_deg=5.0, hae_m=200.0, sigma_m=5.0)


def _report() -> BearingReport:
    return BearingReport(
        node_id="node-rtl-01",
        t_unix_ns=1_700_000_000_000_000_000,
        node_position=_POS,
        azimuth_deg=123.4,
        azimuth_sigma_deg=8.5,
        method=Capability.L1_RSSI,
        snr_db=15.2,
    )


class _FakeResponse:
    def __init__(self, status: int = 200) -> None:
        self.status = status

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


def test_send_bearing_posts_json_to_bearings(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float) -> _FakeResponse:
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResponse(200)

    monkeypatch.setattr("rfmesh_node.bearer.http.urllib.request.urlopen", fake_urlopen)

    bearer = HttpBearer("http://10.0.0.1:8000/")
    bearer.send_bearing(_report())

    assert captured["url"] == "http://10.0.0.1:8000/bearings"
    assert captured["method"] == "POST"
    # header keys are title-cased by urllib
    assert captured["headers"].get("Content-type") == "application/json"
    body = captured["body"]
    assert body["node_id"] == "node-rtl-01"
    assert body["azimuth_deg"] == pytest.approx(123.4)
    assert body["method"] == "l1_rssi"  # enum serialised to its str value
    assert body["node_position"]["lat_deg"] == pytest.approx(50.33)


def test_send_bearing_swallows_unreachable_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    def boom(request: Any, timeout: float) -> _FakeResponse:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("rfmesh_node.bearer.http.urllib.request.urlopen", boom)

    bearer = HttpBearer("http://10.0.0.1:8000")
    # Best-effort delivery: a down backend must not raise into the node.
    bearer.send_bearing(_report())


def test_receive_is_empty_and_close_is_safe() -> None:
    bearer = HttpBearer("http://10.0.0.1:8000")
    assert bearer.receive() == ()
    bearer.close()  # no-op, must not raise


def test_send_status_disables_after_first_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    calls = {"n": 0}

    def boom(request: Any, timeout: float) -> _FakeResponse:
        calls["n"] += 1
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr("rfmesh_node.bearer.http.urllib.request.urlopen", boom)

    from rfmesh_contracts import NodeStatus

    status = NodeStatus(
        node_id="node-rtl-01",
        t_unix_ns=1_700_000_000_000_000_000,
        position=_POS,
        active_capabilities=(Capability.L1_RSSI,),
        gnss_locked=False,
        healthy=True,
        status_detail="",
    )
    bearer = HttpBearer("http://10.0.0.1:8000")
    bearer.send_status(status)
    bearer.send_status(status)
    # /status 404'd once, then disabled -- second call short-circuits.
    assert calls["n"] == 1
