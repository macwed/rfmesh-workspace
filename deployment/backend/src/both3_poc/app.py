"""FastAPI app for the both3-poc ops layer.

Endpoints:
  GET  /health                 -> liveness + counts + cot endpoint
  POST /fixes                  -> ingest one FixEvent or a list (nested or flat)
  POST /bearings               -> ingest one BearingReport or a list
  GET  /fixes                  -> GeoJSON (ellipse polygons + centre points)
  GET  /bearings               -> GeoJSON (node points + LOB rays)
  POST /fixes/{fix_id}/send    -> publish a stored fix to CoT/TAK
  GET  /                       -> static frontend (Leaflet map)

The CoT publisher is owned by the app lifespan (one long-lived instance), per
the PyTAKCotPublisher lifecycle constraints. See cot_send.py.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from functools import partial
from typing import Any
from uuid import UUID, uuid4

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from rfmesh_contracts import BearingReport
from rfmesh_cot import CotError, OperatorMarker

from .config import Settings
from .cot_send import CotSender
from .enhance import (
    RES_GRID,
    EnhanceManager,
    LidarSource,
    finest_band_surface,
    surface_res_for_band,
)
from .geofence import geofence_remarks, outline_to_geofence_markers
from .geojson import bearings_feature_collection, fixes_feature_collection
from .inference import investigate, load_catalog
from .posterior import LENS_CONFIG, PosteriorEngine, _Red, nodes_for_fix
from .seed import load_seed_bearings, load_seed_fixes_with_freq, parse_fix_and_freq
from .store import Store
from .ws import init_registries, push_to_ui_subscribers
from .ws import router as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    settings = Settings.from_env()
    store = Store()
    sender = CotSender(settings.cot_endpoint_url, settings.cot_callsign, settings.send_fresh)

    if settings.seed_demo:
        for fix, freq in load_seed_fixes_with_freq(settings.seed_file):
            store.upsert_fix(fix, seeded=True, center_freq_hz=freq)
        for bearing in load_seed_bearings(settings.seed_bearings_file):
            store.upsert_bearing(bearing)

    app.state.settings = settings
    app.state.store = store
    app.state.sender = sender
    # Monotonic Jx label counter for outline→geofence sends (J1, J2, …).
    app.state.geofence_seq = 0
    # Geofence orders the ops UI has pushed, for the ATAK plugin to pull + draw
    # on-device (FreeTAKServer strips drawing shapes, so the device renders them
    # itself). uid -> {uid,label,geometry,remarks,cutoff,t_unix_ns}.
    app.state.geofences = {}
    engine = PosteriorEngine(settings.dem_file)
    app.state.posterior = engine
    app.state.enhance = EnhanceManager(
        engine,
        {
            "dtm": LidarSource(
                settings.lidar_file,
                source_id="wallonia-lidar-mnt-1m",
                label="Wallonia LiDAR MNT 1 m (bare earth)",
            ),
            "dsm": LidarSource(
                settings.lidar_dsm_file,
                source_id="wallonia-lidar-mns-1m",
                label="Wallonia LiDAR MNS 1 m (surface, incl. buildings)",
            ),
        },
        cache_dir=settings.enhance_cache_dir,
        max_concurrent=settings.enhance_max_concurrent,
        timeout_s=settings.enhance_timeout_s,
    )
    # ADR-018: WebSocket control plane + UI push fan-out.
    # `init_registries` attaches NodeWsRegistry + UiWsRegistry to
    # app.state; the `ws_router` mounted below exposes /ws/node/{id},
    # /ws/ui, /command/{id}, /command_broadcast.
    init_registries(app)
    try:
        yield
    finally:
        await sender.aclose()


app = FastAPI(title="both3-poc", version="0.1.0", lifespan=lifespan)
app.include_router(ws_router)


def _settings(app: FastAPI) -> Settings:
    return app.state.settings


def _store(app: FastAPI) -> Store:
    return app.state.store


def _as_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]
    raise HTTPException(status_code=422, detail="body must be a JSON object or array")


@app.get("/health")
async def health() -> dict[str, Any]:
    fixes, bearings = _store(app).counts()
    return {
        "status": "ok",
        "fix_count": fixes,
        "bearing_count": bearings,
        "cot_endpoint": _settings(app).cot_endpoint_url,
    }


@app.get("/downloads/{name}")
async def download_apk(name: str) -> FileResponse:
    """Serve a hosted ``.apk`` with the Android install MIME type — both the
    RF Geofence plugin (``rfgeofence.apk``) and the SDK-signed ATAK-CIV
    (``atak.apk``, which the plugin's signature matches). Files live in
    ``<frontend_dir>/downloads/`` and are NOT in git. 404 until present."""
    if "/" in name or "\\" in name or ".." in name or not name.endswith(".apk"):
        raise HTTPException(status_code=404, detail="not found")
    path = _settings(app).frontend_dir / "downloads" / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"{name} not uploaded yet")
    return FileResponse(
        path,
        media_type="application/vnd.android.package-archive",
        filename=name,
    )


_MARK_COT_TYPES = {
    "jammer": "a-h-G-E-X-N-J",
    "hostile": "a-h-G",
    "friendly": "a-f-G",
    "neutral": "a-n-G",
    "waypoint": "b-m-p-w",
}


@app.post("/marker")
async def place_marker(payload: Any = Body(...)) -> dict[str, Any]:
    """Drop a named point marker at any map point (operator UI). Default kind is
    ``jammer`` (hostile EW emitter ``a-h-G-E-X-N-J``). Sends a CoT point via the
    operator path to the configured TAK endpoint — set ``COT_ENDPOINT_URL`` at
    the tablets' server for it to reach them."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="body must be a JSON object")
    try:
        lat = float(payload["lat"])
        lon = float(payload["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="numeric lat and lon required") from exc
    name = str(payload.get("name") or "").strip() or "Jammer"
    kind = str(payload.get("kind") or "jammer").lower()
    cot_type = _MARK_COT_TYPES.get(kind, _MARK_COT_TYPES["jammer"])
    sender: CotSender = app.state.sender
    marker = OperatorMarker(
        template_key="hostile",  # point base; cot_type_override sets the icon
        uid=f"rfmesh.mark.{uuid4().hex[:12]}",
        lat_deg=lat,
        lon_deg=lon,
        callsign=name,
        cot_type_override=cot_type,
        remarks=f"operator mark: {name}" + (" (jammer)" if kind == "jammer" else ""),
    )
    try:
        await sender.send_marker(marker)
    except CotError as exc:
        raise HTTPException(
            status_code=502, detail=f"CoT send failed via {sender.endpoint_url}: {exc}"
        ) from exc
    return {
        "sent": True,
        "uid": marker.uid,
        "name": name,
        "kind": kind,
        "endpoint": sender.endpoint_url,
    }


@app.post("/fixes")
async def ingest_fixes(payload: Any = Body(...)) -> dict[str, Any]:
    store = _store(app)
    accepted = 0
    errors: list[str] = []
    for i, rec in enumerate(_as_list(payload)):
        try:
            fix, freq = parse_fix_and_freq(rec)
            store.upsert_fix(fix, center_freq_hz=freq)
            accepted += 1
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"[{i}] {exc}")
    if accepted == 0 and errors:
        raise HTTPException(status_code=422, detail=errors)
    return {"accepted": accepted, "errors": errors}


@app.post("/bearings")
async def ingest_bearings(payload: Any = Body(...)) -> dict[str, Any]:
    store = _store(app)
    accepted = 0
    errors: list[str] = []
    pushed: list[dict[str, Any]] = []
    for i, rec in enumerate(_as_list(payload)):
        try:
            report = BearingReport.model_validate(rec)
            store.upsert_bearing(report)
            accepted += 1
            pushed.append(report.model_dump(mode="json"))
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"[{i}] {exc}")
    # ADR-018: fan accepted bearings out to live UI subscribers. Wrapped
    # in a per-record envelope so the UI knows the message kind. Failure
    # to push (slow UI client) does NOT fail the ingest — the store
    # state is the source of truth, push is best-effort live overlay.
    for record in pushed:
        await push_to_ui_subscribers(app, {"kind": "bearing", "data": record})
    if accepted == 0 and errors:
        raise HTTPException(status_code=422, detail=errors)
    return {"accepted": accepted, "errors": errors}


@app.get("/fixes")
async def get_fixes() -> JSONResponse:
    settings = _settings(app)
    store = _store(app)
    fc = fixes_feature_collection(
        store.list_fixes(),
        now_ns=time.time_ns(),
        stale_window_s=settings.stale_window_s,
        seeded_ids=store.seeded_fix_ids,
        seed_as_fresh=settings.seed_as_fresh,
    )
    return JSONResponse(fc)


@app.get("/bearings")
async def get_bearings() -> JSONResponse:
    settings = _settings(app)
    store = _store(app)
    fc = bearings_feature_collection(
        store.latest_bearings_per_node(),
        lob_length_m=settings.lob_length_m,
    )
    return JSONResponse(fc)


@app.post("/fixes/{fix_id}/send")
async def send_fix(fix_id: UUID) -> dict[str, Any]:
    store = _store(app)
    sender: CotSender = app.state.sender
    fix = store.get_fix(fix_id)
    if fix is None:
        raise HTTPException(status_code=404, detail=f"no fix with id {fix_id}")
    try:
        await sender.send(fix)
    except CotError as exc:
        raise HTTPException(
            status_code=502, detail=f"CoT send failed via {sender.endpoint_url}: {exc}"
        ) from exc
    return {"sent": True, "detail": f"encoded and queued to {sender.endpoint_url}"}


@app.post("/fixes/{fix_id}/geofence")
async def send_geofence(fix_id: UUID, payload: Any = Body(...)) -> dict[str, Any]:
    """Publish an RF-plausibility outline to ATAK as a monitored ``Jx`` geofence.

    WYSIWYG: the body carries the exact GeoJSON ring the operator is looking at
    (one probability-mass band of the posterior). The label, honest remarks, and
    the ``<__geofence>`` detail are server-owned (geofences look authoritative —
    the cue-not-target / no-dBm caveat must not be client-trusted). A
    MultiPolygon yields one geofence shape per part.
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="body must be a JSON object")
    store = _store(app)
    sender: CotSender = app.state.sender
    fix = store.get_fix(fix_id)
    if fix is None:
        raise HTTPException(status_code=404, detail=f"no fix with id {fix_id}")
    geometry = payload.get("geometry")
    if not isinstance(geometry, dict):
        raise HTTPException(
            status_code=422, detail="geometry (GeoJSON Polygon/MultiPolygon) required"
        )
    # Coverage descriptor for the (server-owned) remarks. A cutoff contour says
    # "≥X% of peak plausibility"; a legacy mass band says "X% probability mass".
    cutoff = payload.get("cutoff")
    p_band = payload.get("p_band")
    if cutoff is not None:
        coverage_label = f"≥{round(float(cutoff) * 100)}% of peak plausibility"
    elif p_band is not None:
        coverage_label = f"{round(float(p_band) * 100)}% probability mass"
    else:
        coverage_label = "operator-selected outline"
    label = str(payload.get("label") or "").strip()
    if not label:
        app.state.geofence_seq = int(getattr(app.state, "geofence_seq", 0)) + 1
        label = f"J{app.state.geofence_seq}"
    raw_props = payload.get("source_props")
    source_props = raw_props if isinstance(raw_props, dict) else None
    try:
        markers = outline_to_geofence_markers(
            geometry,
            label=label,
            coverage_label=coverage_label,
            uid_seed=str(fix_id)[:8],
            source_props=source_props,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        for marker in markers:
            await sender.send_marker(marker)
    except CotError as exc:
        raise HTTPException(
            status_code=502, detail=f"CoT send failed via {sender.endpoint_url}: {exc}"
        ) from exc
    note = "burnthrough: shadow may not hold (high-ERP jammer)" if (
        source_props and source_props.get("burnthrough")
    ) else None
    # Store the order so the ATAK plugin can pull + draw it on-device. This is
    # the path that actually reaches the tablet (the CoT/FTS send above can't —
    # FTS strips drawing shapes). Keyed by fix so re-sends replace, not pile up.
    order_uid = f"jx-{str(fix_id)[:8]}"
    app.state.geofences[order_uid] = {
        "uid": order_uid,
        "label": label,
        "geometry": geometry,
        "remarks": geofence_remarks(coverage_label, source_props),
        "cutoff": cutoff if cutoff is not None else p_band,
        "t_unix_ns": time.time_ns(),
    }
    return {
        "label": label,
        "sent": len(markers),
        "uids": [m.uid for m in markers],
        "stored_for_plugin": order_uid,
        "note": note,
    }


@app.get("/geofences")
async def list_geofences() -> JSONResponse:
    """Geofence orders the ops UI pushed, as a GeoJSON FeatureCollection for the
    ATAK plugin to pull and draw on-device (bypassing FreeTAKServer, which strips
    drawing shapes). Each feature carries uid + label + honest remarks."""
    feats = [
        {
            "type": "Feature",
            "geometry": o["geometry"],
            "properties": {
                "uid": o["uid"],
                "label": o["label"],
                "remarks": o["remarks"],
                "cutoff": o.get("cutoff"),
                "feature_kind": "geofence_order",
                "t_unix_ns": o.get("t_unix_ns"),
            },
        }
        for o in app.state.geofences.values()
    ]
    return JSONResponse({"type": "FeatureCollection", "features": feats})


@app.delete("/geofences")
async def clear_geofences() -> dict[str, Any]:
    n = len(app.state.geofences)
    app.state.geofences = {}
    return {"cleared": n}


@app.get("/fixes/{fix_id}/posterior")
async def get_posterior(fix_id: UUID, emitter_h: float | None = None) -> JSONResponse:
    """RF-plausibility posterior. Optional ``emitter_h`` (m) lets the UI re-weight
    for a selected candidate's antenna height (mast vs trench)."""
    settings = _settings(app)
    store = _store(app)
    fix = store.get_fix(fix_id)
    if fix is None:
        raise HTTPException(status_code=404, detail=f"no fix with id {fix_id}")
    engine: PosteriorEngine = app.state.posterior
    nodes = nodes_for_fix(fix, store.list_bearings())
    fc = engine.posterior_geojson(
        fix,
        nodes,
        store.freq_for(fix_id),
        cell_m=settings.posterior_cell_m,
        buffer_m=settings.posterior_buffer_m,
        floor=settings.rf_shadow_floor,
        scale_db=settings.diffraction_loss_scale_db,
        emitter_h=emitter_h if emitter_h is not None else settings.emitter_antenna_h_m,
        node_h=settings.node_antenna_h_m,
    )
    return JSONResponse(fc)


@app.get("/fixes/{fix_id}/posterior/contour")
async def posterior_contour(
    fix_id: UUID, cutoff: float = 0.1, emitter_h: float | None = None
) -> JSONResponse:
    """RF-plausibility level-set at an absolute ``cutoff`` (fraction of peak,
    0-1): the region where plausibility >= cutoff. Backs the geofence slider's
    live preview — the operator dials the cutoff, sees the outline on the map,
    and sends exactly that as the ``Jx`` geofence (no probability-mass binning)."""
    settings = _settings(app)
    store = _store(app)
    fix = store.get_fix(fix_id)
    if fix is None:
        raise HTTPException(status_code=404, detail=f"no fix with id {fix_id}")
    engine: PosteriorEngine = app.state.posterior
    nodes = nodes_for_fix(fix, store.list_bearings())
    loop = asyncio.get_running_loop()
    fn = partial(
        engine.contour_geojson,
        fix,
        nodes,
        store.freq_for(fix_id),
        cutoff=_clamp(float(cutoff), 0.01, 1.0),
        cell_m=settings.posterior_cell_m,
        buffer_m=settings.posterior_buffer_m,
        floor=settings.rf_shadow_floor,
        scale_db=settings.diffraction_loss_scale_db,
        emitter_h=emitter_h if emitter_h is not None else settings.emitter_antenna_h_m,
        node_h=settings.node_antenna_h_m,
    )
    fc = await loop.run_in_executor(None, fn)
    return JSONResponse(fc)


@app.get("/fixes/{fix_id}/posterior/probe")
async def posterior_probe(
    fix_id: UUID,
    lat: float,
    lon: float,
    res: int | None = None,
    surface: str | None = None,
    emitter_h: float | None = None,
) -> JSONResponse:
    """The raw math behind the plausibility at one point (for the hover inspector):
    per-node AoA residual/σ/likelihood + per-node RF distance/clearance/Fresnel-v/
    loss/weight, the two products, and the normalized plausibility + band. Matches
    whatever layer is displayed — pass res+surface for an enhanced view, else the
    Copernicus default is used."""
    settings = _settings(app)
    store = _store(app)
    fix = store.get_fix(fix_id)
    if fix is None:
        raise HTTPException(status_code=404, detail=f"no fix with id {fix_id}")
    engine: PosteriorEngine = app.state.posterior
    mgr: EnhanceManager = app.state.enhance
    nodes = nodes_for_fix(fix, store.list_bearings())
    eh = emitter_h if emitter_h is not None else settings.emitter_antenna_h_m
    raster = None
    cell_m = settings.posterior_cell_m
    n = 24
    if res is not None and res in RES_GRID and surface in {"dtm", "dsm"}:
        w = await mgr.probe_window(fix, res, eh, surface, buffer_m=settings.posterior_buffer_m)
        if w is not None:
            raster = w
            cell_m = float(RES_GRID[res]["cell_m"])
            n = int(RES_GRID[res]["samples"])
    loop = asyncio.get_running_loop()
    fn = partial(
        engine.probe, fix, nodes, store.freq_for(fix_id), lat, lon,
        cell_m=cell_m, buffer_m=settings.posterior_buffer_m, floor=settings.rf_shadow_floor,
        scale_db=settings.diffraction_loss_scale_db, emitter_h=eh,
        node_h=settings.node_antenna_h_m, raster=raster, n_path_samples=n,
    )
    result = await loop.run_in_executor(None, fn)
    return JSONResponse(result)


@app.post("/fixes/{fix_id}/enhance")
async def enhance_fix(
    fix_id: UUID, res: int = 2, emitter_h: float | None = None, surface: str | None = None
) -> JSONResponse:
    """Kick an on-demand high-res (Wallonia 1 m LiDAR) recompute of this fix's
    chunk at scan density ``res`` m (1-4). ``surface`` picks bare-earth ``dtm``
    (MNT) or surface-with-buildings ``dsm`` (MNS); defaults to DSM when staged.
    Returns a job; poll the job endpoint. Falls back to Copernicus (honestly
    labelled) when the chosen LiDAR surface is unavailable."""
    if res not in RES_GRID:
        raise HTTPException(status_code=422, detail=f"res must be one of {sorted(RES_GRID)}")
    settings = _settings(app)
    store = _store(app)
    fix = store.get_fix(fix_id)
    if fix is None:
        raise HTTPException(status_code=404, detail=f"no fix with id {fix_id}")
    mgr: EnhanceManager = app.state.enhance
    surf = surface or mgr.default_surface() or "dsm"
    if surf not in {"dtm", "dsm"}:
        raise HTTPException(status_code=422, detail="surface must be 'dtm' or 'dsm'")
    nodes = nodes_for_fix(fix, store.list_bearings())
    eh = emitter_h if emitter_h is not None else settings.emitter_antenna_h_m
    job = mgr.submit(
        fix, nodes, store.freq_for(fix_id), res, eh,
        surface=surf,
        buffer_m=settings.posterior_buffer_m,
        floor=settings.rf_shadow_floor,
        scale_db=settings.diffraction_loss_scale_db,
        node_h=settings.node_antenna_h_m,
    )
    return JSONResponse(job, status_code=202)


@app.get("/enhance/jobs/{job_id}")
async def enhance_job(job_id: str) -> JSONResponse:
    job = app.state.enhance.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no enhance job {job_id}")
    return JSONResponse(job)


@app.post("/enhance/jobs/{job_id}/cancel")
async def enhance_cancel(job_id: str) -> dict[str, Any]:
    ok = app.state.enhance.cancel(job_id)
    if not ok:
        raise HTTPException(status_code=409, detail="job not cancellable (missing or finished)")
    return {"cancelled": True}


@app.get("/enhance/options")
async def enhance_options() -> dict[str, Any]:
    """Scan-density choices + which LiDAR surfaces are staged (for the UI badge)."""
    mgr: EnhanceManager = app.state.enhance
    surfaces = []
    for key in ("dsm", "dtm"):
        src = mgr.sources.get(key)
        if src is None:
            continue
        surfaces.append({
            "surface": key,
            "label": src.label,
            "available": src.available,
            "detail": src.label if src.available else (src.error or "not staged"),
        })
    avail = mgr.available_surfaces()
    return {
        "lidar_available": bool(avail),
        "default_surface": mgr.default_surface(),
        "surfaces": surfaces,
        "options": [
            {"res_m": r, "eta_s": RES_GRID[r]["eta_s"]} for r in sorted(RES_GRID)
        ],
    }


@app.get("/fixes/{fix_id}/investigate")
async def get_investigate(fix_id: UUID) -> JSONResponse:
    """Ranked candidate equipment + targets + deployment for a fix (two planes:
    measured vs inferred, with a mandatory UNKNOWN mass). See inference.py."""
    settings = _settings(app)
    store = _store(app)
    fix = store.get_fix(fix_id)
    if fix is None:
        raise HTTPException(status_code=404, detail=f"no fix with id {fix_id}")
    emitter_class = fix.emitter_class.value if fix.emitter_class else None
    catalog = load_catalog(settings.equipment_catalog_file)
    result = investigate(
        store.freq_for(fix_id),
        None,  # occupied_bw_hz not carried by FixEvent; reserved
        emitter_class,
        catalog=catalog,
    )
    return JSONResponse(result.as_dict())


# --------------------------------------------------------------------------- #
# Exposure lenses (concealment / leakage / jam-shadow) — ADR-016/017.
# Deployment-layer only; no contract change. Red nodes come in the request body
# (operator-placed or, from S2, the mesh's own jammer fixes).
# --------------------------------------------------------------------------- #


def _exposure_reds(mode: str, payload: dict[str, Any]) -> list[_Red]:
    """Parse the request's red nodes into ``_Red``. A node with no ``role``
    defaults to the lens's canonical role (jammer for jam-shadow, sensor else),
    so a bare {lat,lon} is eligible without the caller knowing the role gate."""
    default_role = "jammer" if mode == "jamshadow" else "recon"
    reds: list[_Red] = []
    for r in payload.get("reds", []) or []:
        reds.append(
            _Red(
                lat=float(r["lat"]),
                lon=float(r["lon"]),
                h_m=float(r.get("h_m", 3.0)),
                role=str(r.get("role", default_role)),
                erp_class=str(r.get("erp_class", "medium")),
                node_id=str(r.get("node_id", "")),
            )
        )
    return reds


def _marker_to_red(m: dict[str, Any]) -> _Red:
    return _Red(
        lat=float(m["lat"]), lon=float(m["lon"]), h_m=float(m.get("h_m", 3.0)),
        role=str(m.get("role", "jammer")), erp_class=str(m.get("erp_class", "medium")),
        node_id=str(m.get("node_id") or m.get("id", "")),
    )


def _auto_jammer_reds() -> list[_Red]:
    """Red jammers derived from the mesh's own fixes: each fix whose top catalog
    candidate is ``role=jammer`` becomes a jam-shadow source at the fix position,
    inheriting the candidate's ERP class. Only *emitting* red is detectable this
    way — passive collectors must be operator-placed (you can't detect a receiver)."""
    settings = _settings(app)
    store = _store(app)
    catalog = load_catalog(settings.equipment_catalog_file)
    out: list[_Red] = []
    for fix in store.list_fixes():
        ec = fix.emitter_class.value if fix.emitter_class else None
        res = investigate(store.freq_for(fix.fix_id), None, ec, catalog=catalog)
        top = next((c for c in res.candidates if not c.is_unknown), None)
        if top is None or top.role != "jammer":
            continue
        h = float(top.antenna_height_class_m.get("typ", 3.0)) if top.antenna_height_class_m else 3.0
        out.append(_Red(
            lat=fix.position.lat_deg, lon=fix.position.lon_deg, h_m=h, role="jammer",
            erp_class=top.erp_class or "medium", node_id=f"fix:{str(fix.fix_id)[:8]}",
        ))
    return out


def _gather_reds(mode: str, payload: dict[str, Any]) -> list[_Red]:
    """Body reds + (optional) operator-placed markers + (optional, jam-shadow)
    mesh-derived jammers. The lens role gate drops anything ineligible."""
    reds = _exposure_reds(mode, payload)
    if payload.get("include_stored"):
        reds += [_marker_to_red(m) for m in _store(app).list_reds()]
    if payload.get("include_auto") and mode == "jamshadow":
        reds += _auto_jammer_reds()
    return reds


def _red_as_dict(r: _Red) -> dict[str, Any]:
    return {
        "lat": r.lat, "lon": r.lon, "h_m": r.h_m, "role": r.role,
        "erp_class": r.erp_class, "node_id": r.node_id,
    }


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


async def _pick_exposure_raster(
    payload: dict[str, Any], bands: list[float], center_lat: float, center_lon: float,
    reach_m: float,
) -> tuple[Any, dict[str, Any]]:
    """Choose the AOI raster: Copernicus by default, or a synchronously-read
    LiDAR window (in an executor) when ``use_lidar`` and the finest band's
    surface is staged. Returns (raster, fidelity-dict). Honest degrade on miss."""
    engine: PosteriorEngine = app.state.posterior
    mgr: EnhanceManager = app.state.enhance
    requested = finest_band_surface(bands)
    fidelity: dict[str, Any] = {
        "requested_surface": requested["surface"],
        "requested_res_m": requested["res"],
        "needs_dsm": requested["needs_dsm"],
        "lidar": False,
    }
    raster = engine.base
    if bool(payload.get("use_lidar", False)):
        surf = str(payload.get("surface") or requested["surface"])
        src = mgr.sources.get(surf)
        if src is not None and src.available:
            loop = asyncio.get_running_loop()
            lid = await loop.run_in_executor(
                None, src.window, center_lat, center_lon, reach_m,
                float(requested["res"]), engine.base,
            )
            if lid is not None:
                raster, fidelity["lidar"], fidelity["surface_used"] = lid, True, surf
            else:
                fidelity["lidar_note"] = "LiDAR window empty/outside coverage; using Copernicus"
        else:
            fidelity["lidar_note"] = f"surface {surf!r} not staged; using Copernicus"
    return raster, fidelity


@app.post("/exposure")
async def exposure(payload: Any = Body(...)) -> JSONResponse:
    """Concealment / leakage / jam-shadow heatmap for an AOI given a set of red
    nodes + bands. Relative terrain cover only — never a detectability/safe claim
    (see posterior.py). Use ``use_lidar`` for the staged 1 m surface."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="body must be a JSON object")
    settings = _settings(app)
    engine: PosteriorEngine = app.state.posterior
    mode = str(payload.get("mode", "jamshadow"))
    if mode not in LENS_CONFIG:
        raise HTTPException(status_code=422, detail=f"mode must be one of {sorted(LENS_CONFIG)}")
    bands = [float(b) for b in (payload.get("bands_hz") or [])]
    if not bands:
        raise HTTPException(status_code=422, detail="bands_hz required (non-empty list)")
    if "center_lat" not in payload or "center_lon" not in payload:
        raise HTTPException(status_code=422, detail="center_lat and center_lon required")
    center_lat = float(payload["center_lat"])
    center_lon = float(payload["center_lon"])
    reach_m = _clamp(float(payload.get("reach_m", 3000.0)), 200.0, 6000.0)
    cell_m = max(20.0, float(payload.get("cell_m", settings.posterior_cell_m)))
    asset_h = float(payload.get("asset_h_m", settings.emitter_antenna_h_m))
    reds = _gather_reds(mode, payload)

    raster, fidelity = await _pick_exposure_raster(payload, bands, center_lat, center_lon, reach_m)
    loop = asyncio.get_running_loop()
    fn = partial(
        engine.exposure_geojson, mode, reds, bands,
        center_lat=center_lat, center_lon=center_lon, reach_m=reach_m, cell_m=cell_m,
        floor=settings.rf_shadow_floor, scale_db=settings.diffraction_loss_scale_db,
        asset_h_m=asset_h, n_path_samples=24, band_reduce=payload.get("band_reduce"),
        polarity=str(payload.get("polarity", "safe")), raster=raster,
    )
    fc = await loop.run_in_executor(None, fn)
    active_res = float(getattr(raster, "res_m", 0.0)) if raster is not None else 0.0
    if fidelity["needs_dsm"] and active_res > 5.0:
        fc["properties"]["fidelity_warning"] = (
            f"high band needs ~{fidelity['requested_res_m']} m DSM but rendered on "
            f"{active_res:.0f} m {fc['properties'].get('dem_source', 'coarse DEM')} — "
            "treat as coarse/degraded (B3)"
        )
    fc["properties"]["fidelity"] = fidelity
    return JSONResponse(fc)


@app.post("/exposure/probe")
async def exposure_probe_ep(payload: Any = Body(...)) -> JSONResponse:
    """Hover inspector: per-red-node distance/clearance/Fresnel-v/loss/transmission
    at one cell, the unioned exposure, and the signed survival."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="body must be a JSON object")
    settings = _settings(app)
    engine: PosteriorEngine = app.state.posterior
    mode = str(payload.get("mode", "jamshadow"))
    if mode not in LENS_CONFIG:
        raise HTTPException(status_code=422, detail=f"mode must be one of {sorted(LENS_CONFIG)}")
    bands = [float(b) for b in (payload.get("bands_hz") or [])]
    if not bands or "lat" not in payload or "lon" not in payload:
        raise HTTPException(status_code=422, detail="bands_hz, lat, lon required")
    reds = _gather_reds(mode, payload)
    asset_h = float(payload.get("asset_h_m", settings.emitter_antenna_h_m))
    reach_m = _clamp(float(payload.get("reach_m", 3000.0)), 200.0, 6000.0)
    raster, _ = await _pick_exposure_raster(
        payload, bands, float(payload["lat"]), float(payload["lon"]), reach_m
    )
    result = engine.exposure_probe(
        mode, reds, bands, float(payload["lat"]), float(payload["lon"]),
        floor=settings.rf_shadow_floor, scale_db=settings.diffraction_loss_scale_db,
        asset_h_m=asset_h, raster=raster,
    )
    return JSONResponse(result)


@app.post("/exposure/combined")
async def exposure_combined(payload: Any = Body(...)) -> JSONResponse:
    """The 'ideal site' lens: concealed from red sensors AND in a jammer's terrain
    shadow for our links (intersection). Red set must contain both sensors
    (role df/recon) and jammers (role jammer)."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="body must be a JSON object")
    if "center_lat" not in payload or "center_lon" not in payload:
        raise HTTPException(status_code=422, detail="center_lat and center_lon required")
    settings = _settings(app)
    engine: PosteriorEngine = app.state.posterior
    bands = [float(b) for b in (payload.get("bands_hz") or [])]
    conceal_bands = [float(b) for b in (payload.get("conceal_bands_hz") or bands)]
    jam_bands = [float(b) for b in (payload.get("jam_bands_hz") or bands)]
    if not conceal_bands or not jam_bands:
        raise HTTPException(
            status_code=422,
            detail="provide bands_hz, or both conceal_bands_hz and jam_bands_hz",
        )
    center_lat = float(payload["center_lat"])
    center_lon = float(payload["center_lon"])
    reach_m = _clamp(float(payload.get("reach_m", 3000.0)), 200.0, 6000.0)
    cell_m = max(20.0, float(payload.get("cell_m", settings.posterior_cell_m)))
    asset_h = float(payload.get("asset_h_m", settings.emitter_antenna_h_m))
    # combined needs both roles; gather body reds (roles preserved), markers, and
    # mesh-derived jammers, then let combined_geojson split them by role.
    reds = _gather_reds("jamshadow", payload)
    raster, fidelity = await _pick_exposure_raster(
        payload, conceal_bands + jam_bands, center_lat, center_lon, reach_m
    )
    loop = asyncio.get_running_loop()
    fn = partial(
        engine.combined_geojson, reds,
        conceal_bands_hz=conceal_bands, jam_bands_hz=jam_bands,
        center_lat=center_lat, center_lon=center_lon, reach_m=reach_m, cell_m=cell_m,
        floor=settings.rf_shadow_floor, scale_db=settings.diffraction_loss_scale_db,
        asset_h_m=asset_h, n_path_samples=24,
        polarity=str(payload.get("polarity", "safe")), raster=raster,
    )
    fc = await loop.run_in_executor(None, fn)
    fc["properties"]["fidelity"] = fidelity
    return JSONResponse(fc)


@app.get("/exposure/options")
async def exposure_options() -> dict[str, Any]:
    """Lens modes, the catalog's bands (with the recommended per-band LiDAR
    surface/res + ``needs_dsm`` honesty flag), and which surfaces are staged."""
    settings = _settings(app)
    mgr: EnhanceManager = app.state.enhance
    catalog = load_catalog(settings.equipment_catalog_file)
    bands: list[dict[str, Any]] = []
    for key, gate in catalog.band_gate.items():
        hz = gate.get("hz", [])
        if not hz:
            continue
        lo, hi = float(hz[0][0]), float(hz[-1][1])
        center = (float(hz[0][0]) + float(hz[0][1])) / 2.0
        bands.append({
            "key": key, "label": key, "lo_hz": lo, "hi_hz": hi, "center_hz": center,
            "multi_use": bool(gate.get("multi_use", False)),
            **surface_res_for_band(center),
        })
    surfaces = []
    for skey in ("dsm", "dtm"):
        src = mgr.sources.get(skey)
        if src is None:
            continue
        surfaces.append({"surface": skey, "label": src.label, "available": src.available})
    return {
        "modes": list(LENS_CONFIG),
        "bands": bands,
        "lidar": {
            "available": bool(mgr.available_surfaces()),
            "default_surface": mgr.default_surface(),
            "surfaces": surfaces,
        },
    }


@app.get("/exposure/reds")
async def list_exposure_reds() -> dict[str, Any]:
    """Operator-placed red markers + the mesh-derived jammers (read-only) so the
    UI can show both source kinds and let the operator toggle each."""
    return {
        "manual": _store(app).list_reds(),
        "auto": [_red_as_dict(r) for r in _auto_jammer_reds()],
    }


@app.post("/exposure/reds")
async def add_exposure_red(payload: Any = Body(...)) -> dict[str, Any]:
    """Place a red node (sensor or jammer) for what-if siting."""
    if not isinstance(payload, dict) or "lat" not in payload or "lon" not in payload:
        raise HTTPException(status_code=422, detail="lat and lon required")
    store = _store(app)
    red = {
        "lat": float(payload["lat"]),
        "lon": float(payload["lon"]),
        "h_m": float(payload.get("h_m", 3.0)),
        "role": str(payload.get("role", "jammer")),
        "erp_class": str(payload.get("erp_class", "medium")),
        "node_id": str(payload.get("node_id", "")),
        "bands_hz": [float(b) for b in (payload.get("bands_hz") or [])],
    }
    red_id = store.add_red(red)
    return {"id": red_id, "reds": store.list_reds()}


@app.delete("/exposure/reds/{red_id}")
async def delete_exposure_red(red_id: str) -> dict[str, Any]:
    if not _store(app).delete_red(red_id):
        raise HTTPException(status_code=404, detail=f"no red marker {red_id}")
    return {"deleted": True, "reds": _store(app).list_reds()}


@app.delete("/exposure/reds")
async def clear_exposure_reds() -> dict[str, Any]:
    _store(app).clear_reds()
    return {"cleared": True}


# Static frontend last so it does not shadow the API routes above.
_frontend_dir = Settings.from_env().frontend_dir
if _frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
