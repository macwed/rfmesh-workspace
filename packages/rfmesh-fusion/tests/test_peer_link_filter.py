"""Tests for the ADR-026 PEER_LINK filter in StansfieldMLEFuser.fuse()."""

from __future__ import annotations

import math

import pytest
from rfmesh_contracts import (
    BearingPriorKind,
    BearingReport,
    Capability,
    FusionConfig,
    GeodeticPosition,
)
from rfmesh_fusion import StansfieldMLEFuser
from rfmesh_fusion.projection import from_enu

_T0_NS = 1_700_000_000_000_000_000
_ORIGIN = GeodeticPosition(lat_deg=50.330, lon_deg=5.0, hae_m=200.0, sigma_m=0.0)


def _fuser() -> StansfieldMLEFuser:
    return StansfieldMLEFuser(FusionConfig(listen_url="udp://0.0.0.0:9000"))


def _bearing(
    *,
    node_enu: tuple[float, float],
    emitter_enu: tuple[float, float],
    sigma_deg: float = 5.0,
    node_id: str,
    prior_kind: BearingPriorKind | None = None,
    prior_mean_deg: float | None = None,
    prior_sigma_deg: float | None = None,
) -> BearingReport:
    de = emitter_enu[0] - node_enu[0]
    dn = emitter_enu[1] - node_enu[1]
    az_deg = math.degrees(math.atan2(de, dn)) % 360.0
    node_geo = from_enu(node_enu[0], node_enu[1], origin=_ORIGIN, sigma_m=5.0)
    return BearingReport(
        node_id=node_id,
        t_unix_ns=_T0_NS,
        node_position=node_geo,
        azimuth_deg=az_deg,
        azimuth_sigma_deg=sigma_deg,
        method=Capability.L1_RSSI,
        prior_kind=prior_kind,
        prior_mean_deg=prior_mean_deg,
        prior_sigma_deg=prior_sigma_deg,
    )


def test_peer_link_bearings_excluded_from_fix() -> None:
    """A peer-acquisition bearing is filtered out; the FLAT bearings produce
    the fix as if the peer bearing were absent."""
    fuser = _fuser()
    emitter = (1000.0, 1000.0)
    bearings = (
        _bearing(
            node_enu=(0.0, 0.0),
            emitter_enu=emitter,
            node_id="node-a",
            prior_kind=BearingPriorKind.FLAT,
        ),
        _bearing(
            node_enu=(2000.0, 0.0),
            emitter_enu=emitter,
            node_id="node-b",
            prior_kind=BearingPriorKind.FLAT,
        ),
        # PEER_LINK bearing intentionally pointed at a different angle so
        # it WOULD pull the fix if not filtered.
        _bearing(
            node_enu=(0.0, 2000.0),
            emitter_enu=(0.0, 4000.0),  # bogus emitter to bias the fix
            node_id="node-c",
            prior_kind=BearingPriorKind.PEER_LINK,
            prior_mean_deg=0.0,
            prior_sigma_deg=1.0,
        ),
    )
    fix = fuser.fuse(bearings)
    assert fix is not None
    # Contributing nodes must NOT include node-c.
    assert "node-c" not in fix.contributing_nodes
    assert set(fix.contributing_nodes) == {"node-a", "node-b"}


def test_all_peer_batch_raises() -> None:
    """B3: a batch of only PEER_LINK bearings is a loud refusal, not silent None."""
    fuser = _fuser()
    bearings = (
        _bearing(
            node_enu=(0.0, 0.0),
            emitter_enu=(1000.0, 1000.0),
            node_id="node-a",
            prior_kind=BearingPriorKind.PEER_LINK,
            prior_mean_deg=45.0,
            prior_sigma_deg=1.5,
        ),
        _bearing(
            node_enu=(2000.0, 0.0),
            emitter_enu=(1000.0, 1000.0),
            node_id="node-b",
            prior_kind=BearingPriorKind.PEER_LINK,
            prior_mean_deg=315.0,
            prior_sigma_deg=1.5,
        ),
    )
    with pytest.raises(ValueError, match="entirely PEER_LINK-prior"):
        fuser.fuse(bearings)


def test_legacy_none_prior_kind_treated_as_flat() -> None:
    """A producer that omits prior_kind (legacy / pre-1.4.0) is included."""
    fuser = _fuser()
    emitter = (1000.0, 1000.0)
    bearings = (
        _bearing(node_enu=(0.0, 0.0), emitter_enu=emitter, node_id="node-a"),
        _bearing(node_enu=(2000.0, 0.0), emitter_enu=emitter, node_id="node-b"),
    )
    fix = fuser.fuse(bearings)
    assert fix is not None
    assert set(fix.contributing_nodes) == {"node-a", "node-b"}


def test_mixed_flat_and_peer_keeps_flat_drops_peer() -> None:
    """Per-peak filter: secondary FLAT peaks from a peer sweep DO contribute."""
    fuser = _fuser()
    emitter = (1000.0, 1000.0)
    bearings = (
        _bearing(
            node_enu=(0.0, 0.0),
            emitter_enu=emitter,
            node_id="node-a",
            prior_kind=BearingPriorKind.FLAT,
        ),
        _bearing(
            node_enu=(2000.0, 0.0),
            emitter_enu=emitter,
            node_id="node-b",
            prior_kind=BearingPriorKind.PEER_LINK,
            prior_mean_deg=270.0,
            prior_sigma_deg=1.5,
        ),
        _bearing(
            node_enu=(0.0, 2000.0),
            emitter_enu=emitter,
            node_id="node-c",
            prior_kind=BearingPriorKind.FLAT,
        ),
    )
    fix = fuser.fuse(bearings)
    assert fix is not None
    assert "node-b" not in fix.contributing_nodes
    assert set(fix.contributing_nodes) == {"node-a", "node-c"}
