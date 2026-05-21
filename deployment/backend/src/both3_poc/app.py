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
from .geojson import bearings_feature_collection, fixes_feature_collection
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
    app.state.posterior = PosteriorEngine(settings.dem_file)
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
async def get_posterior(fix_id: UUID) -> JSONResponse:
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
        emitter_h=settings.emitter_antenna_h_m,
        node_h=settings.node_antenna_h_m,
    )
    return JSONResponse(fc)


# Static frontend last so it does not shadow the API routes above.
_frontend_dir = Settings.from_env().frontend_dir
if _frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
