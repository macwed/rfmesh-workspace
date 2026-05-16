"""Format conversion between uint8-interleaved bytes and normalised ``complex64`` IQ.

Two pure functions, deduplicated from the prior project where the
forward map ``_to_complex64`` lived in two places (``io/iq_reader.py``
and ``io/devices/rtlsdr.py`` -- a tech debt called out in
``SALVAGE_AUDIT.md`` Part 4a). Both directions live here in one place;
the reader, the recorder, and any future hardware backend that emits
the rtl_sdr-native uint8 stream import from the same module.

The map is the canonical rtl_sdr quantization grid:

* Forward (``uint8 -> complex64``)::

      sample = (byte - 127.5) / 127.5

  Bytes 0..255 cover the amplitude range [-127.5/127.5, 127.5/127.5]
  = [-1.0, +1.0]. The "DC offset" 127.5 is *between* bytes 127 and 128
  by design -- the format has no exact-zero byte, which keeps the I and
  Q ADC ranges symmetric.

* Inverse (``complex64 -> uint8 pair``)::

      byte = clip(round(sample * 127.5 + 127.5), 0, 255)

  Values outside [-1, +1] saturate to 0 or 255 rather than wrapping --
  clipping is the honest behaviour for a fixed-point format. The
  inverse is *quasi-identity* in the sense that for any uint8 input
  ``b`` in [0, 255], ``inverse(forward(b)) == b`` bit-exactly. (Proof:
  ``round((b - 127.5)/127.5 * 127.5 + 127.5) == round(b) == b``.)

That bit-exact round-trip is what makes the WS-A-004 1.a roundtrip test
work within the documented ``atol = 1/127.5`` tolerance; any complex64
value in [-1, +1] survives one quantize-then-dequantize step to within
half a quantization step (~0.0039), well under the test tolerance.
"""

from __future__ import annotations

import numpy as np

from .constants import RTL_SDR_DC_OFFSET, RTL_SDR_SCALE

# Saturation bounds for the uint8 round trip. Named so the cast-to-uint8
# call site reads as a clamp, not a magic-number truncation.
_UINT8_MIN = 0
_UINT8_MAX = 255


def uint8_pair_to_complex64(raw: np.ndarray) -> np.ndarray:
    """Convert interleaved uint8 I/Q bytes to normalised ``complex64`` samples.

    Args:
        raw: Flat ``uint8`` array. Even indices carry I, odd indices Q.
            Length must be even -- a half-sample at the end is a malformed
            capture, surfaced as ``MalformedIQFileError`` at the I/O layer.

    Returns:
        ``(n_samples,)`` ``complex64`` array, with values in [-1, +1].

    The ``float32`` intermediate is intentional: it matches the precision
    of the ``complex64`` result so we do not pay for float64 throughput on
    bulk reads.
    """
    if raw.dtype != np.uint8:
        msg = f"uint8_pair_to_complex64: expected dtype uint8, got {raw.dtype}."
        raise TypeError(msg)
    if raw.size % 2 != 0:
        msg = (
            "uint8_pair_to_complex64: byte count must be even (one I + one Q per sample); "
            f"got {raw.size} bytes."
        )
        raise ValueError(msg)
    floats = (raw.astype(np.float32) - RTL_SDR_DC_OFFSET) / RTL_SDR_SCALE
    return (floats[0::2] + 1j * floats[1::2]).astype(np.complex64)


def complex64_to_uint8_pair(samples: np.ndarray) -> np.ndarray:
    """Convert normalised ``complex64`` samples to interleaved uint8 I/Q bytes.

    Args:
        samples: ``(n_samples,)`` ``complex64`` (or ``complex`` castable to
            it) array. Values are assumed to live in [-1, +1]; out-of-range
            inputs are saturated to byte 0 or 255 (clipping, not wrapping).

    Returns:
        ``(n_samples * 2,)`` ``uint8`` array with even indices holding I,
        odd indices holding Q -- the rtl_sdr native layout.

    The intermediate is ``float32`` for the same reason as the forward
    direction: it is the precision the format actually carries.
    """
    if samples.ndim != 1:
        msg = (
            "complex64_to_uint8_pair: samples must be 1-D "
            f"(got shape {samples.shape}); records are single-channel."
        )
        raise ValueError(msg)
    samples_c64 = samples.astype(np.complex64, copy=False)
    real_bytes_f = np.round(samples_c64.real * RTL_SDR_SCALE + RTL_SDR_DC_OFFSET)
    imag_bytes_f = np.round(samples_c64.imag * RTL_SDR_SCALE + RTL_SDR_DC_OFFSET)
    real_bytes = np.clip(real_bytes_f, _UINT8_MIN, _UINT8_MAX).astype(np.uint8)
    imag_bytes = np.clip(imag_bytes_f, _UINT8_MIN, _UINT8_MAX).astype(np.uint8)
    out = np.empty(samples_c64.size * 2, dtype=np.uint8)
    out[0::2] = real_bytes
    out[1::2] = imag_bytes
    return out
