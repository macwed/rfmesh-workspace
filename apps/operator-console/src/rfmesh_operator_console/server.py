"""Operator console backend -- aiohttp app over a held-open TAK session.

Serves the Leaflet map UI and exposes a small JSON API the page calls to
place / delete markers. The single long-lived ``PyTAKCotPublisher`` is the
crux: FreeTAKServer only relays a sender's CoT to other connected clients
while that sender behaves like a connected client, so the server

* opens the publisher once and **keeps the connection open**, and
* sends a **self-SA** presence event on connect and every
  ``SELF_SA_INTERVAL_S`` seconds (``publish_raw`` + ``build_self_sa_xml``),

which is why markers placed here actually appear on the ATAK tablets
(the one-shot CLI ``connect -> write -> close`` did not relay).

It also re-broadcasts every live marker from the ``OperatorMarkerStore``
every ``REBROADCAST_INTERVAL_S`` seconds so a tablet that connects later
still receives them.

This app is a composition root (``apps/*``), so importing ``rfmesh_cot``
is intentional and outside the import-linter star discipline.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from pathlib import Path
from typing import Any

from aiohttp import web
from rfmesh_cot import (
    TEMPLATES,
    OperatorMarker,
    OperatorMarkerStore,
    PyTAKCotPublisher,
    build_self_sa_xml,
)
from rfmesh_cot.exceptions import CotEncodingError, CotError

_LOG = logging.getLogger(__name__)

#: Default TAK endpoint -- the BoTH3 hackathon FreeTAKServer.
DEFAULT_COT_URL = "tcp://35.206.145.140:8087"
#: How often to re-announce our presence so FTS keeps relaying our markers.
SELF_SA_INTERVAL_S = 20.0
#: How often to re-broadcast live markers (so late-joining tablets see them).
REBROADCAST_INTERVAL_S = 30.0

_STATIC = Path(__file__).parent / "static"

# aiohttp app keys
_PUB = web.AppKey("publisher", PyTAKCotPublisher)
_STORE = web.AppKey("store", OperatorMarkerStore)
_TASKS: web.AppKey[list[asyncio.Task[None]]] = web.AppKey("tasks")
_SELF_UID = web.AppKey("self_uid", str)
_SELF_CALLSIGN = web.AppKey("self_callsign", str)


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-") or "marker"


# ----- HTTP handlers -------------------------------------------------------


async def index(_request: web.Request) -> web.StreamResponse:
    return web.FileResponse(_STATIC / "index.html")


async def get_templates(_request: web.Request) -> web.Response:
    """List the operator's message vocabulary for the UI tabs."""
    payload = [{"key": t.key, "label": t.label, "geometry": t.geometry} for t in TEMPLATES.values()]
    return web.json_response(payload)


async def get_markers(request: web.Request) -> web.Response:
    """List the markers currently placed (for the UI side panel)."""
    store = request.app[_STORE]
    payload = [
        {
            "uid": m.uid,
            "template": m.template_key,
            "callsign": m.callsign,
            "remarks": m.remarks,
            "lat": m.lat_deg,
            "lon": m.lon_deg,
            "is_area": m.vertices is not None,
            "vertices": [list(v) for v in m.vertices] if m.vertices else None,
        }
        for m in store.all()
    ]
    return web.json_response(payload)


