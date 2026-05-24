"""Deterministic regenerator for ``tests/golden/*.npz`` artefacts.

Run from the repo root with::

    uv run python packages/rfmesh-dsss/tests/golden_generator.py

The script writes one ``.npz`` per checked-in artefact. The tests in
``tests/test_*.py`` load these and assert byte-for-byte equality
against freshly-computed outputs. Regenerating after an intentional
DSP change is the workflow: run this script, ``git diff`` the
artefacts, commit if the change is intentional.

Convention -- mirror of ``rfmesh-dsp``'s ``golden_generator.py``:

* Every artefact ships a ``description`` 0-D string array so future
  readers can see what the file pins without reading this script.
* Seeds and parameters are inlined at the call site, not imported
  from production code, so a future change to a default constant
  cannot silently shift what the golden file pins.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

import numpy as np

# Make ``rfmesh_dsss`` importable when this script runs directly from
# the repo (uv venv has the package editable; this hop just gives the
# script the same import path as pytest).
_HERE = Path(__file__).resolve().parent
_PKG_SRC = _HERE.parent / "src"
if str(_PKG_SRC) not in sys.path:
    sys.path.insert(0, str(_PKG_SRC))

from rfmesh_dsss.correlation import matched_filter  # noqa: E402  (sys.path hop above)
from rfmesh_dsss.framing import _preamble_bits, encode_frame  # noqa: E402
from rfmesh_dsss.link_budget import (  # noqa: E402
    ber_theoretical_bpsk,
    processing_gain_db,
)
from rfmesh_dsss.modulation import bpsk_demodulate, bpsk_modulate  # noqa: E402
from rfmesh_dsss.pn_sequence import (  # noqa: E402
    cyclic_autocorrelation,
    generate_m_sequence,
)
from rfmesh_dsss.spreading import despread, spread  # noqa: E402
from rfmesh_dsss.timing import correct_carrier_phase  # noqa: E402

_GOLDEN_DIR: Final[Path] = _HERE / "golden"


def _write(name: str, **arrays: np.ndarray) -> Path:
    out = _GOLDEN_DIR / name
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out, **arrays)
    return out


def _pn_len10_seed1_taps10_3() -> Path:
    """Length-10 m-sequence with seed=1 and taps (10, 3).

    Pins: full 1023-chip sequence, cyclic autocorrelation, balance
    count, length. This is THE reference PN for the BoTH3 build.
    """
    register_length = 10
    taps = (10, 3)
    seed = 1
    sequence = generate_m_sequence(register_length, taps, seed)
    autocorr = cyclic_autocorrelation(sequence)
    description = np.array(
        f"length-10 m-sequence, taps={taps}, seed={seed}; "
        f"1023 chips, two-level cyclic autocorrelation (1023 @ lag 0, "
        f"-1 elsewhere); BPSK +/-1 mapping (1->+1, 0->-1)."
    )
    return _write(
        "pn_len10_seed1_taps10_3.npz",
        sequence=sequence,
        autocorr=autocorr,
        balance=np.int64(int(sequence.sum())),
        length=np.int64(sequence.size),
        description=description,
    )


def _bpsk_roundtrip() -> Path:
    """BPSK modulate -> demodulate exact round-trip for a known bit pattern."""
    bits = np.array(
        [0, 1, 0, 0, 1, 1, 0, 1, 1, 0, 0, 0, 1, 1, 1, 0],
        dtype=np.int8,
    )
    symbols = bpsk_modulate(bits)
    recovered = bpsk_demodulate(symbols)
    description = np.array(
        "BPSK round-trip: 16 bits -> +/-1 symbols (complex64, Q=0) -> "
        "back to bits via sign-of-real-part decision. Pins exact "
        "round-trip equality and the 0->+1 / 1->-1 convention."
    )
    return _write(
        "bpsk_roundtrip.npz",
        bits=bits,
        symbols=symbols,
        recovered=recovered,
        description=description,
    )


def _spread_despread_clean() -> Path:
    """Spread then despread, no channel impairment; recover symbols exactly."""
    register_length = 10
    taps = (10, 3)
    seed = 1
    pn = generate_m_sequence(register_length, taps, seed)
    # 4 symbols * 1023 chips * 8 bytes (complex64) = ~32 KiB artefact.
    # Enough symbols to cover {+1, -1, +1, -1} pattern transitions
    # without ballooning the checked-in file (plan ceiling ~100 KiB).
    bits = np.array([0, 1, 0, 1], dtype=np.int8)
    symbols = bpsk_modulate(bits)
    chips = spread(symbols, pn)
    recovered_symbols = despread(chips, pn)
    recovered_bits = bpsk_demodulate(recovered_symbols)
    description = np.array(
        "Spread/despread clean round-trip: 4 BPSK symbols * 1023-chip "
        "PN = 4092 chips; despread recovers symbols to +/- 1 + 0j "
        "exactly (no channel impairment, no noise). Pins the "
        "normalised correlator: clean +1 symbol -> +1 + 0j."
    )
    return _write(
        "spread_despread_clean.npz",
        bits=bits,
        symbols=symbols,
        pn=pn,
        chips=chips,
        recovered_symbols=recovered_symbols,
        recovered_bits=recovered_bits,
        description=description,
    )


def _link_budget_ref() -> Path:
    """Processing gain + BPSK BER at canonical Eb/N0 points."""
    spreading_factors = np.array([31, 63, 127, 255, 511, 1023, 2047], dtype=np.int64)
    pg_db = np.array(
        [processing_gain_db(int(sf)) for sf in spreading_factors],
        dtype=np.float64,
    )
    # Eb/N0 sweep in dB at the canonical curve points (textbook).
    eb_n0_db = np.arange(0.0, 13.0, 1.0, dtype=np.float64)
    eb_n0_linear = 10.0 ** (eb_n0_db / 10.0)
    ber = ber_theoretical_bpsk(eb_n0_linear)
    description = np.array(
        "Link-budget references: processing-gain table for "
        "spreading_factor in {31, 63, 127, 255, 511, 1023, 2047} "
        "and theoretical BPSK BER over Eb/N0 in {0, 1, ..., 12} dB."
    )
    return _write(
        "link_budget_ref.npz",
        spreading_factors=spreading_factors,
        pg_db=pg_db,
        eb_n0_db=eb_n0_db,
        ber=np.asarray(ber, dtype=np.float64),
        description=description,
    )


def _frame_roundtrip() -> Path:
    """Encode a known frame, no channel; pin the bit-stream + CRC."""
    src_node_id = 7
    dst_node_id = 42
    sequence_no = 13
    payload = b"BoTH3-DSSS"
    payload_max_bytes = 64
    bits = encode_frame(
        src_node_id=src_node_id,
        dst_node_id=dst_node_id,
        sequence_no=sequence_no,
        payload=payload,
        payload_max_bytes=payload_max_bytes,
    )
    description = np.array(
        "Frame round-trip: 10-byte payload 'BoTH3-DSSS' from src=7 to "
        "dst=42, seq=13. Pins exact bit layout (preamble + sync 0x1ACFFC1D "
        "+ header + payload + CRC-16/CCITT-FALSE) and the resulting "
        "bit-stream length = FIXED_OVERHEAD_BITS (144) + 8 * len(payload)."
    )
    return _write(
        "frame_roundtrip.npz",
        bits=bits,
        payload=np.frombuffer(payload, dtype=np.uint8),
        src_node_id=np.int64(src_node_id),
        dst_node_id=np.int64(dst_node_id),
        sequence_no=np.int64(sequence_no),
        payload_max_bytes=np.int64(payload_max_bytes),
        description=description,
    )


def _correlation_peak() -> Path:
    """Matched filter against a known preamble + PN replica.

    Pins the correlation-magnitude array shape and the chip-index
    location of the peak for a clean (no-noise, no-offset) input.
    """
    pn = generate_m_sequence(10, (10, 3), seed=1)
    # Preamble = framing._preamble_bits (length-63 m-sequence + pad)
    # -> 64 BPSK symbols -> spread by 1023-chip PN = 65472 chips.
    preamble_bits = _preamble_bits()
    preamble_symbols = bpsk_modulate(preamble_bits)
    preamble_chips = spread(preamble_symbols, pn)
    # Embed the preamble at chip offset 4096 in a longer zero buffer
    # (pure-signal input -- not a realistic channel; the test pins the
    # peak index for the noiseless case as a regression anchor).
    buffer_size = preamble_chips.size + 8192
    received = np.zeros(buffer_size, dtype=np.complex64)
    embed_offset = 4096
    received[embed_offset : embed_offset + preamble_chips.size] = preamble_chips
    corr_mag = matched_filter(received, preamble_chips)
    peak_index = int(np.argmax(corr_mag))
    peak_value = float(corr_mag[peak_index])
    description = np.array(
        "Matched-filter peak: 64-bit preamble * 1023-chip PN embedded at "
        "chip offset 4096 in zero-padded buffer; pins peak index and peak "
        "magnitude for noiseless input. Iter 2 acquisition regression anchor."
    )
    return _write(
        "correlation_peak.npz",
        peak_index=np.int64(peak_index),
        peak_value=np.float64(peak_value),
        embed_offset=np.int64(embed_offset),
        preamble_chip_count=np.int64(preamble_chips.size),
        description=description,
    )


def _carrier_phase_recovery() -> Path:
    """Apply a known carrier rotation; pin that the recovered phase de-rotates it."""
    bits = np.tile(np.array([0, 1], dtype=np.int8), 16)
    symbols = bpsk_modulate(bits)
    rotation_rad = np.float64(np.pi / 6)  # 30 degrees
    rotated = (symbols * np.exp(np.complex64(1j * rotation_rad))).astype(np.complex64)
    derotated = correct_carrier_phase(rotated)
    description = np.array(
        "Carrier-phase recovery: 32 BPSK symbols rotated +30 deg, then "
        "block-mode Costas-like estimator de-rotates them. Pins the input, "
        "rotated, and de-rotated arrays so an Iter-3 change to the estimator "
        "fails this test loudly."
    )
    return _write(
        "carrier_phase_recovery.npz",
        bits=bits,
        symbols=symbols,
        rotation_rad=np.float64(rotation_rad),
        rotated=rotated,
        derotated=derotated,
        description=description,
    )


def main() -> int:
    paths = [
        _pn_len10_seed1_taps10_3(),
        _bpsk_roundtrip(),
        _spread_despread_clean(),
        _link_budget_ref(),
        _frame_roundtrip(),
        _correlation_peak(),
        _carrier_phase_recovery(),
    ]
    for path in paths:
        print(f"wrote {path.relative_to(_HERE.parent.parent.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
