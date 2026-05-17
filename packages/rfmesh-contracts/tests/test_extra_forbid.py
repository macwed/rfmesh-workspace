"""`extra="forbid"` enforcement across every contract model.

Every contract model is built with `ConfigDict(frozen=True, extra="forbid")`.
A typo'd field name (e.g. `azimuthd_eg` in a producer that drifted) must
fail at validation time, not silently land an unknown field. This file
exercises the runtime guard per the model_config docstring of each type.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from rfmesh_contracts.config import (  # type: ignore[import-untyped, unused-ignore]
    ArrayConfig,
    BearerConfig,
    FusionConfig,
    NodeConfig,
    SDRConfig,
)
from rfmesh_contracts.geospatial import (  # type: ignore[import-untyped, unused-ignore]
    EllipseENU,
    GeodeticPosition,
)
from rfmesh_contracts.messages import (  # type: ignore[import-untyped, unused-ignore]
    BearingReport,
    FixEvent,
    NodeStatus,
)


def test_bearing_report_extra_forbid(sample_bearing_report: BearingReport) -> None:
    dumped = sample_bearing_report.model_dump()
    dumped["fabricated_field"] = "this should not be here"
    with pytest.raises(ValidationError, match="fabricated_field"):
        BearingReport.model_validate(dumped)


def test_fix_event_extra_forbid(sample_fix_event: FixEvent) -> None:
    dumped = sample_fix_event.model_dump()
    dumped["fabricated_field"] = 42
    with pytest.raises(ValidationError, match="fabricated_field"):
        FixEvent.model_validate(dumped)


def test_node_status_extra_forbid(sample_node_status: NodeStatus) -> None:
    dumped = sample_node_status.model_dump()
    dumped["fabricated_field"] = True
    with pytest.raises(ValidationError, match="fabricated_field"):
        NodeStatus.model_validate(dumped)


def test_geodetic_position_extra_forbid(sample_position: GeodeticPosition) -> None:
    dumped = sample_position.model_dump()
    dumped["altitude_m"] = 200.0  # plausible-looking typo for `hae_m`
    with pytest.raises(ValidationError, match="altitude_m"):
        GeodeticPosition.model_validate(dumped)


def test_ellipse_extra_forbid(sample_ellipse: EllipseENU) -> None:
    dumped = sample_ellipse.model_dump()
    dumped["area_m2"] = 12345.6  # this exists as a property, not a field
    with pytest.raises(ValidationError, match="area_m2"):
        EllipseENU.model_validate(dumped)


def test_sdr_config_extra_forbid(sample_sdr_config: SDRConfig) -> None:
    dumped = sample_sdr_config.model_dump()
    dumped["agc_enabled"] = True
    with pytest.raises(ValidationError, match="agc_enabled"):
        SDRConfig.model_validate(dumped)


def test_array_config_extra_forbid(sample_array_config: ArrayConfig) -> None:
    dumped = sample_array_config.model_dump()
    dumped["element_pattern"] = "isotropic"
    with pytest.raises(ValidationError, match="element_pattern"):
        ArrayConfig.model_validate(dumped)


def test_bearer_config_extra_forbid(sample_bearer_config: BearerConfig) -> None:
    dumped = sample_bearer_config.model_dump()
    dumped["retry_count"] = 3
    with pytest.raises(ValidationError, match="retry_count"):
        BearerConfig.model_validate(dumped)


def test_node_config_extra_forbid(sample_node_config: NodeConfig) -> None:
    dumped = sample_node_config.model_dump()
    dumped["site_name"] = "Marche-les-Dames"
    with pytest.raises(ValidationError, match="site_name"):
        NodeConfig.model_validate(dumped)


def test_fusion_config_extra_forbid(sample_fusion_config: FusionConfig) -> None:
    dumped = sample_fusion_config.model_dump()
    dumped["max_fixes_per_second"] = 100
    with pytest.raises(ValidationError, match="max_fixes_per_second"):
        FusionConfig.model_validate(dumped)
