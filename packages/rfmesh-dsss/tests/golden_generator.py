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

from rfmesh_dsss.link_budget import (  # noqa: E402  (sys.path hop above)
    ber_theoretical_bpsk,
    processing_gain_db,
)
from rfmesh_dsss.modulation import bpsk_demodulate, bpsk_modulate  # noqa: E402
from rfmesh_dsss.pn_sequence import (  # noqa: E402
    cyclic_autocorrelation,
    generate_m_sequence,
)
from rfmesh_dsss.spreading import despread, spread  # noqa: E402

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


def main() -> int:
    paths = [
        _pn_len10_seed1_taps10_3(),
        _bpsk_roundtrip(),
        _spread_despread_clean(),
        _link_budget_ref(),
    ]
    for path in paths:
        print(f"wrote {path.relative_to(_HERE.parent.parent.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
