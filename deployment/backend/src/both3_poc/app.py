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

import time
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from rfmesh_contracts import BearingReport
from rfmesh_cot import CotError

from .config import Settings
from .cot_send import CotSender
from .enhance import RES_GRID, EnhanceManager, LidarSource
from .geojson import bearings_feature_collection, fixes_feature_collection
from .inference import investigate, load_catalog
from .posterior import PosteriorEngine, nodes_for_fix
from .seed import load_seed_bearings, load_seed_fixes_with_freq, parse_fix_and_freq
from .store import Store


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
    try:
        yield
    finally:
        await sender.aclose()


app = FastAPI(title="both3-poc", version="0.1.0", lifespan=lifespan)


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
    for i, rec in enumerate(_as_list(payload)):
        try:
            store.upsert_bearing(BearingReport.model_validate(rec))
            accepted += 1
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"[{i}] {exc}")
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


# Static frontend last so it does not shadow the API routes above.
_frontend_dir = Settings.from_env().frontend_dir
if _frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
