"""Tests for ADR-026 peer-prior tagging + folding helpers in rendezvous."""

from __future__ import annotations

import math

import pytest
from rfmesh_contracts import GeodeticPosition
from rfmesh_node.rendezvous import (
    great_circle_distance_m,
    peer_prior_sigma_deg,
)

# ---------------------------------------------------------------------------
# peer_prior_sigma_deg
# ---------------------------------------------------------------------------


def test_peer_prior_sigma_small_angle_for_typical_range() -> None:
    """v1 typical: sigma_pos=5 m, range=5 km -> 1e-3 rad = ~0.057 deg."""
    s = peer_prior_sigma_deg(5.0, 5000.0)
    assert s == pytest.approx(math.degrees(5.0 / 5000.0), rel=1e-9)
    assert 0.05 < s < 0.06


def test_peer_prior_sigma_caps_at_90deg_for_co_located() -> None:
    """Pathological co-located peers saturate at the 90-deg servo arc."""
    assert peer_prior_sigma_deg(50.0, 0.0) == 90.0
    assert peer_prior_sigma_deg(100.0, 1.0) == 90.0


def test_peer_prior_sigma_zero_position_uncertainty() -> None:
    """Zero sigma_pos -> zero prior sigma (unrealistic but math-clean)."""
    assert peer_prior_sigma_deg(0.0, 1000.0) == 0.0


# ---------------------------------------------------------------------------
# great_circle_distance_m
# ---------------------------------------------------------------------------


def test_great_circle_distance_zero_for_same_point() -> None:
    a = GeodeticPosition(lat_deg=50.0, lon_deg=5.0, hae_m=0.0, sigma_m=1.0)
    assert great_circle_distance_m(a, a) == pytest.approx(0.0, abs=1e-6)


def test_great_circle_distance_one_degree_latitude() -> None:
    """1° of latitude ~ 111 km (great-circle, spherical)."""
    a = GeodeticPosition(lat_deg=50.0, lon_deg=5.0, hae_m=0.0, sigma_m=1.0)
    b = GeodeticPosition(lat_deg=51.0, lon_deg=5.0, hae_m=0.0, sigma_m=1.0)
    d = great_circle_distance_m(a, b)
    # 6_371_000 * pi/180 ≈ 111_195 m.
    assert d == pytest.approx(111_195.0, rel=1e-3)


def test_great_circle_distance_symmetric() -> None:
    a = GeodeticPosition(lat_deg=50.33, lon_deg=5.0, hae_m=200.0, sigma_m=5.0)
    b = GeodeticPosition(lat_deg=50.34, lon_deg=5.005, hae_m=200.0, sigma_m=5.0)
    assert great_circle_distance_m(a, b) == pytest.approx(great_circle_distance_m(b, a), rel=1e-12)
