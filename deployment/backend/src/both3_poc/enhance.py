"""On-demand LiDAR *Enhance*: windowed high-res recompute of a fix's posterior.

The default RF-plausibility runs on coarse Copernicus GLO-30 (30 m). This module
recomputes a *single fix's chunk* against a pre-staged **Wallonia 1 m LiDAR DSM**
(``data/lidar_aoi.tif``, warped to EPSG:4326 once at staging — see
``stage_lidar.py``), at a caller-chosen scan density (1-4 m). It is the slow,
user-facing step, so it runs as an async job with phased progress + cancel.

Honesty (B3): if the LiDAR raster is missing, the chunk is outside its coverage,
or a read fails, the job finishes against Copernicus and stamps the source
``copernicus-30m (lidar unavailable: <reason>)`` — 30 m data is **never** labelled
as 1 m.

Architecture notes (council-reviewed):
- The compute is fully vectorized numpy (GIL released in the heavy ufuncs), so a
  default ``ThreadPoolExecutor`` parallelizes fine and never blocks the event
  loop — no ProcessPool / pickling needed for this PoC.
- Bounded concurrency (semaphore) + a per-job deadline are the load-bearing
  safeguards against a jury hammering Enhance; idempotency dedups in-flight
  duplicates; results cache by ``(fix_id, res, emitter_h)`` to disk + memory.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .posterior import (
    PosteriorCancelled,
    PosteriorEngine,
    Raster,
    _Node,
)

# res (m) -> grid cell size + path-sample density + a rough ETA for the UI.
# ETAs are measured on the staged AOI (vectorized compute): finest ~1-2 s, others
# sub-second. Kept slightly conservative to cover cold-cache / VPS variance.
RES_GRID: dict[int, dict[str, Any]] = {
    1: {"cell_m": 30.0, "samples": 256, "eta_s": 3},
    2: {"cell_m": 40.0, "samples": 160, "eta_s": 2},
    3: {"cell_m": 50.0, "samples": 96, "eta_s": 1},
    4: {"cell_m": 60.0, "samples": 64, "eta_s": 1},
}

_TERMINAL = {"done", "cancelled", "error"}


class _Canceller:
    """``is_set()`` trips on explicit cancel OR on the per-job deadline."""

    def __init__(self, deadline: float) -> None:
        self._deadline = deadline
        self._cancelled = False
        self.timed_out = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_set(self) -> bool:
        if self._cancelled:
            return True
        if time.monotonic() > self._deadline:
            self.timed_out = True
            return True
        return False


class LidarSource:
    """Lazily-opened, pre-staged 1 m LiDAR raster (EPSG:4326 COG).

    ``source_id`` is stamped onto the result so the UI labels honestly which
    surface produced the heat (bare-earth MNT vs surface-with-buildings MNS).
    """

    def __init__(
        self,
        path: Path,
        native_res_m: float = 1.0,
        source_id: str = "wallonia-lidar-1m",
        label: str = "Wallonia LiDAR 1 m",
    ) -> None:
        self.path = path
        self.native_res_m = native_res_m
        self.source_id = source_id
        self.label = label
        self._checked = False
        self._ok = False
        self._err = ""

    @property
    def available(self) -> bool:
        if not self._checked:
            self._checked = True
            try:
                if not self.path.exists():
                    raise FileNotFoundError(f"{self.path} not present")
                import rasterio  # noqa: PLC0415

                with rasterio.open(self.path):
                    pass
                self._ok = True
            except Exception as exc:  # noqa: BLE001
                self._err = str(exc)
                self._ok = False
        return self._ok

    @property
    def error(self) -> str:
        return self._err

    def window(
        self,
        lat0: float,
        lon0: float,
        reach_m: float,
        res_m: float,
        fill_base: Raster | None,
    ) -> Raster | None:
        """Windowed read over the fix bbox, coarsened to ``res_m`` with
        ``Resampling.max`` (preserves the ridge/rooftop peaks that cause the
        diffraction — averaging would understate obstruction). Nodata is filled
        from the Copernicus base so chunk edges stay sane. Returns ``None`` if
        the window is empty / outside coverage."""
        if not self.available:
            return None
        try:
            import rasterio  # noqa: PLC0415
            from rasterio.enums import Resampling  # noqa: PLC0415
            from rasterio.windows import from_bounds  # noqa: PLC0415

            m_per_deg_lat = 111_320.0
            m_per_deg_lon = m_per_deg_lat * np.cos(np.radians(lat0))
            pad = reach_m * 1.08
            north = lat0 + pad / m_per_deg_lat
            south = lat0 - pad / m_per_deg_lat
            east = lon0 + pad / m_per_deg_lon
            west = lon0 - pad / m_per_deg_lon
            out_n = max(16, int(round(2 * pad / res_m)))
            with rasterio.open(self.path) as ds:
                win = from_bounds(west, south, east, north, ds.transform)
                if win.width < 1 or win.height < 1:
                    return None
                data = ds.read(
                    1,
                    window=win,
                    out_shape=(out_n, out_n),
                    resampling=Resampling.max,
                    boundless=True,
                    fill_value=(ds.nodata if ds.nodata is not None else 0.0),
                ).astype(np.float64)
                nodata = ds.nodata
            valid = np.isfinite(data)
            if nodata is not None:
                valid &= data != nodata
            if valid.sum() == 0:
                return None
            px = (east - west) / out_n
            py = -(north - south) / out_n
            rast = Raster(
                z=data, ox=west, oy=north, px=px, py=py,
                source=self.source_id, res_m=res_m,
            )
            if not valid.all():
                # fill nodata holes from Copernicus (or median) so paths stay continuous
                rows, cols = np.where(~valid)
                if fill_base is not None:
                    lat = north + (rows + 0.5) * py
                    lon = west + (cols + 0.5) * px
                    data[rows, cols] = fill_base.terrain(lat, lon)
                else:
                    data[rows, cols] = float(np.median(data[valid]))
            return rast
        except Exception as exc:  # noqa: BLE001
            self._err = str(exc)
            return None


class EnhanceManager:
    """Owns the job registry, concurrency bound, cache, and the run loop."""

    def __init__(
        self,
        engine: PosteriorEngine,
        sources: dict[str, LidarSource],
        *,
        cache_dir: Path,
        max_concurrent: int = 2,
        timeout_s: float = 30.0,
    ) -> None:
        self.engine = engine
        self.sources = sources  # {"dtm": LidarSource, "dsm": LidarSource}
        self.cache_dir = cache_dir
        self.timeout_s = timeout_s
        self._sem = asyncio.Semaphore(max_concurrent)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancellers: dict[str, _Canceller] = {}
        self._mem_cache: dict[str, dict[str, Any]] = {}
        self._window_cache: dict[str, Raster] = {}  # reuse the LiDAR window for hover probes
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass

    def available_surfaces(self) -> list[str]:
        """Surface keys whose COG is actually staged, in preference order."""
        return [k for k in ("dsm", "dtm") if k in self.sources and self.sources[k].available]

    def default_surface(self) -> str | None:
        avail = self.available_surfaces()
        return avail[0] if avail else None

    async def probe_window(
        self, fix: Any, res: int, emitter_h: float, surface: str, *, buffer_m: float
    ) -> Raster | None:
        """The LiDAR window raster matching a displayed enhanced layer, for the
        hover probe. Reuses the window cached by the last enhance run; reads it
        once (off-thread) on a miss. Returns None -> caller uses Copernicus."""
        src = self.sources.get(surface)
        if res not in RES_GRID or src is None or not src.available:
            return None
        key = self._key(str(fix.fix_id), res, emitter_h, surface)
        rast = self._window_cache.get(key)
        if rast is None:
            ell = fix.confidence_ellipse_95
            reach_m = ell.semi_major_m + buffer_m
            loop = asyncio.get_running_loop()
            rast = await loop.run_in_executor(
                None, src.window,
                fix.position.lat_deg, fix.position.lon_deg, reach_m, float(res),
                self.engine.base,
            )
            if rast is not None:
                if len(self._window_cache) > 16:
                    self._window_cache.clear()
                self._window_cache[key] = rast
        return rast

    # ---- cache helpers ----

    @staticmethod
    def _key(fix_id: str, res: int, emitter_h: float, surface: str) -> str:
        return f"{fix_id}:{res}:{emitter_h:.1f}:{surface}"

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / (key.replace(":", "_") + ".json")

    def _load_cache(self, key: str) -> dict[str, Any] | None:
        if key in self._mem_cache:
            return self._mem_cache[key]
        p = self._cache_path(key)
        if p.exists():
            try:
                fc = json.loads(p.read_text())
                self._mem_cache[key] = fc
                return fc
            except Exception:  # noqa: BLE001
                return None
        return None

    def _store_cache(self, key: str, fc: dict[str, Any]) -> None:
        self._mem_cache[key] = fc
        try:
            self._cache_path(key).write_text(json.dumps(fc))
        except Exception:  # noqa: BLE001
            pass

    def _prune(self) -> None:
        now = time.time()
        dead = [
            jid for jid, j in self._jobs.items()
            if j["state"] in _TERMINAL and now - j.get("finished_at", now) > 300
        ]
        for jid in dead:
            self._jobs.pop(jid, None)
            self._cancellers.pop(jid, None)

    def public(self, job: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in job.items() if k not in {"_fc"}}
        if job["state"] == "done":
            out["result"] = job.get("_fc")
        return out

    def get(self, job_id: str) -> dict[str, Any] | None:
        j = self._jobs.get(job_id)
        return self.public(j) if j else None

    def cancel(self, job_id: str) -> bool:
        c = self._cancellers.get(job_id)
        j = self._jobs.get(job_id)
        if c is None or j is None or j["state"] in _TERMINAL:
            return False
        c.cancel()
        j["state"] = "cancelling"
        return True

    # ---- submission + run ----

    def submit(
        self,
        fix: Any,
        nodes: list[_Node],
        freq: float | None,
        res: int,
        emitter_h: float,
        *,
        surface: str,
        buffer_m: float,
        floor: float,
        scale_db: float,
        node_h: float,
    ) -> dict[str, Any]:
        self._prune()
        key = self._key(str(fix.fix_id), res, emitter_h, surface)

        cached = self._load_cache(key)
        if cached is not None:
            jid = uuid.uuid4().hex[:12]
            job = {
                "job_id": jid, "key": key, "state": "done", "phase": "done",
                "progress": 100, "degraded": cached.get("properties", {}).get("degraded", False),
                "detail": "cached", "res_m": res, "surface": surface, "cached": True,
                "created_at": time.time(), "finished_at": time.time(), "_fc": cached,
            }
            self._jobs[jid] = job
            return self.public(job)

        # idempotency: an in-flight job for the same key -> return it
        for jid, j in self._jobs.items():
            if j["key"] == key and j["state"] not in _TERMINAL:
                return self.public(j)

        jid = uuid.uuid4().hex[:12]
        job: dict[str, Any] = {
            "job_id": jid, "key": key, "state": "queued", "phase": "queued",
            "progress": 0, "degraded": False, "detail": None, "res_m": res,
            "surface": surface, "cached": False,
            "created_at": time.time(), "finished_at": None, "_fc": None,
        }
        self._jobs[jid] = job
        asyncio.create_task(
            self._run(job, fix, nodes, freq, res, emitter_h, surface,
                      buffer_m=buffer_m, floor=floor, scale_db=scale_db, node_h=node_h)
        )
        return self.public(job)

    async def _run(
        self,
        job: dict[str, Any],
        fix: Any,
        nodes: list[_Node],
        freq: float | None,
        res: int,
        emitter_h: float,
        surface: str,
        *,
        buffer_m: float,
        floor: float,
        scale_db: float,
        node_h: float,
    ) -> None:
        async with self._sem:
            if job["state"] == "cancelling":
                job.update(state="cancelled", finished_at=time.time())
                return
            loop = asyncio.get_running_loop()
            canceller = _Canceller(deadline=time.monotonic() + self.timeout_s)
            self._cancellers[job["job_id"]] = canceller
            grid = RES_GRID.get(res, RES_GRID[2])
            cell_m = float(grid["cell_m"])
            samples = int(grid["samples"])

            src = self.sources.get(surface)
            try:
                # ---- phase: fetch (windowed LiDAR read) ----
                job.update(state="running", phase="fetch", progress=4)
                ell = fix.confidence_ellipse_95
                reach_m = ell.semi_major_m + buffer_m
                raster: Raster | None = None
                if src is not None and src.available:
                    raster = await loop.run_in_executor(
                        None,
                        src.window,
                        fix.position.lat_deg, fix.position.lon_deg, reach_m, float(res),
                        self.engine.base,
                    )
                degraded = raster is None
                reason = ""
                if not degraded and raster is not None:
                    if len(self._window_cache) > 16:
                        self._window_cache.clear()
                    self._window_cache[job["key"]] = raster  # for hover probes
                if degraded:
                    raster = self.engine.base
                    if src is None:
                        reason = f"surface '{surface}' not configured"
                    elif not src.available:
                        reason = f"{surface.upper()} raster not staged"
                    else:
                        reason = src.error or "no LiDAR coverage for this chunk"
                job.update(phase="resample", progress=14, degraded=degraded)

                # ---- phase: compute ----
                job.update(phase="compute", progress=20)

                def progress_cb(frac: float) -> None:
                    job["progress"] = int(20 + 78 * max(0.0, min(1.0, frac)))

                def work() -> dict[str, Any]:
                    return self.engine.posterior_geojson(
                        fix, nodes, freq,
                        cell_m=cell_m, buffer_m=buffer_m, floor=floor, scale_db=scale_db,
                        emitter_h=emitter_h, node_h=node_h,
                        raster=raster, n_path_samples=samples,
                        progress_cb=progress_cb, cancel=canceller,
                    )

                fc = await loop.run_in_executor(None, work)

                if degraded:
                    fc["properties"]["dem_source"] = f"copernicus-30m (lidar unavailable: {reason})"
                    fc["properties"]["degraded"] = True
                    fc["properties"]["dem_res_m"] = 30.0
                    fc["properties"]["surface_label"] = "Copernicus GLO-30"
                else:
                    fc["properties"]["degraded"] = False
                    fc["properties"]["surface_label"] = src.label if src else ""
                fc["properties"]["enhanced"] = not degraded
                fc["properties"]["surface"] = surface
                fc["properties"]["scan_density_m"] = res

                if not degraded:
                    self._store_cache(job["key"], fc)
                job.update(
                    state="done", phase="done", progress=100, _fc=fc,
                    finished_at=time.time(),
                    detail=("degraded to Copernicus" if degraded else "ok"),
                )
            except PosteriorCancelled:
                if canceller.timed_out:
                    job.update(state="error", phase="compute",
                               detail=f"timed out after {self.timeout_s:.0f}s",
                               finished_at=time.time())
                else:
                    job.update(state="cancelled", phase="compute",
                               detail="cancelled", finished_at=time.time())
            except Exception as exc:  # noqa: BLE001
                job.update(state="error", detail=str(exc), finished_at=time.time())
