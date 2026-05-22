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
        ell = fix.confidence_ellipse_95
        freq = center_freq_hz or _DEFAULT_FREQ_HZ
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

        # --- AoA likelihood (vectorized) ---
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

        # --- RF path weight (per cell, product over contributing nodes) ---
        has_terrain = active is not None and nodes
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
