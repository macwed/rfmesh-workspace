"""ReplayMetadata unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from rfmesh_demo_replay import ReplayMetadata
from rfmesh_sdr.io.metadata import IQMetadata


def _base_args() -> dict[str, object]:
    return {
        "sample_rate_hz": 2.0e6,
        "center_freq_hz": 915.0e6,
        "n_samples": 100_000,
        "start_time_utc": datetime.now(UTC),
    }


def test_extends_iqmetadata() -> None:
    """ReplayMetadata is structurally an IQMetadata."""
    meta = ReplayMetadata(**_base_args())
    assert isinstance(meta, IQMetadata)
    # IQMetadata fields are reachable.
    assert meta.sample_rate_hz == 2.0e6
    assert meta.n_channels == 1  # default for the replay extension


def test_required_iqmetadata_fields_still_required() -> None:
    """Dropping a required IQMetadata field still raises."""
    args = _base_args()
    del args["sample_rate_hz"]
    with pytest.raises(ValidationError):
        ReplayMetadata(**args)


def test_replay_extras_default_to_none() -> None:
    """All replay-specific fields default to None / 1, never silently zero."""
    meta = ReplayMetadata(**_base_args())
    assert meta.scenario_id is None
    assert meta.node_id is None
    assert meta.beat_id is None
    assert meta.ground_truth_emitter_enu_m is None
    assert meta.ground_truth_bearing_deg is None
    assert meta.channel_model is None
    assert meta.n_channels == 1


def test_roundtrip_through_json_preserves_replay_extras() -> None:
    """model_dump_json -> model_validate_json preserves every field.

    Locks the on-disk sidecar contract: a recording written today
    must be loadable later with no field loss.
    """
    meta = ReplayMetadata(
        **_base_args(),
        scenario_id="trench_demo_v1",
        node_id="node-l1-west",
        beat_id="B",
        ground_truth_emitter_enu_m=(0.0, 3000.0),
        ground_truth_bearing_deg=12.3,
        channel_model={"kind": "free_space"},
        n_channels=2,
    )
    blob = meta.model_dump_json()
    restored = ReplayMetadata.model_validate_json(blob)
    assert restored == meta
    assert restored.n_channels == 2
    assert restored.scenario_id == "trench_demo_v1"


def test_extra_fields_forbidden() -> None:
    """Typos on the sidecar fail at parse time (Invariant 4 surface)."""
    with pytest.raises(ValidationError):
        ReplayMetadata.model_validate(
            {
                **_base_args(),
                "scenairo_id": "typo",
            }
        )
