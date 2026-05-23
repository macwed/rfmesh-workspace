"""S0 — shared exposure engine (concealment / leakage / jam-shadow).

Tests the reduction algebra and the honesty invariants directly on the engine,
on a synthetic ridge raster, so they need neither Docker nor the LiDAR data
(and run even without shapely/rasterio — the HDR polygonizer degrades to no
features but the lens properties are always present).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from both3_poc.posterior import _C, ERP_K, PosteriorEngine, Raster, _Red

FLOOR = 0.15
SCALE_DB = 25.0
BAND_5G8 = 5.8e9

# Asset/center sits WEST of a tall wall; red nodes sit EAST of it, so the
# straight cell->node path is blocked and the west cells are in terrain shadow.
WALL_LON = 0.115
ASSET_LAT, ASSET_LON = 0.0, 0.100
RED_LON = 0.130


def _ridge_raster() -> Raster:
    h = w = 220
    px, py = 0.0009, -0.0009
    ox, oy = 0.0, 0.10  # top-left; lat decreases downward (north-up)
    z = np.zeros((h, w), dtype=np.float64)
    wall = int((WALL_LON - ox) / px)
    z[:, wall - 1 : wall + 2] = 120.0  # ~120 m vertical wall
    return Raster(z=z, ox=ox, oy=oy, px=px, py=py, source="synthetic", res_m=30.0)


def _engine() -> PosteriorEngine:
    return PosteriorEngine(Path("/nonexistent-dem.tif"))  # _base=None; we pass raster=


def _red(role: str, erp: str, *, lat: float = ASSET_LAT, lon: float = RED_LON) -> _Red:
    return _Red(lat=lat, lon=lon, h_m=3.0, role=role, erp_class=erp, node_id=f"{role}-{erp}")


def _west_cells() -> tuple[np.ndarray, np.ndarray]:
    cell_lat = np.linspace(0.02, -0.02, 40)
    cell_lon = np.linspace(0.095, 0.112, 40)  # all west of the wall
    return cell_lat, cell_lon


# --------------------------------------------------------------------------- #
# Kernel / reduction algebra
# --------------------------------------------------------------------------- #


def test_shadow_attenuates_the_path() -> None:
    """A west cell behind the wall sees real diffraction loss from an east node."""
    eng, ras = _engine(), _ridge_raster()
    probe = eng.exposure_probe(
        "jamshadow", [_red("jammer", "medium")], [BAND_5G8], ASSET_LAT, ASSET_LON,
        floor=FLOOR, scale_db=SCALE_DB, asset_h_m=2.0, raster=ras,
    )
    row = probe["nodes"][0]
    assert row["loss_db"] > 6.0  # the wall bites
    assert row["weight"] < 1.0  # transmission reduced
    assert 0.0 <= probe["survival"] <= 1.0


def test_node_union_exceeds_single() -> None:
    """Exposure E = 1 - Π(1-T) is monotone in node count (no concealment over-claim)."""
    eng, ras = _engine(), _ridge_raster()
    one = eng.exposure_probe(
        "jamshadow", [_red("jammer", "medium")], [BAND_5G8], ASSET_LAT, ASSET_LON,
        floor=FLOOR, scale_db=SCALE_DB, asset_h_m=2.0, raster=ras,
    )
    two = eng.exposure_probe(
        "jamshadow",
        [_red("jammer", "medium"), _red("jammer", "medium", lat=0.01)],
        [BAND_5G8], ASSET_LAT, ASSET_LON,
        floor=FLOOR, scale_db=SCALE_DB, asset_h_m=2.0, raster=ras,
    )
    assert two["exposure"] > one["exposure"]


def test_floor_keeps_cover_from_being_total() -> None:
    """Single node: E == T >= floor everywhere, so survival never reaches 1.0
    (a cell is never fully dark — diffraction fills shadows)."""
    eng, ras = _engine(), _ridge_raster()
    cell_lat, cell_lon = _west_cells()
    e, loss = eng._exposure_band_grid(
        ras, cell_lat, cell_lon, [_red("jammer", "medium")], _C / BAND_5G8,
        2.0, FLOOR, SCALE_DB, 24, erp_gate=False,
    )
    assert e.min() >= FLOOR - 1e-9          # never below the floor
    assert e.max() <= 1.0 + 1e-9
    assert (1.0 - e.min()) < 1.0            # survival never total
    assert e.min() < 0.9                    # shadow actually present
    assert loss.max() > 6.0


def test_medium_erp_jamshadow_equals_sign_flipped_concealment() -> None:
    """Regression anchor: with medium ERP (k=1.0), jam-shadow is numerically the
    same kernel as concealment — same geometry -> same survival."""
    assert ERP_K["medium"] == 1.0
    eng, ras = _engine(), _ridge_raster()
    kw = dict(floor=FLOOR, scale_db=SCALE_DB, asset_h_m=2.0, raster=ras)
    conceal = eng.exposure_probe(
        "concealment", [_red("recon", "low")], [BAND_5G8], ASSET_LAT, ASSET_LON, **kw,
    )
    jam = eng.exposure_probe(
        "jamshadow", [_red("jammer", "medium")], [BAND_5G8], ASSET_LAT, ASSET_LON, **kw,
    )
    # same red position/height + medium k -> identical transmission -> identical survival
    assert jam["survival"] == pytest.approx(conceal["survival"], abs=1e-9)


# --------------------------------------------------------------------------- #
# Honesty gates
# --------------------------------------------------------------------------- #


def _geojson(eng: PosteriorEngine, ras: Raster, mode: str, reds: list[_Red]) -> dict:
    return eng.exposure_geojson(
        mode, reds, [BAND_5G8],
        center_lat=ASSET_LAT, center_lon=ASSET_LON, reach_m=1500.0,
        cell_m=80.0, floor=FLOOR, scale_db=SCALE_DB, asset_h_m=2.0,
        n_path_samples=16, raster=ras,
    )


def test_role_gate_filters_ineligible_red() -> None:
    """Concealment is a sensor lens — pure jammers are not eligible (and vice versa)."""
    eng, ras = _engine(), _ridge_raster()
    out = _geojson(eng, ras, "concealment", [_red("jammer", "medium")])
    assert out["features"] == []
    assert "role" in out["properties"]["note"]
    assert out["properties"]["reliable"] is False


def test_spoofer_and_passive_never_get_a_jam_reach_shadow() -> None:
    eng, ras = _engine(), _ridge_raster()
    out = _geojson(eng, ras, "jamshadow", [_red("spoofer", "low"), _red("df", "low")])
    assert out["features"] == []  # neither is role=jammer


def test_burnthrough_flag_tracks_erp() -> None:
    """High/very-high ERP jammers trip burnthrough; medium does not."""
    eng, ras = _engine(), _ridge_raster()
    hi = _geojson(eng, ras, "jamshadow", [_red("jammer", "very_high")])
    assert hi["properties"]["burnthrough"] is True
    assert hi["properties"]["reliable"] is False
    assert "burnthrough" in hi["properties"]["note"]

    med = _geojson(eng, ras, "jamshadow", [_red("jammer", "medium")])
    assert med["properties"]["burnthrough"] is False
    assert med["properties"]["reliable"] is True


def _combined(eng: PosteriorEngine, ras: Raster, reds: list[_Red]) -> dict:
    return eng.combined_geojson(
        reds, conceal_bands_hz=[BAND_5G8], jam_bands_hz=[BAND_5G8],
        center_lat=ASSET_LAT, center_lon=ASSET_LON, reach_m=1500.0, cell_m=80.0,
        floor=FLOOR, scale_db=SCALE_DB, asset_h_m=2.0, n_path_samples=16, raster=ras,
    )


def test_combined_needs_both_a_sensor_and_a_jammer() -> None:
    eng, ras = _engine(), _ridge_raster()
    out = _combined(eng, ras, [_red("jammer", "medium")])  # no sensor
    assert out["features"] == []
    assert "needs" in out["properties"]["note"]


def test_combined_splits_reds_by_role() -> None:
    eng, ras = _engine(), _ridge_raster()
    reds = [_red("recon", "low"), _red("jammer", "medium", lat=0.005)]
    out = _combined(eng, ras, reds)
    assert out["properties"]["mode"] == "combined"
    assert out["properties"]["sensor_count"] == 1
    assert out["properties"]["jammer_count"] == 1


def test_danger_field_is_the_inverse_of_safe() -> None:
    """The exposure (red/danger) grid is anti-correlated with survival (green/safe):
    the safest cell is not the most-exposed cell."""
    eng, ras = _engine(), _ridge_raster()
    surf = eng._exposure_surface(
        [_red("jammer", "medium")], [BAND_5G8], ras,
        center_lat=ASSET_LAT, center_lon=ASSET_LON, reach_m=1500.0, cell_m=80.0,
        floor=FLOOR, scale_db=SCALE_DB, asset_h_m=2.0, n_path_samples=16,
        band_reduce="max", erp_gate=False,
    )
    s, e = surf["s_norm"], surf["e_norm"]
    assert s.shape == e.shape
    assert np.unravel_index(s.argmax(), s.shape) != np.unravel_index(e.argmax(), e.shape)
    assert float(np.corrcoef(s.ravel(), e.ravel())[0, 1]) < 0  # anti-correlated


def test_polarity_both_tags_safe_and_danger() -> None:
    """polarity=both keeps every feature tagged either <mode> or <mode>_danger
    (vacuously true without shapely, where _bands yields no polygons)."""
    eng, ras = _engine(), _ridge_raster()
    out = eng.exposure_geojson(
        "jamshadow", [_red("jammer", "medium")], [BAND_5G8],
        center_lat=ASSET_LAT, center_lon=ASSET_LON, reach_m=1500.0, cell_m=80.0,
        floor=FLOOR, scale_db=SCALE_DB, asset_h_m=2.0, n_path_samples=16,
        polarity="both", raster=ras,
    )
    assert out["properties"]["polarity"] == "both"
    for ft in out["features"]:
        assert ft["properties"]["feature_kind"] in ("jamshadow", "jamshadow_danger")


def test_erp_stretches_scale_so_high_erp_earns_less_shadow() -> None:
    """Higher ERP -> larger scale_db -> less protection (more exposure) on the
    same terrain. The honesty mechanism, in numbers."""
    eng, ras = _engine(), _ridge_raster()
    kw = dict(floor=FLOOR, scale_db=SCALE_DB, asset_h_m=2.0, raster=ras)
    med = eng.exposure_probe(
        "jamshadow", [_red("jammer", "medium")], [BAND_5G8], ASSET_LAT, ASSET_LON, **kw,
    )
    vhi = eng.exposure_probe(
        "jamshadow", [_red("jammer", "very_high")], [BAND_5G8], ASSET_LAT, ASSET_LON, **kw,
    )
    assert vhi["exposure"] >= med["exposure"]  # harder to hide from a stronger jammer
