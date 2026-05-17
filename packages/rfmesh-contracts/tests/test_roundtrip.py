"""Round-trip tests for wire-format messages.

Each message: `model_validate(model_dump())` returns an equal instance.
Closes architect council finding F9 (rfmesh-contracts had zero tests).
"""

from __future__ import annotations

from rfmesh_contracts.messages import (  # type: ignore[import-untyped, unused-ignore]
    BearingReport,
    FixEvent,
    NodeStatus,
)


def test_bearing_report_roundtrip(sample_bearing_report: BearingReport) -> None:
    dumped = sample_bearing_report.model_dump()
    restored = BearingReport.model_validate(dumped)
    assert restored == sample_bearing_report


def test_bearing_report_roundtrip_json(sample_bearing_report: BearingReport) -> None:
    """JSON round-trip exercises serialiser end-to-end (used on the wire)."""
    payload = sample_bearing_report.model_dump_json()
    restored = BearingReport.model_validate_json(payload)
    assert restored == sample_bearing_report


def test_fix_event_roundtrip(sample_fix_event: FixEvent) -> None:
    dumped = sample_fix_event.model_dump()
    restored = FixEvent.model_validate(dumped)
    assert restored == sample_fix_event


def test_fix_event_roundtrip_json(sample_fix_event: FixEvent) -> None:
    payload = sample_fix_event.model_dump_json()
    restored = FixEvent.model_validate_json(payload)
    assert restored == sample_fix_event


def test_node_status_roundtrip(sample_node_status: NodeStatus) -> None:
    dumped = sample_node_status.model_dump()
    restored = NodeStatus.model_validate(dumped)
    assert restored == sample_node_status


def test_node_status_roundtrip_json(sample_node_status: NodeStatus) -> None:
    payload = sample_node_status.model_dump_json()
    restored = NodeStatus.model_validate_json(payload)
    assert restored == sample_node_status
