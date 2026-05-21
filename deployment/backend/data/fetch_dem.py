"""Fetch + clip a small DEM for the demo AOI -> dem_aoi.tif.

Pulls Copernicus DEM GLO-30 (free, no key) straight from the AWS open bucket via
GDAL's /vsicurl, mosaics the 1-degree tiles the AOI touches, clips to the AOI box,
and writes a small GeoTIFF the backend bundles. Run ONCE inside the backend
container (it has rasterio + network), then commit the resulting dem_aoi.tif:

    docker compose ... exec backend python /app/data/fetch_dem.py

Copernicus DEM is an (edited) DSM — it includes surface features — which is what
RF shadowing wants. Wallonia 1 m LiDAR MNS is the fidelity upgrade (swap the
source + raise resolution); the rest of the pipeline is unchanged.
"""

from __future__ import annotations

import math
from pathlib import Path

import rasterio
from rasterio.merge import merge

# Demo AOI box (covers the seed nodes + fixes + posterior buffer around 50.355N, 5.0E).
AOI_W, AOI_S, AOI_E, AOI_N = 4.90, 50.30, 5.10, 50.42

_BUCKET = "https://copernicus-dem-30m.s3.amazonaws.com"
_OUT = Path(__file__).with_name("dem_aoi.tif")


def _tile_url(lat_floor: int, lon_floor: int) -> str:
    ns = f"N{lat_floor:02d}" if lat_floor >= 0 else f"S{abs(lat_floor):02d}"
    ew = f"E{lon_floor:03d}" if lon_floor >= 0 else f"W{abs(lon_floor):03d}"
    name = f"Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM"
    return f"/vsicurl/{_BUCKET}/{name}/{name}.tif"


def main() -> None:
    lon_tiles = range(math.floor(AOI_W), math.floor(AOI_E) + 1)
    lat_tiles = range(math.floor(AOI_S), math.floor(AOI_N) + 1)
    urls = [_tile_url(la, lo) for la in lat_tiles for lo in lon_tiles]
    print(f"AOI {AOI_W},{AOI_S},{AOI_E},{AOI_N} -> tiles:")
    for u in urls:
        print("  ", u)

    srcs = []
    for u in urls:
        try:
            srcs.append(rasterio.open(u))
        except Exception as exc:  # noqa: BLE001
            print(f"  WARN: could not open {u}: {exc}")
    if not srcs:
        raise SystemExit("no DEM tiles could be opened (network? bucket path?)")

    mosaic, transform = merge(srcs, bounds=(AOI_W, AOI_S, AOI_E, AOI_N))
    profile = srcs[0].profile
    profile.update(
        height=mosaic.shape[1],
        width=mosaic.shape[2],
        transform=transform,
        driver="GTiff",
        compress="deflate",
    )
    with rasterio.open(_OUT, "w", **profile) as dst:
        dst.write(mosaic)
    for s in srcs:
        s.close()
    print(f"wrote {_OUT}  shape={mosaic.shape}  ~{_OUT.stat().st_size // 1024} KiB")


if __name__ == "__main__":
    main()
