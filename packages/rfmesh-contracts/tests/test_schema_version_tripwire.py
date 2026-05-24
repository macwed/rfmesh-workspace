"""Runtime half of the SCHEMA_VERSION tripwire (mypy half lives in ADR-012).

ADR-012 introduced `type SchemaVersionT = Literal["<current>"]`. Two complementary
checks fire:

* **At type-check time (mypy):** a producer constructing `BearingReport(
  schema_version="0.9.9", ...)` is a static error in the producing workstream.
  The verification probe lives in ADR-012's References section.
* **At runtime (Pydantic, this file):** the `Literal` constraint also rejects
  mismatched strings during `model_validate` / direct construction.

Without both halves the SCHEMA_VERSION contract is paper.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from rfmesh_contracts.messages import (  # type: ignore[import-untyped, unused-ignore]
    BearingReport,
    FixEvent,
    NodeStatus,
)
from rfmesh_contracts.version import (  # type: ignore[import-untyped, unused-ignore]
    SCHEMA_VERSION,
)


def test_default_schema_version_matches_module_constant(
    sample_bearing_report: BearingReport,
) -> None:
    """A BearingReport built without explicit schema_version uses SCHEMA_VERSION."""
    assert sample_bearing_report.schema_version == SCHEMA_VERSION


def test_correct_schema_version_accepted(
    sample_bearing_report: BearingReport,
) -> None:
    """The canonical SCHEMA_VERSION is accepted explicitly."""
    dumped = sample_bearing_report.model_dump()
    dumped["schema_version"] = SCHEMA_VERSION
    restored = BearingReport.model_validate(dumped)
    assert restored.schema_version == SCHEMA_VERSION


def test_stale_schema_version_rejected_bearing(
    sample_bearing_report: BearingReport,
) -> None:
    """A producer claiming an older SCHEMA_VERSION fails validation."""
    dumped = sample_bearing_report.model_dump()
    dumped["schema_version"] = "1.3.0"
    with pytest.raises(ValidationError, match="schema_version"):
        BearingReport.model_validate(dumped)


def test_stale_schema_version_rejected_fix(sample_fix_event: FixEvent) -> None:
    dumped = sample_fix_event.model_dump()
    dumped["schema_version"] = "1.3.0"
    with pytest.raises(ValidationError, match="schema_version"):
        FixEvent.model_validate(dumped)


def test_stale_schema_version_rejected_node_status(
    sample_node_status: NodeStatus,
) -> None:
    dumped = sample_node_status.model_dump()
    dumped["schema_version"] = "1.3.0"
    with pytest.raises(ValidationError, match="schema_version"):
        NodeStatus.model_validate(dumped)


def test_arbitrary_garbage_schema_version_rejected(
    sample_bearing_report: BearingReport,
) -> None:
    """Random strings (e.g. an attacker's payload) are also rejected."""
    dumped = sample_bearing_report.model_dump()
    dumped["schema_version"] = "<script>alert(1)</script>"
    with pytest.raises(ValidationError):
        BearingReport.model_validate(dumped)
