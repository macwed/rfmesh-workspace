"""WS-A-004 Acceptance 1(e) and 1(f): IQMetadata schema-version pinning and JSON roundtrip."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from rfmesh_sdr.io import IQMetadata


def _baseline_metadata() -> IQMetadata:
    return IQMetadata(
        sample_rate_hz=2.048e6,
        center_freq_hz=915e6,
        n_samples=4096,
        start_time_utc=datetime(2026, 5, 14, 10, 30, 0, tzinfo=UTC),
        source="synthetic_receiver",
        gain_db=35.0,
        notes="round-trip test",
    )


def test_iq_metadata_schema_versioning() -> None:
    """Constructing with a wrong ``schema_version`` literal must raise."""
    # The default ('1.0.0') is accepted.
    md = _baseline_metadata()
    assert md.schema_version == "1.0.0"

    # An older / newer version is a Literal mismatch -> ValidationError.
    with pytest.raises(ValidationError):
        IQMetadata(
            schema_version="0.9.0",  # type: ignore[arg-type]
            sample_rate_hz=2.048e6,
            center_freq_hz=915e6,
            n_samples=4096,
            start_time_utc=datetime(2026, 5, 14, 10, 30, 0, tzinfo=UTC),
        )

    with pytest.raises(ValidationError):
        IQMetadata(
            schema_version="2.0.0",  # type: ignore[arg-type]
            sample_rate_hz=2.048e6,
            center_freq_hz=915e6,
            n_samples=4096,
            start_time_utc=datetime(2026, 5, 14, 10, 30, 0, tzinfo=UTC),
        )


def test_iq_metadata_round_trip_json() -> None:
    """``model_dump_json()`` followed by ``model_validate_json()`` is identity."""
    original = _baseline_metadata()
    payload = original.model_dump_json()
    recovered = IQMetadata.model_validate_json(payload)
    assert recovered == original


def test_iq_metadata_naive_datetime_rejected() -> None:
    """Naive (no timezone) start_time_utc must be rejected -- the schema is UTC-only."""
    naive = datetime(2026, 5, 14, 10, 30, 0)  # deliberately naive for this rejection test
    with pytest.raises(ValidationError):
        IQMetadata(
            sample_rate_hz=2.048e6,
            center_freq_hz=915e6,
            n_samples=4096,
            start_time_utc=naive,
        )


def test_iq_metadata_extra_fields_forbidden() -> None:
    """A typo'd field is rejected, not silently absorbed (Invariant 4 surface)."""
    with pytest.raises(ValidationError):
        IQMetadata.model_validate(
            {
                "sample_rate_hz": 2.048e6,
                "center_freq_hz": 915e6,
                "n_samples": 4096,
                "start_time_utc": "2026-05-14T10:30:00+00:00",
                "smaple_rate_hz": 999.0,  # typo
            }
        )


def test_iq_metadata_optional_fields_default_to_none() -> None:
    """Optional provenance fields default to ``None`` so callers can omit them."""
    md = IQMetadata(
        sample_rate_hz=2.048e6,
        center_freq_hz=915e6,
        n_samples=4096,
        start_time_utc=datetime(2026, 5, 14, 10, 30, 0, tzinfo=UTC),
    )
    assert md.source is None
    assert md.serial is None
    assert md.gain_db is None
    assert md.notes is None
