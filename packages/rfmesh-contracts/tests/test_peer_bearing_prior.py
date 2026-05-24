"""Tests for ADR-026 peer-bearing prior axis (SCHEMA_VERSION 1.4.0)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from rfmesh_contracts import (
    SCHEMA_VERSION,
    BearingPriorKind,
    BearingReport,
    Capability,
    NodeStatus,
    PeerLink,
)


def _base_bearing_payload() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "node_id": "node-test",
        "t_unix_ns": 1_700_000_000_000_000_000,
        "node_position": {
            "lat_deg": 50.33,
            "lon_deg": 5.0,
            "hae_m": 200.0,
            "sigma_m": 5.0,
        },
        "azimuth_deg": 142.0,
        "azimuth_sigma_deg": 6.0,
        "method": Capability.L1_RSSI,
    }


# ---------------------------------------------------------------------------
# BearingPriorKind enum
# ---------------------------------------------------------------------------


def test_bearing_prior_kind_values() -> None:
    """Enum members serialize to the snake-case strings used on the wire."""
    assert BearingPriorKind.FLAT == "flat"
    assert BearingPriorKind.PEER_LINK == "peer_link"


# ---------------------------------------------------------------------------
# BearingReport — new fields default to None (backwards-compat)
# ---------------------------------------------------------------------------


def test_legacy_bearing_round_trip_no_prior_fields() -> None:
    """A producer that omits all 3 new fields validates and round-trips."""
    payload = _base_bearing_payload()
    report = BearingReport.model_validate(payload)
    assert report.prior_kind is None
    assert report.prior_mean_deg is None
    assert report.prior_sigma_deg is None
    # Dump round-trip preserves the None defaults.
    dumped = report.model_dump()
    assert dumped["prior_kind"] is None


def test_flat_prior_kind_no_prior_fields_valid() -> None:
    """prior_kind=FLAT without prior_mean/sigma is valid (flat prior)."""
    payload = {**_base_bearing_payload(), "prior_kind": "flat"}
    report = BearingReport.model_validate(payload)
    assert report.prior_kind is BearingPriorKind.FLAT
    assert report.prior_mean_deg is None


def test_peer_link_prior_kind_requires_both_fields() -> None:
    """PEER_LINK without prior_mean_deg is a loud refusal (B3)."""
    payload = {
        **_base_bearing_payload(),
        "prior_kind": "peer_link",
        "prior_sigma_deg": 1.5,
        # prior_mean_deg missing
    }
    with pytest.raises(ValidationError, match="PEER_LINK requires both"):
        BearingReport.model_validate(payload)


def test_peer_link_prior_kind_requires_prior_sigma() -> None:
    payload = {
        **_base_bearing_payload(),
        "prior_kind": "peer_link",
        "prior_mean_deg": 142.0,
        # prior_sigma_deg missing
    }
    with pytest.raises(ValidationError, match="PEER_LINK requires both"):
        BearingReport.model_validate(payload)


def test_peer_link_prior_full_payload_valid() -> None:
    payload = {
        **_base_bearing_payload(),
        "prior_kind": "peer_link",
        "prior_mean_deg": 142.0,
        "prior_sigma_deg": 1.5,
    }
    report = BearingReport.model_validate(payload)
    assert report.prior_kind is BearingPriorKind.PEER_LINK
    assert report.prior_mean_deg == 142.0
    assert report.prior_sigma_deg == 1.5


def test_flat_with_prior_data_refused() -> None:
    """B3: prior_mean_deg without prior_kind=PEER_LINK is misleading; refuse."""
    payload = {
        **_base_bearing_payload(),
        "prior_kind": "flat",
        "prior_mean_deg": 142.0,
        "prior_sigma_deg": 1.5,
    }
    with pytest.raises(ValidationError, match="only valid when prior_kind=PEER_LINK"):
        BearingReport.model_validate(payload)


def test_none_prior_kind_with_prior_data_refused() -> None:
    """Same coherence rule when prior_kind is None (legacy producer path)."""
    payload = {
        **_base_bearing_payload(),
        "prior_mean_deg": 142.0,
    }
    with pytest.raises(ValidationError, match="only valid when prior_kind=PEER_LINK"):
        BearingReport.model_validate(payload)


def test_prior_mean_deg_validator_range() -> None:
    payload = {
        **_base_bearing_payload(),
        "prior_kind": "peer_link",
        "prior_mean_deg": 365.0,  # out of [0, 360)
        "prior_sigma_deg": 1.5,
    }
    with pytest.raises(ValidationError):
        BearingReport.model_validate(payload)


def test_prior_sigma_deg_strictly_positive() -> None:
    payload = {
        **_base_bearing_payload(),
        "prior_kind": "peer_link",
        "prior_mean_deg": 142.0,
        "prior_sigma_deg": 0.0,  # gt=0
    }
    with pytest.raises(ValidationError):
        BearingReport.model_validate(payload)


# ---------------------------------------------------------------------------
# PeerLink value object
# ---------------------------------------------------------------------------


def test_peer_link_minimal() -> None:
    pl = PeerLink(peer_node_id="node-b")
    assert pl.peer_node_id == "node-b"
    assert pl.last_lock_t_unix_ns is None
    assert pl.link_margin_db is None


def test_peer_link_full() -> None:
    pl = PeerLink(
        peer_node_id="node-b",
        last_lock_t_unix_ns=1_700_000_000_000_000_000,
        link_margin_db=18.5,
    )
    assert pl.link_margin_db == 18.5


def test_peer_link_requires_node_id() -> None:
    with pytest.raises(ValidationError):
        PeerLink(peer_node_id="")


def test_peer_link_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        PeerLink.model_validate({"peer_node_id": "node-b", "secret": "boom"})


# ---------------------------------------------------------------------------
# NodeStatus.peer_links
# ---------------------------------------------------------------------------


def _base_node_status_payload() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "node_id": "node-test",
        "t_unix_ns": 1_700_000_000_000_000_000,
        "position": {
            "lat_deg": 50.33,
            "lon_deg": 5.0,
            "hae_m": 200.0,
            "sigma_m": 5.0,
        },
        "active_capabilities": ["l1_rssi"],
        "gnss_locked": True,
        "healthy": True,
    }


def test_node_status_peer_links_defaults_to_none() -> None:
    status = NodeStatus.model_validate(_base_node_status_payload())
    assert status.peer_links is None  # legacy compat


def test_node_status_peer_links_empty_tuple_distinct_from_none() -> None:
    """Empty tuple = "1.4.0 node with no peers"; None = legacy."""
    payload = {**_base_node_status_payload(), "peer_links": []}
    status = NodeStatus.model_validate(payload)
    assert status.peer_links == ()


def test_node_status_peer_links_with_entries() -> None:
    payload = {
        **_base_node_status_payload(),
        "peer_links": [
            {
                "peer_node_id": "node-b",
                "last_lock_t_unix_ns": 1_700_000_000_000_000_000,
                "link_margin_db": 18.0,
            },
            {"peer_node_id": "node-c"},
        ],
    }
    status = NodeStatus.model_validate(payload)
    assert status.peer_links is not None
    assert len(status.peer_links) == 2
    assert status.peer_links[0].link_margin_db == 18.0
    assert status.peer_links[1].link_margin_db is None
