"""Tests for the LFSR-based m-sequence generator.

The three properties that define an m-sequence are pinned here:
length, balance, and two-level cyclic autocorrelation. The golden
artefact at ``tests/golden/pn_len10_seed1_taps10_3.npz`` is matched
byte-for-byte so a future DSP edit cannot silently change the
canonical reference sequence.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from rfmesh_dsss.exceptions import InvalidPnSequenceError
from rfmesh_dsss.pn_sequence import (
    cyclic_autocorrelation,
    generate_m_sequence,
)

_GOLDEN = Path(__file__).parent / "golden" / "pn_len10_seed1_taps10_3.npz"


def test_length_is_2n_minus_1() -> None:
    """A length-``n`` Fibonacci LFSR with primitive taps produces ``2**n - 1`` chips."""
    for n in (3, 5, 7, 10):
        # Use one known primitive polynomial per n. Reference table:
        # https://en.wikipedia.org/wiki/Linear-feedback_shift_register
        taps = {3: (3, 2), 5: (5, 3), 7: (7, 6), 10: (10, 3)}[n]
        seq = generate_m_sequence(n, taps, seed=1)
        assert seq.size == (1 << n) - 1


def test_balance_is_one() -> None:
    """An m-sequence has exactly one more +1 than -1 (DC characteristic)."""
    for n in (5, 7, 10):
        taps = {5: (5, 3), 7: (7, 6), 10: (10, 3)}[n]
        seq = generate_m_sequence(n, taps, seed=1)
        plus = int(np.sum(seq == 1))
        minus = int(np.sum(seq == -1))
        assert plus - minus == 1


def test_two_level_cyclic_autocorrelation_len10() -> None:
    """Cyclic autocorrelation: ``N`` at lag 0, exactly ``-1`` at every other lag."""
    seq = generate_m_sequence(10, (10, 3), seed=1)
    auto = cyclic_autocorrelation(seq)
    n = seq.size  # 1023
    assert auto[0] == n
    # Every non-zero lag must equal -1 exactly (integer).
    assert np.all(auto[1:] == -1)


def test_dtype_is_int8() -> None:
    """Sequence is ``int8`` so 1023-chip artefacts stay ~1 KiB."""
    seq = generate_m_sequence(10, (10, 3), seed=1)
    assert seq.dtype == np.int8


def test_only_plus_minus_one_values() -> None:
    """Every element is exactly +1 or -1; no zero, no other value."""
    seq = generate_m_sequence(10, (10, 3), seed=1)
    assert np.all((seq == 1) | (seq == -1))


def test_different_seeds_are_cyclic_shifts() -> None:
    """Two seeds yield versions of the same m-sequence, cyclically shifted."""
    seq1 = generate_m_sequence(10, (10, 3), seed=1)
    seq42 = generate_m_sequence(10, (10, 3), seed=42)
    # Cross-correlate via cyclic shift: there exists exactly one
    # rotation of seq42 that equals seq1.
    matches = [np.array_equal(np.roll(seq42, k), seq1) for k in range(seq1.size)]
    assert sum(matches) == 1


def test_golden_match() -> None:
    """Freshly-generated sequence matches the checked-in golden artefact."""
    npz = np.load(_GOLDEN)
    expected = npz["sequence"]
    expected_auto = npz["autocorr"]
    seq = generate_m_sequence(10, (10, 3), seed=1)
    auto = cyclic_autocorrelation(seq)
    np.testing.assert_array_equal(seq, expected)
    np.testing.assert_array_equal(auto, expected_auto)
    assert int(npz["balance"]) == 1
    assert int(npz["length"]) == 1023


@pytest.mark.parametrize(
    ("register_length", "taps", "seed", "expected_msg_fragment"),
    [
        (1, (1,), 1, "register_length"),
        (10, (), 1, "non-empty"),
        (10, (3, 2), 1, "register_length"),
        (10, (10, 0), 1, "1-indexed"),
        (10, (10, 3, 3), 1, "unique"),
        (10, (10, 3), 0, "zero seed"),
        (10, (10, 3), 1 << 10, "seed must be in"),
    ],
)
def test_validation_errors(
    register_length: int,
    taps: tuple[int, ...],
    seed: int,
    expected_msg_fragment: str,
) -> None:
    """Each structural invalidity raises ``InvalidPnSequenceError``."""
    with pytest.raises(InvalidPnSequenceError, match=expected_msg_fragment):
        generate_m_sequence(register_length, taps, seed)
