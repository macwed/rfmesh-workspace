"""S1 — band -> LiDAR surface/resolution lookup (Fresnel-fidelity, ADR-016 §3)."""

from __future__ import annotations

from both3_poc.enhance import finest_band_surface, surface_res_for_band


def test_low_band_is_bare_earth_coarse() -> None:
    sr = surface_res_for_band(433e6)
    assert sr["surface"] == "dtm"
    assert sr["res"] == 4
    assert sr["needs_dsm"] is False


def test_sub_ghz_control_is_bare_earth() -> None:
    assert surface_res_for_band(915e6)["surface"] == "dtm"


def test_gnss_l1_uses_dsm() -> None:
    sr = surface_res_for_band(1.5754e9)
    assert sr["surface"] == "dsm"
    assert sr["needs_dsm"] is True


def test_high_band_is_finest_dsm() -> None:
    sr = surface_res_for_band(5.8e9)
    assert sr["surface"] == "dsm"
    assert sr["res"] == 1
    assert sr["needs_dsm"] is True


def test_finest_band_picks_the_highest() -> None:
    # one AOI raster must serve all bands -> pick the finest (highest freq)
    sr = finest_band_surface([433e6, 915e6, 5.8e9])
    assert sr["surface"] == "dsm" and sr["res"] == 1


def test_finest_band_empty_defaults_coarse() -> None:
    sr = finest_band_surface([])
    assert sr["surface"] == "dtm" and sr["needs_dsm"] is False
