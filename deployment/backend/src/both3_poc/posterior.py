"""Grid-posterior RF-shadow layer.

Turns a single ``FixEvent`` (+ its contributing bearings + a terrain raster) into
a probability heatmap of where the emitter plausibly is. Honest by construction:

    P(cell) ∝ AoA_likelihood(cell) × RF_path_weight(cell)

- **AoA_likelihood** reproduces the bearing geometry per cell (the ellipse, but
  on a grid). Falls back to the fix's covariance ellipse when no bearings exist.
- **RF_path_weight** is a *soft* terrain term from single-knife-edge diffraction
  (ITU-R P.526) along each cell→sensor path over the terrain raster. Shadowed
  cells are down-weighted to a floor, **never zeroed** (diffraction fills
  shadows). The frequency dependence is intrinsic: the Fresnel parameter ``v``
  scales with ``1/sqrt(lambda)``, so sub-GHz shadows are mild and 5.8 GHz shadows
  are sharp, with no hand-tuned per-band factor.

The terrain raster is **pluggable**: the default is the bundled Copernicus
GLO-30 (30 m) DEM; the on-demand *Enhance* path feeds a windowed, downsampled
Wallonia 1 m LiDAR raster (see ``enhance.py``) at a caller-chosen cell size and
path-sample density. Both rasters are sampled in lon/lat (EPSG:4326); the LiDAR
window is warped to 4326 once at staging time so this sampler stays uniform.

Output is a GeoJSON FeatureCollection of nested highest-density bands (50/80/95 %
of the probability mass), drawn *under* the ellipse. No centroid, no hard mask.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np

_C = 299_792_458.0  # speed of light, m/s
_M_PER_DEG_LAT = 111_320.0
_DEFAULT_FREQ_HZ = 1.0e9  # used only when a fix carries no frequency annotation


class _Cancellable(Protocol):
    def is_set(self) -> bool: ...


class PosteriorCancelled(Exception):
    """Raised inside the compute when a caller's cancel flag trips."""


def _m_per_deg_lon(lat_deg: float) -> float:
    return _M_PER_DEG_LAT * math.cos(math.radians(lat_deg))


