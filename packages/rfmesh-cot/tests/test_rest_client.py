"""Tests for ``FreeTakServerRestClient`` against a loopback HTTP server.

A real ``http.server`` on 127.0.0.1 captures the method, path, headers and
body of each request and replies with a programmable status + body. This
exercises the whole urllib path -- including the ``Authorization: Bearer``
header that is the entire point of the Drupal-module port -- without any
network or a live FreeTAKServer.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from rfmesh_cot import (
    CotRestError,
    FreeTakServerRestClient,
    OperatorMarker,
    attitude_from_cot_type,
)


@dataclass
class CapturedRequest:
    """One request the loopback server saw."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


@dataclass
class FakeFts:
    """A loopback FTS REST stand-in: captures requests, replies on demand."""

    host: str = "127.0.0.1"
    port: int = 0
    status: int = 200
    response_body: str = ""
    requests: list[CapturedRequest] = field(default_factory=list)
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        captures = self.requests
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _capture(self, method: str) -> None:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b""
                captures.append(
                    CapturedRequest(
                        method=method,
                        path=self.path,
                        headers={k: v for k, v in self.headers.items()},
                        body=body,
                    )
                )
                payload = outer.response_body.encode("utf-8")
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:
                self._capture("GET")

            def do_POST(self) -> None:
                self._capture("POST")

            def do_PUT(self) -> None:
                self._capture("PUT")

            def log_message(self, *_args: Any) -> None:
                # Silence the default stderr access log during tests.
                return

        self._server = ThreadingHTTPServer((self.host, 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


@pytest.fixture()
def fake_fts() -> Iterator[FakeFts]:
    server = FakeFts()
    server.start()
    try:
        yield server
    finally:
        server.stop()


# ----- attitude mapping (pure) ----------------------------------------


def test_attitude_from_cot_type_affiliations() -> None:
    assert attitude_from_cot_type("a-h-G") == "hostile"
    assert attitude_from_cot_type("a-f-G") == "friendly"
    assert attitude_from_cot_type("a-n-G") == "neutral"
    assert attitude_from_cot_type("a-u-G") == "unknown"


def test_attitude_from_cot_type_non_atom_is_none() -> None:
    assert attitude_from_cot_type("b-m-p-w") is None  # waypoint tactical graphic
    assert attitude_from_cot_type("u-d-f") is None  # drawing polygon
    assert attitude_from_cot_type("") is None


# ----- base URL handling ----------------------------------------------


def test_base_url_defaults_rest_port() -> None:
    client = FreeTakServerRestClient("http://tak.example.com", "tok")
    assert client._base_url == "http://tak.example.com:19023"


def test_base_url_rejects_bad_scheme() -> None:
    with pytest.raises(ValueError, match="http"):
        FreeTakServerRestClient("tcp://tak.example.com:8087", "tok")


# ----- auth header (the Drupal-module port) ---------------------------


def test_bearer_header_sent(fake_fts: FakeFts) -> None:
    fake_fts.response_body = '{"APIVersion": "1.9.5", "SupportedEndpoints": []}'
    client = FreeTakServerRestClient(fake_fts.base_url, "secret-token-123")
    client.get_help()
    req = fake_fts.requests[0]
    assert req.headers["Authorization"] == "Bearer secret-token-123"
    assert req.path == "/manageAPI/getHelp"
    assert req.method == "GET"


def test_empty_token_sends_no_auth_header(fake_fts: FakeFts) -> None:
    fake_fts.response_body = '{"APIVersion": "1.9.5"}'
    client = FreeTakServerRestClient(fake_fts.base_url, "")
    client.get_help()
    assert "Authorization" not in fake_fts.requests[0].headers


# ----- getHelp --------------------------------------------------------


def test_get_help_parses_json(fake_fts: FakeFts) -> None:
    fake_fts.response_body = '{"APIVersion": "1.9.5", "SupportedEndpoints": ["a", "b"]}'
    client = FreeTakServerRestClient(fake_fts.base_url, "t")
    info = client.get_help()
    assert info["APIVersion"] == "1.9.5"
    assert info["SupportedEndpoints"] == ["a", "b"]


# ----- postGeoObject --------------------------------------------------


def test_post_geo_object_body_and_uid(fake_fts: FakeFts) -> None:
    fake_fts.response_body = '{"uid": "server-generated-uid"}'
    client = FreeTakServerRestClient(fake_fts.base_url, "t")
    uid = client.post_geo_object(
        name="Jammer A",
        latitude=50.0661234567,
        longitude=4.866,
        attitude="hostile",
        timeout_s=600,
    )
    assert uid == "server-generated-uid"
    req = fake_fts.requests[0]
    assert req.method == "POST"
    assert req.path == "/ManageGeoObject/postGeoObject"
    body = req.json()
    assert body["name"] == "Jammer A"
    assert body["attitude"] == "hostile"
    assert body["geoObject"] == "Ground"
    assert body["how"] == "nonCoT"
    assert body["latitude"] == pytest.approx(50.0661235)  # rounded to 7dp
    assert body["timeout"] == 600


def test_post_geo_object_uid_fallback_on_empty_body(fake_fts: FakeFts) -> None:
    fake_fts.response_body = ""  # success but no body
    client = FreeTakServerRestClient(fake_fts.base_url, "t")
    uid = client.post_geo_object(
        name="X", latitude=1.0, longitude=2.0, attitude="hostile", uid="my-uid"
    )
    assert uid == "my-uid"


def test_post_geo_object_bare_uid_string(fake_fts: FakeFts) -> None:
    fake_fts.response_body = '"echoed-uid"'
    client = FreeTakServerRestClient(fake_fts.base_url, "t")
    uid = client.post_geo_object(name="X", latitude=1.0, longitude=2.0, attitude="hostile")
    assert uid == "echoed-uid"


# ----- putGeoObject ---------------------------------------------------


def test_put_geo_object_sends_uid(fake_fts: FakeFts) -> None:
    fake_fts.response_body = '{"uid": "u1"}'
    client = FreeTakServerRestClient(fake_fts.base_url, "t")
    client.put_geo_object(uid="u1", latitude=1.0, longitude=2.0, attitude="hostile")
    req = fake_fts.requests[0]
    assert req.method == "PUT"
    assert req.path == "/ManageGeoObject/putGeoObject"
    assert req.json()["uid"] == "u1"


# ----- getGeoObject (query params) ------------------------------------


def test_get_geo_object_query_params(fake_fts: FakeFts) -> None:
    fake_fts.response_body = "[]"
    client = FreeTakServerRestClient(fake_fts.base_url, "t")
    result = client.get_geo_object(latitude=38.889, longitude=-77.0, radius_m=5000)
    assert result == []
    path = fake_fts.requests[0].path
    assert path.startswith("/ManageGeoObject/getGeoObject?")
    assert "latitude=38.889" in path
    assert "radius=5000" in path


# ----- OperatorMarker adapter -----------------------------------------


def test_publish_marker_maps_template_to_attitude(fake_fts: FakeFts) -> None:
    fake_fts.response_body = '{"uid": "rfmesh.op.hostile.x"}'
    client = FreeTakServerRestClient(fake_fts.base_url, "t")
    marker = OperatorMarker(
        template_key="hostile",
        uid="rfmesh.op.hostile.x",
        lat_deg=50.0,
        lon_deg=4.0,
        callsign="Jammer A",
        remarks="operator-confirmed",
    )
    uid = client.publish_marker(marker)
    assert uid == "rfmesh.op.hostile.x"
    body = fake_fts.requests[0].json()
    assert body["attitude"] == "hostile"
    assert body["uid"] == "rfmesh.op.hostile.x"
    assert body["name"] == "Jammer A"
    assert body["remarks"] == "operator-confirmed"
    assert body["timeout"] == 3600  # template default stale


def test_publish_marker_rejects_polygon(fake_fts: FakeFts) -> None:
    client = FreeTakServerRestClient(fake_fts.base_url, "t")
    marker = OperatorMarker(
        template_key="no_go",
        uid="rfmesh.op.no_go.n",
        vertices=((50.0, 4.0), (50.1, 4.0), (50.1, 4.1)),
    )
    with pytest.raises(CotRestError, match="points only"):
        client.publish_marker(marker)
    assert fake_fts.requests == []  # never hit the wire


def test_publish_marker_rejects_non_affiliation_type(fake_fts: FakeFts) -> None:
    client = FreeTakServerRestClient(fake_fts.base_url, "t")
    marker = OperatorMarker(template_key="waypoint", uid="wp1", lat_deg=1.0, lon_deg=2.0)
    with pytest.raises(CotRestError, match="no FTS REST attitude"):
        client.publish_marker(marker)


def test_expire_marker_puts_one_second_timeout(fake_fts: FakeFts) -> None:
    fake_fts.response_body = '{"uid": "x"}'
    client = FreeTakServerRestClient(fake_fts.base_url, "t")
    marker = OperatorMarker(template_key="hostile", uid="x", lat_deg=50.0, lon_deg=4.0)
    client.expire_marker(marker)
    req = fake_fts.requests[0]
    assert req.method == "PUT"
    assert req.json()["timeout"] == 1


# ----- error surfacing (B3: never silent) -----------------------------


def test_http_500_becomes_rest_error(fake_fts: FakeFts) -> None:
    fake_fts.status = 500
    fake_fts.response_body = "bad geoObject name"
    client = FreeTakServerRestClient(fake_fts.base_url, "t")
    with pytest.raises(CotRestError) as exc_info:
        client.post_geo_object(name="X", latitude=1.0, longitude=2.0, attitude="hostile")
    assert exc_info.value.status_code == 500
    assert "bad geoObject name" in exc_info.value.body


def test_connection_refused_becomes_rest_error() -> None:
    # Nothing is listening on this port -> URLError, surfaced with status None.
    client = FreeTakServerRestClient("http://127.0.0.1:1", "t", timeout_s=1.0)
    with pytest.raises(CotRestError) as exc_info:
        client.get_help()
    assert exc_info.value.status_code is None


def test_get_help_non_json_raises(fake_fts: FakeFts) -> None:
    fake_fts.response_body = "<html>not json</html>"
    client = FreeTakServerRestClient(fake_fts.base_url, "t")
    with pytest.raises(CotRestError, match="non-JSON"):
        client.get_help()
