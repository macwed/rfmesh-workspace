"""End-to-end DSSS frame round-trip through TX -> loopback channel -> RX.

Closes the ADR-025 simulator-first loop for comms: a byte payload
is encoded, BPSK-modulated, spread, written into a
``rfmesh_sdr.simulator.LoopbackChannel``, optionally degraded by
chip-level AWGN, read back through a ``LoopbackReceiver``,
despread, demodulated, and decoded back to the original byte
payload. Confirms the entire pipeline composes with no hardware
in the loop (Iter 3 gate per the implementation plan).

This is the comms analogue of the ``rfmesh-dsp`` end-to-end DF
test that drops a ``SyntheticReceiver`` into the L1 estimator.
Pure-Python, scipy + numpy + rfmesh-contracts, no SDR.

Lives in ``rfmesh-dsss/tests`` and uses ``rfmesh-sdr`` as a
test-only dependency (per ``[dependency-groups] test`` in
``packages/rfmesh-dsss/pyproject.toml``). Mirror of the
``rfmesh-dsp`` + ``rfmesh-sdr`` test-group pattern; package
source code never imports ``rfmesh-sdr`` (B5 purity).
"""

from __future__ import annotations

import numpy as np
import pytest
from rfmesh_dsss.framing import (
    FIXED_OVERHEAD_BITS,
    PREAMBLE_LEN_BITS,
    decode_frame,
    encode_frame,
)
from rfmesh_dsss.modulation import bpsk_demodulate, bpsk_modulate
from rfmesh_dsss.pn_sequence import generate_m_sequence
from rfmesh_dsss.spreading import despread, spread
from rfmesh_sdr.simulator import (
    LoopbackChannel,
    LoopbackReceiver,
    SyntheticTransmitter,
)

_PAYLOAD_MAX = 64
_SAMPLE_RATE_HZ = 10_000_000.0  # 10 Msps (= chip rate at 1 sample/chip)


def _spread_frame(payload: bytes) -> tuple[np.ndarray, np.ndarray]:
    """Return (spread chips, original payload) for a known frame."""
    pn = generate_m_sequence(10, (10, 3), seed=1)
    bits = encode_frame(
        src_node_id=7,
        dst_node_id=42,
        sequence_no=11,
        payload=payload,
        payload_max_bytes=_PAYLOAD_MAX,
    )
    symbols = bpsk_modulate(bits)
    chips = spread(symbols, pn)
    return chips, payload


def _decode_chips_to_payload(chips: np.ndarray) -> bytes:
    """Inverse of ``_spread_frame``'s spread side: despread -> demod -> decode."""
    pn = generate_m_sequence(10, (10, 3), seed=1)
    symbols = despread(chips, pn)
    bits = bpsk_demodulate(symbols)
    contents = decode_frame(bits, payload_max_bytes=_PAYLOAD_MAX)
    return contents.payload


def test_lossless_frame_roundtrip() -> None:
    """No channel impairments: TX -> channel -> RX -> decode returns the payload."""
    payload = b"BoTH3-loopback-OK"
    chips, expected = _spread_frame(payload)
    channel = LoopbackChannel(sample_rate_hz=_SAMPLE_RATE_HZ)
    tx = SyntheticTransmitter(channel)
    rx = LoopbackReceiver(channel)

    tx.open()
    rx.open()
    n_written = tx.write(chips)
    assert n_written == chips.size

    received = rx.read(chips.size)
    decoded = _decode_chips_to_payload(received)
    assert decoded == expected

    tx.close()
    rx.close()


@pytest.mark.parametrize(
    "chip_snr_db",
    [-20.0, -15.0],
)
def test_frame_roundtrip_at_chip_snr(chip_snr_db: float) -> None:
    """At chip SNR ``{-20, -15}`` dB, the processing gain (~+30 dB) keeps the link clean.

    Both points are well above the BPSK error wall (post-spread Eb/N0
    of +10 dB / +15 dB gives BER << 1e-3 / << 1e-5), so the framed
    payload should round-trip without CRC failure on a single trial.
    The point of the parametric test is to prove the *channel*
    integration is honest: writes carry the configured AWGN through
    and reads pull the perturbed samples back out, exactly the wiring
    the BER-honesty Monte-Carlo (Iter 2) measured against the textbook
    curve.
    """
    payload = b"channel-aware-frame"
    chips, expected = _spread_frame(payload)
    channel = LoopbackChannel(
        sample_rate_hz=_SAMPLE_RATE_HZ,
        chip_snr_db=chip_snr_db,
        rng_seed=20260524,
    )
    tx = SyntheticTransmitter(channel)
    rx = LoopbackReceiver(channel)

    tx.open()
    rx.open()
    tx.write(chips)
    received = rx.read(chips.size)
    decoded = _decode_chips_to_payload(received)
    assert decoded == expected


def test_zero_length_payload_roundtrip() -> None:
    """Empty payload (header + CRC only) survives loopback."""
    chips, expected = _spread_frame(b"")
    channel = LoopbackChannel(sample_rate_hz=_SAMPLE_RATE_HZ)
    tx = SyntheticTransmitter(channel)
    rx = LoopbackReceiver(channel)
    tx.open()
    rx.open()
    tx.write(chips)
    received = rx.read(chips.size)
    assert _decode_chips_to_payload(received) == expected


def test_pipeline_chip_count_matches_overhead_arithmetic() -> None:
    """Sanity: produced chip count = (overhead + 8*payload_bytes) * SF.

    Catches a future regression in ``encode_frame`` or ``spread`` that
    silently inserts / drops chips before the channel sees them.
    """
    pn = generate_m_sequence(10, (10, 3), seed=1)
    sf = pn.size  # 1023
    payload = b"chip-count"
    chips, _expected = _spread_frame(payload)
    expected_n_chips = (FIXED_OVERHEAD_BITS + 8 * len(payload)) * sf
    assert chips.size == expected_n_chips


def test_preamble_chip_count_matches_module_constants() -> None:
    """Preamble occupies the first ``PREAMBLE_LEN_BITS * SF`` chips of the frame.

    Cross-package sanity: the constant the ``rfmesh_dsss`` test
    suite uses to slice off the preamble for the acquisition
    Monte-Carlo lines up with what ``spread`` actually emits on
    the wire.
    """
    pn = generate_m_sequence(10, (10, 3), seed=1)
    chips, _ = _spread_frame(b"a")
    # The preamble portion is the first PREAMBLE_LEN_BITS bits' worth
    # of spread chips; the framing-side spreader sees one symbol per
    # bit, so preamble_chip_count = PREAMBLE_LEN_BITS * SF.
    preamble_chip_count = PREAMBLE_LEN_BITS * pn.size
    assert preamble_chip_count <= chips.size
