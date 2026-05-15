"""Deterministic per-stream RNG manager for the simulator.

One integer seed in -> one fully reproducible sequence of IQ blocks out.
The manager wraps ``numpy.random.SeedSequence`` and exposes a dedicated
``numpy.random.Generator`` per stochastic stream (noise, per-emitter
jitter, per-channel phase error, ...). Streams are spawned from a single
root sequence at deterministic indices so adding a stream in a later
ticket never perturbs the seed of any pre-existing stream -- a property
that lets golden-file tests stay valid across simulator-feature growth.

``reseed(s)`` rebuilds the root from a fresh seed and re-spawns every
stream. This is the mechanism that the
``test_reseed_changes_noise_keeps_signal`` test relies on: only the
stochastic streams change, the deterministic emitter geometry (heading,
range, antenna pattern, sample-offset counter, frequencies) is untouched.

WS-A-001 ships only one active stream (single-channel complex AWGN).
Subsequent tickets that introduce phase jitter, multipath shadowing, or
coherent per-channel noise consume reserved indices below the noise one.
"""

from __future__ import annotations

import numpy as np


class RngManager:
    """Spawns and owns the simulator's stochastic-stream Generators.

    The class is intentionally tiny: it does no IQ math, only seed bookkeeping.
    """

    # Number of streams reserved up front so later tickets can add streams
    # without perturbing the seed of any earlier-numbered stream.
    _N_RESERVED_STREAMS = 8
    # Index assignments. Reserved indices may be added in future tickets:
    # 1: per-emitter phase jitter; 2: multipath shadowing; 4..7: coherent
    # per-channel noise (currently unused; the single noise Generator at
    # index 0 supplies per-channel coherent AWGN sequentially).
    _NOISE_INDEX = 0
    # WS-A-002: the calibration handshake renders a noise reference on a
    # dedicated stream so a calibrate() call does not perturb the main
    # noise stream's state (preserving read/read_coherent reproducibility
    # across a calibration event).
    _CAL_NOISE_INDEX = 3

    def __init__(self, seed: int = 0) -> None:
        """Initialise from a single integer seed.

        Two ``RngManager(seed=s)`` instances produce byte-identical Generator
        outputs in the same call order, on the same numpy build. That is the
        property the protocol-conformance and determinism tests rely on.
        """
        self._seed = seed
        self._spawn_streams()

    def _spawn_streams(self) -> None:
        """(Re)build every stream's Generator from the current root seed."""
        root = np.random.SeedSequence(self._seed)
        children = root.spawn(self._N_RESERVED_STREAMS)
        self._noise_gen = np.random.default_rng(children[self._NOISE_INDEX])
        self._cal_noise_gen = np.random.default_rng(children[self._CAL_NOISE_INDEX])

    def reseed(self, seed: int) -> None:
        """Reset the root seed and re-spawn every stream.

        Deterministic geometry (heading, scenario, sample-offset counter) is
        untouched -- those live on the Receiver, not on the RngManager.
        """
        self._seed = seed
        self._spawn_streams()

    @property
    def noise(self) -> np.random.Generator:
        """The complex-AWGN Generator (the only active stream in WS-A-001)."""
        return self._noise_gen

    @property
    def cal_noise(self) -> np.random.Generator:
        """The calibration-handshake reference-noise Generator (WS-A-002).

        Held on its own stream so ``CoherentReceiver.calibrate()`` does not
        consume samples from the main ``noise`` stream -- meaning a calibrate
        call between two ``read_coherent`` calls does not change the IQ that
        those reads would have produced without it.
        """
        return self._cal_noise_gen

    @property
    def seed(self) -> int:
        """The current root seed, exposed for diagnostics and test assertions."""
        return self._seed
