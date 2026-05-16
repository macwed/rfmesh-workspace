"""WS-A-004 Acceptance 1(g): pinned constants for the rtl_sdr uint8 format.

The two numeric facts -- ``RTL_SDR_DC_OFFSET = 127.5`` and
``BYTES_PER_SAMPLE = 2`` -- are the entire on-disk contract. Nothing
else in this package may quietly retune them; this test exists so a
future refactor that "rounds 127.5 to 128 for clarity" fails CI
immediately.

The expected values are mirrored as module-level constants below; the
test asserts equality against them so the literal-vs-magic-value
distinction is unambiguous to ruff (and to a reader).
"""

from __future__ import annotations

from rfmesh_sdr.io.constants import BYTES_PER_SAMPLE, RTL_SDR_DC_OFFSET

# Mirror values pinned by the WS-A-004 acceptance criterion. A test
# constant rather than a numeric literal avoids the PLR2004 noise.
_EXPECTED_RTL_SDR_DC_OFFSET = 127.5
_EXPECTED_BYTES_PER_SAMPLE = 2


def test_rtl_sdr_dc_offset_value() -> None:
    """DC offset is 127.5 (the midpoint of the uint8 range) and one IQ sample is 2 bytes."""
    assert RTL_SDR_DC_OFFSET == _EXPECTED_RTL_SDR_DC_OFFSET
    assert BYTES_PER_SAMPLE == _EXPECTED_BYTES_PER_SAMPLE
