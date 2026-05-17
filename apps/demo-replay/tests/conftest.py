"""Shared fixtures for ``apps/demo-replay`` tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fixtures_dir() -> Path:
    """Directory holding the test fixtures (YAML, etc.)."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def tiny_scenario_path(fixtures_dir: Path) -> Path:
    """Path to the small smoke scenario."""
    return fixtures_dir / "tiny_scenario.yaml"
