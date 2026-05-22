"""Stage Wallonia MNT 1 m -> an AOI 1 m terrain COG (EPSG:4326) for Enhance.

The province GeoTIFF bundle (`RELIEF_WALLONIE_MNT_1M_2021_2022_GEOTIFF_3812_PROV_*.zip`,
~9.5 GB for Namur, CC-BY) ships as ONE giant province-wide GeoTIFF (~3 Gcells in
EPSG:3812). This clips just the demo AOI out of it and writes a small
Cloud-Optimized GeoTIFF (EPSG:4326) that `enhance.py` window-reads.

It opens sources straight from the ZIP via GDAL `/vsizip/` (no extraction),
**windowed-reads only the AOI** (so the 3 Gcell province is never materialized),
then reprojects + clips to EPSG:4326 with `Resampling.max` so the ridge peaks
that drive knife-edge diffraction survive (averaging would understate
obstruction — GIS-council finding). Handles one giant tif or many tiles
uniformly. Pure rasterio: no GDAL CLI, runs inside the backend image.

Run (backend image, ZIP + output dir mounted):
    python stage_lidar.py \
        --zip /staging/namur_mnt1m.zip \
        --bbox 4.89 50.31 5.04 50.39 \
        --out /lidar/lidar_aoi.tif

MNT = bare-earth (DTM); honest UI label is "Wallonia MNT 1 m". (The 0.5 m
MNS/DSM with buildings exists but is 43-52 GB; out of scope for the PoC.)
"""

from __future__ import annotations

import argparse
import os
import sys
import zipfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_bounds as transform_from_bounds
from rasterio.warp import Resampling, reproject, transform_bounds
from rasterio.windows import from_bounds as window_from_bounds

_M_PER_DEG_LAT = 111_320.0
_NEG = np.float32(-1.0e30)  # sentinel for "no data" during max-accumulation


def _overlaps(b: tuple[float, float, float, float], w: float, s: float, e: float, n: float) -> bool:
    return not (b[2] < w or b[0] > e or b[3] < s or b[1] > n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True, help="province MNT 1 m GeoTIFF zip")
    ap.add_argument("--bbox", nargs=4, type=float, required=True,
                    metavar=("W", "S", "E", "N"), help="AOI in lon/lat (EPSG:4326)")
    ap.add_argument("--out", required=True, help="output COG path (EPSG:4326)")
    ap.add_argument("--res-m", type=float, default=1.0, help="output ground res (m)")
    a = ap.parse_args()
    w, s, e, n = a.bbox
    zip_abs = os.path.abspath(a.zip)
    if not os.path.exists(zip_abs):
        print(f"ERROR: zip not found: {zip_abs}", file=sys.stderr)
        return 2

    members = [m for m in zipfile.ZipFile(zip_abs).namelist()
               if m.lower().endswith((".tif", ".tiff"))]
    print(f"{len(members)} GeoTIFF source(s) in archive")

    # ---- target 4326 grid over the AOI ----
    res_deg_lat = a.res_m / _M_PER_DEG_LAT
    res_deg_lon = a.res_m / (_M_PER_DEG_LAT * np.cos(np.radians((s + n) / 2.0)))
    width = max(16, int(round((e - w) / res_deg_lon)))
    height = max(16, int(round((n - s) / res_deg_lat)))
    dst_trans = transform_from_bounds(w, s, e, n, width, height)
    target = np.full((height, width), _NEG, dtype="float32")
    print(f"target: {width}x{height} @ ~{a.res_m} m EPSG:4326 ({width * height / 1e6:.1f} Mcells)")

    used = 0
    for m in members:
        vp = f"/vsizip/{zip_abs}/{m}"
        with rasterio.open(vp) as ds:
            b4326 = transform_bounds(ds.crs, "EPSG:4326", *ds.bounds, densify_pts=21)
            if not _overlaps(b4326, w, s, e, n):
                continue
            # AOI window in the source's own CRS (bounded read; province never fully loaded)
            aoi_native = transform_bounds("EPSG:4326", ds.crs, w, s, e, n, densify_pts=21)
            win = window_from_bounds(*aoi_native, ds.transform).round_offsets().round_lengths()
            arr = ds.read(1, window=win, boundless=True,
                          fill_value=(ds.nodata if ds.nodata is not None else 0.0)).astype("float32")
            src_t = ds.window_transform(win)
            src_crs = ds.crs
            src_nodata = ds.nodata
            print(f"  {m}: read window {arr.shape} from native bounds "
                  f"{[round(x) for x in aoi_native]}")
        if src_nodata is not None:
            arr = np.where(arr == src_nodata, _NEG, arr)
        tmp = np.full((height, width), _NEG, dtype="float32")
        reproject(
            source=arr, destination=tmp,
            src_transform=src_t, src_crs=src_crs, src_nodata=float(_NEG),
            dst_transform=dst_trans, dst_crs="EPSG:4326", dst_nodata=float(_NEG),
            resampling=Resampling.max,
        )
        np.fmax(target, tmp, out=target)
        used += 1

    if used == 0:
        print("ERROR: no source overlapped the AOI", file=sys.stderr)
        return 3

    fill = -9999.0
    valid = target > _NEG / 2
    out_arr = np.where(valid, target, np.float32(fill))
    if valid.sum() == 0:
        print("ERROR: AOI window is all nodata", file=sys.stderr)
        return 4
    print(f"used {used} source(s); valid cells: {int(valid.sum())}/{out_arr.size} "
          f"({100.0 * valid.sum() / out_arr.size:.1f}%)  "
          f"z range: {float(out_arr[valid].min()):.1f}..{float(out_arr[valid].max()):.1f} m")

    out_path = Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    base_prof = {
        "driver": "GTiff", "height": height, "width": width, "count": 1,
        "dtype": "float32", "crs": "EPSG:4326", "transform": dst_trans,
        "nodata": fill, "tiled": True, "blockxsize": 512, "blockysize": 512,
        "compress": "DEFLATE", "predictor": 3,
    }
    # No max-overviews: Enhance reads at 1-4 m straight from the full-res 1 m band
    # with Resampling.max (true peak per output cell). Overviews are only a zoom
    # optimization; let the COG driver build them with `nearest` (no smoothing).
    tmp_tif = out_path.with_suffix(".tmp.tif")
    with rasterio.open(tmp_tif, "w", **base_prof) as dst:
        dst.write(out_arr, 1)

    try:
        from rasterio.shutil import copy as rio_copy  # noqa: PLC0415

        rio_copy(tmp_tif, out_path, driver="COG", compress="DEFLATE",
                 overview_resampling="nearest")
        tmp_tif.unlink(missing_ok=True)
        print(f"WROTE COG: {out_path}")
    except Exception as exc:  # noqa: BLE001
        os.replace(tmp_tif, out_path)
        print(f"  COG driver unavailable ({exc}); wrote tiled GTiff: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
