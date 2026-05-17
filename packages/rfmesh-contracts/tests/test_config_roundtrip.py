"""Round-trip tests for configuration types."""

from __future__ import annotations

from rfmesh_contracts.config import (  # type: ignore[import-untyped, unused-ignore]
    ArrayConfig,
    BearerConfig,
    FusionConfig,
    NodeConfig,
    SDRConfig,
)


def test_sdr_config_roundtrip(sample_sdr_config: SDRConfig) -> None:
    dumped = sample_sdr_config.model_dump()
    restored = SDRConfig.model_validate(dumped)
    assert restored == sample_sdr_config


def test_array_config_roundtrip(sample_array_config: ArrayConfig) -> None:
    dumped = sample_array_config.model_dump()
    restored = ArrayConfig.model_validate(dumped)
    assert restored == sample_array_config


def test_bearer_config_roundtrip(sample_bearer_config: BearerConfig) -> None:
    dumped = sample_bearer_config.model_dump()
    restored = BearerConfig.model_validate(dumped)
    assert restored == sample_bearer_config


def test_node_config_roundtrip(sample_node_config: NodeConfig) -> None:
    dumped = sample_node_config.model_dump()
    restored = NodeConfig.model_validate(dumped)
    assert restored == sample_node_config


def test_node_config_roundtrip_json(sample_node_config: NodeConfig) -> None:
    payload = sample_node_config.model_dump_json()
    restored = NodeConfig.model_validate_json(payload)
    assert restored == sample_node_config


def test_fusion_config_roundtrip(sample_fusion_config: FusionConfig) -> None:
    dumped = sample_fusion_config.model_dump()
    restored = FusionConfig.model_validate(dumped)
    assert restored == sample_fusion_config


def test_fusion_config_roundtrip_json(sample_fusion_config: FusionConfig) -> None:
    payload = sample_fusion_config.model_dump_json()
    restored = FusionConfig.model_validate_json(payload)
    assert restored == sample_fusion_config
