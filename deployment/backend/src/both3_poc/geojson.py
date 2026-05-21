"""Render fixes and bearings as GeoJSON for the Leaflet frontend.

Ellipses are projected server-side via the existing, tested
`rfmesh_cot.ellipse_to_polygon_vertices` — the single source of truth for the
rotated metre-ellipse math. GeoJSON coordinate order is [lon, lat].
"""

from __future__ import annotations

import math
from typing import Any

from rfmesh_contracts import BearingReport, FixEvent
from rfmesh_cot import ellipse_to_polygon_vertices

from .mgrs_util import to_mgrs

_WGS84_A_M = 6_378_137.0


def _fix_age_and_stale(
    fix: FixEvent,
    *,
    now_ns: int,
    stale_window_s: float,
    seeded: bool,
    seed_as_fresh: bool,
) -> tuple[float | None, bool]:
    """Return (age_seconds_or_None, is_stale). Seeded+fresh => (None, False)."""
    if seeded and seed_as_fresh:
        return None, False
    age_s = (now_ns - fix.t_unix_ns) / 1e9
    return age_s, age_s > stale_window_s


def fix_to_features(
    fix: FixEvent,
    *,
    now_ns: int,
    stale_window_s: float,
    seeded: bool,
    seed_as_fresh: bool,
) -> list[dict[str, Any]]:
    """Two GeoJSON features per fix: the ellipse polygon and the centre point."""
    ring_latlon = ellipse_to_polygon_vertices(
        fix.confidence_ellipse_95,
        fix.position.lat_deg,
        fix.position.lon_deg,
        n_vertices=72,
    )
    ring_lonlat = [[lon, lat] for (lat, lon) in ring_latlon]

    age_s, stale = _fix_age_and_stale(
        fix,
        now_ns=now_ns,
        stale_window_s=stale_window_s,
        seeded=seeded,
        seed_as_fresh=seed_as_fresh,
    )

    props: dict[str, Any] = {
        "fix_id": str(fix.fix_id),
        "lat": fix.position.lat_deg,
        "lon": fix.position.lon_deg,
        "mgrs": to_mgrs(fix.position.lat_deg, fix.position.lon_deg),
        "semi_major_m": fix.confidence_ellipse_95.semi_major_m,
        "semi_minor_m": fix.confidence_ellipse_95.semi_minor_m,
        "orientation_deg": fix.confidence_ellipse_95.orientation_deg,
        "area_m2": fix.confidence_ellipse_95.area_m2,
        "gdop": fix.gdop,
        "confidence_level": fix.confidence_level.value,
        "method": fix.method,
        "emitter_class": fix.emitter_class.value if fix.emitter_class else None,
        "t_unix_ns": fix.t_unix_ns,
        "age_s": age_s,
        "stale": stale,
        "seeded": seeded,
        "contributing_nodes": list(fix.contributing_nodes),
        "residuals_deg": list(fix.residuals_deg),
    }

    return [
        {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring_lonlat]},
            "properties": {**props, "feature_kind": "ellipse"},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [fix.position.lon_deg, fix.position.lat_deg]},
            "properties": {**props, "feature_kind": "center"},
        },
    ]


def fixes_feature_collection(
    fixes: list[FixEvent],
    *,
    now_ns: int,
    stale_window_s: float,
    seeded_ids: set[Any],
    seed_as_fresh: bool,
) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for fix in fixes:
        features.extend(
            fix_to_features(
                fix,
                now_ns=now_ns,
                stale_window_s=stale_window_s,
                seeded=fix.fix_id in seeded_ids,
                seed_as_fresh=seed_as_fresh,
            )
        )
    return {"type": "FeatureCollection", "features": features}


def _dest_point(lat_deg: float, lon_deg: float, az_deg: float, dist_m: float) -> tuple[float, float]:
    """Equirectangular destination point from a start, true bearing, and range."""
    az = math.radians(az_deg)
    d_north = dist_m * math.cos(az)
    d_east = dist_m * math.sin(az)
    d_lat = math.degrees(d_north / _WGS84_A_M)
    cos_lat = math.cos(math.radians(lat_deg))
    d_lon = math.degrees(d_east / (_WGS84_A_M * cos_lat)) if cos_lat else 0.0
    return lat_deg + d_lat, lon_deg + d_lon


def bearings_feature_collection(
    bearings: list[BearingReport],
    *,
    lob_length_m: float,
) -> dict[str, Any]:
    """Node points + LOB line-of-bearing rays for each bearing."""
    features: list[dict[str, Any]] = []
    for b in bearings:
        nlat = b.node_position.lat_deg
        nlon = b.node_position.lon_deg
        end_lat, end_lon = _dest_point(nlat, nlon, b.azimuth_deg, lob_length_m)
        common = {
            "node_id": b.node_id,
            "azimuth_deg": b.azimuth_deg,
            "azimuth_sigma_deg": b.azimuth_sigma_deg,
            "method": b.method.value,
            "t_unix_ns": b.t_unix_ns,
        }
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [nlon, nlat]},
                "properties": {**common, "feature_kind": "node"},
            }
        )
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[nlon, nlat], [end_lon, end_lat]]},
                "properties": {**common, "feature_kind": "lob"},
            }
        )
    return {"type": "FeatureCollection", "features": features}