async def post_marker(request: web.Request) -> web.Response:
    """Place (or move) a marker from a map click / area draw."""
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        raise web.HTTPBadRequest(reason="body must be JSON") from None

    template_key = body.get("template")
    if template_key not in TEMPLATES:
        raise web.HTTPBadRequest(reason=f"unknown template {template_key!r}")
    tmpl = TEMPLATES[template_key]

    callsign = (body.get("callsign") or tmpl.label).strip()
    remarks = (body.get("remarks") or "").strip()
    uid = body.get("uid") or f"console.op.{tmpl.key}.{_slug(callsign)}-{uuid.uuid4().hex[:6]}"

    vertices = None
    lat = float(body.get("lat") or 0.0)
    lon = float(body.get("lon") or 0.0)
    if tmpl.geometry == "polygon":
        raw_verts = body.get("vertices") or []
        vertices = tuple((float(v[0]), float(v[1])) for v in raw_verts)

    marker = OperatorMarker(
        template_key=tmpl.key,
        uid=uid,
        lat_deg=lat,
        lon_deg=lon,
        callsign=callsign,
        remarks=remarks,
        vertices=vertices,
        stale_after_s=86400.0,  # operator markers persist a day
    )
    pub = request.app[_PUB]
    try:
        pub.publish_marker(marker)
    except CotEncodingError as exc:
        raise web.HTTPBadRequest(reason=str(exc)) from exc
    except CotError as exc:
        raise web.HTTPServiceUnavailable(reason=str(exc)) from exc
    request.app[_STORE].put(marker)
    _LOG.info("placed marker uid=%s template=%s", uid, tmpl.key)
    return web.json_response({"uid": uid})


async def post_delete(request: web.Request) -> web.Response:
    """Remove a previously-placed marker (un-send on all clients)."""
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(reason="body must be JSON") from None
    uid = body.get("uid")
    if not uid:
        raise web.HTTPBadRequest(reason="uid required")
    request.app[_STORE].remove(uid)
    pub = request.app[_PUB]
    with contextlib.suppress(CotError):
        pub.delete_marker(uid)
    _LOG.info("deleted marker uid=%s", uid)
    return web.json_response({"uid": uid})


# ----- background tasks ----------------------------------------------------


async def _self_sa_loop(app: web.Application) -> None:
    """Re-announce presence so FTS keeps relaying our markers."""
    pub, uid, cs = app[_PUB], app[_SELF_UID], app[_SELF_CALLSIGN]
    while True:
        with contextlib.suppress(CotError):
            pub.publish_raw(build_self_sa_xml(uid, cs))
        await asyncio.sleep(SELF_SA_INTERVAL_S)


async def _rebroadcast_loop(app: web.Application) -> None:
    """Re-send live markers so a tablet that joins later receives them."""
    pub, store = app[_PUB], app[_STORE]
    while True:
        await asyncio.sleep(REBROADCAST_INTERVAL_S)
        for marker in store.all():
            with contextlib.suppress(CotError):
                pub.publish_marker(marker)


async def _on_startup(app: web.Application) -> None:
    pub = app[_PUB]
    await pub.__aenter__()
    # Identify immediately so the first marker is relayed.
    with contextlib.suppress(CotError):
        pub.publish_raw(build_self_sa_xml(app[_SELF_UID], app[_SELF_CALLSIGN]))
    app[_TASKS] = [
        asyncio.create_task(_self_sa_loop(app)),
        asyncio.create_task(_rebroadcast_loop(app)),
    ]
    _LOG.info("operator console up; relaying markers to the TAK endpoint")


async def _on_cleanup(app: web.Application) -> None:
    for task in app.get(_TASKS, []):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    with contextlib.suppress(Exception):
        await app[_PUB].__aexit__(None, None, None)


def build_app(cot_url: str, self_callsign: str = "13-counter-jamming-console") -> web.Application:
    """Construct the aiohttp application wired to ``cot_url``."""
    app = web.Application()
    app[_PUB] = PyTAKCotPublisher(cot_url, node_callsign=self_callsign)
    app[_STORE] = OperatorMarkerStore()
    app[_SELF_UID] = f"console.self.{_slug(self_callsign)}.{uuid.uuid4().hex[:6]}"
    app[_SELF_CALLSIGN] = self_callsign
    app.router.add_get("/", index)
    app.router.add_get("/api/templates", get_templates)
    app.router.add_get("/api/markers", get_markers)
    app.router.add_post("/api/marker", post_marker)
    app.router.add_post("/api/delete", post_delete)
    app.router.add_static("/static/", _STATIC)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app
