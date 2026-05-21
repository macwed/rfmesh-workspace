"""Grid-posterior RF-shadow layer.

Turns a single ``FixEvent`` (+ its contributing bearings + a terrain DEM) into a
probability heatmap of where the emitter plausibly is. Honest by construction:

    P(cell) ∝ AoA_likelihood(cell) × RF_path_weight(cell)

- **AoA_likelihood** reproduces the bearing geometry per cell (the ellipse, but
  on a grid). Falls back to the fix's covariance ellipse when no bearings exist.
- **RF_path_weight** is a *soft* terrain term from single-knife-edge diffraction
  (ITU-R P.526) along each cell→sensor path over the DEM. Shadowed cells are
  down-weighted to a floor, **never zeroed** (diffraction fills shadows). The
  frequency dependence is intrinsic: the Fresnel parameter ``v`` scales with
  ``1/sqrt(lambda)``, so sub-GHz shadows are mild and 5.8 GHz shadows are sharp,
  with no hand-tuned per-band factor.

Output is a GeoJSON FeatureCollection of nested highest-density bands (50/80/95 %
of the probability mass), drawn *under* the ellipse. No centroid, no hard mask.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

_C = 299_792_458.0  # speed of light, m/s
_M_PER_DEG_LAT = 111_320.0
_DEFAULT_FREQ_HZ = 1.0e9  # used only when a fix carries no frequency annotation


def _m_per_deg_lon(lat_deg: float) -> float:
    return _M_PER_DEG_LAT * math.cos(math.radians(lat_deg))


def _bearing_deg(from_lat: float, from_lon: float, to_lat: float, to_lon: float) -> float:
    """True-north azimuth (deg, CW) from one point to another."""
    d_e = (to_lon - from_lon) * _m_per_deg_lon(from_lat)
    d_n = (to_lat - from_lat) * _M_PER_DEG_LAT
    return math.degrees(math.atan2(d_e, d_n)) % 360.0


def _ang_diff(a: float, b: float) -> float:
    """Smallest absolute difference between two azimuths, degrees [0,180]."""
    d = abs((a - b + 180.0) % 360.0 - 180.0)
    return d


def _knife_edge_loss_db(
    terrain: np.ndarray,
    h_tx_abs: float,
    h_rx_abs: float,
    total_dist_m: float,
    wavelength_m: float,
) -> float:
    """Single-dominant-edge diffraction loss (ITU-R P.526), dB >= 0.

    ``terrain`` is the ground-height profile sampled evenly from tx to rx.
    """
    n = terrain.shape[0]
    if n < 3 or total_dist_m <= 1.0:
        return 0.0
    d = np.linspace(0.0, total_dist_m, n)
    los = np.linspace(h_tx_abs, h_rx_abs, n)
    clearance = terrain - los  # >0 means terrain pokes above the line of sight
    d1 = d[1:-1]
    d2 = total_dist_m - d1
    h = clearance[1:-1]
    # Fresnel-Kirchhoff diffraction parameter v.
    v = h * np.sqrt(2.0 * total_dist_m / (wavelength_m * d1 * d2))
    v_max = float(np.max(v))
    if v_max <= -0.78:
        return 0.0
    return float(6.9 + 20.0 * math.log10(math.sqrt((v_max - 0.1) ** 2 + 1.0) + v_max - 0.1))


@dataclass
class _Node:
    lat: float
    lon: float
    azimuth_deg: float
    azimuth_sigma_deg: float
    node_id: str = ""


class PosteriorEngine:
    """Loads the DEM once; computes a per-fix posterior on demand."""

    def __init__(self, dem_file: Path) -> None:
        self._ok = False
        self._z: np.ndarray | None = None
        try:
            import rasterio  # noqa: PLC0415

            with rasterio.open(dem_file) as ds:
                self._z = ds.read(1).astype(np.float64)
                t = ds.transform
                self._ox, self._oy = t.c, t.f
                self._px, self._py = t.a, t.e  # px>0, py<0 typically
                self._h, self._w = self._z.shape
                self._nodata = ds.nodata
            if self._nodata is not None:
                self._z[self._z == self._nodata] = 0.0
            self._ok = True
        except Exception as exc:  # noqa: BLE001 - DEM optional; degrade to no-shadow
            self._err = str(exc)

    @property
    def available(self) -> bool:
        return self._ok

    def _terrain(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        """Nearest-cell terrain height for arrays of lat/lon (0.0 outside DEM)."""
        assert self._z is not None
        col = ((lon - self._ox) / self._px).astype(int)
        row = ((lat - self._oy) / self._py).astype(int)
        inside = (row >= 0) & (row < self._h) & (col >= 0) & (col < self._w)
        out = np.zeros(lat.shape, dtype=np.float64)
        out[inside] = self._z[row[inside], col[inside]]
        return out

    def _path_weight(
        self,
        cell_lat: float,
        cell_lon: float,
        node: _Node,
        wavelength_m: float,
        emitter_h: float,
        node_h: float,
        floor: float,
        scale_db: float,
        n_samples: int = 24,
    ) -> float:
        d_e = (node.lon - cell_lon) * _m_per_deg_lon(cell_lat)
        d_n = (node.lat - cell_lat) * _M_PER_DEG_LAT
        dist = math.hypot(d_e, d_n)
        if dist < self._px * _M_PER_DEG_LAT:  # same cell-ish
            return 1.0
        fr = np.linspace(0.0, 1.0, n_samples)
        lats = cell_lat + (node.lat - cell_lat) * fr
        lons = cell_lon + (node.lon - cell_lon) * fr
        terrain = self._terrain(lats, lons)
        h_tx = terrain[0] + emitter_h
        h_rx = terrain[-1] + node_h
        loss = _knife_edge_loss_db(terrain, h_tx, h_rx, dist, wavelength_m)
        return max(floor, 1.0 - loss / scale_db)

    def _dominant_obstruction(
        self,
        clat: float,
        clon: float,
        nodes: list[_Node],
        emitter_h: float,
        node_h: float,
        n: int = 48,
    ) -> dict[str, Any] | None:
        """The single worst terrain blocker on the emitter-center → sensor paths.

        Returns the point of greatest clearance-above-line-of-sight (the ridge that
        casts the RF shadow) so the UI can mark it with its height. None if nothing
        meaningfully obstructs.
        """
        if not self._ok or not nodes:
            return None
        best: tuple[float, float, float, float, str] | None = None
        for nd in nodes:
            fr = np.linspace(0.0, 1.0, n)
            lats = clat + (nd.lat - clat) * fr
            lons = clon + (nd.lon - clon) * fr
            terr = self._terrain(lats, lons)
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
    ) -> dict[str, Any]:
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
        rf = np.ones((nlat, nlon), dtype=np.float64)
        if self._ok and nodes:
            for i in range(nlat):
                for j in range(nlon):
                    w = 1.0
                    for nd in nodes:
                        w *= self._path_weight(
                            float(cell_lat[i]), float(cell_lon[j]), nd,
                            wavelength, emitter_h, node_h, floor, scale_db,
                        )
                    rf[i, j] = w

        post = aoa * rf
        if post.max() <= 0:
            post = aoa.copy()

        # How much did terrain reshape the bearing-only estimate? Total-variation
        # distance between the AoA-only and AoA*RF normalized distributions (0..1).
        a_sum = float(aoa.sum())
        p_sum = float(post.sum())
        if self._ok and a_sum > 0 and p_sum > 0:
            effect = float(0.5 * np.abs(post / p_sum - aoa / a_sum).sum())
        else:
            effect = 0.0
        if not self._ok:
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
        features = self._bands(post, north, west, dlat, dlon, freq)
        obstruction = self._dominant_obstruction(lat0, lon0, nodes, emitter_h, node_h)
        return {
            "type": "FeatureCollection",
            "features": features,
            "properties": {
                "fix_id": str(fix.fix_id),
                "band_hz": freq,
                "rf_model": "knife-edge ITU-R P.526" if self._ok else "none (no DEM)",
                "rf_prior_strength": "terrain-aware" if self._ok else "geometry-only",
                "rf_effect": round(effect, 3),
                "rf_effect_label": label,
                "obstruction": obstruction,
                "cell_m": cell_m,
                "note": "soft RF-plausibility cue; diffraction-based, not a target point",
            },
        }

    def _bands(
        self, post: np.ndarray, north: float, west: float, dlat: float, dlon: float, freq: float
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
            mask = (post >= thresh).astype(np.uint8)
            if mask.sum() == 0:
                continue
            geoms = [
                shape(g)
                for g, val in rasterio.features.shapes(mask, mask=mask.astype(bool), transform=transform)
                if val == 1
            ]
            if not geoms:
                continue
            merged = unary_union(geoms).simplify(dlon * 0.5)
            feats.append(
                {
                    "type": "Feature",
                    "geometry": mapping(merged),
                    "properties": {"p_band": mass, "band_hz": freq, "feature_kind": "posterior"},
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
