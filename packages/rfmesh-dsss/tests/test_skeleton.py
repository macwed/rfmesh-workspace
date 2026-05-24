"""Cross-module wiring tests for ``rfmesh-dsss``.

These tests outlived Iter 0: even with real implementations behind
``pn_sequence`` / ``modulation`` / ``spreading`` / ``link_budget``,
the import-cleanliness check + the
``constants <-> CommsConfig`` drift guard keep working as cheap
canaries for cross-package regressions. Module-specific behaviour
lives in ``test_pn_sequence.py``, ``test_modulation.py``,
``test_spreading.py``, ``test_link_budget.py``.
"""

from __future__ import annotations

import importlib

import pytest
from rfmesh_contracts import CommsConfig
from rfmesh_dsss.constants import (
    DEFAULT_LFSR_TAPS_LEN10,
    DEFAULT_SPREADING_FACTOR,
    EPSILON,
)
from rfmesh_dsss.exceptions import (
    AcquisitionFailedError,
    DsssError,
    FrameDecodeError,
    InvalidPnSequenceError,
)


@pytest.mark.parametrize(
    "module_name",
    [
        "rfmesh_dsss",
        "rfmesh_dsss.constants",
        "rfmesh_dsss.exceptions",
        "rfmesh_dsss.pn_sequence",
        "rfmesh_dsss.modulation",
        "rfmesh_dsss.spreading",
        "rfmesh_dsss.correlation",
        "rfmesh_dsss.timing",
        "rfmesh_dsss.framing",
        "rfmesh_dsss.link_budget",
    ],
)
def test_module_imports_cleanly(module_name: str) -> None:
    """Every module in the package imports without raising."""
    module = importlib.import_module(module_name)
    assert module is not None


def test_exception_hierarchy_intact() -> None:
    """All DSSS exceptions descend from ``DsssError``."""
    assert issubclass(InvalidPnSequenceError, DsssError)
    assert issubclass(AcquisitionFailedError, DsssError)
    assert issubclass(FrameDecodeError, DsssError)
    assert issubclass(DsssError, Exception)


def test_constants_match_default_commsconfig() -> None:
    """``constants`` aligns with ``CommsConfig``'s default spreading.

    Catches drift between ``constants.DEFAULT_*`` (this package's
    convenience constants) and ``CommsConfig.spreading_factor``'s
    default (the frozen-contract value). If a future edit changes
    one without the other, this test fires.
    """
    cfg = CommsConfig(
        carrier_freq_hz=868e6,
        chip_rate_hz=1e6,
        lfsr_taps=DEFAULT_LFSR_TAPS_LEN10,
        lfsr_seed=1,
        tdd_slot_ms=100.0,
        tdd_guard_ms=20.0,
        frame_payload_max_bytes=64,
    )
    assert cfg.spreading_factor == DEFAULT_SPREADING_FACTOR
    assert max(cfg.lfsr_taps) == 10


def test_epsilon_aligns_with_dsp() -> None:
    """Epsilon must equal the rfmesh-dsp constant (1e-12) for cross-package consistency."""
    assert pytest.approx(1e-12) == EPSILON