def _ang_diff(a: float, b: float) -> float:
    """Smallest absolute difference between two azimuths, degrees [0,180]."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


@dataclass
class _Node:
    lat: float
    lon: float
    azimuth_deg: float
    azimuth_sigma_deg: float
    node_id: str = ""


@dataclass
class Raster:
    """A terrain-height grid sampled in lon/lat with a north-up affine.

    ``px`` is the lon step (deg/px, > 0), ``py`` the lat step (deg/px, < 0 for a
    north-up raster). ``(ox, oy)`` is the top-left corner.
    """

    z: np.ndarray
    ox: float
    oy: float
    px: float
    py: float
    source: str = "copernicus-30m"
    res_m: float = 30.0

    @property
    def h(self) -> int:
        return self.z.shape[0]

    @property
    def w(self) -> int:
        return self.z.shape[1]

    def terrain(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        """Nearest-cell terrain height for arrays of lat/lon (0.0 outside grid)."""
        col = ((lon - self.ox) / self.px).astype(int)
        row = ((lat - self.oy) / self.py).astype(int)
        inside = (row >= 0) & (row < self.h) & (col >= 0) & (col < self.w)
        out = np.zeros(lat.shape, dtype=np.float64)
        out[inside] = self.z[row[inside], col[inside]]
        return out

    @property
    def cell_deg_lat(self) -> float:
        return abs(self.py)


def _j_v(v_max: np.ndarray) -> np.ndarray:
    """ITU-R P.526 single-knife-edge loss J(v) in dB, vectorized, >= 0."""
    loss = np.where(
        v_max <= -0.78,
        0.0,
        6.9 + 20.0 * np.log10(np.sqrt((v_max - 0.1) ** 2 + 1.0) + v_max - 0.1),
    )
    return np.maximum(loss, 0.0)


class PosteriorEngine:
    """Loads the default DEM once; computes a per-fix posterior on demand."""

    def __init__(self, dem_file: Path) -> None:
        self._base: Raster | None = None
        self._err = ""
        # per-layer cache for the hover probe: key -> {raw_max, thresholds}
        self._probe_cache: dict[str, dict[str, Any]] = {}
        try:
            import rasterio  # noqa: PLC0415

            with rasterio.open(dem_file) as ds:
                z = ds.read(1).astype(np.float64)
                t = ds.transform
                nodata = ds.nodata
            if nodata is not None:
                z[z == nodata] = 0.0
            self._base = Raster(z=z, ox=t.c, oy=t.f, px=t.a, py=t.e,
                                source="copernicus-30m", res_m=30.0)
        except Exception as exc:  # noqa: BLE001 - DEM optional; degrade to no-shadow
            self._err = str(exc)

    @property
    def available(self) -> bool:
        return self._base is not None

    @property
    def base(self) -> Raster | None:
        return self._base

    # ---- terrain helpers (operate on whichever raster is active) ----

    def _dominant_obstruction(
        self,
        raster: Raster,
        clat: float,
        clon: float,
        nodes: list[_Node],
        emitter_h: float,
        node_h: float,
        n: int = 48,
    ) -> dict[str, Any] | None:
        """The single worst terrain blocker on the emitter-center → sensor paths."""
        if not nodes:
            return None
        best: tuple[float, float, float, float, str] | None = None
        for nd in nodes:
            fr = np.linspace(0.0, 1.0, n)
            lats = clat + (nd.lat - clat) * fr
            lons = clon + (nd.lon - clon) * fr
            terr = raster.terrain(lats, lons)
            if terr.shape[0] < 3:
                continue
            los = np.linspace(terr[0] + emitter_h, terr[-1] + node_h, n)
            clr = terr - los
            k = int(np.argmax(clr[1:-1])) + 1
            c = float(clr[k])
            if best is None or c > best[0]:
                best = (c, float(lats[k]), float(lons[k]), float(terr[k]), nd.node_id)
        if best is None or best[0] <= 3.0:  # <3 m above sight-line = not really blocking
            return None
        c, lat, lon, terr_m, node_id = best
        return {
            "lat": lat,
            "lon": lon,
            "clearance_m": round(c),
            "terrain_m": round(terr_m),
            "node_id": node_id,
        }

    def _rf_grid(
        self,
        raster: Raster,
        cell_lat: np.ndarray,
        cell_lon: np.ndarray,
        nodes: list[_Node],
        wavelength_m: float,
        emitter_h: float,
        node_h: float,
        floor: float,
        scale_db: float,
        n_samples: int,
        progress_cb: Callable[[float], None] | None,
        cancel: _Cancellable | None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Vectorized per-cell RF path weight (product over nodes).

        Returns ``(rf_weight, terrain_at_cell, max_loss_db)`` grids, all
        ``(nlat, nlon)``. Computed in row-chunks so memory stays bounded and
        progress/cancel can be honoured between chunks.
        """
        nlat = cell_lat.shape[0]
        nlon = cell_lon.shape[0]
        latg, long = np.meshgrid(cell_lat, cell_lon, indexing="ij")
        rf = np.ones((nlat, nlon), dtype=np.float64)
        loss_grid = np.zeros((nlat, nlon), dtype=np.float64)
        terr_cell = raster.terrain(latg.ravel(), long.ravel()).reshape(nlat, nlon)

        near = raster.cell_deg_lat * _M_PER_DEG_LAT  # "same cell" distance
        fr = np.linspace(0.0, 1.0, n_samples)
        chunk = max(1, min(nlat, int(2_000_000 / max(1, nlon * n_samples))))
        total = max(1, len(nodes))
        for ni, nd in enumerate(nodes):
            if cancel is not None and cancel.is_set():
                raise PosteriorCancelled
            for r0 in range(0, nlat, chunk):
                r1 = min(nlat, r0 + chunk)
                sub_lat = latg[r0:r1]  # (rc, nlon)
                sub_lon = long[r0:r1]
                rc = r1 - r0
                lat_s = sub_lat[..., None] + (nd.lat - sub_lat[..., None]) * fr
                lon_s = sub_lon[..., None] + (nd.lon - sub_lon[..., None]) * fr
                terr = raster.terrain(lat_s.ravel(), lon_s.ravel()).reshape(rc, nlon, n_samples)
                d_e = (nd.lon - sub_lon) * _m_per_deg_lon(nd.lat)
                d_n = (nd.lat - sub_lat) * _M_PER_DEG_LAT
                dist = np.hypot(d_e, d_n)  # (rc, nlon)
                h_tx = terr[..., 0] + emitter_h
                h_rx = terr[..., -1] + node_h
                los = h_tx[..., None] + (h_rx - h_tx)[..., None] * fr
                clr = terr - los  # (rc, nlon, ns)
                d_path = dist[..., None] * fr
                d1 = d_path[..., 1:-1]
                d2 = dist[..., None] - d1
                hh = clr[..., 1:-1]
                with np.errstate(divide="ignore", invalid="ignore"):
                    vv = hh * np.sqrt(2.0 * dist[..., None] / (wavelength_m * d1 * d2))
                vv = np.where(np.isfinite(vv), vv, -np.inf)
                v_max = np.max(vv, axis=-1)  # (rc, nlon)
                v_max = np.where(np.isfinite(v_max), v_max, -1.0)
                loss = _j_v(v_max)
                far = dist >= near
                w = np.where(far, np.maximum(floor, 1.0 - loss / scale_db), 1.0)
                rf[r0:r1] *= w
                loss_grid[r0:r1] = np.maximum(loss_grid[r0:r1], np.where(far, loss, 0.0))
            if progress_cb is not None:
                progress_cb((ni + 1) / total)
        return rf, terr_cell, loss_grid

    def _grid(
        self,
        fix: Any,
        nodes: list[_Node],
        freq: float,
        active: Raster | None,
        *,
        cell_m: float,
        buffer_m: float,
        floor: float,
        scale_db: float,
        emitter_h: float,
        node_h: float,
        n_path_samples: int,
        progress_cb: Callable[[float], None] | None = None,
        cancel: _Cancellable | None = None,
    ) -> dict[str, Any]:
        """Build the AoA + RF grids for a fix. Shared by ``posterior_geojson``
        (the heat) and ``probe`` (the hover numbers) so they never disagree."""
        lat0 = fix.position.lat_deg
        lon0 = fix.position.lon_deg
        ell = fix.confidence_ellipse_95
        wavelength = _C / freq
        reach_m = ell.semi_major_m + buffer_m
        dlat = cell_m / _M_PER_DEG_LAT
        dlon = cell_m / _m_per_deg_lon(lat0)
        nlat = max(8, int(2 * reach_m / cell_m))
        nlon = nlat
        north = lat0 + reach_m / _M_PER_DEG_LAT
        west = lon0 - reach_m / _m_per_deg_lon(lat0)
        rows = np.arange(nlat)
        cols = np.arange(nlon)
        cell_lat = north - (rows + 0.5) * dlat
        cell_lon = west + (cols + 0.5) * dlon
        latg, long = np.meshgrid(cell_lat, cell_lon, indexing="ij")

        if nodes:
            aoa = np.ones((nlat, nlon), dtype=np.float64)
            for nd in nodes:
                d_e = (long - nd.lon) * _m_per_deg_lon(nd.lat)
                d_n = (latg - nd.lat) * _M_PER_DEG_LAT
                pred = (np.degrees(np.arctan2(d_e, d_n))) % 360.0
                diff = np.abs((pred - nd.azimuth_deg + 180.0) % 360.0 - 180.0)
                sig = max(nd.azimuth_sigma_deg, 0.1)
                aoa *= np.exp(-0.5 * (diff / sig) ** 2)
        else:
            sa = ell.semi_major_m / 2.4477
            sb = ell.semi_minor_m / 2.4477
            th = math.radians(ell.orientation_deg)
            d_e = (long - lon0) * _m_per_deg_lon(lat0)
            d_n = (latg - lat0) * _M_PER_DEG_LAT
            u = d_e * math.cos(th) + d_n * math.sin(th)
            v = -d_e * math.sin(th) + d_n * math.cos(th)
            aoa = np.exp(-0.5 * ((u / sa) ** 2 + (v / sb) ** 2))

        has_terrain = active is not None and bool(nodes)
        if has_terrain:
            assert active is not None
            rf, terr_cell, loss_grid = self._rf_grid(
                active, cell_lat, cell_lon, nodes, wavelength, emitter_h, node_h,
                floor, scale_db, n_path_samples, progress_cb, cancel,
            )
        else:
            rf = np.ones((nlat, nlon), dtype=np.float64)
            terr_cell = np.zeros((nlat, nlon), dtype=np.float64)
            loss_grid = np.zeros((nlat, nlon), dtype=np.float64)

        return {
            "north": north, "west": west, "dlat": dlat, "dlon": dlon,
            "cell_lat": cell_lat, "cell_lon": cell_lon,
            "aoa": aoa, "rf": rf, "terr_cell": terr_cell, "loss_grid": loss_grid,
            "has_terrain": has_terrain,
        }

    @staticmethod
    def _band_thresholds(post_norm: np.ndarray) -> dict[float, float]:
        """Highest-density-region thresholds (normalized units) for 50/80/95 %."""
        flat = post_norm.ravel()
        order = np.argsort(flat)[::-1]
        total = flat.sum()
        out: dict[float, float] = {}
        if total <= 0:
            return {0.5: 1.1, 0.8: 1.1, 0.95: 1.1}
        csum = np.cumsum(flat[order]) / total
        for mass in (0.5, 0.8, 0.95):
            k = int(np.searchsorted(csum, mass)) + 1
            out[mass] = float(flat[order][min(k, len(order) - 1)])
        return out

    def probe(
        self,
        fix: Any,
        nodes: list[_Node],
        center_freq_hz: float | None,
        lat: float,
        lon: float,
        *,
        cell_m: float,
        buffer_m: float,
        floor: float,
        scale_db: float,
        emitter_h: float,
        node_h: float,
        raster: Raster | None = None,
        n_path_samples: int = 24,
    ) -> dict[str, Any]:
        """The raw math behind the plausibility at one point: per-node AoA
        (measured vs predicted bearing, residual, sigma, likelihood) and per-node
        RF path (distance, clearance vs LOS, Fresnel v, diffraction loss, soft
        weight), the two products, and the normalized plausibility + band.

        The grid peak + band thresholds are cached per layer so a live hover
        doesn't recompute the whole grid each move; the per-point numbers are
        cheap and computed fresh at the exact cursor location."""
        active = raster if raster is not None else self._base
        freq = center_freq_hz or _DEFAULT_FREQ_HZ
        wavelength = _C / freq
        src = active.source if active is not None else "none"
        res_m = active.res_m if active is not None else 0.0
        key = f"{fix.fix_id}:{src}:{res_m}:{emitter_h:.1f}:{cell_m}:{n_path_samples}"
        ent = self._probe_cache.get(key)
        if ent is None:
            g = self._grid(
                fix, nodes, freq, active,
                cell_m=cell_m, buffer_m=buffer_m, floor=floor, scale_db=scale_db,
                emitter_h=emitter_h, node_h=node_h, n_path_samples=n_path_samples,
            )
            post = g["aoa"] * g["rf"]
            raw_max = float(post.max())
            if raw_max <= 0:
                post = g["aoa"]
                raw_max = float(post.max()) or 1.0
            thresholds = self._band_thresholds(post / raw_max)
            ent = {"raw_max": raw_max, "thresholds": thresholds}
            if len(self._probe_cache) > 64:
                self._probe_cache.clear()
            self._probe_cache[key] = ent
        raw_max = ent["raw_max"]
        thresholds = ent["thresholds"]

        has_terrain = active is not None and bool(nodes)
        node_rows: list[dict[str, Any]] = []
        aoa_prod = 1.0
        rf_prod = 1.0
        if nodes:
            for nd in nodes:
                # --- AoA term ---
                d_e = (lon - nd.lon) * _m_per_deg_lon(nd.lat)
                d_n = (lat - nd.lat) * _M_PER_DEG_LAT
                pred = math.degrees(math.atan2(d_e, d_n)) % 360.0
                resid = _ang_diff(pred, nd.azimuth_deg)
                sig = max(nd.azimuth_sigma_deg, 0.1)
                like = math.exp(-0.5 * (resid / sig) ** 2)
                aoa_prod *= like
                row: dict[str, Any] = {
                    "node_id": nd.node_id,
                    "azimuth_meas_deg": round(nd.azimuth_deg, 1),
                    "bearing_pred_deg": round(pred, 1),
                    "residual_deg": round(resid, 2),
                    "sigma_deg": round(sig, 1),
                    "aoa_likelihood": round(like, 3),
                }
                # --- RF path term ---
                if has_terrain:
                    pb = self._path_breakdown(
                        active, lat, lon, nd, wavelength, emitter_h, node_h,
                        floor, scale_db, n_path_samples,
                    )
                    rf_prod *= pb["weight"]
                    row.update(pb)
                node_rows.append(row)

        raw = aoa_prod * (rf_prod if has_terrain else 1.0)
        norm = raw / raw_max if raw_max > 0 else 0.0
        band: float | None = None
        for mass in (0.5, 0.8, 0.95):
            if norm >= thresholds.get(mass, 1.1):
                band = mass
                break

        return {
            "lat": lat, "lon": lon,
            "freq_hz": freq, "wavelength_m": round(wavelength, 4),
            "dem_source": src, "dem_res_m": res_m,
            "model": "knife-edge ITU-R P.526" if has_terrain else "geometry-only",
            "nodes": node_rows,
            "aoa_product": round(aoa_prod, 4),
            "rf_product": round(rf_prod, 4) if has_terrain else None,
            "raw": round(raw, 5),
            "plausibility": round(min(1.0, norm), 3),
            "band": band,
            "note": "plausibility ∝ Π(AoA likelihood) × Π(RF path weight), "
                    "normalized to the local peak. soft cue — not a target point.",
        }

    def _path_breakdown(
        self,
        raster: Raster,
        clat: float,
        clon: float,
        node: _Node,
        wavelength_m: float,
        emitter_h: float,
        node_h: float,
        floor: float,
        scale_db: float,
        n_samples: int,
    ) -> dict[str, Any]:
        """Scalar version of one cell→node RF path (mirrors ``_rf_grid``)."""
        d_e = (node.lon - clon) * _m_per_deg_lon(clat)
        d_n = (node.lat - clat) * _M_PER_DEG_LAT
        dist = math.hypot(d_e, d_n)
        near = raster.cell_deg_lat * _M_PER_DEG_LAT
        if dist < near:
            return {"distance_m": round(dist), "clearance_m": 0.0,
                    "fresnel_v": 0.0, "loss_db": 0.0, "weight": 1.0}
        fr = np.linspace(0.0, 1.0, n_samples)
        lats = clat + (node.lat - clat) * fr
        lons = clon + (node.lon - clon) * fr
        terr = raster.terrain(lats, lons)
        h_tx = terr[0] + emitter_h
        h_rx = terr[-1] + node_h
        los = h_tx + (h_rx - h_tx) * fr
        clr = terr - los
        d_path = dist * fr
        d1 = d_path[1:-1]
        d2 = dist - d1
        hh = clr[1:-1]
        with np.errstate(divide="ignore", invalid="ignore"):
            vv = hh * np.sqrt(2.0 * dist / (wavelength_m * d1 * d2))
        vv = np.where(np.isfinite(vv), vv, -np.inf)
        v_max = float(np.max(vv)) if vv.size else -1.0
        if not math.isfinite(v_max):
            v_max = -1.0
        loss = float(_j_v(np.array([v_max]))[0])
        weight = max(floor, 1.0 - loss / scale_db)
        return {
            "distance_m": round(dist),
            "clearance_m": round(float(np.max(clr)), 1),
            "fresnel_v": round(v_max, 2),
            "loss_db": round(loss, 1),
            "weight": round(weight, 3),
        }

    def posterior_geojson(
        self,
        fix: Any,
        nodes: list[_Node],
        center_freq_hz: float | None,
        *,
        cell_m: float,
        buffer_m: float,
        floor: float,
        scale_db: float,
        emitter_h: float,
        node_h: float,
        raster: Raster | None = None,
        n_path_samples: int = 24,
        progress_cb: Callable[[float], None] | None = None,
        cancel: _Cancellable | None = None,
    ) -> dict[str, Any]:
        active = raster if raster is not None else self._base
        lat0 = fix.position.lat_deg
        lon0 = fix.position.lon_deg
        freq = center_freq_hz or _DEFAULT_FREQ_HZ
        g = self._grid(
            fix, nodes, freq, active,
            cell_m=cell_m, buffer_m=buffer_m, floor=floor, scale_db=scale_db,
            emitter_h=emitter_h, node_h=node_h, n_path_samples=n_path_samples,
            progress_cb=progress_cb, cancel=cancel,
        )
        north, west, dlat, dlon = g["north"], g["west"], g["dlat"], g["dlon"]
        aoa, rf = g["aoa"], g["rf"]
        terr_cell, loss_grid = g["terr_cell"], g["loss_grid"]
        has_terrain = g["has_terrain"]

        post = aoa * rf
        if post.max() <= 0:
            post = aoa.copy()

        # How much did terrain reshape the bearing-only estimate? Total-variation
        # distance between the AoA-only and AoA*RF normalized distributions (0..1).
        a_sum = float(aoa.sum())
        p_sum = float(post.sum())
        if has_terrain and a_sum > 0 and p_sum > 0:
            effect = float(0.5 * np.abs(post / p_sum - aoa / a_sum).sum())
        else:
            effect = 0.0
        if not has_terrain:
            label = "no-terrain"
        elif effect < 0.05:
            label = "negligible"
        elif effect < 0.15:
            label = "weak"
        elif effect < 0.30:
            label = "moderate"
        else:
            label = "strong"

        post = post / post.max()
        features = self._bands(post, terr_cell, loss_grid, north, west, dlat, dlon, freq)
        obstruction = (
            self._dominant_obstruction(active, lat0, lon0, nodes, emitter_h, node_h)
            if active is not None
            else None
        )
        src = active.source if active is not None else "none"
        res_m = active.res_m if active is not None else 0.0
        return {
            "type": "FeatureCollection",
            "features": features,
            "properties": {
                "fix_id": str(fix.fix_id),
                "band_hz": freq,
                "rf_model": "knife-edge ITU-R P.526" if has_terrain else "none (no DEM)",
                "rf_prior_strength": "terrain-aware" if has_terrain else "geometry-only",
                "rf_effect": round(effect, 3),
                "rf_effect_label": label,
                "obstruction": obstruction,
                "cell_m": cell_m,
                "dem_source": src,
                "dem_res_m": res_m,
                "n_path_samples": n_path_samples,
                "note": "soft RF-plausibility cue; diffraction-based, not a target point",
            },
        }

    def _bands(
        self,
        post: np.ndarray,
        terr_cell: np.ndarray,
        loss_grid: np.ndarray,
        north: float,
        west: float,
        dlat: float,
        dlon: float,
        freq: float,
    ) -> list[dict[str, Any]]:
        try:
            import rasterio.features  # noqa: PLC0415
            import rasterio.transform  # noqa: PLC0415
            from shapely.geometry import mapping, shape  # noqa: PLC0415
            from shapely.ops import unary_union  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            return []
        transform = rasterio.transform.from_origin(west, north, dlon, dlat)
        flat = post.ravel()
        order = np.argsort(flat)[::-1]
        csum = np.cumsum(flat[order]) / flat.sum()
        feats: list[dict[str, Any]] = []
        for mass in (0.95, 0.80, 0.50):  # outer -> inner so inner draws on top
            k = int(np.searchsorted(csum, mass)) + 1
            thresh = float(flat[order][min(k, len(order) - 1)])
            sel = post >= thresh
            mask = sel.astype(np.uint8)
            if mask.sum() == 0:
                continue
            geoms = [
                shape(g)
                for g, val in rasterio.features.shapes(
                    mask, mask=mask.astype(bool), transform=transform
                )
                if val == 1
            ]
            if not geoms:
                continue
            merged = unary_union(geoms).simplify(dlon * 0.5)
            # representative terrain + diffraction loss for the inspector
            terr_m = float(np.median(terr_cell[sel])) if terr_cell.any() else 0.0
            loss_db = float(np.median(loss_grid[sel])) if loss_grid.any() else 0.0
            if loss_db < 3.0:
                loss_label = "low"
            elif loss_db < 10.0:
                loss_label = "moderate"
            else:
                loss_label = "high"
            feats.append(
                {
                    "type": "Feature",
                    "geometry": mapping(merged),
                    "properties": {
                        "p_band": mass,
                        "band_hz": freq,
                        "feature_kind": "posterior",
                        "terrain_m": round(terr_m, 1),
                        "loss_db": round(loss_db, 1),
                        "loss_label": loss_label,
                    },
                }
            )
        return feats


def nodes_for_fix(fix: Any, bearings: list[Any]) -> list[_Node]:
    """Match a fix's contributing nodes to the latest stored bearings."""
    by_node: dict[str, Any] = {}
    for b in bearings:
        cur = by_node.get(b.node_id)
        if cur is None or b.t_unix_ns > cur.t_unix_ns:
            by_node[b.node_id] = b
    out: list[_Node] = []
    for node_id in fix.contributing_nodes:
        b = by_node.get(node_id)
        if b is not None:
            out.append(
                _Node(
                    lat=b.node_position.lat_deg,
                    lon=b.node_position.lon_deg,
                    azimuth_deg=b.azimuth_deg,
                    azimuth_sigma_deg=b.azimuth_sigma_deg,
                    node_id=node_id,
                )
            )
    return out
