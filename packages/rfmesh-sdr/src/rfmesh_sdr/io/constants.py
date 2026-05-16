"""Constants for the rtl_sdr-style uint8-interleaved IQ capture format.

The format is the native output of the ``rtl_sdr`` CLI tool: each IQ
sample is two bytes (I then Q) where a byte ``b`` in ``[0, 255]`` maps to
the real value ``(b - 127.5) / 127.5`` -- DC sits at byte 127/128 and
full-scale is byte 0 or 255. Capturing in this format keeps the IQ
files compact (2 bytes/sample) and lets the same files be consumed by
the wider rtl_sdr toolchain.

These constants are the numerical scaffolding for ``io.conversion``; the
acceptance criterion ``test_rtl_sdr_dc_offset_value`` pins
``RTL_SDR_DC_OFFSET = 127.5`` and ``BYTES_PER_SAMPLE = 2`` so a future
agent cannot quietly retune them without breaking the test.
"""

from __future__ import annotations

# rtl_sdr uint8 interleaved format: DC centered at 127.5, range [0, 255].
RTL_SDR_DC_OFFSET: float = 127.5
RTL_SDR_SCALE: float = 127.5

# One I byte + one Q byte per complex sample.
BYTES_PER_SAMPLE: int = 2

# Default streaming chunk size for ``IQReader.iter_chunks``: ~1 M samples
# is ~2 MB on disk, fits comfortably in L2 cache, and amortises file-read
# overhead without holding more than a few MB in memory at once.
DEFAULT_CHUNK_SAMPLES: int = 1_048_576
